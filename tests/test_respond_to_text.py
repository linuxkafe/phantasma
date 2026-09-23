"""Tests for shared text routing: respond_to_text / _respond_with_llm (T035)."""

from unittest.mock import MagicMock, patch


def _make_pipeline():
    """Build a PhantasmaPipeline with all heavy deps stubbed."""
    with patch("src.pipeline.utils.Result"):
        import os

        # Minimize import side effects from audio/STT/TTS deps.
        import config as config_module

        config_module.BRAIN_DB_PATH = "/tmp/test_brain.db"
        config_module.SKILLS_DIR = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"
        )
        config_module.FEEDBACK_WINDOW_SECONDS = 3

        from assistant import PhantasmaPipeline

        pipeline = PhantasmaPipeline.__new__(PhantasmaPipeline)
        pipeline._skill_loader = MagicMock()
        pipeline._fly_brain = MagicMock()
        pipeline.audio_playback = MagicMock()
        pipeline.audio_capture = MagicMock()
        pipeline.vad = MagicMock()
        pipeline.hotword = MagicMock()
        pipeline.audio_playback.play.return_value = MagicMock(success=True)
        pipeline._positive_keywords = []
        pipeline._negative_keywords = []
        pipeline._collecting_feedback = False
        pipeline._feedback_start_time = None
        pipeline._feedback_window_seconds = 3
        pipeline._max_silence_frames = 5
        pipeline._speech_frames = []
        pipeline._silence_frames = 0
        pipeline._running = False
        pipeline._worker_thread = None
        pipeline._hotword_detected = False
        pipeline._collecting_speech = False
        return pipeline


def test_respond_to_text_skill_handled():
    """respond_to_text returns the skill response when a skill matches."""
    pipeline = _make_pipeline()
    pipeline._skill_loader.execute_skill.return_value = "Hoje: sol no Porto."

    result = pipeline.respond_to_text("como está o tempo?")

    assert result == "Hoje: sol no Porto."
    pipeline._fly_brain.step.assert_not_called()


@patch("ollama.Client")
def test_respond_to_text_llm_fallback(mock_ollama_client):
    """respond_to_text falls back to LLM when no skill matches."""
    pipeline = _make_pipeline()
    pipeline._skill_loader.execute_skill.return_value = None
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": "Resposta do LLM"}}
    mock_ollama_client.return_value = mock_client

    result = pipeline.respond_to_text("explica-te melhor")

    assert result == "Resposta do LLM"
    pipeline._fly_brain.step.assert_called_once()


@patch("ollama.Client")
def test_respond_to_text_llm_failure_returns_none(mock_ollama_client):
    """respond_to_text returns None when no skill matches and LLM fails."""
    pipeline = _make_pipeline()
    pipeline._skill_loader.execute_skill.return_value = None
    mock_client = MagicMock()
    mock_client.chat.side_effect = Exception("Ollama down")
    mock_ollama_client.return_value = mock_client

    result = pipeline.respond_to_text("olá")

    assert result is None
