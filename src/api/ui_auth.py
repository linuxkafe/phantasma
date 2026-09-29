"""Authentication for the voice UI at `/`.

Why this exists
---------------
`/` served the whole house: the device list, the tiles that switch the lights,
and the chat that runs commands. It was open to anything that could reach port
5000, and the service is reachable from the LAN and, through a router
port-forward, from outside. The admin surface was already behind login; `/` was
not, which made the *admin* login a decoration: the same box served the lights
to anyone who asked.

Two callers, two mechanisms
---------------------------
A **browser** logs in with a password and gets a session cookie. A **program**
sends `Authorization: Bearer <token>` (see command_token.py) and needs no
session. Neither is a fallback for the other, and a token is never accepted as
a cookie.

Design decisions that are load-bearing
--------------------------------------

* **The loopback bypass does NOT open `/`.** localauth.bypass_identity grants
  *admin* to any local process. `/` is the house-control surface, and extending
  an admin bypass to it would let anything running as any user on this host --
  and anything that can be induced to make a request, including an SSRF in a
  dependency -- switch the lights. The bypass stays where it is, on /admin/*.
  If the owner wants `/` open locally, that is a deliberate configuration, not
  a side effect of how the admin bypass happens to work.
* **Reads stay open; actions do not.** /get_devices and /api/devices feed the
  tiles. Locking them means a logged-in browser renders an empty device list,
  and a list of device names is not worth breaking the UI over. The endpoints
  that ACT are gated separately, in routes.py, by the command token.
* **Rate limited before any work**, for the same reason the admin login is: an
  unthrottled password endpoint is a free oracle and a free spam vector.
* **The failure is identical for an unknown address and a wrong password**, and
  the bcrypt comparison runs either way, so neither the message nor the timing
  reveals which addresses exist.
* **No password is logged, and the session is cleared on login** so a session
  fixed before the login cannot be replayed after it.
* **Post-login redirect is restricted to local paths.** An absolute URL there
  would be an open redirect on a login form, which is a phishing tool rather
  than a convenience.

What is deliberately NOT here
----------------------------
No registration and no self-service signup: a house-control page that lets you
create the account that controls it is a worse design than an admin creating
one. No password reset by e-mail: the admin area's OTP flow is the recovery
path, and duplicating it here would be a second, weaker one.
"""

from __future__ import annotations

import logging
from functools import wraps

from flask import redirect, request, session, url_for

logger = logging.getLogger("phantasma.auth")

SESSION_KEY = "ui_user"
# The endpoint is registered with `add_url_rule('/login', 'ui_login', ...)`,
# which creates the name verbatim -- there is no Blueprint to add a "ui."
# prefix, so naming it "ui.login" here would 500 every unauthenticated visit
# with a routing BuildError.
UI_LOGIN_ROUTE = "ui_login"
NEXT_SESSION_KEY = "ui_login_next"

# Compared against when the address does not exist, so a wrong address costs
# the same wall-clock time as a right one. A real bcrypt hash of a known
# throwaway string; the password it encodes is never used for anything.
_TIMING_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEe.4cCPXVF9v5p8Gm4Ck0/2Z0m1lPM1T5W"


def _user_row(email: str) -> dict | None:
    """The users row for an address, or None. Never raises."""
    from src.api import admin as admin_mod

    if not email or not email.strip():
        return None
    try:
        conn = admin_mod.get_db_connection()
    except Exception:  # noqa: BLE001 - a missing store must not 500 the form
        logger.exception("ui auth: user store unavailable")
        return None
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.strip(),)
        ).fetchone()
        return dict(row) if row else None
    except Exception:  # noqa: BLE001
        logger.exception("ui auth: user lookup failed")
        return None
    finally:
        conn.close()


def current_user() -> dict | None:
    """The signed-in UI user, or None.

    Re-resolved from the store on every call rather than trusted from the
    cookie: a session naming an address that was since deleted must stop
    working, and a deleted account is a revocation the owner expects to take
    effect immediately.
    """
    email = session.get(SESSION_KEY)
    if not email:
        return None
    user = _user_row(email)
    if user is None:
        session.pop(SESSION_KEY, None)
    return user


def is_authenticated() -> bool:
    return current_user() is not None


def is_admin() -> bool:
    user = current_user()
    return bool(user and user.get("role") == "admin")


def authenticate(email: str, password: str) -> bool:
    """Check a password. Identical result and timing for every failure."""
    from src.api import admin as admin_mod

    user = _user_row(email)
    stored = (user or {}).get("password_hash")
    if not stored:
        # Burn the time a real check would take, so a missing address is not
        # detectable by response time.
        try:
            admin_mod.verify_password(_TIMING_HASH, password or "")
        except Exception:  # noqa: BLE001 - timing only, never fatal
            pass
        return False

    try:
        if not admin_mod.verify_password(stored, password or ""):
            return False
    except Exception:  # noqa: BLE001 - a bad hash is a rejection, not a 500
        logger.exception("ui auth: password verification raised")
        return False
    return bool((user or {}).get("is_active", 1))


def login(email: str, password: str) -> dict | None:
    """Sign a user in and return their row, or None. Rate limiting is the
    caller's job, before this is reached."""
    if not authenticate(email, password):
        return None
    user = _user_row(email)
    if user is None:
        return None
    session.clear()  # a pre-login session must not survive as a valid one
    session[SESSION_KEY] = user["email"]
    session.permanent = True
    logger.info("ui auth: signed in %s (role=%s)", user["email"], user.get("role"))
    return user


def logout() -> None:
    session.pop(SESSION_KEY, None)
    session.pop(NEXT_SESSION_KEY, None)


def _safe_local_path(path: str | None) -> str | None:
    """A local path, or None.

    `//evil.example` is protocol-relative and lands on another host, so a plain
    `startswith("/")` check is not enough on a login form.
    """
    if not path or not isinstance(path, str):
        return None
    if not path.startswith("/") or path.startswith("//"):
        return None
    return path


def remember_next(path: str | None) -> None:
    safe = _safe_local_path(path)
    if safe:
        session[NEXT_SESSION_KEY] = safe


def take_next() -> str:
    return _safe_local_path(session.pop(NEXT_SESSION_KEY, None)) or "/"


def login_required(view):
    """Protect a UI route, sending the browser to the login form first."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if is_authenticated():
            return view(*args, **kwargs)
        remember_next(request.path)
        # `next=` rather than a bare redirect so the login form knows where the
        # browser was heading. The endpoint is addressed by name; if it is not
        # registered (a trimmed-down app, a unit fixture), fall back to a plain
        # 401 rather than letting url_for raise a BuildError and turn "please
        # log in" into a 500.
        try:
            return redirect(url_for(UI_LOGIN_ROUTE, next=request.path))
        except Exception:  # noqa: BLE001
            logger.warning("ui auth: login route not registered; returning 401")
            return ("401 Unauthorized", 401)

    return wrapped
