"""
Speech-to-Text using Whisper.
"""

from typing import Optional

import numpy as np
import whisper

from config import config
from src.pipeline.utils import Result, logger


class WhisperSTT:
    """Whisper transcription with model caching."""

    _model: Optional[whisper.Whisper] = None
    _model_size: Optional[str] = None

    @classmethod
    def load_model(cls, model_size: str) -> Result:
        """Load Whisper model (cached)."""
        if cls._model is not None and cls._model_size == model_size:
            return Result.ok(cls._model)

        try:
            logger.info(f"Loading Whisper model: {model_size}")
            cls._model = whisper.load_model(model_size, device="cpu")
            cls._model_size = model_size
            logger.info(f"Whisper model loaded: {model_size}")
            return Result.ok(cls._model)
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            return Result.fail(str(e))

    @classmethod
    def transcribe(cls, audio: np.ndarray, language: Optional[str] = None) -> Result:
        """
        Transcribe audio to text.
        Args:
            audio: int16 or float32 numpy array, 16kHz mono
            language: Language code (e.g., "pt", "en") or None for auto-detect
        Returns:
            Result with transcribed text in data field
        """
        import time

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

            duration_ms = (time.perf_counter() - start) * 1000
            lang = result.get("language", "unknown")
            logger.info(f"STT transcribed: '{text[:100]}...' (lang={lang})")
            return Result.ok(text, duration_ms=duration_ms)

        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(f"Transcription error: {e}")
            return Result.fail(str(e), duration_ms=duration_ms)


def transcribe(audio: np.ndarray, language: Optional[str] = None) -> Result:
    """Convenience function for direct transcription."""
    return WhisperSTT.transcribe(audio, language)
