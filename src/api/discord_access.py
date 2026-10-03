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

# The default allowlist, and it lives HERE rather than in config.py for a reason
# that cost a deploy to learn.
#
# `config.py` is not part of the deployed product: deploy.sh deliberately leaves
# it out of both SYNC_DIRS and TOP_LEVEL_SYNC, and hard-gates it as
# byte-identical across the trees, because a host value in a dataclass default is
# how the two copies forked for years. The consequence is that config.py is
# FROZEN -- a new setting added there can never reach production by the script,
# and copying it by hand is exactly what the script exists to prevent.
#
# So this rule reads what `/admin/config` already writes. The config page stores
# each value twice and says which one counts: the config-table write is "the
# display mirror", the `set_setting` write is "the source of truth" for the
# running assistant. Reading `settings_store` is therefore not a workaround, it
# is the pattern the page was already built on -- and it takes effect on the
# next message instead of the next boot.
#
# Order: the admin page, then config/env if anyone ever sets one, then this.
DEFAULT_GUEST_SKILLS = "weather,calculator"

def allowed_guest_skills() -> list[str]:
    """The skill names a guest may run, from ``GUEST_SKILLS_ALLOWED``.

    Read on every call, never cached into a module global -- both so an edit on
    ``/admin/config`` takes effect on the very next message, and because a module
    global here would be a second copy of the policy with its own lifetime. The
    question "which one is in force" is the question that turns a permission
    change into an argument.

    Both ``weather`` and ``skill_weather`` are accepted. The loader names a
    skill after its module, so the raw value is ``skill_weather`` -- which is
    noise in a box the owner is meant to type into, and noise in a help string.
    Accepting the short form is friendlier than forcing the owner to learn the
    loader's internal naming.
    """
    raw = _read_owner_setting(
        "GUEST_SKILLS_ALLOWED", DEFAULT_GUEST_SKILLS, empty_is_a_value=True
    )
    out = []
    for part in str(raw).split(","):
        name = part.strip().lower()
        if name.startswith("skill_"):
            name = name[len("skill_"):]
        if name:
            out.append(name)
    return out


def _owner_set(key: str) -> tuple[str | None, bool]:
    """``(value, store_was_readable)`` for the admin page's value of ``key``.

    Split out because three states have to stay apart, and the two previous
    readers could not tell them:

    - the owner set a value, including the empty string
    - the owner never touched this key
    - the store could not be read

    ``None`` means the second, and a readable-but-empty string means the first.
    Collapsing them is what made revocation impossible: emptying the id box
    wrote ``""``, the reader treated that as "unset", and `_owner_list` fell
    back to the `.env` -- so deleting a guest from the page handed the id
    straight back. Verified:

        store='' env=[111]  ->  _owner_guest_ids() == {'111'}
        check(111) -> (True, '')

    The third state matters just as much. A `database is locked` at the moment
    the owner revoked the last skill used to fall through to
    ``DEFAULT_GUEST_SKILLS``, handing every guest weather and calculator. The
    docstring of `_read_owner_setting` states the opposite is intended, so the
    code contradicted its own stated rule. ``check()`` already refuses on a
    broken profile lookup; the POLICY lookup now fails the same way, because a
    permission check that opens the door when the database is unhappy is not a
    permission check.
    """
    try:
        from src.settings_store import get_setting

        stored = get_setting(key, None)
        return (None if stored is None else str(stored)), True
    except Exception as exc:  # noqa: BLE001
        log.error("discord: leitura de %s falhou (%s)", key, exc)
        return None, False


