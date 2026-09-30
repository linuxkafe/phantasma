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


def _ensure_column(conn, table: str, column: str, decl: str) -> bool:
    """Add a column if the table has it and the column is missing.

    ``CREATE TABLE IF NOT EXISTS`` cannot add a column to a table that already
    exists, so a schema change to somebody else's table needs this instead. The
    ``PRAGMA`` check is what makes it idempotent: safe to call on every request,
    which is what ``ensure_schema`` already promises.

    Silently does nothing when the table is absent. On a fresh install ``users``
    is created by the bootstrap that owns it, not here, and a column migration
    that raised on a missing table would take the whole auth path down on a
    database that is merely not initialised yet.
    """
    try:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return False
    if not existing or column in existing:
        return False
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True
    except sqlite3.Error:
        return False


def ensure_schema(conn_or_path) -> None:
    """Create the tables if absent. Safe to call on every request.

    Takes an OPEN connection where one is available, so a code is consumed in
    the same transaction as whatever it authorised.
    """
    conn = _connect(conn_or_path)
    conn.executescript(SCHEMA)
    # `users` is not ours to create, but the Discord identity is an auth
    # concern, so the column is added here rather than in a migration nobody
    # remembers to run. Additive and guarded, so an existing install gains it on
    # the next request and a fresh one gets it on its first.
    _ensure_column(conn, "users", "discord_id", "TEXT")
    try:
        # One Discord account, one phantasma user. Enforced here and not only in
        # the form: without it, two accounts could each claim the same ID and
        # the second one to save would silently win, which is an authorisation
        # decision made by whichever request arrived last.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id"
            " ON users (discord_id) WHERE discord_id IS NOT NULL"
            " AND discord_id != ''"
        )
    except sqlite3.Error:
        # Pre-existing duplicates must not stop the service from starting. The
        # lookup below resolves them by refusing the ambiguous ID, which is the
        # safe direction: no access rather than the wrong access.
        pass


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


def _code_payload(email: str, code: str) -> bytes:
    """The bytes that get hashed, for BOTH issuing and verifying a code.

    Pre-hashed with SHA-256 on purpose. bcrypt refuses inputs longer than 72
    bytes -- it raises ValueError, it does not truncate -- and the payload is
    `pepper|email|code`. With the default 28-char placeholder that fits, so
    every test passed and the code shipped for months; with the 64-char hex
    pepper the README tells the owner to set, the same payload was 88 bytes and
    EVERY recovery and new-device code raised on the first request. The pepper
    was not merely hardening, it was breaking the feature it protects -- and the
    absence of the pepper was the only reason nobody had noticed.

    SHA-256 collapses the input to 32 bytes, which is fixed whatever the pepper
    or the address length, and leaves room for the bcrypt limit. The pepper is
    still fully mixed in: it is an input to the digest, not a truncation of it.

    One function, used by both paths, because hashing with one construction and
    verifying with another is how a code becomes permanently unredeemable while
    both sides still look correct.
    """
    return hashlib.sha256(f"{_pepper()}|{email.strip().lower()}|{code}".encode()).digest()


