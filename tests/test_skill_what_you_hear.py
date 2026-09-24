"""Tests for skill_what_you_hear (T036)."""

from unittest.mock import MagicMock, patch

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
@patch("skills.skill_what_you_hear.AudioCapture")
@patch("skills.skill_what_you_hear.stt_transcribe")
def test_what_you_hear_returns_transcription(
    mock_stt, mock_audio_capture_class, mock_config
):
    """Skill should capture audio, transcribe and return formatted text."""
    # Arrange config values to make blocks_needed = 3
    mock_config.audio.sample_rate = 16000
    mock_config.audio.block_size = 16000
    mock_config.audio.channels = 1
    mock_config.audio.dtype = "int16"
    mock_config.audio.device_in = 0
    mock_config.pipeline.queue_maxsize = 10

    mock_capture = MagicMock()
    mock_audio_capture_class.return_value = mock_capture
    mock_capture.start.return_value = Result.ok(None)

    # Simulate 3 frames
    frame = np.zeros(16000, dtype=np.int16)
    mock_capture.get_frame.side_effect = [
        Result.ok(frame),
        Result.ok(frame),
        Result.ok(frame),
    ]
    mock_capture.stop.return_value = None

    # STT returns a transcription
    mock_stt.return_value = Result.ok("olá mundo")

    handle = _make_skill()

    # Act
    result = handle("o que ouves", "o que ouves")

    # Assert
    assert result == "Ouço: olá mundo"
    mock_capture.start.assert_called_once()
    assert mock_capture.get_frame.call_count >= 3
    mock_stt.assert_called_once()


@patch("skills.skill_what_you_hear.config")
@patch("skills.skill_what_you_hear.AudioCapture")
@patch("skills.skill_what_you_hear.stt_transcribe")
def test_what_you_hear_handles_stt_failure(
    mock_stt, mock_audio_capture_class, mock_config
):
    """If STT fails, skill returns error message."""
    mock_config.audio.sample_rate = 16000
    mock_config.audio.block_size = 16000
    mock_config.audio.channels = 1
    mock_config.audio.dtype = "int16"
    mock_config.audio.device_in = 0
    mock_config.pipeline.queue_maxsize = 10

    mock_capture = MagicMock()
    mock_audio_capture_class.return_value = mock_capture
    mock_capture.start.return_value = Result.ok(None)
    frame = np.zeros(16000, dtype=np.int16)
    mock_capture.get_frame.return_value = Result.ok(frame)
    mock_capture.stop.return_value = None

    mock_stt.return_value = Result.fail("STT error")

    handle = _make_skill()
    result = handle("o que ouves", "o que ouves")

    assert "Não consegui transcrever" in result


@patch("skills.skill_what_you_hear.config")
@patch("skills.skill_what_you_hear.AudioCapture")
def test_what_you_hear_handles_capture_failure(mock_audio_capture_class, mock_config):
    """If audio capture fails to start, skill returns error."""
    mock_config.audio.sample_rate = 16000
    mock_config.audio.block_size = 16000
    mock_config.audio.channels = 1
    mock_config.audio.dtype = "int16"
    mock_config.audio.device_in = 0
    mock_config.pipeline.queue_maxsize = 10

    mock_capture = MagicMock()
    mock_audio_capture_class.return_value = mock_capture
    mock_capture.start.return_value = Result.fail("device busy")

    handle = _make_skill()
    result = handle("o que ouves", "o que ouves")

    assert "Não consegui iniciar a captura" in result
