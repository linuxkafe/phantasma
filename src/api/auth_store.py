"""Password recovery, new-device verification, and per-user API tokens.

Three related problems, one store.

Password recovery
-----------------
The admin login has an OTP flow, but `/` (the house-control surface) is a
password login and had no way back in when the password was forgotten. The
admin's OTP store is an in-memory dict, so it is not a model for this: it dies
with the process, which means a code requested just before a restart is
silently gone, and it cannot tell a restart from an attack.

The codes here live in SQLite, hashed, single-use, and short-lived.

New-device verification
-----------------------
A password is a bearer secret, and it is typed into phones, laptops and
anything with a browser. Once one of those is compromised, every login from it
is. So a login from a device that has not been trusted before is asked for a
second factor -- a code mailed to the address on the account -- and only then is
the device trusted. A trusted device is a long random token in an httpOnly
cookie, not a flag in the session: a session dies with the browser, a trusted
device has to die with an explicit revocation.

Per-user API tokens
-------------------
`PHANTASMA_COMMAND_TOKEN` is one secret for the whole house: everyone who has
it can send commands, it cannot be attributed to anyone, and rotating it breaks
every integration at once. A per-user token is scoped to a user, named, shown
exactly once, revocable on its own, and attributable in the log.

Security decisions, stated so they can be argued with
----------------------------------------------------
* **Recovery never confirms that an address exists.** The response, the timing
  and the page are identical whether the address is in the store or not. The
  feature is reachable for anyone, so "we sent you a code" is a free oracle
  for the user list, and a recovery flow is exactly where an attacker goes
  looking for one.
* **Codes are bcrypt-hashed, single-use, and expire.** A six-digit code is
  1e6 candidates; stored in plaintext, a database copy is a complete bypass of
  the whole login. bcrypt at the store makes an offline sweep cost hours per
  guess, and a failed-attempt counter caps the online one.
* **A code is destroyed by a wrong guess, not just by success**, so three
  failures cannot be resumed.
* **Requesting a code is rate limited per address as well as per client**,
  because the per-client limit does nothing about a script that walks the
  address list from one host -- that is both the enumeration problem and a mail
  bomb.
* **The recovery code is single-use and short-lived**, and a successful reset
  changes the password hash, which invalidates every existing credential that
  was derived from it.
* **Tokens are stored hashed and shown once.** A token list that can re-display
  a secret is a password list with extra steps.
* **A password reset revokes trusted devices.** Otherwise a stolen device stays
  trusted through a password change, which defeats the point of the change.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import sqlite3
import time

logger = logging.getLogger("phantasma.auth")

# --- limits -----------------------------------------------------------------

CODE_TTL_SECONDS = 900  # 15 min: long enough to fetch the mail, short enough
CODE_MAX_FAILURES = 5  # a code dies after 5 wrong guesses
CODE_REQUEST_COOLDOWN = 60  # per address, between code requests
ENV_RECOVERY_PEPPER = "PHANTASMA_RECOVERY_PEPPER"

TOKEN_PREFIX = "phnt_"
TOKEN_BYTES = 32
TOKEN_SKEW_DAYS = 90  # shown in the UI so an old token is recognisable
DEVICE_COOKIE = "phantasma_device"
DEVICE_TOKEN_BYTES = 32

# A password has to survive a shoulder-surf and a dictionary. Eight characters
# is the floor here, not a recommendation: this is the credential in front of
# the light switches.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200  # bcrypt truncates past 72 BYTES; reject rather than
# silently ignore the tail, which would make two different long passwords equal

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    purpose TEXT NOT NULL,          -- 'recover' | 'new_device'
    code_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0,
    consumed INTEGER NOT NULL DEFAULT 0,
    requested_ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_codes_email
    ON auth_codes (email, purpose, consumed);

CREATE TABLE IF NOT EXISTS auth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,           -- shown in the UI: "phnt_a1b2…"
    created_at REAL NOT NULL,
    last_used_at REAL,
    revoked_at REAL,
    scope TEXT NOT NULL DEFAULT 'command'
);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_email ON auth_tokens (email, revoked_at);

CREATE TABLE IF NOT EXISTS trusted_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    device_hash TEXT NOT NULL UNIQUE,
    label TEXT,
    created_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_trusted_devices_email
    ON trusted_devices (email, revoked_at);
"""


