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
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union


@dataclass
class AudioConfig:
    """Audio hardware configuration."""

    device_in: Union[int, str] = "auto"
    device_out: str = "plughw:0,0"
    sample_rate: int = 16000
    channels: int = 1
    block_size: int = 1600
    dtype: str = "int16"
    volume_percent: int = 85
    auto_detect: bool = True
    disable_agc: bool = True
    capture_volume: int = 85


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
    # Per-model threshold overrides, e.g. {"hey_fantasma": 0.50}.
    #
    # STATE as of 2026-09-27: INERT. Prod loads {} and WAKEWORD_CONFIDENCE_PER_MODEL
    # is set in no .env, so src/pipeline/audio.py:302 falls through to the global
    # threshold below and this field changes nothing at runtime. The rationale is
    # kept because it is still the right design, but the override is not active.
    # To activate: set WAKEWORD_CONFIDENCE_PER_MODEL in the .env, e.g.
    #   WAKEWORD_CONFIDENCE_PER_MODEL=ola_fantasma:0.70,hey_fantasma:0.50
    #
    # Why it should exist: a single global threshold cannot serve both models.
    # Measured peaks were ola_fantasma 0.8111 and hey_fantasma 0.6326, so the
    # legacy global 0.70 left hey_fantasma with ZERO qualifying windows -- that
    # phrase could never fire. Against a live speech floor of 0.0009, a 0.50
    # floor for hey_fantasma still leaves ~550x margin.
    #
    # TOPOLOGY (corrected twice on 2026-09-27): /opt/phantasma/src is a REAL
    # DIRECTORY, not a symlink and not a hardlink. It was a symlink into
    # /home/seyon/dev/pHantasma/src until 2026-09-27, when it was replaced by a
    # verified copy and the two trees were decoupled. Do not reintroduce either
    # form of sharing: deploy.sh:80 asserts it is not a symlink, and a shared
    # tree makes prod and dev unfalsifiable against each other.
    thresholds_per_model: dict = field(default_factory=dict)

    # --- Ambient-noise adaptive threshold ---
    #
    # Added 2026-09-29 after a 03:41 false activation inside quiet hours
    # (score 0.80 vs a flat 0.70) that played the acknowledgement and
    # transcribed to nothing. The room was the trigger, not the wake word.
    #
    # A flat threshold cannot distinguish a quiet room from a noisy one, so the
    # measured floor of the room raises the bar -- and only the floor, never the
    # instantaneous level, because the loudest sound in the room is the user
    # saying the wake word. See src/pipeline/noise.py for why that asymmetry is
    # the whole design.
    #
    # max_bump is deliberately bounded: raising the bar forever is the same as
    # turning the microphone off. 0.20 takes the 03:41 event (0.80) below a
    # 0.90 bar while leaving a real wake word, which scores far higher than the
    # 0.0009 speech floor measured on 2026-09-27, reachable.
    noise_adaptive: bool = True
    noise_quiet_db: float = -60.0
    noise_loud_db: float = -35.0
    noise_max_bump: float = 0.20


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
    # Connect timeout, separate from the read timeout above.
    #
    # OLLAMA_TIMEOUT is a READ budget: a long generation legitimately takes
    # minutes, and the operator set 600 on purpose. It was never passed to
    # ollama.Client at all, so a host that is simply down (the primary
    # 10.0.0.128:11434 stopped answering on 2026-09-29) blocked the caller until
    # the OS gave up -- an assistant that cannot answer is one that also cannot
    # be restarted or tested. Reaching a dead host is not a slow generation, it
    # is a failure, and it is worth failing fast on.
    connect_timeout: int = 10
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
    end: int = 7  # was 9: the owner asked for 23-07. .env still says 9; the admin
                 # override wins over both and is what the schedule reads.


