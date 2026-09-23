"""
Speech-to-Text using Whisper.

Provides WhisperSTT class with model caching and a convenience transcribe() function.
Thread-safe model loading with class-level lock.
"""

import threading
import time
from typing import Optional

import numpy as np
import whisper

from config import config
from src.pipeline.utils import Result, logger


class WhisperSTT:
    """Whisper transcription with model caching.

    Loads Whisper model once and reuses for subsequent transcriptions.
    Thread-safe model loading using class-level lock.

    Class Attributes:
        _model: Cached Whisper model instance.
        _model_size: Size of currently loaded model.
        _lock: Threading lock for model loading.
    """

    _model: Optional[whisper.Whisper] = None
    _model_size: Optional[str] = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def load_model(cls, model_size: str) -> Result:
        """Load Whisper model (cached, thread-safe).

        Args:
            model_size: Whisper model size ("tiny", "base", "small", "medium", "large").

        Returns:
            Result.ok(model) on success, Result.fail(error) on failure.
        """
        # Fast path: already loaded
        if cls._model is not None and cls._model_size == model_size:
            return Result.ok(cls._model)

        # Slow path: need to load (with lock)
        with cls._lock:
            # Double-check after acquiring lock
            if cls._model is not None and cls._model_size == model_size:
                return Result.ok(cls._model)

            try:
                logger.info(f"Loading Whisper model: {model_size}")
                start = time.perf_counter()
                cls._model = whisper.load_model(model_size, device="cpu")
                cls._model_size = model_size
                load_time = (time.perf_counter() - start) * 1000
                logger.info(
                    f"Whisper model loaded: {model_size} (load_time_ms={load_time:.1f})"
                )
                return Result.ok(cls._model)
            except Exception as e:
                logger.error(f"Failed to load Whisper model: {e}")
                return Result.fail(str(e))

    @classmethod
    def transcribe(cls, audio: np.ndarray, language: Optional[str] = None) -> Result:
        """Transcribe audio to text.

        Args:
            audio: Audio samples as numpy array (int16 or float32), 16kHz mono.
                int16 will be normalized to float32 [-1, 1].
            language: Language code (e.g., "pt", "en") or None for auto-detect.
                If None, uses config.stt.language.

        Returns:
            Result.ok(text) with transcribed text, or Result.fail(error).
            Result.duration_ms contains total processing time.
        """
        start = time.perf_counter()

        load_result = cls.load_model(config.stt.model_size)
        if not load_result.success:
            return load_result

        model = load_result.data

        try:
            # Convert to float32 [-1, 1] if needed
            if audio.dtype == np.int16:
                audio_float = audio.astype(np.float32) / 32768.0
            else:
                audio_float = audio.astype(np.float32)

            # Whisper expects 16kHz mono
            options = {
                "fp16": config.stt.fp16,
                "language": language or config.stt.language,
                "task": "transcribe",
            }

            result = model.transcribe(audio_float, **options)
            text = result.get("text", "").strip()

            # Apply domain-specific phonetic fixes (mis-transcriptions of
            # common phrases, e.g. "não é que está ótimo" -> "como está o
            # tempo"). Ordered so a match can chain into the next fix.
            fixes = getattr(config, "phonetic_fixes", {})
            if fixes and text:
                lower_text = text.lower()
                for wrong, right in fixes.items():
                    if wrong in lower_text:
                        lower_text = lower_text.replace(wrong, right)
                if lower_text != text.lower():
                    logger.info(
                        f"STT phonetic fix applied: '{text}' -> '{lower_text}'"
                    )
                    text = lower_text

            duration_ms = (time.perf_counter() - start) * 1000
            lang = result.get("language", "unknown")
            logger.info(f"STT transcribed: '{text[:100]}...' (lang={lang})")
            return Result.ok(text, duration_ms=duration_ms)

        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(f"Transcription error: {e}")
            return Result.fail(str(e), duration_ms=duration_ms)


def transcribe(audio: np.ndarray, language: Optional[str] = None) -> Result:
    """Convenience function for direct transcription.

    Creates WhisperSTT instance and calls transcribe(). Use for one-off
    transcriptions. For repeated use, call WhisperSTT.transcribe() directly
    to benefit from model caching.

    Args:
        audio: Audio samples as numpy array (int16 or float32), 16kHz mono.
        language: Language code or None for auto-detect.

    Returns:
        Result with transcribed text in data field.
    """
    return WhisperSTT.transcribe(audio, language)
