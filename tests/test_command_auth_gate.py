"""Integration tests for the command authorisation gate.

The unit tests in test_command_token.py cover the token comparison. These
cover the thing that actually breaks a home: whether the gate is on or off,
and what happens to existing clients either way.

The compatibility requirement is explicit. `/comando`, `/device_action` and
`/api/command` are called today by the vendored Discord skill and by
third-party scripts, none of which sends an Authorization header. If the gate
rejected them by default, the assistant would go quiet for reasons that look
like a broken microphone. So: with no token configured, everything must behave
exactly as before.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.api import command_token
from src.api.routes import create_app

COMMAND_PATHS = ["/comando", "/device_action", "/api/command"]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv(command_token.ENV_TOKEN, raising=False)
    # The store is seeded because a session is only accepted once the address
    # resolves to a real user: `ui_auth.is_authenticated()` re-reads it on every
    # request, so a cookie naming nobody is correctly treated as signed out.
    from tests.helpers_ui_auth import seeded_store

    seeded_store(monkeypatch)
    pipeline = MagicMock()
    pipeline.respond_to_text.return_value = "luz do balcão ligada."
    app = create_app(pipeline=pipeline)
    app.config["TESTING"] = True
    return app.test_client(), pipeline


def test_without_a_token_anonymous_callers_are_refused(client):
    """CONTRACT CHANGED 2026-09-29, on owner decision.

    This test used to assert the opposite -- "Unset => unchanged", 200 for a
    caller with no credential at all -- which is precisely the hole: the token
    was never set on the production box, so `POST /comando` and
    `POST /device_action` were open to anything that could reach port 5000, and
    a command turns the lights on. The visible door (`/`) was locked by a
    session, which made it worse: the page was protected and the window beside
    it was not.

    The new contract is "either credential, never neither": a browser with a
    session, or a program with the bearer token. Unset is not "open", it is
    "browsers only".
    """
    c, _ = client
    resp = c.post("/comando", json={"prompt": "liga a luz do balcao"})
    assert resp.status_code == 401, (
        "an anonymous caller was accepted with no command token configured"
    )


def test_without_a_token_device_action_is_refused(client):
    """Same contract change, on the endpoint that actually switches things."""
    c, _ = client
    resp = c.post("/device_action", json={"device": "luz do balcão", "action": "ligar"})
    assert resp.status_code == 401


def test_without_a_token_a_browser_session_still_works(client, monkeypatch):
    """The other half of the contract: closing the door must not lock the owner
    out of their own page, which calls these endpoints with no header at all."""
    from src.api import ui_auth

    monkeypatch.delenv(command_token.ENV_TOKEN, raising=False)
    c, _ = client
    with c.session_transaction() as sess:
        sess[ui_auth.SESSION_KEY] = "b@t.test"
        sess[ui_auth.ADMIN_SESSION_KEY] = "b@t.test"
    resp = c.post("/comando", json={"prompt": "liga a luz do balcao"})
    assert resp.status_code == 200, (
        f"the signed-in browser was refused ({resp.status_code}); the gate "
        f"accepts a token but not the session the page already has"
    )


def test_with_a_token_a_request_without_it_is_rejected(client, monkeypatch):
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    c, pipeline = client

    resp = c.post("/comando", json={"prompt": "liga a luz do balcao"})
    assert resp.status_code == 401

    # The critical assertion: it was rejected BEFORE reaching the pipeline, so
    # no light was actually switched.
    pipeline.respond_to_text.assert_not_called()


def test_with_a_token_the_bearer_header_is_accepted(client, monkeypatch):
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    c, _ = client
    resp = c.post(
        "/comando",
        json={"prompt": "liga a luz do balcao"},
        headers={"Authorization": "Bearer s3cret-token"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_wrong_token_is_rejected(client, monkeypatch):
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    c, _ = client
    resp = c.post(
        "/comando",
        json={"prompt": "desliga tudo"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_device_action_is_gated_too(client, monkeypatch):
    """A device action is a real-world action, not a read."""
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    c, _ = client
    resp = c.post("/device_action", json={"device": "luz do balcão", "action": "ligar"})
    assert resp.status_code == 401


def test_reads_of_the_house_require_a_credential_now(client, monkeypatch):
    """CONTRACT CHANGED 2026-09-29, on owner decision.

    This test asserted the opposite, and its reason is the sentence worth
    keeping: "they disclose nothing sensitive". That was true when the service
    only answered inside the house. It stopped being true the moment the service
    got a hostname: `https://phantasma.linuxkafe.com/get_devices` was verified
    returning the full inventory of a private home -- every light, socket and
    appliance -- to an anonymous caller, with `Access-Control-Allow-Origin: *`
    so any website could read it.

    `/help` and `/api/auth` stay open: one is the command vocabulary, which is
    documentation, and the other describes the auth mechanism without holding a
    secret. The readings do not stay open.
    """
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    c, _ = client
    for path in ("/get_devices", "/api/devices", "/api/graph/audit"):
        assert c.get(path).status_code == 401, (
            f"{path} discloses the home and is still readable with no credential"
        )
    for path in ("/help", "/api/auth"):
        assert c.get(path).status_code == 200, f"{path} should stay open: it is documentation"


def test_a_query_string_token_is_not_accepted(client, monkeypatch):
    """Query strings land in the access log and in browser history. A token
    accepted there is a token written down by the infrastructure."""
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    c, _ = client
    resp = c.post("/comando?token=s3cret-token", json={"prompt": "liga a luz"})
    assert resp.status_code == 401


def test_auth_status_reports_state_without_secrets(client, monkeypatch):
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    c, _ = client
    body = c.get("/api/auth").get_data(as_text=True)
    assert "s3cret-token" not in body
    assert '"enabled": true' in body or '"enabled":true' in body
