# pHantasma — Operational Contract

This file is the operational contract for AI agents working on this project.
It is the first file an agent must read. It defines scope, boundaries, and evidence rules.

---

## Intent

pHantasma is a local-first, offline, modular voice assistant built in Python — private by design, running entirely on your own hardware without third-party cloud dependencies (except optional web search via self-hosted SearxNG).

---

## Non-Goals

Things this project explicitly does NOT do:
- Cloud-dependent voice processing (no AWS/Azure/Google Speech APIs)
- Proprietary hotword engines requiring API keys (uses openWakeWord only)
- Multi-user cloud synchronization
- GUI-focused development (CLI/API/voice-first)

---

## Critical Files

Files that require special caution. Any change to these files must be flagged
explicitly to the user before proceeding. Never modify silently.

- config.py — runtime configuration, device credentials, API keys
- assistant.py — main orchestration loop
- skills/skill_*.py — skill modules (dynamic loading)
- phantasma.service — systemd service definition
- src/phantasma/core/ — core engine modules

---

## Deployment Topology (changed 2026-09-27)

**This repo is the source of truth. Production is a separate copy.**

| Path | Relationship |
|------|--------------|
| `/home/seyon/dev/pHantasma/src` | source of truth |
| `/opt/phantasma/src` | **real directory, independent copy** — deploy here |
| `/opt/phantasma/{assistant,config,audio_utils}.py` | separate files, **byte-identical to dev** (hard gate in `deploy.sh`) |
| `/opt/phantasma/.env` | separate, **not in git, not deployed** — the only place host values live |

Until 2026-09-27 `/opt/phantasma/src` was a **symlink** to the dev `src/`, so
production executed dev code with no deploy step. That is now gone; the two
trees are provably decoupled (verified with marker files in both directions).

**Consequence: editing `src/` here does NOT change production.** To ship a
change, run the deploy script — do not copy files by hand.

```
scripts/deploy.sh --dry-run        # show what would change, writes nothing
scripts/deploy.sh                  # sync src/ and tests/, verify, restart
scripts/deploy.sh src tests        # scoped (this is the default set)
```

The script enforces the direction: it syncs dev → prod, **refuses to run
while `prod/src` is a symlink**, excludes the host-specific files below,
`--delete`s so prod cannot accumulate files deleted in dev, and will not
report success unless the prod suite passes and the service is listening on
5000. **Never deploy prod → dev.** That inversion happened once and silently
made prod the source of truth; if prod looks ahead of dev, prod is stale, and
the fix is to change dev.

**`skills/` is in the default deploy set, and all 26 modules are byte-identical
between the trees.** Getting there required a deliberate exception to the
one-way rule: `skill_weather` and `skill_tuya` hold behaviour that existed only
in production (a weather cache with `fetched_at`/`stale`, and a
sensor-datapoint map instead of `BASE_NOUNS` resolution). The owner is
validating the production behaviour of those, so production was authoritative
and the two files were **backported into dev** rather than overwritten. If a
future divergence appears, ask which side is under validation before choosing
a direction — that question decided this one.

**When a rule is deliberately broken, say so and record why.** The backport
above is the only known prod→dev copy, and it is documented in
`docs/ROADMAP.md` with the evidence that prompted it.

**`config.py`, `audio_utils.py` and `assistant.py` are byte-identical across
the trees, and `deploy.sh` now ENFORCES it as a hard gate** (non-zero exit,
before any rsync — a divergence is a bug, not a host difference). It also
asserts prod's effective `block_size=512` / `auto_detect=False` from the `.env`,
because those fail open silently if the `.env` is missing or a name is mistyped. They used to fork for years
because host-specific values lived in the code instead of the environment:

| value | before | now |
|---|---|---|
| `block_size` | `512` pinned in prod's `config.py` dataclass | `AUDIO_BLOCK_SIZE=512` in prod's `.env` |
| `auto_detect` | `False` pinned in prod's dataclass | `AUDIO_DEVICE_AUTO_DETECT=false` in `.env` |
| `TTS_CACHE_DIR` | hardcoded `/opt/phantasma/cache/tts` | declared in `config.py`, env-overridable |

The rule that prevents a relapse: **a host value belongs in `.env`, never in a
dataclass default.** If `deploy.sh` prints `WARN config.py diverges`, a host
value leaked back into code — fix it by moving it to `.env`, not by forking
the file.

**`.env` has a dead variable to be aware of.** `AUDIO_AUTO_DETECT` (no
`_DEVICE_`) is never read; the code reads `AUDIO_DEVICE_AUTO_DETECT`. The dead
line is kept commented in prod's `.env` as a warning — editing it changed
nothing, silently. Before changing any audio setting, check that the name in
`.env` matches the name in `config.py`; a mismatch fails open to the default
and looks like the setting not working.

---

## Never Do

