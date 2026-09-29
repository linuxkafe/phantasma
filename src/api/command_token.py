"""Bearer tokens for the command endpoints.

Why this exists
---------------
The owner wants `/` behind a login, and wants other applications (the Android
companion, Home Assistant, a shell script) to be able to send commands without
a browser session. Those are two different callers with two different risk
profiles, so they get two different mechanisms:

* a **browser** gets a session cookie from the existing password flow;
* a **program** presents `Authorization: Bearer <token>`.

The design rule that matters
----------------------------
The token is not a session. It is compared in constant time, it is never
logged, and it grants exactly one thing: sending a command. It cannot read the
memory graph, list users, or touch the admin surface. A leaked token is a
leaked light switch, not a leaked home.

Threat model, stated plainly
----------------------------
The service listens on 0.0.0.0:5000 and is reachable from the LAN. A token in
a `.env` or an Android app is a bearer secret: whoever holds it can send
commands. Mitigations that are actually implemented here:

* constant-time comparison, so a token cannot be recovered byte by byte;
* the token is only accepted from a header, never from a query string, because
  query strings end up in access logs and browser history;
* every accepted use is logged at INFO without the token value;
* an empty or unset token disables the whole feature rather than falling open.

Explicitly NOT implemented, and why:

* No token rotation or expiry. A rotation scheme needs somewhere to store
  several tokens, and a single-owner home assistant does not need it yet. It
  should be added together with a store, not faked with a timestamp in the
  secret itself.
* No per-token scope. One token, one capability. When a second capability
  appears, the token needs a scope field and a real store before it grows.
"""

from __future__ import annotations

import hmac
import logging
import os

logger = logging.getLogger("phantasma.auth")

ENV_TOKEN = "PHANTASMA_COMMAND_TOKEN"


def configured_token() -> str:
    """The configured command token, or '' when unset.

    Read from the environment on every call rather than cached at import: the
    admin config editor can write a value, and a token that needs a restart to
    take effect is a support question waiting to happen.
    """
    return (os.getenv(ENV_TOKEN) or "").strip()


def enabled() -> bool:
    """True when a token is configured.

    Unset means the command endpoints keep working as they do now. That is a
    deliberate choice for a single-owner install on a LAN: turning the feature
    on by default would break every existing client, including the Android app,
    and shipping a default token would be worse than shipping none.
    """
    return bool(configured_token())


def verify(token: str | None) -> bool:
    """True when `token` is the configured one.

    Constant-time via `hmac.compare_digest`, which is what makes this safe to
    expose to a network: a naive `==` returns as soon as it finds a difference,
    so an attacker learns the correct prefix one byte at a time.
    """
    expected = configured_token()
    if not expected or not token:
        return False
    return hmac.compare_digest(token, expected)


def check_header(header_value: str | None) -> bool:
    """Validate an `Authorization: Bearer <token>` header.

    Only the Bearer scheme is accepted. `?token=` in a query string is
    deliberately not supported: those land in access logs and in the browser's
    history, and a secret that gets written down by the infrastructure is a
    secret that leaks.
    """
    if not header_value:
        return False
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    if verify(parts[1].strip()):
        return True
    logger.warning("Command token presented but rejected (from request)")
    return False


def describe() -> dict:
    """Operational description, for /api/health and for tests.

    Reports whether a token is set, never what it is.
    """
    return {
        "enabled": enabled(),
        "env_var": ENV_TOKEN,
        "scheme": "Authorization: Bearer <token>",
        "scope": "command endpoints only; not the admin surface",
        "query_string_tokens": "not accepted (they would be logged)",
    }
