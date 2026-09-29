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
| `CHACON_PLUG_IP` | `chacon_plug_ip` | Local IP of the Chacon balcony plug (default 10.0.0.116). Non-empty ⇒ the device gets a tile in `/`. |
| `CHACON_PLUG_NAME` | `chacon_plug_name` | Nickname shown on the tile (default "luz do balcão") |
| `CHACON_PLUG_PORT` | `chacon_plug_port` | UDP port (default 18530) |

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
- **Device tiles** grouped by room (`Geral`, `WC`, `Sala`, `Quarto`,
  `Entrada`) with a per-name icon; a click sends a natural-language command
  through the same pipeline as a voice request. Devices are listed by
  `/get_devices`, and their state by `/device_status`
- **Weather widget** — live IPMA/Open-Meteo via `skill_weather` daemon
- **Pipeline resilience** — `src/pipeline/noise.py` tracks the ambient noise
  floor and `src/pipeline/quiet.py` gates replies; the LLM call gets a bounded
  **connect** timeout (a dead Ollama host used to block with no bound at all
  and stalled every answer) while keeping the generous read budget

### Wake Words
- `olá fantasma` (TTS trigger, score ~0.81)
- `hey fantasma` (score ~0.63, below persistence threshold)
- Config: `WAKEWORD_CONFIDENCE=0.50`, `WAKEWORD_PERSISTENCE=2`

### Sensors & Devices
- **Tuya sensors** — declared DPS mapping (`SENSOR_TEMP_MAP`), validity 5–45°C
- **Real readings**: `24.5° · 30m`, `25.3° · 1h`, `0 ppm`, `sem leitura · 17h`
- No fake "ON" fallback; shows `age_s`, `stale` flag

### Chacon Balcony Light (`skill_chacon_udp`)

The balcony plug is a Hi-Flying **HF-LPB100** (firmware V1.0.08) and is
controlled **directly over the LAN**, with no vendor account. The previous
path went through the DIO/Chacon cloud, whose account is dead (see
`aes/tickets/T036`, `T051`); `skill_chacon` now fails with a clear message
instead of pretending to work.

- **Device**: MAC `F0:FE:6B:57:E7:5A`, UDP port **18530**, commands in English
- **No AES key needed.** The plug's local key is random, generated at DIO
  pairing, so encrypted packets are silently dropped. The firmware accepts
  **plaintext** packets (header flag `bEncrypt` cleared) whose body header
  matches the device constants, and it answers them. The skill keeps a UDP
  socket bound to `:18530` because the plug replies on that **fixed** port,
  not to the sender's ephemeral one.
- **Packet** (25 bytes, validated live): open header `pv=0x01`, flag `0x04`,
  MAC, `dataLen=0x10`; body `reserved=0x00`, `sn=0xFFFF`, `deviceType=0xDF`,
  `factoryCode=0xF1`, `license=0x21B4`, `cmd`, `arg`, pad. `cmd 0x01`
  (SET_GPIO_STATUS) with `arg 0xFFFF` = on, `0x00FF` = off.
- **Triggered** by the balcony nicknames; `PRIORITY=60` so it wins over
  `skill_tasmota` (50), which cannot drive this firmware.
- **UI tile** in `/`: `luz do balcão` is listed by `/get_devices` and grouped
  under **Sala** with the bulb icon. Clicking it routes through the same
  natural-language pipeline as a voice command.
- **State is reported as `unknown`, never guessed.** The plug accepts
  `GET_GPIO_STATUS` (0x02) but encrypts its reply with the per-device key, so
  there is nothing to read back; `/device_status` therefore answers
  `{"state": "unknown", "readable": false}` — reachable, but honest about not
  knowing whether the relay is closed. A fabricated on/off would be worse
  than admitted ignorance.

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
- **Config/Env** — YAML/ENV editor with validation. `update_config()` inserts
  the row when the key is not in the config table yet, so a setting that was
  never persisted can still be set from the UI and read back; categories come
  from `CONFIG_CONTROLS` and each value is normalised per key.

### Quality Gates
- **Python**: 641 passed, 1 skipped (`pytest`, full suite)
  - 17 `tests/test_hotword.py` errors are **pre-existing** `onnxruntime`
    model-loading failures, unrelated to the code changes; proven by
    re-running them with the changes stashed
- **Node (mermaid)**: 26/26 tests passing
- **Chromium (explorer)**: 36/36 tests passing
- **0 tracebacks** in service
- Ruff lint + format, MyPy typecheck, pytest
- Pre-commit hook runs the gates on every commit (`.aes/hooks/pre-commit.sh`)

## Skills System

Skills are dynamic Python modules in `skills/` directory. Two forms are
supported and both are loaded by `skills/loader.py`:

- **Class-based** (preferred) — subclass `skills.base.Skill` with `NAME`,
  `TRIGGERS`, `TRIGGER_TYPE`, `PRIORITY` and `handle()`. A higher `PRIORITY`
  wins when several skills match the same text, which is how
  `skill_chacon_udp` (60) takes the balcony nicknames from `skill_tasmota`
  (50). `get_status_for_device()` here is what feeds the `/device_status`
  endpoint behind each UI tile.
- **Legacy module-level** — a module exposing `TRIGGERS` and
  `handle(text, context)`, wrapped by the loader in a `LegacySkillAdapter`.

`TRIGGER_TYPE` is `"contains" | "exact" | "regex"`. Skills may also define
`init_skill_daemon()` for background tasks.

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
| `GET /get_devices` | Device list for the `/` UI (toggles + sensors) |
| `GET /device_status?nickname=` | State behind one UI tile |
| `POST /device_action` | Toggle a device by nickname (natural language) |
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
│       ├── noise.py      # Ambient noise-floor tracking
│       ├── quiet.py      # Reply gating (stay quiet when not needed)
│       ├── stt.py        # Whisper STT
│       ├── llm.py        # Ollama LLM
│       ├── tts.py        # Piper TTS
│       └── utils.py      # Result, logging, timing
├── tests/                # Unit + integration tests (mocked + real)
├── skills/               # Skill modules (dynamic loading)
│   ├── skill_ui.py       # Voice UI at /
│   ├── skill_chacon_udp.py # Chacon balcony light over local UDP
│   ├── skill_tasmota.py  # Tasmota HTTP device path (PRIORITY 50)
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
| `main` | Production (mirrors `origin/main`; formerly `master`) |
| `testing` | Latest features, merged into `main` (GMIF dream, unified brain, sleep button, local Chacon control, admin config editor) |

Recent work on `testing` (all in `main`):
- `feat(skill): control Chacon balcony plug over local UDP (no cloud/AES)` — voice control
- `feat(ui): show the Chacon balcony light in / with a Sala tile and honest state`
- `chore: land in-progress work from parallel sessions` — admin config editor, noise/quiet pipeline, LLM connect timeout, Tasmota path, FlyBrain/GMIF fixes

See `aes/tickets/` for the tracked work items (`T051` Chacon, `T053` Tasmota reflash).

## License

MIT
