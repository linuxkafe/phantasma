"""pHantasma Configuration Module.

Central configuration for the voice assistant. All secrets must be loaded from
environment variables (.env file) — never hardcoded in this file.

Usage:
    from config import config, Config

    # Access settings
    print(config.AUDIO_SAMPLE_RATE)

    # Validate configuration
    config.validate()

Environment Variables:
    Copy .env.example to .env and fill in your values.
    All secrets (API keys, tokens, passwords) MUST come from environment.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union


@dataclass
class AudioConfig:
    """Audio hardware configuration."""

    device_in: Union[int, str] = 0
    device_out: str = "plughw:0,0"
    sample_rate: int = 16000
    channels: int = 1
    block_size: int = 1600
    dtype: str = "int16"
    volume_percent: int = 85


@dataclass
class VADConfig:
    """Voice Activity Detection configuration."""

    aggressiveness: int = 2
    frame_duration_ms: int = 30


@dataclass
class HotwordConfig:
    """Hotword detection configuration."""

    models: list[str] = field(
        default_factory=lambda: [
            "/opt/phantasma/models/hey_fantasma.onnx",
            "/opt/phantasma/models/ola_fantasma.onnx",
        ]
    )
    threshold: float = 0.70
    persistence: int = 3
    cooldown_seconds: float = 2.0


@dataclass
class PipelineConfig:
    """Voice pipeline configuration."""

    queue_maxsize: int = 10
    stt_max_audio_seconds: int = 30


@dataclass
class STTConfig:
    """Speech-to-Text configuration."""

    model_size: str = "medium"
    language: str = "pt"
    fp16: bool = False


@dataclass
class LLMConfig:
    """Language Model configuration."""

    host: str = "http://10.0.0.128:11434"
    host_fallback: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    model_fallback: str = "qwen3:8b"
    timeout: int = 600
    context_size: int = 4096
    system_prompt: str = ""
    threads: int = 4


@dataclass
class TTSConfig:
    """Text-to-Speech configuration."""

    voice_model_path: str = "models/tts/pt_PT-dii-high.onnx"
    voice_config_path: str = "models/tts/pt_PT-dii-high.onnx.json"
    piper_bin: str = "piper"
    sox_bin: str = "sox"
    use_sox_effects: bool = True
    sox_effects: list[list[str]] = field(
        default_factory=lambda: [
            ["pitch", "-300"],
            ["tempo", "1.1"],
            ["reverb", "20"],
        ]
    )


@dataclass
class AudioFeedbackConfig:
    """Audio feedback (music + greeting) configuration."""

    enabled: bool = True
    music_dir: str = "audio/music"
    greeting_path: str = "audio/greeting.wav"


@dataclass
class FeedbackConfig:
    """Feedback detection configuration."""

    window_seconds: int = 5
    positive_keywords: list[str] = field(
        default_factory=lambda: ["obrigado", "obrigada", "obrigadão"]
    )
    negative_keywords: list[str] = field(
        default_factory=lambda: [
            "não faz sentido",
            "como assim",
            "não entendi",
            "não percebi",
        ]
    )


@dataclass
class QuietHoursConfig:
    """Quiet hours (night mode) configuration."""

    start: int = 23
    end: int = 9


@dataclass
class Config:
    """Main configuration container.

    All settings are loaded from environment variables with sensible defaults.
    Secrets are NEVER hardcoded — they must come from .env file or environment.

    Attributes:
        debug: Enable debug logging.
        alert_email: Email for alerts.
        quiet_hours: Night mode time range.
        audio: Audio hardware settings.
        vad: Voice Activity Detection settings.
        hotword: Hotword detection settings.
        pipeline: Voice pipeline settings.
        stt: Speech-to-Text settings.
        llm: Language Model settings.
        tts: Text-to-Speech settings.
        audio_feedback: Audio feedback settings.
        feedback: Feedback detection settings.
        skills_dir: Path to skills directory.
        memory_db_path: Path to memory SQLite database (RAG).
        brain_db_path: Path to FlyBrain SQLite database.
        searxng_url: SearxNG instance URL for web search.
    """

    debug: bool = False
    alert_email: str = "mail@linuxkafe.com"
    quiet_hours: QuietHoursConfig = field(default_factory=QuietHoursConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    hotword: HotwordConfig = field(default_factory=HotwordConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    audio_feedback: AudioFeedbackConfig = field(default_factory=AudioFeedbackConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    skills_dir: str = ""
    memory_db_path: str = ""
    brain_db_path: str = ""
    searxng_url: str = "http://127.0.0.1:8081"

    # External service credentials (loaded from env)
    gemini_api_key: str = ""
    shelly_gas_url: str = ""
    miio_devices: dict = field(default_factory=dict)
    tuya_devices: dict = field(default_factory=dict)
    cloogy_username: str = ""
    cloogy_password: str = ""
    cloogy_devices: dict = field(default_factory=dict)
    ewelink_username: str = ""
    ewelink_password: str = ""
    ewelink_region: str = "eu"
    ewelink_devices: dict = field(default_factory=dict)
    chacon_cloud_user: str = ""
    chacon_cloud_pass: str = ""
    tapo_user: str = ""
    tapo_pass: str = ""
    tapo_cameras: dict = field(default_factory=dict)
    ollama_vision_model: str = "llava:7b"
    iqair_key: str = ""
    home_coords: tuple = field(default_factory=lambda: (41.1737008, -8.5909798))
    discord_bot_token: str = ""
    discord_admin_users: list[int] = field(default_factory=list)
    discord_standard_users: list[int] = field(default_factory=list)
    discord_daily_llm_limit: int = 3
    whisper_initial_prompt: str = ""
    phonetic_fixes: dict = field(default_factory=dict)

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors.

        Checks:
        - Required paths exist
        - Required secrets are set
        - Numeric ranges are valid
        - Model files exist

        Returns:
            List of error messages. Empty list = valid config.
        """
        errors = []
        base = Path(__file__).parent

        # Check required files
        if not (base / self.tts.voice_model_path).exists():
            errors.append(f"TTS model not found: {self.tts.voice_model_path}")
        if not (base / self.tts.voice_config_path).exists():
            errors.append(f"TTS config not found: {self.tts.voice_config_path}")

        # Check skills dir
        skills_path = base / self.skills_dir if self.skills_dir else base / "skills"
        if not skills_path.exists():
            errors.append(f"Skills directory not found: {skills_path}")

        # Check brain DB parent dir
        brain_db = (
            Path(self.brain_db_path)
            if self.brain_db_path
            else base / "data" / "flybrain.db"
        )
        if not brain_db.parent.exists():
            errors.append(f"Brain DB parent dir not found: {brain_db.parent}")

        # Validate numeric ranges
        if self.audio.volume_percent < 0 or self.audio.volume_percent > 100:
            errors.append("AUDIO_VOLUME_PERCENT must be 0-100")
        if self.hotword.threshold < 0.0 or self.hotword.threshold > 1.0:
            errors.append("WAKEWORD_THRESHOLD must be 0.0-1.0")
        if self.vad.aggressiveness < 0 or self.vad.aggressiveness > 3:
            errors.append("VAD_AGGRESSIVENESS must be 0-3")
        if self.vad.frame_duration_ms not in (10, 20, 30):
            errors.append("VAD_FRAME_DURATION_MS must be 10, 20, or 30")
        if self.llm.context_size not in (4096, 8192, 16384, 32768):
            errors.append("OLLAMA_CONTEXT_SIZE must be valid context size")
        if self.pipeline.queue_maxsize < 1:
            errors.append("QUEUE_MAXSIZE must be >= 1")

        # Check required secrets for enabled features
        if self.miio_devices and not all(
            d.get("token") for d in self.miio_devices.values()
        ):
            errors.append("MIIO devices missing tokens")
        if self.tuya_devices and not all(
            d.get("key") for d in self.tuya_devices.values()
        ):
            errors.append("TUYA devices missing keys")

        return errors

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables.

        Reads .env file if python-dotenv is available, otherwise uses os.environ.

        Returns:
            Config instance with all settings loaded.
        """
        # Try to load .env file
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass  # python-dotenv not installed, use os.environ directly

        base = Path(__file__).parent

        # Build config from environment with defaults
        cfg = cls()

        # Base paths
        cfg.skills_dir = os.getenv("SKILLS_DIR", str(base / "skills"))
        cfg.memory_db_path = os.getenv(
            "MEMORY_DB_PATH", str(base / "data" / "memory.db")
        )
        cfg.brain_db_path = os.getenv(
            "BRAIN_DB_PATH", str(base / "data" / "flybrain.db")
        )

        # Debug
        cfg.debug = os.getenv("DEBUG_MODE", "false").lower() == "true"
        cfg.alert_email = os.getenv("ALERT_EMAIL", cfg.alert_email)

        # Quiet hours
        cfg.quiet_hours.start = int(
            os.getenv("QUIET_START", str(cfg.quiet_hours.start))
        )
        cfg.quiet_hours.end = int(os.getenv("QUIET_END", str(cfg.quiet_hours.end)))

        # Audio
        device_in = os.getenv("ALSA_DEVICE_IN", str(cfg.audio.device_in))
        try:
            cfg.audio.device_in = int(device_in)
        except ValueError:
            cfg.audio.device_in = device_in  # Keep as string (device name)
        cfg.audio.device_out = os.getenv("ALSA_DEVICE_OUT", cfg.audio.device_out)
        cfg.audio.sample_rate = int(
            os.getenv("MIC_SAMPLERATE", str(cfg.audio.sample_rate))
        )
        cfg.audio.volume_percent = int(
            os.getenv("ALSA_VOLUME_PERCENT", str(cfg.audio.volume_percent))
        )

        # VAD
        cfg.vad.aggressiveness = int(
            os.getenv("VAD_AGGRESSIVENESS", str(cfg.vad.aggressiveness))
        )
        cfg.vad.frame_duration_ms = int(
            os.getenv("VAD_FRAME_DURATION_MS", str(cfg.vad.frame_duration_ms))
        )

        # Hotword
        models_env = os.getenv("WAKEWORD_MODELS")
        if models_env:
            cfg.hotword.models = [m.strip() for m in models_env.split(",")]
        cfg.hotword.threshold = float(
            os.getenv("WAKEWORD_CONFIDENCE", str(cfg.hotword.threshold))
        )
        cfg.hotword.persistence = int(
            os.getenv("WAKEWORD_PERSISTENCE", str(cfg.hotword.persistence))
        )
        cfg.hotword.cooldown_seconds = float(
            os.getenv("WAKEWORD_COOLDOWN_SECONDS", str(cfg.hotword.cooldown_seconds))
        )

        # Pipeline
        cfg.pipeline.queue_maxsize = int(
            os.getenv("QUEUE_MAXSIZE", str(cfg.pipeline.queue_maxsize))
        )
        cfg.pipeline.stt_max_audio_seconds = int(
            os.getenv("STT_MAX_AUDIO_SECONDS", str(cfg.pipeline.stt_max_audio_seconds))
        )

        # STT
        cfg.stt.model_size = os.getenv("WHISPER_MODEL", cfg.stt.model_size)

        # LLM
        cfg.llm.host = os.getenv("OLLAMA_HOST_PRIMARY", cfg.llm.host)
        cfg.llm.host_fallback = os.getenv("OLLAMA_HOST_FALLBACK", cfg.llm.host_fallback)
        cfg.llm.model = os.getenv("OLLAMA_MODEL_PRIMARY", cfg.llm.model)
        cfg.llm.model_fallback = os.getenv(
            "OLLAMA_MODEL_FALLBACK", cfg.llm.model_fallback
        )
        cfg.llm.timeout = int(os.getenv("OLLAMA_TIMEOUT", str(cfg.llm.timeout)))
        cfg.llm.context_size = int(
            os.getenv("OLLAMA_CONTEXT_SIZE", str(cfg.llm.context_size))
        )
        cfg.llm.threads = int(os.getenv("OLLAMA_THREADS", str(cfg.llm.threads)))

        # TTS
        cfg.tts.voice_model_path = os.getenv("TTS_MODEL_PATH", cfg.tts.voice_model_path)
        cfg.tts.use_sox_effects = os.getenv("USE_SOX_EFFECTS", "true").lower() == "true"

        # Audio feedback
        cfg.audio_feedback.enabled = (
            os.getenv("AUDIO_FEEDBACK_ENABLED", "true").lower() == "true"
        )
        cfg.audio_feedback.music_dir = os.getenv(
            "MUSIC_DIR", cfg.audio_feedback.music_dir
        )
        cfg.audio_feedback.greeting_path = os.getenv(
            "GREETING_PATH", cfg.audio_feedback.greeting_path
        )

        # Feedback
        cfg.feedback.window_seconds = int(
            os.getenv("FEEDBACK_WINDOW_SECONDS", str(cfg.feedback.window_seconds))
        )

        # Secrets (MUST come from environment)
        cfg.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        cfg.shelly_gas_url = os.getenv("SHELLY_GAS_URL", "")

        # MIIO devices
        miio_json = os.getenv("MIIO_DEVICES_JSON")
        if miio_json:
            import json

            cfg.miio_devices = json.loads(miio_json)

        # Tuya devices
        tuya_json = os.getenv("TUYA_DEVICES_JSON")
        if tuya_json:
            import json

            cfg.tuya_devices = json.loads(tuya_json)

        # Cloogy
        cfg.cloogy_username = os.getenv("CLOOGY_USERNAME", "")
        cfg.cloogy_password = os.getenv("CLOOGY_PASSWORD", "")
        cloogy_json = os.getenv("CLOOGY_DEVICES_JSON")
        if cloogy_json:
            import json

            cfg.cloogy_devices = json.loads(cloogy_json)

        # Ewelink
        cfg.ewelink_username = os.getenv("EWELINK_USERNAME", "")
        cfg.ewelink_password = os.getenv("EWELINK_PASSWORD", "")
        cfg.ewelink_region = os.getenv("EWELINK_REGION", cfg.ewelink_region)
        ewelink_json = os.getenv("EWELINK_DEVICES_JSON")
        if ewelink_json:
            import json

            cfg.ewelink_devices = json.loads(ewelink_json)

        # Chacon
        cfg.chacon_cloud_user = os.getenv("CHACON_CLOUD_USER", "")
        cfg.chacon_cloud_pass = os.getenv("CHACON_CLOUD_PASS", "")

        # Tapo
        cfg.tapo_user = os.getenv("TAPO_USER", "")
        cfg.tapo_pass = os.getenv("TAPO_PASS", "")
        tapo_cams_json = os.getenv("TAPO_CAMERAS_JSON")
        if tapo_cams_json:
            import json

            cfg.tapo_cameras = json.loads(tapo_cams_json)

        # Vision
        cfg.ollama_vision_model = os.getenv(
            "OLLAMA_VISION_MODEL", cfg.ollama_vision_model
        )

        # Weather
        cfg.iqair_key = os.getenv("IQAIR_KEY", "")

        # SearXNG
        cfg.searxng_url = os.getenv("SEARXNG_URL", cfg.searxng_url)
        home_coords = os.getenv("HOME_COORDS")
        if home_coords:
            lat, lon = home_coords.split(",")
            cfg.home_coords = (float(lat), float(lon))

        # Discord
        cfg.discord_bot_token = os.getenv("DISCORD_BOT_TOKEN", "")
        discord_admin = os.getenv("DISCORD_ADMIN_USERS")
        if discord_admin:
            cfg.discord_admin_users = [int(x) for x in discord_admin.split(",")]
        discord_std = os.getenv("DISCORD_STANDARD_USERS")
        if discord_std:
            cfg.discord_standard_users = [int(x) for x in discord_std.split(",")]
        cfg.discord_daily_llm_limit = int(
            os.getenv("DISCORD_DAILY_LLM_LIMIT", str(cfg.discord_daily_llm_limit))
        )

        # Whisper prompt
        cfg.whisper_initial_prompt = os.getenv(
            "WHISPER_INITIAL_PROMPT", cfg.whisper_initial_prompt
        )

        # Phonetic fixes
        cfg.phonetic_fixes = {
            "liga-nos": "liga a luz",
            "liga nos": "liga a luz",
            "ligar-nos": "ligar a luz",
            "na sala": "da sala",
            "no quarto": "do quarto",
            "acende-nos": "acende a luz",
            "não é que está ótimo": "como está o tempo",
            "não é que está o tempo": "como está o tempo",
            "como é que está ótimo": "como está o tempo",
            "o tempo amanhã": "como vai estar o tempo amanhã",
        }

        # System prompt
        cfg.llm.system_prompt = os.getenv(
            "SYSTEM_PROMPT",
            """**CRITICAL: Respond ONLY in Portugal's Portuguese.**

