"""Regression tests for password verification across hash formats.

The bug
-------
``verify_password`` called ``bcrypt.checkpw`` on whatever was in the store. The
production store held passlib-style **scrypt** hashes, and bcrypt does not
return False on a foreign encoding -- it RAISES ``ValueError: Invalid salt``.
``authenticate_user`` had no exception handling, so authenticating the existing
administrator returned 500 instead of a clean rejection.

The invariant
-------------
Verification NEVER raises. An unrecognised or corrupt encoding is a
verification FAILURE, because a hash the code cannot interpret must not be able
to take a request down. Both supported formats verify correctly, and a wrong
password is rejected by both.

If a third format ever appears, add it here explicitly -- do not let it fall
through to a raise.
"""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

import bcrypt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.admin import hash_password, verify_password  # noqa: E402


def _make_scrypt(password: bytes, n=1024, r=8, p=1) -> str:
    """Build a passlib-style scrypt hash, the format already in the store."""
    salt = b"0123456789abcdef"
    dk = hashlib.scrypt(
        password, salt=salt, n=n, r=r, p=p, dklen=32, maxmem=128 * n * r * p + (1 << 20)
    )
    return "scrypt:{}:{}:{}${}${}".format(
        n, r, p, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


class TestBcryptFormat:
    def test_round_trip(self):
        h = hash_password("correct horse")
        assert verify_password(h, "correct horse") is True

    def test_wrong_password_rejected(self):
        assert verify_password(hash_password("correct horse"), "battery") is False


class TestScryptFormat:
    def test_correct_password_accepted(self):
        assert verify_password(_make_scrypt(b"correct horse"), "correct horse") is True

    def test_wrong_password_rejected(self):
        assert verify_password(_make_scrypt(b"correct horse"), "battery") is False


class TestNeverRaises:
    """The core invariant: a bad hash is a rejection, not a crash."""

    @pytest.mark.parametrize(
        "stored",
        [
            "",
            "not-a-hash",
            "scrypt:",
            "scrypt:broken$missing-parts",
            "scrypt:999:8:1$!!!notbase64!!!$!!!nope!!!",
            "scrypt:32768:8:1$onlysalt",
            "$2b$truncated",
            "argon2:xyz",
        ],
    )
    def test_malformed_hash_returns_false(self, stored):
        assert verify_password(stored, "whatever") is False

    def test_real_production_hash_shape_is_handled(self):
        """The exact shape found in the production store."""
        stored = "scrypt:32768:8:1$rmtPXKYCReUR08l2$7fb61f78695c6da50448b4a961a627b5b6d1"
        # Must not raise. The correct password is unknown and must stay unknown.
        assert verify_password(stored, "phantasma123") in (True, False)


class TestTheOriginalBugIsPinned:
    def test_bcrypt_would_have_raised_on_this_hash(self):
        """Document WHY the dispatch exists: bcrypt raises, it does not return False.

        If a future refactor removes the scrypt branch, this test still passes but
        the next one fails -- together they pin the regression from both sides.
        """
        stored = "scrypt:32768:8:1$rmtPXKYCReUR08l2$7fb61f78"
        with pytest.raises(ValueError):
            bcrypt.checkpw(b"x", stored.encode())

    def test_verify_password_does_not_raise_on_the_same_hash(self):
        stored = "scrypt:32768:8:1$rmtPXKYCReUR08l2$7fb61f78"
        assert verify_password(stored, "x") is False


class TestAuthenticateUserSurvivesBadHashes:
    def test_authenticate_user_returns_none_not_exception(self, admin_store):
        """The end-to-end symptom: a 500 during login must be impossible."""
        import sqlite3

        db = Path(admin_store.CONFIG_DB_PATH)
        con = sqlite3.connect(db)
        # This database is session-scoped (tests/conftest.py): other admin
        # tests later in the same run sign in as TEST_ADMIN_EMAIL, whose row
        # lives in this very table. Deleting every row breaks them for the
        # rest of the session -- it turned /admin/config into a login redirect
        # by the time test_quiet.py ran. Snapshot and restore, so this test
        # stays a test and stops being a session landmine.
        before = list(con.execute("SELECT email, password_hash, role, is_active FROM users"))
        try:
            con.execute("DELETE FROM users")
            con.execute(
                "INSERT INTO users (email, password_hash, role, is_active) "
                "VALUES ('broken@example.invalid', 'scrypt:garbage$nope', 'admin', 1)"
            )
            con.commit()
            assert admin_store.authenticate_user("broken@example.invalid", "anything") is None
        finally:
            con.execute("DELETE FROM users")
            con.executemany(
                "INSERT INTO users (email, password_hash, role, is_active) VALUES (?, ?, ?, ?)",
                before,
            )
            con.commit()
            con.close()
