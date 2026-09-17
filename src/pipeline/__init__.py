"""Pipeline package for pHantasma voice assistant."""

from src.pipeline.audio import (
    AudioCapture,
    AudioFrame,
    AudioPlayback,
    HotwordDetector,
    VADProcessor,
)
from src.pipeline.llm import OllamaLLM, chat
from src.pipeline.stt import WhisperSTT, transcribe
from src.pipeline.tts import PiperTTS, synthesize
from src.pipeline.utils import Result, log_stage, logger, setup_logging, timed, timer

__all__ = [
    "AudioCapture",
    "AudioPlayback",
    "VADProcessor",
    "HotwordDetector",
    "AudioFrame",
    "WhisperSTT",
    "transcribe",
    "OllamaLLM",
    "chat",
    "PiperTTS",
    "synthesize",
    "Result",
    "logger",
    "log_stage",
    "setup_logging",
    "timed",
    "timer",
]
