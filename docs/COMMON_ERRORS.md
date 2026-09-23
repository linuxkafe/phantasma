# Common Errors

Known failure patterns and their fixes. Check here when debugging issues.

## Audio Pipeline

| Error | Cause | Fix |
|-------|-------|-----|
| `OSError: [Errno -9997] Invalid sample rate` | Device doesn't support 16000Hz | Set `audio.sample_rate` to device native rate (44100/48000) and resample |
| `sounddevice.PortAudioError: Device unavailable` | Device index/name wrong or in use | Run `python -m sounddevice` to list devices; set `PHANTASMA_DEVICE_IN/OUT` |
| `webrtcvad: frame size must be 10/20/30ms` | Invalid `vad.frame_duration_ms` | Must be 10, 20, or 30 (config validates this) |
| `openwakeword: model not found` | Model name not in pretrained paths | Check `openwakeword.get_pretrained_model_paths()`; use exact names |
| Hotword never triggers | Threshold too high / VAD too aggressive | Lower `hotword.threshold` (0.3-0.5); reduce `vad.aggressiveness` |
| Hotword triggers constantly | Threshold too low / VAD not filtering | Raise `hotword.threshold` (0.6-0.8); increase `vad.aggressiveness` |
| Container cannot open audio device (no such device) | /dev/snd not mapped / wrong group | Compose service needs `devices: - /dev/snd:/dev/snd` + `group_add: ["audio"]`; run `scripts/docker-audio-setup.sh check` |
| Raw ALSA `hw:0,0` capture fails ("cannot set channel count") but `plughw` works | Raw hw: node is playback-only / native-rate mismatch | Set `ALSA_DEVICE_IN`/`ALSA_DEVICE_OUT` to `plughw:0,0` (rate-converting ALSA plugin); sounddevice stack works on both |
| Whisper loop: "`medium.pt` exists, but the SHA256 checksum does not match; re-downloading" | A previous download was interrupted (container stop/start, rebuild) leaving a corrupt partial; Whisper re-downloads from zero every time | Never stop the container mid-download. Cache is persisted in `./data/whisper`. If corrupted, redownload once in one shot: `docker run --rm -v ./data/whisper:/root/.cache/whisper --entrypoint python phantasma-phantasma -c "import whisper; whisper.load_model('medium')"` |
| First speech stalls for minutes after container start | Whisper `medium` (1.4 GB) downloads lazily on first transcription | Expected once; cached afterwards. To preload: run the one-shot preload command above |

## STT (Whisper)

| Error | Cause | Fix |
|-------|-------|-----|
| `FileNotFoundError: medium.pt` | Model not downloaded | First run downloads to `~/.cache/whisper/`; ensure internet or pre-download |
| `CUDA out of memory` | GPU memory exhausted | Set `stt.fp16=false` (CPU) or use smaller model (`base`/`small`) |
| Transcription empty/garbled | Audio level too low/high | Check `audio.block_size` (1600=100ms at 16kHz); verify mic gain |
| Wrong language detected | Short utterance / ambiguous | Set `stt.language="pt"` or `"en"` in config |

## LLM (Ollama)

| Error | Cause | Fix |
|-------|-------|-----|
| `Connection refused` | Ollama not running | `ollama serve` or `systemctl start ollama` |
| `Model not found` | Model not pulled | `ollama pull llama3:8b-instruct-8k` |
| `Context length exceeded` | Conversation too long | Current limit 8192 tokens; implement history truncation |
| Slow response (>10s) | Model too large / no GPU | Use `llama3:8b-instruct-8k` (not 70b); ensure GPU acceleration |

## TTS (Piper)

| Error | Cause | Fix |
|-------|-------|-----|
| `piper: command not found` | Piper not in PATH | Install piper; add to PATH or set `PHANTASMA_PIPER_BIN` |
| `sox: command not found` | Sox not installed | `sudo apt-get install sox libsox-fmt-all` |
| `Voice model not found` | Path wrong / file missing | Download from huggingface.co/rhasspy/piper-voices; verify `.onnx` and `.json` |
| Audio distorted/robotic | Sox effects too aggressive | Adjust `tts.sox_effects` (pitch -100, tempo 1.1, reverb 20 default) |
| Synthesis timeout | Text too long | Split long responses; current timeout 30s |

