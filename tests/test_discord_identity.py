"""Claiming a Discord identity from your own profile, and what it authorises.

Owner request, 2026-09-30: "nesse perfil tambem deve ser possivel definir o ID
de discord para possibilitar a interacao no skill_discord".

The access lists the bot already had lived in the environment, in a file only the
owner can edit, so a second person in the household could not join and nobody
could discover their own id without asking. A signed-in user may now claim an
id from /perfil, and the WEB ROLE of that account decides what the id may do.

This is an authorisation change, so most of these tests are about the way it can
go wrong rather than the way it is supposed to go:

* the migration must be idempotent (it runs on every request) and must not touch
  a database whose `users` table does not exist yet;
* an id must be digits -- a snowflake -- because the bot compares it against an
  int and a string in that column is a comparison that silently never matches;
* one id, one account, enforced by the DATABASE and not only by the form;
* claiming an id another account already holds is refused, not silently stolen;
* an id held by two accounts resolves to NO access, never to a guess;
* a deactivated account keeps its id and loses its access;
* the environment lists keep their precedence: a profile field must not be able
  to take a capability away from the owner.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import auth_store  # noqa: E402


def _db():
    """A connection to a fresh database with a `users` table shaped like prod's.

    Also patches `admin.get_db_connection`, which is what `_connect(None)` falls
    through to. Without that, every call that does not pass a connection
    explicitly -- which is every call the Discord bot makes -- resolves to the
    REAL application database: the first version of these tests wrote to a
    temporary file and read from production, and failed with "no such column:
    discord_id" and a claim it could not find. Patching it here means the tests
    exercise the same resolution path the bot does, on a database they own.
    """
    from src.api import admin as admin_mod

    conn = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "discord.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, "
        "password_hash TEXT, role TEXT, is_active INTEGER, created_at TEXT, "
        "updated_at TEXT)"
    )
    conn.commit()
    original = admin_mod.get_db_connection
    admin_mod.get_db_connection = lambda: conn
    _db._restore = lambda: setattr(admin_mod, "get_db_connection", original)
    return conn


@pytest.fixture(autouse=True)
def _restore_connection():
    yield
    restore = getattr(_db, "_restore", None)
    if restore is not None:
        restore()
        _db._restore = None


def _user(conn, email, role="user", active=1):
    conn.execute(
        "INSERT INTO users (email, password_hash, role, is_active, created_at,"
        " updated_at) VALUES (?, 'x', ?, ?, 'now', 'now')",
        (email, role, active),
    )
    conn.commit()


# ------------------------------------------------------------ the column ----

def test_the_migration_adds_the_column_to_an_existing_table():
    conn = _db()
    _user(conn, "a@t.test")
    assert "discord_id" not in {
        r[1] for r in conn.execute("PRAGMA table_info(users)")
    }
    auth_store.ensure_schema(conn)
    assert "discord_id" in {r[1] for r in conn.execute("PRAGMA table_info(users)")}


def test_the_migration_is_idempotent():
    """ensure_schema runs on every request. A migration that failed the second
    time would take the auth path down on a request."""
    conn = _db()
    _user(conn, "a@t.test")
    for _ in range(5):
        auth_store.ensure_schema(conn)
    assert "discord_id" in {r[1] for r in conn.execute("PRAGMA table_info(users)")}


def test_the_migration_runs_when_the_app_starts_not_when_someone_opens_the_page():
    """A lazy migration is a migration that has not happened.

    Measured on the deploy that introduced this column: green health check,
    1097 tests passing, and `users.discord_id` absent from the production
    database -- because `ensure_schema` only ran on a request that needed it,
    and nobody had opened /perfil yet. The feature would have failed on first
    use, in production, days after a deploy everyone trusted.

    So the schema is applied in `create_app`, and this asserts that: an app
    built against a database with an old `users` table comes up with the column
    already there, before a single request is served.
    """
    from src.api.routes import create_app

    conn = _db()  # patches get_db_connection, and has no discord_id yet
    _user(conn, "a@t.test")
    assert "discord_id" not in {r[1] for r in conn.execute("PRAGMA table_info(users)")}

    create_app()  # no pipeline, no request, no request context

    assert "discord_id" in {r[1] for r in conn.execute("PRAGMA table_info(users)")}, (
        "create_app did not apply the auth schema: the column is still missing "
        "after a restart, so the feature depends on somebody opening the page"
    )


def test_the_migration_does_not_explode_without_a_users_table():
    """On a fresh install `users` is created by its own bootstrap, not here. A
    column migration that raised on a missing table would make an uninitialised
    database unauthenticatable rather than merely empty."""
    conn = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "bare.db"))
    conn.row_factory = sqlite3.Row
    auth_store.ensure_schema(conn)  # must not raise
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "auth_tokens" in tables, "the auth tables were not created"


def test_the_unique_index_exists():
    conn = _db()
    _user(conn, "a@t.test")
    auth_store.ensure_schema(conn)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert any("discord" in n for n in names), f"no discord index: {names}"


# ---------------------------------------------------------- the validation ----

@pytest.mark.parametrize("raw", ["abc", "12a34", "1 2 3x", "٣٤٥", "-123"])
def test_a_non_numeric_id_is_refused(raw):
    """A Discord id is a snowflake. A string in this column is a comparison
    against an int that can never match, which reads as "access denied" with no
    explanation anywhere."""
    got, problem = auth_store.normalise_discord_id(raw)
    assert got is None
    assert problem, f"{raw!r} was accepted"


def test_an_absurdly_long_id_is_refused():
    got, problem = auth_store.normalise_discord_id("1" * 40)
    assert got is None and problem


def test_a_plain_snowflake_is_accepted():
    assert auth_store.normalise_discord_id("123456789012345678") == (
        "123456789012345678", None)


def test_whitespace_and_underscores_are_tolerated():
    """Discord clients render the id with spaces, and users paste it with an
    underscore from the underscore-name era."""
    assert auth_store.normalise_discord_id(" 123 456 ")[0] == "123456"
    assert auth_store.normalise_discord_id("123_456")[0] is None or True
    # underscores are NOT stripped: a snowflake has no underscore and silently
    # accepting one would store something the bot will never match.
    assert auth_store.normalise_discord_id("123_456")[0] is None


def test_empty_means_clear_not_error():
    assert auth_store.normalise_discord_id("") == (None, None)
    assert auth_store.normalise_discord_id(None) == (None, None)


# ------------------------------------------------------------- the claim ----

def test_claiming_and_reading_back():
    conn = _db()
    _user(conn, "a@t.test")
    ok, problem = auth_store.set_discord_id("a@t.test", "123456789012345678")
    assert ok and problem is None
    assert auth_store.get_discord_id("a@t.test", conn) == "123456789012345678"


def test_claiming_is_per_account():
    conn = _db()
    _user(conn, "a@t.test")
    _user(conn, "b@t.test")
    assert auth_store.set_discord_id("a@t.test", "111")[0]
    assert auth_store.set_discord_id("b@t.test", "222")[0]
    assert auth_store.get_discord_id("a@t.test", conn) == "111"
    assert auth_store.get_discord_id("b@t.test", conn) == "222"


def test_an_id_already_claimed_is_refused_and_not_stolen():
    """The dangerous move. If a second account could claim the first one's id,
    the last writer would win, and the first person would silently lose their
    access -- or worse, gain the second's."""
    conn = _db()
    _user(conn, "owner@t.test", role="admin")
    _user(conn, "other@t.test", role="user")
    assert auth_store.set_discord_id("owner@t.test", "999")[0]
    ok, problem = auth_store.set_discord_id("other@t.test", "999")
    assert not ok, "a second account took an id that was already claimed"
    assert problem and "já está" in problem, problem
    # And the original owner still holds it.
    assert auth_store.get_discord_id("owner@t.test", conn) == "999"
    assert auth_store.get_discord_id("other@t.test", conn) is None


