# Quality Gates

Base gates run via `make check`. Extend this file with domain-specific gates.

## Base Gates (All Projects)

| Gate | Command | Failure Action |
|------|---------|----------------|
| Docs check | `make docs-check` | BLOCKER — docs missing or stale |
| Code check | `make code-check` | BLOCKER — TODOs or structure issues |
| Test check | `make test-check` | WARNING — coverage below threshold |
| Lint check | `make lint-check` | BLOCKER — lint errors |
| Type check | `make typecheck` | BLOCKER — type errors |

## Backend / Voice Assistant Gates

### Audio Pipeline Integrity
| Gate | Command | Failure Action |
|------|---------|----------------|
| Hotword detection loads | `python -c "import openwakeword; openwakeword.Model()"` | BLOCKER — core dependency broken |
| PT wake words load + infer | `scripts/check-wakewords.sh check` | BLOCKER — run BEFORE `docker compose up`; model missing/corrupt stops boot |
| Whisper model loads | `python -c "import whisper; whisper.load_model('medium')"` | BLOCKER — STT broken |
| Ollama connectivity | `python -c "import ollama; ollama.list()"` | BLOCKER — LLM unreachable |
| Piper TTS synthesizes | `python -c "from piper import PiperVoice; PiperVoice.load('voice.onnx')"` | BLOCKER — TTS broken |

### Skill System
| Gate | Command | Failure Action |
|------|---------|----------------|
| Skills load dynamically | `python -c "from skills.loader import load_skills; load_skills()"` | BLOCKER — skill system broken |
| No hardcoded device IPs | `grep -r "10\.0\.0\." src/ skills/ --include="*.py" | grep -v "config.py" | grep -v test` | BLOCKER — config violation |

### API & CLI
| Gate | Command | Failure Action |
|------|---------|----------------|
| Flask app starts | `timeout 5 python -c "from assistant import app; app.run(port=0)" 2>&1 | grep -q "Running on"` | BLOCKER — API broken |
| CLI script executable | `test -x phantasma-cli.sh` | BLOCKER — CLI missing |

### Config & Security
| Gate | Command | Failure Action |
|------|---------|----------------|
| No secrets in source | `grep -r "ACCESS_KEY\|LOCAL_KEY\|TOKEN" src/ skills/ --include="*.py" | grep -v "config.py" | grep -v "os.getenv" | grep -v "test"` | BLOCKER — secret leak |
| Config validates | `python -c "import config; config.validate()"` | BLOCKER — config invalid |

### Systemd & Deployment
| Gate | Command | Failure Action |
|------|---------|----------------|
| Service file valid | `systemd-analyze verify phantasma.service` | BLOCKER — service won't start |
| Venv path correct in service | `grep -q "venv/bin/python" phantasma.service` | BLOCKER — wrong python path |

## Infrastructure Gates (SearxNG / Ollama)

| Gate | Command | Failure Action |
|------|---------|----------------|
| Ollama model exists | `ollama list | grep -q "llama3:8b-instruct-8k"` | WARNING — model missing |
| SearxNG reachable | `curl -sf http://127.0.0.1:8081 >/dev/null` | WARNING — web search unavailable |
| Container audio access | `scripts/docker-audio-setup.sh check` | WARNING — voice pipeline cannot reach sound hardware |

## Adding Custom Gates

Add sections above for any additional domain-specific gates.
Each gate must have: name, command, failure action (BLOCKER/WARNING).