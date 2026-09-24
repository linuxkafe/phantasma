---
project: pHantasma
created: 2026-09-17
current_sprint: sprint-01
current_ticket: T002
---

# pHantasma Kanban

## Backlog
| ID | Title | Priority | Sprint |
|----|-------|----------|--------|

## Sprint 01 — Core Voice Pipeline & Skills System
| ID | Title | Status |
|----|-------|--------|
| T002 | Skills system implementation (dynamic loader, base classes) | pending |
| T003 | Long-term memory skill (SQLite RAG) | pending |
| T004 | SearxNG web search RAG skill | pending |
| T005 | Tuya local control skill | pending |
| T006 | Xiaomi local control skill | pending |
| T008 | Audio feedback system (music snippet + greeting) | pending |
| T009 | Systemd service & deployment config | pending |
| T010 | Config management & validation | pending |
| T031 | Fix Docker startup crash (pkg_resources / setuptools>=81) | done |
| T032 | Container audio access verification + setup script | done |
| T033 | docker-audio-setup audit: cwd bug, busy-probe, Whisper download loop | done |

## Sprint 02 — Android Companion App Build & Integration
| ID | Title | Status |
|----|-------|--------|
| T011 | Android app build, test & server integration | pending |
| T012 | Android app: Room DB offline caching | pending |
| T013 | Android app: WebSocket streaming for real-time voice | pending |
| T014 | Android app: Push notifications (foreground service) | pending |

## Sprint 03 — dfb FlyBrain Integration
| ID | Title | Status |
|----|-------|--------|
| T016 | FlyBrain core service (E-PG + Mushroom Body + Octopamine) | done |
| T017 | Semantic encoder skill (text → topic angle + habituation) | done |
| T018 | Neuro→Ollama adapter (connectome state → LLM params) | done |
| T019 | Feedback skill (++/-- reward/punishment) | done |
| T020 | Telemetry logging (structured JSONL + console) | pending |
| T021 | FlyBrain state persistence (SQLite) | done |
| T022 | Integration tests (5-turn episodes from dfb) | pending |
| T023 | Configuration for FlyBrain (thresholds, tau, gains) | pending |
| T030 | Voice-based feedback (post-response keyword detection) | done |

## Sprint 04 — Production Hardening
| ID | Title | Status |
|----|-------|--------|
| T024 | Async pipeline (STT/LLM/TTS parallel) | pending |
| T025 | Circuit breaker for Ollama | pending |
| T026 | Health endpoint + Prometheus metrics | pending |
| T027 | Audio device auto-discovery CLI | pending |
| T028 | Piper model auto-download in setup | pending |

## Done
| ID | Title | Completed |
|----|-------|-----------|
| T001 | Core voice pipeline integration (openWakeWord → Whisper → Ollama → Piper) | 2026-09-17 |
| T007 | REST API & CLI interface | 2026-09-17 |
| T011-android | Android companion app source code (Kotlin/Compose) | 2026-09-17 |
| T015 | AES Peer Review: pHantasma codebase & dfb integration analysis | 2026-09-21 |
| T019 | Feedback skill (++/-- reward/punishment) | 2026-09-21 |
| T021 | FlyBrain state persistence (SQLite) | 2026-09-21 |
| T030 | Voice-based feedback (post-response keyword detection) | 2026-09-21 |
| T031 | Docker startup crash fix (pkg_resources / setuptools>=81 pin) | 2026-09-23 |
| T032 | Container audio access verification + setup script | 2026-09-23 |
| T033 | docker-audio-setup audit: cwd bug, busy-probe, Whisper download loop | 2026-09-23 |
| T034 | PT wake words load+pre-flight, Ollama fallback, legacy skill bridge (weather/discord restore) | 2026-09-23 |

## Backlog (follow-ups)
| ID | Title | Status |
|----|-------|--------|
| T035 | Discord live bot: assistant-mode Flask API + `/comando` payload alignment (gap found in T034) | done |
| T036 | Checkpoint: migrate `Skill` subclass contract incrementally (legacy adapter currently bridges) | pending |
| T037 | Skill "o que ouves" (what_you_hear): microfone → Whisper → transcrição, resposta via TTS quando invocada por voz | done |
| T038 | Live-daemon PortAudio input blindness: throwaway image captures rms=47 but live app input stream fails -9998 (query_devices in live shows max_input_channels=0 on all devices) | done |
| T039 | Design: base de dados única com grafos (memory.db + flybrain.db + claims) | pending |
| T040 | Feedback por texto (Discord/REST) via helper partilhado `_apply_feedback_reward` (janela voz intacta) | done |
| T041 | Persistência configurável `PHANTASMA_DATA_DIR` no docker-compose (default `./data`) | done |
| T042 | Discord thumbs-down (👎) silenciosamente ignorado: sem reward FlyBrain nem ack (2 guards: emoji não mapeado + author != bot) | done |

## Learning History
| Date | Ticket | Insight |
|------|--------|---------|
| 2026-09-21 | T030 | Voice feedback UX: natural language keywords in post-response window beat explicit ++/-- for voice; hash-based topic angles work as placeholder but need semantic encoder (T017) |
| 2026-09-21 | T019 | Skill-based ++/-- triggers complement voice feedback for power users; minimal skill system unblocked T019 but T002 needed for production; both feedback paths converge on FlyBrain DAN plasticity |
| 2026-09-21 | T021 | Full FlyBrain state persistence (wkc, _proj, pool params) enables long-term affinity learning; persist _proj to avoid numpy version drift; auto-save every 10 steps balances durability vs I/O |
| 2026-09-23 | T031 | setuptools>=81 removed pkg_resources; old deps doing top-level `import pkg_resources` (webrtcvad) crash on any fresh install resolving latest versions — pin `<81` at every install surface until the dep is replaced |
| 2026-09-23 | T032 | Container reaches mic via /dev/snd + gid 29 + PortAudio; raw C-ALSA hw:0,0 fails 16 kHz, plughw converts; verify with a real capture level, not just enumeration; procfs st_size=0 and grp-vs-pwd are classic checker traps |
| 2026-09-23 | T033 | Large lazy models must live in a durable bind mount; interrupting a download corrupts it (SHA-256 gate → full redownload → "stuck" loop). Detect in-flight downloads by *file growth*, not logs (tqdm is \r-rendered). ALSA: second opener of a claimed PCM = PortAudio -9998 "Invalid number of channels", not busy |
| 2026-09-23 | T034 | Custom model paths filtered by basename never load → silent fallback to wrong set (wake words); fix = load existing file paths directly + pre-flight gate. Config fallbacks only work if the call path invokes them (Ollama host fallback was dead). Loaders must bridge legacy interfaces explicitly (adapter), never silent-drop. Destructive sys.modules edits in tests must restore in finally |