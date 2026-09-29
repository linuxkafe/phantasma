"""The command endpoints must refuse anonymous callers, and must not refuse the owner.

The hole this closes, stated exactly. `_command_authorized()` used to begin:

    if not command_token.enabled():
        return True  # feature not opted into; behave as before

`PHANTASMA_COMMAND_TOKEN` was never set on the production box. So on a service
listening on 0.0.0.0:5000, `POST /comando`, `POST /device_action` and
`POST /api/command` accepted a request from anything that could reach the port --
a command turns the lights on. The page `/` was already behind a session, which
made the whole thing worse rather than better: the visible door was locked and
the window next to it was open, and the browser was the one caller that had
every reason to be let in and no mechanism to be.

Two failures to avoid while closing it, and each has a test:

* **Refusing the owner.** The web UI calls `/comando` and `/device_action` from
  the page. If the gate only understands bearer tokens, the owner's own page
  stops turning lights on and the fix has broken the product.
* **Refusing a program while calling it closed.** A token that is unset is now
  "browsers work, everything else is refused", not "everything works". The
  second is what the old code did and it is the bug.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import command_token  # noqa: E402

COMMAND_PATHS = ("/comando", "/device_action", "/api/command")


@pytest.fixture
def store(monkeypatch):
    """A file-backed user store with one admin, patched in.

    The seed itself lives in tests/helpers_ui_auth.py: three modules need it
    now, and a third copy of a schema seed is a third thing to update when the
    users table moves.
    """
    from tests.helpers_ui_auth import seeded_store

    return seeded_store(monkeypatch)


def _full_app():
    """The real application: the API routes AND the voice UI.

    Not optional. The first version of the session tests used
    `make_app_with_user`, which registers only the UI routes -- so `/comando`
    was not defined there at all, the request came back 404, and the test
    asserted `status != 401` and therefore passed against a server that had no
    such endpoint. A test that passes because the route is missing is worse
    than no test: it reports the gate as working.
    """
    from skills import skill_ui
    from src.api.routes import create_app

    app = create_app()
    skill_ui.register_routes(app)
    return app


@pytest.fixture
def anonymous_app(store):
    """The real app, with no session."""
    return _full_app()


def _signed_in_client(app, email="b@t.test"):
    """A client holding a valid session, without going through the login form.

    The login endpoint is rate limited, so a fixture that POSTs credentials per
    test gets refused partway through the file for a reason that has nothing to
    do with the gate. The session cookie is what the browser would present.
    """
    from src.api import ui_auth

    client = app.test_client()
    with client.session_transaction() as sess:
        sess[ui_auth.SESSION_KEY] = email
        sess[ui_auth.ADMIN_SESSION_KEY] = email
    return client


@pytest.fixture
def signed_in_app(store):
    """The real app, with a browser session for an admin."""
    return _full_app()


# --- the hole ---------------------------------------------------------------


@pytest.mark.parametrize("path", COMMAND_PATHS)
def test_anonymous_is_refused_with_no_token_configured(anonymous_app, monkeypatch, path):
    """The regression. With PHANTASMA_COMMAND_TOKEN unset -- the production
    state -- an anonymous POST must be a 401, not a light switch."""
    monkeypatch.delenv(command_token.ENV_TOKEN, raising=False)
    body = {"prompt": "liga a luz", "device": "sala", "action": "on", "text": "liga a luz"}
    res = anonymous_app.test_client().post(path, json=body)
    assert res.status_code == 401, (
        f"{path} accepted an anonymous POST with no command token configured: "
        f"{res.status_code}. This is the LAN-wide hole."
    )


@pytest.mark.parametrize("path", COMMAND_PATHS)
def test_anonymous_is_refused_with_a_token_configured(anonymous_app, monkeypatch, path):
    monkeypatch.setenv(command_token.ENV_TOKEN, "a-real-looking-token-value")
    body = {"prompt": "liga a luz", "device": "sala", "action": "on", "text": "liga a luz"}
    res = anonymous_app.test_client().post(path, json=body)
    assert res.status_code == 401


def test_a_wrong_token_is_refused(anonymous_app, monkeypatch):
    monkeypatch.setenv(command_token.ENV_TOKEN, "a-real-looking-token-value")
    res = anonymous_app.test_client().post(
        "/comando",
        json={"prompt": "liga a luz"},
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert res.status_code == 401


def test_the_right_token_is_accepted(anonymous_app, monkeypatch):
    """The program path still works -- closing the door must not brick the API."""
    monkeypatch.setenv(command_token.ENV_TOKEN, "a-real-looking-token-value")
    res = anonymous_app.test_client().post(
        "/comando",
        json={"prompt": "ola"},
        headers={"Authorization": "Bearer a-real-looking-token-value"},
    )
    assert res.status_code != 401, "a valid command token was rejected"


# --- the owner is not locked out -------------------------------------------


def test_the_web_ui_can_still_command_the_house(signed_in_app, monkeypatch):
    """The regression that a token-only gate would have introduced.

    The page at `/` is behind a session and calls `/comando` and
    `/device_action` with no Authorization header at all. If the gate did not
    accept that session, the owner's own page would stop working -- a security
    fix that breaks the product is not a fix.

    Asserts the endpoint EXISTS as well as that it is not 401: an earlier
    version of this test ran against an app without the API routes, got a 404,
    and passed because 404 is not 401.
    """
    client = _signed_in_client(signed_in_app)
    monkeypatch.delenv(command_token.ENV_TOKEN, raising=False)
    res = client.post("/comando", json={"prompt": "ola"})
    assert res.status_code != 404, "/comando is not registered in the test app"
    assert res.status_code != 401, (
        "the signed-in browser was refused: the gate accepts a token but not "
        "the session the page already has"
    )


def test_device_action_from_the_page_is_allowed(signed_in_app, monkeypatch):
    """The other POST the page makes. A light switch is the whole point."""
    client = _signed_in_client(signed_in_app)
    monkeypatch.delenv(command_token.ENV_TOKEN, raising=False)
    res = client.post("/device_action", json={"device": "sala", "action": "on"})
    assert res.status_code != 404, "/device_action is not registered in the test app"
    assert res.status_code != 401, "the page could not switch a device"


def test_the_voice_endpoint_is_still_session_gated(signed_in_app):
    """`/api/voz` is not in COMMAND_PATHS and must not have been added to it by
    accident: it is gated by its own session check, and putting it in the token
    list would make the browser mic need a bearer token it cannot have."""
    client = _signed_in_client(signed_in_app)
    res = client.post("/api/voz", json={"audio_base64": "UklGRg=="})
    assert res.status_code != 401, "a signed-in browser was refused /api/voz"
    assert res.status_code != 404, "/api/voz is not registered in the test app"


def test_a_session_for_a_deleted_account_is_refused(signed_in_app, store):
    """The session is re-resolved against the store, so deleting the account
    revokes the ability to command the house -- not just the ability to see the
    page."""
    client = _signed_in_client(signed_in_app)
    assert client.post("/comando", json={"prompt": "ola"}).status_code != 401

    conn = store()
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()

    assert client.post("/comando", json={"prompt": "ola"}).status_code == 401, (
        "a session naming a deleted user still authorised a command"
    )


# --- the cookie that now carries this weight --------------------------------


def test_the_session_cookie_is_httponly_and_samesite(anonymous_app):
    """Accepting a session on a POST replaces the token's CSRF immunity with the
    cookie's. SameSite=Lax withholds the cookie on a cross-site POST, which is
    the control that replaces it -- so it must be set EXPLICITLY, not left to
    whatever the browser happens to default to."""
    cfg = anonymous_app.config
    assert cfg["SESSION_COOKIE_HTTPONLY"] is True, (
        "the session authorises the house now; script must not read it"
    )
    assert cfg["SESSION_COOKIE_SAMESITE"] == "Lax", (
        f"SameSite is {cfg['SESSION_COOKIE_SAMESITE']!r}; without Lax a "
        f"cross-site form could command the house with the owner's cookie"
    )


def test_secure_is_not_forced_on_plain_http(anonymous_app):
    """Forcing Secure on a LAN HTTP service sets a cookie the browser refuses to
    send back, and every session silently breaks."""
    assert anonymous_app.config["SESSION_COOKIE_SECURE"] is False
