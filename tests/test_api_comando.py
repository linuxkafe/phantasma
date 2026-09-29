"""Tests for the legacy /comando endpoint (T035)."""

from unittest.mock import MagicMock

import pytest

from src.api import ui_auth
from src.api.routes import create_app


@pytest.fixture()
def api_client(monkeypatch):
    """Flask test client with a stub pipeline exposing respond_to_text.

    Carries a session AND a seeded store, because these tests are about the
    ENDPOINT's behaviour -- the response shape for an empty prompt, the mapping
    of a pipeline failure to 502 -- and the command gate refuses an anonymous
    caller before the handler runs. Without this they were all asserting a 401
    and calling it a 400: a test that cannot fail for the reason it exists.
    """
    from tests.helpers_ui_auth import seeded_store

    seeded_store(monkeypatch)
    pipeline = MagicMock()
    pipeline.respond_to_text.return_value = "Hoje em Porto: céu pouco nublado."
    app = create_app(pipeline=pipeline)
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[ui_auth.SESSION_KEY] = "b@t.test"
        sess[ui_auth.ADMIN_SESSION_KEY] = "b@t.test"
    return client, pipeline


def test_comando_returns_ok_response(api_client):
    """POST /comando with a prompt returns the pipeline response."""
    client, pipeline = api_client
    resp = client.post("/comando", json={"prompt": "como está o tempo?"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "céu pouco nublado" in data["response"]
    pipeline.respond_to_text.assert_called_once_with("como está o tempo?")


def test_comando_empty_prompt_returns_400(api_client):
    """POST /comando with an empty prompt is rejected."""
    client, _ = api_client
    resp = client.post("/comando", json={"prompt": ""})
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "error"


def test_comando_missing_json_returns_400(api_client):
    """POST /comando without a JSON body is rejected."""
    client, _ = api_client
    resp = client.post("/comando", data="", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "error"


def test_comando_pipeline_error_returns_502(api_client):
    """POST /comando surfaces None (no skill/LLM) as 502."""
    client, pipeline = api_client
    pipeline.respond_to_text.return_value = None
    resp = client.post("/comando", json={"prompt": "olá"})
    assert resp.status_code == 502
    assert resp.get_json()["status"] == "error"
