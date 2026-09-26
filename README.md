# pHantasma

Local-first, offline, modular voice assistant built in Python — private by design, running entirely on your own hardware without third-party cloud dependencies (except optional web search via self-hosted SearxNG).

## Quickstart

```bash
# 1. Install system dependencies
sudo apt-get install -y python3.11 python3.11-venv sox mpg123 libopenblas-dev portaudio19-dev

# 2. Install Ollama and pull model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3:8b-instruct-8k

# 3. Install Piper TTS voice
mkdir -p voices
wget -O voices/pt_PT-nicolau-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_PT/nicolau/medium/pt_PT-nicolau-medium.onnx
wget -O voices/pt_PT-nicolau-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_PT/nicolau/medium/pt_PT-nicolau-medium.onnx.json

# 4. Setup project
python3.11 -m venv venv
source venv/bin/activate
pip install -e .[dev]

# 5. Configure audio devices (optional - auto-detects)
# cp config.example.py config.local.py  # edit device_in/device_out if needed

# 6. Run
make run
# or: python -m src.main
```

## Architecture

```
┌─────────────┐     ┌──────────┐     ┌────────┐     ┌──────────┐     ┌─────┐
│  Microphone │────▶│  VAD     │────▶│ Hotword│────▶│  Whisper │────▶│ LLM │
│  (sounddev) │     │ (webrtc) │     │ (oww)  │     │ (STT)    │     │(Ollama)│
└─────────────┘     └──────────┘     └────────┘     └──────────┘     └─────┘
                                                                     │
                                                                     ▼
┌─────────────┐     ┌──────────┐     ┌────────┐     ┌──────────┐     ┌─────┐
│  Speaker    │◀────│ Audio    │◀────│ Piper  │◀────│  TTS     │◀────│      │
│  (sounddev) │     │ Playback │     │ (TTS)  │     │ (synth)  │     │      │
└─────────────┘     └──────────┘     └────────┘     └──────────┘     └─────┘
```

**Pipeline stages:**
1. **AudioCapture** — sounddevice input stream, callback feeds queue
2. **VADProcessor** — WebRTC VAD filters non-speech frames
3. **HotwordDetector** — openWakeWord detects "Hey Jarvis"/"Alexa"/etc.
4. **WhisperSTT** — Transcribes speech to text (cached model)
5. **OllamaLLM** — Generates response via local LLM
5. **PiperTTS** — Synthesizes speech with sox effects
6. **AudioPlayback** — Plays response via sounddevice

## Configuration

All runtime configuration in `config.py` with environment variable overrides:

