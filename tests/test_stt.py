"""Tests for STT (Whisper) component."""

from unittest.mock import MagicMock, patch

import numpy as np

from src.pipeline.stt import WhisperSTT, transcribe
from src.pipeline.utils import Result


class TestWhisperSTT:
    """Test WhisperSTT transcription with model caching."""

    @patch("src.pipeline.stt.whisper.load_model")
    def test_load_model_caches(self, mock_load_model):
        """Test load_model caches and reuses model."""
        mock_model = MagicMock()
        mock_load_model.return_value = mock_model

        # First call loads model
        result1 = WhisperSTT.load_model("base")
        assert result1.success is True
        assert result1.data is mock_model

        # Second call returns cached model
        result2 = WhisperSTT.load_model("base")
        assert result2.success is True
        assert result2.data is mock_model

        # whisper.load_model should only be called once
        assert mock_load_model.call_count == 1

    @patch("src.pipeline.stt.whisper.load_model")
    def test_load_model_reloads_on_size_change(self, mock_load_model):
        """Test load_model reloads when model_size changes."""
        # Reset class variables
        WhisperSTT._model = None
        WhisperSTT._model_size = None

        mock_model1 = MagicMock()
        mock_model2 = MagicMock()
        mock_load_model.side_effect = [mock_model1, mock_model2]

        result1 = WhisperSTT.load_model("base")
        result2 = WhisperSTT.load_model("small")

        assert result1.data is mock_model1
        assert result2.data is mock_model2
        assert mock_load_model.call_count == 2

    @patch("src.pipeline.stt.whisper.load_model")
    def test_load_model_handles_exception(self, mock_load_model):
        """Test load_model returns Result.fail on exception."""
        mock_load_model.side_effect = Exception("Model download failed")

        result = WhisperSTT.load_model("base")

        assert result.success is False
        assert "Model download failed" in result.error

    @patch.object(WhisperSTT, "load_model")
    def test_transcribe_converts_int16_to_float32(self, mock_load_model):
        """Test transcribe normalizes int16 to float32 [-1, 1]."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "hello world", "language": "en"}
        mock_load_model.return_value = Result.ok(mock_model)

        audio_int16 = np.array([0, 16384, -16384, 32767, -32768], dtype=np.int16)
        result = WhisperSTT.transcribe(audio_int16)

        assert result.success is True
        assert result.data == "hello world"
        # Verify model was called with float32
        call_args = mock_model.transcribe.call_args
        passed_audio = call_args[0][0]
        assert passed_audio.dtype == np.float32
        assert np.max(passed_audio) <= 1.0
        assert np.min(passed_audio) >= -1.0

    @patch.object(WhisperSTT, "load_model")
    def test_transcribe_passes_float32_through(self, mock_load_model):
        """Test transcribe passes float32 through unchanged."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "test", "language": "en"}
        mock_load_model.return_value = Result.ok(mock_model)

        audio_float = np.array([0.0, 0.5, -0.5], dtype=np.float32)
        result = WhisperSTT.transcribe(audio_float)

        assert result.success is True
        call_args = mock_model.transcribe.call_args
        passed_audio = call_args[0][0]
        assert passed_audio.dtype == np.float32

    @patch.object(WhisperSTT, "load_model")
    def test_transcribe_uses_config_language(self, mock_load_model):
        """Test transcribe uses config.stt.language when None provided."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "test", "language": "pt"}
        mock_load_model.return_value = Result.ok(mock_model)

        with patch("src.pipeline.stt.config") as mock_config:
            mock_config.stt.model_size = "base"
            mock_config.stt.language = "pt"
            mock_config.stt.fp16 = False

            _ = WhisperSTT.transcribe(np.zeros(100, dtype=np.float32), language=None)

            call_kwargs = mock_model.transcribe.call_args.kwargs
            assert call_kwargs["language"] == "pt"

    @patch.object(WhisperSTT, "load_model")
    def test_transcribe_uses_override_language(self, mock_load_model):
        """Test transcribe uses explicit language override."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "test", "language": "en"}
        mock_load_model.return_value = Result.ok(mock_model)

        with patch("src.pipeline.stt.config") as mock_config:
            mock_config.stt.model_size = "base"
            mock_config.stt.language = "pt"
            mock_config.stt.fp16 = False

            _ = WhisperSTT.transcribe(np.zeros(100, dtype=np.float32), language="en")

            call_kwargs = mock_model.transcribe.call_args.kwargs
            assert call_kwargs["language"] == "en"

    @patch.object(WhisperSTT, "load_model")
    def test_transcribe_returns_empty_text(self, mock_load_model):
        """Test transcribe handles empty text result."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "   ", "language": "en"}
        mock_load_model.return_value = Result.ok(mock_model)

        result = WhisperSTT.transcribe(np.zeros(100, dtype=np.float32))

        assert result.success is True
        assert result.data == ""

    @patch.object(WhisperSTT, "load_model")
    def test_transcribe_handles_exception(self, mock_load_model):
        """Test transcribe returns Result.fail on exception."""
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = Exception("CUDA OOM")
        mock_load_model.return_value = Result.ok(mock_model)

        result = WhisperSTT.transcribe(np.zeros(100, dtype=np.float32))

        assert result.success is False
        assert "CUDA OOM" in result.error

    @patch.object(WhisperSTT, "load_model")
    def test_transcribe_includes_duration(self, mock_load_model):
        """Test transcribe includes duration_ms in result."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "test", "language": "en"}
        mock_load_model.return_value = Result.ok(mock_model)

        result = WhisperSTT.transcribe(np.zeros(100, dtype=np.float32))

        assert result.duration_ms > 0


class TestTranscribeConvenienceFunction:
    """Test transcribe() convenience function."""

    @patch("src.pipeline.stt.WhisperSTT.transcribe")
    def test_transcribe_delegates_to_class_method(self, mock_transcribe):
        """Test convenience function delegates to WhisperSTT.transcribe."""
        mock_transcribe.return_value = Result.ok("hello", duration_ms=100.0)

        result = transcribe(np.zeros(100), language="pt")

        assert result.success is True
        assert result.data == "hello"
        mock_transcribe.assert_called_once()


class TestWhisperSTTThreadSafety:
    """Test WhisperSTT thread-safe model loading."""

    @patch("src.pipeline.stt.whisper.load_model")
    def test_concurrent_load_model(self, mock_load_model):
        """Test concurrent load_model calls don't create duplicate models."""
        import threading
        import time

        mock_model = MagicMock()
        load_count = 0

        def slow_load(*args, **kwargs):
            nonlocal load_count
            load_count += 1
            time.sleep(0.01)  # Simulate load time
            return mock_model

        mock_load_model.side_effect = slow_load

        def load_in_thread(results, idx):
            results[idx] = WhisperSTT.load_model("base")

        threads = []
        results = [None] * 5
        for i in range(5):
            t = threading.Thread(target=load_in_thread, args=(results, i))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All should succeed
        assert all(r.success for r in results)
        # Model should only be loaded once despite concurrent calls
        assert load_count == 1