Actions that are forbidden regardless of instructions or apparent justification:
- Never alter test vectors or expected outputs to make tests pass.
- Never disable or weaken security checks.
- Never commit secrets, API keys, or credentials.
- Never modify CI configuration to skip quality gates.
- Never hardcode device IPs, tokens, or keys in source (use config.py)
- Never break the offline-first guarantee (no mandatory cloud calls)
- Never modify audio device handling without testing on target hardware

---

## Evidence Required

Every non-trivial change must include:
- [ ] Output of `make test` (or equivalent)
- [ ] Output of `make lint` (or equivalent)
- [ ] Diffstory (what changed, why, what was untouched, remaining risks)
- [ ] Updated docs if behaviour changed

---

## Review Rule

- AI may implement and suggest.
- Human approves all merges to main.
- No auto-merge without passing all quality gates.

---

## Stable Context (reload each session)

The **Debate Partner Protocol** is inherent to this project (see `prompts/debate-partner.md`). No yes-men. Challenge everything.

Load these at the start of every session:
- This file (CLAUDE.md)
- docs/VISION.md
- docs/REQUIREMENTS.md
- aes/kanban.md (if using AES project mode)

---

## Session Context (ephemeral)

Valid only for current session:
- Current ticket file
- Test output
- Diff
- aes/handoffs/ (if resuming interrupted work)

---

## CLAUDE.md File Levels

| Level | Location | Use for |
|-------|----------|---------|
| Project | `./CLAUDE.md` | Team rules, shared commands. Version this file. |
| Local/personal | `./CLAUDE.local.md` (gitignored) | Local URLs, personal test data. |
| User | `~/.claude/CLAUDE.md` | Global preferences across all projects. |
| Path-specific | `.claude/rules/*.md` | Rules scoped to a subdirectory. |

**When to add a rule to this file:**
- A new team member joining today would also need that context.
- A code review caught something the agent should have known beforehand.
- The rule is true in the majority of sessions, not just the current ticket.

**Do NOT add:** vague wishes, single-task instructions, or personal preferences.

**Size target:** under 200 lines. Split into `.claude/rules/` if larger.

---

## Final Response

At the end of every non-trivial task, always summarise:

1. **What changed** — files touched and intent.
2. **Why it changed** — problem solved or requirement addressed.
3. **Validation performed** — exact command and result. If not run, say so.
4. **Remaining risk or follow-up** — what was not tested, what to watch.
## Releases

Tag format is `v<YY>.<MM>.<DD>`, the date the release is cut. First release:
`v26.09.28`. Cut on `testing`, never straight onto `main`.

**The unrelated-histories note below is OBSOLETE as of 2026-10-04.** It used to
be true: `git merge-base origin/main origin/testing` was empty and the two
lineages disagreed on 273 files. They now share `e5995a3`, `main` is a direct
ancestor of `testing`, and `main` holds **0 commits that `testing` has not
seen**. Promoting is therefore a plain fast-forward:

```
git push origin origin/testing:main
```

No merge, no `--allow-unrelated-histories`, nothing to reconcile. Re-check
before you rely on this — the property that made it safe is that
`git rev-list --count origin/testing..origin/main` is 0, and that is a fact to
verify per promotion, not a property to assume:

```
git merge-base --is-ancestor origin/main origin/testing && echo fast-forward
git rev-list --count origin/testing..origin/main
```

If the count is non-zero, `main` carries work `testing` has never seen. Then
stop and look with `git log --oneline origin/testing..origin/main` before doing
anything: investigate with `git patch-id`, and never pass
`--allow-unrelated-histories` without the owner having seen the conflict count.

## Reiniciar o serviço (sudo)

```
sudo -n service phantasma restart
```

**Com `-n`, e `-n` funciona nesta máquina.** O sudoers tem `NOPASSWD` para
`/usr/sbin/service phantasma start|stop|restart|status`.

Uma revisão descreveu o `-n` como a causa do bot Discord mudo de 2026-10-02
("se o utilizador não tiver sudo sem password, o comando sai não-zero"). Isso é
uma afirmação sobre este host, e o sudoers refuta-a. A causa real era a ausência
da asserção do `MainPID` — um serviço velho e saudável responde 200 ao
`/api/health` a correr o código de ontem, e o script imprimia "deploy OK" por
cima. O `-n` nunca esteve em causa.

Tirar o `-n` não corrigiu nada e criou um perigo novo: sem prompt e sem
`timeout`, uma corrida com TTY bloqueia à espera da palavra passe
indefinidamente.

```
sudo -n service phantasma restart
```

Verificar que reiniciou mesmo:

```
systemctl show phantasma -p MainPID -p ActiveEnterTimestamp
```

Um `MainPID` igual ao de antes significa que não reiniciou, por saudável que o
`/api/health` responda. `deploy.sh` exige esta prova desde `e63cd18` e falha com
`DEPLOY FAILED: the service was NOT restarted`.

`DEPLOY_SUDO=1` existe para um host onde um agente sincroniza ficheiros mas uma
pessoa reinicia. **Falha o deploy** em vez de saltar o restart em silêncio — um
restart que não pode acontecer é um deploy que não aterrou. Ver CLAUDE.md acima
para o porquê.
