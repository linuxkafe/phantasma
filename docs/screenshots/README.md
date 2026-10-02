# Screenshots

Captured with Playwright against a **development** server (isolated databases,
one memory), never against `:5000` — that is `/opt/phantasma`, production, a
separate copy. See `aes/tickets/T058-barra-de-topo-unica-admin-brain.md`.

## What is here

| File | View | Viewport |
|------|------|----------|
| `admin-brain-desktop.png` | `/admin/brain`, one top line, graph panel | 1440×900 |
| `admin-brain-mobile.png` | `/admin/brain`, one top line | 375×667 |
| `memory-3d.png` | `/memory/3d` standalone, target of "Ecrã inteiro" | 1440×900 |

## What is missing, and why

**The voice UI at `/` is not here.** `/` refuses the loopback bypass on purpose
(`skills/skill_ui.py:791`: that bypass grants ADMIN to any local process, and
extending it to the light switches would let anything on the host turn the
lights on), and `/login` needs a password plus a device code delivered by e-mail.
There is no way to photograph it without someone's credentials, and working
around the gate to produce a README picture would be the wrong trade.

Still to capture, once someone can open `/` in a normal session:

- [ ] Desktop: device tiles + conversation
- [ ] Mobile: device strip with rooms stacked
- [ ] Mobile: dock closed
- [ ] Mobile: dock open (conversation panel)
- [ ] Mobile: burger menu open
- [ ] Mobile: voice composer with mic button

**`/admin/brain` with the "Tudo" drawer open** was captured and then thrown
away: this container has no emoji font (`fc-list | grep -c emoji` → 0), so the
🌙 on the "Dormir e Sonhar" button renders as a tofu box and the screenshot
would misrepresent the UI. Re-take it on a host with a colour emoji font.

## Reproducing

The dev server used is the same construction as `tests/test_brain_top_line.py`:
`create_app()` from `src.api.routes` against throwaway SQLite files built from
`tests/schema.sql`, with `_current_user_data` / `_current_user` / `_bypass_or_none`
forced to an admin identity, served by `werkzeug.serving.make_server` on
127.0.0.1. Then Playwright navigates and screenshots.

Do not point a capture at `:5000`. That is `/opt/phantasma/assistant.py` — a
separate copy of production — so a screenshot taken there documents whatever was
last deployed, not the tree you are editing.

Three traps, all hit while producing these:

1. **The 3D graph needs two things that are not in git** —
   `public/vendor/{three.module.js,OrbitControls.js,mermaid.min.js}` and
   `public/static/mermaid_graph.mjs`. They exist in `/opt/phantasma/public/`
   and nowhere else. A fresh clone renders an empty canvas and says
   "a carregar o grafo..." forever. Recorded in `docs/ROADMAP.md`.
2. **Restart the dev server after every template edit.** `render_template_string`
   reads a module-level constant, so a running process keeps serving the
   template as it was at import. This produced a mobile screenshot of a layout
   that no longer existed — a screenshot is evidence, and stale evidence is
   worse than none.
3. **Wait for the graph.** The canvas is built asynchronously; ~9s here.

## CI

`make test-mobile` and `tests/test_brain_top_line.py` validate the layout
programmatically. No screenshot is needed for a gate — a screenshot is
documentation, and a gate that compares pixels fails for reasons that have
nothing to do with the code.