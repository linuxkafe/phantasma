"""Tests for STT (Whisper) component.

The engine is faster-whisper now (CTranslate2, int8) rather than
openai-whisper; see src/pipeline/stt.py for the measurements. These tests
mocked `whisper.load_model` and the `_model`/`_model_size` cache attributes,
which no longer exist, so they were rewritten against the current surface
rather than left failing against a removed API.

The mocking style matters here: faster-whisper's `transcribe()` returns a
LAZY generator, so a mock that returns a dict is not a faithful stand-in and
would let a real bug (consuming the generator outside the timing block) pass
unnoticed. `_engine()` below returns the (segments, info) pair the real thing
returns, and a real generator.

tests/test_stt_engine.py covers the engine-choice contract separately.
"""

import types
from unittest.mock import MagicMock, patch

import numpy as np

from src.pipeline.stt import WhisperSTT, transcribe
from src.pipeline.utils import Result


def _engine(text="hello world", language="en"):
    """A stand-in for faster_whisper.WhisperModel with the real return shape.

    The segments are a generator because the real API's are: if a future change
    stopped consuming it, a dict mock would keep these tests green while the
    transcription came back empty in production.
    """

    def _segments():
        # Real faster-whisper segments already carry a trailing space. The
        # implementation concatenates rather than joining with " ", precisely
        # so a gap between segments does not become a double space.
        words = text.split()
        for i, word in enumerate(words):
            suffix = " " if i < len(words) - 1 else ""
            yield types.SimpleNamespace(text=word + suffix)

    engine = MagicMock()

    def _transcribe(_audio, **kwargs):
        return (
            _segments(),
            types.SimpleNamespace(language=kwargs.get("language") or language),
        )

    engine.transcribe.side_effect = _transcribe
    return engine


def _patched(text="hello world", language="en"):
    """Patch load_model so no real model is downloaded, return the mock."""
    engine = _engine(text, language)
    return patch.object(WhisperSTT, "load_model", return_value=Result.ok(engine)), engine


