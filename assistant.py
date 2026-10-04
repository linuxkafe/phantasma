"""
Main orchestration loop for pHantasma voice pipeline.

Coordinates the full voice pipeline:
AudioCapture -> VADProcessor -> HotwordDetector ->
WhisperSTT -> OllamaLLM -> PiperTTS -> AudioPlayback

Implements hotword-activated speech collection with
VAD-gated silence detection.
"""

import logging

# What the Phantom says when it hears its own name. Synthesised on first
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from typing import Optional

import numpy as np

import config as config_module
from config import config
from data_utils import get_cached_response, retrieve_from_rag
from skills import SkillContext, SkillLoader
from src.brain.fly_brain import FlyBrain
from src.brain.persistence import FlyBrainStore
from src.pipeline import quiet
from src.pipeline.audio import (
    AudioCapture,
    AudioPlayback,
    HotwordDetector,
    VADProcessor,
)
from src.pipeline.stt import transcribe as stt_transcribe
from src.pipeline.utils import Result, log_stage, logger
from src.settings_store import get_persona
from tools import search_with_searxng

# What the Phantom says when it hears its own name. Synthesised on first
# use and served from the TTS cache after that.
GREETING_TEXT = "Sim."


def _llm_timeout():
    """The HTTP timeout for one Ollama call.

    Read budget stays generous (OLLAMA_TIMEOUT, 600s in prod) because a long
    generation is legitimate. Connect budget is short: reaching a host that is
    down is a failure, not a slow answer, and on 2026-09-29 a dead primary
    (10.0.0.128:11434) left the call blocking with no bound at all, which is
    what stopped the whole pipeline from answering.
    """
    import httpx

    import config

    read = float(getattr(config.config.llm, "timeout", 600) or 600)
    connect = min(float(getattr(config.config.llm, "connect_timeout", 10) or 10), read)
    return httpx.Timeout(read, connect=connect)


def sanitize_llm_context(context: str) -> str:
    """Sanitize RAG/web context for LLM injection.

    Removes technical markers, timestamps, and poetic artifacts
    that pollute the context.
    """
    if not context or not isinstance(context, str):
        return ""

    # Remove technical RAG instructions
    context = re.sub(r"MEMÓRIAS PESSOAIS.*?\n\n", "", context, flags=re.DOTALL | re.IGNORECASE)
    context = re.sub(r"NOTA: Se houver contradições.*?\n", "", context, flags=re.IGNORECASE)

    # Remove timestamps and technical IDs
    context = re.sub(r"\[\d{4}-\d{2}-\d{2}.*?\]", "", context)

    # Remove poetic artifacts from RAG
    poison_terms = ["Sombra", "Aquietação", "Fim", "Silêncio", "Fúria da Memória"]
    for term in poison_terms:
        context = re.sub(rf"\*\*{term}\*\*", "", context, flags=re.IGNORECASE)
        context = re.sub(rf"{term}:", "", context, flags=re.IGNORECASE)

    return context.strip()


def _prewarm_stt():
    """Load Whisper in the background while the device is still booting.

    The class-level cache in WhisperSTT was never the problem: it holds the
    model and the second command reuses it. The 33 seconds land on the first
    command after a start, because that is the first time anything asks for
    the model. Paying it here takes it off the owner's first request.
    """
    def _load():
        try:
            from src.pipeline.stt import WhisperSTT

            result = WhisperSTT.load_model(config.stt.model_size)
            if result.success:
                logger.info("STT prewarmed: %s ready", config.stt.model_size)
            else:
                logger.warning("STT prewarm failed: %s", result.error)
        except Exception as exc:  # noqa: BLE001
            logger.warning("STT prewarm skipped: %s", exc)

    threading.Thread(target=_load, name="stt-prewarm", daemon=True).start()


def _speech_seconds(text: str) -> float:
    """Rough spoken length, used to keep the mic shut while we are talking.

    Portuguese at the ghost tempo runs near 13 characters a second. Being
    generous costs silence; being tight costs the loop.
    """
    return max(0.6, len(text) / 13.0 + 0.4)




def _wake_score_is_worth_logging(
    top_score: float,
    last_logged: float,
    threshold: float,
    movement: float = 0.05,
    near_ratio: float = 0.4,
) -> bool:
    """Should this wake score be written to the journal?

    Two moments carry information and everything else is noise:

    * **near** -- the score is within ``near_ratio`` of the threshold that would
      actually fire the wake word for this model. This is why the threshold is
      passed in rather than re-read from the environment here: the detector
      holds a per-model threshold with a global fallback
      (``HotwordConfig.thresholds_per_model``), so a single number read from
      the environment would be wrong for any model configured otherwise, and
      the log would disagree with the decision it exists to explain.
    * **moved** -- the score shifted by at least ``movement`` since the last
      line printed. Without this, a word that rises from 0.02 to 0.10 -- the
      shape of somebody starting to speak -- would print nothing until it was
      already close to firing, and the one window where the diagnostic is most
      useful is the one where it is quiet.

    Silence below that is not worth a line every five seconds forever. It was:
    on 2026-09-29 that unconditional line was most of what the service wrote.

    Args:
        top_score: Highest raw score this tick.
        last_logged: Top score of the last line actually printed.
        threshold: The threshold in force for the model that produced the score.
        movement: Score change that counts as worth reporting. Default 0.05.
        near_ratio: Fraction of the threshold that counts as "near".
            Default 0.4.

    Returns:
        True if the line should be written.
    """
    if top_score >= threshold * near_ratio:
        return True
    return abs(top_score - last_logged) >= movement


