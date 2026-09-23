"""Tests for Neuro→Ollama Adapter (T018)."""

import numpy as np
import pytest

from src.brain.fly_brain import ConnectomeState
from src.brain.neuro_to_ollama import (
    ABRUPT_JUMP_DEG,
    AVERSIVE_AFFINITY,
    CAUTION_NOTE,
    HIGH_PREDICT,
    HIGH_TEMP,
    LOW_PREDICT,
    LOW_TEMP,
    NOTA_INTERNA,
    build_request,
    ollama_params,
)

BASE_SYSTEM = "Você é o Phantasma, um assistente de voz local."


def make_state(
    orientation: float = 42.0,
    jump: float = 10.0,
    affinity: float = 0.0,
    octopamine: float = 0.5,
) -> ConnectomeState:
    """Helper to create a ConnectomeState."""
    return ConnectomeState(
        orientation_deg=orientation,
        epg_jump_deg=jump,
        affinity=affinity,
        octopamine=octopamine,
        kc_active_fraction=0.1,
        mbon=np.zeros(8),
    )


class TestOllamaParams:
    """Test ollama_params() computation."""

    def test_temperature_interpolation_low_octopamine(self):
        """Low octopamine -> low temperature (calm/terse)."""
        state = make_state(octopamine=0.15)  # baseline
        params = ollama_params(state, BASE_SYSTEM)
        assert params.temperature == LOW_TEMP

    def test_temperature_interpolation_high_octopamine(self):
        """High octopamine -> high temperature (aroused/verbose)."""
        state = make_state(octopamine=0.7)  # saturated
        params = ollama_params(state, BASE_SYSTEM)
        assert params.temperature == HIGH_TEMP

    def test_temperature_interpolation_mid_octopamine(self):
        """Mid octopamine -> interpolated temperature."""
        state = make_state(octopamine=0.425)  # midpoint
        params = ollama_params(state, BASE_SYSTEM)
        expected = round((LOW_TEMP + HIGH_TEMP) / 2, 2)
        assert params.temperature == expected

    def test_temperature_clamped(self):
        """Temperature clamped to [0.0, 1.0]."""
        state = make_state(octopamine=2.0)  # above range
        params = ollama_params(state, BASE_SYSTEM)
        assert params.temperature <= 1.0
        state2 = make_state(octopamine=-1.0)  # below range
        params2 = ollama_params(state2, BASE_SYSTEM)
        assert params2.temperature >= 0.0

    def test_num_predict_interpolation(self):
        """num_predict interpolates between LOW_PREDICT and HIGH_PREDICT."""
        state = make_state(octopamine=0.15)
        params = ollama_params(state, BASE_SYSTEM)
        assert params.num_predict == LOW_PREDICT

        state2 = make_state(octopamine=0.7)
        params2 = ollama_params(state2, BASE_SYSTEM)
        assert params2.num_predict == HIGH_PREDICT

    def test_num_predict_clamped(self):
        """num_predict clamped to valid range."""
        state = make_state(octopamine=2.0)
        params = ollama_params(state, BASE_SYSTEM)
        assert LOW_PREDICT <= params.num_predict <= HIGH_PREDICT

    def test_abrupt_topic_change_detected(self):
        """|epg_jump_deg| > 120° -> abrupt_topic_change=True."""
        state = make_state(jump=150.0)
        params = ollama_params(state, BASE_SYSTEM)
        assert params.abrupt_topic_change is True
        assert NOTA_INTERNA in params.system

    def test_abrupt_topic_change_not_detected(self):
        """|epg_jump_deg| <= 120° -> abrupt_topic_change=False."""
        state = make_state(jump=100.0)
        params = ollama_params(state, BASE_SYSTEM)
        assert params.abrupt_topic_change is False
        assert NOTA_INTERNA not in params.system

    def test_negative_jump_also_triggers(self):
        """Negative jump beyond -120° also triggers."""
        state = make_state(jump=-150.0)
        params = ollama_params(state, BASE_SYSTEM)
        assert params.abrupt_topic_change is True

    def test_aversive_affinity_detected(self):
        """affinity < -0.3 -> aversive=True."""
        state = make_state(affinity=-0.5)
        params = ollama_params(state, BASE_SYSTEM)
        assert params.aversive is True
        assert CAUTION_NOTE in params.system

    def test_aversive_affinity_not_detected(self):
        """affinity >= -0.3 -> aversive=False."""
        state = make_state(affinity=-0.2)
        params = ollama_params(state, BASE_SYSTEM)
        assert params.aversive is False
        assert CAUTION_NOTE not in params.system

    def test_both_abrupt_and_aversive(self):
        """Both conditions add both notes to system."""
        state = make_state(jump=150.0, affinity=-0.5)
        params = ollama_params(state, BASE_SYSTEM)
        assert params.abrupt_topic_change is True
        assert params.aversive is True
        assert NOTA_INTERNA in params.system
        assert CAUTION_NOTE in params.system
        # Both notes present
        assert params.system.count("[Nota interna") == 2

    def test_base_system_preserved(self):
        """Base system prompt is preserved and prepended."""
        state = make_state()
        params = ollama_params(state, BASE_SYSTEM)
        assert params.system.startswith(BASE_SYSTEM)

    def test_empty_base_system(self):
        """Empty base system works."""
        state = make_state(jump=150.0)
        params = ollama_params(state, "")
        assert params.system == NOTA_INTERNA

    def test_params_dataclass_fields(self):
        """OllamaParams has all required fields."""
        state = make_state()
        params = ollama_params(state, BASE_SYSTEM)
        assert hasattr(params, "temperature")
        assert hasattr(params, "num_predict")
        assert hasattr(params, "system")
        assert hasattr(params, "abrupt_topic_change")
        assert hasattr(params, "aversive")


