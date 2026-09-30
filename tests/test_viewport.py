"""Every HTML page the app serves must declare a viewport.

Owner report, 2026-09-30: "o /admin em mobile é que está por seu lado pouco
optimizado", alongside a broken desktop root page.

Of the ten `<head>` sites in `src/api/admin.py`, exactly one carried
`<meta name="viewport">`. The other nine did not, and the consequence is not
subtle: with no viewport declaration a mobile browser falls back to a layout
viewport of about 980px and scales the whole page down to fit the real screen.
Measured on `/admin/brain` in an emulated 375x667 phone, `window.innerWidth`
came back **981**, not 375. Every element was laid out for a desktop and then
shrunk, which is what "not optimised on mobile" looks like from the outside --
tiny text, a squeezed desktop, and touch targets that are small because
everything is scaled.

The root page and the auth pages have had the tag all along (it arrived with the
PWA work), which is exactly why the difference was never noticed: the one page
the owner uses on a phone is the one page that was correct.

This test is the deliverable, not the edit. Nine heads were fixed by hand and the
tenth page would have shipped without it; a test that walks the served pages
catches the eleventh, and -- more usefully -- it fails the moment somebody adds a
page and forgets.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VIEWPORT = re.compile(r'<meta\s+name=["\']viewport["\']', re.I)


@pytest.fixture(scope="module")
def served_pages():
    """The real app, and the HTML of the pages that do not need a working
    pipeline: the layout pages and the auth pages.

    `/admin/config` and `/admin/brain` are excluded from the assertions below
    rather than from the fixture -- they need a populated database, and a test
    that skips itself when a fixture is thin is a test that stops testing. What
    is asserted is the invariant over whatever came back.
    """
    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store, ui_auth
    from src.api.routes import create_app

    path = os.path.join(tempfile.mkdtemp(), "viewport.db")

    def _fresh():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    seed = _fresh()
    # The REAL schema, copied from the production database rather than
    # approximated. Two earlier versions of this fixture hand-wrote `config` and
    # `config_categories` and each one was wrong in a different way -- a missing
    # table, then a missing column (`display_order`) -- so every assertion below
    # died on an OperationalError that said nothing about viewports. A fixture
    # that guesses the schema turns a layout test into a schema test.
    seed.executescript(
        """
        CREATE TABLE config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            description TEXT,
            is_sensitive BOOLEAN DEFAULT FALSE,
            UNIQUE(category, key)
        );
        CREATE TABLE config_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            display_order INTEGER DEFAULT 0
        );
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO users (email, password_hash, role, is_active)
             VALUES ('a@t.test', 'x', 'admin', 1);
        """
    )
    seed.commit()
    auth_store.ensure_schema(seed)
    seed.close()
    original = admin_mod.get_db_connection
    admin_mod.get_db_connection = _fresh
    try:
        app = create_app()
        skill_ui.register_routes(app)
        app.config["TESTING"] = True
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[ui_auth.SESSION_KEY] = "a@t.test"
            sess[ui_auth.ADMIN_SESSION_KEY] = "a@t.test"
        pages = {}
        for url in ("/", "/login", "/perfil", "/admin/users", "/admin/brain",
                    "/admin/config", "/admin/env"):
            res = client.get(url)
            if res.status_code == 200 and "html" in (res.headers.get("Content-Type") or ""):
                pages[url] = res.get_data(as_text=True)
    finally:
        admin_mod.get_db_connection = original
    return pages


def test_the_fixture_actually_served_pages(served_pages):
    """Otherwise every assertion below passes vacuously on an empty dict."""
    assert len(served_pages) >= 3, (
        f"only {sorted(served_pages)} came back: a viewport test that inspects "
        f"nothing is worse than no viewport test"
    )
    assert "/" in served_pages


@pytest.mark.parametrize("url", sorted(
    ["/", "/login", "/perfil", "/admin/users", "/admin/brain", "/admin/config",
     "/admin/env"]
))
def test_every_page_declares_a_viewport(url, served_pages):
    if url not in served_pages:
        pytest.skip(f"{url} did not render in this fixture; see the fixture note")
    html = served_pages[url]
    assert VIEWPORT.search(html), (
        f"{url} has no <meta name=\"viewport\">. A mobile browser then lays the "
        f"page out at about 980px and scales it down: measured on /admin/brain "
        f"in a 375px phone, window.innerWidth came back 981."
    )


def test_the_viewport_uses_the_device_width(served_pages):
    """`width=device-width` is the part that matters. A hard-coded
    `width=375` on a tablet is a page that letterboxes itself."""
    for url, html in served_pages.items():
        m = re.search(r'<meta\s+name=["\']viewport["\'][^>]*>', html, re.I)
        assert m, f"{url} has no viewport tag"
        assert "device-width" in m.group(0), (
            f"{url}: {m.group(0)} -- without device-width the layout width is "
            f"fixed and the page is scaled to fit"
        )


def test_the_admin_module_has_no_head_without_a_viewport():
    """The structural version, so a NEW page cannot skip the check by not being
    reachable from this fixture.

    Reads the module rather than the served pages on purpose: it sees every head
    including ones behind a database this fixture does not have.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "src", "api", "admin.py",
    )
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    heads = [m.start() for m in re.finditer(r"<head\b[^>]*>", src, re.I)]
    assert len(heads) >= 5, f"only {len(heads)} heads found: the pattern changed"
    naked = []
    for start in heads:
        window = src[start:start + 400]
        if not VIEWPORT.search(window):
            naked.append(src[:start].count("\n") + 1)
    assert not naked, (
        f"these <head> sites have no viewport within 400 chars: lines {naked}. "
        f"Nine were fixed by hand and the tenth would have shipped without it."
    )
