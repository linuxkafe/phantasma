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
import time
from dataclasses import dataclass
from typing import Optional, Union

import numpy as np
import openwakeword
import openwakeword.model
import sounddevice as sd
import webrtcvad

from config import config
from src.pipeline.utils import Result, logger


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
                frame = np.pad(
                    frame, (0, self.frame_size - len(frame)), mode="constant"
                )
            else:
                frame = frame[: self.frame_size]
        # Convert to bytes (int16 little-endian)
        frame_bytes = frame.astype(np.int16).tobytes()
        return self.vad.is_speech(frame_bytes, self.sample_rate)

    def process_chunk(self, chunk: np.ndarray) -> list[AudioFrame]:
        """Split chunk into frames and run VAD on each.

        Args:
            chunk: Audio chunk as numpy array (int16 or float32).

        Returns:
            List of AudioFrame objects with VAD results.
            Incomplete final frame is discarded.
        """
        frames = []
        num_frames = len(chunk) // self.frame_size
        for i in range(num_frames):
            frame_data = chunk[i * self.frame_size : (i + 1) * self.frame_size]
            is_speech = self.is_speech(frame_data)
            frames.append(
                AudioFrame(data=frame_data, timestamp=time.time(), is_speech=is_speech)
            )
        return frames


class HotwordDetector:
    """openWakeWord hotword detection.

    Loads pretrained openWakeWord models and runs inference on audio chunks.
    Implements cooldown period to prevent repeated detections.

    Attributes:
        models: List of model names to detect (e.g., ["hey_jarvis", "alexa"]).
        threshold: Detection confidence threshold 0.0-1.0.
        sample_rate: Audio sample rate in Hz. Default 16000.
        chunk_size: Expected audio chunk size in samples. Default 1600 (100ms at 16kHz).
        oww_model: Underlying openwakeword.model.Model instance.
        _cooldown: Cooldown period in seconds (from config).
        _last_detection: Timestamp of last detection.
    """

    def __init__(
        self,
        models: list[str],
        threshold: float = 0.5,
        sample_rate: int = 16000,
        chunk_size: int = 1600,
    ):
        """Initialize hotword detector.

        Args:
            models: List of model names to load. Must match pretrained model names
                from openwakeword.get_pretrained_model_paths().
            threshold: Detection threshold 0.0-1.0. Default 0.5.
            sample_rate: Audio sample rate in Hz. Default 16000.
            chunk_size: Audio chunk size in samples. Default 1600.

        Raises:
            RuntimeError: If openWakeWord model fails to load.
        """
        self.models = models
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size

        # Custom model paths resolve to existing files -> load them directly.
        # This is how the PT wake words (models/ola_fantasma.onnx etc.) are used;
        # the old code only filtered pretrained model NAMES, so custom .onnx paths
        # silently fell back to all English pretrained models ("Hey Jarvis").
        custom_paths = [p for p in models if os.path.isfile(p)]
        if custom_paths:
            logger.info(
                f"Loading custom wake word models: {custom_paths}"
            )
            audio_features_kwargs = {"sr": sample_rate, "ncpu": 1}
            self.oww_model = openwakeword.model.Model(
                wakeword_model_paths=custom_paths,
                **audio_features_kwargs,
            )
            return

        # Load openWakeWord model - use pre-trained models if paths not provided
        model_paths = openwakeword.get_pretrained_model_paths()
        # Filter to only the models we want
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

        # AudioFeatures kwargs (passed through Model **kwargs)
        audio_features_kwargs = {"sr": sample_rate, "ncpu": 1}

        self.oww_model = openwakeword.model.Model(
            wakeword_model_paths=filtered_paths if filtered_paths else model_paths,
            **audio_features_kwargs,
        )

        # Track last detection for cooldown
        self._last_detection = 0.0
        self._cooldown = config.hotword.cooldown_seconds

    def process(self, audio_chunk: np.ndarray) -> tuple[bool, Optional[str]]:
        """Process audio chunk for hotword detection.

        Args:
            audio_chunk: Audio data as numpy array (int16 or float32).
                Must be chunk_size samples at sample_rate.

        Returns:
            Tuple of (detected: bool, model_name: Optional[str]).
            model_name is the name of the model that triggered (e.g., "hey_jarvis").
        """
        now = time.time()

        # Cooldown check
        if now - self._last_detection < self._cooldown:
            return False, None

        # openWakeWord expects float32 [-1, 1]
        if audio_chunk.dtype == np.int16:
            audio_float = audio_chunk.astype(np.float32) / 32768.0
        else:
            audio_float = audio_chunk.astype(np.float32)

        # Run prediction
        predictions = self.oww_model.predict(audio_float)

        # Check each model
        for model_name, score in predictions.items():
            if score >= self.threshold:
                self._last_detection = now
                return True, model_name

        return False, None

    def reset(self):
        """Reset cooldown timer. Useful for testing or manual reset."""
        self._last_detection = 0.0


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
        queue_maxsize: int = 10,
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
                self.queue.put_nowait(indata.copy().flatten())
            except queue.Full:
                # Drop oldest frame to prevent blocking audio thread
                try:
                    self.queue.get_nowait()
                    self.queue.put_nowait(indata.copy().flatten())
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
            logger.info(
                f"Audio capture started: device={self.device}, sr={self.sample_rate}"
            )
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
                audio_data = np.interp(
                    indices, np.arange(len(audio_data)), audio_data
                ).astype(np.int16)

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
