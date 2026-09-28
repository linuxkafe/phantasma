"""Durable, owner-editable settings.

Three things the owner must be able to change without editing code or a file
and redeploying: the persona, the FlyBrain weight of each reaction emoji, and
the knowledge itself. They all live here so /admin has one storage contract
instead of three ad-hoc ones.

The persona used to come from prompts/system.txt, read once at import. That
made it unchangeable at runtime, and it was also never actually reaching the
model: _respond_with_llm called the Ollama SDK with a single user message. A
prompt that cannot be seen in the logs and cannot be changed from a screen is
not a configuration, it is a constant that happens to be spelled out.

Defaults are not stored, only overrides. Delete the override and the previous
behaviour returns; storing a copy of the default would mean editing the default
later had no effect, which is how a config drifts away from the code.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import Any, Optional

import config

_LOCK = threading.Lock()
logger = logging.getLogger("phantasma.settings")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by  TEXT
)
"""

# Keys are stable strings so a typo in a route fails loudly rather than
# silently writing an orphan row nothing ever reads.
PERSONA_KEY = "persona.text"
REACTION_WEIGHTS_KEY = "reactions.weights"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    conn.commit()


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read one override, or ``default`` when the owner never set one."""
    try:
        with _LOCK:
            conn = _connect()
            try:
                _ensure_schema(conn)
                row = conn.execute(
                    "SELECT value FROM app_settings WHERE key = ?", (key,)
                ).fetchone()
                return row["value"] if row else default
            finally:
                conn.close()
    except sqlite3.Error as e:
        # A read failure must not take the assistant down -- but it must not be
        # invisible either. Silently returning the default made a read-only
        # database look like a working admin page showing the original persona.
        logging.getLogger("phantasma.settings").warning(
            f"settings read failed for {key!r}, using default: {e}"
        )
        return default


def set_setting(key: str, value: str, updated_by: Optional[str] = None) -> None:
    """Persist one override. Raises on failure so the admin can say so."""
    with _LOCK:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO app_settings (key, value, updated_at, updated_by) "
                "VALUES (?, ?, datetime('now'), ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at, "
                "updated_by=excluded.updated_by",
                (key, value, updated_by),
            )
            conn.commit()
        finally:
            conn.close()


def clear_setting(key: str) -> None:
    """Remove an override so the built-in default applies again."""
    with _LOCK:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            conn.commit()
        finally:
            conn.close()


def set_json(key: str, payload: Any, updated_by: Optional[str] = None) -> None:
    set_setting(key, json.dumps(payload, ensure_ascii=False), updated_by=updated_by)


def get_json(key: str, default: Any = None) -> Any:
    raw = get_setting(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        # A hand-edited row that is not valid JSON is a mistake in the admin,
        # not a reason to refuse the assistant a persona.
        return default


# --- persona ---------------------------------------------------------------


def default_persona() -> str:
    """The persona the code ships with: file first, then the loaded config."""
    for candidate in (
        getattr(getattr(config, "llm", None), "system_prompt", ""),
        getattr(config, "SYSTEM_PROMPT", ""),
    ):
        if candidate and candidate.strip():
            return candidate
    try:
        with open("prompts/system.txt", encoding="utf-8") as fh:
            text = fh.read()
        if text.strip():
            return text
    except OSError:
        pass
    return ""


def get_persona() -> str:
    """The persona in force right now.

    Read per request, not cached at import, so a change in /admin takes effect
    on the next message without a restart.
    """
    return get_setting(PERSONA_KEY) or default_persona()


def set_persona(text: str, updated_by: Optional[str] = None) -> None:
    if not (text or "").strip():
        raise ValueError("A persona não pode ficar vazia.")
    set_setting(PERSONA_KEY, text, updated_by=updated_by)


def reset_persona() -> None:
    clear_setting(PERSONA_KEY)


def persona_is_overridden() -> bool:
    return get_setting(PERSONA_KEY) is not None


# --- reaction weights ------------------------------------------------------

# The shipped FlyBrain rewards. Kept as the default so a reset returns here
# and so a future code change actually reaches the assistant.
DEFAULT_REACTION_WEIGHTS: dict[str, float] = {
    "\U0001f44d": 1.0,  # thumbs up
    "\U0001f44e": -1.0,  # thumbs down
    "❤️": 1.0,  # heart
    "\U0001f525": 1.0,  # fire
    "\U0001f621": -1.0,  # angry
    "\U0001f622": -0.5,  # cry
}


def get_reaction_weights() -> dict[str, float]:
    """Owner-set weights, falling back per emoji to the shipped default.

    Merged rather than replaced so adding an emoji to the code does not
    silently lose its weight on an install that already has an override.
    """
    weights = dict(DEFAULT_REACTION_WEIGHTS)
    stored = get_json(REACTION_WEIGHTS_KEY, {}) or {}
    if isinstance(stored, dict):
        for emoji, value in stored.items():
            # Filter on read as well as on write. Rows written before the
            # write-side check existed are still in the owner's database, and
            # the caller of this function is what renders the page.
            if str(emoji) not in DEFAULT_REACTION_WEIGHTS:
                continue
            try:
                weights[str(emoji)] = float(value)
            except (TypeError, ValueError):
                # A row that is not a number is dropped, not fatal: one bad
                # emoji should not silence the other five.
                continue
    return weights


def set_reaction_weights(
    weights: dict[str, float], updated_by: Optional[str] = None
) -> None:
    """Store reaction weights, keeping only keys that are actual emojis.

    This saved {"0": 1.0, "1": -0.5, ... "11": 0.5} and /admin/config rendered
    eighteen weight rows: six emojis and twelve digits. The digits came from
    another form on the same route whose inputs were named w_0..w_11, and the
    collector -- {k[2:]: v for k in request.form if k.startswith("w_")} --
    picked them up because the route accepts every POST. A weight is keyed by
    an emoji, so a key that is not one is a programming error, not a setting.
    """
    clean: dict[str, float] = {}
    rejected: list[str] = []
    for emoji, value in weights.items():
        key = str(emoji)
        if key not in DEFAULT_REACTION_WEIGHTS:
            rejected.append(key)
            continue
        try:
            clean[key] = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Peso invalido para {emoji!r}: {value!r}")
    if rejected:
        logger.warning("Ignoring non-emoji reaction weight keys: %s", rejected[:12])
    if not clean:
        raise ValueError("Nenhum peso de reaccao valido foi fornecido.")
    set_json(REACTION_WEIGHTS_KEY, clean, updated_by=updated_by)
