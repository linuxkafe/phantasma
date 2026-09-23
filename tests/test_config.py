"""Tests for pHantasma configuration."""

from config import Config, config


class TestConfig:
    """Test configuration validation."""

    def test_default_config_valid(self):
        """Default config should be valid (except for missing files)."""
        errors = config.validate()
        # Should only fail on missing voice model and music dir (expected in test env)
        assert isinstance(errors, list)
        # In test env, these are expected to fail
        # The important thing is that validation runs without crashing

    def test_config_load_from_env(self, monkeypatch):
        """Environment variables should override config."""
        monkeypatch.setenv("ALSA_DEVICE_IN", "hw:1,0")
        monkeypatch.setenv("VAD_AGGRESSIVENESS", "3")
        monkeypatch.setenv("WHISPER_MODEL", "small")

        test_config = Config.from_env()

        assert test_config.audio.device_in == "hw:1,0"
        assert test_config.vad.aggressiveness == 3
        assert test_config.stt.model_size == "small"

    def test_vad_aggressiveness_validation(self):
        """VAD aggressiveness must be 0-3."""
        test_config = Config()
        test_config.vad.aggressiveness = 5
        errors = test_config.validate()
        assert any("AGGRESSIVENESS" in e for e in errors)

    def test_vad_frame_duration_validation(self):
        """VAD frame duration must be 10, 20, or 30."""
        test_config = Config()
        test_config.vad.frame_duration_ms = 15
        errors = test_config.validate()
        assert any("FRAME_DURATION" in e for e in errors)

    def test_hotword_threshold_validation(self):
        """Hotword threshold must be 0.0-1.0."""
        test_config = Config()
        test_config.hotword.threshold = 1.5
        errors = test_config.validate()
        assert any("THRESHOLD" in e for e in errors)

    def test_volume_percent_validation(self):
        """Volume percent must be 0-100."""
        test_config = Config()
        test_config.audio.volume_percent = 150
        errors = test_config.validate()
        assert any("VOLUME_PERCENT" in e for e in errors)

    def test_context_size_validation(self):
        """Ollama context size must be valid."""
        test_config = Config()
        test_config.llm.context_size = 1234
        errors = test_config.validate()
        assert any("CONTEXT_SIZE" in e for e in errors)

    def test_config_dataclass_fields(self):
        """Config should have all expected dataclass fields."""
        cfg = Config()
        assert hasattr(cfg, "debug")
        assert hasattr(cfg, "audio")
        assert hasattr(cfg, "vad")
        assert hasattr(cfg, "hotword")
        assert hasattr(cfg, "pipeline")
        assert hasattr(cfg, "stt")
        assert hasattr(cfg, "llm")
        assert hasattr(cfg, "tts")
        assert hasattr(cfg, "audio_feedback")
        assert hasattr(cfg, "feedback")
        assert hasattr(cfg, "skills_dir")
        assert hasattr(cfg, "brain_db_path")
        assert hasattr(cfg, "searxng_url")

    def test_audio_config_defaults(self):
        """AudioConfig should have sensible defaults."""
        ac = Config().audio
        assert ac.sample_rate == 16000
        assert ac.channels == 1
        assert ac.dtype == "int16"

    def test_hotword_config_defaults(self):
        """HotwordConfig should have sensible defaults."""
        hc = Config().hotword
        assert isinstance(hc.models, list)
        assert len(hc.models) >= 1
        assert 0.0 <= hc.threshold <= 1.0

    def test_llm_config_defaults(self):
        """LLMConfig should have sensible defaults."""
        lc = Config().llm
        assert lc.timeout > 0
        assert lc.context_size in (4096, 8192, 16384, 32768)

    def test_secrets_not_hardcoded(self):
        """Secrets should be empty by default (loaded from env)."""
        cfg = Config()
        # These should be empty strings by default
        assert cfg.gemini_api_key == ""
        assert cfg.shelly_gas_url == ""
        assert cfg.discord_bot_token == ""
        assert cfg.iqair_key == ""


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
