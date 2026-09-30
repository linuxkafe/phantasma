"""Who may talk to the house from Discord, and how much.

Extracted from ``skills/skill_discord.py``, where the decision lived, because
there it could not be tested.

``skill_discord`` imports ``discord.py`` and builds a live ``Client`` at import
time. Every test of the access rule therefore had to either install a 15 MB SDK
in the development venv (it is only installed in production's) or hand-build a
fake that had to grow a new attribute for every line of the skill it imported
past -- ``Intents.default()``, then a ``Client`` that takes keywords, then
``Client().event``. Each of those fakes is a place where the test quietly stops
testing the real thing, and a test that needs an ever-growing fake to run is
usually a test pointed at the wrong module.

The rule itself needs ``config`` and the user store. Nothing else. So it lives
here, imports nothing heavy, and the skill keeps its name by delegating.

The precedence, and why:

1. **The environment lists.** ``DISCORD_ADMIN_USERS`` and
   ``DISCORD_STANDARD_USERS`` are the owner's deliberate, out-of-band decision.
   A profile field must not be able to take a capability away from them, so
   they are consulted first and nothing here can override them.
2. **The profile.** A signed-in user may claim their own Discord id from
   ``/perfil``, and the WEB ROLE of that account decides what the id may do:
   ``admin`` grants everything, ``user`` grants the same standard access the
   environment list would. This is what lets a second person in the household
   join without editing a file only the owner can open, and without the owner
   having to know anybody's id.
3. **Refused.**

This is an authorisation change, so the interesting cases are the wrong ones:
an id claimed by two accounts resolves to no access rather than to a guess, and
a deactivated account keeps its id and loses its capability.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import config

log = logging.getLogger(__name__)

# Skills a standard user may use without spending quota. Weather and the
# calculator: questions about the house's own state, which is the whole point of
# the bot, as opposed to asking the language model something.
ALLOWED_SKILL_KEYWORDS = [
    # Meteorologia
    "tempo",
    "clima",
    "meteorologia",
    "previsão",
    "vai chover",
    "qualidade do ar",
    # Calculadora
    "quanto é",
    "calcula",
    "a dividir",
    "vezes",
    "somado",
    "subtraído",
    "+",
    "-",
    "*",
    "/",
]

# Per-user daily counters: {user_id: {"date": "YYYY-MM-DD", "count": int}}.
# In memory, and deliberately: a restart forgives the quota rather than locking
# the owner out of their own house for the rest of the day. It is a rate limit,
# not an entitlement.
_USER_QUOTAS: dict[Any, dict[str, Any]] = {}


def _standard_quota(user_id, prompt_lower: str) -> tuple[bool, str]:
    """The standard tier: some skills are free, the rest are rationed daily."""
    if any(keyword in prompt_lower for keyword in ALLOWED_SKILL_KEYWORDS):
        return True, ""

    today = datetime.now().strftime("%Y-%m-%d")
    if user_id not in _USER_QUOTAS or _USER_QUOTAS[user_id]["date"] != today:
        _USER_QUOTAS[user_id] = {"date": today, "count": 0}

    limit = getattr(config, "DISCORD_DAILY_LLM_LIMIT", 3)
    if _USER_QUOTAS[user_id]["count"] < limit:
        _USER_QUOTAS[user_id]["count"] += 1
        return True, ""
    return (
        False,
        f"Atingiste o teu limite diário de {limit} perguntas ao cérebro do "
        f"Phantasma (as ferramentas da casa continuam a funcionar).",
    )


def _from_environment(user_id, prompt_lower: str) -> tuple[bool, str] | None:
    """The owner's own lists, or None when this id is not in them."""
    admins = getattr(config, "DISCORD_ADMIN_USERS", None)
    if admins and user_id in admins:
        return True, ""
    standard = getattr(config, "DISCORD_STANDARD_USERS", None)
    if standard and user_id in standard:
        # The prompt goes through, or an id the owner put in DISCORD_STANDARD_USERS
        # would spend its quota asking about the weather -- which is the one
        # question the free tier exists for. A first version of this function
        # passed "" and quietly taxed every allowed question.
        return _standard_quota(user_id, prompt_lower)
    return None


def check(user_id, prompt_lower: str) -> tuple[bool, str]:
    """``(allowed, message)`` for a Discord user id and a lowercased prompt.

    A Discord id is a snowflake and the column is text, so the comparison is
    made as text: an int that overflowed or a client that sent a string with
    padding must not be able to produce a match by accident.
    """
    decided = _from_environment(user_id, prompt_lower)
    if decided is not None:
        return decided

    try:
        from src.api import auth_store

        role = auth_store.discord_role_for(user_id)
    except Exception as exc:  # noqa: BLE001
        # A broken lookup must REFUSE, never allow. The failure mode of the
        # other choice is a house that opens itself to whoever asks while the
        # database is unhappy.
        log.warning("discord: perfil indisponivel: %s", exc)
        return False, "Acesso negado."

    if role == "admin":
        return True, ""
    if role:
        return _standard_quota(user_id, prompt_lower)
    return False, "Acesso negado."


def reset_quotas() -> None:
    """Test hook: forget every daily counter."""
    _USER_QUOTAS.clear()