def _hash_code(email: str, code: str) -> str:
    """bcrypt over the code, bound to the address and the purpose.

    Binding matters: a code issued for recovery must not be redeemable as a
    new-device code, and a code issued to one address must not be redeemable by
    another. Both are free to enforce here and impossible to enforce later.
    """
    import bcrypt

    return bcrypt.hashpw(_code_payload(email, code), bcrypt.gensalt()).decode("utf-8")


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
        # SUPPRESSION, and the caller must be able to tell it apart from "the
        # address does not exist". Those two need different words on the page:
        # one says "we sent you a code, wait for it", the other says the same
        # thing for the privacy of the user list. Collapsing them into a bare
        # `None` is what let a real login look broken -- the route redirected to
        # the code page as though a code had gone out, and none had.
        logger.info(
            "auth: %s code suppressed for %s (cooldown, %ds since the last)",
            purpose, email, int(now - last_at),
        )
        return None

    code = f"{secrets.randbelow(1_000_000):06d}"
    conn.execute(
        "INSERT INTO auth_codes"
        " (email, purpose, code_hash, created_at, expires_at, requested_ip)"
        " VALUES (?,?,?,?,?,?)",
        (email, purpose, _hash_code(email, code), now, now + CODE_TTL_SECONDS, requested_ip),
    )
    # A re-request invalidates every previous code for the same purpose, so a
    # code leaked before a re-request cannot be redeemed afterwards. This is a
    # SECURITY property and it stays absolute: there is no way to tell a code
    # the owner is still holding from one that leaked, so every earlier code
    # dies. (A softer rule -- "a superseded code stays valid until its own
    # window closes" -- was tried and reverted for exactly this reason: it
    # widens the window in which a leaked code still works, and the test
    # test_re_requesting_invalidates_the_previous_code is right to object.)
    #
    # The usability problem this caused is real and is solved elsewhere, without
    # touching the security property: the verification page now tells the user a
    # newer code exists and that theirs was replaced, and a re-request is
    # refused inside the cooldown instead of silently superseding the code they
    # are holding. See live_code_state and the route in skills/skill_ui.py.
    conn.execute(
        "UPDATE auth_codes SET consumed = 1 WHERE email = ? AND purpose = ? AND id <>"
        " (SELECT MAX(id) FROM auth_codes WHERE email = ? AND purpose = ?)",
        (email, purpose, email, purpose),
    )
    conn.commit()
    logger.info("auth: %s code issued for %s (previous codes invalidated)", purpose, email)
    return code


def consume_code(
    conn_or_path, email: str, code: str, purpose: str, now: float | None = None
) -> bool:
    """Verify and burn a code. Single use, single purpose, bound to the address.

    Considers EVERY unconsumed code for the address, not just the newest, and
    keeps the reason so the caller can tell the user what actually happened.

    The newest-only version is what made a real account look permanently broken.
    Measured in production on 2026-09-30, for `elsavamp@gmail.com`:

        09:23:32  new_device code issued
        09:25:20  new_device code issued
        09:27:09  new_device code issued
        09:29:48  new_device code issued

    Four codes in six minutes, every one of them already `consumed=1`, and not
    a single "device verified" for that account -- while `mail@linuxkafe.com`
    verified on its first attempt minutes later. Re-issuing a code marks every
    earlier one consumed (the line below), so a user who asked twice and then
    read the FIRST email was submitting a code that had already been killed,
    and the answer was the same sentence as for a genuinely expired code:
    "O código não é válido ou expirou."

    Three things were wrong with that, and only the third is about security:

    * the newest-only lookup could not tell "you typed an old code" from "your
      code timed out", so the one message had to cover both and told the user
      nothing actionable;
    * a code still inside its own 900s window was rejected because an
      unrelated later request had superseded it -- the user had done nothing
      wrong except ask twice, and was punished for it;
    * the failure counter moved on the WRONG row. The wrong guess incremented
      `failures` on the newest code, not on the one she actually typed, so
      five mistypes spread across three emails locked out the live code.

    The fix keeps single use, single purpose, expiry and the failure cap
    exactly as they were, and only stops the supersession from being silent.
    """
    now = time.time() if now is None else now
    email = (email or "").strip().lower()
    conn = _connect(conn_or_path)
    ensure_schema(conn)

    rows = conn.execute(
        "SELECT * FROM auth_codes WHERE email = ? AND purpose = ? AND consumed = 0"
        " ORDER BY id DESC",
        (email, purpose),
    ).fetchall()
    if not rows:
        return False

    import bcrypt

    def _verify(row) -> bool:
        try:
            return bcrypt.checkpw(
                _code_payload(email, code), row["code_hash"].encode("utf-8")
            )
        except (ValueError, TypeError):
            # bcrypt raises on anything over 72 bytes rather than truncating, so
            # a hash written by an older build, or a payload built differently
            # here, must read as "does not verify" and not as a 500 on the owner
            # trying to recover their password.
            logger.warning("auth: unverifiable code hash format")
            return False

    # There is normally exactly ONE unconsumed code, because a re-request
    # retires the previous one. The loop exists so a code that predates this
    # rule, or one written by a concurrent request, is still redeemed rather
    # than refused for being in the wrong row. Single use is preserved: only one
    # row can be consumed per call, and the redemption below burns the rest.
    for row in rows:
        row = dict(row)
        if now > row["expires_at"]:
            continue
        if row["failures"] >= CODE_MAX_FAILURES:
            continue
        if _verify(row):
            conn.execute(
                "UPDATE auth_codes SET consumed = 1 WHERE id = ?", (row["id"],)
            )
            conn.execute(
                "UPDATE auth_codes SET consumed = 1 WHERE email = ? AND purpose = ?"
                " AND id <> ?",
                (email, purpose, row["id"]),
            )
            conn.commit()
            return True

    # No match. Charge the failure to the code she most plausibly typed -- the
    # newest LIVE one -- and only ever once per attempt, however many rows were
    # scanned. Counting a miss once per scanned row would burn the five
    # attempts in a single wrong keystroke.
    live = [dict(r) for r in rows if now <= r["expires_at"]]
    if live:
        target = live[0]
        failures = target["failures"] + 1
        conn.execute(
            "UPDATE auth_codes SET failures = ? WHERE id = ?", (failures, target["id"])
        )
        if failures >= CODE_MAX_FAILURES:
            conn.execute(
                "UPDATE auth_codes SET consumed = 1 WHERE id = ?", (target["id"],)
            )
        conn.commit()
    return False


