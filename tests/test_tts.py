"""Tests for TTS (Piper) component."""

from unittest.mock import MagicMock, patch

import numpy as np

from src.pipeline.tts import PiperTTS, synthesize
from src.pipeline.utils import Result


class TestPiperTTS:
    """Test PiperTTS synthesis with sox effects."""

    @patch("src.pipeline.tts.subprocess.run")
    @patch("os.path.exists")
    def test_check_dependencies_success(self, mock_exists, mock_run):
        """Test check_dependencies passes when piper, sox, model exist."""
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        tts = PiperTTS()
        result = tts.check_dependencies()

        assert result.success is True
        assert mock_run.call_count >= 2  # piper --help, sox --help

    @patch("src.pipeline.tts.subprocess.run")
    @patch("os.path.exists")
    def test_check_dependencies_missing_piper(self, mock_exists, mock_run):
        """Test check_dependencies fails when piper not found."""
        mock_run.side_effect = FileNotFoundError()

        tts = PiperTTS()
        result = tts.check_dependencies()

        assert result.success is False
        assert "piper not found" in result.error

    @patch("src.pipeline.tts.subprocess.run")
    @patch("os.path.exists")
    def test_check_dependencies_missing_sox_disables(self, mock_exists, mock_run):
        """Test check_dependencies disables sox when not found."""
        mock_exists.return_value = True

        def run_side_effect(cmd, *args, **kwargs):
            if cmd[0] == "piper":
                return MagicMock(returncode=0)
            elif cmd[0] == "sox":
                raise FileNotFoundError()
            return MagicMock(returncode=0)

        mock_run.side_effect = run_side_effect

        tts = PiperTTS()
        result = tts.check_dependencies()

        assert result.success is True
        assert tts._sox_bin is None  # Sox disabled

    @patch("os.path.exists")
    def test_check_dependencies_missing_model(self, mock_exists):
        """Test check_dependencies fails when voice model missing."""
        mock_exists.side_effect = lambda p: p != "/missing/model.onnx"

        tts = PiperTTS(voice_model_path="/missing/model.onnx")
        result = tts.check_dependencies()

        assert result.success is False
        assert "Voice model not found" in result.error

    @patch("src.pipeline.tts.PiperTTS.check_dependencies")
    @patch("src.pipeline.tts.subprocess.run")
    @patch("src.pipeline.tts.tempfile.NamedTemporaryFile")
    @patch("soundfile.read")
    @patch("src.pipeline.tts.os.path.exists")
    @patch("src.pipeline.tts.os.unlink")
    def test_synthesize_success(
        self, mock_unlink, mock_exists, mock_sf_read, mock_tmpfile, mock_run, mock_check
    ):
        """Test synthesize returns audio data on success."""
        mock_check.return_value = Result.ok(None)
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        mock_sf_read.return_value = (np.zeros(22050, dtype=np.int16), 22050)
        mock_exists.return_value = True

        # Mock tempfile context manager
        mock_file = MagicMock()
        mock_file.name = "/tmp/output.wav"
        mock_tmpfile.return_value.__enter__.return_value = mock_file
        mock_tmpfile.return_value.__exit__.return_value = None

        tts = PiperTTS()
        result = tts.synthesize("Hello world")

        assert result.success is True
        assert isinstance(result.data, tuple)
        audio_data, sample_rate = result.data
        assert audio_data.dtype == np.int16
        assert sample_rate == 22050
        assert result.duration_ms > 0

    @patch("src.pipeline.tts.PiperTTS.check_dependencies")
    @patch("src.pipeline.tts.subprocess.run")
    @patch("src.pipeline.tts.tempfile.NamedTemporaryFile")
    @patch("src.pipeline.tts.os.path.exists")
    @patch("src.pipeline.tts.os.unlink")
    def test_synthesize_piper_failure(
        self, mock_unlink, mock_exists, mock_tmpfile, mock_run, mock_check
    ):
        """Test synthesize fails when piper returns non-zero."""
        mock_check.return_value = Result.ok(None)
        mock_run.return_value = MagicMock(returncode=1, stderr=b"Piper error")

        mock_file = MagicMock()
        mock_file.name = "/tmp/output.wav"
        mock_tmpfile.return_value.__enter__.return_value = mock_file

        tts = PiperTTS()
        result = tts.synthesize("Hello")

        assert result.success is False
        assert "Piper failed" in result.error

    @patch("src.pipeline.tts.PiperTTS.check_dependencies")
    @patch("src.pipeline.tts.subprocess.run")
    @patch("src.pipeline.tts.tempfile.NamedTemporaryFile")
    @patch("soundfile.read")
    @patch("src.pipeline.tts.os.path.exists")
    @patch("src.pipeline.tts.os.unlink")
    def test_synthesize_applies_sox_effects(
        self, mock_unlink, mock_exists, mock_sf_read, mock_tmpfile, mock_run, mock_check
    ):
        """Test synthesize applies sox effects when enabled."""
        mock_check.return_value = Result.ok(None)
        mock_sf_read.return_value = (np.zeros(22050, dtype=np.int16), 22050)
        mock_exists.return_value = True

        # Mock run: first call piper (success), second call sox (success)
        piper_result = MagicMock(returncode=0, stderr=b"")
        sox_result = MagicMock(returncode=0, stderr=b"")
        mock_run.side_effect = [piper_result, sox_result]

        mock_file = MagicMock()
        mock_file.name = "/tmp/output.wav"
        mock_tmpfile.return_value.__enter__.return_value = mock_file

        tts = PiperTTS()
        tts._sox_bin = "sox"  # Enable sox
        result = tts.synthesize("Hello")

        assert result.success is True
        # Should call piper then sox
        assert mock_run.call_count == 2

    @patch("src.pipeline.tts.PiperTTS.check_dependencies")
    @patch("src.pipeline.tts.subprocess.run")
    @patch("src.pipeline.tts.tempfile.NamedTemporaryFile")
    @patch("soundfile.read")
    @patch("src.pipeline.tts.os.path.exists")
    @patch("src.pipeline.tts.os.unlink")
    def test_synthesize_sox_failure_fallback(
        self, mock_unlink, mock_exists, mock_sf_read, mock_tmpfile, mock_run, mock_check
    ):
        """Test synthesize falls back to raw output when sox fails."""
        mock_check.return_value = Result.ok(None)
        mock_sf_read.return_value = (np.zeros(22050, dtype=np.int16), 22050)
        mock_exists.return_value = True

        piper_result = MagicMock(returncode=0, stderr=b"")
        sox_result = MagicMock(returncode=1, stderr=b"Sox error")
        mock_run.side_effect = [piper_result, sox_result]

        mock_file = MagicMock()
        mock_file.name = "/tmp/output.wav"
        mock_tmpfile.return_value.__enter__.return_value = mock_file

        tts = PiperTTS()
        tts._sox_bin = "sox"
        result = tts.synthesize("Hello")

        assert result.success is True  # Should succeed with raw output

    @patch("src.pipeline.tts.PiperTTS.check_dependencies")
    @patch("src.pipeline.tts.subprocess.run")
    @patch("src.pipeline.tts.tempfile.NamedTemporaryFile")
    def test_synthesize_timeout(self, mock_tmpfile, mock_run, mock_check):
        """Test synthesize handles timeout."""
        mock_check.return_value = Result.ok(None)
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("piper", 30)

        mock_file = MagicMock()
        mock_file.name = "/tmp/output.wav"
        mock_tmpfile.return_value.__enter__.return_value = mock_file

        tts = PiperTTS()
        result = tts.synthesize("Hello")

        assert result.success is False
        assert "timeout" in result.error.lower()

    @patch("src.pipeline.tts.PiperTTS.check_dependencies")
    @patch("src.pipeline.tts.subprocess.run")
    @patch("src.pipeline.tts.tempfile.NamedTemporaryFile")
    @patch("src.pipeline.tts.os.path.exists")
    @patch("src.pipeline.tts.os.unlink")
    def test_synthesize_cleans_temp_files(
        self, mock_unlink, mock_exists, mock_tmpfile, mock_run, mock_check
    ):
        """Test synthesize cleans up temp files on success and failure."""
        mock_check.return_value = Result.ok(None)
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        mock_exists.return_value = True

        mock_file = MagicMock()
        mock_file.name = "/tmp/output.wav"
        mock_tmpfile.return_value.__enter__.return_value = mock_file

        tts = PiperTTS()
        tts.synthesize("Hello")

        # Should unlink output file
        mock_unlink.assert_called_with("/tmp/output.wav")

    @patch("src.pipeline.tts.PiperTTS.check_dependencies")
    def test_synthesize_includes_duration(self, mock_check):
        """Test synthesize includes duration_ms in result."""
        mock_check.return_value = Result.ok(None)

        with (
            patch("src.pipeline.tts.subprocess.run") as mock_run,
            patch("src.pipeline.tts.tempfile.NamedTemporaryFile") as mock_tmpfile,
            patch("soundfile.read") as mock_sf_read,
            patch("src.pipeline.tts.os.path.exists") as mock_exists,
            patch("src.pipeline.tts.os.unlink"),
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr=b"")
            mock_sf_read.return_value = (np.zeros(22050, dtype=np.int16), 22050)
            mock_exists.return_value = True

            mock_file = MagicMock()
            mock_file.name = "/tmp/output.wav"
            mock_tmpfile.return_value.__enter__.return_value = mock_file

            tts = PiperTTS()
            result = tts.synthesize("Hello")

            assert result.duration_ms > 0


class TestSynthesizeConvenienceFunction:
    """Test synthesize() convenience function."""

    @patch("src.pipeline.tts.PiperTTS")
    def test_synthesize_delegates_to_class(self, mock_tts_class):
        """Test convenience function creates PiperTTS and calls synthesize."""
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = Result.ok((np.zeros(100), 22050), duration_ms=200.0)
        mock_tts_class.return_value = mock_tts

        result = synthesize("Hello")

        assert result.success is True
        assert result.data[1] == 22050
        mock_tts.synthesize.assert_called_once_with("Hello")
