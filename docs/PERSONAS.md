# Personas

## User

**Profile:** Privacy-conscious technical user (developer, homelab enthusiast, privacy advocate) who wants a voice assistant that never sends audio to the cloud. Runs on personal hardware (mini PC, Raspberry Pi, home server). Comfortable with Linux, systemd, Python venvs, Docker for SearxNG/Ollama.

**Goals:**
- Voice control of smart home devices (Tuya, Xiaomi) 100% locally
- Ask questions and get answers from local LLM + optional web search
- Memorise personal facts ("my cat is named Bimby") and recall them later
- No API keys, no cloud accounts, no subscription fees
- Hackable: add new skills by dropping a Python file in skills/

**Pain Points:**
- Existing open-source assistants (Mycroft, Rhasspy) are unmaintained or complex
- Commercial assistants require internet and harvest data
- Hotword engines require paid licenses (Porcupine) or cloud APIs
- IoT integrations often require cloud bridges (Tuya Cloud, Mi Cloud)

## Maintainer

**Profile:** Engineer maintaining this codebase — likely the same as the user (solo project)

**Goals:**
- Keep code clean, documented, and modular
- Ensure quality without friction (make check passes)
- Skills system stays simple: one file = one skill
- Config stays in config.py — no scattered constants

**Tools:**
- AES (Ambrósio Engineering System) for structured development
- `make check` for validation (pytest, ruff, format)
- `journalctl -u phantasma -f` for runtime debugging
- GitHub Actions CI for automated testing