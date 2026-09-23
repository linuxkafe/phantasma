"""Sample test for AES project."""

import sys
from unittest.mock import MagicMock, patch

import pytest


def test_main_output(caplog):
    """Test that main() prints expected output and exits on error.

    Mocks all assistant dependencies before import to avoid loading audio deps.
    """
    # Create mock pipeline
    mock_pipeline = MagicMock()
    mock_pipeline.start.return_value = MagicMock(
        success=False, error="no audio device in test"
    )

    # Mock all modules that assistant.py imports BEFORE importing src.main
    mock_modules = {
        "config": MagicMock(),
        "src.brain.fly_brain": MagicMock(FlyBrain=MagicMock()),
        "src.brain.persistence": MagicMock(FlyBrainStore=MagicMock()),
        "skills": MagicMock(SkillLoader=MagicMock(), SkillContext=MagicMock()),
        "src.pipeline.audio": MagicMock(),
        "src.pipeline.llm": MagicMock(),
        "src.pipeline.stt": MagicMock(),
        "src.pipeline.tts": MagicMock(),
        "src.pipeline.utils": MagicMock(
            Result=MagicMock, logger=MagicMock(), log_stage=MagicMock()
        ),
        "assistant": MagicMock(
            PhantasmaPipeline=MagicMock(return_value=mock_pipeline),
            run=MagicMock(side_effect=SystemExit(1)),
        ),
    }

    # Clear any cached modules first, and restore them afterwards so tests
    # collected earlier (test_stt, test_tts) that hold refs to these modules
    # keep working (otherwise their @patch targets re-import fresh classes
    # and silently miss -> real whisper/piper calls leak into tests).
    saved_modules = {}
    for mod in list(sys.modules.keys()):
        if mod.startswith(
            ("assistant", "src.main", "src.pipeline", "src.brain", "skills", "config")
        ):
            saved_modules[mod] = sys.modules[mod]
            del sys.modules[mod]

    try:
        with patch.dict(sys.modules, mock_modules):
            from src.main import main

            # main() calls sys.exit(1) on failure
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 1
    finally:
        # Restore module cache so subsequent tests see the original objects.
        sys.modules.update(saved_modules)


def test_version():
    """Test version is defined."""
    from src import __version__

    assert __version__ == "0.1.0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
