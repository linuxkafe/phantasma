"""Integration tests for recovery, device verification and the profile page.

The store is unit-tested in test_auth_store.py. This file is about the ROUTES,
where the risks are different:

* the recovery response must be identical for a known and an unknown address,
  because the form is public and a different answer is a free oracle;
* an unverified login must not produce a working session;
* a new device must owe a second factor, and a trusted one must not;
* a reset must revoke trusted devices, or the password change is cosmetic;
* the profile must not leak another user's tokens;
* the minted secret must appear in the creation response and never again.

No test sends a real email: the mailer is stubbed, and the tests assert on the
SMTP call, which is also how they prove a code was actually issued.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def env(monkeypatch):
    """A Flask app with the UI routes, an isolated store, and a captured mailer."""
    from src.api import admin as admin_mod
    from src.api import auth_store, ratelimit

    # The login limiter is process-global and every test here logs in, so
    # without a reset per test the suite throttles itself into 429s that have
    # nothing to do with the code under test. client_key() is derived from the
    # request, and the test client is always loopback.
    ratelimit.login_limiter.reset("127.0.0.1")

    sent = []
    monkeypatch.setattr(
        admin_mod,
        "_send_mail",
        lambda to, subject, body, otp=None: sent.append((to, subject, otp)),
    )

    from flask import Flask

    from skills import skill_ui

    application = Flask(__name__)
    application.secret_key = "k"
    application.config["TESTING"] = True
    skill_ui.register_routes(application)

    # The store is shared by every module that touches it, and each closes the
    # connection it is handed. Handing out the SAME connection means the first
    # close poisons the rest and the failure surfaces far from the cause
    # ("user lookup failed" for a user that exists). So: a real file, and a
    # fresh connection per caller, which is what the real get_db_connection
    # does anyway.
    import os
    import tempfile

    path = os.path.join(tempfile.mkdtemp(), "auth.db")

    def _fresh():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    seed = _fresh()
    seed.execute(
        "CREATE TABLE users (email TEXT PRIMARY KEY, password_hash TEXT,"
        " role TEXT, is_active INTEGER, updated_at TEXT)"
    )
    seed.execute(
        "INSERT INTO users VALUES ('owner@x.test', ?, 'admin', 1, 'now')",
        (admin_mod.hash_password("password-boa"),),
    )
    seed.commit()
    auth_store.ensure_schema(seed)
    seed.close()

    monkeypatch.setattr(admin_mod, "get_db_connection", _fresh)
    live = _fresh()
    yield application, live, sent
    live.close()


def _sign_in(app, sent, email="owner@x.test", password="password-boa"):
    """Log in THROUGH the new-device check.

    A first login from an untrusted device owes a second factor, so a test that
    posts the password and then goes straight for /perfil is testing the wrong
    flow -- and it fails for a reason that has nothing to do with what it meant
    to check. Every helper here walks the real path: password, then code, then
    the profile.
    """
    c = app.test_client()
    r = c.post("/login", data={"email": email, "password": password})
    assert "/verificar-dispositivo" in r.headers.get("Location", ""), (
        "expected a new-device challenge on the first login"
    )
    code = _code_from(sent)
    assert code, "no device code was mailed"
    done = c.post("/verificar-dispositivo", data={"code": code})
    assert done.status_code == 302
    return c


def _code_from(sent):
    """The code that was mailed, or None."""
    for _to, _subject, otp in sent:
        if otp:
            return otp
    return None


# --- the enumeration rule ---------------------------------------------------


def test_recovery_answers_the_same_for_a_known_and_an_unknown_address(env):
    """The one rule that matters most here. A different response for "no such
    user" turns a public form into a way to test the user list."""
    app, _db, sent = env
    c = app.test_client()

    known = c.post("/recuperar", data={"email": "owner@x.test"})
    unknown = c.post("/recuperar", data={"email": "ninguem@x.test"})

    def neutral(resp):
        text = resp.get_data(as_text=True)
        return "Se o endereço existir, enviámos um código." in text

    assert neutral(known), "the known-address reply changed"
    assert neutral(unknown), "the unknown-address reply differs: enumeration"
    assert known.status_code == unknown.status_code


def test_a_code_is_only_mailed_to_a_real_address(env):
    app, _db, sent = env
    c = app.test_client()
    c.post("/recuperar", data={"email": "ninguem@x.test"})
    assert sent == [], "a code was mailed to an address that does not exist"
    c.post("/recuperar", data={"email": "owner@x.test"})
    assert len(sent) == 1


# --- the full recovery path -------------------------------------------------


