"""Pipeline package for pHantasma voice assistant."""

from src.pipeline.audio_utils import (
    find_working_samplerate,
    force_volume_down,
    play_audio_file,
    play_greeting,
    play_random_music_snippet,
    play_tts,
    record_audio_vad,
)
from src.pipeline.llm import OllamaLLM, chat
from src.pipeline.stt import WhisperSTT, transcribe
from src.pipeline.tts import PiperTTS, synthesize
from src.pipeline.utils import Result, log_stage, logger, setup_logging, timed, timer

__all__ = [
    "force_volume_down",
    "find_working_samplerate",
    "play_tts",
    "play_random_music_snippet",
    "play_greeting",
    "record_audio_vad",
    "play_audio_file",
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
