"""
Audio I/O, VAD, and hotword detection.
"""

import os
import queue
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import openwakeword
import openwakeword.model
import sounddevice as sd
import webrtcvad

from config import config
from src.pipeline.utils import Result, logger


@dataclass
class AudioFrame:
    """Single audio frame with metadata."""

    data: np.ndarray  # int16, shape (samples,)
    timestamp: float
    is_speech: bool = False


class VADProcessor:
    """WebRTC VAD wrapper for frame-level speech detection."""

    def __init__(
        self,
        aggressiveness: int = 2,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
    ):
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(sample_rate * frame_duration_ms / 1000)
        self._bytes_per_frame = self.frame_size * 2  # int16 = 2 bytes

    def is_speech(self, frame: np.ndarray) -> bool:
        """Check if frame contains speech. Frame must be exactly frame_size samples."""
        if len(frame) != self.frame_size:
            # Pad or truncate
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
        """Split chunk into frames and run VAD on each."""
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
    """openWakeWord hotword detection."""

    def __init__(
        self,
        models: list[str],
        threshold: float = 0.5,
        sample_rate: int = 16000,
        chunk_size: int = 1600,
    ):
        self.models = models
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size

        # Load openWakeWord model - use pre-trained models if paths not provided
        model_paths = openwakeword.get_pretrained_model_paths()
        # Filter to only the models we want
        filtered_paths = []
        for path in model_paths:
            model_name = os.path.basename(path).replace(".onnx", "")
            if model_name in models:
                filtered_paths.append(path)

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
        """
        Process audio chunk for hotword detection.
        Returns (detected, model_name).
        """
        import time

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
        """Reset cooldown."""
        self._last_detection = 0.0


class AudioCapture:
    """Manages sounddevice input stream with callback feeding a queue."""

    def __init__(
        self,
        device: Optional[str] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        block_size: int = 1600,
        dtype: str = "int16",
        queue_maxsize: int = 10,
    ):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_size = block_size
        self.dtype = dtype
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=queue_maxsize)
        self._stream: Optional[sd.InputStream] = None
        self._running = False

    def _callback(self, indata, frames, time_info, status):
        """sounddevice callback - runs in audio thread."""
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
        """Start audio capture."""
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
            self._stream.start()
            self._running = True
            logger.info(
                f"Audio capture started: device={self.device}, sr={self.sample_rate}"
            )
            return Result.ok(None)
        except Exception as e:
            logger.error(f"Failed to start audio capture: {e}")
            return Result.fail(str(e))

    def stop(self) -> Result:
        """Stop audio capture."""
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
        """Get next audio frame from queue."""
        try:
            frame = self.queue.get(timeout=timeout)
            return Result.ok(frame)
        except queue.Empty:
            return Result.fail("timeout")


class AudioPlayback:
    """Manages sounddevice output stream for TTS playback."""

    def __init__(
        self,
        device: Optional[str] = None,
        sample_rate: int = 22050,  # Piper default
        channels: int = 1,
        dtype: str = "int16",
    ):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self._stream: Optional[sd.OutputStream] = None

    def play(self, audio_data: np.ndarray) -> Result:
        """Play audio data (blocking)."""
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
