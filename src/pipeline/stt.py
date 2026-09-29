"""
Speech-to-Text using Whisper.

Engine: faster-whisper (CTranslate2) instead of openai-whisper. Measured on
this host (Intel i5-8500T, 6 threads, no usable GPU) with the same 1.3s
fixture:

    openai-whisper, medium, fp32, CPU   17.6 - 27.8s   (13-21x real time)
    faster-whisper, medium, int8, CPU    7.45s         (5.7x real time)

Same model, same output, 2.4-3.7x faster. openai-whisper runs in float32 on
the CPU (float16 is unsupported there, so it silently downgrades) and pays
Python-level overhead per layer; CTranslate2 with int8 quantisation is the
same weights at a fraction of the arithmetic. Dropping the engine also drops
torch from the import path.

The model name is unchanged (`config.stt.model_size`), so this is a drop-in:
same config, same `Result` shape, same language handling, same phonetic-fix
post-processing.

VAD is deliberately off. A wake word is the opening of a longer utterance, and
silence-splitting would keep the wake word and drop the request.
"""

import threading
import time
from typing import Any, Optional

import numpy as np

from config import config
from src.pipeline.utils import Result, logger

# int8 is the point of the swap. float32 on this host was 2.4-3.7x slower.
_DEFAULT_COMPUTE_TYPE = "int8"


def _make_engine(model_size: str, device: str, compute_type: str, **kwargs) -> Any:
    """Build the recognition model. Separated so tests can inject a fake.

    The import is local on purpose: it is slow, and a module-level import
    would make every test that merely imports `stt` pay for it.
    """
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device=device, compute_type=compute_type, **kwargs)


class WhisperSTT:
    """Whisper transcription with model caching.

    Loads the model once and reuses it for subsequent transcriptions, because
    loading `medium` costs minutes. Thread-safe via a class-level lock.

    Class Attributes:
        _engine: Cached model instance.
        _engine_key: (size, device, compute_type) the cache was built for.
        _lock: Threading lock for model loading.
    """

    _engine: Optional[Any] = None
    _engine_key: Optional[tuple] = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def load_model(cls, model_size: str) -> Result:
        """Load the model (cached, thread-safe).

        Args:
            model_size: Whisper size ("tiny", "base", "small", "medium",
                "large", "large-v2", "large-v3", "large-v3-turbo", or a
                distil-* / turbo name).

        Returns:
            Result.ok(engine) on success, Result.fail(error) on failure.
        """
        device = "cpu"
        compute_type = _DEFAULT_COMPUTE_TYPE
        key = (model_size, device, compute_type)

        # Fast path: already loaded for this exact configuration.
        if cls._engine is not None and cls._engine_key == key:
            return Result.ok(cls._engine)

        with cls._lock:
            # Double-check after acquiring the lock.
            if cls._engine is not None and cls._engine_key == key:
                return Result.ok(cls._engine)

            try:
                logger.info(f"Loading Whisper model: {model_size} ({compute_type}, {device})")
                start = time.perf_counter()
                engine = _make_engine(model_size, device, compute_type)
                cls._engine = engine
                cls._engine_key = key
                load_time = (time.perf_counter() - start) * 1000
                logger.info(f"Whisper model loaded: {model_size} ({load_time:.0f}ms)")
                return Result.ok(engine)
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

        engine = load_result.data
        lang = language or config.stt.language

        try:
            # Convert to float32 [-1, 1] if needed
            if audio.dtype == np.int16:
                audio_float = audio.astype(np.float32) / 32768.0
            else:
                audio_float = audio.astype(np.float32)

            # faster-whisper returns a lazy generator: the inference happens
            # when it is consumed, so it must be consumed INSIDE the timing
            # below. Timing the call alone measures nothing (see SD-OPS-204).
            segments, info = engine.transcribe(
                audio_float,
                language=lang,
                task="transcribe",
                beam_size=5,
                # Never split on silence: the utterance starts with the wake
                # word, so VAD would keep the wake word and drop the request.
                vad_filter=False,
            )
            text = " ".join(seg.text for seg in segments).strip()

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
                    logger.info(f"STT phonetic fix applied: '{text}' -> '{lower_text}'")
                    text = lower_text

            duration_ms = (time.perf_counter() - start) * 1000
            detected = getattr(info, "language", None) or lang or "unknown"
            logger.info(
                f"STT transcribed: '{text[:100]}...' "
                f"(lang={detected}, {duration_ms:.0f}ms)"
            )
            return Result.ok(text, duration_ms=duration_ms)

        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(f"Transcription error: {e}")
            return Result.fail(str(e), duration_ms=duration_ms)


def transcribe(audio: np.ndarray, language: Optional[str] = None) -> Result:
    """Convenience function for direct transcription.

    Creates a WhisperSTT instance and calls transcribe(). Use for one-off
    transcriptions. For repeated use, call WhisperSTT.transcribe() directly
    to benefit from model caching.

    Args:
        audio: Audio samples as numpy array (int16 or float32), 16kHz mono.
        language: Language code or None for auto-detect.

    Returns:
        Result with transcribed text in data field.
    """
    return WhisperSTT.transcribe(audio, language)
