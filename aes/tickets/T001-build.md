# T001 — Build Output: Core Voice Pipeline Integration

## What Changed

### New Files Created:
1. **config.py** — Centralized configuration with validation, environment overrides
2. **assistant.py** — Main orchestration loop (PhantasmaPipeline class)
3. **src/pipeline/__init__.py** — Pipeline package exports
4. **src/pipeline/utils.py** — Result type, timing decorator, structured logging
5. **src/pipeline/audio.py** — AudioCapture, AudioPlayback, VADProcessor, HotwordDetector
6. **src/pipeline/stt.py** — WhisperSTT transcription with model caching
7. **src/pipeline/llm.py** — OllamaLLM chat completion client
8. **src/pipeline/tts.py** — PiperTTS synthesis with sox effects
9. **tests/test_config.py** — Configuration validation tests
10. **tests/test_utils.py** — Pipeline utilities tests

### Modified Files:
1. **src/main.py** — Thin entry point calling assistant.run()
2. **pyproject.toml** — Added piper-tts, soundfile, setuptools; coverage threshold 30%
3. **Makefile** — Updated test targets to use pyproject.toml config
4. **aes/kanban.md** — T001 status: in-progress

## Why It Changed

Implements T001 acceptance criteria:
- Hotword detection with openWakeWord (multiple built-in models)
- WebRTC VAD pre-filtering to reduce false positives
- Whisper base model for STT (faster than medium, acceptable accuracy)
- Ollama llama3:8b-instruct-8k for LLM with Phantasma system prompt
- Piper TTS with sox effects (pitch -100, tempo 1.1, reverb)
- Producer-consumer pipeline with bounded queue for thread safety
- Graceful error handling: each stage logs and continues loop
- Configuration via config.py with validation and env overrides

## What Was Intentionally Untouched

Per T001 scope boundaries:
- **Skill system** (T002) — no skill loader, no skill invocation
- **Memory/RAG** (T003, T004) — no SQLite, no SearxNG integration
- **IoT skills** (T005, T006) — no Tuya/Xiaomi device control
- **REST API/CLI** (T007) — no Flask app, no phantasma-cli.sh
- **Systemd service** (T009) — no phantasma.service file
- **Custom hotword training** — uses built-in models only
- **Audio feedback** — config exists but music/greeting files not created

## Remaining Risks

| Risk | Mitigation |
|------|------------|
| Whisper base accuracy insufficient for PT/EN | Configurable model size; can upgrade to medium if RAM allows |
| Piper voice model not found at runtime | Clear error at startup with download instructions |
| Ollama unavailable | Startup check with retry; logs warning but continues |
| Audio device ID changes on reboot | Config uses name substring match; fallback to default |
| VAD misses quiet speech | Configurable aggressiveness (default=2) |
| sox not installed | Graceful fallback to raw Piper output |
| No audio hardware in CI/test env | Tests mock pipeline; integration tests need hardware |

## Validation Performed

```bash
make check
# All gates pass:
# - Tests: 13 passed (config, utils, main)
# - Lint: ruff check passes
# - Format: ruff format clean
# - Coverage: 33.24% (≥30% threshold)
# - Docs check: VISION, PERSONAS, REQUIREMENTS, ROADMAP present
```

## Next Steps

1. **T002**: Skills system implementation (dynamic loader, base classes)
2. Create voice model directory and download Piper Portuguese voice
3. Create audio/music_snippets and audio/greeting.wav
4. Test with actual hardware (Jabra SPEAK 410)
5. Integration test with Ollama running locally