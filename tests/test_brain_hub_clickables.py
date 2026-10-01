"""The two controls on /admin/brain that were visible and did nothing.

Both bugs were invisible to the test suite and visible to the owner, on
2026-10-01, as "the Sleep & Dream button never becomes available" and "the
Apply buttons don't work". Neither was a backend failure. Both were clicks
that never reached the handler, so nothing was logged and nothing errored:

* **Sleep & Dream was covered.** In fullscreen the informational sleep bar
  becomes `position: fixed; top: 12px; right: 12px; z-index: 40` -- a small
  card in the top-right corner, with its summary div hidden. `#brain-inspect`,
  the drawer that carries the actual "Dormir e Sonhar" button, sat at
  `z-index: 30`. Measured on prod at 1916x895: the bar occupied
  x=1771..1904, y=12..129; the button occupied x=1726..1903, y=110..144. The
  button's centre (1814, 127) was inside the bar, and
  `document.elementFromPoint` at that point returned `DIV#sonhar.sleep-bar`,
  not the button. A click there was delivered to the bar. The button reported
  itself visible and enabled the whole time. Playwright clicking the centre
  produced no dialog and no POST; clicking 30px to the left, clear of the bar,
  worked immediately. That asymmetry is the whole bug.

* **Apply threw before it could do anything.** The row renders its mode select
  as `data-ref-edge`/`data-ref-side`, but the handler read
  `row.querySelector('[data-ref-mode]')` -- an attribute that appears nowhere
  in the template. `mode` was therefore always null, and
  `mode.dataset.refEdge` threw `TypeError: Cannot read properties of null
  (reading 'dataset')` on every press. The status line stayed empty because
  the throw happened before any of the messages were written.

The existing test missed the second one because it asserted
`"data-ref-mode" in body or "data-ref=" in body`: the `or` was satisfied by
the unrelated `data-ref` on the target select, so the attribute under test was
never actually required.

So these tests click. A test that greps markup cannot tell whether a click
reaches its handler; only the browser can, and the failure modes here were
both spatial and typographical rather than server-side.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import admin as admin_mod  # noqa: E402

# The viewport the owner reported in, not a convenient one: at a narrower
# width the sleep bar's `@media (max-width: 640px)` branch makes it static and
# the collision disappears, which is exactly how this can pass in CI and fail
# on the owner's screen.
OWNER_VIEWPORT = {"width": 1916, "height": 895}


@pytest.fixture
def brain_db(tmp_path, monkeypatch):
    """One real node, and one edge whose target dangles.

    The dangling edge is the whole point: without it the resolver section
    renders its "no unresolved references" branch and there is no Apply button
    to click.
    """
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    db = tmp_path / "brain.db"
    con = sqlite3.connect(db)
    con.executescript(schema)
    con.execute(
        "INSERT INTO memories (id, timestamp, text) VALUES"
        " (1, '2026-01-01', 'uma memoria qualquer')"
    )
    con.execute(
        "INSERT INTO memory_graph"
        " (id, node_key, node_type, label, source, affinity, weight,"
        "  touch_count, created_at, updated_at)"
        " VALUES (1, 'node:alfa', 'node', 'alfa', 'memory', 3.0, 1.0, 1,"
        " '2026-01-01', '2026-01-01')"
    )
    con.execute(
        "INSERT INTO memory_graph"
        " (id, node_key, node_type, label, source, target, affinity, weight,"
        "  touch_count, created_at, updated_at)"
        " VALUES (2, 'edge:1->fantasma', 'edge', 'alva com fantasma',"
        " 'memory', 'fantasma ausente', 1.0, 1.0, 1,"
        " '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(admin_mod, "BRAIN_DB_PATH", Path(db))
    from src.brain import graph_edit

    graph_edit.ensure_schema(str(db))
    return db


@pytest.fixture
def brain_server(brain_db, monkeypatch):
    """A real HTTP server, because a click has to travel over a real origin.

    `page.set_content()` would leave the page without an origin, so the fetch in
    the resolve handler would fail on its way out and the test would prove
    nothing about the payload.
    """
    from werkzeug.serving import make_server

    from src.api.routes import create_app

    ident = {"role": "admin", "email": "owner@example.invalid"}
    monkeypatch.setattr(admin_mod, "_current_user_data", lambda: ident)
    monkeypatch.setattr(admin_mod, "_current_user", lambda: "owner@example.invalid")
    monkeypatch.setattr(admin_mod, "_bypass_or_none", lambda: ident)

    app = create_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def open_everything(brain_server):
    """A page on /admin/brain with "Tudo" open, as the owner described.

    Returns the Playwright page plus the list of POSTs it made. `dialog.accept`
    matters: `triggerSleep()` opens a `confirm()`, and Playwright dismisses
    dialogs by default, which makes a working button look broken.
    """
    from playwright.sync_api import sync_playwright

    posts: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport=OWNER_VIEWPORT)
        page.on("dialog", lambda d: d.accept())
        page.on(
            "request",
            lambda r: posts.append({"method": r.method, "url": r.url, "post": r.post_data})
            if r.method == "POST"
            else None,
        )
        page.goto(f"{brain_server}/admin/brain", wait_until="domcontentloaded")
        page.wait_for_selector("#brain-inspect-toggle")
        page.click("#brain-inspect-toggle")
        page.wait_for_selector("#brain-inspect.is-active [data-sleep]")
        try:
            yield page, posts
        finally:
            browser.close()


@pytest.fixture(scope="module")
def _chromium():
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            pw.chromium.launch(args=["--no-sandbox"]).close()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"chromium unavailable: {exc}")


def test_the_sleep_button_is_the_topmost_thing_where_it_is_drawn(open_everything):
    """A button can be visible, enabled and unclickable at the same time.

    This asserts stacking, not visibility: `is_visible` was true throughout
    the bug. The bar painted over the button because the drawer's z-index (30)
    was below the floating bar's (40), so `elementFromPoint` -- what the
    browser actually hit-tests on a click -- returned the bar.
    """
    page, _ = open_everything
    hit = page.evaluate(
        """() => {
            const b = document.querySelector('[data-sleep]').getBoundingClientRect();
            const el = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
            return {tag: el.tagName, id: el.id, cls: el.className};
        }"""
    )
    assert hit["id"] != "sonhar", (
        "the floating sleep bar is painted over the Sleep & Dream button: "
        f"elementFromPoint at the button's centre returned {hit}"
    )
    assert hit["tag"] in ("BUTTON", "SPAN"), (
        f"the button's centre is not the button: {hit}"
    )


def test_the_drawer_sits_above_the_floating_sleep_bar(open_everything):
    """The relation that broke, stated directly so a future edit to either
    z-index fails here rather than in the owner's browser."""
    page, _ = open_everything
    z = page.evaluate(
        """() => ({
            drawer: parseInt(getComputedStyle(document.getElementById('brain-inspect')).zIndex, 10),
            bar: parseInt(getComputedStyle(document.getElementById('sonhar')).zIndex, 10),
            tabs: parseInt(getComputedStyle(document.querySelector('.brain-tabs')).zIndex, 10),
        })"""
    )
    assert z["drawer"] > z["bar"], (
        f"the drawer must paint above the floating bar or it steals its clicks: {z}"
    )
    assert z["drawer"] < z["tabs"], (
        f"the drawer must stay below the subnav or the tabs stop taking clicks: {z}"
    )


