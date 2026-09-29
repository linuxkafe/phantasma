"""Tests for the auth store: recovery codes, API tokens, trusted devices.

The store is where a mistake is expensive and invisible, so these are mostly
negative tests: the ways it must refuse, expire, forget and fail to disclose.

The ones that matter most:

* recovery never confirms an address exists (enumeration);
* a code cannot be replayed, reused for another purpose, or moved to another
  address;
* a wrong guess destroys the code rather than pausing it;
* a reset revokes trusted devices, so a stolen device does not ride through a
  password change;
* a token is shown once and stored hashed, and revoking one does not touch the
  others;
* a token presented by the wrong scope is refused.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from src.api import auth_store


@pytest.fixture()
def conn(monkeypatch):
    """An isolated users DB with one active account and one inactive one."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE users (email TEXT PRIMARY KEY, password_hash TEXT,"
        " role TEXT, is_active INTEGER, updated_at TEXT)"
    )
    db.execute(
        "INSERT INTO users VALUES ('user@x.test','hash','user',1,'now')"
    )
    db.execute(
        "INSERT INTO users VALUES ('off@x.test','hash','user',0,'now')"
    )
    db.commit()
    auth_store.ensure_schema(db)
    yield db
    db.close()


# --- codes: issue and verify ------------------------------------------------


def test_a_code_verifies_once_and_only_once(conn):
    code = auth_store.request_code(conn, "user@x.test", "recover")
    assert code and len(code) == 6
    assert auth_store.consume_code(conn, "user@x.test", code, "recover") is True
    # Replayed: the whole point of burning it.
    assert auth_store.consume_code(conn, "user@x.test", code, "recover") is False


def test_a_code_is_bound_to_its_purpose(conn):
    """A recovery code must not be redeemable as a new-device code: they go to
    the same address, and cross-use would turn a password reset into a login
    bypass."""
    code = auth_store.request_code(conn, "user@x.test", "recover")
    assert auth_store.consume_code(conn, "user@x.test", code, "new_device") is False


def test_a_code_is_bound_to_its_address(conn):
    code = auth_store.request_code(conn, "user@x.test", "recover")
    assert auth_store.consume_code(conn, "off@x.test", code, "recover") is False


def test_a_wrong_guess_destroys_the_code(conn):
    """A code must die after enough wrong guesses, not pause and let the search
    resume. The exact attempt at which it dies is the configured cap, so drive
    it to the cap rather than assuming a smaller number."""
    code = auth_store.request_code(conn, "user@x.test", "recover")
    for _ in range(auth_store.CODE_MAX_FAILURES):
        assert auth_store.consume_code(conn, "user@x.test", "000000", "recover") is False
    # The real code is dead too, and cannot be resumed.
    assert auth_store.consume_code(conn, "user@x.test", code, "recover") is False


def test_a_code_survives_up_to_the_cap(conn):
    """The complement of the test above: a typo must not cost the user their
    code after one slip, or recovery becomes unusable in practice."""
    code = auth_store.request_code(conn, "user@x.test", "recover")
    for _ in range(auth_store.CODE_MAX_FAILURES - 1):
        assert auth_store.consume_code(conn, "user@x.test", "000000", "recover") is False
    assert auth_store.consume_code(conn, "user@x.test", code, "recover") is True


def test_an_expired_code_is_refused(conn):
    code = auth_store.request_code(conn, "user@x.test", "recover")
    later = time.time() + auth_store.CODE_TTL_SECONDS + 1
    assert auth_store.consume_code(conn, "user@x.test", code, "recover", now=later) is False


def test_re_requesting_invalidates_the_previous_code(conn):
    first = auth_store.request_code(conn, "user@x.test", "recover")
    # Force past the cooldown by backdating the first row.
    conn.execute("UPDATE auth_codes SET created_at = 0")
    conn.commit()
    second = auth_store.request_code(conn, "user@x.test", "recover")
    assert second is not None
    # A code leaked before a re-request must stop working.
    assert auth_store.consume_code(conn, "user@x.test", first, "recover") is False


def test_a_code_is_not_stored_in_the_clear(conn):
    code = auth_store.request_code(conn, "user@x.test", "recover")
    stored = conn.execute("SELECT code_hash FROM auth_codes").fetchone()[0]
    assert code not in stored
    assert stored.startswith("$2")  # bcrypt


def test_cooldown_suppresses_a_second_request(conn):
    assert auth_store.request_code(conn, "user@x.test", "recover") is not None
    assert auth_store.request_code(conn, "user@x.test", "recover") is None


# --- enumeration: the important one ----------------------------------------


def test_an_unknown_address_gets_no_code(conn):
    assert auth_store.request_code(conn, "nobody@x.test", "recover") is None


def test_an_inactive_account_gets_no_code(conn):
    assert auth_store.request_code(conn, "off@x.test", "recover") is None


def test_an_unknown_address_leaves_no_trace(conn):
    """No row at all: a table that grows for unknown addresses is a log of
    which addresses were probed."""
    auth_store.request_code(conn, "nobody@x.test", "recover")
    n = conn.execute("SELECT COUNT(*) FROM auth_codes").fetchone()[0]
    assert n == 0


# --- password policy --------------------------------------------------------


