"""FlyBrain neural co-processor package."""

from src.brain.fly_brain import (
    ConnectomeState,
    EPGRingAttractor,
    FlyBrain,
    MushroomBody,
    NeuromodulatoryPool,
    circular_distance,
)
from src.brain.neuro_to_ollama import (
    OllamaParams,
    build_request,
    ollama_params,
)

__all__ = [
    "ConnectomeState",
    "EPGRingAttractor",
    "FlyBrain",
    "MushroomBody",
    "NeuromodulatoryPool",
    "circular_distance",
    "OllamaParams",
    "build_request",
    "ollama_params",
]