def test_clicking_sleep_dream_centre_starts_the_cycle(open_everything):
    """The click has to reach `triggerSleep()` from the centre of the button.

    The POST is intercepted and stubbed: the real endpoint starts a cycle whose
    every step calls the LLM, which belongs in a test of the cycle, not of a
    button.
    """
    page, posts = open_everything
    page.route(
        "**/admin/brain/sleep",
        lambda route: route.fulfill(
            status=202, content_type="application/json", body='{"status":"started"}'
        ),
    )
    page.locator("[data-sleep]").click()
    page.wait_for_timeout(500)
    assert any("/admin/brain/sleep" in p["url"] for p in posts), (
        "a click in the centre of the Sleep & Dream button reached no handler: "
        f"POSTs seen: {posts}"
    )


def test_apply_reaches_the_resolve_endpoint(open_everything):
    """The bug was a null deref before the fetch, so nothing was ever sent."""
    page, posts = open_everything
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    seen: list[dict] = []

    def _capture(route):
        seen.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body="{}")

    page.route("**/api/graph/resolve", _capture)
    page.select_option("[data-ref]", index=1)
    page.select_option("[data-ref-edge]", "relink")
    page.click("[data-resolve-btn]")
    page.wait_for_timeout(500)

    assert not errors, f"the Apply handler threw instead of resolving: {errors}"
    assert seen, (
        "Apply sent nothing to /api/graph/resolve. The mode select is rendered "
        f"with data-ref-edge but the handler read another attribute, so it threw "
        f"on mode.dataset before the fetch. POSTs seen: {posts}"
    )
    body = seen[0]
    assert body["edge_key"] == "edge:1->fantasma", body
    assert body["side"] in ("source", "target"), body
    assert body["action"] == "relink", body
    assert body["target_label"], body


def test_apply_refuses_an_empty_target_without_pretending_to_work(open_everything):
    """The guard that runs before the payload: picking no node must say so.

    Kept because the guard was the only visible symptom of the old bug -- the
    empty-choice path returned cleanly while the chosen-node path threw, which
    is why the status line looked alive right up until the real press.
    """
    page, posts = open_everything
    page.route("**/api/graph/resolve", lambda r: r.fulfill(status=200, body="{}"))
    page.click("[data-resolve-btn]")
    page.wait_for_timeout(300)
    status = page.evaluate("() => document.getElementById('ref-status').textContent")
    assert status.strip(), "no feedback when no target node was chosen"
    assert not any("/api/graph/resolve" in p["url"] for p in posts), (
        "it posted a resolve request with no target chosen"
    )
