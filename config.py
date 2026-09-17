"""
pHantasma Configuration
All runtime configuration in one place. Validated on startup.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AudioConfig:
    """Audio device and stream configuration."""
    device_in: Optional[str] = None  # ALSA device name or index (e.g., "hw:1,0" or "1")
    device_out: Optional[str] = None
    sample_rate: int = 16000
    channels: int = 1
    block_size: int = 1600  # 100ms at 16kHz
    dtype: str = "int16"


@dataclass
class VADConfig:
    """WebRTC Voice Activity Detection configuration."""
    aggressiveness: int = 2  # 0=least aggressive, 3=most aggressive
    frame_duration_ms: int = 30  # 10, 20, or 30ms


@dataclass
class HotwordConfig:
    """openWakeWord hotword detection configuration."""
    models: list[str] = field(default_factory=lambda: [
        "hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy"
    ])
    threshold: float = 0.5
    cooldown_seconds: float = 2.0


@dataclass
class STTConfig:
    """Speech-to-Text (Whisper) configuration."""
    model_size: str = "base"  # tiny, base, small, medium, large
    language: Optional[str] = None  # None = auto-detect; "pt", "en"
    fp16: bool = False  # CPU inference


@dataclass
class LLMConfig:
    """Language Model (Ollama) configuration."""
    host: str = "http://127.0.0.1:11434"
    model: str = "llama3:8b-instruct-8k"
    timeout_seconds: float = 30.0
    system_prompt: str = """Você é o Phantasma, um assistente de voz local, privado e offline.
Responda em português de Portugal, de forma concisa e natural.
Evite formatação markdown, emojis, ou texto que soe mal em TTS.
Não use "WOOHOO" ou exclamações robóticas.
O utilizador é vegano — considere isso em sugestões de comida/receitas."""


@dataclass
class TTSConfig:
    """Text-to-Speech (Piper) configuration."""
    voice_model_path: str = "voices/pt_PT-nicolau-medium.onnx"
    voice_config_path: str = "voices/pt_PT-nicolau-medium.onnx.json"
    use_sox_effects: bool = True
    sox_effects: list[list[str]] = field(default_factory=lambda: [
        ["pitch", "-100"],
        ["tempo", "1.1"],
        ["reverb", "20"]
    ])


@dataclass
class AudioFeedbackConfig:
    """Audio feedback (music snippet + greeting) configuration."""
    music_dir: str = "audio/music_snippets"
    greeting_path: str = "audio/greeting.wav"
    enabled: bool = True


@dataclass
class PipelineConfig:
    """Pipeline orchestration configuration."""
    queue_maxsize: int = 10
    hotword_chunk_seconds: float = 1.0
    stt_max_audio_seconds: float = 10.0
    log_level: str = "INFO"


@dataclass
class Config:
    """Root configuration."""
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    hotword: HotwordConfig = field(default_factory=HotwordConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    audio_feedback: AudioFeedbackConfig = field(default_factory=AudioFeedbackConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)

    def validate(self) -> list[str]:
        """Validate configuration. Returns list of errors (empty = valid)."""
        errors = []

        if self.vad.aggressiveness not in (0, 1, 2, 3):
            errors.append(f"vad.aggressiveness must be 0-3, got {self.vad.aggressiveness}")

        if self.vad.frame_duration_ms not in (10, 20, 30):
            errors.append(f"vad.frame_duration_ms must be 10, 20, or 30, got {self.vad.frame_duration_ms}")

        if self.stt.model_size not in ("tiny", "base", "small", "medium", "large"):
            errors.append(f"stt.model_size must be tiny/base/small/medium/large, got {self.stt.model_size}")

        if self.hotword.threshold < 0.0 or self.hotword.threshold > 1.0:
            errors.append(f"hotword.threshold must be 0.0-1.0, got {self.hotword.threshold}")

        if self.pipeline.queue_maxsize < 1:
            errors.append("pipeline.queue_maxsize must be >= 1")

        if not os.path.exists(self.tts.voice_model_path):
            errors.append(f"TTS voice model not found: {self.tts.voice_model_path}")

        if not os.path.exists(self.tts.voice_config_path):
            errors.append(f"TTS voice config not found: {self.tts.voice_config_path}")

        if self.audio_feedback.enabled and not os.path.isdir(self.audio_feedback.music_dir):
            errors.append(f"Music snippets directory not found: {self.audio_feedback.music_dir}")

        # Audio device validation - warn but don't fail (may not be available in test env)
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            if self.audio.device_in is not None:
                # Try to find device
                found = False
                for i, dev in enumerate(devices):
                    if str(self.audio.device_in) in (str(i), dev["name"]):
                        found = True
                        break
                if not found:
                    errors.append(f"Audio input device not found: {self.audio.device_in}")
        except Exception:
            pass  # sounddevice not available or no devices

        return errors


# Global config instance
config = Config()


def load_from_env(config: Config) -> Config:
    """Override config from environment variables."""
    # Audio
    if d := os.getenv("PHANTASMA_DEVICE_IN"):
        config.audio.device_in = d
    if d := os.getenv("PHANTASMA_DEVICE_OUT"):
        config.audio.device_out = d

    # VAD
    if v := os.getenv("PHANTASMA_VAD_AGGRESSIVENESS"):
        config.vad.aggressiveness = int(v)

    # Hotword
    if t := os.getenv("PHANTASMA_HOTWORD_THRESHOLD"):
        config.hotword.threshold = float(t)

    # STT
    if m := os.getenv("PHANTASMA_WHISPER_MODEL"):
        config.stt.model_size = m

    # LLM
    if h := os.getenv("PHANTASMA_OLLAMA_HOST"):
        config.llm.host = h
    if m := os.getenv("PHANTASMA_OLLAMA_MODEL"):
        config.llm.model = m

    # TTS
    if p := os.getenv("PHANTASMA_PIPER_VOICE"):
        config.tts.voice_model_path = p

    # Pipeline
    if q := os.getenv("PHANTASMA_QUEUE_SIZE"):
        config.pipeline.queue_maxsize = int(q)

    return config


# Load environment overrides on import
load_from_env(config)