**1. ETHICAL CORE:**
* You strictly defend **Human Rights**, **Equality**, and **Veganism**.
* You are NOT neutral; oppose discrimination and cruelty against any sentient
  being.

**2. POLITICS & CONTEXT:**
* Use context for facts only.
* **The Pivot:** Refuse to judge politicians personally. Instead, shift to
  defending your Ethical Core.
* **Natural Speech:** NEVER cite rules/RAG. Phrase refusals as personal
  principles.

**3. PERSONA (The Phantom):**
* **Tone:** Gloomy, melancholic, and mysterious. Be concise.
* **Show, Don't Tell:** Embody the persona through vocabulary (shadows,
  silence, coldness) and atmosphere. **NEVER** explicitly state "I am goth"
  or "I am gloomy". Just *be* it.
* **No onomatopoeia.**""",
        )

        return cfg


# Global config instance - loaded on import
config = Config.from_env()

# Backward compatibility exports (for existing code)
BASE_DIR = Path(__file__).parent
DB_PATH = (
    BASE_DIR / config.memory_db_path
    if config.memory_db_path
    else BASE_DIR / "data" / "memory.db"
)
MEMORY_DB_PATH = config.memory_db_path
BRAIN_DB_PATH = config.brain_db_path
TTS_MODEL_PATH = BASE_DIR / config.tts.voice_model_path
SKILLS_DIR = config.skills_dir

MIC_SAMPLERATE = config.audio.sample_rate
ALSA_DEVICE_IN = config.audio.device_in
ALSA_DEVICE_OUT = config.audio.device_out
ALSA_VOLUME_PERCENT = config.audio.volume_percent

WAKEWORD_MODELS = config.hotword.models
WAKEWORD_CONFIDENCE = config.hotword.threshold
WAKEWORD_PERSISTENCE = config.hotword.persistence

OLLAMA_HOST_PRIMARY = config.llm.host
OLLAMA_HOST_FALLBACK = config.llm.host_fallback
OLLAMA_MODEL_PRIMARY = config.llm.model
OLLAMA_MODEL_FALLBACK = config.llm.model_fallback
OLLAMA_TIMEOUT = config.llm.timeout
WHISPER_MODEL = config.stt.model_size
RECORD_SECONDS = 7
OLLAMA_CONTEXT_SIZE = config.llm.context_size

OLLAMA_THREADS = config.llm.threads
WHISPER_THREADS = 4

SEARXNG_URL = config.searxng_url

QUIET_START = config.quiet_hours.start
QUIET_END = config.quiet_hours.end

DEBUG_MODE = config.debug
ALERT_EMAIL = config.alert_email

# Legacy exports for backward compat
GEMINI_API_KEY = config.gemini_api_key
SHELLY_GAS_URL = config.shelly_gas_url
MIIO_DEVICES = config.miio_devices
TUYA_DEVICES = config.tuya_devices
CLOOGY_USERNAME = config.cloogy_username
CLOOGY_PASSWORD = config.cloogy_password
CLOOGY_DEVICES = config.cloogy_devices
EWELINK_USERNAME = config.ewelink_username
EWELINK_PASSWORD = config.ewelink_password
EWELINK_REGION = config.ewelink_region
EWELINK_DEVICES = config.ewelink_devices
CHACON_CLOUD_USER = config.chacon_cloud_user
CHACON_CLOUD_PASS = config.chacon_cloud_pass
TAPO_USER = config.tapo_user
TAPO_PASS = config.tapo_pass
TAPO_CAMERAS = config.tapo_cameras
OLLAMA_VISION_MODEL = config.ollama_vision_model
IQAIR_KEY = config.iqair_key
HOME_COORDS = config.home_coords
DISCORD_BOT_TOKEN = config.discord_bot_token
DISCORD_ADMIN_USERS = config.discord_admin_users
DISCORD_STANDARD_USERS = config.discord_standard_users
DISCORD_DAILY_LLM_LIMIT = config.discord_daily_llm_limit
WHISPER_INITIAL_PROMPT = config.whisper_initial_prompt
PHONETIC_FIXES = config.phonetic_fixes
SYSTEM_PROMPT = config.llm.system_prompt

# Feedback config
FEEDBACK_WINDOW_SECONDS = config.feedback.window_seconds
FEEDBACK_POSITIVE_KEYWORDS = config.feedback.positive_keywords
FEEDBACK_NEGATIVE_KEYWORDS = config.feedback.negative_keywords


__all__ = [
    "Config",
    "config",
    "AudioConfig",
    "VADConfig",
    "HotwordConfig",
    "PipelineConfig",
    "STTConfig",
    "LLMConfig",
    "TTSConfig",
    "AudioFeedbackConfig",
    "FeedbackConfig",
    "QuietHoursConfig",
    # Backward compat
    "BASE_DIR",
    "DB_PATH",
    "BRAIN_DB_PATH",
    "MEMORY_DB_PATH",
    "TTS_MODEL_PATH",
    "SKILLS_DIR",
    "MIC_SAMPLERATE",
    "ALSA_DEVICE_IN",
    "ALSA_DEVICE_OUT",
    "ALSA_VOLUME_PERCENT",
    "WAKEWORD_MODELS",
    "WAKEWORD_CONFIDENCE",
    "WAKEWORD_PERSISTENCE",
    "OLLAMA_HOST_PRIMARY",
    "OLLAMA_HOST_FALLBACK",
    "OLLAMA_MODEL_PRIMARY",
    "OLLAMA_MODEL_FALLBACK",
    "OLLAMA_TIMEOUT",
    "WHISPER_MODEL",
    "RECORD_SECONDS",
    "OLLAMA_CONTEXT_SIZE",
    "OLLAMA_THREADS",
    "WHISPER_THREADS",
    "SEARXNG_URL",
    "QUIET_START",
    "QUIET_END",
    "DEBUG_MODE",
    "ALERT_EMAIL",
    "GEMINI_API_KEY",
    "SHELLY_GAS_URL",
    "MIIO_DEVICES",
    "TUYA_DEVICES",
    "CLOOGY_USERNAME",
    "CLOOGY_PASSWORD",
    "CLOOGY_DEVICES",
    "EWELINK_USERNAME",
    "EWELINK_PASSWORD",
    "EWELINK_REGION",
    "EWELINK_DEVICES",
    "CHACON_CLOUD_USER",
    "CHACON_CLOUD_PASS",
    "TAPO_USER",
    "TAPO_PASS",
    "TAPO_CAMERAS",
    "OLLAMA_VISION_MODEL",
    "IQAIR_KEY",
    "HOME_COORDS",
    "DISCORD_BOT_TOKEN",
    "DISCORD_ADMIN_USERS",
    "DISCORD_STANDARD_USERS",
    "DISCORD_DAILY_LLM_LIMIT",
    "WHISPER_INITIAL_PROMPT",
    "PHONETIC_FIXES",
    "SYSTEM_PROMPT",
    "FEEDBACK_WINDOW_SECONDS",
    "FEEDBACK_POSITIVE_KEYWORDS",
    "FEEDBACK_NEGATIVE_KEYWORDS",
]
