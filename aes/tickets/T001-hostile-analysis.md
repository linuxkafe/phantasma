# T001 — Hostile Analysis: Core Voice Pipeline Integration

## INSIGHTS CONSULTED
- docs/VISION.md (privacy-first, offline, modular voice assistant)
- docs/REQUIREMENTS.md (F-01 to F-14 functional requirements)
- docs/QUALITY_GATES.md (audio pipeline integrity gates)
- docs/CHECKLIST.md (BLOCKER items: error handling, logging, input validation)
- CLAUDE.md (never-do: no cloud APIs, no hardcoded secrets, no breaking offline-first)

## ASSUMPTIONS I'M MAKING (with uncertainty classification)

- [KNOWN] openWakeWord provides pre-trained models for "Hey Jarvis", "Alexa", "Hey Mycroft", "Hey Rhasspy" — justification: openWakeWord docs confirm these built-in models
- [INFERRED] WebRTC VAD can filter audio before openWakeWord to reduce false positives — evidence: webrtcvad is in requirements; common pattern in voice assistants
- [INFERRED] Whisper medium model (~2GB RAM) will load on target hardware (HP Mini G4, 16GB RAM) — evidence: 16GB >> 2GB requirement; but GPU unavailable so CPU inference
- [ASSUMED] Ollama runs locally on 127.0.0.1:11434 with llama3:8b-instruct-8k model — potential impact if false: pipeline fails at LLM stage; need graceful degradation
- [ASSUMED] Piper TTS voice model file exists at configurable path — potential impact if false: TTS silent; need fallback or clear error
- [ASSUMED] ALSA device IDs stable across reboots — potential impact if false: audio I/O fails; need device detection fallback
- [UNKNOWN] Exact VAD threshold for Jabra SPEAK 410 in user's environment — why outside reliable knowledge: depends on room acoustics, mic gain, background noise
- [UNKNOWN] Piper + sox effects chain latency on CPU-only — why outside reliable knowledge: no benchmark data for this hardware combo

## WHAT WASN'T SPECIFIED (that matters)
- Hotword sensitivity threshold (openWakeWord default vs custom)
- VAD aggressiveness level (0-3)
- Whisper language detection vs forced Portuguese/English
- Ollama timeout and retry policy
- Piper voice model selection (multiple voices available?)
- Music snippets directory structure and format
- Error recovery: what happens when any stage fails mid-pipeline?
- Logging format and destination (journald vs file)
- Hotword cooldown period to prevent re-triggering
- Audio sample rate consistency across pipeline (16kHz vs 48kHz)

## ALTERNATIVES I DIDN'T CHOOSE (and why)

- **Porcupine (pvporcupine)**: Rejected — requires API key, cloud dependency, license expiration. Violates offline-first principle.
- **Vosk STT**: Rejected — less accurate than Whisper for Portuguese; larger model for same accuracy.
- **Coqui TTS**: Rejected — slower inference, more complex setup than Piper.
- **Rhasspy/Micropython stack**: Rejected — abandoned project, over-engineered for single-device use.
- **In-memory audio buffer vs streaming**: Chose buffered chunks for simplicity — streaming adds complexity for marginal latency gain.
- **Async/await vs threading**: Chose threading for sounddevice callbacks (blocking I/O) — async adds complexity without clear benefit for linear pipeline.

## INVITE CONTRADICTION
- What critical flaw might I be missing?
  - VAD + openWakeWord double-processing may add latency >500ms target
  - Whisper medium on CPU may exceed 3s end-to-end budget
  - ALSA device enumeration may return different IDs on boot
  - Piper + sox subprocess may introduce memory leaks over time
  - No hotword confirmation sound specified — user won't know when to speak

## DISTINGUISH CLAIM TYPES / QUESTION TYPE DISTINCTION

**Empirical (what is):**
- openWakeWord loads models in <10s on target hardware
- Whisper medium transcribes 5s audio in <2s on CPU
- Ollama llama3:8b responds in <1s for short prompts
- Piper synthesizes 100 chars in <500ms on CPU
- VAD reduces false positives by >80% in typical home environment

**Normative (what should be):**
- Hotword detection should use "Hey Jarvis" as primary (most reliable model)
- VAD threshold should default to 2 (moderate) with config override
- Pipeline should log each stage timing for observability
- Errors should not crash the main loop — continue listening

## RISKS & SIDE EFFECTS
- **Memory pressure**: Whisper (2GB) + Ollama (4-6GB) + Piper (500MB) + openWakeWord (200MB) = ~7-9GB baseline. 16GB leaves margin but no room for other processes.
- **Latency budget tight**: 500ms (hotword) + 2000ms (STT) + 1000ms (LLM) + 500ms (TTS) = 4s > 3s target. Need optimization or smaller Whisper model.
- **Audio device fragility**: ALSA device indices change on USB reconnect. Need name-based selection or udev rules.
- **Blocking I/O in callbacks**: sounddevice callback runs in separate thread; heavy processing there causes audio glitches. Must offload to worker queue.
- **Piper + sox subprocess**: Spawning processes per TTS adds latency and memory. Consider Piper Python API directly.

## COST OF BEING WRONG: HIGH
- Core pipeline failure = entire assistant non-functional
- Audio device issues = silent failure, hard to debug
- Latency >3s = unusable UX, user abandons project
- Memory OOM = system instability, service crashes

→ **Proceeding with implementation but with explicit mitigations for each risk.**

## REASONING SKELETON FOR KEY CLAIMS

**Claim**: End-to-end latency < 3s achievable on CPU-only hardware
- Premise 1: Whisper tiny/base models are 3-5x faster than medium with acceptable accuracy
- Premise 2: Ollama with 8K context on 8B model responds in ~500-800ms for short prompts
- Premise 3: Piper Python API (not CLI) synthesizes in ~200-300ms
- Inference: Use Whisper "base" model (not medium) for speed; use Piper Python API; pipeline stages in parallel where possible
- Conclusion: Target achievable with model downgrade and API optimization

**Claim**: VAD gate reduces false positives without missing hotwords
- Premise 1: WebRTC VAD aggressiveness 2 filters non-speech reliably
- Premise 2: openWakeWord runs on VAD-passed audio only
- Premise 3: VAD adds ~10ms per 30ms frame
- Inference: VAD pre-filter is net positive if threshold tuned
- Conclusion: Implement VAD gate with configurable aggressiveness

**Claim**: Threaded pipeline with queue prevents audio glitches
- Premise 1: sounddevice callback must return in <10ms
- Premise 2: Whisper/Ollama/Piper take 100ms-2s each
- Premise 3: Queue decouples capture from processing
- Inference: Callback pushes to queue; worker thread pulls and processes
- Conclusion: Implement producer-consumer with bounded queue

## SCOPE BOUNDARIES DECLARATION
This analysis covers:
- assistant.py main orchestration loop
- Audio I/O with sounddevice + VAD + openWakeWord
- Whisper transcription (base model for speed)
- Ollama chat completion with system prompt
- Piper TTS via Python API + sox effects
- config.py with all configurable parameters
- Basic logging to stdout/journald

This analysis deliberately excludes:
- Skill system (T002) — no skill invocation in this ticket
- Memory/RAG (T003, T004) — no SQLite or SearxNG integration
- IoT skills (T005, T006) — no device control
- REST API/CLI (T007) — no Flask or CLI script
- Systemd service (T009) — no service file
- Custom hotword training — uses built-in models only