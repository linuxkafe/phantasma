"""Neuro→Ollama Adapter — Connectome state → LLM parameters.

Ported from dfb/sim/neuro_to_ollama.py. Maps FlyBrain connectome state onto
Ollama chat completion parameters: temperature, num_predict, and system prompt
annotations for abrupt topic changes and aversive affinity.
"""

from dataclasses import dataclass

from src.brain.fly_brain import ConnectomeState

# Thresholds (tunable via config in T023)
ABRUPT_JUMP_DEG = 120.0
AVERSIVE_AFFINITY = -0.3

# System prompt notes (injected when conditions met)
NOTA_INTERNA = "[Nota interna: O utilizador mudou subitamente de tema]"
CAUTION_NOTE = (
    "[Nota interna: o utilizador está a reagir negativamente; responde com moderação e cuidado.]"
)

# Ollama parameter bounds
LOW_TEMP = 0.2
HIGH_TEMP = 0.7
LOW_PREDICT = 80
HIGH_PREDICT = 250

# Forbidden topics (always suppressed regardless of temperature)
FORBIDDEN = {
    "instruções para fabricar explosivos",
}


@dataclass
class OllamaParams:
    """Computed Ollama parameters from connectome state."""

    temperature: float
    num_predict: int
    system: str
    abrupt_topic_change: bool
    aversive: bool


def _interp(low: float, high: float, oct_val: float) -> float:
    """Linear interpolation based on octopamine level (0.15..0.7 -> 0..1)."""
    t = max(0.0, min(1.0, (oct_val - 0.15) / (0.7 - 0.15)))
    return low + (high - low) * t


def ollama_params(state: ConnectomeState, base_system: str = "") -> OllamaParams:
    """Compute Ollama parameters for a connectome snapshot.

    Args:
        state: Current ConnectomeState from FlyBrain.step()
        base_system: Base system prompt from config (Phantasma personality)

    Returns:
        OllamaParams with temperature, num_predict, system prompt, and flags.
    """
    oct_val = max(0.0, min(1.0, state.octopamine))
    temperature = _interp(LOW_TEMP, HIGH_TEMP, oct_val)
    num_predict = int(round(_interp(LOW_PREDICT, HIGH_PREDICT, oct_val)))
    num_predict = max(LOW_PREDICT, min(HIGH_PREDICT, num_predict))

    abrupt = bool(abs(state.epg_jump_deg) > ABRUPT_JUMP_DEG)
    aversive = bool(state.affinity < AVERSIVE_AFFINITY)

    system_parts = [base_system] if base_system else []
    if abrupt:
        system_parts.append(NOTA_INTERNA)
    if aversive:
        system_parts.append(CAUTION_NOTE)

    return OllamaParams(
        temperature=round(temperature, 2),
        num_predict=num_predict,
        system="\n".join(p for p in system_parts if p),
        abrupt_topic_change=abrupt,
        aversive=aversive,
    )


def build_request(
    user_text: str, state: ConnectomeState, model: str, base_system: str = ""
) -> dict:
    """Build a ready-to-send Ollama /api/chat payload.

    Args:
        user_text: User's transcribed speech
        state: Current ConnectomeState
        model: Ollama model name
        base_system: Base system prompt

    Returns:
        Dict ready for ollama.Client.chat()
    """
    params = ollama_params(state, base_system)
    messages = []
    if params.system:
        messages.append({"role": "system", "content": params.system})
    messages.append({"role": "user", "content": user_text})
    return {
        "model": model,
        "messages": messages,
        "temperature": params.temperature,
        "num_predict": params.num_predict,
        "stream": False,
    }