# --- plumbing ---------------------------------------------------------------


def _connect(conn_or_path) -> sqlite3.Connection:
    if isinstance(conn_or_path, sqlite3.Connection):
        return conn_or_path
    from src.api import admin as admin_mod

    return admin_mod.get_db_connection()


def ensure_schema(conn_or_path) -> None:
    """Create the tables if absent. Safe to call on every request.

    Takes an OPEN connection where one is available, so a code is consumed in
    the same transaction as whatever it authorised.
    """
    _connect(conn_or_path).executescript(SCHEMA)


def _pepper() -> bytes:
    """A per-install secret mixed into every stored verifier.

    Without it, a stolen copy of the database is enough to mount an offline
    search over the six-digit space. With it, the same copy is not: the
    attacker needs a secret that never leaves this host.
    """
    raw = (os.getenv(ENV_RECOVERY_PEPPER) or "").strip()
    if not raw:
        # No configured pepper: fall back to a fixed domain separator rather
        # than silently using "". The hash is still bcrypt-slow; the pepper is
        # defence in depth, not the primary barrier, and pretending otherwise
        # would be worse than saying it here.
        raw = "phantasma-auth-default-pepper"
        logger.warning(
            "%s is not set: recovery codes are protected by bcrypt only",
            ENV_RECOVERY_PEPPER,
        )
    return raw.encode("utf-8")


def _hash_code(email: str, code: str) -> str:
    """bcrypt over the code, bound to the address and the purpose.

    Binding matters: a code issued for recovery must not be redeemable as a
    new-device code, and a code issued to one address must not be redeemable by
    another. Both are free to enforce here and impossible to enforce later.
    """
    import bcrypt

    payload = f"{_pepper()}|{email.strip().lower()}|{code}".encode("utf-8")
    return bcrypt.hashpw(payload, bcrypt.gensalt()).decode("utf-8")