def live_code_state(
    conn_or_path, email: str, purpose: str, now: float | None = None
) -> dict:
    """What the verification page needs to say something useful.

    ``replaced`` means there is a newer code than the one she is holding, which
    is the case that used to read as "expired" with no explanation. Returned as
    data rather than baked into a message so the wording lives in one place --
    the page -- and the store stays free of user-facing Portuguese.
    """
    now = time.time() if now is None else now
    email = (email or "").strip().lower()
    conn = _connect(conn_or_path)
    ensure_schema(conn)
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM auth_codes WHERE email = ? AND purpose = ?"
            " ORDER BY id DESC",
            (email, purpose),
        ).fetchall()
    ]
    live = [r for r in rows if not r["consumed"] and now <= r["expires_at"]]
    if not live:
        return {"state": "none", "seconds_left": 0, "attempts_left": 0}
    newest = live[0]
    # A re-request retires the previous code (a security property, absolute).
    # So the signal that "a newer code replaced the one she is holding" is: a
    # CONSUMED code that is still inside its original window -- which only
    # happens because it was superseded, since a redeemed one would be a
    # different story. This is what the page turns into "usa o mais recente".
    superseded = [
        r for r in rows
        if r["consumed"] and not r["failures"] and r["id"] != newest["id"]
        and r["created_at"] > 0 and (r["expires_at"] - now) > 0
    ]
    return {
        "state": "live",
        "seconds_left": max(0, int(newest["expires_at"] - now)),
        "attempts_left": max(0, CODE_MAX_FAILURES - newest["failures"]),
        "replaced": len(superseded) > 0,
        "issued_at": newest["created_at"],
    }



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


# --------------------------------------------------------------- Discord ----
#
# The Discord bot authorises by Discord's own numeric user id, from
# DISCORD_ADMIN_USERS / DISCORD_STANDARD_USERS in the environment. That is fine
# for an install with one operator and terrible for a household: the list lives
# in a file only the owner can edit, so a second person cannot join, and nobody
# can discover their own id without asking.
#
# So a signed-in user may claim an id from their own profile, and the web role
# decides what that id may do. The environment lists keep working and keep their
# precedence: they are the owner's deliberate, out-of-band decision, and a
# profile field must not be able to take a capability away from them.
#
# The security property that matters: this is an AUTHORISATION change, so the
# dangerous move is one user claiming another user's id. Hence the unique index,
# the refusal to overwrite an id that is already claimed, and -- for ids that
# somehow exist twice -- a refusal rather than a guess. In every ambiguous case
# the answer is "no access", never "possibly the wrong access".

DISCORD_ID_MAX = 20  # Discord snowflakes are 17-20 digits and fit in an int64