class PhantasmaPipeline:
    """Main voice pipeline orchestrator.

    Manages the complete voice interaction loop:
    1. Continuous audio capture via AudioCapture (sounddevice callback -> queue)
    2. VAD filtering via VADProcessor (WebRTC VAD on fixed-size frames)
    3. Hotword detection via HotwordDetector (openWakeWord on VAD frames)
    4. Speech collection: accumulate VAD frames until 1s silence
    5. STT via WhisperSTT (transcribe collected speech)
    6. LLM via OllamaLLM (generate response)
    7. TTS via PiperTTS (synthesize response)
    8. Playback via AudioPlayback (sounddevice output)

    State Machine:
        IDLE (hotword detection) ->
        COLLECTING (speech accumulation) ->
        PROCESSING (STT/LLM/TTS) -> IDLE

    Attributes:
        audio_capture: AudioCapture instance for microphone input.
        audio_playback: AudioPlayback instance for speaker output.
        vad: VADProcessor for voice activity detection.
        hotword: HotwordDetector for wake word detection.
        _running: Pipeline running flag.
        _worker_thread: Background thread for audio processing.
        _hotword_detected: Whether hotword was detected in current interaction.
        _collecting_speech: Whether currently accumulating speech frames.
        _speech_frames: List of collected speech audio frames.
        _silence_frames: Count of consecutive non-speech frames.
        _max_silence_frames: Frames of silence before ending collection (1s default).
    """

    def __init__(self):
        """Initialize pipeline components from config."""
        self.audio_capture = AudioCapture(
            device=config.audio.device_in,
            sample_rate=config.audio.sample_rate,
            channels=config.audio.channels,
            block_size=config.audio.block_size,
            dtype=config.audio.dtype,
            queue_maxsize=config.pipeline.queue_maxsize,
        )
        self.audio_playback = AudioPlayback(
            device=config.audio.device_out,
            sample_rate=22050,  # Piper default output rate
            channels=1,
            dtype="int16",
        )
        self.vad = VADProcessor(
            aggressiveness=config.vad.aggressiveness,
            sample_rate=config.audio.sample_rate,
            frame_duration_ms=config.vad.frame_duration_ms,
        )
        self.hotword = HotwordDetector(
            models=config.hotword.models,
            threshold=config.hotword.threshold,
            sample_rate=config.audio.sample_rate,
            chunk_size=1280,  # openWakeWord expects 1280 samples (80ms at 16kHz)
        )
        # The wake-score log, and what it is for.
        #
        # It used to be an unconditional INFO every 5 s, forever, which made a
        # diagnostic into a tax: the owner reported the journal filling with
        # "Wake scores: ola_fantasma=0.0008", a number meaning silence. It is
        # still here, and deliberately so -- a model that never fires is
        # otherwise indistinguishable from a microphone that hears nothing, which
        # cost a full diagnosis cycle once already (hey_fantasma sat under its
        # threshold and read as "no reaction" rather than "scoring 0.09").
        #
        # What changed is WHEN it prints: near the threshold, or when the score
        # has moved. See _wake_score_is_worth_logging and the audio loop.
        #
        # _last_top_score is the top score of the last line actually printed;
        # the "moved" test compares against it, so it is only ever updated when
        # a line is written.
        self._last_score_log = 0.0
        self._last_top_score = 0.0
        self._last_level_log = 0.0

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._audio_buffer: list[np.ndarray] = []
        self._hotword_detected = False
        self._collecting_speech = False
        self._speech_frames: list[np.ndarray] = []
        self._silence_frames = 0
        # 1 second of silence frames at current VAD frame duration
        self._max_silence_frames = int(1.0 * 1000 / config.vad.frame_duration_ms)

        self._fly_brain_store = FlyBrainStore(config_module.BRAIN_DB_PATH)
        self._fly_brain = FlyBrain(store=self._fly_brain_store)
        # Register as the canonical instance so reactions from Discord and
        # from the web chat step THIS brain, not a rival over the same store.
        try:
            from src.brain.fly_brain import register_shared_fly_brain

            register_shared_fly_brain(self._fly_brain)
        except Exception as exc:  # noqa: BLE001 - never block startup
            logging.getLogger(__name__).warning("Could not register the shared FlyBrain: %s", exc)
        self._skill_loader = SkillLoader(
            skills_dir=config_module.SKILLS_DIR,
            context=SkillContext(fly_brain=self._fly_brain),
        )
        self._skill_loader.load_all()
        # Start legacy daemon skills (discord, weather, system_stats, tuya, ...)
        self._skill_loader.start_daemons()
        self._collecting_feedback = False
        self._feedback_start_time: Optional[float] = None
        self._feedback_window_seconds = config_module.FEEDBACK_WINDOW_SECONDS
        # True while the speakers are talking, so the listener can stay shut.
        self._speaking = False
        self._positive_keywords = config_module.FEEDBACK_POSITIVE_KEYWORDS
        self._negative_keywords = config_module.FEEDBACK_NEGATIVE_KEYWORDS

    def _play_audio_feedback(self) -> Result:
        """Play music snippet + greeting on hotword detection.

        Reads a random music file from config.audio_feedback.music_dir
        (supports .mp3, .wav, .ogg), then plays the greeting file.

        MP3 files are played via mpg123 subprocess; WAV/OGG via sounddevice.
        Failures are non-fatal and logged as warnings.

        Returns:
            Result.ok(None) always — audio feedback failure doesn't stop pipeline.
        """
        if not config.audio_feedback.enabled:
            return Result.ok(None)

        try:
            music_dir = config.audio_feedback.music_dir
            # The music directory is optional. os.listdir raised
            # FileNotFoundError when it was absent, the except below
            # swallowed it, and the greeting -- the next statement -- was
            # never reached. So the wake word fired, logged one warning
            # and said nothing. Each part of the feedback has to fail on
            # its own terms rather than take the greeting down with it.
            music_files = []
            if os.path.isdir(music_dir):
                music_files = [f for f in os.listdir(music_dir)
                               if f.endswith((".mp3", ".wav", ".ogg"))]

            if music_files:
                music_file = os.path.join(music_dir, random.choice(music_files))
                # Use mpg123 for mp3, sounddevice for wav/ogg
                if music_file.endswith(".mp3"):
                    subprocess.run(["mpg123", "-q", music_file], check=False, timeout=5)
                else:
                    import soundfile as sf

                    data, sr = sf.read(music_file, dtype="int16")
                    self.audio_playback.play(data)

            # Play greeting. It is synthesised, not shipped: there is no
            # audio/greeting.wav in any branch and there never was, so the
            # only thing that could ever play it was the TTS cache. Serve the
            # cache when it has the text, synthesise and cache it when it does
            # not, and keep this independent of the music so one missing thing
            # cannot silence the other.
            greeting_path = config.audio_feedback.greeting_path
            if not os.path.isabs(greeting_path):
                greeting_path = os.path.join(os.getcwd(), greeting_path)

            greeted = False
            if os.path.exists(greeting_path):
                import soundfile as sf

                data, _sr = sf.read(greeting_path, dtype="int16")
                if len(data):
                    self.audio_playback.play(data)
                    greeted = True

            if not greeted:
                # play_tts reads the cache first and writes the result back,
                # keyed on the text, so the second wake word costs nothing.
                try:
                    from audio_utils import play_tts

                    play_tts(GREETING_TEXT, use_cache=True)
                    greeted = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Greeting synthesis failed: %s", exc)

            if not greeted:
                # Silent is the failure mode that cost the session: the wake
                # word fired, one warning was logged, and nothing was said.
                logger.warning(
                    "No greeting audio for %r: no cached or synthesised file",
                    GREETING_TEXT,
                )

            return Result.ok(None)
        except Exception as e:
            logger.warning(f"Audio feedback failed: {e}")
            return Result.ok(None)  # Non-fatal

    def _check_feedback_keywords(self, text: str) -> Optional[float]:
        """Check if text contains positive/negative feedback keywords.

        Scans the transcribed text for configured positive and negative
        feedback phrases (e.g., "obrigado" = positive, "não entendi" = negative).

        Args:
            text: Transcribed user text (Portuguese).

        Returns:
            +1.0 for positive feedback, -1.0 for negative, None if no feedback detected.
        """
        text_lower = text.lower().strip()

        for kw in self._positive_keywords:
            if kw in text_lower:
                logger.info(f"Positive feedback detected: '{kw}'")
                return +1.0

        for kw in self._negative_keywords:
            if kw in text_lower:
                logger.info(f"Negative feedback detected: '{kw}'")
                return -1.0

        return None

    def _process_feedback(self, text: str):
        """Process feedback text and update FlyBrain neuromodulatory state.

        Called during the feedback collection window after a response.
        Checks for keywords and applies reward/punishment to FlyBrain.

        Args:
            text: Transcribed user text during feedback window.
        """
        reward = self._check_feedback_keywords(text)
        if reward is not None:
            # Use current ring orientation as topic, low novelty for feedback
            self._fly_brain.step(
                topic_angle_deg=self._fly_brain.ring.orientation_deg,
                novelty=0.1,
                reward=reward,
            )
            # Feedback is a deliberate, user-authored correction. step() only
            # auto-saves every 10 steps, so without this the reinforcement is
            # lost whenever the process restarts before the interval elapses.
            if self._fly_brain.persist():
                logger.info("FlyBrain state persisted after feedback")
            logger.info(
                f"FlyBrain updated: reward={reward}, affinity={self._fly_brain.mb.affinity:.3f}"
            )
        else:
            logger.debug(f"No feedback keyword in: '{text}'")

        # Exit feedback collection mode
        self._collecting_feedback = False
        self._feedback_start_time = None

    def _process_audio_frame(self, frame: np.ndarray):
        """Process single audio frame through hotword detection and VAD.

        Implements the main state machine (matching working legacy):
        - IDLE: Run hotword detection on raw 1280-sample frames (NO VAD)
        - COLLECTING: Accumulate speech frames using VAD for silence detection
        - FEEDBACK: After response, collect user feedback using VAD

        Args:
            frame: Raw audio frame from AudioCapture (int16, 1280 samples at 16kHz).
        """
        # Check feedback window timeout
        if self._collecting_feedback and self._feedback_start_time:
            elapsed = time.time() - self._feedback_start_time
            if elapsed >= self._feedback_window_seconds:
                logger.debug("Feedback window expired")
                self._collecting_feedback = False
                self._feedback_start_time = None

        # Our own voice must never become a command. _speak already stops
        # the capture; this catches a device a skill reopened underneath us.
        if self._ignore_while_speaking():
            return

        if self._collecting_feedback:
            # Feedback collection mode - use VAD for speech segmentation
            vad_frames = self.vad.process_chunk(frame)
            for vad_frame in vad_frames:
                self._speech_frames.append(vad_frame.data)

                if vad_frame.is_speech:
                    self._silence_frames = 0
                else:
                    self._silence_frames += 1

                # Process feedback on silence (end of utterance)
                if self._silence_frames >= self._max_silence_frames:
                    if self._speech_frames:
                        speech_audio = np.concatenate(self._speech_frames)
                        stt_result = stt_transcribe(speech_audio)
                        if stt_result.success and stt_result.data:
                            self._process_feedback(stt_result.data)
                    self._speech_frames = []
                    self._silence_frames = 0

        elif not self._collecting_speech:
            # Hotword detection mode (IDLE) - PASS RAW FRAME DIRECTLY (no VAD)
            # frame comes from AudioCapture at config.audio.block_size;
            # HotwordDetector buffers to its own 80ms window internally.
            detected, model = self.hotword.process(frame)
            scores = getattr(self.hotword, "last_predictions", {}) or {}
            now = time.time()
            # Input level, sampled ten times more often than the wake score.
            # A wake word lasts about a second and the score line prints every
            # 5 s, so a 5 s sample of silence says nothing about what the
            # microphone captured while someone was speaking. Without this,
            # "it did not fire" cannot be told apart from "the microphone
            # heard nothing", which are opposite problems with opposite fixes.
            if now - self._last_level_log > 0.5 and len(frame):
                self._last_level_log = now
                _a = frame.astype("float64")
                # debug, not info: at 0.5 s this was two thirds of every log
                # line the service wrote. It is the diagnostic that made the
                # wake word measurable, and it stays -- but it does not belong
                # in a journal nobody asked to fill.
                logger.debug(
                    "Input level: peak=%.1f%% rms=%.1f%% of scale",
                    100.0 * float(np.abs(_a).max()) / 32768.0,
                    100.0 * float(np.sqrt((_a ** 2).mean())) / 32768.0,
                )
            # Wake scores, only when they are worth reading.
            #
            # This was an unconditional INFO every 5 seconds, forever, and it
            # printed the same line TWICE per tick -- once through the root
            # handler and once through the pipeline's own, so the journal filled
            # with a number that was 0.0008 in an empty room and said nothing
            # about whether the microphone was working.
            #
            # A score is only interesting near the threshold, or when the top
            # score has moved a lot since the last one printed: those are the
            # two moments that tell you something. Silence is silence, and
            # logging it on a loop is how a diagnostic becomes noise. The
            # threshold comes from the same config the detector uses, so this
            # cannot drift away from what would actually fire the wake word.
            if now - getattr(self, "_last_score_log", 0.0) > 5.0:
                ranked_all = sorted(scores.items(), key=lambda kv: -kv[1])
                top_name, top_score = ranked_all[0] if ranked_all else ("n/a", 0.0)
                # The threshold in force for THAT model, from the detector
                # itself -- not a constant, and not a second read of the
                # environment. Same source as the decision it explains.
                per_model = getattr(self.hotword, "_thresholds", {}) or {}
                threshold = per_model.get(
                    top_name, getattr(self.hotword, "threshold", 0.5)
                )
                last_top = getattr(self, "_last_top_score", 0.0)
                if _wake_score_is_worth_logging(top_score, last_top, threshold):
                    self._last_score_log = now
                    self._last_top_score = top_score
                    ranked = ranked_all[:2]
                    logger.info(
                        "🔬 Wake scores: "
                        + (" ".join(f"{n}={v:.4f}" for n, v in ranked) or "n/a")
                    )
            if detected:
                # Night mode is not "speak quietly": it means the assistant is
                # not awake. Measured on 2026-09-29 at 03:41, inside quiet
                # hours, a false positive (0.80 against 0.70) reached the branch
                # below, played "Sim." at 03:41 in an empty house, burned 23 s
                # of STT, and transcribed to nothing. _speak's silence gate
                # cannot help here: _play_audio_feedback() speaks directly, and
                # by then the microphone, the model and the user have all been
                # woken for no reason.
                #
                # So the detection is swallowed here, before any of it. The
                # detector is told to reject rather than to reset: the mel
                # buffer now describes a wake word the rest of the system never
                # accepted (the phantom-score state _prime_buffer exists to
                # prevent), but the cooldown must survive, or a steady noise
                # source is re-scored and re-rejected on every frame.
                if quiet.is_quiet():
                    logger.info(
                        "Quiet hours: hotword %r rejected (score %.2f) -- not waking",
                        model,
                        max(scores.values()) if scores else 0.0,
                    )
                    self.hotword.reject_detection()
                    return
                logger.info(f"Hotword detected: {model}")
                self._hotword_detected = True
                self._collecting_speech = True
                self._speech_frames = []
                self._silence_frames = 0
                # The wake word is itself speech: the incoming partial VAD
                # frame belongs to the phrase that is being collected, so it
                # must not be treated as leading silence.
                self.vad.reset()

                # Play audio feedback (music + greeting)
                self._play_audio_feedback()
        else:
            # Speech collection mode - use VAD for silence detection
            vad_frames = self.vad.process_chunk(frame)
            for vad_frame in vad_frames:
                self._speech_frames.append(vad_frame.data)

                if vad_frame.is_speech:
                    self._silence_frames = 0
                else:
                    self._silence_frames += 1

                # Check for end of speech (1 second silence)
                if self._silence_frames >= self._max_silence_frames:
                    self._process_speech()
                    self._collecting_speech = False
                    self._hotword_detected = False
                    # _process_speech blocks this thread for the whole
                    # STT+LLM+TTS cycle (measured 55s for one utterance), and
                    # skills additionally stop/start the capture stream to free
                    # the USB PCM. Both leave openWakeWord's rolling mel buffer
                    # describing a timeline the model never received, which is
                    # what produced the phantom 0.09-0.49 scores. Reset here --
                    # after the block, before the worker consumes again -- so
                    # the first frame back is scored against silence-primed
                    # state. This is the invariant _prime_buffer documents but
                    # nothing was calling to maintain it. The working legacy
                    # did call it (engine.reset() after each detection); it was
                    # lost in the port, and its absence is what let the phantom
                    # scores fire.
                    self.hotword.reset()
                    # Same reasoning for the VAD's partial frame: the audio
                    # withheld during the block belongs to neither the wake
                    # word nor the next utterance, so it must not be carried
                    # into either.
                    self.vad.reset()

    def respond_to_text(self, text: str) -> Optional[str]:
        """Route text through skills then FlyBrain + LLM.

        Shared by the voice path (_process_speech) and the REST API
        (/comando). Skips TTS/playback/feedback-window concerns; those
        belong to the caller.

        Args:
            text: User text (e.g. "como está o tempo?").

        Returns:
            Response text, or None if no skill handled it and LLM failed.
        """
        # Check skills first (intercept ++/-- and weather/tuya before LLM)
        skill_response = self._execute_with_paused_shared_audio(text)
        if skill_response is not None:
            # A skill's output is the answer to "what is the weather in Porto",
            # and it is NOT the answer to "what do you make of the weather in
            # Porto". The first is a lookup; the second is asking for a reading,
            # and answering it with a bare telemetry line is the bot refusing to
            # have an opinion -- measured 2026-10-04, "o que achas de como esta
            # o tempo em Lisboa" returned "Hoje em Lisboa: estado incerto,
            # entre 17 e 27" verbatim, with no persona and no research.
            #
            # So the skill still RUNS in both cases -- the data is the same and
            # it is still the freshest source -- but in the opinion case it
            # becomes an input to the model instead of the model's absence.
            #
            # Only for opinions, deliberately. "acende a luz" is a command: the
            # user wants the light on, not a paragraph about lighting. Sending
            # every skill through the LLM would make the house slower and less
            # reliable to get exactly the answers it is best at.
            if self._is_opinion(text):
                logger.info(
                    "Skill devolveu dados para uma opiniao: %s", skill_response
                )
                return self._respond_with_llm(text, skill_data=skill_response)
            logger.info(f"Skill '{text}' handled: {skill_response}")
            return skill_response

        return self._respond_with_llm(text)

    def _execute_with_paused_shared_audio(self, text: str) -> Optional[str]:
        """Run a skill while the shared hotword capture is paused.

        The skill (ex. skill_what_you_hear) needs exclusive access to the
        USB capture PCM (pcmC0D0c) held by the shared AudioCapture that
        powers the hotword/VAD listener. Opening a second capture stream
        on the same device fails with PortAudio -9998
        ("Invalid number of channels"). Pausing the shared capture around
        the skill execution releases the PCM so the skill's own
        AudioCapture can open it; the stream is resumed in a `finally`
        so the hotword always comes back even if the skill raises.

        Args:
            text: User text (skill trigger, e.g. "o que ouves?").

        Returns:
            Skill response string, or None if no skill handled it.
        """
        if self.audio_capture:
            pause = self.audio_capture.stop()
            if not pause.success:
                logger.warning(f"Could not pause hotword capture: {pause.error}")

        try:
            return self._skill_loader.execute_skill(text)
        finally:
            if self.audio_capture:
                resume = self.audio_capture.start()
                if not resume.success:
                    logger.error(f"Could not resume hotword capture: {resume.error}")

    @staticmethod
    def _build_messages(prompt: str) -> list:
        """The message list for Ollama, with the persona as a system turn.

        THE PERSONA WAS DEAD HERE. This called `ollama.Client(...).chat()` with
        `messages=[{"role": "user", ...}]` and no system turn at all, while
        `config.SYSTEM_PROMPT` was loaded from prompts/system.txt and validated
        at startup ("refusing to start beats serving an empty or wrong prompt").
        The Phantom's ethical core, its tone and its "respond only in European
        Portuguese" rule therefore never reached the model: every reply was a
        plain user turn. `src/pipeline/llm.py` did build the system role, but
        this path bypasses that module and used the SDK directly.

        If the persona is empty the user turn is sent alone, with a warning --
        a visible degradation rather than the silent one this was.
        """
        # `config` inside this module is the Config INSTANCE (line 24 imports
        # the module as `config_module`), and SYSTEM_PROMPT is a module-level
        # export -- so reading it off `config` yields "" and the persona is
        # dropped again, which is precisely the bug this is fixing. The
        # instance attribute is the reliable one; the module export is the
        # fallback.
        # The store first: this is the persona the owner set in /admin/persona
        # and it must win, per request, over the value frozen at import. The
        # config attribute and the module export are only the shipped default.
        # Reading the config here (and not get_persona) is what made the admin
        # page cosmetic: the save succeeded and the model never saw it.
        system_prompt = str(
            get_persona()
            or getattr(getattr(config, "llm", None), "system_prompt", "")
            or getattr(config_module, "SYSTEM_PROMPT", "")
            or ""
        ).strip()
        if not system_prompt:
            logger.warning("SYSTEM_PROMPT is empty: replying without a persona turn")
            return [{"role": "user", "content": prompt}]
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

    @staticmethod
    def _is_factual_lookup(text: str) -> bool:
        """Decide if this is a factual lookup rather than open conversation.

        One temperature cannot serve both. The persona asks for "gloomy,
        melancholic" prose, which is right when talking and wrong when the
        answer is a fact sitting in the knowledge base: at temperature 0.6 an
        8B model embellishes instead of reporting. Asked what the Capuchinho
        Verde is, with "a pastelaria vegan" in the context, it answered at
        length about a residential security module.
        """
        t = (text or "").strip().lower()
        if not t:
            return False
        # Compare without diacritics. "e'" in text may be U+00E9 or "e" plus
        # a combining acute, and "sao" may be "sao" or "sa~" -- enumerating
        # spellings loses to one of them. Folding first cannot.
        t = (text or "").strip().lower()
        t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
        if not t:
            return False
        openers = (
            "o que e",
            "o que foi",
            "o que sao",
            "quem foi",
            "quem e",
            "quando",
            "onde",
            "quanto",
            "qual",
            "quais",
            "que ano",
            "em que ano",
            "como era",
            "quantos",
            "qual foi",
            "define",
            "o que significa",
            "o que quer dizer",
        )
        if t.startswith(openers):
            return True
        return t.rstrip("?.").endswith(
            (
                "o que e",
                "quem foi",
                "quando foi",
                "onde fica",
                "quanto custa",
            )
        )

    @staticmethod
    def _strip_accents(text: str) -> str:
        """Lowercased and without diacritics, so "vês" and "ves" match alike."""
        return "".join(
            c
            for c in unicodedata.normalize("NFD", text)
            if unicodedata.category(c) != "Mn"
        )

    # The same list _is_opinion matches on, kept in one place because the search
    # subject and the routing decision have to agree: if the router treats a
    # phrase as an opinion and the search does not strip it, the engine looks up
    # the wrapper. That is not hypothetical, it is what SearxNG returned on the
    # first live run.
    OPINION_MARKERS = (
        "o que achas", "o que pensas", "qual achas", "que achas", "como ves",
        "como vês", "o que te parece", "o que lhe parece", "gostas de",
        "gostas do", "gostas da", "gosto de", "gosto do", "concordas",
        "a tua opiniao", "na tua opiniao", "a tua visao", "o que sentes",
    )

    # Only these can be a PREFIX, so only these can be stripped off a query
    # without cutting into the subject. "gostas de" cannot: "gostas de-listedas"
    # is not a thing, but a bare "gostas do" with nothing after it would leave an
    # empty query, so it is handled by the fallback in _search_subject.
    OPINION_PREFIXES = (
        "o que achas", "o que pensas", "qual achas", "que achas", "como ves",
        "como vês", "o que te parece", "o que lhe parece", "o que sentes",
        "concordas", "a tua opiniao", "na tua opiniao", "a tua visao",
    )

    @classmethod
    def _search_subject(cls, text: str) -> str:
        """The part of the question worth looking up.

        Strips the opinion framing so the engine gets "como está o tempo em
        Lisboa" instead of "o que achas de como está o tempo em Lisboa". Only
        applied when there is something left to search; if stripping would empty
        the query, the original text is searched unchanged, because a bare "o
        que achas" is still a question about the house and something is better
        than an empty query.
        """
        raw = (text or "").strip()
        if not raw:
            return raw
        low = cls._strip_accents(raw).lower()
        for prefix in cls.OPINION_PREFIXES:
            if low.startswith(prefix):
                rest = raw[len(prefix):].strip()
                # "o que achas DE X" and "concordas COM X": the preposition is
                # part of the wrapper, and leaving it in makes the engine search
                # for "com isso".
                for lead in ("de ", "do ", "da ", "dos ", "das ", "com ",
                             "about "):
                    if cls._strip_accents(rest).lower().startswith(lead):
                        rest = rest[len(lead):].strip()
                        break
                if rest:
                    return rest
        return raw

    @classmethod
    def _is_opinion(cls, text: str) -> bool:
        """A question about taste, not about a fact.

        Measured 2026-10-03, asked "o que achas do Edgar Allan Poe": the answer
        was a third-person literary review -- "A sua obra e uma denuncia da
        hipocrisia..." -- which is what you get when an 8B is asked a factual
        question in a cold register instead of being asked what it thinks.

        `_is_factual_lookup` already said False here, which was right, but False
        did nothing on its own: `grounded` was True because the graph returned
        "capitalismo tardio" for every question, so temperature stayed at 0.15
        and the persona had no room to speak.

        Matched on substring, not prefix: "e o que achas do X" and "concordas
        com ele" are the same request as "o que achas".
        """
        t = cls._strip_accents((text or "").strip().lower())
        if not t:
            return False
        return any(marker in t for marker in cls.OPINION_MARKERS)

    @staticmethod
    def _render_memory(raw: str) -> str:
        """Turn a stored memory into something a reader can actually use.

        49 of the 61 stored memories are graph fragments -- raw JSON like
        {"tags": ["Vegan", "Pastelaria"], "mermaid": "graph TD; ..."} -- and
        they were handed to the model verbatim. A model handed a JSON blob
        under a heading that says "knowledge" either ignores it or, worse,
        refuses the question: asked what the Capuchinho Verde is, with this
        exact fragment in context, it answered "I cannot provide information
        about it". The knowledge was there and unusable.

        Rendered as plain relational lines it reads as what it is -- a set of
        connected concepts -- which is the knowledge the store actually holds.
        Non-JSON text is passed through untouched.
        """
        import json as _json

        raw = (raw or "").strip()
        if not raw:
            return ""
        # retrieve_from_rag joins several memories with newlines, so this can
        # be many JSON documents, not one. Rendering the whole blob failed to
        # parse and fell through to the raw text -- the original sin. Render
        # each object on its own and keep prose lines as they are.
        #
        # Braces are counted OUTSIDE string literals. Counting them blindly
        # infinite-looped on 9 of the 53 production memories: a `{` inside a
        # string value pushed the depth up, `buf` never emptied, and the chunk
        # handed back still started with `{` and still had more than one, so it
        # re-entered here forever. Reproduced minimally:
        #
        #     _render_memory('{"tags": ["a {b} c"], "facts": ["d"]}')
        #     RecursionError: maximum recursion depth exceeded
        #
        # Any migrated memory with a brace in a value did it, and the assistant
        # was one RAG hit away from a crash on the conversation path.
        if raw.startswith("{") and raw.count("{") > 1:
            chunks, buf, depth = [], [], 0
            in_string = escaped = False
            for ch in raw:
                buf.append(ch)
                if escaped:
                    escaped = False
                elif ch == "\\" and in_string:
                    escaped = True
                elif ch == '"':
                    in_string = not in_string
                elif not in_string:
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            chunks.append("".join(buf))
                            buf = []
            if buf and "".join(buf).strip():
                chunks.append("".join(buf))
            # Progress guard: a chunk identical to the whole input means the
            # brace count found no boundary (unbalanced brace outside a string).
            # Handing it straight back would recurse on the same bytes forever,
            # so it is dropped and the blob goes to the parser, which falls back
            # to raw text. The CORRECT brace count above is what prevents the
            # crash in the real data; this only covers malformed input the
            # counter cannot split.
            splittable = [c for c in chunks if c != raw]
            rendered = [
                PhantasmaPipeline._render_memory(c) for c in splittable
            ]
            if not splittable:
                rendered = []
            rendered = [r for r in rendered if r]
            if len(rendered) > 1:
                return "\n".join(f"- {r}" for r in rendered)
        if not raw.startswith("{"):
            return raw
        try:
            data = _json.loads(raw)
        except (ValueError, TypeError):
            return raw
        if not isinstance(data, dict):
            return raw

        lines: list = []
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if tags:
            lines.append("Conceitos: " + ", ".join(str(t) for t in tags))
        mermaid = str(data.get("mermaid") or "")
        if mermaid:
            # "A --> B" or "A[Label]-->B[Label]" -> "A relaciona-se com B"
            for edge in mermaid.split("\n"):
                edge = edge.strip()
                if not edge or "-->" not in edge:
                    continue
                edge = edge.split("-->", 1)
                left = re.sub(r"[\[\]\(\){}]", "", edge[0]).strip()
                right = re.sub(r"[\[\]\(\){}]", "", edge[1]).strip()
                if left and right:
                    lines.append(f"{left} relaciona-se com {right}")
        # Facts, relations, everything else. The first version of this only
        # kept scalars, so a memory like
        #   {"tags": ["gato","Bimby"], "facts": ["gato -> has name -> Bimby"]}
        # rendered to "Conceitos: gato, Bimby" and the fact was dropped. The
        # model was then asked "quem e o bimby?" holding nothing but the word
        # "Bimby" and answered it had no knowledge. The knowledge was in the
        # store; the renderer threw it away.
        for key, value in data.items():
            if key in ("tags", "mermaid"):
                continue
            if isinstance(value, (str, int, float)) and str(value).strip():
                lines.append(f"{key}: {value}")
            elif isinstance(value, (list, tuple)):
                for item in value:
                    item = str(item).strip()
                    if not item:
                        continue
                    if "->" in item:
                        parts = [x.strip() for x in item.split("->") if x.strip()]
                        if len(parts) >= 2:
                            lines.append(f"  {' -> '.join(parts)}")
                            continue
                    lines.append(f"  {item}")
            elif isinstance(value, dict):
                for k2, v2 in value.items():
                    if str(v2).strip():
                        lines.append(f"  {k2}: {v2}")
        if not lines:
            return raw
        return "\n".join(lines)

    def _speak(self, text: str, use_cache: bool = True):
        """Speak, with the microphone held shut for as long as we are talking.

        The skill path pauses the shared capture so the skill can open the
        PCM, then resumes it in a `finally` -- but the response is spoken
        after that, so the mic was live while the speakers were talking. The
        capture transcribed our own voice, matched it against the skills, and
        re-armed itself: "Liga a luz da sala" turned the light on, the
        spoken confirmation turned it off, and the loop left nobody able to
        get a word in afterwards. Volume at 100% made the bleed certain.

        Holding the mic costs a moment of silence. The loop cost the device.
        """
        # Night mode: during quiet hours the answer is written but not spoken.
        # This is the single choke point both response paths go through, so no
        # route can bypass it. The text is still logged -- night mode silences
        # the speaker, it does not make the assistant mute.
        if quiet.is_quiet():
            logger.info("Quiet hours: not spoken, answer below\n%s", text)
            return Result.ok(None)

        if self.audio_capture:
            self.audio_capture.stop()
        self._speaking = True
        try:
            from audio_utils import play_tts

            play_tts(text, use_cache=use_cache)
        except Exception as exc:  # noqa: BLE001
            logger.warning("TTS failed: %s", exc)
            return Result.fail(str(exc))
        finally:
            # The room keeps ringing after the last sample, and the tail is
            # exactly where a confirmation is still intelligible as a command.
            time.sleep(_speech_seconds(text) + 0.8)
            self._speaking = False
            # Come back to listening whatever happened above. Gating this on
            # "did the stop succeed" was the bug the owner described: a stop
            # that failed left the capture down and the device deaf for good,
            # with no error anywhere to point at. Listening is the default
            # state, so it is restored unconditionally.
            if self.audio_capture is not None:
                try:
                    self.audio_capture.start()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Could not resume listening: %s", exc)
        return Result.ok(None)

    def _respond_with_llm(
        self, text: str, skill_data: Optional[str] = None
    ) -> Optional[str]:
        """Answer through SearXNG + the LLM, and mark the answer as web-derived.

        The flag is cleared in a finally: latched on, it would make every
        later answer uncacheable, which is the opposite of its purpose.

        `skill_data` is the output of a skill that already ran for this same
        message, when the message asked for an opinion rather than a lookup.
        It is passed down rather than re-derived: running the skill twice would
        cost a second HTTP call to a device and could report a different
        reading than the one the answer is about.
        """
        self._web_derived = True
        try:
            return self._respond_with_llm_body(text, skill_data=skill_data)
        finally:
            self._web_derived = False

    def _respond_with_llm_body(
        self, text: str, skill_data: Optional[str] = None
    ) -> Optional[str]:
        # This path goes through SearXNG before the LLM, so its answers are not
        # the owner's own words and are not worth keeping. Set here rather
        # than inferred by the caller, because the caller only sees a string
        # and cannot tell where it came from.
        """LLM fallback for text that no skill handled.

        Full fallback chain: Cache -> RAG + SearXNG -> Ollama with multiple hosts.
        Mirrors the upstream route_and_respond logic.

        Args:
            text: User text.

        Returns:
            Response text, or None on failure/empty response.
        """
        # Step FlyBrain for conversation turn.
        #
        # This MUST run before the cache check. It used to sit below it, so a
        # cache hit returned at line 437 and never stepped -- meaning the brain
        # only ever saw the *first* time something was asked, and FlyBrain
        # state (0 rows in flybrain_state after 61 memories) was starved of
        # turns. There is no documented intent to skip repeats: novelty and
        # reward are hardcoded to the same values on both paths, and
        # topic_angle is a pure function of the text. The response cache is a
        # response cache, not an interaction filter. Batching the write is
        # already handled by maybe_auto_save(); skipping the step entirely
        # loses the turn, not just the write.
        topic_angle = (hash(text) % 3600) / 10.0
        self._fly_brain.step(topic_angle_deg=topic_angle, novelty=0.5, reward=0.0)

        # Check cache first
        cached = get_cached_response(text)
        if cached:
            logger.info("Cache hit for prompt")
            return cached

        # Retrieve RAG memories
        rag = sanitize_llm_context(self._render_memory(retrieve_from_rag(text)))
        logger.debug(f"RAG context length: {len(rag)}")

        # Retrieve memory-graph context (FlyBrain affinity-weighted topics)
        graph_ctx = ""
        try:
            from src.brain.memory_graph import graph_context_text, init_db

            init_db()
            graph_ctx = sanitize_llm_context(graph_context_text(text))
        except Exception as e:
            logger.warning(f"Memory-graph context failed: {e}")
        logger.debug(f"Graph context length: {len(graph_ctx)}")

        # Web search via SearXNG, on every message.
        #
        # There was a version here that skipped the search for a greeting -- "Olá"
        # has no referent, so there was nothing to look up, and searching for it
        # returned a dictionary entry the model then recited. The owner rejected
        # the gate: "deve pesquisar mesmo sem pergunta, tem é de digerir com a
        # persona e o rag no brain". The search IS the intake. Skipping it would
        # have left the brain with nothing new to digest on exactly the turns
        # where the assistant is being talked to.
        #
        # So the search always runs, and the fix lives in the prompt below: the
        # result is digested with the persona and the local knowledge, and what
        # comes out is an answer. Measured 2026-10-03, asked "Olá":
        # rag=0, graph=80, web=517 -- and the answer was Infopédia's definition.
        # Search the SUBJECT, not the opinion wrapper.
        #
        # Measured 2026-10-04 in production, on the first live run of the reading
        # route: "o que achas de como está o tempo em Lisboa?" went to SearxNG
        # whole, and came back with a Spanish thread about the phrase "tal y
        # como está" and Infopédia's dictionary entry for the word "achas". The
        # engine searched for the wrapper because the wrapper was in the query.
        # The reading already answers the question; the web is there for what the
        # sensors cannot know.
        web = sanitize_llm_context(search_with_searxng(self._search_subject(text)))
        logger.debug(f"Web context length: {len(web)}")
        # Say so when the search found nothing. Measured 2026-09-28: from this
        # host every engine refuses us (duckduckgo CAPTCHA, brave/startpage
        # 429+CAPTCHA, mojeek/qwant/yep access denied, seznam 429), so search
        # returns an empty list and the answer was built as if the web had been
        # consulted. The user could not tell that the model was answering from
        # memory alone, which is how a confident wrong answer about an unfamiliar
        # subject looked like a working search.
        web_empty = not (web or "").strip()

        # Build full prompt with context injection
        # Only inject a section when it has something in it. An unconditional
        # "### CONHECIMENTO LOCAL DO PHANTASMA (fonte primaria)" header, sent
        # with an empty body, made the model narrate the scaffolding instead of
        # replying: asked "ola" it answered "Estou aqui para ajudar com
        # qualquer coisa que precise saber sobre o conhecimento local do
        # Phantasma", losing the Phantom entirely, because the prompt told it
        # local knowledge was "a fonte primaria e chega para responder" while
        # there was none. The scaffolding is the model's raw material for
        # talking about the scaffolding.
        local = "\n".join(x for x in (rag, graph_ctx) if (x or "").strip())
        parts = []
        # Reading taken from a skill that just ran for this message. It goes in
        # FIRST and it is labelled as a live reading, because it is the only
        # part of this prompt that was measured seconds ago: the graph is what
        # Phantasma happens to have stored, and the web block is a search that
        # may or may not have covered the question. Without the label, a
        # forecast sitting next to stored opinions gets averaged with them --
        # 17-27 graus next to "gosto de chuva" is how you get "chove, mas
        # talvez" out of two hard facts.
        if (skill_data or "").strip():
            parts.append(
                "### LEITURA DOS DISPOSITIVOS E SERVIÇOS (agora):\n"
                "Isto foi lido agora, dos teus sensores e serviços. É a tua "
                "fonte primária para o que foi medido, e vale mais do que o "
                "guardado e do que a pesquisa. Fala a partir disto, na tua "
                "voz. Não digas que consultaste um sensor, um serviço ou uma "
                "fonte, e não recites a leitura como uma linha de dados: "
                "responde ao que te perguntaram sobre ela.\n"
                "Os números da leitura são o que a casa mediu: se trouxer "
                "uma temperatura, um índice, uma potência ou uma contagem, cita "
                "esse valor na resposta, por extenso ou em algarismos. Não o "
                "substituas por 'frio', 'baixo', 'alto' ou 'muitos' -- quem "
                "perguntou quer o número, e paraphrasear é responder a outra "
                "pergunta. Se o valor não couber na frase, põe a leitura no fim, "
                "com as cifras.\n"
                f"{skill_data}\n"
            )
        if local:
            parts.append(
                "### CONHECIMENTO LOCAL DO PHANTASMA (fonte primária):\n"
                "Isto é o que o Phantasma guardou. Usa estes factos.\n"
                f"{local}\n"
            )
        if (web or "").strip():
            parts.append(f"### PESQUISA WEB\n{web}\n")
            # This block used to say "responde a partir dele e diz de onde veio",
            # and four lines later say "não nomeies o mecanismo". Two opposite
            # instructions, and the model followed the first one. Measured
            # 2026-10-03, asked "Olá": the answer was a dictionary entry --
            # "de acordo com o Dicionário Infopédia..." plus the Wikipedia
            # disambiguation page plus an apartment in Svinoústí. It cited the
            # sources because it had been told to.
            #
            # What replaces "diz de onde veio" is DIGESTION. The owner's words:
            # the search is to be digested with the persona and the RAG that are
            # already in the brain. So the block goes IN, and what comes out is
            # an answer in voice. The web is an intake, not an output -- and
            # never, under any phrasing, a listing.
            parts.append(
                "### COMO RESPONDER COM A PESQUISA\n"
                "O bloco acima entra no teu brain. Digere-o com a tua persona e\n"
                "com o conhecimento local que já tens, e responde do que saiu daí.\n"
                "Nunca o reproduzes.\n"
                "- Não escrevas uma definição de dicionário, nem comeces por\n"
                "  \"X é...\" seguido de significados e de exemplos.\n"
                "- Não enumeres fontes. Não digas \"de acordo com\", \"segundo\",\n"
                "  \"fontes\", \"páginas\", \"resultados\" nem \"a pesquisa\".\n"
                "- Não escrevas URLs, nem títulos de páginas, nem listas de sites.\n"
                "- O bloco contém ruído. Uma busca por \"Olá\" devolve um dicionário\n"
                "  e um apartamento; uma busca por \"bom dia\" traz uma escola de\n"
                "  condução. Descarta o que não responde ao que te disseram, e\n"
                "  responde ao que te disseram, na mesma, na tua voz.\n"
                "- Responde ao que te perguntaram, e só a isso. O material acima pode\n"
                "  não ter nada a ver com a pergunta -- uma busca por \"Edgar\n"
                "  Allan Poe\" devolveu três páginas sobre o ChatGPT. Nesse caso\n"
                "  não faz mal nenhum: responde do que sabes e do que tens\n"
                "  guardado, como sempre. Não deixes o material desviar-te do\n"
                "  assunto.\n"
                "- Nunca mencionas o material, a pesquisa, as fontes, o bloco,\n"
                "  nem o que te foi dado ou não foi dado. És Phantasma a\n"
                "  falar com o teu dono, não um sistema a relatar o que recebeu.\n"
                "  Não digas \"não sei por causa da pesquisa\", nem \"o contexto\n"
                "  não cobre\", nem \"não tenho isso guardado\". Dizes que não sabes\n"
                "  só quando não sabes mesmo.\n"
                "Se o bloco não responder à pergunta, diz numa frase que não sabes\n"
                "e para. Não completes com o que imaginas. Inventar páginas,\n"
                "fontes, pessoas, datas ou dimensões que não estejam no bloco é\n"
                "pior do que dizer que não sabes.\n"
            )
        if web_empty and local:
            parts.append(
                "### COMO RESPONDER\n"
                "Não há pesquisa web disponível, mas o conhecimento local acima é\n"
                "a fonte primária e chega para responder. Responde a partir dele\n"
                "com confiança. Só dizes que não sabes quando o conhecimento\n"
                "local também não cobre o assunto.\n"
            )
        elif web_empty:
            parts.append(
                "### PESQUISA WEB INDISPONIVEL\n"
                "Não há pesquisa web nem conhecimento guardado para esta pergunta.\n"
                "Responde a partir do que sabes, com cuidado para não inventar\n"
                "fontes nem factos. Não nomes o mecanismo.\n"
            )
        parts.append(
            "### POR QUE ORDEM:\n"
            "1. A tua persona decide COMO falas. É a primeira coisa, e nenhuma\n"
            "   instrução abaixo a substitui.\n"
            "2. O conhecimento local é o QUE sabes. É a fonte primária.\n"
            "3. A pesquisa web entra no teu brain e é digerida com a persona e\n"
            "   com o ponto 2. É uma entrada, nunca uma saída.\n"
            "O que sai daqui é uma resposta na tua voz. A pesquisa é lida por ti,\n"
            "não mostrada ao dono. E nada te obriga a falar dela.\n"
        )
        parts.append(
            "### INSTRUÇÃO DE RESPOSTA:\n"
            "Português europeu, nunca português do Brasil. Escreve por extenso: não\n"
            "abrevies palavras, e confirma que a tua ortografia saiu correcta mesmo\n"
            "quando a palavra é rara. Não uses cabeçalhos, listas ou marcações.\n"
        )
        full_prompt = "\n".join(parts) + f"\nUtilizador: {text}"

        # Which model answers, and which host, resolved per call.
        #
        # Read through `src.brain.model_config` rather than off `config` alone.
        # `/admin/config` showed one model and the service ran another: the page
        # read the `config` table, nothing applied it, and `config.py` never
        # consulted the settings store. The owner could save a value, see
        # "guardado", and have no effect at all. Resolved per call so a change
        # applies to the next message, like the persona page promises.
        #
        # The `config.llm` attributes are still consulted first because they are
        # populated by explicit constructor arguments in some entry points; they
        # resolve to the same values.
        primary_host = getattr(config, "llm", None) and getattr(config.llm, "host", None)
        primary_model = getattr(config, "llm", None) and getattr(config.llm, "model", None)
        fallback_host = getattr(config, "llm", None) and getattr(config.llm, "host_fallback", None)
        fallback_model = getattr(config, "llm", None) and getattr(
            config.llm, "model_fallback", None
        )

        from src.brain.model_config import (
            HOST_FALLBACK,
            HOST_PRIMARY,
            MODEL_FALLBACK,
            MODEL_PRIMARY,
            resolve,
        )

        primary_host = primary_host or resolve(HOST_PRIMARY).value
        fallback_host = fallback_host or resolve(HOST_FALLBACK).value
        primary_model = primary_model or resolve(MODEL_PRIMARY).value
        fallback_model = fallback_model or resolve(MODEL_FALLBACK).value

        inference_targets = []
        if primary_host:
            inference_targets.append((primary_host, primary_model))
        if fallback_host and fallback_host != primary_host:
            inference_targets.append((fallback_host, fallback_model))

        if not inference_targets:
            logger.error("No Ollama hosts configured")
            return None

        import ollama

        # Ground the answer first, loosen it only when there is nothing to
        # ground it on. A factual lookup with context in hand is reported, not
        # performed.
        #
        # An opinion is never cold, whatever the context says. Measured
        # 2026-10-03: "o que achas do Edgar Allan Poe" ran at temperature 0.15
        # because `grounded` was true -- the graph returns "capitalismo tardio"
        # for every question, so it almost always is -- and came back as a
        # third-person literary review. Freezing a question about taste is what
        # produces a review nobody asked for.
        grounded = bool((rag or "").strip() or (graph_ctx or "").strip())
        factual = self._is_factual_lookup(text)
        opinion = self._is_opinion(text)
        from src.brain.model_config import temperature as resolve_temperature

        temperature = resolve_temperature(
            factual=factual or (grounded and not opinion)
        )
        logger.info(
            f"LLM temperature {temperature} (factual={factual}, "
            f"opinion={opinion}, grounded={grounded}, rag={len(rag or '')}, "
            f"graph={len(graph_ctx or '')}, web={len(web or '')})"
        )

        for host, model in inference_targets:
            try:
                logger.info(f"Trying Ollama: {host} (model: {model})")
                client = ollama.Client(host=host, timeout=_llm_timeout())
                response = client.chat(
                    model=model,
                    messages=self._build_messages(full_prompt),
                    options={
                        "repeat_penalty": 1.4,
                        "temperature": temperature,
                        "num_ctx": 8192,
                        "top_p": 0.9,
                    },
                )
                response_text = response.get("message", {}).get("content", "").strip()
                if response_text:
                    logger.info(f"Ollama responded via {host} ({model})")
                    return response_text
            except Exception as e:
                logger.warning(f"Ollama {host} failed: {e}")
                continue

        logger.error("All Ollama hosts failed")
        return None

    def _ignore_while_speaking(self) -> bool:
        """True when a chunk arrived while we were talking, so drop it.

        Stopping the capture is the real guard; this is the belt to its
        braces, because the shared capture can be restarted by a skill that
        opens the PCM, and a half-open device leaks.
        """
        if not getattr(self, "_speaking", False):
            return False
        logger.debug("Dropped audio chunk: we were speaking")
        return True

    def _process_speech(self):
        """Process collected speech through STT -> LLM -> TTS -> Playback.

        Pipeline:
        1. Concatenate speech frames
        2. Truncate to max duration (config.pipeline.stt_max_audio_seconds)
        3. STT: transcribe audio to text
        4. LLM: generate response from text
        5. TTS: synthesize response to audio
        6. Playback: play audio via speaker

        Each stage logs via log_stage() for structured observability.
        Failures at any stage are logged and pipeline returns to IDLE.
        """
        if not self._speech_frames:
            return

        # Concatenate speech frames
        speech_audio = np.concatenate(self._speech_frames)

        # Limit max duration
        max_samples = int(config.pipeline.stt_max_audio_seconds * config.audio.sample_rate)
        if len(speech_audio) > max_samples:
            speech_audio = speech_audio[:max_samples]
            logger.warning(f"Speech truncated to {config.pipeline.stt_max_audio_seconds}s")

        logger.info(
            f"Processing speech: {len(speech_audio)} samples "
            f"({len(speech_audio) / config.audio.sample_rate:.1f}s)"
        )

        # STT
        # NOTE: this blocks the audio worker for 15-55s (measured). While
        # blocked the PortAudio callback overflows and the queue drops frames.
        # The damage is contained by hotword.reset() at the end of the cycle;
        # see _process_audio_frame. Measured: 9 dropped frames produced a next
        # score of 0.0009 (the noise floor), not the 0.09-0.49 phantoms seen
        # before the reset existed.
        stt_result = stt_transcribe(speech_audio)
        log_stage(logger, "stt", stt_result)
        if not stt_result.success:
            logger.error(f"STT failed: {stt_result.error}")
            return

        text = stt_result.data
        if not text:
            logger.info("STT returned empty text")
            return

        logger.info(f"User said: {text}")

        # Check skills first (intercept ++/-- before LLM). Skill responses
        # are spoken and returned early — no feedback window is opened so a
        # rapid follow-up command isn't swallowed as feedback.
        skill_response = self._execute_with_paused_shared_audio(text)
        if skill_response is not None:
            logger.info(f"Skill '{text}' handled: {skill_response}")
            # Speak skill response via TTS, served from the cache when the
            # same answer has been spoken before.
            # _speak has already played it, through the cache, with the mic
            # shut. Unpacking .data here killed the listener thread: the
            # Result carries no samples, so every skill response ended in
            # "TypeError: cannot unpack non-iterable NoneType" and the wake
            # word never woke again.
            self._speak(skill_response)
            return

        # LLM fallback shares the response. If None (LLM failure), _process_speech
        # should not open a feedback window either.
        response = self._respond_with_llm(text)
        if response is None:
            return

        # TTS. The cache is read for every answer; it is written only when the
        # answer did not come from SearXNG, so the store fills with the
        # owner's own exchanges and not with retrieved web text.
        tts_result = self._speak(response, use_cache=not getattr(
            self, "_web_derived", False))
        log_stage(logger, "tts", tts_result)
        if not tts_result.success:
            logger.error(f"TTS failed: {tts_result.error}")
            return

        # No unpacking and no second play: _speak reads the cache, plays, and
        # keeps the mic shut while it talks. See the note on the skill path.

        # Start feedback collection window after response
        logger.info(f"Feedback window opened for {self._feedback_window_seconds}s")
        self._collecting_feedback = True
        self._feedback_start_time = time.time()
        self._speech_frames = []
        self._silence_frames = 0

    def _worker_loop(self):
        """Background worker that processes audio queue.

        Runs in daemon thread. Pulls frames from AudioCapture queue
        and processes through VAD/hotword/speech collection state machine.

        Exits when self._running becomes False.
        """
        logger.info("Pipeline worker started")

        while self._running:
            result = self.audio_capture.get_frame(timeout=0.5)
            if not result.success:
                continue

            frame = result.data
            self._process_audio_frame(frame)

        logger.info("Pipeline worker stopped")

    def start(self) -> Result:
        """Start the pipeline.

        Initializes audio capture, spawns worker thread.

        Returns:
            Result.ok(None) on success, Result.fail(error) on failure.
        """
        if self._running:
            return Result.fail("Already running")

        # Start audio capture
        capture_result = self.audio_capture.start()
        if not capture_result.success:
            return capture_result

        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        logger.info("Phantasma pipeline started")
        return Result.ok(None)

    def stop(self) -> Result:
        """Stop the pipeline.

        Signals worker thread to stop, joins thread, stops audio capture.

        Returns:
            Result.ok(None) on success, Result.fail(error) on failure.
        """
        if not self._running:
            return Result.ok(None)

        self._running = False

        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)

        self.audio_capture.stop()

        logger.info("Phantasma pipeline stopped")
        return Result.ok(None)


