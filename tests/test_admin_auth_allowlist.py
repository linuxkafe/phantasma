"""Access to /admin/login must be allowlist-only.

Before this, `login()` accepted ANY address: it generated an OTP, stored it
and mailed it. The OTP was then printed to the journal, so an allowlist on its
own would be cosmetic. These tests pin the allowlist rules and, just as
importantly, pin that a database failure denies rather than admits.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import admin as admin_mod  # noqa: E402


def _conn_with(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users (email TEXT, role TEXT, is_active INT)")
    conn.executemany(
        "INSERT INTO users VALUES (?, ?, ?)",
        [(e, r, a) for e, r, a in rows],
    )
    return conn


def test_active_user_is_allowed():
    with mock.patch.object(
        admin_mod,
        "get_db_connection",
        return_value=_conn_with([("u@x.pt", "admin", 1)]),
    ):
        assert admin_mod._email_is_allowed("u@x.pt") is True


def test_unknown_address_is_denied():
    with mock.patch.object(
        admin_mod,
        "get_db_connection",
        return_value=_conn_with([("u@x.pt", "admin", 1)]),
    ):
        assert admin_mod._email_is_allowed("stranger@x.pt") is False


def test_inactive_user_is_denied():
    """is_active=0 must revoke access without deleting the row."""
    with mock.patch.object(
        admin_mod,
        "get_db_connection",
        return_value=_conn_with([("old@x.pt", "admin", 0)]),
    ):
        assert admin_mod._email_is_allowed("old@x.pt") is False


def test_admin_emails_env_var_grants_access():
    with mock.patch.dict("os.environ", {"ADMIN_EMAILS": "boss@x.pt"}):
        with mock.patch.object(admin_mod, "get_db_connection", return_value=_conn_with([])):
            assert admin_mod._email_is_allowed("boss@x.pt") is True


def test_database_failure_denies_rather_than_admits():
    """An allowlist that fails open is not an allowlist."""
    with mock.patch.object(
        admin_mod,
        "get_db_connection",
        side_effect=sqlite3.OperationalError("db is gone"),
    ):
        assert admin_mod._email_is_allowed("admin@x.pt") is False


def test_database_failure_does_not_raise_into_the_login_view():
    with mock.patch.object(
        admin_mod,
        "get_db_connection",
        side_effect=sqlite3.OperationalError("db is gone"),
    ):
        # Must not raise: login() relies on this returning cleanly.
        assert admin_mod._email_is_allowed("admin@x.pt") in (True, False)


def test_otp_is_never_written_to_the_journal():
    """The OTP must not appear in the source at all.

    It used to be `print(f"[OTP DEBUG] Generated OTP for ...: {otp}")`, which
    put admin access tokens in the service log.
    """
    source = (Path(__file__).resolve().parent.parent / "src/api/admin.py").read_text(
        encoding="utf-8"
    )
    assert "OTP DEBUG" not in source
    # No statement may print/format the otp variable.
    for line in source.splitlines():
        if "otp" in line.lower() and ("print(" in line or "logger." in line):
            assert "{otp}" not in line and ", otp)" not in line, (
                f"OTP appears to be emitted: {line.strip()}"
            )