## Configuration

| Error | Cause | Fix |
|-------|-------|-----|
| `Config validation failed: TTS voice model not found` | Voice files missing in test env | Set `PHANTASMA_TEST_MODE=1` or provide voice files |
| `Audio input device not found` | Device name changed | Run `python -c "import sounddevice; print(sounddevice.query_devices())"` |
| Environment variables not applied | Typo in var name | Check `PHANTASMA_*` prefix; restart process after change |

## API / Flask

| Error | Cause | Fix |
|-------|-------|-----|
| `Address already in use` | Port 5000 occupied | Kill existing process or change port in `src/api/routes.py` |
| CORS errors from Android | Origin not allowed | API adds `Access-Control-Allow-Origin: *`; check Android network config |
| `413 Request Entity Too Large` | Audio upload >16MB | Increase `app.config["MAX_CONTENT_LENGTH"]` |
| Base64 decode fails | Invalid audio format | Ensure client sends valid base64 WAV/MP3; check `format` field |

## Systemd

| Error | Cause | Fix |
|-------|-------|-----|
| `Service failed to start` | Wrong python path in service | Verify `ExecStart=/path/to/venv/bin/python -m src.main` |
| `Permission denied` on audio | User not in audio group | `sudo usermod -a -G audio $USER`; relogin |
| Service restarts loop | Pipeline crashes on init | Check `journalctl -u phantasma -f`; usually missing voice model or Ollama |

## Testing

## Voice Assistant (T034)

| Error | Cause | Fix |
|-------|-------|-----|
| `Hotword detected: 0` + empty STT loop every ~5s | Custom PT `.onnx` wake-word paths never loaded; code fallback silently to all pretrained EN models ("Say 'Hey Jarvis'") | Run `scripts/check-wakewords.sh check` before `docker compose up`; verify `.env` WAKEWORD_MODELS points to `/app/models/*.onnx` |
| `Ollama chat error: Failed to connect to Ollama` | `host.docker.internal` unresolved (no `host-gateway`) and fallback host never attempted | compose has `extra_hosts: ["host.docker.internal:host-gateway"]`; set `OLLAMA_HOST_FALLBACK` in `.env`; `OllamaLLM.chat()` now fails over |
| "como está o tempo?" / discord no response | Legacy skills (weather, discord, ...) invisible to new `SkillLoader` (only `Skill` subclasses loaded) | Loader wraps legacy `TRIGGERS`+`handle` modules via `LegacySkillAdapter`; daemons start via `start_daemons()`. T035: live discord bot needs Flask API in assistant mode |

## Testing

| Error | Cause | Fix |
|-------|-------|-----|
| `Coverage below 30%` | New code untested | Add tests for new modules; target ≥50% on pipeline |
| `ImportError: src.pipeline` | PYTHONPATH not set | Run via `make test` (sets pythonpath) or `PYTHONPATH=src pytest` |
| Tests hang on audio | Real sounddevice in test | Mock `sounddevice` in conftest.py; tests must not open real devices |

## Dependencies

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: No module named 'pkg_resources'` at `import webrtcvad` | setuptools >= 81 removed `pkg_resources`; `webrtcvad` 2.0.10 imports it at module top | Keep `setuptools<81` (pinned in `pyproject.toml` and `Dockerfile`); migrate off webrtcvad long-term (ROADMAP backlog) |
| `openwakeword` import fails | Missing onnxruntime | `pip install onnxruntime` (CPU) or `onnxruntime-gpu` |
| `whisper` import fails | Missing tiktoken | `pip install tiktoken` |
| `piper-tts` import fails | Missing espeak-ng | `sudo apt-get install espeak-ng libespeak-ng-dev` |
| `tinytuya`/`python-miio` fail | Missing crypto libs | `pip install cryptography` |

## Adding New Errors

When you encounter a new error pattern:
1. Add entry to this file with Error, Cause, Fix columns
2. Include file:line reference if code-related
3. Tag with component (Audio/STT/LLM/TTS/Config/API/Systemd/Testing/Deps)