def _start_api_server(pipeline: PhantasmaPipeline) -> None:
    """Expose the legacy Discord/UI contract (POST /comando) on port 5000,
    plus the /api/* endpoints from src.api.routes.

    Served by a production WSGI server (waitress), not werkzeug's development
    server. gunicorn was evaluated and rejected: it forks, which would force the
    pipeline into the forked worker and break the /comando contract, since a
    command arriving at the API would be enqueued into a copy that never reaches
    the loop owning the microphone. waitress serves in-thread, so the API keeps
    holding the same pipeline object the voice loop owns. The full reasoning is
    in src/api/serve.py; do not "upgrade" this without reading it.

    Failure is non-fatal: the voice assistant keeps running, only the HTTP
    bridge is absent.

    Args:
        pipeline: Running PhantasmaPipeline to route commands through.
    """
    from src.api.serve import start_http_server

    start_http_server(lambda: pipeline)


def run():
    """Main entry point for running the assistant.

    Creates pipeline, sets up signal handlers (SIGINT/SIGTERM),
    starts pipeline, blocks until signal received.
    """
    pipeline = PhantasmaPipeline()
    _prewarm_stt()

    # Signal handling
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        pipeline.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start pipeline
    result = pipeline.start()
    if not result.success:
        logger.error(f"Failed to start pipeline: {result.error}")
        sys.exit(1)

    # Start REST API in background thread (Discord/UI bridge on port 5000).
    # Non-fatal: assistant keeps running if the API cannot start.
    _start_api_server(pipeline)

    # Report the wake words actually configured (PT custom .onnx basenames
    # when present, else model names) — not a hardcoded English phrase.
    hotword_words = [os.path.splitext(os.path.basename(m))[0] for m in config.hotword.models]
    hotword_words = [w for w in hotword_words if w]
    if hotword_words:
        say_words = ", ".join(f"'{w}'" for w in hotword_words)
        logger.info(f"Phantasma is listening... Say {say_words} to activate")
    else:
        logger.info("Phantasma is listening... wake word configured")
    logger.info("Press Ctrl+C to stop")

    # Keep main thread alive
    try:
        while pipeline._running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()


if __name__ == "__main__":
    run()