_OVERLAY_KEYS = frozenset(
    {
        # Audio
        "ALSA_DEVICE_IN",
        "ALSA_DEVICE_OUT",
        "ALSA_VOLUME_PERCENT",
        "MIC_SAMPLERATE",
        "VAD_AGGRESSIVENESS",
        "VAD_FRAME_DURATION_MS",
        "WAKEWORD_CONFIDENCE",
        "WAKEWORD_PERSISTENCE",
        "WAKEWORD_COOLDOWN_SECONDS",
        "WAKEWORD_MODELS",
        # General
        "AUDIO_FEEDBACK_ENABLED",
        "USE_SOX_EFFECTS",
        "FEEDBACK_WINDOW_SECONDS",
        "STT_MAX_AUDIO_SECONDS",
        "QUEUE_MAXSIZE",
        "MUSIC_DIR",
        "GREETING_PATH",
        "TTS_MODEL_PATH",
        "SKILLS_DIR",
        "HOME_COORDS",
        # LLM
        "OLLAMA_HOST_PRIMARY",
        "OLLAMA_HOST_FALLBACK",
        "OLLAMA_MODEL_PRIMARY",
        "OLLAMA_MODEL_FALLBACK",
        "OLLAMA_VISION_MODEL",
        "OLLAMA_CONTEXT_SIZE",
        "OLLAMA_THREADS",
        "OLLAMA_TIMEOUT",
        "WHISPER_MODEL",
        "WHISPER_INITIAL_PROMPT",
        # Security
        "DEBUG_MODE",
        "ALERT_EMAIL",
    }
)