| Env Var | Config Path | Description |
|---------|-------------|-------------|
| `PHANTASMA_DEVICE_IN` | `audio.device_in` | ALSA input device (e.g., "hw:1,0") |
| `PHANTASMA_DEVICE_OUT` | `audio.device_out` | ALSA output device |
| `PHANTASMA_VAD_AGGRESSIVENESS` | `vad.aggressiveness` | 0-3 (default 2) |
| `PHANTASMA_HOTWORD_THRESHOLD` | `hotword.threshold` | 0.0-1.0 (default 0.5) |
| `PHANTASMA_WHISPER_MODEL` | `stt.model_size` | tiny/base/small/medium/large |
| `PHANTASMA_OLLAMA_HOST` | `llm.host` | Ollama URL (default http://127.0.0.1:11434) |
| `PHANTASMA_OLLAMA_MODEL` | `llm.model` | Model name (default llama3:8b-instruct-8k) |
| `PHANTASMA_PIPER_VOICE` | `tts.voice_model_path` | Path to .onnx voice model |
| `PHANTASMA_QUEUE_SIZE` | `pipeline.queue_maxsize` | Audio queue size |

## Features Implemented (branch `testing`)

### Admin Interface (`/admin/brain`)
- **Unified brain view** — Memory, RAG, FlyBrain, 3D explorer on one screen (`/admin/brain`)
- **GMIF Graph Classification** — M1–M5 epistemic levels (M3 = logical implication)
- **Sleep/Dream Button** — One-click sleep/dream cycle (`/admin/brain/sleep`)
  - Analyzes GMIF-classified graph for gaps (weak edges M1/M2, disconnected pairs, missing requirements, causal gaps)
  - Performs targeted SearxNG research per gap type
  - Stores synthesized insights via `save_to_rag()`
- **Admin Burger** — 6 links (Cérebro, Dashboard, Configuração, Utilizadores, .env, Sair)
- **Hamburger 44×44 px** responsive < 900px

### Voice UI (`/`)
- **Skill-based UI** served at `/` with design system shared from admin
- **Hamburger menu** with admin links (Cérebro, Dashboard, Config, Users, .env, Logout)
- **Device sensors** — real temperature/humidity/power, no fake "ON" fallback
- **Weather widget** — live IPMA/Open-Meteo via `skill_weather` daemon

### Wake Words
- `olá fantasma` (TTS trigger, score ~0.81)
- `hey fantasma` (score ~0.63, below persistence threshold)
- Config: `WAKEWORD_CONFIDENCE=0.50`, `WAKEWORD_PERSISTENCE=2`

### Sensors & Devices
- **Tuya sensors** — declared DPS mapping (`SENSOR_TEMP_MAP`), validity 5–45°C
- **Real readings**: `24.5° · 30m`, `25.3° · 1h`, `0 ppm`, `sem leitura · 17h`
- No fake "ON" fallback; shows `age_s`, `stale` flag

### Weather
- `skill_weather` daemon populates `weather_cache.json` every 30 min
- IPMA forecast + Open-Meteo AQI + moon phase
- UI shows stale indicator in tooltip

### GMIF Graph Memory
- **Schema**: `memory_graph` extended with GMIF columns (`logical_form`, `validation_type`, `extraction_confidence`, `validation_confidence`, `source_chunks`, `gmif_level`, `node_gmif_type`, `node_gmif_confidence`, `node_gmif_evidence`)
- **Classifier** (`src/pipeline/gmif_classifier.py`): M1–M5 levels, logical/external/human validation
- **GMIF Dream** (`skills/skill_gmif_dream.py`) — runs after standard dream (03:00)
  - Analyzes weak edges (M1/M2 promotable to M3/M4)
  - Finds disconnected semantic pairs
  - Detects missing requirements and causal gaps
  - Targeted SearxNG research per gap type, LLM synthesis → `save_to_rag()`

### Wake Word Feedback
- `++` / `--` exact triggers → DAN+/DAN- in FlyBrain
- Immediate FlyBrain `persist()` flush

### Admin Features
- **Dashboard** — stats, memory graph, FlyBrain state
- **Memory** — live `brain.db` sample with graph
- **RAG** — retrievable chunks with tags/facts
- **FlyBrain Manager** — reinforcement parameters (α, γ, ε, steps)
- **Users** — role-based (admin/user), bcrypt passwords
- **Config/Env** — YAML/ENV editor with validation

### Quality Gates
- **Python**: 80/80 tests passing
- **Node (mermaid)**: 26/26 tests passing
- **Chromium (explorer)**: 36/36 tests passing
- **0 tracebacks** in service
- Ruff lint + format, MyPy typecheck, pytest with mutation tests

## Skills System

Skills are dynamic Python modules in `skills/` directory. Each skill defines:
- `TRIGGERS`: list of keyword patterns
- `TRIGGER_TYPE`: "contains" | "exact" | "regex"
- `handle(text, context)`: function returning response text
- Optional: `init_skill_daemon()` for background tasks

## REST API

Flask server at port 5000:

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/info` | Server capabilities |
| `POST /api/command` | Execute voice/text command |
| `POST /api/stt` | Speech-to-text |
| `POST /api/tts` | Text-to-speech |
| `GET /api/devices` | List configured devices |
| `POST /api/devices/<name>/control` | Control device |
| `GET/POST /api/memory` | Long-term memory |
| `GET /api/weather` | Weather widget data |
| `GET /api/memory/graph` | Explorer payload |
| `POST /admin/brain/sleep` | Trigger sleep/dream cycle |

## Systemd Service

```bash
sudo cp phantasma.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now phantasma
journalctl -u phantasma -f
```

## Development

```bash
make setup      # Create venv, install deps
make test       # Run tests with coverage
make lint       # Ruff lint
make format     # Ruff format
make check      # All quality gates
make typecheck  # MyPy type checking
```

## Project Structure

```
pHantasma/
├── assistant.py          # Main pipeline orchestration
├── config.py             # Configuration (validated on startup)
├── src/
│   ├── main.py           # Entry point
│   ├── api/              # Flask REST API (admin, routes)
│   │   ├── admin.py      # Admin routes (brain, memory, rag, flybrain, users, config)
│   │   ├── routes.py     # Public API routes (command, stt, tts, devices, weather)
│   │   └── design.py     # Design system CSS/JS
│   └── pipeline/         # Voice pipeline stages
│       ├── audio.py      # Audio I/O, VAD, hotword, GMIF classifier
│       ├── stt.py        # Whisper STT
│       ├── llm.py        # Ollama LLM
│       ├── tts.py        # Piper TTS
│       └── utils.py      # Result, logging, timing
├── tests/                # Unit + integration tests (mocked + real)
├── skills/               # Skill modules (dynamic loading)
│   ├── skill_ui.py       # Voice UI at /
│   ├── skill_dream.py    # Standard dream (02:30)
│   ├── skill_gmif_dream.py # GMIF dream (03:00)
│   ├── skill_feedback.py # ++/-- feedback
│   ├── skill_weather.py  # Weather daemon
│   └── skill_*.py        # Device/skill modules
├── android/              # Android companion app (Kotlin/Compose)
├── aes/                  # AES project tracking
├── docs/                 # Project documentation
└── Makefile              # Quality gates and commands
```

## Android Companion App

The `android/` directory contains a complete Kotlin/Jetpack Compose app implementing:
- mDNS discovery (`_phantasma._http._tcp.`)
- REST API client (Ktor + Kotlinx Serialization) for all endpoints
- Voice interaction: 16kHz PCM recording → Base64 → `/api/command` → Base64 TTS playback
- Material 3 dark theme (pHantasma green), 4 screens: Home, Devices, Memory, Settings
- Device dashboard with toggle/control actions
- Memory viewer with add/delete
- Settings: manual IP override, theme, auto-discover toggle

**Build & Test (requires Android SDK):**
```bash
cd android
./gradlew assembleDebug          # Build debug APK
./gradlew test                   # Run unit tests
# APK at: app/build/outputs/apk/debug/app-debug.apk
```

## Privacy

- **Zero cloud calls** for core operation
- **All processing local**: hotword, STT, LLM, TTS
- **Optional**: SearxNG web search (self-hosted)
- **No telemetry**, no accounts, no API keys required

## Branch Status

| Branch | Status |
|--------|--------|
| `master` | Stable production |
| `testing` | Latest features (GMIF dream, unified brain, sleep button, GMIF classifier, sleep endpoint) |

## License

MIT
