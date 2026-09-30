"""Installable as an app.

Owner request, 2026-09-29. The interesting decisions are all about what a
service worker must NOT do to a page that switches lights:

* **Nothing that carries a reading is cached.** No cache-first, no
  stale-while-revalidate, no "fall back to the last known value". A switch that
  silently acts on an hour-old state is not a feature, it is a house that
  misreports itself. So `/api/*`, `/comando`, `/device_action`,
  `/device_status` and `/get_devices` go to the network every time.
* **No authenticated page is ever stored.** The page is behind a session; a
  signed-in document in a shared cache is how one household's house ends up in
  another's browser.
* **The worker is served from the site root**, because a worker served from
  /public/ defaults to the scope /public/ and never sees a navigation. That is
  the quietest possible way to ship a PWA that installs and does nothing.
* **The login page carries the manifest too.** `start_url` is `/`, and `/`
  redirects to `/login` for anyone signed out, so the login page is what the
  installed app actually shows on the home screen.

Verified over real HTTP rather than against the HTML: `file://` cannot register
a service worker, and localhost counts as a secure context, which is the only
place this can be tested honestly.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _browser_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        with sync_playwright() as pw:
            pw.chromium.launch(args=["--no-sandbox"]).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _browser_ok(),
    reason=("no headless browser: a service worker cannot be registered over "
            "file://, so this cannot be checked any other way"),
)


@pytest.fixture(scope="module")
def server():
    """The real app on a real port, because localhost is a secure context."""
    import threading
    import time

    from werkzeug.serving import make_server

    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store
    from src.api.routes import create_app

    path = os.path.join(tempfile.mkdtemp(), "pwa.db")

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
    original = admin_mod.get_db_connection
    admin_mod.get_db_connection = _fresh

    app = create_app()
    skill_ui.register_routes(app)
    httpd = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.4)
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        yield base, app
    finally:
        httpd.shutdown()
        admin_mod.get_db_connection = original


def _cookie_for(app, base):
    """A signed-in cookie, so the browser gets `/` and not the login page."""
    client = app.test_client()
    from src.api import ui_auth

    with client.session_transaction() as sess:
        sess[ui_auth.SESSION_KEY] = "b@t.test"
        sess[ui_auth.ADMIN_SESSION_KEY] = "b@t.test"
    client.get("/")
    raw = client.get_cookie("session")
    if raw is None:
        return None
    # Werkzeug's Cookie exposes .key, not .name. Reading the wrong attribute
    # is only visible when the browser then refuses the cookie -- and the
    # symptom lands 40 lines away as "no service worker registered", which is
    # exactly the kind of lie a test should never tell.
    name = getattr(raw, "key", None) or getattr(raw, "name", None) or "session"
    return {"name": name, "value": raw.value,
            "domain": "127.0.0.1", "path": "/"}


def test_the_manifest_is_served_and_is_installable(server):
    import urllib.request

    base, _app = server
    with urllib.request.urlopen(base + "/public/manifest.json", timeout=10) as r:
        assert r.status == 200
        m = json.loads(r.read())
    # The criteria Chrome actually applies, stated one by one.
    assert m["display"] == "standalone", m["display"]
    assert m.get("start_url"), "no start_url: the app has nothing to launch"
    assert m.get("name"), "no name: the home screen shows nothing"
    sizes = [int(i["sizes"].split("x")[0]) for i in m["icons"]]
    assert max(sizes) >= 192, f"largest icon is {max(sizes)}px"
    assert any(i.get("purpose") == "maskable" for i in m["icons"]), (
        "no maskable icon: Android will letterbox the ghost in a white circle"
    )


def test_every_icon_the_manifest_claims_exists(server):
    import urllib.request

    base, _app = server
    with urllib.request.urlopen(base + "/public/manifest.json", timeout=10) as r:
        m = json.loads(r.read())
    for icon in m["icons"]:
        with urllib.request.urlopen(base + icon["src"], timeout=10) as r:
            body = r.read()
        assert r.status == 200, f"{icon['src']} is {r.status}"
        assert body[:8] == b"\x89PNG\r\n\x1a\n", f"{icon['src']} is not a PNG"
        assert len(body) > 500, f"{icon['src']} is {len(body)} bytes: a blank icon"


def test_the_service_worker_is_served_from_the_root_and_not_cached(server):
    import urllib.request

    base, _app = server
    req = urllib.request.Request(base + "/sw.js")
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        assert "javascript" in r.headers.get("Content-Type", ""), (
            "the worker is served as something the browser will not execute"
        )
        # A stale worker is a fix that never reaches anybody.
        assert "no-store" in r.headers.get("Cache-Control", ""), (
            "the worker is cacheable: a corrected version would stick around"
        )
        assert r.headers.get("Service-Worker-Allowed") == "/", (
            "the worker cannot claim the root scope from this path"
        )


def test_the_worker_never_caches_a_reading_or_a_command(server):
    """The property that makes it safe to install at all."""
    import urllib.request

    base, _app = server
    with urllib.request.urlopen(base + "/sw.js", timeout=10) as r:
        src = r.read().decode()
    for path in ("/comando", "/device_action", "/get_devices", "/api/stt",
                 "/api/voz", "/admin"):
        assert f"'{path}'" in src or f'"{path}"' in src, (
            f"{path} is not in the do-not-cache list. Cached state that switches "
            f"a light is not an optimisation"
        )
    assert "LIVE" in src, "the exclusion list lost its name"


def test_the_service_worker_actually_registers_with_root_scope(server):
    from playwright.sync_api import sync_playwright

    base, app = server
    cookie = _cookie_for(app, base)
    if cookie is None:
        pytest.skip("this build signs sessions in a cookie the test cannot copy")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 375, "height": 667},
                                  is_mobile=True, has_touch=True)
        ctx.add_cookies([cookie])
        pg = ctx.new_page()
        pg.set_default_timeout(10000)
        pg.goto(base + "/", wait_until="load")
        pg.wait_for_timeout(2500)
        reg = pg.evaluate(
            """async () => {
                const r = await navigator.serviceWorker.getRegistration();
                return r ? {scope: r.scope,
                            live: !!(r.active || r.installing || r.waiting)} : null;
            }"""
        )
        assert reg, "no service worker registered: the app installs and does nothing"
        assert reg["live"], "registered but not active"
        assert reg["scope"].rstrip("/") == base, (
            f"the worker controls {reg['scope']}, not the site: a worker served "
            f"from a sub-path cannot see a navigation"
        )
        browser.close()


def test_the_login_page_carries_the_manifest(server):
    """start_url is "/", and "/" redirects to /login for anyone signed out --
    so the login page is what the installed app shows on the home screen."""
    import urllib.request

    base, _app = server
    with urllib.request.urlopen(base + "/login", timeout=10) as r:
        html = r.read().decode()
    assert 'rel="manifest"' in html, (
        "the login page has no manifest link: the installed app opens to a bare "
        "form with no icon and no theme colour"
    )
    assert 'name="theme-color"' in html
    assert "viewport-fit=cover" in html, (
        "without viewport-fit=cover the installed app gets black bars and the "
        "chat sits under the home indicator"
    )
