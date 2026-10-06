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
---

## Porque é que `deploy.sh` demora ~11 minutos (medido 2026-10-05)

O dono perguntou porque é que o deploy "demora sempre demasiado". Medido, em vez
de suposto.

### O número

| fase | tempo |
|---|---|
| `--dry-run` completo (rsync + gates) | **10 s** |
| suite de prod, `pytest tests/ -q` | **665–685 s** |
| total de um deploy | ~11,5 min |

O deploy **não** está lento a copiar ficheiros nem a reiniciar. 98% do tempo é
a suite de testes de prod a correr sequencialmente.

### Onde estão os 665 s

Por ficheiro, medido um a um (a soma é 828 s porque cada ficheiro paga o seu
arranque de interpretador):

| ficheiro | tempo | o que é |
|---|---|---|
| `test_mobile_controls.py` | 136 s | 41 testes de browser |
| `test_mobile_owner_complaints.py` | 113 s | 50 testes de browser |
| `test_ghost_avatar_browser.py` | 57 s | 18 testes de browser |
| `test_brain_top_line.py` | 55 s | browser |
| `test_brain_layout_rendered.py` | 48 s | browser |
| `test_mobile_layout_browser.py` | 36 s | browser |
| os restantes 103 ficheiros | 296 s | quase tudo sem browser |

**172 dos ~1700 testes são de browser**, e custam ~3,3 s cada contra ~0,2 s de
um teste normal. Só os testes de browser são ~570 s dos 665 s.

### O que NÃO é a causa (medido, para não voltar a culpar o mesmo)

- **Não** é o arranque do Chromium. `sync_playwright()` + `chromium.launch()`
  juntos: **0,5 s**.
- **Não** é `scope="module"` em falta no fixture de browser. Abrir um browser
  por teste em vez de um por módulo: 6,28 s contra 5,75 s para 3 testes — 0,2 s
  de diferença por browser. Não vale a pena.
- **Não** é `device_scale_factor=3`. Com dsf=1 em vez de 3: 4,85 s contra
  4,92 s para 3 testes.
- **Não** é o render Flask. `create_app()` + seed de auth: 0,31 s, e o fixture
  já é `scope="module"`.

### O que é a causa

O fixture de browser ends em `pg.wait_for_timeout(1500)` — **1,5 s de sono por
teste**, à espera de nada em particular. Com 172 testes de browser, são ~260 s
de dormir, mais o `goto` (~0,15 s cada, ~26 s). Os restantes ~380 s são os
testes de browser que esperam por acções reais (tocar no microfone, esperar o
chip de voz, animations) com timeouts generosos.

O `device_scale_factor=3` merece nota aparte: renderiza a 3× para testar
nitidez de pixis num ecrã de telefone. É caro mas é o que os testes de
legibilidade precisam, e mudar isso enfraqueceria o que eles verificam.

### O caminho que NÃO foi escolhido: `pytest-xdist` (2026-10-05, medido)

`pytest-xdist` foi instalado e medido. Corre a suite em paralelo e é
**2,6× mais rápido** — mas **não é fiável para o gate de deploy**, e foi
por isso que ficou **fora do `addopts`** (opt-in, via `-n 4`).

Medido, quatro corridas a `-n 4` nesta máquina (6 núcleos):

| corrida | resultado |
|---|---|
| 1 | 1702 passed |
| 2 | 1701 passed, **1 error** |
| 3 | 1702 passed |
| 4 | 1701 passed, **1 failed** |

**Metade das corridas falha.** A causa é SQLite partilhado: os testes de
browser e de admin abrem BDs de sessão que vários workers podem tocar ao
mesmo tempo, e sob contenção uma inserção perde a corrida. Isolar essas BDs
por worker é trabalho a sério, não uma linha de configuração.

**Porque isto é unacceptable num gate:** um deploy que falha por contenção de
recursos custa mais tempo do que o paralelismo poupa — e falha com o **sinal
errado**: uma mudança verde aparece vermelha por causa da máquina, não do
código. Foi exactamente o que aconteceu com o flake do teste do avatar, e
custou dois deploys.

### O caminho que fica para quando for preciso

1. **Isolar o estado de sessão por worker** (dar a cada worker as suas BDs),
   depois `-n 4` passa a ser seguro e o deploy desce de ~11 min para ~4,5 min.
2. **Esperar condições em vez de `wait_for_timeout(1500)`** — poupa ~260 s e é
   seguro, mas faz-se ficheiro a ficheiro com o `--durations` a confirmar o
   ganho e o flake a ser verificado.

Nenhum dos dois foi feito aqui. Ambos mexem na fiabilidade do gate, e essa é
uma decisão do dono.
