"""
Main orchestration loop for pHantasma voice pipeline.

Coordinates the full voice pipeline:
AudioCapture -> VADProcessor -> HotwordDetector ->
WhisperSTT -> OllamaLLM -> PiperTTS -> AudioPlayback

Implements hotword-activated speech collection with
VAD-gated silence detection.
"""

import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

import numpy as np

import config as config_module
from config import config
from data_utils import get_cached_response, retrieve_from_rag
from skills import SkillContext, SkillLoader
from src.brain.fly_brain import FlyBrain
from src.brain.persistence import FlyBrainStore
from src.pipeline.audio import (
    AudioCapture,
    AudioPlayback,
    HotwordDetector,
    VADProcessor,
)
from src.pipeline.stt import transcribe as stt_transcribe
from src.pipeline.tts import synthesize as tts_synthesize
from src.pipeline.utils import Result, log_stage, logger
from tools import search_with_searxng


def sanitize_llm_context(context: str) -> str:
    """Sanitize RAG/web context for LLM injection.

    Removes technical markers, timestamps, and poetic artifacts
    that pollute the context.
    """
    if not context or not isinstance(context, str):
        return ""

    # Remove technical RAG instructions
    context = re.sub(
        r"MEMÓRIAS PESSOAIS.*?\n\n", "", context, flags=re.DOTALL | re.IGNORECASE
    )
    context = re.sub(
        r"NOTA: Se houver contradições.*?\n", "", context, flags=re.IGNORECASE
    )

    # Remove timestamps and technical IDs
    context = re.sub(r"\[\d{4}-\d{2}-\d{2}.*?\]", "", context)

    # Remove poetic artifacts from RAG
    poison_terms = ["Sombra", "Aquietação", "Fim", "Silêncio", "Fúria da Memória"]
    for term in poison_terms:
        context = re.sub(rf"\*\*{term}\*\*", "", context, flags=re.IGNORECASE)
        context = re.sub(rf"{term}:", "", context, flags=re.IGNORECASE)

    return context.strip()


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
            chunk_size=config.audio.block_size,
        )

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
            music_files = [
                f for f in os.listdir(music_dir) if f.endswith((".mp3", ".wav", ".ogg"))
            ]

            if music_files:
                music_file = os.path.join(music_dir, random.choice(music_files))
                # Use mpg123 for mp3, sounddevice for wav/ogg
                if music_file.endswith(".mp3"):
                    subprocess.run(["mpg123", "-q", music_file], check=False, timeout=5)
                else:
                    import soundfile as sf

                    data, sr = sf.read(music_file, dtype="int16")
                    self.audio_playback.play(data)

            # Play greeting
            greeting_path = config.audio_feedback.greeting_path
            if os.path.exists(greeting_path):
                import soundfile as sf

                data, sr = sf.read(greeting_path, dtype="int16")
                self.audio_playback.play(data)

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
            logger.info(
                "FlyBrain updated: "
                f"reward={reward}, affinity={self._fly_brain.mb.affinity:.3f}"
            )
        else:
            logger.debug(f"No feedback keyword in: '{text}'")

        # Exit feedback collection mode
        self._collecting_feedback = False
        self._feedback_start_time = None

    def _process_audio_frame(self, frame: np.ndarray):
        """Process single audio frame through VAD and hotword detection.

        Implements the main state machine:
        - IDLE: Run hotword detection on VAD frames
        - COLLECTING: Accumulate speech frames, count silence frames
          Transition to PROCESSING when silence_frames >= max_silence_frames
        - FEEDBACK: After response, collect user feedback for configured window

        Args:
            frame: Raw audio frame from AudioCapture (int16, block_size samples).
        """
        # Run VAD on frame
        vad_frames = self.vad.process_chunk(frame)

        for vad_frame in vad_frames:
            # Check feedback window timeout
            if self._collecting_feedback and self._feedback_start_time:
                elapsed = time.time() - self._feedback_start_time
                if elapsed >= self._feedback_window_seconds:
                    logger.debug("Feedback window expired")
                    self._collecting_feedback = False
                    self._feedback_start_time = None

            if self._collecting_feedback:
                # Feedback collection mode - accumulate speech for STT
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
                # Hotword detection mode (IDLE)
                detected, model = self.hotword.process(vad_frame.data)
                if detected:
                    logger.info(f"Hotword detected: {model}")
                    self._hotword_detected = True
                    self._collecting_speech = True
                    self._speech_frames = []
                    self._silence_frames = 0

                    # Play audio feedback (music + greeting)
                    self._play_audio_feedback()
            else:
                # Speech collection mode
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

    def _respond_with_llm(self, text: str) -> Optional[str]:
        """LLM fallback for text that no skill handled.

        Full fallback chain: Cache -> RAG + SearXNG -> Ollama with multiple hosts.
        Mirrors the upstream route_and_respond logic.

        Args:
            text: User text.

        Returns:
            Response text, or None on failure/empty response.
        """
        # Check cache first
        cached = get_cached_response(text)
        if cached:
            logger.info("Cache hit for prompt")
            return cached

        # Step FlyBrain for conversation turn
        topic_angle = (hash(text) % 3600) / 10.0
        self._fly_brain.step(topic_angle_deg=topic_angle, novelty=0.5, reward=0.0)

        # Retrieve RAG memories
        rag = sanitize_llm_context(retrieve_from_rag(text))
        logger.debug(f"RAG context length: {len(rag)}")

        # Web search via SearXNG (only if no skill context yet)
        web = sanitize_llm_context(search_with_searxng(text))
        logger.debug(f"Web context length: {len(web)}")

        # Build full prompt with context injection
        sys_prompt = getattr(config, "llm", None) and getattr(
            config.llm, "system_prompt", ""
        )
        if not sys_prompt:
            sys_prompt = getattr(config, "SYSTEM_PROMPT", "")

        full_prompt = (
            f"{sys_prompt}\n\n"
            "### CONHECIMENTO DISPONÍVEL (Usa apenas para factos):\n"
            f"{rag}\n{web}\n\n"
            "### INSTRUÇÃO DE RESPOSTA:\n"
            "Responde de forma fluida e natural em português. "
            "NÃO uses cabeçalhos ou marcações. Sê um assistente útil.\n\n"
            f"Utilizador: {text}"
        )

        # Try Ollama with primary and fallback hosts
        primary_host = getattr(config, "llm", None) and getattr(
            config.llm, "host", None
        )
        primary_model = getattr(config, "llm", None) and getattr(
            config.llm, "model", None
        )
        fallback_host = getattr(config, "llm", None) and getattr(
            config.llm, "host_fallback", None
        )
        fallback_model = getattr(config, "llm", None) and getattr(
            config.llm, "model_fallback", None
        )

        if not primary_host:
            primary_host = getattr(config, "OLLAMA_HOST_PRIMARY", None)
            primary_model = primary_model or getattr(
                config, "OLLAMA_MODEL_PRIMARY", "llama3.1:8b"
            )
        if not fallback_host:
            fallback_host = getattr(
                config, "OLLAMA_HOST_FALLBACK", "http://localhost:11434"
            )
            fallback_model = fallback_model or getattr(
                config, "OLLAMA_MODEL_FALLBACK", "llama3"
            )

        inference_targets = []
        if primary_host:
            inference_targets.append((primary_host, primary_model or "llama3.1:8b"))
        if fallback_host and fallback_host != primary_host:
            inference_targets.append((fallback_host, fallback_model or "llama3"))

        if not inference_targets:
            logger.error("No Ollama hosts configured")
            return None

        import ollama

        for host, model in inference_targets:
            try:
                logger.info(f"Trying Ollama: {host} (model: {model})")
                client = ollama.Client(host=host)
                response = client.chat(
                    model=model,
                    messages=[{"role": "user", "content": full_prompt}],
                    options={
                        "repeat_penalty": 1.4,
                        "temperature": 0.6,
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
        max_samples = int(
            config.pipeline.stt_max_audio_seconds * config.audio.sample_rate
        )
        if len(speech_audio) > max_samples:
            speech_audio = speech_audio[:max_samples]
            logger.warning(
                f"Speech truncated to {config.pipeline.stt_max_audio_seconds}s"
            )

        logger.info(
            f"Processing speech: {len(speech_audio)} samples "
            f"({len(speech_audio) / config.audio.sample_rate:.1f}s)"
        )

        # STT
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
            # Speak skill response via TTS
            tts_result = tts_synthesize(skill_response)
            if tts_result.success:
                audio_data, sample_rate = tts_result.data
                self.audio_playback.play(audio_data)
            return

        # LLM fallback shares the response. If None (LLM failure), _process_speech
        # should not open a feedback window either.
        response = self._respond_with_llm(text)
        if response is None:
            return

        # TTS
        tts_result = tts_synthesize(response)
        log_stage(logger, "tts", tts_result)
        if not tts_result.success:
            logger.error(f"TTS failed: {tts_result.error}")
            return

        audio_data, sample_rate = tts_result.data

        # Playback
        playback_result = self.audio_playback.play(audio_data)
        log_stage(logger, "playback", playback_result)
        if not playback_result.success:
            logger.error(f"Playback failed: {playback_result.error}")

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
    """Start the Flask REST API in a background daemon thread.

    Exposes the legacy Discord/UI contract (POST /comando) on port 5000,
    plus the /api/* endpoints from src.api.routes. Failure is non-fatal:
    the voice assistant keeps running, only the HTTP bridge is absent.

    Args:
        pipeline: Running PhantasmaPipeline to route commands through.
    """
    try:
        from src.api.routes import create_app

        app = create_app(pipeline=pipeline)
        thread = threading.Thread(
            target=app.run,
            kwargs={
                "host": "0.0.0.0",
                "port": int(os.getenv("PHANTASMA_API_PORT", "5000")),
                "debug": False,
                "use_reloader": False,
                "threaded": True,
            },
            daemon=True,
            name="api-server",
        )
        thread.start()
        logger.info("REST API started on 0.0.0.0:5000 (Discord/UI bridge)")
    except Exception as e:
        logger.warning(f"REST API failed to start (non-fatal): {e}")


def run():
    """Main entry point for running the assistant.

    Creates pipeline, sets up signal handlers (SIGINT/SIGTERM),
    starts pipeline, blocks until signal received.
    """
    pipeline = PhantasmaPipeline()

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
    hotword_words = [
        os.path.splitext(os.path.basename(m))[0] for m in config.hotword.models
    ]
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
