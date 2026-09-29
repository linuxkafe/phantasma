"""
Audio I/O, VAD, and hotword detection.

This module provides:
- AudioFrame: Container for audio data with metadata
- VADProcessor: WebRTC VAD wrapper for frame-level speech detection
- HotwordDetector: openWakeWord-based hotword detection with cooldown
- AudioCapture: sounddevice input stream with thread-safe queue
- AudioPlayback: sounddevice output stream for TTS playback

All classes are designed for real-time audio processing with minimal latency.
"""

import os
import queue
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Union

import numpy as np
import openwakeword
import openwakeword.model
import sounddevice as sd
import webrtcvad

from config import config
from src.pipeline.noise import NoiseFloor
from src.pipeline.utils import Result, logger


def alsa_card_for_portaudio_device(device_index: int) -> Optional[int]:
    """Resolve the ALSA card number behind a PortAudio device index.

    These are different namespaces and the legacy code conflated them: it
    called ``amixer -c <portaudio_index>``. That happened to work here only
    because PortAudio device 0 is ``hw:0,0``, i.e. ALSA card 0. On a machine
    with any other device enumerated first it would have configured the wrong
    card's capture gain.

    Args:
        device_index: PortAudio device index.

    Returns:
        ALSA card number, or None if the device name carries no ``hw:N,M``.
    """
    try:
        name = sd.query_devices(device_index)["name"]
    except Exception:
        return None
    match = re.search(r"hw:(\d+),", name)
    if not match:
        return None
    return int(match.group(1))


def _capture_controls(card_index: int) -> list[str]:
    """List the ALSA simple controls on a card, empty on any failure."""
    try:
        result = subprocess.run(
            ["amixer", "-c", str(card_index), "scontrols"],
            capture_output=True,
            text=True,
        )
        return re.findall(r"Simple mixer control '([^']+)'", result.stdout)
    except Exception as e:
        logger.warning(f"⚠️ Erro ao ler controlos do card {card_index}: {e}")
        return []


