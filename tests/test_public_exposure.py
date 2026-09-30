"""The service is on the public internet. These are the tests that say so.

Verified live on 2026-09-29: `https://phantasma.linuxkafe.com` resolves to a
public address, terminates TLS somewhere else, and forwards to this box. The
service is not a LAN-only application that happens to have a URL; it is on the
internet, and every endpoint decision has to be made on that assumption.

Two things were wrong, and both were found by fetching the real URL rather than
by reading the code:

1. `Access-Control-Allow-Origin: *` on every response. `GET /get_devices`
   returned the inventory of a private home to an anonymous caller, and `*`
   told the browser any site may read it -- so a page the owner visited could
   exfiltrate it. Nothing needed to be broken for that to work.
2. The disclosure endpoints had no gate at all. "Read-only endpoints are left
   open on purpose" was a decision made when the service was only reachable
   inside the house, and it was never revisited when the house got a hostname.

The tests here assert the properties, not the implementation: no response may
carry a wildcard origin, and the house may not be readable without a
credential.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import command_token, ui_auth  # noqa: E402

# Everything that discloses the home: the inventory, the live readings, the
# memory graph, and the expensive machine endpoints.
DISCLOSURE_PATHS = (
    "/get_devices",
    "/api/devices",
    "/device_status?nickname=casa",
    "/api/graph/audit",
    "/api/reactions",
)


def _app():
    from skills import skill_ui
    from src.api import auth_store
    from src.api.routes import create_app

    app = create_app()
    skill_ui.register_routes(app)
    path = os.path.join(tempfile.mkdtemp(), "pub.db")

    def _fresh():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    seed = _fresh()
    seed.execute(
        "CREATE TABLE users (email TEXT PRIMARY KEY, password_hash TEXT,"
        " role TEXT, is_active INTEGER, updated_at TEXT)"
    )
    seed.execute("INSERT INTO users VALUES ('b@t.test','x','admin',1,'now')")
    seed.commit()
    auth_store.ensure_schema(seed)
    seed.close()
    app.get_db_connection = _fresh
    return app


@pytest.fixture
def store(monkeypatch):
    from tests.helpers_ui_auth import seeded_store

    return seeded_store(monkeypatch)


@pytest.fixture
def anon(monkeypatch, store):
    """The real app, no session, no command token."""
    monkeypatch.delenv(command_token.ENV_TOKEN, raising=False)
    return _app().test_client()


def _signed_in(app, email="b@t.test"):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[ui_auth.SESSION_KEY] = email
        sess[ui_auth.ADMIN_SESSION_KEY] = email
    return client


# --- the wildcard -----------------------------------------------------------


def test_no_response_carries_a_wildcard_cors_origin(store):
    """The single most important assertion in this file.

    `*` is the difference between "private unless you find the URL" and "any
    website the owner visits can read it". A browser enforces CORS on the
    victim's behalf, so this header hands the data to a page the owner has
    never thought about.
    """
    app = _app()
    client = _signed_in(app)
    paths = ["/", "/get_devices", "/api/health", "/help", "/api/devices"]
    for path in paths:
        res = client.get(path, headers={"Origin": "https://attacker.example"})
        acao = res.headers.get("Access-Control-Allow-Origin")
        assert acao != "*", (
            f"{path} still answers Access-Control-Allow-Origin: * to a foreign "
            f"origin; any site on the internet can read this response"
        )


def test_cors_is_absent_by_default(store):
    """Nothing configured means no CORS headers at all, not a permissive one."""
    app = _app()
    res = _signed_in(app).get("/get_devices", headers={"Origin": "https://x.example"})
    assert "Access-Control-Allow-Origin" not in res.headers


def test_an_allow_listed_origin_is_reflected_exactly(monkeypatch, store):
    """A separate front-end can be permitted, but only by exact origin."""
    monkeypatch.setenv("PHANTASMA_CORS_ORIGINS", "https://ui.example, https://other.test")
    app = _app()
    res = _signed_in(app).get("/get_devices", headers={"Origin": "https://ui.example"})
    assert res.headers.get("Access-Control-Allow-Origin") == "https://ui.example"
    assert "Origin" in (res.headers.get("Vary") or ""), (
        "the response depends on the Origin, so a shared cache must key on it"
    )


def test_a_lookalike_origin_is_not_allowed(monkeypatch, store):
    """`https://ui.example.evil.test` must not match `https://ui.example`."""
    monkeypatch.setenv("PHANTASMA_CORS_ORIGINS", "https://ui.example")
    app = _app()
    res = _signed_in(app).get(
        "/get_devices", headers={"Origin": "https://ui.example.evil.test"}
    )
    assert "Access-Control-Allow-Origin" not in res.headers


# --- the house is not public ------------------------------------------------


@pytest.mark.parametrize("path", DISCLOSURE_PATHS)
def test_the_house_is_not_readable_anonymously(anon, path):
    res = anon.get(path)
    assert res.status_code == 401, (
        f"GET {path} answered {res.status_code} to an anonymous caller on the "
        f"public internet; it discloses the home"
    )


def test_a_session_still_reads_everything_the_page_needs(store):
    """The regression a read-gate introduces if done carelessly: the page at `/`
    is the only client we can see, and it fetches these on load with no header."""
    app = _app()
    client = _signed_in(app)
    for path in DISCLOSURE_PATHS:
        res = client.get(path)
        assert res.status_code != 401, f"the signed-in page cannot read {path}"
        assert res.status_code != 404, f"{path} is not registered in the test app"


def test_the_machine_token_can_still_read_the_house(anon, monkeypatch, store):
    """A program on the network is not a browser and has no session. Without this
    it loses the device list, and the only way to keep it is to leave the house
    public -- which is the thing being fixed."""
    monkeypatch.setenv(command_token.ENV_TOKEN, "a-real-looking-token-value")
    res = anon.get(
        "/get_devices", headers={"Authorization": "Bearer a-real-looking-token-value"}
    )
    assert res.status_code != 401, (
        "a valid machine token was refused the device list; the Discord skill "
        "and any script would break, and the alternative is not leaving it open"
    )


def test_the_machine_token_still_cannot_reach_the_admin_surface(anon, monkeypatch, store):
    """Widening the token to the device API must not widen it to /admin.

    This is the boundary that matters, and it is the one the token was designed
    around: a leaked token is a leaked light switch, not a leaked home.

    Uses the routes as registered -- `/admin` is a 308 routing redirect to
    `/admin/`, and asserting on a 308 would be asserting that Flask normalises
    a trailing slash, which is not the property under test.
    """
    monkeypatch.setenv(command_token.ENV_TOKEN, "a-real-looking-token-value")
    headers = {"Authorization": "Bearer a-real-looking-token-value"}
    for path in ("/admin/", "/admin/users", "/admin/config", "/admin/api/memory"):
        res = anon.get(path, headers=headers)
        assert res.status_code in (302, 401, 403), (
            f"a command token reached {path} with {res.status_code}; the token "
            f"is not supposed to be able to see the admin surface"
        )


# --- the expensive endpoints ------------------------------------------------


@pytest.mark.parametrize("path", ["/api/stt", "/api/tts"])
def test_the_cpu_endpoints_are_not_free_for_the_internet(anon, path):
    """Both are POSTs, and a POST that answers 400 rather than 401 has already
    passed the gate and reached the handler. An unauthenticated /api/stt is a
    free transcription service on someone else's electricity -- and with a
    wildcard CORS origin, readable by the caller too."""
    res = anon.post(path, json={})
    assert res.status_code == 401, (
        f"POST {path} answered {res.status_code} anonymously; it reached the "
        f"handler instead of being refused"
    )


def test_health_stays_open_but_tells_nothing(store):
    """Deliberately ungated: the deploy gate and the proxy need it. Asserted so
    the decision is written down rather than accidental."""
    app = _app()
    client = app.test_client()
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.get_json()
    assert "status" in body
    # No address, no port, no secret, no device readings.
    flat = str(body)
    assert "0.0.0.0" not in flat and "127.0.0.1" not in flat
    assert command_token.ENV_TOKEN not in flat
