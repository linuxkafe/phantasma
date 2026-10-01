# pHantasma

Local-first, offline, modular voice assistant built in Python — private by design, running entirely on your own hardware without third-party cloud dependencies (except optional web search via self-hosted SearxNG).

## Quickstart

```bash
# 1. Install system dependencies
sudo apt-get install -y python3.11 python3.11-venv sox mpg123 libopenblas-dev portaudio19-dev

# 2. Install Ollama and pull model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3:8b-instruct-8k

# 3. Install Piper TTS voice
mkdir -p voices
wget -O voices/pt_PT-nicolau-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_PT/nicolau/medium/pt_PT-nicolau-medium.onnx
wget -O voices/pt_PT-nicolau-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_PT/nicolau/medium/pt_PT-nicolau-medium.onnx.json

# 4. Setup project
python3.11 -m venv venv
source venv/bin/activate
pip install -e .[dev]

# 5. Configure audio devices (optional - auto-detects)
# cp config.example.py config.local.py  # edit device_in/device_out if needed

# 6. Run
make run
# or: python -m src.main
```

## Architecture

```
┌─────────────┐     ┌──────────┐     ┌────────┐     ┌──────────┐     ┌─────┐
│  Microphone │────▶│  VAD     │────▶│ Hotword│────▶│  Whisper │────▶│ LLM │
│  (sounddev) │     │ (webrtc) │     │ (oww)  │     │ (STT)    │     │(Ollama)│
└─────────────┘     └──────────┘     └────────┘     └──────────┘     └─────┘
                                                                     │
                                                                     ▼
┌─────────────┐     ┌──────────┐     ┌────────┐     ┌──────────┐     ┌─────┐
│  Speaker    │◀────│ Audio    │◀────│ Piper  │◀────│  TTS     │◀────│      │
│  (sounddev) │     │ Playback │     │ (TTS)  │     │ (synth)  │     │      │
└─────────────┘     └──────────┘     └────────┘     └──────────┘     └─────┘
```

**Pipeline stages:**
1. **AudioCapture** — sounddevice input stream, callback feeds queue
2. **VADProcessor** — WebRTC VAD filters non-speech frames
3. **HotwordDetector** — openWakeWord detects "Hey Jarvis"/"Alexa"/etc.
4. **WhisperSTT** — Transcribes speech to text (cached model)
5. **OllamaLLM** — Generates response via local LLM
5. **PiperTTS** — Synthesizes speech with sox effects
6. **AudioPlayback** — Plays response via sounddevice

## Configuration

All runtime configuration in `config.py` with environment variable overrides:

| Env Var | Config Path | Description |
|---------|-------------|-------------|
| `PHANTASMA_DEVICE_IN` | `audio.device_in` | ALSA input device (e.g., "hw:1,0") |
| `PHANTASMA_DEVICE_OUT` | `audio.device_out` | ALSA output device |
| `PHANTASMA_VAD_AGGRESSIVENESS` | `vad.aggressiveness` | 0-3 (default 2) |
| `PHANTASMA_HOTWORD_THRESHOLD` | `hotword.threshold` | 0.0-1.0 (default 0.5) |
| `PHANTASMA_WHISPER_MODEL` | `stt.model_size` | tiny/base/small/medium/large |
| `PHANTASMA_OLLAMA_HOST` | `llm.host` | Ollama URL (default http://127.0.0.1:11434) |
| `PHANTASMA_OLLAMA_MODEL` | `llm.model` | Model name (default llama3:8b-instruct-8k) |
| `PHANTASMA_PIPER_VOICE` | `tts.voice_model_path` | Path to .onnx voice model |
| `PHANTASMA_QUEUE_SIZE` | `pipeline.queue_maxsize` | Audio queue size |
| `CHACON_PLUG_IP` | `chacon_plug_ip` | Local IP of the Chacon balcony plug (default 10.0.0.116). Non-empty ⇒ the device gets a tile in `/`. |
| `CHACON_PLUG_NAME` | `chacon_plug_name` | Nickname shown on the tile (default "luz do balcão") |
| `CHACON_PLUG_PORT` | `chacon_plug_port` | UDP port (default 18530) |

## Features Implemented (branch `testing`)

### Admin Interface (`/admin/brain`)
- **Unified brain view** — Memory, RAG, FlyBrain, 3D explorer on one screen (`/admin/brain`)
- **GMIF Graph Classification** — M1–M5 epistemic levels (M3 = logical implication)
- **Sleep/Dream Button** — One-click sleep/dream cycle (`/admin/brain/sleep`)
  - Analyzes GMIF-classified graph for gaps (weak edges M1/M2, disconnected pairs, missing requirements, causal gaps)
  - Performs targeted SearxNG research per gap type
  - Stores synthesized insights via `save_to_rag()`
- **Admin Burger** — 6 links (Cérebro, Dashboard, Configuração, Utilizadores, .env, Sair)
- **Hamburger 44×44 px** responsive < 900px

### Voice UI (`/`)
- **Skill-based UI** served at `/` with design system shared from admin
- **Hamburger menu** with admin links (Cérebro, Dashboard, Config, Users, .env, Logout)
- **Device sensors** — real temperature/humidity/power, no fake "ON" fallback
- **Device tiles** grouped by room (`Geral`, `WC`, `Sala`, `Quarto`,
  `Entrada`) with a per-name icon; a click sends a natural-language command
  through the same pipeline as a voice request. Devices are listed by
  `/get_devices`, and their state by `/device_status`
  - **Weather widget** — live IPMA/Open-Meteo via `skill_weather` daemon
  - **Pipeline resilience** — `src/pipeline/noise.py` tracks the ambient noise
    floor and `src/pipeline/quiet.py` gates replies; the LLM call gets a bounded
    **connect** timeout (a dead Ollama host used to block with no bound at all
    and stalled every answer) while keeping the generous read budget

  ### Phone layout

Measured in headless Chromium at 375x667, and every number below was read off a
rendered page rather than asserted on a CSS string. A mobile-UI audit on
2026-09-29 found four defects that every string-based test had passed, two of
them in features I had just reported as working.

**The dock.** One full-width bar at the bottom: pull it up (or tap it) and the
conversation unrolls over the screen, push it down or drag the grip and the
tiles come back. A 71px pill in the corner was what a thumb misses, and it was
also painted over the microphone — `elementFromPoint` at the mic's centre
returned the pill, so the mic could not be tapped at all.

**The blind, not a slide.** Opening animates `clip-path: inset(0 0 100% 0)` →
`inset(0)`, so an edge travels down the screen while the content beneath it is
revealed. A `translate` alone reads as a rectangle moving; this reads as a
roller blind being pulled down.

**The burger is at the top right** — the corner a right thumb reaches on a
phone held in one hand, and no longer competing with the dock for the bottom
one. It was 44×44 at (323, 8). The menu also stops hiding the sign-out: it was
306px tall and wrapped "Sair" into a second flex column at x=365 of a 375px
screen, so signing out was off-screen on the page whose job includes signing
out.

**Every device is visible by default.** The rooms were laid out in a row with
`flex-shrink: 0`, so the strip held 857px of content in a 375px box with
`overflow-x: hidden`: 7 of 17 switches were painted outside the edge and
unreachable, while `scrollWidth == innerWidth` so the no-side-scroll test
passed. Rooms stack and tiles wrap now; 13 tiles, 0 cut off, no scroll needed.

**Icons are SVG, not emoji.** Emoji rendered differently per platform — the
same light was a yellow bulb on iOS and a white one on Android — ignored the
tile's grayscale filter, so an "off" device still shouted colour, and a
catch-all `⚡` meant `porta`, `ar` and `camera` all rendered as a lightning
bolt. Each device type has a drawn icon, the fallback is a neutral device
outline, and `currentColor` means the grayscale still works. The brand, the
weather, the moon phases and the air-quality indicator are SVG too; zero emoji
glyphs remain in the rendered page.

**Two microphones, one machine.** The dock one is always visible; the composer
one sends a voice message inside the conversation. They share a single
`getUserMedia` stream and a single recording state, so neither can win silently.

**Device states are live when the page has loaded.** They arrived 5.29s late on
the first tick of a 5-second interval, so every switch was inert for five
seconds of every load. A person cannot throw a switch they have not waited for.

**What the browser tests assert** (`tests/test_mobile_controls.py`,
`tests/test_mobile_layout_browser.py`, 33 tests, run in the deploy gate): the
page raises no JavaScript error; a tap on the microphone reaches
`getUserMedia`; nothing is painted over it and it is 44×44 or larger; no switch
is outside the strip; the states are not still disabled at 1.5s; every icon
slot holds a sized SVG; the brand SVG is not 0×0; no emoji is rendered; the
burger is in the top 20% and right half; the sign-out is on screen; the dock is
at least 90% of the width and 56px tall with a 40px grip; pulling it up opens
the panel; opening is a `clip-path` blind and not a translate; the dock stays
above the open panel; the desktop layout is unchanged.

### Phone layout

The device tiles own the phone screen. The conversation is a full-screen sheet
that appears when asked for, covers everything, and is dismissed by dragging it
down or by the "Fechar" button that moves out of the thumb arc to avoid the
composer.

- **The opener lives outside the panel.** It was a child of `#main` first, and
  `visibility` is inherited, so hiding the panel hid the only control that could
  open it. Measured at 375×667: the tab sat at **y=1076 in a 667px viewport**.
- **`#header-strip` is `display: contents` on a phone.** `#devices` is a child
  of it, so no amount of flex on `#devices` makes it grow past its parent — the
  strip measured 45px of a 667px screen while the wrapper had 208px. Dissolving
  the wrapper leaves the DOM untouched (every `#header-strip #devices` selector
  still matches) and puts the tiles in the body's column, where they get
  everything the brand does not need.
- **The phone overrides are the last rules in the stylesheet.** Sitting in the
  middle, later rules won on order: `#main` computed as `position: relative`,
  the panel stayed in the flow, and it took 459px from the tiles.
- **Two microphones, one machine.** The one in the nav bar is a shortcut; the
  one in the composer sends a voice *message*. They share a single
  `getUserMedia` stream and a single recording state, because two independent
  recorders would mean whichever was pressed last silently won.

**These are measured, not asserted on strings.** `tests/test_mobile_layout_browser.py`
loads the page in headless Chromium at 375×667 and measures rectangles: the
strip is at least 60% of the viewport with at least 8 tiles rendered, the opener
is inside the viewport and not inside the panel, the open panel is full-screen,
the microphone is a 44px target in the composer, a 30px drag springs back, a
long drag closes, and a drag upwards does nothing. It runs in the deploy gate
(12 passed in production) and skips loudly, with a reason, where no browser
exists.

### Voice from the browser (phone)
  - **Press to talk, release to send.** One round trip: the browser records,
    decodes, and posts; the server transcribes, executes, and answers in text
    and audio.
  - **The audio is decoded in the browser, not the server.** MediaRecorder
    produces `webm/opus` on Android Chrome, which `soundfile` cannot read, and
    the old "assume raw PCM" fallback read the opus container as 16-bit samples
    and handed the recogniser noise — which transcribes to a confident, wrong
    sentence with no error anywhere. `AudioContext.decodeAudioData` handles
    every format the browser itself can record, and it costs the server no
    ffmpeg dependency.
  - **`POST /api/voz`** — session-gated like the page, because it turns light
    switches into a POST body. Returns `{success, transcript, text,
    audio_base64, audio_format, processing_time_ms}`.
  - **Silence is refused before the recogniser runs.** A mis-tap posts an empty
    room; Whisper will happily invent a sentence for it. A peak-amplitude check
    answers "nothing was heard" instead.
  - **The transcript is always echoed back** into the chat log, so a mishearing
    is visible before the user acts on the answer.
  - **Latency is the real cost, and it is not small.** Measured on the
    production box (i5-8500T, 6 threads) with the live service also running, a
    1.6 s utterance takes ~12 s to transcribe with the configured `medium`
    model, ~6 s for the LLM and ~2.5 s for the reply audio — **~20 s end to
    end**. The web path deliberately reuses the already-loaded model rather
    than loading a second one: the box has 15 GiB with **8 GiB of swap already
    in use**, and a second Whisper would make the pressure worse. Lowering
    `WHISPER_MODEL` to `small` or `base` trades accuracy for roughly 2 s and
    1 s respectively, and helps the always-on path too.
  - **What is not tested here:** the browser half. `getUserMedia`,
    `MediaRecorder` and `AudioContext` need a real device and a real
    microphone. The server contract is covered by `tests/test_ui_voice.py`, the
    inline JavaScript is syntax-checked, and the flow was verified by hand
    against real synthesised speech.

  ### Wake Words
- `olá fantasma` (TTS trigger, score ~0.81)
- `hey fantasma` (score ~0.63, below persistence threshold)
- Config: `WAKEWORD_CONFIDENCE=0.50`, `WAKEWORD_PERSISTENCE=2`

### Sensors & Devices
- **Tuya sensors** — declared DPS mapping (`SENSOR_TEMP_MAP`), validity 5–45°C
- **Real readings**: `24.5° · 30m`, `25.3° · 1h`, `0 ppm`, `sem leitura · 17h`
- No fake "ON" fallback; shows `age_s`, `stale` flag

### Chacon Balcony Light (`skill_chacon_udp`)

The balcony plug is a Hi-Flying **HF-LPB100** (firmware V1.0.08) and is
controlled **directly over the LAN**, with no vendor account. The previous
path went through the DIO/Chacon cloud, whose account is dead (see
`aes/tickets/T036`, `T051`); `skill_chacon` now fails with a clear message
instead of pretending to work.

- **Device**: MAC `F0:FE:6B:57:E7:5A`, UDP port **18530**, commands in English
- **No AES key needed.** The plug's local key is random, generated at DIO
  pairing, so encrypted packets are silently dropped. The firmware accepts
  **plaintext** packets (header flag `bEncrypt` cleared) whose body header
  matches the device constants, and it answers them. The skill keeps a UDP
  socket bound to `:18530` because the plug replies on that **fixed** port,
  not to the sender's ephemeral one.
- **Packet** (25 bytes, validated live): open header `pv=0x01`, flag `0x04`,
  MAC, `dataLen=0x10`; body `reserved=0x00`, `sn=0xFFFF`, `deviceType=0xDF`,
  `factoryCode=0xF1`, `license=0x21B4`, `cmd`, `arg`, pad. `cmd 0x01`
  (SET_GPIO_STATUS) with `arg 0xFFFF` = on, `0x00FF` = off.
- **Triggered** by the balcony nicknames; `PRIORITY=60` so it wins over
  `skill_tasmota` (50), which cannot drive this firmware.
- **UI tile** in `/`: `luz do balcão` is listed by `/get_devices` and grouped
  under **Sala** with the bulb icon. Clicking it routes through the same
  natural-language pipeline as a voice command.
- **State is reported as `unknown`, never guessed.** The plug accepts
  `GET_GPIO_STATUS` (0x02) but encrypts its reply with the per-device key, so
  there is nothing to read back; `/device_status` therefore answers
  `{"state": "unknown", "readable": false}` — reachable, but honest about not
  knowing whether the relay is closed. A fabricated on/off would be worse
  than admitted ignorance.

### Weather
- `skill_weather` daemon populates `weather_cache.json` every 30 min
- IPMA forecast + Open-Meteo AQI + moon phase
- UI shows stale indicator in tooltip

### GMIF Graph Memory
- **Schema**: `memory_graph` extended with GMIF columns (`logical_form`, `validation_type`, `extraction_confidence`, `validation_confidence`, `source_chunks`, `gmif_level`, `node_gmif_type`, `node_gmif_confidence`, `node_gmif_evidence`)
- **Classifier** (`src/pipeline/gmif_classifier.py`): M1–M5 levels, logical/external/human validation
- **GMIF Dream** (`skills/skill_gmif_dream.py`) — runs after standard dream (03:00)
  - Analyzes weak edges (M1/M2 promotable to M3/M4)
  - Finds disconnected semantic pairs
  - Detects missing requirements and causal gaps
  - Targeted SearxNG research per gap type, LLM synthesis → `save_to_rag()`

### Wake Word Feedback
- `++` / `--` exact triggers → DAN+/DAN- in FlyBrain
- Immediate FlyBrain `persist()` flush

### Admin Features
- **Dashboard** — stats, memory graph, FlyBrain state
- **Memory** — live `brain.db` sample with graph
- **RAG** — retrievable chunks with tags/facts
- **FlyBrain Manager** — reinforcement parameters (α, γ, ε, steps)
- **Users** — role-based (admin/user), bcrypt passwords
- **Config/Env** — YAML/ENV editor with validation. `update_config()` inserts
  the row when the key is not in the config table yet, so a setting that was
  never persisted can still be set from the UI and read back; categories come
  from `CONFIG_CONTROLS` and each value is normalised per key.

### Quality Gates
- **Python**: 641 passed, 1 skipped (`pytest`, full suite)
  - 17 `tests/test_hotword.py` errors are **pre-existing** `onnxruntime`
    model-loading failures, unrelated to the code changes; proven by
    re-running them with the changes stashed
- **Node (mermaid)**: 26/26 tests passing
- **Chromium (explorer)**: 36/36 tests passing
- **0 tracebacks** in service
- Ruff lint + format, MyPy typecheck, pytest
- Pre-commit hook runs the gates on every commit (`.aes/hooks/pre-commit.sh`)

### Authentication

`/` is behind a session; `/admin/*` has its own OTP flow. Two callers, two
mechanisms:

| Caller | Mechanism | Grants |
|---|---|---|
| Browser | `POST /login` with email + password → session cookie | the voice UI, and `/admin/*` if the role is admin |
| Program | `Authorization: Bearer <token>` | sending commands, nothing else |

A login from a device the account has not used before is asked for a code
mailed to the address, and only then is the device trusted (a long random token
in an httpOnly cookie, revocable from the profile). The loopback admin bypass
does **not** open `/`: that bypass grants admin to any local process, and the
light switches are not something an admin bypass should reach.

**`/recuperar`** resets a forgotten password with a one-time code. It never
confirms whether an address exists -- the page, the message and the status are
identical either way, because a public form that answers differently is a free
oracle for the user list. Codes are bcrypt-hashed, bound to the address and the
purpose, single-use, and destroyed by a wrong guess rather than paused.

**`/perfil`** issues per-user API tokens, shown once at creation and never
re-displayable, revocable one at a time. A token is one capability: send
commands. It cannot read this page, the memories, or the admin surface.
`PHANTASMA_COMMAND_TOKEN` remains as the machine credential for the Discord
skill and any other program on the network.

| Env Var | Description |
|---------|-------------|
| `PHANTASMA_RECOVERY_PEPPER` | Mixed into every recovery-code verifier. **Set it.** Without it the codes are protected by bcrypt alone, and a stolen copy of the database becomes a slow offline search over the code space instead of an impossible one. A warning is logged on every code issued while it is absent. |
| `PHANTASMA_COMMAND_TOKEN` | Machine-to-machine bearer token: a program may send commands and read the non-admin device/reading API. It cannot reach `/admin`, the user store, or the memory editor. **Unset means browsers with a session still work and everything else is a 401** — it does not mean "open". Set it. |

## Skills System

Skills are dynamic Python modules in `skills/` directory. Two forms are
supported and both are loaded by `skills/loader.py`:

- **Class-based** (preferred) — subclass `skills.base.Skill` with `NAME`,
  `TRIGGERS`, `TRIGGER_TYPE`, `PRIORITY` and `handle()`. A higher `PRIORITY`
  wins when several skills match the same text, which is how
  `skill_chacon_udp` (60) takes the balcony nicknames from `skill_tasmota`
  (50). `get_status_for_device()` here is what feeds the `/device_status`
  endpoint behind each UI tile.
- **Legacy module-level** — a module exposing `TRIGGERS` and
  `handle(text, context)`, wrapped by the loader in a `LegacySkillAdapter`.

`TRIGGER_TYPE` is `"contains" | "exact" | "regex"`. Skills may also define
`init_skill_daemon()` for background tasks.

## REST API

### Public exposure (verified 2026-09-29)

`https://phantasma.linuxkafe.com` resolves to a public address, terminates TLS
in front of this box, and forwards to it. **This service is on the internet, not
on the LAN** — and it was answering as if it were on the LAN.

Verified against the live URL before anything was changed:

| Endpoint | Anonymous, before |
|---|---|
| `GET /get_devices` | **200** — every light, socket and appliance |
| `GET /device_status?nickname=casa` | **200** — live power, consumption, series |
| `GET /api/graph/audit` | **200** — the memory graph |
| `POST /api/stt`, `POST /api/tts` | reached the handler — free CPU, readable |
| `POST /comando`, `/device_action`, `/api/command` | reached the handler — lights |

and **every** response carried `Access-Control-Allow-Origin: *`. That header is
what made it exploitable rather than merely private: `*` tells the browser any
site may read the response, so a page the owner visited could `fetch()` the
inventory and send it anywhere. No vulnerability was required, only a link.

**Now:** every endpoint above answers `401` without a credential, and no
response carries a CORS origin unless it is on the allow-list.

`PHANTASMA_CORS_ORIGINS` is a comma-separated list of exact origins. It is empty
by default, and the default emits no CORS headers at all. Nothing legitimate
needed the wildcard: the voice UI is served *by* this service, so it is
same-origin. Every client that is not a browser — the Discord skill, a script,
anything using `PHANTASMA_COMMAND_TOKEN` — is outside the browser's CORS
enforcement entirely, so a wildcard would have bought nothing and cost the
origin check.

### The pepper was breaking the thing it protected

`PHANTASMA_RECOVERY_PEPPER` is documented as "Set it". Setting it broke password
recovery and new-device codes completely, and nobody noticed for the whole time
it was absent.

`_hash_code` built `pepper|email|code` and passed it to `bcrypt.hashpw`. bcrypt
does not truncate at 72 bytes — it raises `ValueError`. With the 28-character
default placeholder the payload was 53 bytes and every test passed; with the
64-character hex value the README asks for, the same payload was 88 bytes and
**every** code raised on the first request.

The payload is now SHA-256'd to a fixed 32 bytes before bcrypt, so its length
cannot depend on the pepper or the address. The pepper is still fully mixed in —
it is an input to the digest, not a truncation of it. Codes issued under the old
construction no longer verify; they are single-use and short-lived, so this is
acceptable, and `tests/test_auth_pepper_length.py` states it as a test rather
than a comment.

`hash_password` never mixed in the pepper, so **no user password was affected**.

The general lesson, and it is the one worth keeping: a feature that only runs
when the owner follows the documentation is a feature nobody has tested.



`POST /comando`, `POST /device_action` and `POST /api/command` require **one of
two credentials** — never neither:

| Caller | Credential |
|---|---|
| Browser | the session cookie from `/login` — this is what the page uses |
| Program | `Authorization: Bearer $PHANTASMA_COMMAND_TOKEN` |

Before this, the gate began `if not command_token.enabled(): return True`, and
the token had never been set on the production box. On a service listening on
`0.0.0.0:5000` that meant any device on the LAN could `POST /comando` and turn
the lights on. It also made the login look stronger than it was: `/` was behind
a session while the endpoints behind it were not.

With no token configured, browsers with a session still work and everything else
gets a `401` — an unset token is no longer "open", it is "browsers only". To let
programs in, set the token and restart:

```bash
sudo sh -c 'printf "\n# Authorises programs (Home Assistant, Discord, shell).\n# One capability: sending commands. Not the admin surface.\nPHANTASMA_COMMAND_TOKEN=%s\n" \
  "$(openssl rand -hex 32)" >> /opt/phantasma/.env'
sudo service phantasma restart
```

**Consequence to expect:** until that token is set, the Discord skill cannot
command the house and will say so in its own reply rather than failing silently.
The browser is unaffected.

Accepting a session on a POST trades the token's CSRF immunity — a cross-site
form cannot set an `Authorization` header — for the session cookie's, so the
cookie now sets `SameSite=Lax` and `HttpOnly` **explicitly** in
`src/api/routes.py`. That withholds the cookie on a cross-site POST, which is
the control that replaces the immunity; it is set in config rather than left to
whatever a browser happens to default to. `SESSION_COOKIE_SECURE` stays off
because the service is plain HTTP on the LAN, and a forced `Secure` cookie would
be silently dropped by the browser and break every session.

Flask server at port 5000:

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/info` | Server capabilities |
| `POST /api/command` | Execute voice/text command |
| `POST /api/stt` | Speech-to-text |
| `POST /api/voz` | Browser voice: 16 kHz mono WAV (base64) → transcript, answer and answer audio. **Session-gated**, unlike the three above |
| `POST /api/tts` | Text-to-speech |
| `GET /api/devices` | List configured devices |
| `POST /api/devices/<name>/control` | Control device |
| `GET /get_devices` | Device list for the `/` UI (toggles + sensors) |
| `GET /device_status?nickname=` | State behind one UI tile |
| `POST /device_action` | Toggle a device by nickname (natural language) |
| `GET/POST /api/memory` | Long-term memory |
| `GET /api/weather` | Weather widget data |
| `GET /api/memory/graph` | Explorer payload |
| `POST /admin/brain/sleep` | Trigger sleep/dream cycle |

## Systemd Service

```bash
sudo cp phantasma.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now phantasma
journalctl -u phantasma -f
```

## Development

```bash
make setup      # Create venv, install deps
make test       # Run tests with coverage
make lint       # Ruff lint
make format     # Ruff format
make check      # All quality gates
make typecheck  # MyPy type checking
```

## Project Structure

```
pHantasma/
├── assistant.py          # Main pipeline orchestration
├── config.py             # Configuration (validated on startup)
├── src/
│   ├── main.py           # Entry point
│   ├── api/              # Flask REST API (admin, routes)
│   │   ├── admin.py      # Admin routes (brain, memory, rag, flybrain, users, config)
│   │   ├── routes.py     # Public API routes (command, stt, tts, devices, weather)
│   │   └── design.py     # Design system CSS/JS
│   └── pipeline/         # Voice pipeline stages
│       ├── audio.py      # Audio I/O, VAD, hotword, GMIF classifier
│       ├── noise.py      # Ambient noise-floor tracking
│       ├── quiet.py      # Reply gating (stay quiet when not needed)
│       ├── stt.py        # Whisper STT
│       ├── llm.py        # Ollama LLM
│       ├── tts.py        # Piper TTS
│       └── utils.py      # Result, logging, timing
├── tests/                # Unit + integration tests (mocked + real)
├── skills/               # Skill modules (dynamic loading)
│   ├── skill_ui.py       # Voice UI at /
│   ├── skill_chacon_udp.py # Chacon balcony light over local UDP
│   ├── skill_tasmota.py  # Tasmota HTTP device path (PRIORITY 50)
│   ├── skill_dream.py    # Standard dream (02:30)
│   ├── skill_gmif_dream.py # GMIF dream (03:00)
│   ├── skill_feedback.py # ++/-- feedback
│   ├── skill_weather.py  # Weather daemon
│   └── skill_*.py        # Device/skill modules
├── aes/                  # AES project tracking
├── docs/                 # Project documentation
└── Makefile              # Quality gates and commands
```

## Mobile / Desktop Interface

The interface at `/` is responsive and serves as both desktop and mobile UI.

### Screenshots

| Desktop (≥900px) | Mobile (375×667) |
|------------------|------------------|
| ![Desktop UI](docs/screenshots/desktop.png) | ![Mobile UI](docs/screenshots/mobile.png) |

**Desktop features:**
- Side-by-side: device tiles (left) + conversation (right)
- Admin burger menu (top-right) with 6 links
- Room-grouped tiles with SVG icons
- Live weather widget

**Mobile features:**
- Full-width device strip (rooms stack, tiles wrap)
- Bottom dock (pull up for conversation)
- Top-right burger menu (thumb-reachable)
- Two microphones (nav + composer), shared stream
- Blind animation (clip-path) for panel open/close

### Mobile Layout Validation

Automated browser tests (`tests/test_mobile_*.py`, 33 tests) verify:
- No JS errors, microphone reachable (44×44 target)
- Zero tiles cut off (rooms stack, no horizontal scroll)
- SVG icons (no emoji), grayscale filter works on OFF
- Burger in top 20%/right half, sign-out visible
- Dock ≥90% width, 56px tall, 40px grip
- Blind animation (not translate), dock stays above panel
- Device states loaded ≤1.5s after page load

Run: `make test-mobile` (requires Chromium + Playwright)

### Voice from Browser (Phone)

- **Press-to-talk**: hold mic → release to send
- **Audio decoded in browser** (MediaRecorder → AudioContext.decodeAudioData)
- **POST /api/voz** → returns transcript, text answer, base64 audio
- **~20s end-to-end** (1.6s utterance → 12s STT + 6s LLM + 2.5s TTS on i5-8500T)
- **Transcript echoed** in chat log before answer

### Layout Comparison

| Element | Desktop | Mobile |
|---------|---------|--------|
| Device tiles | Left column (fixed) | Full width, stacked rooms |
| Conversation | Right column | Bottom dock (pull up) |
| Admin menu | Top-right burger | Top-right burger |
| Weather | Top of tiles | In dock panel |
| Mic button | Nav bar + composer | Composer only (nav shortcut) |

## Zigbee Local Control (NEW)

**Problem:** Oven plug on Cloogy Cloud returns 401 on control attempts — API doesn't support actuator writes for PLUG device types.

**Solution:** Add Zigbee2MQTT with a USB coordinator dongle (~€25) for fully local control.

### Quick Setup

```bash
# 1. Hardware: Sonoff ZBDongle-E (CC2652P) ~€25
# 2. Docker services (Mosquitto + Zigbee2MQTT)
# 3. Pair oven plug (IEEE: 0x00124b00023771d1 → friendly_name: forno)
# 4. Add skill_zigbee2mqtt.py (PRIORITY 70)
# 5. Deploy
```

See [`docs/ZIGBEE_INTEGRATION.md`](docs/ZIGBEE_INTEGRATION.md) for full guide.

| Current (Cloogy) | With Zigbee2MQTT |
|------------------|------------------|
| ❌ Control returns 401 | ✅ Local ON/OFF via MQTT |
| ❌ No state feedback | ✅ Real-time power/energy |
| ☁️ Cloud-dependent | 🏠 100% offline |

## Privacy

- **Zero cloud calls** for core operation
- **All processing local**: hotword, STT, LLM, TTS
- **Optional**: SearxNG web search (self-hosted)
- **No telemetry**, no accounts, no API keys required
- **Zigbee local**: No vendor cloud for device control

## Branch Status

| Branch | Status |
|--------|--------|
| `main` | Production (mirrors `origin/main`; formerly `master`) |
| `testing` | Latest features, merged into `main` (GMIF dream, unified brain, sleep button, local Chacon control, admin config editor) |

Recent work on `testing` (all in `main`):
- `feat(skill): control Chacon balcony plug over local UDP (no cloud/AES)` — voice control
- `feat(ui): show the Chacon balcony light in / with a Sala tile and honest state`
- `chore: land in-progress work from parallel sessions` — admin config editor, noise/quiet pipeline, LLM connect timeout, Tasmota path, FlyBrain/GMIF fixes

See `aes/tickets/` for the tracked work items (`T051` Chacon, `T053` Tasmota reflash).

## License

MIT