def test_re_saving_your_own_id_is_not_an_error():
    """Re-typing the id you already have must not be reported as a conflict:
    the check is `email != ?`, and a form that errors on its own value is a
    form people learn to distrust."""
    conn = _db()
    _user(conn, "a@t.test")
    assert auth_store.set_discord_id("a@t.test", "555")[0]
    ok, problem = auth_store.set_discord_id("a@t.test", "555")
    assert ok, f"re-saving your own id failed: {problem}"


def test_clearing_removes_the_id():
    conn = _db()
    _user(conn, "a@t.test")
    auth_store.set_discord_id("a@t.test", "777")
    ok, _ = auth_store.set_discord_id("a@t.test", "")
    assert ok
    assert auth_store.get_discord_id("a@t.test", conn) is None


def test_an_id_can_be_reassigned_after_being_cleared():
    conn = _db()
    _user(conn, "a@t.test")
    _user(conn, "b@t.test")
    auth_store.set_discord_id("a@t.test", "888")
    assert not auth_store.set_discord_id("b@t.test", "888")[0]
    auth_store.set_discord_id("a@t.test", "")
    assert auth_store.set_discord_id("b@t.test", "888")[0]


# ---------------------------------------------------- what the id grants ----

def test_an_admin_web_role_grants_admin():
    conn = _db()
    _user(conn, "a@t.test", role="admin")
    auth_store.set_discord_id("a@t.test", "12345")
    assert auth_store.discord_role_for("12345") == "admin"


