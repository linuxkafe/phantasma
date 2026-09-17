"""Sample test for AES project."""

from unittest.mock import MagicMock, patch

import pytest


def test_main_output(caplog):
    """Test that main() prints expected output and exits on error."""
    with patch("assistant.PhantasmaPipeline") as mock_pipeline_class:
        mock_pipeline = MagicMock()
        mock_pipeline.start.return_value = MagicMock(
            success=False, error="no audio device in test"
        )
        mock_pipeline_class.return_value = mock_pipeline

        from src.main import main

        # main() calls sys.exit(1) on failure
        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        # Check logs
        assert "Failed to start pipeline" in caplog.text
        assert "no audio device" in caplog.text


def test_version():
    """Test version is defined."""
    from src import __version__

    assert __version__ == "0.1.0"
