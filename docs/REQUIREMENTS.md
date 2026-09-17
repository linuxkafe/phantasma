# Requirements

## Functional

- **F-01 Hotword Detection**: Detect "Olá Fantasma" / "Hey Fantasma" / "Hey Jarvis" / "Alexa" / "Hey Mycroft" / "Hey Rhasspy" offline using openWakeWord
- **F-02 Speech-to-Text**: Transcribe Portuguese/English audio to text using Whisper (medium model)
- **F-03 Language Understanding**: Process transcribed text with local Ollama (Llama3 8K context) using system prompt for Phantasma personality
- **F-04 Text-to-Speech**: Generate robotic voice using Piper TTS with sox effects
- **F-05 Skill System**: Dynamically load skills from `skills/` directory; each skill defines TRIGGERS and handle() function
- **F-06 Long-term Memory**: Store/retrieve facts via "memoriza isto..." commands in SQLite
- **F-07 Web Search RAG**: Enrich LLM responses with real-time search via self-hosted SearxNG
- **F-08 Audio Feedback**: Play random music snippet + greeting on hotword detection
- **F-09 REST API**: Flask endpoint for CLI/external control (`/api/command`)
- **F-10 CLI Interface**: `phantasma-cli.sh` for sending commands via API
- **F-11 Tuya Local Control**: Control Tuya/SmartLife devices (switches DPS 1, lights DPS 20, sensors v3.1) via tinytuya
- **F-12 Xiaomi Local Control**: Control Mi Home devices (vacuum, Yeelight) via python-miio
- **F-13 Systemd Service**: Run as background service with proper PATH, Nice=19, IOSchedulingClass=idle
- **F-14 VAD Gate**: WebRTC VAD pre-filter to prevent false hotword triggers

## Non-Functional

- **Performance**: Hotword → response < 3s on HP Mini G4 (16GB RAM); hotword detection < 500ms
- **Privacy**: Zero mandatory cloud calls; all processing local; optional SearxNG is self-hosted
- **Reliability**: Service auto-restarts on failure (Restart=on-failure, RestartSec=5)
- **Maintainability**: Skills as independent modules; config in config.py; no hardcoded secrets
- **Observability**: Structured logging to journald; `journalctl -u phantasma -f` for debugging
- **Portability**: Python 3.11+; venv isolation; systemd service template provided

## Constraints

- Language: Python 3.11+ (pyenv managed)
- Deployment: systemd service on Linux (Ubuntu/Debian target)
- Audio: ALSA devices configured via config.py (Jabra SPEAK 410 tested)
- Dependencies: openwakeword, whisper, ollama, piper, sox, mpg123, tinytuya, python-miio, flask, webrtcvad
- Hardware: Minimum 8GB RAM (16GB recommended for Whisper medium + Ollama)
- Network: Local LAN only; Ollama and SearxNG on localhost/127.0.0.1