def set_capture_volume(card_index: int, target: int):
    """Set capture gain to ``target`` percent, leaving AGC untouched.

    Split out from force_volume_down so gain and AGC can be decided
    independently. They are not the same decision: turning AGC off changes
    how loud speech sounds, which invalidates any wake word threshold measured
    while AGC was enabled. Gain alone changes level without changing the
    transfer curve.

    Args:
        card_index: ALSA card number (not a PortAudio index).
        target: Capture volume percentage.
    """
    logger.info(f"🎚️ A configurar captura no Card {card_index} (Alvo: {target}%)...")
    for ctrl in _capture_controls(card_index):
        if any(x in ctrl for x in ["PCM", "Master", "Speaker", "Headphone", "Playback"]):
            continue
        if "Capture" in ctrl or "Mic" in ctrl:
            logger.info(f"   ↘ Ajustando ganho: '{ctrl}' -> {target}%")
            subprocess.run(
                [
                    "amixer",
                    "-c",
                    str(card_index),
                    "sset",
                    ctrl,
                    f"{target}%",
                    "unmute",
                    "cap",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def force_volume_down(card_index):
    """Apply capture gain and disable AGC.

    AGC is disabled because automatic gain makes the wake word's absolute
    score depend on how quiet the room is. Any threshold calibrated with AGC on
    must be re-measured with it off.
    """
    target = getattr(config.audio, "volume_percent", 85)
    set_capture_volume(card_index, target)
    for ctrl in _capture_controls(card_index):
        if "AGC" in ctrl or "Auto Gain" in ctrl:
            logger.info(f"   🚫 A desativar AGC: '{ctrl}'")
            subprocess.run(
                ["amixer", "-c", str(card_index), "sset", ctrl, "off"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def find_working_samplerate(device_index):
    candidates = [16000, 48000, 44100, 32000]
    logger.info(f"🕵️ A negociar Sample Rate para o device {device_index}...")
    for rate in candidates:
        try:
            with sd.InputStream(device=device_index, channels=1, samplerate=rate, dtype="int16"):
                pass
            logger.info(f"✅ Hardware aceitou: {rate} Hz")
            return rate
        except Exception:
            continue
    logger.warning("⚠️ Nenhuma sample rate aceita, fallback 16000 Hz")
    return 16000


@dataclass
class AudioFrame:
    """Single audio frame with metadata.

    Attributes:
        data: Audio samples as int16 numpy array, shape (samples,).
        timestamp: Unix timestamp when frame was captured (time.time()).
        is_speech: Whether VAD detected speech in this frame.
    """

    data: np.ndarray  # int16, shape (samples,)
    timestamp: float
    is_speech: bool = False


class VADProcessor:
    """WebRTC VAD wrapper for frame-level speech detection.

    Splits audio chunks into fixed-size frames and runs WebRTC VAD on each.
    Used to filter non-speech audio before hotword detection and during
    speech collection.

    Attributes:
        vad: Underlying webrtcvad.Vad instance.
        sample_rate: Audio sample rate in Hz (must match VAD supported rates).
        frame_duration_ms: Frame duration in ms (10, 20, or 30).
        frame_size: Number of samples per frame
            (sample_rate * frame_duration_ms / 1000).
        _bytes_per_frame: Frame size in bytes (frame_size * 2 for int16).
    """

    def __init__(
        self,
        aggressiveness: int = 2,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
    ):
        """Initialize VAD processor.

        Args:
            aggressiveness: VAD aggressiveness 0-3. Higher = more aggressive
                filtering (fewer false positives, more false negatives). Default 2.
            sample_rate: Audio sample rate in Hz. Must be 8000, 16000, 32000, or 48000.
                Default 16000 (Whisper compatible).
            frame_duration_ms: Frame duration in ms. Must be 10, 20, or 30. Default 30.

        Raises:
            ValueError: If parameters are invalid (validated by webrtcvad).
        """
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(sample_rate * frame_duration_ms / 1000)
        self._bytes_per_frame = self.frame_size * 2  # int16 = 2 bytes
        # Partial-frame carry-over. See process_chunk: without it, 6.25% of
        # all audio is discarded because 512-sample blocks do not divide into
        # 480-sample frames.
        self._accum = np.zeros(0, dtype=np.int16)

    def is_speech(self, frame: np.ndarray) -> bool:
        """Check if frame contains speech.

        Args:
            frame: Audio frame as numpy array. Will be padded/truncated to frame_size.

        Returns:
            True if speech detected, False otherwise.
        """
        if len(frame) != self.frame_size:
            # Pad or truncate to exact frame size
            if len(frame) < self.frame_size:
                frame = np.pad(frame, (0, self.frame_size - len(frame)), mode="constant")
            else:
                frame = frame[: self.frame_size]
        # Convert to bytes (int16 little-endian)
        frame_bytes = frame.astype(np.int16).tobytes()
        return self.vad.is_speech(frame_bytes, self.sample_rate)

    def process_chunk(self, chunk: np.ndarray) -> list[AudioFrame]:
        """Split chunk into frames and run VAD on each.

        Buffers across calls so no audio is lost. AudioCapture delivers
        config.audio.block_size (512 samples = 32ms) while this processor
        needs 30ms (480 samples). The previous implementation computed
        ``len(chunk) // frame_size`` and returned, silently discarding the
        32-sample remainder on every single chunk: 6.25% of all audio, as a
        2ms gap every 32ms. Chops like that degrade speech detection, which is
        what made end-of-speech unreliable (utterances ran to 15.3s).

        This is the same class of defect as the wake word framing: a consumer
        whose input size did not divide its work size.

        Args:
            chunk: Audio chunk as numpy array (int16 or float32).

        Returns:
            List of AudioFrame objects with VAD results.
        """
        frames = []
        self._accum = np.concatenate([self._accum, chunk])
        num_frames = len(self._accum) // self.frame_size
        for i in range(num_frames):
            frame_data = self._accum[i * self.frame_size : (i + 1) * self.frame_size]
            is_speech = self.is_speech(frame_data)
            frames.append(AudioFrame(data=frame_data, timestamp=time.time(), is_speech=is_speech))
        # Carry the remainder instead of dropping it.
        self._accum = self._accum[num_frames * self.frame_size :]
        return frames

    def reset(self):
        """Drop any partial frame left over from a previous utterance.

        Without this, a stale partial frame from before a wake word is spliced
        onto the silence that follows, which can both delay and fabricate
        end-of-speech.
        """
        self._accum = np.zeros(0, dtype=np.int16)


class HotwordDetector:
    """openWakeWord hotword detection.

    Matches the working legacy implementation from /home/seyon/dev/tmp/old-phant/pha/assistant.py.
    Processes 1280-sample chunks (80ms at 16kHz) directly as int16.

    Attributes:
        models: List of model names to detect.
        threshold: Detection confidence threshold 0.0-1.0.
        chunk_size: Expected audio chunk size in samples (1280 for 80ms at 16kHz).
        oww_model: Underlying openwakeword.model.Model instance.
        _cooldown: Cooldown period in seconds (from config).
        _last_detection: Timestamp of last detection.
        _streaks: Per-model detection streak counters.
        _max_patience: Max patience for tolerance (2 frames).
        _persistence: Number of consecutive frames needed for detection.
    """

    def __init__(
        self,
        models: list[str],
        threshold: float = 0.7,  # Match working legacy WAKEWORD_CONFIDENCE
        sample_rate: int = 16000,
        chunk_size: int = 1280,  # 80ms at 16kHz - openWakeWord standard
        persistence: int | None = None,
    ):
        """Initialize hotword detector.

        Args:
            models: List of model names/paths to load.
            threshold: Detection threshold 0.0-1.0. Default 0.7 (legacy).
            sample_rate: Audio sample rate in Hz. Default 16000.
            chunk_size: Audio chunk size in samples. Default 1280.

        Raises:
            RuntimeError: If openWakeWord model fails to load.
        """
        self.models = models
        self.threshold = threshold
        # Per-model thresholds, falling back to the global one. See
        # HotwordConfig.thresholds_per_model for why a single value cannot
        # serve both models.
        self._thresholds = dict(config.hotword.thresholds_per_model)
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size

        # Track last detection for cooldown
        self._last_detection = 0.0
        self._cooldown = config.hotword.cooldown_seconds
        self._persistence = config.hotword.persistence if persistence is None else persistence
        self._streaks = {}
        # Patience must persist ACROSS calls. In the working legacy this was a
        # variable in the enclosing function scope; as a local in process() it
        # was re-initialised on every frame, so a single dip could never reset
        # the streak and the "tolerance" the legacy relied on did not exist.
        self._patience = {}
        # Last raw model scores, for diagnostics. See process().
        self.last_predictions: dict = {}
        # Partial-window carry-over. See process() for why framing is enforced.
        self._accum = np.zeros(0, dtype=np.int16)
        self._max_patience = 2
        # Ambient noise floor. Fed from the raw input, used to raise the
        # threshold when the room is noisy enough to make the model unreliable.
        # See src/pipeline/noise.py and HotwordConfig.noise_adaptive.
        self._noise = NoiseFloor(
            quiet_db=config.hotword.noise_quiet_db,
            loud_db=config.hotword.noise_loud_db,
            max_bump=config.hotword.noise_max_bump if config.hotword.noise_adaptive else 0.0,
        )
        self._last_noise_log = 0.0

        # Custom model paths resolve to existing files -> load them directly.
        # This is how the PT wake words (models/ola_fantasma.onnx etc.) are used.
        custom_paths = [p for p in models if os.path.isfile(p)]
        if custom_paths:
            logger.info(f"Loading custom wake word models: {custom_paths}")
            # NO sr/ncpu params - match working legacy implementation
            self.oww_model = openwakeword.model.Model(
                wakeword_models=custom_paths,
                inference_framework="onnx",
            )
            self._prime_buffer()
            return

        # Load openWakeWord model - use pre-trained models if paths not provided
        model_paths = openwakeword.get_pretrained_model_paths()
        filtered_paths = []
        for path in model_paths:
            model_name = os.path.basename(path).replace(".onnx", "")
            if model_name in models:
                filtered_paths.append(path)

        if not filtered_paths:
            logger.warning(
                f"No configured wake word models found ({models}); "
                "falling back to ALL pretrained openWakeWord models."
            )

        self.oww_model = openwakeword.model.Model(
            wakeword_models=filtered_paths if filtered_paths else model_paths,
            inference_framework="onnx",
        )

    def effective_threshold(self, model_name: str) -> float:
        """The bar this model must clear right now: its base, plus the room's.

        Falls back to the flat base until the floor has heard the room, so a
        cold start behaves exactly as it did before noise adaptation existed.
        """
        base = self._thresholds.get(model_name, self.threshold)
        return min(base + self._noise.penalty(), 0.99)

    def noise_state(self) -> str:
        """One-line description of the floor, for logs and the admin page."""
        return self._noise.describe()

    def reject_detection(self) -> None:
        """Refuse a detection the rest of the system is not going to accept.

        Distinct from reset() on purpose. reset() clears ``_last_detection``,
        which would let a continuous noise source be re-scored on the very next
        frame and rejected again -- a reset storm, each one re-priming the mel
        buffer. Rejecting must still respect the cooldown.

        Clearing the streaks is the point: a rejected window must not count
        towards the next model's persistence, and the mel buffer is re-primed
        because it now describes a wake word nothing acted on.
        """
        self._streaks.clear()
        self._patience.clear()
        self._last_detection = time.time()
        self._accum = np.zeros(0, dtype=np.int16)
        if getattr(self, "oww_model", None) is not None:
            self.oww_model.reset()
            self._prime_buffer()

    def process(self, audio_chunk: np.ndarray) -> tuple[bool, Optional[str]]:
        """Process 1280-sample int16 chunk for hotword detection.

        Matches the working legacy: passes int16 directly to predict().

        Args:
            audio_chunk: Audio data as numpy array (int16), must be 1280 samples.

        Returns:
            Tuple of (detected: bool, model_name: Optional[str]).
        """
        now = time.time()

        # Cooldown check
        if now - self._last_detection < self._cooldown:
            return False, None

        # openWakeWord accepts int16 directly (like legacy)
        if audio_chunk.dtype != np.int16:
            audio_chunk = audio_chunk.astype(np.int16)

        # --- Framing: always predict on exactly one 80ms window ---
        #
        # AudioCapture delivers 512-sample blocks (config.audio.block_size),
        # but openWakeWord only produces a NEW score every 1280 samples. Left
        # unbuffered, the same score was returned ~2.5 times in a row and the
        # persistence counter accumulated those repeats. Measured consequence:
        # the same audio fired at persistence=3 with 512-sample input and never
        # fired with 1280-sample input. So the configured persistence did not
        # mean "N independent windows" -- it meant "N/2.5 windows", and its
        # meaning depended on an unrelated config value.
        #
        # Buffering here makes persistence count independent 80ms windows
        # regardless of block size, and makes the live path agree with both
        # _prime_buffer (already 1280) and tests/fixtures (also 1280).
        self._accum = np.concatenate([self._accum, audio_chunk])
        if len(self._accum) < self.chunk_size:
            return False, None
        window = self._accum[: self.chunk_size]
        self._accum = self._accum[self.chunk_size :]

        # Same window the model is about to score, so the floor and the score
        # describe the same audio. Fed BEFORE predict() so the threshold used
        # on this very window already reflects the room.
        self._noise.update(window)
        if time.time() - self._last_noise_log > 30.0:
            self._last_noise_log = time.time()
            logger.debug(self._noise.describe())

        predictions = self.oww_model.predict(window)

        if not predictions:
            self.last_predictions = {}
            return False, None

        # Exposed so the pipeline can report the score distribution. Without
        # this, a real wake word scoring BELOW threshold is invisible: no log,
        # no dump, no way to tell "too strict" from "user did not speak".
        self.last_predictions = dict(predictions)

        # Log raw predictions when any score is above 0.05
        max_score = max(predictions.values())
        if max_score > 0.05:
            # DEBUG, not INFO: this fired on virtually every speech frame and
            # flooded the journal (80 lines/second of audio).
            logger.debug(f"🔍 Predictions: {predictions}")

        # --- LÓGICA DE DETECÇÃO COM TOLERÂNCIA (match legacy) ---
        for model_name, score in predictions.items():
            # Per-model threshold, raised by the room's noise floor. The legacy
            # code compared every model against one global value, which is what
            # left hey_fantasma unusable.
            model_threshold = self.effective_threshold(model_name)

            if score >= model_threshold:
                bar = "█" * int(score * 20)
                logger.info(
                    f"👻 Wake word '{model_name}' probability: {score:.4f} "
                    f"(thr {model_threshold:.2f}) | {bar}"
                )

            streak = self._streaks.get(model_name, 0)
            patience = self._patience.get(model_name, 0)

            if score >= model_threshold:
                streak += 1
                self._streaks[model_name] = streak
                patience = self._max_patience  # Reset da paciência se acertou
            else:
                if streak > 0 and patience > 0:
                    patience -= 1  # Não zera o streak, apenas gasta paciência
                else:
                    streak = 0
                    patience = 0  # Zera tudo
            self._patience[model_name] = patience

            if streak >= self._persistence:
                logger.info(f"\n⚡ WAKEWORD DETETADA! (Score final: {score:.2f})")
                self._last_detection = now
                self._streaks.clear()
                self._patience.clear()
                return True, model_name

        return False, None

    def _prime_buffer(self, frames: int = 96):
        """Fill openWakeWord's rolling mel buffer with SILENCE, deterministically.

        openWakeWord seeds ``feature_buffer`` with
        ``np.random.randint(-1000, 1000, ...)`` — unseeded random noise. The
        model reads a 76-frame context window, so for the first ~6 seconds of
        audio the predictions depend on that random noise.

        Measured consequence in this project: identical audio produced
        different verdicts between runs. Two streams sharing their first 16
        frames ("olá fantasma" twice vs "olá" then "hey") one fired and one did
        not, purely because of leftover random state.

        Priming with true silence removes the non-determinism: the same clip
        always yields the same scores. It does not raise the scores, so it does
        not manufacture detections — it makes them reproducible.
        """
        if getattr(self, "oww_model", None) is None:
            return
        silence = np.zeros(self.chunk_size, dtype=np.int16)
        for _ in range(frames):
            self.oww_model.predict(silence)

    def reset(self):
        """Reset cooldown, streaks, patience AND the model's feature buffers.

        Three defects were fixed here:

        1. ``reset()`` was defined twice. Python keeps the last definition, so
           the version that cleared ``_streaks`` was dead code and never ran.
        2. It did not call ``oww_model.reset()``. openWakeWord's Model keeps a
           rolling mel-spectrogram buffer, so the first detections after a
           reset were computed against a stale (and randomly seeded) buffer.
        3. It left the buffer randomly seeded, making detection
           non-deterministic. It is now re-primed with silence.
        """
        self._last_detection = 0.0
        self._streaks.clear()
        self._patience.clear()
        # Drop any partial window. After reset() the model's mel buffer holds
        # silence; a leftover half-window from before the reset would splice
        # pre-reset audio onto that silence and defeat the point of priming.
        self._accum = np.zeros(0, dtype=np.int16)
        if getattr(self, "oww_model", None) is not None:
            self.oww_model.reset()
            self._prime_buffer()


class AudioCapture:
    """Manages sounddevice input stream with callback feeding a queue.

    Runs audio capture in a separate thread via sounddevice callback.
    Frames are pushed to a thread-safe queue for consumption by pipeline worker.

    Attributes:
        device: ALSA device name/index or None for default.
        sample_rate: Sample rate in Hz.
        channels: Number of channels.
        block_size: Samples per callback block.
        dtype: NumPy dtype for audio data.
        queue: Thread-safe queue for audio frames.
        _stream: Active sounddevice InputStream or None.
        _running: Whether capture is active.
    """

    def __init__(
        self,
        device: Optional[Union[int, str]] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        block_size: int = 1600,
        dtype: str = "int16",
        queue_maxsize: int = 50,
    ):
        """Initialize audio capture.

        Args:
            device: ALSA device name (e.g., "hw:1,0") or index. None = default.
            sample_rate: Sample rate in Hz. Default 16000.
            channels: Number of channels. Default 1 (mono).
            block_size: Samples per callback. Default 1600 (100ms at 16kHz).
            dtype: NumPy dtype. Default "int16".
            queue_maxsize: Max frames in queue. Default 10. Prevents memory buildup.
        """
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_size = block_size
        self.dtype = dtype
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=queue_maxsize)
        self._stream: Optional[sd.InputStream] = None
        self._running = False
        self._downsample_factor = 1

    def _callback(self, indata, frames, time_info, status):
        """sounddevice callback - runs in audio thread.

        This runs in a high-priority audio thread. Must be non-blocking.
        Copies data to avoid buffer reuse issues.

        Args:
            indata: Input audio buffer (numpy array).
            frames: Number of frames.
            time_info: Timing information.
            status: Callback status flags.
        """
        if status:
            logger.warning(f"Audio input status: {status}")
        if self._running:
            # Copy data to avoid buffer reuse issues
            try:
                audio_copy = indata.copy().flatten()
                if self._downsample_factor > 1:
                    audio_copy = audio_copy[:: self._downsample_factor]
                self.queue.put_nowait(audio_copy)
            except queue.Full:
                # Drop oldest frame to prevent blocking audio thread
                try:
                    self.queue.get_nowait()
                    audio_copy = indata.copy().flatten()
                    if self._downsample_factor > 1:
                        audio_copy = audio_copy[:: self._downsample_factor]
                    self.queue.put_nowait(audio_copy)
                except queue.Empty:
                    pass

    def start(self) -> Result:
        """Start audio capture.

        Resolves device name to index if needed, creates and starts
        sounddevice InputStream with callback.

        Returns:
            Result.ok(None) on success, Result.fail(error) on failure.
        """
        try:
            # Resolve device index from name if needed
            device_idx = self.device
            if isinstance(self.device, str):
                devices = sd.query_devices()
                for i, dev in enumerate(devices):
                    if self.device in dev["name"]:
                        device_idx = i
                        break

            # Capture gain is applied unconditionally, NOT under auto_detect.
            # It used to be gated behind that flag, which also controls sample
            # rate negotiation -- two unrelated concerns sharing one switch.
            # Because AUDIO_AUTO_DETECT is false in production,
            # ALSA_VOLUME_PERCENT was inert: the config claimed 85% while the
            # hardware sat at the system default, and every level judgement
            # drawn from it was reasoning about a number with no effect.
            if isinstance(device_idx, int):
                card = alsa_card_for_portaudio_device(device_idx)
                if card is not None:
                    if config.audio.disable_agc:
                        force_volume_down(card)
                    else:
                        set_capture_volume(card, config.audio.volume_percent)
                else:
                    logger.warning(
                        f"Could not resolve an ALSA card from PortAudio device "
                        f"{device_idx}; capture gain left at system default"
                    )

            # Sample rate negotiation stays behind auto_detect: it changes
            # blocksize and adds a downsample factor, which is a real
            # behavioural change and not something to enable by accident.
            if config.audio.auto_detect and isinstance(device_idx, int):
                detected_sr = find_working_samplerate(device_idx)
                # Downsample logic to mimic old assistant.py
                base_block = 1280
                factor = 1
                if detected_sr == 48000:
                    factor = 3
                elif detected_sr == 32000:
                    factor = 2
                self._downsample_factor = factor
                self.sample_rate = detected_sr
                self.block_size = base_block * factor
                logger.info(
                    f"🎚️ Sample rate ajustado: {self.sample_rate} Hz | fator downsample {factor}x | blocksize {self.block_size}"
                )
            else:
                self._downsample_factor = 1

            self._stream = sd.InputStream(
                device=device_idx,
                samplerate=self.sample_rate,
                channels=self.channels,
                blocksize=self.block_size,
                dtype=self.dtype,
                callback=self._callback,
            )
            self._stream.start()  # type: ignore[union-attr]
            self._running = True
            logger.info(f"👂 Audio capture started: device={self.device}, sr={self.sample_rate}")
            return Result.ok(None)
        except Exception as e:
            logger.error(f"Failed to start audio capture: {e}")
            return Result.fail(str(e))

    def stop(self) -> Result:
        """Stop audio capture.

        Stops and closes the sounddevice stream.

        Returns:
            Result.ok(None) on success, Result.fail(error) on failure.
        """
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
                self._stream = None
                logger.info("Audio capture stopped")
                return Result.ok(None)
            except Exception as e:
                logger.error(f"Error stopping audio capture: {e}")
                return Result.fail(str(e))
        return Result.ok(None)

    def get_frame(self, timeout: float = 1.0) -> Result:
        """Get next audio frame from queue.

        Args:
            timeout: Max seconds to wait for frame. Default 1.0s.

        Returns:
            Result.ok(frame) with numpy array, or Result.fail("timeout").
        """
        try:
            frame = self.queue.get(timeout=timeout)
            return Result.ok(frame)
        except queue.Empty:
            return Result.fail("timeout")


class AudioPlayback:
    """Manages sounddevice output stream for TTS playback.

    Opens a new output stream for each play() call (blocking).
    Handles format conversion and resampling if needed.

    Attributes:
        device: ALSA device name/index or None for default.
        sample_rate: Output sample rate in Hz. Default 22050 (Piper default).
        channels: Number of channels. Default 1 (mono).
        dtype: NumPy dtype for output. Default "int16".
    """

    def __init__(
        self,
        device: Optional[str] = None,
        sample_rate: int = 22050,  # Piper default
        channels: int = 1,
        dtype: str = "int16",
    ):
        """Initialize audio playback.

        Args:
            device: ALSA device name/index or None for default.
            sample_rate: Output sample rate in Hz. Default 22050.
            channels: Number of channels. Default 1.
            dtype: NumPy dtype. Default "int16".
        """
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self._stream: Optional[sd.OutputStream] = None

    def play(self, audio_data: np.ndarray) -> Result:
        """Play audio data (blocking).

        Converts float32 to int16 if needed. Resamples if output sample_rate
        differs from Piper's 22050Hz using linear interpolation.

        Args:
            audio_data: Audio samples as numpy array (int16 or float32 [-1, 1]).

        Returns:
            Result.ok(None) on success, Result.fail(error) on failure.
        """
        try:
            # Ensure correct format
            if audio_data.dtype != np.int16:
                audio_data = (audio_data * 32767).astype(np.int16)

            # Resample if needed (Piper outputs 22050, we may need
            # 16000 for some devices)
            if self.sample_rate != 22050:
                # Simple linear interpolation resample
                ratio = self.sample_rate / 22050
                new_length = int(len(audio_data) * ratio)
                indices = np.linspace(0, len(audio_data) - 1, new_length)
                audio_data = np.interp(indices, np.arange(len(audio_data)), audio_data).astype(
                    np.int16
                )

            with sd.OutputStream(
                device=self.device,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
            ) as stream:
                stream.write(audio_data)

            return Result.ok(None)
        except Exception as e:
            logger.error(f"Audio playback error: {e}")
            return Result.fail(str(e))