class TestBuildRequest:
    """Test build_request() payload generation."""

    def test_build_request_structure(self):
        """Payload has correct structure for Ollama /api/chat."""
        state = make_state()
        payload = build_request("olá", state, "llama3.1:8b", BASE_SYSTEM)

        assert payload["model"] == "llama3.1:8b"
        assert payload["stream"] is False
        assert "temperature" in payload
        assert "num_predict" in payload
        assert "messages" in payload
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1] == {"role": "user", "content": "olá"}

    def test_build_request_temperature_bounds(self):
        """Temperature in valid Ollama range."""
        state = make_state(octopamine=0.5)
        payload = build_request("test", state, "llama3.1:8b", BASE_SYSTEM)
        assert 0.0 <= payload["temperature"] <= 1.0

    def test_build_request_num_predict_bounds(self):
        """num_predict in valid range."""
        state = make_state(octopamine=0.5)
        payload = build_request("test", state, "llama3.1:8b", BASE_SYSTEM)
        assert 1 <= payload["num_predict"] <= 4096

    def test_build_request_abrupt_adds_note(self):
        """Abrupt jump adds NOTA_INTERNA to system message."""
        state = make_state(jump=150.0)
        payload = build_request("test", state, "llama3.1:8b", BASE_SYSTEM)
        system_msg = payload["messages"][0]["content"]
        assert NOTA_INTERNA in system_msg

    def test_build_request_aversive_adds_note(self):
        """Aversive affinity adds CAUTION_NOTE to system message."""
        state = make_state(affinity=-0.5)
        payload = build_request("test", state, "llama3.1:8b", BASE_SYSTEM)
        system_msg = payload["messages"][0]["content"]
        assert CAUTION_NOTE in system_msg


class TestConstants:
    """Test constant values match dfb."""

    def test_constants_match_dfb(self):
        assert ABRUPT_JUMP_DEG == 120.0
        assert AVERSIVE_AFFINITY == -0.3
        assert LOW_TEMP == 0.2
        assert HIGH_TEMP == 0.7
        assert LOW_PREDICT == 80
        assert HIGH_PREDICT == 250
        assert "mudou subitamente de tema" in NOTA_INTERNA
        assert "reagir negativamente" in CAUTION_NOTE


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
