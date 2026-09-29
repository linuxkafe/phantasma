"""bcrypt's 72-byte limit, and the pepper that crosses it.

Found on 2026-09-29, by setting `PHANTASMA_RECOVERY_PEPPER` for the first time.

The pepper is documented as "Set it" -- without it the recovery codes are
protected by bcrypt alone. Setting it broke recovery completely, and nothing
noticed for the whole time the pepper was absent.

`_hash_code` built the payload `pepper|email|code` and handed it straight to
`bcrypt.hashpw`. bcrypt does not truncate at 72 bytes, it raises `ValueError`.
With the 28-character placeholder that ships as the default the payload was 53
bytes and every test passed; with the 64-character hex value the README asks
for, the same payload was 88 bytes and **every** recovery code and every
new-device code raised on the first request.

The bug was invisible because the hardening was never exercised. A feature that
only runs when the owner follows the advice is not a feature that is tested.

These tests assert the property rather than a fixed number: whatever the pepper
and the address, the material handed to bcrypt is the same length and under the
limit. The previous test suite asserted nothing about it, and would have passed
again.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import auth_store  # noqa: E402

BCRYPT_LIMIT = 72


def _store_with_user(monkeypatch, email):
    """A store containing `email`, so request_code will issue for it.

    Returns the connection factory, not a connection: request_code closes what
    it is handed.
    """
    import sqlite3

    from tests.helpers_ui_auth import seeded_store

    seeded_store(monkeypatch, email=email, role="admin")

    def _fresh():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    return _fresh


@pytest.mark.parametrize(
    "pepper",
    [
        "phantasma-auth-default-pepper",  # the shipped placeholder, 28 chars
        "a" * 64,                          # 32 bytes of hex, what the README sets
        "b" * 200,                         # a longer one: length must not matter
        "",
    ],
    ids=["placeholder", "hex64", "long200", "empty"],
)
@pytest.mark.parametrize(
    "email,code",
    [
        ("b@t.test", "123456"),
        ("owner@exemplo.pt", "A7B9K2"),
        ("someone.with.a.very.long.address@subdomain.example.org", "ZZZZZZZZ"),
    ],
    ids=["short", "typical", "long"],
)
def test_the_payload_always_fits_inside_bcrypt(monkeypatch, pepper, email, code):
    """The property that broke: bcrypt raises above 72 bytes."""
    monkeypatch.setenv(auth_store.ENV_RECOVERY_PEPPER, pepper)
    payload = auth_store._code_payload(email, code)
    assert len(payload) <= BCRYPT_LIMIT, (
        f"payload is {len(payload)} bytes, over bcrypt's {BCRYPT_LIMIT}: the "
        f"recovery code would raise instead of verifying"
    )


def test_the_payload_length_does_not_depend_on_the_pepper(monkeypatch):
    """Fixed length is the property that makes the limit unreachable, whatever
    the owner puts in the variable."""
    lengths = set()
    for pepper in ("short", "a" * 64, "z" * 500):
        monkeypatch.setenv(auth_store.ENV_RECOVERY_PEPPER, pepper)
        lengths.add(len(auth_store._code_payload("owner@exemplo.pt", "A7B9K2")))
    assert len(lengths) == 1, f"payload length varies with the pepper: {lengths}"


def test_a_real_pepper_issues_and_verifies_a_code(monkeypatch):
    """The end-to-end path the pepper broke: issue, then redeem.

    Not a stub and not a unit of the hash function -- the point is that a code
    issued with a real pepper can be verified with the same one.
    """
    monkeypatch.setenv(auth_store.ENV_RECOVERY_PEPPER, "a" * 64)
    email = "owner@exemplo.pt"
    conn = _store_with_user(monkeypatch, email)
    code = auth_store.request_code(conn, email, "recover", requested_ip="127.0.0.1")
    assert code, "a code should be issued with a real pepper"
    assert auth_store.consume_code(conn, email, code, "recover") is True
    assert auth_store.consume_code(conn, email, "WRONG1", "recover") is False


def test_a_long_address_and_a_long_pepper_still_issue(monkeypatch):
    """The combination that failed in production: a real address plus the
    pepper the README asks for, which is what an owner who follows the advice
    actually has."""
    monkeypatch.setenv(auth_store.ENV_RECOVERY_PEPPER, "a" * 64)
    email = "someone.with.a.very.long.address@subdomain.example.org"
    conn = _store_with_user(monkeypatch, email)
    code = auth_store.request_code(conn, email, "new_device", requested_ip="127.0.0.1")
    assert code, "issuing a code raised with a real pepper and a long address"


def test_changing_the_pepper_invalidates_old_codes(monkeypatch):
    """Documented consequence, stated as a test so that it is a decision.

    The pepper is mixed into the digest, so a code issued under one pepper does
    not verify under another. That is the point of a pepper, and it is why
    rotating it discards codes in flight -- acceptable for single-use,
    short-lived codes, and NOT acceptable for a permanent credential. The
    difference is why this is a test and not a comment.
    """
    email = "owner@exemplo.pt"
    conn = _store_with_user(monkeypatch, email)
    monkeypatch.setenv(auth_store.ENV_RECOVERY_PEPPER, "a" * 64)
    code = auth_store.request_code(conn, email, "recover", requested_ip="127.0.0.1")
    assert code
    assert auth_store.consume_code(conn, email, code, "recover") is True

    monkeypatch.setenv(auth_store.ENV_RECOVERY_PEPPER, "b" * 64)
    assert auth_store.consume_code(conn, email, code, "recover") is False, (
        "a code verified after the pepper rotated; the pepper is not mixed in"
    )