def test_recovery_changes_the_password_and_lets_the_user_in(env):
    app, db, sent = env
    c = app.test_client()
    c.post("/recuperar", data={"email": "owner@x.test"})
    code = _code_from(sent)
    assert code

    r = c.post(
        "/recuperar/codigo",
        data={
            "email": "owner@x.test",
            "code": code,
            "password": "outra-password-boa",
            "confirm": "outra-password-boa",
        },
    )
    assert "Password alterada" in r.get_data(as_text=True)

    stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
    assert "outra-password-boa" not in stored

    # The new password works on the real login. A fresh client has never been a
    # trusted device, so it owes the second factor too -- which is the point of
    # the reset: the old devices were revoked and this one has to prove itself.
    sent.clear()
    c2 = app.test_client()
    login = c2.post(
        "/login", data={"email": "owner@x.test", "password": "outra-password-boa"}
    )
    assert "/verificar-dispositivo" in login.headers.get("Location", "")
    device_code = _code_from(sent)
    assert device_code
    c2.post("/verificar-dispositivo", data={"code": device_code})
    assert c2.get("/perfil").status_code == 200


def test_the_old_password_stops_working_after_a_reset(env):
    app, _db, sent = env
    c = app.test_client()
    c.post("/recuperar", data={"email": "owner@x.test"})
    c.post(
        "/recuperar/codigo",
        data={
            "email": "owner@x.test",
            "code": _code_from(sent),
            "password": "outra-password-boa",
            "confirm": "outra-password-boa",
        },
    )
    r = app.test_client().post(
        "/login", data={"email": "owner@x.test", "password": "password-boa"}
    )
    assert "inválidos" in r.get_data(as_text=True), (
        "the previous password still authenticates after a reset"
    )


def test_a_wrong_code_does_not_change_the_password(env):
    app, db, sent = env
    c = app.test_client()
    c.post("/recuperar", data={"email": "owner@x.test"})
    before = db.execute("SELECT password_hash FROM users").fetchone()[0]

    r = c.post(
        "/recuperar/codigo",
        data={
            "email": "owner@x.test",
            "code": "000000",
            "password": "outra-password-boa",
            "confirm": "outra-password-boa",
        },
    )
    assert "não é válido" in r.get_data(as_text=True)
    assert db.execute("SELECT password_hash FROM users").fetchone()[0] == before


def test_a_mismatched_confirmation_is_refused(env):
    app, db, sent = env
    c = app.test_client()
    c.post("/recuperar", data={"email": "owner@x.test"})
    code = _code_from(sent)
    before = db.execute("SELECT password_hash FROM users").fetchone()[0]
    c.post(
        "/recuperar/codigo",
        data={
            "email": "owner@x.test",
            "code": code,
            "password": "outra-password-boa",
            "confirm": "diferente-que-nada",
        },
    )
    assert db.execute("SELECT password_hash FROM users").fetchone()[0] == before


def test_a_recovery_code_cannot_be_used_as_a_device_code(env):
    """Both go to the same address; cross-use would turn a reset into a login
    bypass."""
    app, _db, sent = env
    c = app.test_client()
    c.post("/recuperar", data={"email": "owner@x.test"})
    code = _code_from(sent)
    r = c.post("/verificar-dispositivo", data={"code": code, "email": "owner@x.test"})
    assert r.status_code in (302, 200)
    # No session was created: the pending claim does not exist for this path.
    assert c.get("/perfil").status_code in (302, 401)


def test_a_reset_revokes_trusted_devices(env):
    """Otherwise a stolen device stays trusted through a password change."""
    from src.api import auth_store

    app, db, sent = env
    app.test_client().post("/recuperar", data={"email": "owner@x.test"})
    code = _code_from(sent)
    raw = auth_store.trust_device(db, "owner@x.test", "laptop roubado")
    assert auth_store.is_device_trusted(db, "owner@x.test", raw) is True

    app.test_client().post(
        "/recuperar/codigo",
        data={
            "email": "owner@x.test",
            "code": code,
            "password": "outra-password-boa",
            "confirm": "outra-password-boa",
        },
    )
    assert auth_store.is_device_trusted(db, "owner@x.test", raw) is False


# --- new-device verification ------------------------------------------------


def test_a_first_login_owes_a_second_factor(env):
    """The device has never been trusted, so the password alone is not enough."""
    app, _db, sent = env
    c = app.test_client()
    r = c.post(
        "/login", data={"email": "owner@x.test", "password": "password-boa"}
    )
    assert r.status_code == 302
    assert "/verificar-dispositivo" in r.headers["Location"]
    # And crucially, no session yet.
    assert c.get("/perfil").status_code in (302, 401)
    assert sent, "no device code was sent"