def _read_owner_setting(key: str, default: str, empty_is_a_value: bool = False) -> str:
    """What the owner set on ``/admin/config``, or ``default``.

    The admin page first, because it is what the page writes and it applies
    without a restart. ``config``/env second, for a value set by hand in ``.env``,
    and only when the page has never been used for that key.

    ``empty_is_a_value`` is the difference between "the owner emptied this" and
    "the owner never touched this", and they are not the same. Unticking the last
    guest skill posts no checkbox at all, so the form submits an empty list, and
    an empty list is the owner saying "no". Treating that as unset gives every
    guest the default allowlist at exactly the moment the owner took it away --
    the sort of inversion that only shows up when somebody tries to close a door.

    It is opt-in per key rather than the default because for the other keys an
    empty string is indistinguishable from never being set, and guessing wrong
    there is harmless.
    """
    stored, readable = _owner_set(key)
    if not readable:
        # Fail CLOSED, not open. The owner's intent for this key is known and it
        # is the restrictive one; substituting the default because the database
        # was busy would grant exactly what was taken away, at the moment
        # somebody was relying on it being gone.
        return ""
    if stored is not None and (stored != "" or empty_is_a_value):
        return stored
    from_env = getattr(config, key, None)
    if from_env not in (None, ""):
        # Strings and numbers only. The id lists are the ones `config.py` keeps
        # as `list[int]`, and they never come through here -- `_owner_list` reads
        # `config` via `_ids_in` and the page via `_owner_set`. Passing
        # a list here is what produced the ids "[111" and "222]".
        return str(from_env)
    return default


def _ids_in(raw: Any) -> set[str]:
    if isinstance(raw, (list, tuple, set)):
        parts = [str(x) for x in raw]
    else:
        parts = str(raw or "").split(",")
    return {p.strip() for p in parts if str(p).strip()}


def _owner_list(key: str) -> set[str]:
    """Every id the owner put on ``key``, from BOTH places.

    Union, not first-wins, and the reason is a trap I walked into: the page was
    going to shadow the ``.env``, which means the first time the owner saved it,
    every id he had in ``.env`` and did not retype became disallowed at once --
    with no message, because a refused guest is silent by design. An admin page
    that locks people out as a side effect of using it is worse than one that
    forgets.

    Both lists are the owner's own decision and both outrank a profile field:
    ``/perfil`` lets any signed-in account claim an id, and a claim must never
    hand out a capability the owner did not grant, nor take one away.

    Compared as text because the column is text and a Discord id is a snowflake:
    an int that overflowed, or a form value that arrived padded, must not match by
    accident -- and must not fail to match either.
    """
    stored, readable = _owner_set(key)
    if not readable:
        log.error("discord: %s ilegivel; a lista do dono fica vazia", key)
        return set()
    if stored is not None:
        # The page is authoritative whenever the owner has touched it. It used
        # to be a UNION with the `.env`, which was right for adding an id and
        # wrong for revoking one: emptying the field returned the `.env` list
        # unchanged, so removing a guest from the page did not remove them. A
        # union cannot express "take this away", and revoking is the operation
        # that has to work -- an admin page that locks people out by accident is
        # bad, and one that cannot unlock them at all is worse.
        #
        # The `.env` remains the default for anyone who has never opened the
        # page, so the owner's existing setup keeps working untouched.
        return _ids_in(stored)
    return _ids_in(getattr(config, key, None))


def _owner_guest_ids() -> set[str]:
    return _owner_list("DISCORD_STANDARD_USERS")


def _owner_admin_ids() -> set[str]:
    return _owner_list("DISCORD_ADMIN_USERS")


def _normalise_skill(skill_name: Any) -> str:
    name = str(skill_name or "").strip().lower()
    return name[len("skill_"):] if name.startswith("skill_") else name


# Per-user daily counters: {user_id: {"date": "YYYY-MM-DD", "count": int}}.
# In memory, and deliberately: a restart forgives the quota rather than locking
# the owner out of their own house for the rest of the day. It is a rate limit,
# not an entitlement.
_USER_QUOTAS: dict[Any, dict[str, Any]] = {}


def _daily_limit() -> int:
    """Requests per guest per day, as the owner set it.

    ``0`` means unlimited. It is spelled out because "a limit of zero" reads like
    a bug in a message and a config value, and an owner who sets 0 to mean
    "unlimited" and gets "your limit of 0" has been told nothing.
    """
    raw = _read_owner_setting("DISCORD_DAILY_LLM_LIMIT", "")
    if raw.strip() == "":
        try:
            return int(getattr(config, "DISCORD_DAILY_LLM_LIMIT", 3))
        except (TypeError, ValueError):
            return 3
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        log.warning("discord: DISCORD_DAILY_LLM_LIMIT invalido: %r", raw)
        return 3