def _overlay_owner_settings(memory_db_path: str) -> None:
    """Apply owner settings written by /admin/config on top of os.environ.

    The admin UI persists friendly controls to ``app_settings`` (the same
    database table as ``quiet_schedule``). Without this overlay those controls
    would be decoration: the table is real, this read is what turns it into
    effective configuration for the whole process on the next start.

    Deliberately silent on failure. ``app_settings`` hosts no secrets (secret
    service tokens stay in the root-owned ``.env``), the key set is whitelisted
    above, and any row that cannot be applied is dropped rather than fatal.
    """
    if not memory_db_path:
        return
    try:
        conn = sqlite3.connect(memory_db_path, timeout=5)
    except sqlite3.Error:
        return
    try:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    except sqlite3.Error:
        rows = ()
    finally:
        conn.close()
    for key, value in rows:
        if key in _OVERLAY_KEYS and value not in (None, ""):
            os.environ[key] = str(value)


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
        tts_cache_dir: Path to the TTS audio cache.
        config_db_path: Path to the config SQLite database (admin settings).
        cache_dir: Root directory for the JSON caches used by skills.
        public_dir: Directory of static assets served under /public.
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
    config_db_path: str = ""
    tts_cache_dir: str = ""
    cache_dir: str = ""
    public_dir: str = ""
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
    # Local Chacon plug (HF-LPB100). The cloud credentials above are dead, so the
    # plug is controlled directly over UDP. Non-empty IP => the device gets a tile
    # in the UI. Defaulted to the known plug so the tile exists without env setup.
    chacon_plug_ip: str = "10.0.0.116"
    chacon_plug_name: str = "luz do balcão"
    chacon_plug_port: int = 18530
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

    def db_path(self, name: str) -> str:
        """Resolve a database path to an ABSOLUTE path, exactly once.

        This is the ONLY supported way to obtain a database path. Every caller
        must go through here, because both failure modes it replaces were
        silent:

        1. A relative value like ``data/brain.db`` resolved against whatever the
           process cwd happened to be. Starting the API from another directory
           opened a different database -- or none at all.
        2. Modules that "knew" the path hardcoded ``/opt/phantasma/data/...``.
           That works in production by coincidence and, in a dev checkout, points
           a dev process at the PRODUCTION database -- so a dev session writes to
           production data.

        An absolute path in the .env is honoured as-is, so a host can still place
        its databases outside the tree.
        """
        p = Path(name)
        if p.is_absolute():
            return str(p)
        return str((Path(__file__).parent / p).resolve())

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
        brain_db = Path(self.brain_db_path) if self.brain_db_path else base / "data" / "flybrain.db"
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
        if self.miio_devices and not all(d.get("token") for d in self.miio_devices.values()):
            errors.append("MIIO devices missing tokens")
        if self.tuya_devices and not all(d.get("key") for d in self.tuya_devices.values()):
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
        cfg.memory_db_path = os.getenv("MEMORY_DB_PATH", str(base / "data" / "memory.db"))
        cfg.brain_db_path = os.getenv("BRAIN_DB_PATH", str(base / "data" / "flybrain.db"))
        cfg.config_db_path = os.getenv("CONFIG_DB_PATH", str(base / "data" / "config.db"))
        # Resolve to absolute here, once, so no downstream module is tempted to
        # re-derive a path. See Config.db_path() for the two silent failures
        # this prevents.
        cfg.brain_db_path = cfg.db_path(cfg.brain_db_path)
        cfg.memory_db_path = cfg.db_path(cfg.memory_db_path)
        cfg.config_db_path = cfg.db_path(cfg.config_db_path)
        cfg.tts_cache_dir = cfg.db_path(cfg.tts_cache_dir)
        cfg.cache_dir = cfg.db_path(cfg.cache_dir)
        cfg.public_dir = cfg.db_path(cfg.public_dir)
        # audio_utils.py reads this via getattr(config, "TTS_CACHE_DIR", ...).
        # The attribute did not exist, so the getattr always fell through to
        # its hardcoded /opt/phantasma fallback and the indirection was inert.
        # Declaring it here makes the lookup real and puts the path with the
        # other paths, overridable per host like everything else.
        cfg.tts_cache_dir = os.getenv("TTS_CACHE_DIR", str(base / "cache" / "tts"))
        # Root for every JSON cache that used to be hardcoded to
        # /opt/phantasma/cache/<name>.json. Same rationale as TTS_CACHE_DIR: a
        # host path in code cannot be overridden, so the caches had to be forked
        # per host. Resolved absolutely so it does not depend on cwd.
        cfg.cache_dir = os.getenv("CACHE_DIR", str(base / "cache"))
        # Static assets served by /public. Only present in the prod tree;
        # a dev checkout legitimately has none and the route 404s, which is
        # correct -- the old literal also did not exist there.
        cfg.public_dir = os.getenv("PUBLIC_DIR", str(base / "public"))

        # Owner-editable settings saved from /admin/config land in app_settings
        # (the same store as quiet_schedule and reaction weights). Overlay them
        # on os.environ so the runtime honours the next boot: the UI controls
        # are real, not a decorative mirror. All env reads below happen after
        # this point. A missing DB or a bad row must never brick starting up.
        _overlay_owner_settings(cfg.memory_db_path)

        # Debug
        cfg.debug = os.getenv("DEBUG_MODE", "false").lower() == "true"
        cfg.alert_email = os.getenv("ALERT_EMAIL", cfg.alert_email)

        # Quiet hours
        cfg.quiet_hours.start = int(os.getenv("QUIET_START", str(cfg.quiet_hours.start)))
        cfg.quiet_hours.end = int(os.getenv("QUIET_END", str(cfg.quiet_hours.end)))

        # Audio
        device_in = os.getenv("ALSA_DEVICE_IN", str(cfg.audio.device_in))
        if device_in == "":
            cfg.audio.device_in = None
        else:
            try:
                cfg.audio.device_in = int(device_in)
            except ValueError:
                cfg.audio.device_in = device_in  # Keep as string (device name)
        cfg.audio.device_out = os.getenv("ALSA_DEVICE_OUT", cfg.audio.device_out)
        cfg.audio.sample_rate = int(os.getenv("MIC_SAMPLERATE", str(cfg.audio.sample_rate)))
        cfg.audio.volume_percent = int(
            os.getenv("ALSA_VOLUME_PERCENT", str(cfg.audio.volume_percent))
        )
        cfg.audio.auto_detect = os.getenv("AUDIO_DEVICE_AUTO_DETECT", "true").lower() == "true"
        # The capture block size was the one audio setting with NO env override,
        # so it was pinned in a host-specific edit of this file -- which is what
        # forced /opt/phantasma/config.py to fork from this one. Prod captures
        # 512 (one ALSA period) while this repo defaults to 1600. Now that it is
        # overridable the two files can be identical and the host value lives in
        # .env, where it belongs.
        cfg.audio.block_size = int(os.getenv("AUDIO_BLOCK_SIZE", str(cfg.audio.block_size)))
        cfg.audio.disable_agc = os.getenv("AUDIO_AGC_DISABLE", "true").lower() == "true"
        cfg.audio.capture_volume = int(
            os.getenv("AUDIO_CAPTURE_VOLUME", str(cfg.audio.capture_volume))
        )

        # VAD
        cfg.vad.aggressiveness = int(os.getenv("VAD_AGGRESSIVENESS", str(cfg.vad.aggressiveness)))
        cfg.vad.frame_duration_ms = int(
            os.getenv("VAD_FRAME_DURATION_MS", str(cfg.vad.frame_duration_ms))
        )

        # Hotword
        models_env = os.getenv("WAKEWORD_MODELS")
        if models_env:
            cfg.hotword.models = [m.strip() for m in models_env.split(",")]
        cfg.hotword.threshold = float(os.getenv("WAKEWORD_CONFIDENCE", str(cfg.hotword.threshold)))
        cfg.hotword.persistence = int(
            os.getenv("WAKEWORD_PERSISTENCE", str(cfg.hotword.persistence))
        )
        cfg.hotword.cooldown_seconds = float(
            os.getenv("WAKEWORD_COOLDOWN_SECONDS", str(cfg.hotword.cooldown_seconds))
        )
        # Per-model overrides: "ola_fantasma:0.70,hey_fantasma:0.50".
        # Malformed entries are ignored rather than fatal, so one typo cannot
        # take the whole assistant down; the global threshold still applies.
        # But they are REPORTED: a silent fallback means an operator who wrote
        # "ola_fantasma=0.70" instead of "ola_fantasma:0.70" gets the global
        # threshold and no clue why. That is the AUDIO_AUTO_DETECT fail-open
        # class, and it is not acceptable in a wake-word path.
        per_model: dict = {}
        for pair in os.getenv("WAKEWORD_CONFIDENCE_PER_MODEL", "").split(","):
            pair = pair.strip()
            if not pair:
                continue
            if ":" not in pair:
                print(
                    f"WARNING: ignoring malformed WAKEWORD_CONFIDENCE_PER_MODEL "
                    f"entry {pair!r} -- expected 'name:0.70,name2:0.50' "
                    f"(colon, not equals). Falling back to the global threshold "
                    f"{cfg.hotword.threshold} for that model.",
                    file=sys.stderr,
                )
                continue
            name, _, value = pair.partition(":")
            try:
                per_model[name.strip()] = float(value)
            except ValueError:
                print(
                    f"WARNING: ignoring WAKEWORD_CONFIDENCE_PER_MODEL entry "
                    f"{pair!r} -- {value!r} is not a number. Falling back to the "
                    f"global threshold {cfg.hotword.threshold} for that model.",
                    file=sys.stderr,
                )
                continue
        cfg.hotword.thresholds_per_model = per_model

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
        cfg.llm.model_fallback = os.getenv("OLLAMA_MODEL_FALLBACK", cfg.llm.model_fallback)
        cfg.llm.timeout = int(os.getenv("OLLAMA_TIMEOUT", str(cfg.llm.timeout)))
        cfg.llm.connect_timeout = int(
            os.getenv("OLLAMA_CONNECT_TIMEOUT", str(cfg.llm.connect_timeout))
        )
        cfg.llm.context_size = int(os.getenv("OLLAMA_CONTEXT_SIZE", str(cfg.llm.context_size)))
        cfg.llm.threads = int(os.getenv("OLLAMA_THREADS", str(cfg.llm.threads)))

        # TTS
        cfg.tts.voice_model_path = os.getenv("TTS_MODEL_PATH", cfg.tts.voice_model_path)
        cfg.tts.use_sox_effects = os.getenv("USE_SOX_EFFECTS", "true").lower() == "true"

        # Audio feedback
        cfg.audio_feedback.enabled = os.getenv("AUDIO_FEEDBACK_ENABLED", "true").lower() == "true"
        cfg.audio_feedback.music_dir = os.getenv("MUSIC_DIR", cfg.audio_feedback.music_dir)
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
        cfg.chacon_plug_ip = os.getenv("CHACON_PLUG_IP", cfg.chacon_plug_ip)
        cfg.chacon_plug_name = os.getenv("CHACON_PLUG_NAME", cfg.chacon_plug_name)
        cfg.chacon_plug_port = int(os.getenv("CHACON_PLUG_PORT", cfg.chacon_plug_port))

        # Tapo
        cfg.tapo_user = os.getenv("TAPO_USER", "")
        cfg.tapo_pass = os.getenv("TAPO_PASS", "")
        tapo_cams_json = os.getenv("TAPO_CAMERAS_JSON")
        if tapo_cams_json:
            import json

            cfg.tapo_cameras = json.loads(tapo_cams_json)

        # Vision
        cfg.ollama_vision_model = os.getenv("OLLAMA_VISION_MODEL", cfg.ollama_vision_model)

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
        cfg.whisper_initial_prompt = os.getenv("WHISPER_INITIAL_PROMPT", cfg.whisper_initial_prompt)

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

        # System prompt.
        #
        # This used to be a 780-char triple-quoted string embedded here, with a
        # DUPLICATE in prod's .env. That duplicate was dead: python-dotenv cannot
        # parse multi-line values, so it silently discarded all 12 lines and the
        # code default always won. Editing the .env copy did nothing and looked
        # like the setting "not working" -- the same fail-open class as
        # AUDIO_AUTO_DETECT.
        #
        # It now lives in prompts/system.txt: versioned, diffable, reviewable in a
        # PR. A system prompt is product content, not host configuration, so it
        # must NOT live in .env -- .env is untracked, so a persona change there is
        # unreviewable and unrecoverable.
        #
        # Precedence: SYSTEM_PROMPT (inline, rare) > SYSTEM_PROMPT_FILE (a host
        # pointing at a different file) > prompts/system.txt beside this module.
        _prompt_file = os.getenv(
            "SYSTEM_PROMPT_FILE",
            str(Path(__file__).parent / "prompts" / "system.txt"),
        )
        _inline = os.getenv("SYSTEM_PROMPT", "").strip()
        if _inline:
            cfg.llm.system_prompt = _inline
        elif os.path.exists(_prompt_file):
            cfg.llm.system_prompt = Path(_prompt_file).read_text(encoding="utf-8").strip()
        else:
            raise FileNotFoundError(
                f"system prompt not found at {_prompt_file} and SYSTEM_PROMPT is "
                f"unset. The persona is load-bearing: refusing to start beats "
                f"serving an empty or wrong prompt."
            )
        # A multi-line SYSTEM_PROMPT="""...""" block in .env is discarded by
        # python-dotenv without failing. Warn rather than let it rot again.
        try:
            _env_raw = (Path(base) / ".env").read_text(encoding="utf-8")
        except OSError:
            _env_raw = ""
        if re.search(r'^\s*SYSTEM_PROMPT\s*=\s*"""', _env_raw, re.M):
            print(
                f"WARNING: using {_prompt_file}, but .env still holds a "
                f'multi-line SYSTEM_PROMPT="""...""" block. python-dotenv cannot '
                f"parse it; it is being IGNORED. Delete it and edit "
                f"prompts/system.txt instead.",
                file=sys.stderr,
            )

        return cfg


# Global config instance - loaded on import
config = Config.from_env()

# Backward compatibility exports (for existing code)
BASE_DIR = Path(__file__).parent
DB_PATH = (
    BASE_DIR / config.memory_db_path if config.memory_db_path else BASE_DIR / "data" / "memory.db"
)
MEMORY_DB_PATH = config.memory_db_path
BRAIN_DB_PATH = config.brain_db_path
CONFIG_DB_PATH = config.config_db_path
TTS_MODEL_PATH = BASE_DIR / config.tts.voice_model_path
SKILLS_DIR = config.skills_dir
TTS_CACHE_DIR = config.tts_cache_dir
CACHE_DIR = config.cache_dir
PUBLIC_DIR = config.public_dir

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
CHACON_PLUG_IP = config.chacon_plug_ip
CHACON_PLUG_NAME = config.chacon_plug_name
CHACON_PLUG_PORT = config.chacon_plug_port
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
    "CHACON_PLUG_IP",
    "CHACON_PLUG_NAME",
    "CHACON_PLUG_PORT",
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
