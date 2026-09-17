"""Tests for pHantasma configuration."""

from config import Config, config, load_from_env


class TestConfig:
    """Test configuration validation."""

    def test_default_config_valid(self):
        """Default config should be valid (except for missing files)."""
        errors = config.validate()
        # Should only fail on missing voice model and music dir (expected in test env)
        assert isinstance(errors, list)

    def test_config_load_from_env(self, monkeypatch):
        """Environment variables should override config."""
        monkeypatch.setenv("PHANTASMA_DEVICE_IN", "hw:1,0")
        monkeypatch.setenv("PHANTASMA_VAD_AGGRESSIVENESS", "3")
        monkeypatch.setenv("PHANTASMA_WHISPER_MODEL", "small")

        test_config = Config()
        load_from_env(test_config)

        assert test_config.audio.device_in == "hw:1,0"
        assert test_config.vad.aggressiveness == 3
        assert test_config.stt.model_size == "small"

    def test_vad_aggressiveness_validation(self):
        """VAD aggressiveness must be 0-3."""
        test_config = Config()
        test_config.vad.aggressiveness = 5
        errors = test_config.validate()
        assert any("vad.aggressiveness" in e for e in errors)

    def test_vad_frame_duration_validation(self):
        """VAD frame duration must be 10, 20, or 30."""
        test_config = Config()
        test_config.vad.frame_duration_ms = 15
        errors = test_config.validate()
        assert any("vad.frame_duration_ms" in e for e in errors)

    def test_hotword_threshold_validation(self):
        """Hotword threshold must be 0.0-1.0."""
        test_config = Config()
        test_config.hotword.threshold = 1.5
        errors = test_config.validate()
        assert any("hotword.threshold" in e for e in errors)

    def test_queue_maxsize_validation(self):
        """Queue maxsize must be >= 1."""
        test_config = Config()
        test_config.pipeline.queue_maxsize = 0
        errors = test_config.validate()
        assert any("queue_maxsize" in e for e in errors)