@pytest.mark.parametrize(
    "bad, fragment",
    [
        ("", "vazia"),
        ("short", "pelo menos"),
        (" leading", "espaços"),
        ("trailing ", "espaços"),
        ("aaaaaaaaaa", "repetido"),
        ("x" * 300, "longa"),
    ],
)
def test_weak_passwords_are_refused_with_a_reason(bad, fragment):
    problem = auth_store.password_problem(bad)
    assert problem is not None
    assert fragment in problem


def test_a_reasonable_password_is_accepted():
    assert auth_store.password_problem("chaton-velho-42") is None


def test_set_password_refuses_a_weak_one(conn):
    assert auth_store.set_password(conn, "user@x.test", "abc") is False


def test_set_password_writes_a_hash_not_the_password(conn):
    assert auth_store.set_password(conn, "user@x.test", "chaton-velho-42") is True
    stored = conn.execute(
        "SELECT password_hash FROM users WHERE email = 'user@x.test'"
    ).fetchone()[0]
    assert "chaton-velho-42" not in stored
    assert stored.startswith("$2")


# --- tokens -----------------------------------------------------------------


def test_a_token_is_returned_once_and_stored_hashed(conn):
    secret, row = auth_store.create_token(conn, "user@x.test", "telemóvel")
    assert secret.startswith(auth_store.TOKEN_PREFIX)
    assert row["prefix"] == secret[:12]
    stored = conn.execute("SELECT token_hash FROM auth_tokens").fetchone()[0]
    assert secret not in stored
    # The list can never re-display the secret.
    listed = auth_store.list_tokens(conn, "user@x.test")
    assert secret not in str(listed)


def test_a_valid_token_resolves_to_its_owner(conn):
    secret, _ = auth_store.create_token(conn, "user@x.test", "ha")
    found = auth_store.verify_token(conn, secret)
    assert found["email"] == "user@x.test"


def test_verify_stamps_last_used(conn):
    secret, _ = auth_store.create_token(conn, "user@x.test", "ha")
    auth_store.verify_token(conn, secret)
    row = conn.execute("SELECT last_used_at FROM auth_tokens").fetchone()
    assert row[0] is not None


def test_a_revoked_token_stops_working_and_others_do_not(conn):
    keep, row_keep = auth_store.create_token(conn, "user@x.test", "fica")
    drop, row_drop = auth_store.create_token(conn, "user@x.test", "morre")
    assert auth_store.revoke_token(conn, "user@x.test", row_drop["id"]) is True
    assert auth_store.verify_token(conn, drop) is None
    assert auth_store.verify_token(conn, keep) is not None


def test_one_user_cannot_revoke_another_users_token(conn):
    auth_store.create_token(conn, "off@x.test", "dela")
    victim = conn.execute("SELECT id FROM auth_tokens").fetchone()[0]
    assert auth_store.revoke_token(conn, "user@x.test", victim) is False


@pytest.mark.parametrize("junk", [None, "", "x", "not-a-real-token", 12345])
def test_junk_tokens_are_refused(conn, junk):
    assert auth_store.verify_token(conn, junk) is None


def test_an_unknown_scope_is_refused_at_creation(conn):
    with pytest.raises(ValueError):
        auth_store.create_token(conn, "user@x.test", "x", scope="root")


def test_a_token_with_an_unusable_scope_never_verifies(conn):
    """Belt and braces: even if a row with another scope exists (a migration, a
    hand-edited row), the check refuses it rather than relying on the insert
    path to have been the only way to get there."""
    secret, _ = auth_store.create_token(conn, "user@x.test", "ha")
    conn.execute("UPDATE auth_tokens SET scope = 'admin-everything'")
    conn.commit()
    assert auth_store.verify_token(conn, secret) is None


# --- trusted devices --------------------------------------------------------


def test_a_trusted_device_is_recognised(conn):
    raw = auth_store.trust_device(conn, "user@x.test", "Pixel")
    assert auth_store.is_device_trusted(conn, "user@x.test", raw) is True


def test_a_device_belongs_to_one_address(conn):
    raw = auth_store.trust_device(conn, "user@x.test")
    assert auth_store.is_device_trusted(conn, "off@x.test", raw) is False


def test_a_password_reset_revokes_trusted_devices(conn):
    """Otherwise a stolen device stays trusted through a password change, and
    the reset is a reset the attacker rides straight through."""
    raw = auth_store.trust_device(conn, "user@x.test", "laptop roubado")
    assert auth_store.is_device_trusted(conn, "user@x.test", raw) is True
    assert auth_store.revoke_devices(conn, "user@x.test") >= 1
    assert auth_store.is_device_trusted(conn, "user@x.test", raw) is False


def test_revoking_devices_is_idempotent(conn):
    auth_store.trust_device(conn, "user@x.test")
    auth_store.revoke_devices(conn, "user@x.test")
    assert auth_store.revoke_devices(conn, "user@x.test") == 0


@pytest.mark.parametrize("junk", [None, "", "not-a-device"])
def test_junk_device_cookies_are_refused(conn, junk):
    assert auth_store.is_device_trusted(conn, "user@x.test", junk) is False


def test_device_tokens_are_not_stored_in_the_clear(conn):
    raw = auth_store.trust_device(conn, "user@x.test")
    stored = conn.execute("SELECT device_hash FROM trusted_devices").fetchone()[0]
    assert raw not in stored