def _guest_tier(user_id, prompt_lower: str, matching: Any) -> tuple[bool, str]:
    """The guest tier: an allowlist of skills, and a daily budget for requests.

    Two decisions, and the order is the whole point.

    1. **Does the request land on a skill the guest may not run?** Then refuse.
       ``matching`` is every skill that matched, not the first one, and the test
       is "is any of them outside the list". Naming only the first match would
       be fail-open in a way that is easy to miss: "acende a luz da sala" matches
       ``skill_chacon``, ``skill_tuya`` and ``skill_xiaomi``, and ``execute_skill``
       walks that list until one of them answers, so a guest refused only on the
       first would still be handed to the second.
    2. **Otherwise, charge the budget.** Every request costs one, including the
       ones an allowlisted skill answers.

    That second rule is the owner's call ("contabiliza tudo"), and it is also
    what closes a hole that outlived the obvious one. An earlier version exempted
    any prompt an allowlisted skill merely *matched*: "conta-me uma historia"
    matches ``skill_calculator`` -- which returns ``None`` -- so the answer came
    from the language model, for free, forever, for anyone who typed a hyphen.
    Deciding on "matched" cannot tell "this skill answered" from "this skill
    declined and the LLM picked it up", and the difference is the whole quota.

    ``matching=None`` means the caller could not resolve skills at all, and is
    REFUSED. Reading it as "no skill matched" would be fail-open on the check
    that keeps guests away from the house, and a wiring fault must not be the
    thing that opens it.
    """
    if matching is None:
        log.error("discord: nao foi possivel resolver a skill; pedido recusado")
        return False, "Não consegui verificar o que esse pedido ia tocar."

    allowed = allowed_guest_skills()
    outside = [n for n in matching if _normalise_skill(n) not in allowed]
    if outside:
        names = ", ".join(sorted({_normalise_skill(n) for n in outside}))
        return False, f"Não tens acesso a {names}."

    today = datetime.now().strftime("%Y-%m-%d")
    if user_id not in _USER_QUOTAS or _USER_QUOTAS[user_id]["date"] != today:
        _USER_QUOTAS[user_id] = {"date": today, "count": 0}

    limit = _daily_limit()
    if _USER_QUOTAS[user_id]["count"] < limit:
        _USER_QUOTAS[user_id]["count"] += 1
        return True, ""
    if limit == 0:
        return True, ""
    return (
        False,
        f"Atingiste o teu limite diário de {limit} pedidos. Fica para amanhã.",
    )


def _from_environment(
    user_id, prompt_lower: str, matching: Any
) -> tuple[bool, str] | None:
    """The owner's own lists, or None when this id is not in them."""
    as_text = str(user_id).strip()
    if as_text in _owner_admin_ids():
        return True, ""
    if as_text in _owner_guest_ids():
        # The prompt goes through, or an id the owner put in DISCORD_STANDARD_USERS
        # would spend its quota asking about the weather -- which is the one
        # question the free tier exists for. A first version of this function
        # passed "" and quietly taxed every allowed question.
        return _guest_tier(user_id, prompt_lower, matching)
    return None


def check(user_id, prompt_lower: str, matching: Any) -> tuple[bool, str]:
    """``(allowed, message)`` for a Discord user id, a prompt and the skill it hits.

    ``matching`` is the list of skills that *would* run, resolved before anything
    is spent. An empty list means general conversation. ``None`` means the caller
    could not resolve at all, and is refused -- see ``_guest_tier``.

    It is a required argument, and deliberately. With a default, the callers
    written before this rule existed kept working by passing nothing, and a
    permission check whose failure mode is "you forgot an argument and nobody
    noticed" is not a permission check.

    ``prompt_lower`` survives as a parameter and is no longer read to decide
    anything. The previous version exempted any prompt containing ``+``, ``-``,
    ``*`` or ``/`` -- which meant "conta-me uma historia" was free forever and
    "conta me uma historia" stopped after three. The text of a request says
    nothing about whether it is cheap; which skill it lands on does.

    A Discord id is a snowflake and the column is text, so the comparison is
    made as text: an int that overflowed or a client that sent a string with
    padding must not be able to produce a match by accident.
    """
    decided = _from_environment(user_id, prompt_lower, matching)
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
        return _guest_tier(user_id, prompt_lower, matching)
    return False, "Acesso negado."


def reset_quotas() -> None:
    """Test hook: forget every daily counter."""
    _USER_QUOTAS.clear()