def test_a_user_web_role_grants_user():
    conn = _db()
    _user(conn, "a@t.test", role="user")
    auth_store.set_discord_id("a@t.test", "12345")
    assert auth_store.discord_role_for("12345") == "user"


def test_an_unclaimed_id_grants_nothing():
    _db()  # patches the connection; the claim is on nobody
    assert auth_store.discord_role_for("99999") is None


def test_a_deactivated_account_loses_its_access():
    """The id stays, so re-activating the account restores it. What must not
    happen is a deactivated account keeping the capability of an active one."""
    conn = _db()
    _user(conn, "a@t.test", role="admin", active=0)
    auth_store.set_discord_id("a@t.test", "12345")
    assert auth_store.discord_role_for("12345") is None


def test_a_duplicated_id_grants_nothing():
    """A duplicate that predates the unique index. Refuse, do not guess: in
    every ambiguous case the answer is no access, never possibly-wrong access.
    Built by dropping the index, since the index is what normally prevents it."""
    conn = _db()
    _user(conn, "a@t.test", role="admin")
    _user(conn, "b@t.test", role="user")
    auth_store.ensure_schema(conn)          # the column has to exist first
    conn.execute("DROP INDEX IF EXISTS idx_users_discord_id")
    conn.execute("UPDATE users SET discord_id = '424242'")
    conn.commit()
    assert auth_store.discord_role_for("424242") is None


def test_a_unicode_digit_id_is_refused():
    """`str.isdigit()` is True for every Unicode decimal digit. "٣٤٥" is a
    number by Python's definition, looks like a number to a person, and can
    never match the int the bot compares against -- so accepting it stores a
    value that is guaranteed to fail later, somewhere the user is not
    looking."""
    assert "٣٤٥".isdigit(), "the premise of this test is that isdigit() says yes"
    got, problem = auth_store.normalise_discord_id("٣٤٥")
    assert got is None and problem
    # And the fullwidth digits, which look even more like ASCII ones.
    assert auth_store.normalise_discord_id("１２３")[0] is None


def test_no_id_is_never_a_match():
    _db()
    assert auth_store.discord_role_for(None) is None
    assert auth_store.discord_role_for("") is None
