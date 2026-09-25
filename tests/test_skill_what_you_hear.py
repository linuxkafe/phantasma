"""Tests for skill_what_you_hear (T036)."""

from unittest.mock import patch

import numpy as np

from src.pipeline.utils import Result


def _make_skill():
    """Import the skill module and return its handle function."""
    import sys

    # Ensure fresh import
    for mod in list(sys.modules.keys()):
        if mod.startswith("skills.skill_what_you_hear"):
            del sys.modules[mod]

    from skills import skill_what_you_hear

    return skill_what_you_hear.handle


@patch("skills.skill_what_you_hear.config")
@patch("skills.skill_what_you_hear.sd")
@patch("skills.skill_what_you_hear.stt_transcribe")
def test_what_you_hear_returns_transcription(mock_stt, mock_sd, mock_config):
    """Skill should capture audio, transcribe and return formatted text."""
    mock_config.audio.sample_rate = 16000
    mock_config.audio.device_in = 0

    # Mock sounddevice rec
    audio_data = np.zeros(48000, dtype=np.int16)
    mock_sd.rec.return_value = audio_data
    mock_sd.wait.return_value = None

    # STT returns a transcription
    mock_stt.return_value = Result.ok("olá mundo")

    handle = _make_skill()

    # Act
    result = handle("o que ouves", "o que ouves")

    # Assert
    assert result == "Ouço: olá mundo"
    mock_sd.rec.assert_called_once()
    mock_stt.assert_called_once()


@patch("skills.skill_what_you_hear.config")
@patch("skills.skill_what_you_hear.sd")
@patch("skills.skill_what_you_hear.stt_transcribe")
def test_what_you_hear_handles_stt_failure(mock_stt, mock_sd, mock_config):
    """If STT fails, skill returns error message."""
    mock_config.audio.sample_rate = 16000
    mock_config.audio.device_in = 0

    audio_data = np.zeros(48000, dtype=np.int16)
    mock_sd.rec.return_value = audio_data
    mock_sd.wait.return_value = None

    mock_stt.return_value = Result.fail("STT error")

    handle = _make_skill()
    result = handle("o que ouves", "o que ouves")

    assert "Não consegui transcrever" in result


@patch("skills.skill_what_you_hear.config")
@patch("skills.skill_what_you_hear.sd")
def test_what_you_hear_handles_capture_failure(mock_sd, mock_config):
    """If audio capture fails to start, skill returns error."""
    mock_config.audio.sample_rate = 16000
    mock_config.audio.device_in = 0

    mock_sd.rec.side_effect = Exception("device busy")

    handle = _make_skill()
    result = handle("o que ouves", "o que ouves")

    assert "Não consegui processar o áudio" in result