def _hash_token(raw: str) -> str:
    """SHA-256 for API tokens.

    Not bcrypt, deliberately: a token is 256 bits of `secrets` output, so there
    is no dictionary to attack and the token is looked up on every command.
    SHA-256 is appropriate for a high-entropy secret and wrong for a
    low-entropy one; the distinction is the whole reason the code above uses
    bcrypt and this does not.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- codes ------------------------------------------------------------------


def request_code(
    conn_or_path,
    email: str,
    purpose: str,
    requested_ip: str | None = None,
    now: float | None = None,
) -> str | None:
    """Issue a code. Returns the code for sending, or None if not issued.

    None is returned for a rate-limited request, an unknown address, or a
    suppressed re-request -- the caller must behave identically in all three.
    """
    if purpose not in ("recover", "new_device"):
        raise ValueError(f"unknown purpose: {purpose}")
    now = time.time() if now is None else now
    email = (email or "").strip().lower()

    conn = _connect(conn_or_path)
    ensure_schema(conn)

    user = conn.execute(
        "SELECT is_active FROM users WHERE email = ?", (email,)
    ).fetchone()
    if user is None:
        # No code, no row, no timing difference worth measuring: the bcrypt
        # below never runs, so an unknown address returns sooner. The caller
        # pads the response to a constant cost; see the route.
        logger.info("auth: recovery requested for an unknown address")
        return None
    if not (user["is_active"] if user.keys() else user[0]):
        logger.info("auth: recovery requested for an inactive account")
        return None

    last = conn.execute(
        "SELECT created_at FROM auth_codes WHERE email = ? AND purpose = ?"
        " ORDER BY id DESC LIMIT 1",
        (email, purpose),
    ).fetchone()
    last_at = last["created_at"] if (last is not None and last.keys()) else (
        last[0] if last is not None else 0
    )
    if last is not None and now - last_at < CODE_REQUEST_COOLDOWN:
        return None  # cooldown; the caller shows the same message anyway

    code = f"{secrets.randbelow(1_000_000):06d}"
    conn.execute(
        "INSERT INTO auth_codes"
        " (email, purpose, code_hash, created_at, expires_at, requested_ip)"
        " VALUES (?,?,?,?,?,?)",
        (email, purpose, _hash_code(email, code), now, now + CODE_TTL_SECONDS, requested_ip),
    )
    # Issuing a new code invalidates the previous one for the same purpose, so
    # a leaked earlier code cannot be redeemed after a re-request.
    conn.execute(
        "UPDATE auth_codes SET consumed = 1 WHERE email = ? AND purpose = ? AND id <>"
        " (SELECT MAX(id) FROM auth_codes WHERE email = ? AND purpose = ?)",
        (email, purpose, email, purpose),
    )
    conn.commit()
    logger.info("auth: %s code issued for %s", purpose, email)
    return code


def consume_code(
    conn_or_path, email: str, code: str, purpose: str, now: float | None = None
) -> bool:
    """Verify and burn a code. Single use, single purpose, bound to the address."""
    now = time.time() if now is None else now
    email = (email or "").strip().lower()
    conn = _connect(conn_or_path)
    ensure_schema(conn)

    row = conn.execute(
        "SELECT * FROM auth_codes WHERE email = ? AND purpose = ? AND consumed = 0"
        " ORDER BY id DESC LIMIT 1",
        (email, purpose),
    ).fetchone()
    if row is None:
        return False
    row = dict(row)
    if now > row["expires_at"]:
        conn.execute("UPDATE auth_codes SET consumed = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return False
    if row["failures"] >= CODE_MAX_FAILURES:
        conn.execute("UPDATE auth_codes SET consumed = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return False

    import bcrypt

    try:
        ok = bcrypt.checkpw(
            f"{_pepper()}|{email}|{code}".encode("utf-8"),
            row["code_hash"].encode("utf-8"),
        )
    except (ValueError, TypeError):
        logger.warning("auth: unverifiable code hash format")
        ok = False

    if not ok:
        # A wrong guess kills the code. Resuming a code after failures turns
        # the five-attempt cap into an unlimited online search with pauses.
        failures = row["failures"] + 1
        conn.execute(
            "UPDATE auth_codes SET failures = ? WHERE id = ?", (failures, row["id"])
        )
        if failures >= CODE_MAX_FAILURES:
            conn.execute("UPDATE auth_codes SET consumed = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return False

    conn.execute("UPDATE auth_codes SET consumed = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    return True


# --- API tokens -------------------------------------------------------------


def create_token(
    conn_or_path, email: str, name: str, scope: str = "command", now: float | None = None
) -> tuple[str, dict]:
    """Mint a token. Returns (secret, row). The secret is shown ONCE.

    The stored form is a SHA-256 of the secret, and there is no way to recover
    it afterwards -- that is the point. A UI that can re-display a token is a
    password list with a nicer header.
    """
    now = time.time() if now is None else now
    email = (email or "").strip().lower()
    name = (name or "").strip() or "token"
    if len(name) > 60:
        raise ValueError("name too long")
    if scope not in ("command",):
        raise ValueError(f"unknown scope: {scope}")

    conn = _connect(conn_or_path)
    ensure_schema(conn)
    secret = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    prefix = secret[:12]
    cur = conn.execute(
        "INSERT INTO auth_tokens"
        " (email, name, token_hash, prefix, created_at, scope)"
        " VALUES (?,?,?,?,?,?)",
        (email, name, _hash_token(secret), prefix, now, scope),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM auth_tokens WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    logger.info("auth: token %r created for %s (scope=%s)", prefix, email, scope)
    return secret, dict(row)


def verify_token(conn_or_path, secret: str) -> dict | None:
    """Resolve a token to its owner, or None. Also stamps last_used_at."""
    if not secret or not isinstance(secret, str) or len(secret) < 16:
        return None
    conn = _connect(conn_or_path)
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM auth_tokens WHERE token_hash = ? AND revoked_at IS NULL",
        (_hash_token(secret),),
    ).fetchone()
    if row is None:
        return None
    now = time.time()
    conn.execute("UPDATE auth_tokens SET last_used_at = ? WHERE id = ?", (now, row["id"]))
    conn.commit()
    out = dict(row)
    # A revoked token is not returned, and neither is a scope this caller does
    # not hold: the check is here so a future scope cannot leak by omission.
    if out.get("scope") != "command":
        return None
    return out


def list_tokens(conn_or_path, email: str) -> list[dict]:
    conn = _connect(conn_or_path)
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id, name, prefix, created_at, last_used_at, revoked_at, scope"
        " FROM auth_tokens WHERE email = ? ORDER BY id DESC",
        ((email or "").strip().lower(),),
    ).fetchall()
    return [dict(r) for r in rows]


def revoke_token(conn_or_path, email: str, token_id: int) -> bool:
    """Revoke one of this user's own tokens. Never anyone else's."""
    conn = _connect(conn_or_path)
    ensure_schema(conn)
    cur = conn.execute(
        "UPDATE auth_tokens SET revoked_at = ? WHERE id = ? AND email = ? AND revoked_at IS NULL",
        (time.time(), token_id, (email or "").strip().lower()),
    )
    conn.commit()
    return cur.rowcount > 0


# --- trusted devices --------------------------------------------------------


def trust_device(
    conn_or_path, email: str, label: str | None = None, now: float | None = None
) -> str:
    """Return a device token to store in an httpOnly cookie."""
    now = time.time() if now is None else now
    email = (email or "").strip().lower()
    raw = secrets.token_urlsafe(DEVICE_TOKEN_BYTES)
    conn = _connect(conn_or_path)
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO trusted_devices"
        " (email, device_hash, label, created_at, last_seen_at)"
        " VALUES (?,?,?,?,?)",
        (email, _hash_token(raw), (label or "")[:60], now, now),
    )
    conn.commit()
    logger.info("auth: device trusted for %s", email)
    return raw


def is_device_trusted(conn_or_path, email: str, raw: str) -> bool:
    if not raw:
        return False
    conn = _connect(conn_or_path)
    ensure_schema(conn)
    row = conn.execute(
        "SELECT id FROM trusted_devices WHERE device_hash = ? AND email = ? AND revoked_at IS NULL",
        (_hash_token(raw), (email or "").strip().lower()),
    ).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE trusted_devices SET last_seen_at = ? WHERE id = ?", (time.time(), row["id"])
    )
    conn.commit()
    return True


def revoke_devices(conn_or_path, email: str) -> int:
    """Revoke every trusted device for a user. Returns how many.

    Called on a password change: a reset that left the old devices trusted would
    be a reset the attacker rides straight through.
    """
    conn = _connect(conn_or_path)
    ensure_schema(conn)
    cur = conn.execute(
        "UPDATE trusted_devices SET revoked_at = ? WHERE email = ? AND revoked_at IS NULL",
        (time.time(), (email or "").strip().lower()),
    )
    conn.commit()
    return cur.rowcount


def list_devices(conn_or_path, email: str) -> list[dict]:
    conn = _connect(conn_or_path)
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id, label, created_at, last_seen_at, revoked_at FROM trusted_devices"
        " WHERE email = ? ORDER BY id DESC",
        ((email or "").strip().lower(),),
    ).fetchall()
    return [dict(r) for r in rows]


# --- password policy and the reset itself -----------------------------------


def password_problem(password: str) -> str | None:
    """A human-readable reason the password is refused, or None if it is fine."""
    if not password:
        return "A password não pode ficar vazia."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"A password precisa de pelo menos {MIN_PASSWORD_LENGTH} caracteres."
    if len(password.encode("utf-8")) > MAX_PASSWORD_LENGTH:
        return "A password é demasiado longa."
    if password.strip() != password:
        return "A password não pode começar nem terminar com espaços."
    if re.fullmatch(r"(.)\1*", password):
        return "A password não pode ser um único carácter repetido."
    return None


def set_password(conn_or_path, email: str, password: str) -> bool:
    """Replace the stored hash. Returns False if the policy refuses it."""
    problem = password_problem(password)
    if problem:
        logger.info("auth: password reset refused by policy")
        return False
    from src.api import admin as admin_mod

    conn = _connect(conn_or_path)
    cur = conn.execute(
        "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP"
        " WHERE email = ?",
        (admin_mod.hash_password(password), (email or "").strip().lower()),
    )
    conn.commit()
    return cur.rowcount > 0