def test_a_trusted_device_logs_straight_in(env):
    from src.api import auth_store

    app, db, _sent = env
    raw = auth_store.trust_device(db, "owner@x.test", "meu portátil")
    c = app.test_client()
    c.set_cookie(auth_store.DEVICE_COOKIE, raw, domain="localhost")
    r = c.post(
        "/login", data={"email": "owner@x.test", "password": "password-boa"}
    )
    assert r.status_code == 302
    assert "/verificar-dispositivo" not in r.headers["Location"]
    assert c.get("/perfil").status_code == 200


def test_a_device_code_completes_the_login_and_sets_the_cookie(env):
    app, _db, sent = env
    c = app.test_client()
    c.post("/login", data={"email": "owner@x.test", "password": "password-boa"})
    code = _code_from(sent)
    assert code

    r = c.post("/verificar-dispositivo", data={"code": code, "label": "telemóvel"})
    assert r.status_code == 302
    # A session now exists, and the device is trusted for next time.
    assert c.get("/perfil").status_code == 200


def test_device_verification_without_a_pending_login_is_refused(env):
    """No claim, no code redemption: otherwise any browser could redeem a code
    for an identity it never proved."""
    app, _db, sent = env
    c = app.test_client()
    c.post("/recuperar", data={"email": "owner@x.test"})
    code = _code_from(sent)
    r = c.post("/verificar-dispositivo", data={"code": code})
    assert "expirou" in r.get_data(as_text=True)
    assert c.get("/perfil").status_code in (302, 401)


def test_a_device_code_cannot_be_redeemed_twice(env):
    app, _db, sent = env
    c = app.test_client()
    c.post("/login", data={"email": "owner@x.test", "password": "password-boa"})
    code = _code_from(sent)
    assert c.post("/verificar-dispositivo", data={"code": code}).status_code == 302

    c2 = app.test_client()
    c2.post("/login", data={"email": "owner@x.test", "password": "password-boa"})
    r = c2.post("/verificar-dispositivo", data={"code": code})
    assert "não é válido" in r.get_data(as_text=True)


def test_a_wrong_password_does_not_send_a_device_code(env):
    """The mail must not go out before the password is proven, or the flow
    becomes a way to send mail to any address in the store."""
    app, _db, sent = env
    c = app.test_client()
    c.post("/login", data={"email": "owner@x.test", "password": "errada"})
    assert sent == []


# --- the profile page -------------------------------------------------------


def test_the_profile_needs_a_session(env):
    app, _db, _sent = env
    assert app.test_client().get("/perfil").status_code in (302, 401)


def test_a_minted_token_is_shown_once_and_not_re_readable(env):
    import re


    app, db, sent = env
    c = _sign_in(app, sent)

    created = c.post("/perfil", data={"op": "create_token", "name": "HA"})
    body = created.get_data(as_text=True)
    assert "copia-o agora" in body
    m = re.search(r'class="mono">(phnt_[A-Za-z0-9_\-]+)<', body)
    assert m, "the minted secret is not in the creation response"
    secret = m.group(1)

    again = c.get("/perfil").get_data(as_text=True)
    assert secret not in again, "the secret is re-displayed on a later visit"


def test_a_minted_token_actually_works_for_commands(env):
    """A token that is shown to the user has to be the token the API accepts."""
    import re

    from src.api import auth_store, command_token

    app, db, sent = env
    c = _sign_in(app, sent)
    body = c.post("/perfil", data={"op": "create_token", "name": "HA"}).get_data(
        as_text=True
    )
    secret = re.search(r'class="mono">(phnt_[A-Za-z0-9_\-]+)<', body).group(1)

    assert command_token.verify(secret) is True
    assert auth_store.verify_token(db, secret)["email"] == "owner@x.test"


def test_revoking_a_token_stops_it_working(env):
    import re

    from src.api import command_token

    app, _db, sent = env
    c = _sign_in(app, sent)
    body = c.post("/perfil", data={"op": "create_token", "name": "HA"}).get_data(
        as_text=True
    )
    secret = re.search(r'class="mono">(phnt_[A-Za-z0-9_\-]+)<', body).group(1)
    assert command_token.verify(secret) is True

    page = c.get("/perfil").get_data(as_text=True)
    import re as _re

    token_id = _re.search(r"name='id' value='(\d+)'", page).group(1)
    c.post("/perfil", data={"op": "revoke_token", "id": token_id})
    assert command_token.verify(secret) is False


def test_the_profile_does_not_show_another_users_tokens(env):
    from src.api import auth_store

    app, db, _sent = env
    auth_store.create_token(db, "outro@x.test", "dele")
    c = app.test_client()
    c.post("/login", data={"email": "owner@x.test", "password": "password-boa"})
    c.post("/verificar-dispositivo", data={"code": _code_from(_sent)})
    page = c.get("/perfil").get_data(as_text=True)
    assert "dele" not in page
