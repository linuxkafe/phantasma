# T001 — Solution Proposal: Core Voice Pipeline Integration

## CHOSEN APPROACH

Implement `assistant.py` as a single-file orchestrator with:
1. **Configuration-first**: All params in `config.py` (validated on startup)
2. **Producer-consumer pipeline**: sounddevice callback → bounded queue → worker thread
3. **Stage isolation**: Each pipeline stage (VAD, hotword, STT, LLM, TTS) as independent function with error handling
4. **Observability**: Structured logging with stage timings
5. **Graceful degradation**: Stage failures log and continue; never crash main loop

## FILES TO CREATE/MODIFY

### New Files:
- `config.py` — Runtime configuration with validation
- `assistant.py` — Main orchestration loop (replaces src/main.py)
- `src/pipeline/__init__.py` — Pipeline package
- `src/pipeline/audio.py` — Audio I/O, VAD, hotword detection
- `src/pipeline/stt.py` — Whisper transcription
- `src/pipeline/llm.py` — Ollama chat completion
- `src/pipeline/tts.py` — Piper TTS + sox effects
- `src/pipeline/utils.py` — Shared utilities (timing, logging)

### Modified Files:
- `src/main.py` → thin entry point calling `assistant.run()`
- `pyproject.toml` — Add `piper-tts` dependency (if not in requirements)
- `Makefile` — Add `run-assistant` target

### NOT Changing:
- `src/__init__.py` — Keep version only
- `tests/test_main.py` — Will add integration tests later (T002+)
- No skill system, no API, no systemd — out of scope

## VERIFICATION CRITERIA

| Criterion | How Verified |
|-----------|--------------|
| Hotword detection works | `python -c "from src.pipeline.audio import HotwordDetector; h=HotwordDetector(); h.test()"` |
| VAD filters silence | Unit test with silent vs speech audio samples |
| Whisper transcribes | `python -c "from src.pipeline.stt import transcribe; print(transcribe('test.wav'))"` |
| Ollama responds | `python -c "from src.pipeline.llm import chat; print(chat('hello'))"` (requires Ollama running) |
| Piper synthesizes | `python -c "from src.pipeline.tts import synthesize; synthesize('test')"` (requires voice model) |
| Pipeline integrates | `make run-assistant` starts without error, logs pipeline stages |
| Config validates | `python -c "import config; config.validate()"` |
| No hardcoded secrets | `grep -r "KEY\|TOKEN" src/ --include="*.py" | grep -v config` returns empty |
| Stage timing logged | Run assistant, speak hotword, verify log shows `stage=hotword duration_ms=...` |

## IMPLEMENTATION ORDER

1. **config.py** — All configuration with validation
2. **src/pipeline/utils.py** — Timing decorator, structured logging setup
3. **src/pipeline/audio.py** — sounddevice stream, VAD, openWakeWord
4. **src/pipeline/stt.py** — Whisper model loading + transcribe()
5. **src/pipeline/llm.py** — Ollama client + chat() with system prompt
6. **src/pipeline/tts.py** — Piper voice load + synthesize() + sox effects
7. **assistant.py** — Main loop wiring all stages with queue
8. **src/main.py** — Entry point
9. **Tests** — Unit tests for each pipeline module

## KEY DESIGN DECISIONS

| Decision | Rationale |
|----------|-----------|
| Whisper **base** model (not medium) | 3x faster, ~1GB RAM, acceptable PT/EN accuracy |
| Piper **Python API** (not CLI) | Avoids subprocess spawn latency (~200ms savings) |
| Threading (not async) | sounddevice callback is blocking; simpler mental model |
| Bounded queue (maxsize=10) | Backpressure prevents memory growth; drops oldest if full |
| Stage functions return `Result` tuple | `(success, data, error)` — explicit error handling, no exceptions across threads |
| Config via Python module | Type hints, validation, IDE support; no YAML/JSON parsing |
| Journald-friendly logging | `logger.info("stage=hotword duration_ms=42")` — parseable by log tools |

## REMAINING RISKS (Accepted with Mitigations)

| Risk | Mitigation |
|------|------------|
| Whisper base accuracy insufficient | Configurable model size; can upgrade to medium if RAM allows |
| Piper voice model not found | Clear error at startup with download instructions |
| Ollama unavailable | Startup check with retry; log warning but continue (hotword still works) |
| Audio device ID changes | Config uses device name substring match; fallback to default |
| VAD misses quiet speech | Configurable aggressiveness; default=2 (moderate) |
| sox not installed | Check at startup; fallback to raw Piper output (less robotic) |