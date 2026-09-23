# Roadmap

## [HIGH] Project Initialisation & AES Setup
- Impact: High
- Effort: Low
- Status: done

## [HIGH] Core Voice Pipeline Integration
- Impact: High
- Effort: High
- Status: done
- Description: Integrate openWakeWord → Whisper → Ollama → Piper pipeline in assistant.py with VAD gating
- Completed: 2026-09-17 (commit eb41968)

## [HIGH] Skills System Implementation
- Impact: High
- Effort: Medium
- Status: todo
- Description: Dynamic skill loader; base skill classes; example skills (calc, weather, music, memory)

## [HIGH] Long-term Memory (SQLite RAG)
- Impact: High
- Effort: Medium
- Status: todo
- Description: Skill for "memoriza isto..." / "o que memorizei?" with SQLite storage and retrieval

## [HIGH] SearxNG Web Search RAG
- Impact: High
- Effort: Medium
- Status: todo
- Description: Skill to enrich Ollama responses with real-time search via local SearxNG

## [HIGH] Tuya Local Control Skill
- Impact: High
- Effort: Medium
- Status: todo
- Description: skill_tuya.py with DPS mapping (1=switch, 20=light); DHCP reservation required

## [HIGH] Xiaomi Local Control Skill
- Impact: High
- Effort: Medium
- Status: todo
- Description: skill_xiaomi.py for Viomi vacuum and Yeelight via python-miio

## [MEDIUM] REST API & CLI
- Impact: Medium
- Effort: Low
- Status: done
- Description: Flask /api/command endpoint; phantasma-cli.sh wrapper
- Completed: 2026-09-17 (src/api/ implemented)

## [MEDIUM] Audio Feedback System
- Impact: Medium
- Effort: Low
- Status: todo
- Description: Random music snippet + greeting on hotword using mpg123

## [MEDIUM] Systemd Service & Deployment
- Impact: Medium
- Effort: Low
- Status: todo
- Description: phantasma.service with venv PATH, Nice=19, auto-restart

## [LOW] Config Management & Validation
- Impact: Low
- Effort: Low
- Status: todo
- Description: config.py schema validation; device discovery helpers

## [LOW] Custom Hotword Training
- Impact: Low
- Effort: High
- Status: backlog
- Description: Train "Ei Fantasma" custom onnx model via Google Colab / local training

## [LOW] Tuya UDP Daemon
- Impact: Low
- Effort: Medium
- Status: backlog
- Description: Background daemon to collect Tuya device state via UDP for sensors

## [LOW] Test Coverage & CI
- Impact: Low
- Effort: Medium
- Status: backlog
- Description: pytest suite; GitHub Actions CI; make check quality gates

## [MEDIUM] Migrate off webrtcvad (drop setuptools<81 pin)
- Impact: Medium
- Effort: Medium
- Status: backlog
- Description: [DISCOVERED mid-task T031] Replace webrtcvad 2.0.10 (imports pkg_resources; setuptools>=81 removed it) with a pkg_resources-free VAD binding, then lift the `setuptools>=61,<81` pin in pyproject.toml and Dockerfile. setuptools<81 is EOL — this is a stopgap.

## [MEDIUM] Compose healthcheck mismatch in assistant mode
- Impact: Medium
- Effort: Low
- Status: backlog
- Description: [DISCOVERED mid-task T031] docker-compose.yml healthcheck curls :5000/health, which is only served in `api` mode; assistant mode container shows `unhealthy` while the pipeline runs fine. Fix: make assistant mode expose health, or change healthcheck.