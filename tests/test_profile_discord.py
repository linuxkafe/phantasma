"""The profile page and the bot, end to end through the app.

`tests/test_discord_identity.py` covers the store. This one covers the two
things the owner will actually touch: the form in /perfil, and whether what is
saved there is what the bot then honours.

The bot's rule is driven through `src.api.discord_access.check`, which is where
it now lives, with its environment lists emptied -- because the precedence is the
point: the lists in the environment are the owner's deliberate out-of-band
decision and a profile field must not be able to take a capability away from
them. With the lists emptied, everything that gets access came from a profile.

Note what is NOT here: a fake `discord` module. The rule was untestable while it
lived in `skills/skill_discord.py`, because importing that builds a live client
against the discord.py SDK -- installed in production's venv and not in the
development one. Getting a test to run needed a fake that grew a new attribute
for every line of the skill it imported past, and a fake that grows is a place
where a test stops testing. The rule was moved out instead; this file imports no
SDK and never will.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app_and_client(monkeypatch):
    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store
    from src.api.routes import create_app

    path = os.path.join(tempfile.mkdtemp(), "profile.db")

    def _fresh():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    seed = _fresh()
    seed.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT,"
        " password_hash TEXT, role TEXT, is_active INTEGER, created_at TEXT,"
        " updated_at TEXT)"
    )
    seed.execute(
        "INSERT INTO users (email, password_hash, role, is_active, created_at,"
        " updated_at) VALUES ('u@t.test','x','user',1,'now','now')")
    seed.commit()
    auth_store.ensure_schema(seed)
    seed.close()

    original = admin_mod.get_db_connection
    admin_mod.get_db_connection = _fresh
    app = create_app()
    skill_ui.register_routes(app)
    app.config["TESTING"] = True
    try:
        yield app, _fresh
    finally:
        admin_mod.get_db_connection = original


def _conn():
    """The live connection the app itself uses, not a second one."""
    from src.api import admin as admin_mod

    return admin_mod.get_db_connection()


def _discord_id(email="u@t.test"):
    from src.api import auth_store

    return auth_store.get_discord_id(email, _conn())


def _signed_in(app):
    from src.api import ui_auth

    client = app.test_client()
    with client.session_transaction() as sess:
        sess[ui_auth.SESSION_KEY] = "u@t.test"
        sess[ui_auth.ADMIN_SESSION_KEY] = "u@t.test"
    return client


# ------------------------------------------------------------- the form ----

def test_the_profile_offers_the_discord_field(app_and_client):
    app, _fresh = app_and_client
    html = _signed_in(app).get("/perfil").get_data(as_text=True)
    assert 'name="discord_id"' in html, "the profile has no Discord field"
    assert 'value="set_discord_id"' in html, "and no way to save it"
    assert "Discord" in html


def test_the_field_is_numeric_on_the_phone(app_and_client):
    """A phone keyboard with letters is a phone keyboard that produces ids that
    cannot exist. `inputmode=numeric` is the whole difference."""
    app, _fresh = app_and_client
    html = _signed_in(app).get("/perfil").get_data(as_text=True)
    field = html[html.index('name="discord_id"') - 200:html.index('name="discord_id"') + 80]
    assert 'inputmode="numeric"' in field, field
    assert 'pattern="[0-9]*"' in field, field


def test_saving_from_the_profile_persists_and_is_shown_back(app_and_client):
    app, _fresh = app_and_client
    client = _signed_in(app)
    body = client.post("/perfil", data={"op": "set_discord_id",
                                       "discord_id": "123456789012345678"})
    assert body.status_code == 200
    assert _discord_id() == "123456789012345678", "the id did not persist"
    html = client.get("/perfil").get_data(as_text=True)
    assert 'value="123456789012345678"' in html, "the page does not show it back"


def test_the_result_is_always_said_out_loud(app_and_client):
    """A form that saves silently is a form whose failure the user discovers
    from Discord saying "Acesso negado." an hour later."""
    app, _fresh = app_and_client
    client = _signed_in(app)
    ok_html = client.post("/perfil", data={"op": "set_discord_id",
                                          "discord_id": "555"}).get_data(as_text=True)
    assert "ID do Discord guardado" in ok_html, ok_html[:400]
    bad_html = client.post("/perfil", data={"op": "set_discord_id",
                                           "discord_id": "abc"}).get_data(as_text=True)
    assert "algarismos" in bad_html, "a refused id was not explained"
    assert "guardado" not in bad_html, "a refused id was reported as saved"


def test_clearing_from_the_profile(app_and_client):
    app, _fresh = app_and_client
    client = _signed_in(app)
    client.post("/perfil", data={"op": "set_discord_id", "discord_id": "777"})
    html = client.post("/perfil", data={"op": "clear_discord_id"}).get_data(as_text=True)
    assert "removido" in html
    assert _discord_id() is None


def test_a_second_account_cannot_take_a_claimed_id_through_the_form(app_and_client):
    """The same refusal, through the HTTP surface rather than the store."""
    app, _fresh = app_and_client
    from src.api import auth_store

    # ONE connection for the write and the commit. The fixture's factory opens a
    # new connection per call, so `_conn().execute(...)` followed by
    # `_conn().commit()` writes on one connection and commits on another: the
    # row is discarded with the first one and the second account silently does
    # not exist. That is what made this test fail with a redirect to /login
    # rather than with the conflict it was written to catch.
    conn = _conn()
    conn.execute(
        "INSERT INTO users (email, password_hash, role, is_active, created_at,"
        " updated_at) VALUES ('v@t.test','x','user',1,'now','now')")
    conn.commit()
    assert auth_store.set_discord_id("u@t.test", "999", conn)[0]

    from src.api import ui_auth

    other = app.test_client()
    with other.session_transaction() as sess:
        sess[ui_auth.SESSION_KEY] = "v@t.test"
        sess[ui_auth.ADMIN_SESSION_KEY] = "v@t.test"
    html = other.post("/perfil", data={"op": "set_discord_id",
                                       "discord_id": "999"}).get_data(as_text=True)
    assert "já está associado" in html, "a second account took a claimed id"
    assert _discord_id("v@t.test") is None


def test_an_anonymous_visitor_gets_no_profile(app_and_client):
    app, _fresh = app_and_client
    res = app.test_client().get("/perfil")
    assert res.status_code in (302, 303, 401, 403) or b"Entrar" in res.data, (
        "the profile answered an anonymous caller with content"
    )


# ------------------------------------------------------------- the bot ----

def _check(user_id, monkeypatch):
    from src.api import discord_access

    monkeypatch.setattr(discord_access.config, "DISCORD_ADMIN_USERS", [], raising=False)
    monkeypatch.setattr(discord_access.config, "DISCORD_STANDARD_USERS", [], raising=False)
    discord_access.reset_quotas()
    # The skills the prompt lands on, named explicitly, and one the guest tier
    # allows. Passing nothing used to mean "no idea, allow it", which under the
    # guest rule is a refusal -- and a refusal in a test about whether a claimed
    # id is honoured tests the wrong thing entirely. Naming a DEVICE skill would
    # have the same problem for the same reason: a guest is refused those on
    # purpose, so the test would be asserting a refusal and calling it identity.
    #
    # The assertion below is unchanged. If the id failed to resolve, the message
    # would be "Acesso negado" rather than a skill complaint, and it would still
    # fail -- so this still tests identity, not access.
    return discord_access.check(user_id, "liga a luz", ["skill_weather"])


def test_the_bot_honours_a_claimed_id(app_and_client, monkeypatch):
    app, _fresh = app_and_client
    from src.api import auth_store

    auth_store.set_discord_id("u@t.test", "300300300")
    allowed, message = _check("300300300", monkeypatch)
    assert allowed, f"a claimed id was refused by the bot: {message}"


def test_the_bot_refuses_an_unclaimed_id(app_and_client, monkeypatch):
    allowed, message = _check("400400400", monkeypatch)
    assert not allowed
    assert message


def test_the_environment_lists_keep_precedence(app_and_client, monkeypatch):
    """The owner's deliberate list is not something a profile field can take
    away. Tested with the list RESTORED, not only emptied."""
    app, _fresh = app_and_client
    from src.api import auth_store, discord_access

    auth_store.set_discord_id("u@t.test", "500500500")
    monkeypatch.setattr(discord_access.config, "DISCORD_ADMIN_USERS", [600600600],
                        raising=False)
    monkeypatch.setattr(discord_access.config, "DISCORD_STANDARD_USERS", [], raising=False)
    allowed, _ = discord_access.check(600600600, "liga a luz", ["skill_weather"])
    assert allowed, "an id in DISCORD_ADMIN_USERS lost its access"

    # And a plain standard id from the environment, with no profile behind it.
    monkeypatch.setattr(discord_access.config, "DISCORD_ADMIN_USERS", [], raising=False)
    monkeypatch.setattr(discord_access.config, "DISCORD_STANDARD_USERS", [700700700],
                        raising=False)
    # The store is stubbed to "the owner never touched this key", because it is
    # no longer equivalent. Since the revocation fix the ADMIN PAGE is
    # authoritative for the id lists once the owner has used it, and only the
    # `.env` applies otherwise -- so on a machine where the page has been saved,
    # the `.env` list is genuinely not in force.
    #
    # This test passed in dev and failed in production for exactly that reason:
    # `/opt/phantasma` has DISCORD_STANDARD_USERS in `app_settings`, written by
    # the page, and the dev database does not. A test that assumed the two
    # machines agree is a test that only runs on one of them.
    monkeypatch.setattr(discord_access, "_owner_set",
                        lambda key: (None, True))
    allowed, _ = discord_access.check(700700700, "liga a luz", ["skill_weather"])
    assert allowed, "an id in DISCORD_STANDARD_USERS lost its access"


def test_the_bot_refuses_rather_than_guesses_on_a_duplicate(app_and_client, monkeypatch):
    app, _fresh = app_and_client
    from src.api import auth_store

    # One connection again, and the commit happens before the lookup: the
    # lookup opens its own connection, and an uncommitted write on this one
    # holds a RESERVED lock that makes it fail with "database is locked".
    conn = _conn()
    auth_store.ensure_schema(conn)
    conn.execute("DROP INDEX IF EXISTS idx_users_discord_id")
    conn.execute(
        "INSERT INTO users (email, password_hash, role, is_active, created_at,"
        " updated_at) VALUES ('w@t.test','x','admin',1,'now','now')")
    conn.execute("UPDATE users SET discord_id = '800800800'")
    conn.commit()
    conn.close()
    allowed, _ = _check("800800800", monkeypatch)
    assert not allowed, "an id claimed by two accounts was allowed"
    assert _discord_id("w@t.test") == "800800800"
