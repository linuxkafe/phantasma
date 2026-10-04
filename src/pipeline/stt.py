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

import re
import threading
import time
from typing import Any, Optional

import numpy as np

from config import config
from src.pipeline.utils import Result, logger
from text_norm import DEVICE_VARIANTS, fold

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
            # The device vocabulary, handed to the DECODER.
            #
            # `WHISPER_INITIAL_PROMPT` has been set in prod's .env since before
            # this code existed, and it was never passed here: measured on
            # 2026-10-04, the owner said "liga o exaustor", Whisper returned
            # "Liga o exaustório", and `skill_tuya` looked for "exaustor" in it
            # and found nothing. The prompt already said "Liga o exaustor."
            # three lines above the failure. A setting that is written down and
            # not read is indistinguishable from not having it.
            #
            # This is a bias, not a guarantee, which is why the deterministic
            # alias pass below follows it.
            decode_kwargs = {}
            initial_prompt = getattr(config, "whisper_initial_prompt", "") or ""
            if initial_prompt.strip():
                decode_kwargs["initial_prompt"] = initial_prompt

            segments, info = engine.transcribe(
                audio_float,
                language=lang,
                task="transcribe",
                beam_size=5,
                # Never split on silence: the utterance starts with the wake
                # word, so VAD would keep the wake word and drop the request.
                vad_filter=False,
                **decode_kwargs,
            )
            # Split, not join: real segments already carry a trailing space,
            # and joining with " " as well produces a double space in every
            # gap. `" ".join(s.text for s in ...)` is the trap.
            text = "".join(seg.text for seg in segments).strip()

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

            # The guarantee, where the prompt is only the bias.
            #
            # `initial_prompt` nudges the decoder towards the words it was given
            # and does not guarantee any of them. So every alias the owner can be
            # misheard as is rewritten to the name the device registry actually
            # holds -- and only aliases of names this house HAS. Rewriting a word
            # the house owns no device for would be inventing vocabulary.
            canonical = _canonical_device_names(text)
            if canonical:
                logger.info("STT device alias applied: %s", canonical)
                text = canonical

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


def _canonical_device_names(text: str) -> Optional[str]:
    """Rewrite misheard device names to the ones the registry holds.

    Word by word, so the accents everywhere else in the sentence survive: only
    the token that names a device is replaced. Folding the whole line would
    "fix" the extractor and mangle the rest of what the owner said.

    Scoped to devices this house actually owns. Rewriting a name nobody has would
    be inventing vocabulary, and inventing vocabulary is how a house ends up
    confidently agreeing about a device that does not exist.

    Returns None when nothing changed, so the caller can tell "no alias" from
    "rewrote it to itself".
    """
    if not text:
        return None
    devices = getattr(config, "TUYA_DEVICES", None) or {}
    if not devices:
        return None

    # folded variant -> the canonical NOUN, not the registered nickname.
    #
    # Rewriting "o exaustor" into "o Exaustor do WC" would duplicate whatever
    # room the owner already said -- "liga o exaustor do wc" becomes "liga o
    # Exaustor do WC do wc" -- and it would silently pin the command to one
    # device when the owner named a type. Which device answers is the skill's
    # job, from its own nickname and fallback logic; this pass only makes the
    # WORD legible.
    mapping = {}
    for device, variants in DEVICE_VARIANTS.items():
        if not any(fold(device) in fold(d) for d in devices):
            continue  # this house owns no such device
        for variant in variants:
            mapping.setdefault(fold(variant), device)

    if not mapping:
        return None

    def _replace(match):
        return mapping.get(fold(match.group(0)), match.group(0))

    out = re.sub(r"[^\W\d_]+", _replace, text, flags=re.UNICODE)
    return out if out != text else None


def decode_bytes(data: bytes) -> np.ndarray:
    """Decode ANY recorded audio into 16 kHz mono float32.

    Added 2026-09-29 because the browser could not be made to do it reliably.

    The page used to decode its own MediaRecorder output with
    `AudioContext.decodeAudioData`, resample to 16 kHz by hand and write a WAV
    container. On a real Android phone that path failed and the user got "falha
    ao enviar o áudio" -- after the recording, the transcription and everything
    else had already worked. Three moving parts in the browser to replace one
    line here.

    `faster_whisper.audio.decode_audio` is PyAV, and PyAV is already an indirect
    dependency of faster-whisper itself, so this is free: it opens the container
    (webm, opus, ogg, mp4/aac, wav), resamples to 16 kHz and downmixes to mono,
    exactly as the recogniser wants. The hand-rolled resampler and the
    hand-written WAV header were recreating what this returns.

    Measured on this box (PyAV 18.1.0, faster-whisper 1.2.1) against synthetic
    live-streamed WebM of the shape MediaRecorder actually produces: it decodes
    down to 20ms clips.

    Two things it does NOT forgive, and both are cheap to catch here: a
    header-only container (a recording that never received data) and a lost
    first chunk. Both raise, and raising with a clear message beats a client
    that silently sends noise.

    Args:
        data: The raw bytes of a recorded clip, any format PyAV can open.

    Returns:
        float32 numpy array at 16 kHz, mono.

    Raises:
        Exception: whatever PyAV raises for a container it cannot read. The
            caller is expected to turn that into a message the user can act on.
    """
    import io

    from faster_whisper.audio import decode_audio

    return decode_audio(io.BytesIO(data), sampling_rate=16000)


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