class TestWhisperSTT:
    """Test WhisperSTT transcription with model caching."""

    def setup_method(self):
        WhisperSTT._engine = None
        WhisperSTT._engine_key = None

    def teardown_method(self):
        WhisperSTT._engine = None
        WhisperSTT._engine_key = None

    def test_load_model_caches(self):
        """load_model caches and reuses the model."""
        engine = _engine()
        with patch("src.pipeline.stt._make_engine", return_value=engine) as mock_make:
            result1 = WhisperSTT.load_model("base")
            result2 = WhisperSTT.load_model("base")

        assert result1.success
        assert result1.data is engine
        assert result2.data is engine
        # Building a model costs minutes, so it must happen once.
        assert mock_make.call_count == 1

    def test_load_model_reloads_on_size_change(self):
        """load_model reloads when model_size changes."""
        engine1, engine2 = _engine("one"), _engine("two")
        with patch("src.pipeline.stt._make_engine", side_effect=[engine1, engine2]) as m:
            result1 = WhisperSTT.load_model("base")
            result2 = WhisperSTT.load_model("small")

        assert result1.data is engine1
        assert result2.data is engine2
        assert m.call_count == 2

    def test_load_model_handles_exception(self):
        """load_model returns Result.fail on exception."""
        with patch("src.pipeline.stt._make_engine", side_effect=Exception("download failed")):
            result = WhisperSTT.load_model("base")

        assert result.success is False
        assert "download failed" in result.error

    def test_transcribe_converts_int16_to_float32(self):
        """transcribe normalizes int16 to float32 [-1, 1]."""
        patcher, engine = _patched("hello world")
        with patcher:
            audio = np.array([0, 16384, -16384, 32767, -32768], dtype=np.int16)
            result = WhisperSTT.transcribe(audio)

        assert result.success is True
        assert result.data == "hello world"
        passed = engine.transcribe.call_args[0][0]
        assert passed.dtype == np.float32
        assert np.max(passed) <= 1.0
        assert np.min(passed) >= -1.0

    def test_transcribe_passes_float32_through(self):
        """transcribe passes float32 through unchanged."""
        patcher, engine = _patched("test")
        with patcher:
            result = WhisperSTT.transcribe(np.array([0.0, 0.5, -0.5], dtype=np.float32))

        assert result.success is True
        assert engine.transcribe.call_args[0][0].dtype == np.float32

    def test_transcribe_uses_config_language(self):
        """transcribe uses config.stt.language when None provided."""
        patcher, engine = _patched()
        with patcher, patch("src.pipeline.stt.config") as mock_config:
            mock_config.stt.model_size = "base"
            mock_config.stt.language = "pt"
            WhisperSTT.transcribe(np.zeros(100, dtype=np.float32), language=None)

        assert engine.transcribe.call_args.kwargs["language"] == "pt"

    def test_transcribe_uses_override_language(self):
        """transcribe uses explicit language override."""
        patcher, engine = _patched()
        with patcher, patch("src.pipeline.stt.config") as mock_config:
            mock_config.stt.model_size = "base"
            mock_config.stt.language = "pt"
            WhisperSTT.transcribe(np.zeros(100, dtype=np.float32), language="en")

        assert engine.transcribe.call_args.kwargs["language"] == "en"

    def test_transcribe_returns_empty_text(self):
        """transcribe handles an empty transcription."""
        patcher, _ = _patched("   ")
        with patcher:
            result = WhisperSTT.transcribe(np.zeros(100, dtype=np.float32))

        assert result.success is True
        assert result.data == ""

    def test_transcribe_handles_exception(self):
        """transcribe returns Result.fail on exception."""
        engine = MagicMock()
        engine.transcribe.side_effect = Exception("CUDA OOM")
        with patch.object(WhisperSTT, "load_model", return_value=Result.ok(engine)):
            result = WhisperSTT.transcribe(np.zeros(100, dtype=np.float32))

        assert result.success is False
        assert "CUDA OOM" in result.error

    def test_transcribe_includes_duration(self):
        """transcribe includes duration_ms in result."""
        patcher, _ = _patched("test")
        with patcher:
            result = WhisperSTT.transcribe(np.zeros(100, dtype=np.float32))

        assert result.duration_ms > 0


class TestTranscribeConvenienceFunction:
    """Test transcribe() convenience function."""

    def test_transcribe_delegates_to_class_method(self):
        """transcribe delegates to WhisperSTT.transcribe."""
        with patch.object(
            WhisperSTT, "transcribe", return_value=Result.ok("hello", duration_ms=100.0)
        ) as mock:
            result = transcribe(np.zeros(100), language="pt")

        assert result.success is True
        assert result.data == "hello"
        mock.assert_called_once()


class TestWhisperSTTThreadSafety:
    """Test WhisperSTT thread-safe model loading."""

    def setup_method(self):
        WhisperSTT._engine = None
        WhisperSTT._engine_key = None

    def teardown_method(self):
        WhisperSTT._engine = None
        WhisperSTT._engine_key = None

    def test_concurrent_load_model(self):
        """Concurrent load_model calls don't create duplicate models."""
        import threading
        import time

        load_count = 0

        def slow_load(*_args, **_kwargs):
            nonlocal load_count
            load_count += 1
            time.sleep(0.01)  # simulate load time
            return _engine()

        results = [None] * 5
        with patch("src.pipeline.stt._make_engine", side_effect=slow_load):
            threads = []
            for i in range(5):
                t = threading.Thread(
                    target=lambda idx=i: results.__setitem__(
                        idx, WhisperSTT.load_model("base")
                    )
                )
                threads.append(t)
                t.start()
            for t in threads:
                t.join()

        assert all(r.success for r in results)
        # Five threads, one model: the lock is what makes that true.
        assert load_count == 1