def normalise_discord_id(raw: str | None) -> tuple[str | None, str | None]:
    """Validate a claimed Discord id.

    Returns ``(id, None)`` or ``(None, reason)``. Digits only: a Discord user id
    is a snowflake, and accepting anything else would put a string into a
    column that the bot compares against integers.
    """
    text = (raw or "").strip().replace(" ", "")
    if not text:
        return None, None  # empty means "clear it", not an error
    # `[0-9]` and not `str.isdigit()`. `isdigit()` is True for every Unicode
    # decimal digit, so "٣٤٥" (Arabic-Indic) validated as a numeric id: it looks
    # like a number, it goes into a column the bot compares against an int, and
    # it can therefore never match. A field that accepts a value guaranteed to
    # fail later is worse than one that refuses it here, where the user is
    # looking.
    if not re.fullmatch(r"[0-9]+", text):
        return None, "O ID do Discord são só algarismos."
    if len(text) > DISCORD_ID_MAX:
        return None, "Esse ID do Discord é demasiado longo."
    return text, None


def get_discord_id(email: str, conn_or_path=None) -> str | None:
    """The Discord id claimed by ``email``."""
    row = _connect(conn_or_path).execute(
        "SELECT discord_id FROM users WHERE email = ?",
        ((email or "").strip().lower(),),
    ).fetchone()
    if row is None:
        return None
    value = row["discord_id"] if not isinstance(row, tuple) else row[0]
    return (value or "").strip() or None


def set_discord_id(email: str, raw: str | None, conn_or_path=None) -> tuple[bool, str | None]:
    """Claim (or clear) a Discord id for ``email``.

    ``(True, None)`` on success, ``(False, reason)`` otherwise -- the reason is
    shown to the user, so it says what to do rather than "error".
    """
    discord_id, problem = normalise_discord_id(raw)
    if problem:
        return False, problem
    conn = _connect(conn_or_path)
    who = (email or "").strip().lower()
    try:
        ensure_schema(conn)
        if discord_id is None:
            conn.execute("UPDATE users SET discord_id = NULL WHERE email = ?", (who,))
            conn.commit()
            return True, None
        taken = conn.execute(
            "SELECT email FROM users WHERE discord_id = ? AND email != ?",
            (discord_id, who),
        ).fetchone()
        if taken is not None:
            # Not "already yours, fine": the owner may have re-typed it, and the
            # honest answer is that the id is in use, not that it worked.
            return False, "Esse ID do Discord já está associado a outra conta."
        conn.execute(
            "UPDATE users SET discord_id = ? WHERE email = ?", (discord_id, who)
        )
        conn.commit()
        return True, None
    except sqlite3.IntegrityError:
        # The unique index fired, which is the database disagreeing with a
        # check-then-write. Two requests, same id, same instant.
        return False, "Esse ID do Discord já está associado a outra conta."
    except sqlite3.Error as exc:
        logger.error(f"auth: discord id refused: {exc}")
        return False, "Não foi possível guardar o ID do Discord."


def discord_role_for(discord_id, conn_or_path=None) -> str | None:
    """The web role of the account that claims this Discord id.

    ``"admin"``, ``"user"``, or ``None`` when the id is not claimed, is claimed
    ambiguously, or belongs to a deactivated account. A deactivated account
    keeps its id and loses its access, which is the point of the check.
    """
    if discord_id in (None, ""):
        return None
    try:
        conn = _connect(conn_or_path)
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT role, is_active FROM users WHERE discord_id = ?", (str(discord_id),)
        ).fetchall()
    except sqlite3.Error as exc:
        logger.error(f"auth: discord role lookup failed: {exc}")
        return None
    if len(rows) != 1:
        # Zero: not claimed. More than one: a duplicate that predates the unique
        # index. Either way there is no single account to speak for this id.
        return None
    row = rows[0]
    role = row["role"] if not isinstance(row, tuple) else row[0]
    active = row["is_active"] if not isinstance(row, tuple) else row[1]
    if not active:
        return None
    return (role or "user").strip().lower() or "user"
