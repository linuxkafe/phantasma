"""Build a UI app with a signed-in user of a given role.

Shared by the tests that need to render `/` as somebody specific: the role
decides which links the shared nav renders, and each test that rebuilt the
store to arrange that was one more copy of the same seed to keep in step.
"""

from __future__ import annotations

import re
import sqlite3

# HTML comments are stripped before any structural assertion. The `/` template
# documents its own history in comments, and one of those comments quotes
# `id="nav-menu"` -- so a test that counts that string without stripping comments
# counts its own documentation and "fails" against a correct page.
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def root_page_html(client) -> str:
    """The rendered `/` document, with HTML comments removed.

    Structural invariants about the nav ("the burger is not inside the panel",
    "there is exactly one nav-menu") are properties of the ASSEMBLED document,
    not of any one source file. The nav markup is built by admin._build_nav_menu
    and inserted into the voice UI's template, so a test that greps
    skills/skill_ui.py for `<nav id="nav-menu">` found nothing once the two were
    correctly de-duplicated -- the test broke, not the page.
    """
    response = client.get("/")
    assert response.status_code == 200, (
        f"expected the signed-in page, got {response.status_code}; if the "
        f"session did not take, the helper is what is broken, not the page"
    )
    return HTML_COMMENT.sub("", response.get_data(as_text=True))


def make_app_with_user(email: str, role: str, password: str = "correct horse",
                       monkeypatch=None):
    """A Flask app carrying the UI routes, with `email` signed in as `role`.

    Returns (app, client). The device is marked trusted so the login is not
    challenged for a second factor: these tests are about what a session
    establishes and what the page renders, not about the device challenge, which
    tests/test_ui_recovery_routes.py covers.

    `monkeypatch` is not optional in practice. The store is reached through
    `admin.get_db_connection`, so pointing it at a test database is a global
    change; assigning it directly leaks into every later test in the session and
    they fail with "no such table" for reasons that have nothing to do with them.
    Nine tests failed that way before this took a monkeypatch.
    """
    from flask import Flask

    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store, ui_auth

    app = Flask(__name__)
    app.secret_key = "k"
    app.config["TESTING"] = True
    skill_ui.register_routes(app)

    # A file-backed store: several modules each close the connection they are
    # handed, so an in-memory database shared by reference is a closed handle
    # by the second caller.
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
        "INSERT INTO users VALUES (?,?,?,1,'now')",
        (email, admin_mod.hash_password(password), role),
    )
    seed.commit()
    auth_store.ensure_schema(seed)
    seed.close()

    if monkeypatch is None:
        raise ValueError(
            "make_app_with_user needs a monkeypatch: it replaces "
            "admin.get_db_connection, which is global state"
        )
    # `_viewer_is_admin()` in skill_ui asks the store for the role, so this
    # patched connection is what it reads -- the role is never taken from the
    # session alone.
    monkeypatch.setattr(admin_mod, "get_db_connection", _fresh)

    client = app.test_client()
    device = auth_store.trust_device(_fresh(), email, "test")
    client.set_cookie(auth_store.DEVICE_COOKIE, device, domain="localhost")
    with client.session_transaction() as s:
        s[ui_auth.SESSION_KEY] = email
        if role == "admin":
            s[ui_auth.ADMIN_SESSION_KEY] = email

    return app, client
