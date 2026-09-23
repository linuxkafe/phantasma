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
6. **PiperTTS** — Synthesizes speech with sox effects
7. **AudioPlayback** — Plays response via sounddevice

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

## Skills System

Skills are dynamic Python modules in `skills/` directory. Each skill defines:
- `TRIGGERS`: list of keyword patterns
- `handle(text, context)`: function returning response text

Example `skills/skill_time.py`:
```python
TRIGGERS = ["hora", "que horas", "what time"]

def handle(text, context):
    from datetime import datetime
    return f"São {datetime.now().strftime('%H:%M')}"
```

## REST API

Flask server for Android app and CLI:
```bash
# Start API server
python -m src.api.routes

# Send command
curl -X POST http://localhost:5000/api/command \
  -H "Content-Type: application/json" \
  -d '{"text": "que horas são", "type": "text"}'
```

Endpoints:
- `GET /health` — Health check
- `GET /api/info` — Server capabilities
- `POST /api/command` — Execute voice/text command
- `POST /api/stt` — Speech-to-text
- `POST /api/tts` — Text-to-speech
- `GET /api/devices` — List configured devices
- `POST /api/devices/<name>/control` — Control device
- `GET/POST /api/memory` — Long-term memory

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
│   ├── api/              # Flask REST API
│   └── pipeline/         # Voice pipeline stages
│       ├── audio.py      # Audio I/O, VAD, hotword
│       ├── stt.py        # Whisper STT
│       ├── llm.py        # Ollama LLM
│       ├── tts.py        # Piper TTS
│       └── utils.py      # Result, logging, timing
├── tests/                # Unit tests (mocked)
├── skills/               # Skill modules (dynamic loading)
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
# Install Android SDK (Android Studio or command line tools)
# Accept licenses: sdkmanager --licenses

cd android
./gradlew assembleDebug          # Build debug APK
./gradlew test                   # Run unit tests
# APK at: app/build/outputs/apk/debug/app-debug.apk
```

**Integration Test:**
```bash
# Terminal 1: Start pHantasma server with mDNS advertisement
make run

# Terminal 2: Install APK on emulator/device
adb install android/app/build/outputs/apk/debug/app-debug.apk

# Verify: discovery → voice command → device control → memory flow
```

See `aes/tickets/T011-android-build-test.md` for detailed acceptance criteria and known risks.

## Privacy

- **Zero cloud calls** for core operation
- **All processing local**: hotword, STT, LLM, TTS
- **Optional**: SearxNG web search (self-hosted)
- **No telemetry**, no accounts, no API keys required

## License

MIT