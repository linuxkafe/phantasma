"""Which model actually answers, and where that decision came from.

The assistant had three sources for the model name and only one of them did
anything:

    /opt/phantasma/.env             gemma3:4b    honoured
    config.db (the /admin/config)   llama3.1:8b   NOT read
    app_settings (set_setting)      empty         NOT read

So `/admin/config` was a dead mirror for the model names. The owner could save
`gemma3:4b`, see "guardado", and the service kept running whatever was in the
`.env` -- and the page showed `llama3.1:8b` while the deleted model was gone and
`gemma3:4b` was answering.

The comment above `CONFIG_CONTROLS` in admin.py claimed a boot overlay honoured
the config table for exactly this reason. There is no boot overlay: no
`os.environ[...]` write, no `putenv`, no import of admin from `assistant.py`,
`tools.py`, `config.py` or `src/pipeline/`. It described a safeguard that was
never built.

Precedence, and why in this order:

1. `app_settings`  the owner's most recent explicit choice, from the page
2. `.env`          host truth. A host value belongs here, per CLAUDE.md, and
                   it is what survives a lost database
3. `config.py`     dataclass default. A last resort, not a default that quietly
                   wins

Guest settings already work this way -- the page is authoritative and says so
in the code. These three keys were the exception, and the exception was not
documented anywhere an owner would read.

Read per call, not at import, so a change applies to the next message without a
restart. That is what the persona page promises and what the model page did not.
"""

from __future__ import annotations

import logging
import os
from typing import NamedTuple

log = logging.getLogger("phantasma.models")

MODEL_PRIMARY = "OLLAMA_MODEL_PRIMARY"
MODEL_FALLBACK = "OLLAMA_MODEL_FALLBACK"
MODEL_VISION = "OLLAMA_VISION_MODEL"
HOST_PRIMARY = "OLLAMA_HOST_PRIMARY"
HOST_FALLBACK = "OLLAMA_HOST_FALLBACK"

MODEL_KEYS = (MODEL_PRIMARY, MODEL_FALLBACK, MODEL_VISION)
HOST_KEYS = (HOST_PRIMARY, HOST_FALLBACK)

# Last resort only. These are the models measured on 2026-10-03; if the host
# cannot serve them the deploy gate says so, which is a loud failure, rather than
# the assistant answering with something that was uninstalled months ago.
DEFAULTS = {
    MODEL_PRIMARY: "gemma3:4b",
    MODEL_FALLBACK: "gemma3:4b",
    MODEL_VISION: "gemma3:4b",
    HOST_PRIMARY: "http://10.0.0.128:11434",
    HOST_FALLBACK: "http://localhost:11434",
}


class Resolved(NamedTuple):
    value: str
    source: str  # "settings" | "env" | "default"


def _from_settings(key: str) -> str | None:
    try:
        from src.settings_store import get_setting

        got = get_setting(key)
    except Exception as exc:  # noqa: BLE001
        # A read failure must not silence the assistant. It must not be silent
        # either: this is the same shape as the settings_store warning, and the
        # model name is load-bearing.
        log.warning("could not read %s from settings store: %s", key, exc)
        return None
    if got is None:
        return None
    got = str(got).strip()
    return got or None


def _from_env(key: str) -> str | None:
    # Read .env directly, not only os.environ: config.load_dotenv() is called
    # without a path and so depends on the CWD, and the service is started from
    # /opt/phantasma while tooling may run from elsewhere. The file is the host
    # truth and should be read the same way every time.
    for path in (os.path.join("/opt/phantasma", ".env"), ".env"):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, val = line.split("=", 1)
                    if name.strip() == key:
                        val = val.strip().strip('"').strip("'")
                        if val:
                            return val
        except OSError:
            continue
    val = (os.environ.get(key) or "").strip()
    return val or None


def resolve(key: str) -> Resolved:
    """The effective value, and which source it came from."""
    if key not in DEFAULTS:
        raise KeyError(f"{key} is not a model/host key")

    # The guard is HERE, around the call, not inside _from_settings. A test that
    # replaced _from_settings raised straight through, and so would any other
    # wrapper -- which meant a locked database could take down the reply instead
    # of falling back to .env, the exact opposite of the intent.
    try:
        got = _from_settings(key)
    except Exception as exc:  # noqa: BLE001
        # A read failure must not silence the assistant, and must not be silent
        # either: the model name is load-bearing, so this is logged.
        log.warning("could not read %s from settings store: %s", key, exc)
        got = None

    if got and got.strip():
        return Resolved(got.strip(), "settings")

    got = _from_env(key)
    if got:
        return Resolved(got, "env")

    return Resolved(DEFAULTS[key], "default")


def effective() -> dict[str, Resolved]:
    return {key: resolve(key) for key in DEFAULTS}


def as_dict() -> dict[str, str]:
    return {key: res.value for key, res in effective().items()}


def provenance() -> dict[str, str]:
    """key -> source, for the page.

    Shown to the owner so that "the page says one thing and the assistant does
    another" is visible rather than inferred. The whole reason this module
    exists is that three sources disagreed and nothing said so.
    """
    return {key: res.source for key, res in effective().items()}


def write_env_file(key: str, value: str, env_path: str = "/opt/phantasma/.env") -> bool:
    """Update a key in the .env in place, preserving everything else.

    Writes the host file as well as the store, so the two cannot drift into
    disagreeing about what the host must have. Comments, order and unrelated
    keys are preserved: this is an edit of a file someone maintains by hand, not
    a rewrite of it.

    Returns True when the file changed. A read-only .env returns False and is not
    an error -- the store already holds the value and the runtime will use it --
    but it is reported, because a .env that silently stopped tracking the page
    is how the two sources drift again.
    """
    try:
        with open(env_path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError as exc:
        log.warning("could not read %s to update %s: %s", env_path, key, exc)
        return False

    replaced = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (
            not replaced
            and not stripped.startswith("#")
            and "=" in stripped
            and stripped.split("=", 1)[0].strip() == key
        ):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)

    if not replaced:
        out.append(f"{key}={value}")

    try:
        with open(env_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out))
    except OSError as exc:
        log.warning("could not write %s for %s: %s", env_path, key, exc)
        return False
    return True
