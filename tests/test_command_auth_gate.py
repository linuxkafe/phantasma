"""Integration tests for the command authorisation gate.

The unit tests in test_command_token.py cover the token comparison. These
cover the thing that actually breaks a home: whether the gate is on or off,
and what happens to existing clients either way.

The compatibility requirement is explicit. `/comando`, `/device_action` and
`/api/command` are called today by the Android companion app and the vendored
Discord skill, neither of which sends an Authorization header. If the gate
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
    pipeline = MagicMock()
    pipeline.respond_to_text.return_value = "luz do balcão ligada."
    app = create_app(pipeline=pipeline)
    app.config["TESTING"] = True
    return app.test_client(), pipeline


def test_without_a_token_the_existing_clients_still_work(client):
    """The Android app and the Discord skill send no token. Unset => unchanged."""
    c, _ = client
    resp = c.post("/comando", json={"prompt": "liga a luz do balcao"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_without_a_token_device_action_still_works(client):
    c, _ = client
    resp = c.post("/device_action", json={"device": "luz do balcão", "action": "ligar"})
    assert resp.status_code == 200


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


def test_reads_stay_open_when_the_token_is_set(client, monkeypatch):
    """The UI renders its tiles from these. Locking them would mean a logged-in
    browser shows an empty device list, and they disclose nothing sensitive."""
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    c, _ = client
    for path in ("/get_devices", "/help", "/api/devices", "/api/auth"):
        assert c.get(path).status_code == 200, path


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
