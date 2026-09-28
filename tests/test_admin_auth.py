"""Auth-matrix and route-behaviour tests for the admin blueprint.

Why this suite exists
---------------------
``/admin/users`` was declared as::

    @admin_bp.route("/users")
    @admin_required
    @login_required

Decorators apply bottom-up, so ``admin_required`` ran **first**. For an
anonymous visitor ``_current_user_data()`` returns ``None``, and the guard
answered ``abort(403)`` -- so the nav link returned *Forbidden* instead of
redirecting to the login page. Every other admin route returned 302. The
inverted order was invisible to the syntax checker and to the route-existence
test, which only asserted that ``/users`` was registered.

The correct order puts the cheap authentication check first and the role check
second, which is also the only ordering that distinguishes the three cases
meaningfully:

    anonymous        -> 302 to login   (not 403)
    authenticated    -> 200
    non-admin role   -> 403            (still denied)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flask  # noqa: E402

from src.api import admin as admin_mod  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A real Flask app with the admin blueprint and a stubbed user store."""
    monkeypatch.setattr(admin_mod, "get_db_connection", lambda: _FakeConn())

    application = flask.Flask(__name__)
    application.secret_key = "test"
    application.config["TESTING"] = True
    application.register_blueprint(admin_mod.admin_bp)
    return application


class _FakeCursor:
    def __init__(self, row):
        self._row = row
        self._one = True

    def fetchone(self):
        if self._one:
            self._one = False
            return self._row
        return None

    def fetchall(self):
        return []

    def close(self):
        pass


class _FakeConn:
    """Stands in for the users table with a single, configurable account."""

    def __init__(self, email="phantasma@linuxkafe.com", role="admin"):
        self.email = email
        self.role = role

    def execute(self, sql, params=()):
        low = sql.lower()
        if "from users" in low and "where email" in low:
            return _FakeCursor({"id": 1, "email": self.email, "role": self.role, "is_active": 1})
        return _FakeCursor(None)

    def close(self):
        pass


def _login(client, email="phantasma@linuxkafe.com"):
    with client.session_transaction() as sess:
        sess["admin_user"] = email


# ----------------------------------------------------------------------
# The regression itself
# ----------------------------------------------------------------------


def test_anonymous_users_redirects_to_login_not_forbidden(app):
    """The bug: this returned 403."""
    with app.test_client() as client:
        resp = client.get("/admin/users")
    assert resp.status_code == 302, (
        f"anonymous visitor must be redirected to login, got {resp.status_code}"
    )
    assert "/admin/login" in resp.headers["Location"]


def test_admin_can_open_users(app):
    with app.test_client() as client:
        _login(client)
        resp = client.get("/admin/users")
    assert resp.status_code == 200


def test_non_admin_is_still_forbidden(app, monkeypatch):
    """Fixing the redirect must not accidentally grant access."""
    monkeypatch.setattr(admin_mod, "get_db_connection", lambda: _FakeConn(role="user"))
    with app.test_client() as client:
        _login(client)
        resp = client.get("/admin/users")
    assert resp.status_code == 403


def test_decorator_order_is_auth_then_role(app):
    """Pin the source order so it cannot silently invert again."""
    from src.api import admin

    source = Path(admin.__file__).read_text(encoding="utf-8")
    block = source[source.index('route("/users"') :]
    block = block[: block.index("def user_manager")]
    assert block.index("@login_required") < block.index("@admin_required"), (
        "login_required must be the outer decorator so anonymous users get 302"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/admin/dashboard",
        "/admin/memory",
        "/admin/rag",
        "/admin/flybrain",
        "/admin/users",
        "/admin/env",
        "/admin/config",
    ],
)
def test_every_admin_page_redirects_anonymous_users(app, path):
    """Uniform behaviour: the nav must never link to a 403."""
    with app.test_client() as client:
        resp = client.get(path)
    assert resp.status_code == 302, f"{path} returned {resp.status_code} anonymously"
    assert "/admin/login" in resp.headers["Location"]


# ----------------------------------------------------------------------
# Dashboard hygiene
# ----------------------------------------------------------------------


def test_dashboard_has_no_injected_foreign_content(app):
    """A hardcoded 'Capuchinho Verde / WooCommerce' block was sitting in the
    dashboard template, describing a WordPress theme this project is not."""
    with app.test_client() as client:
        _login(client)
        body = client.get("/admin/dashboard").get_data(as_text=True)
    for forbidden in (
        "Capuchinho",
        "WooCommerce",
        "WordPress",
        "autocompilado",
        "Bash autocompilado",
    ):
        assert forbidden not in body, f"dashboard still contains {forbidden!r}"


def test_dashboard_redirects_to_brain(app):
    """/admin/dashboard is now a 301 to /admin/brain.

    It used to be a separate page summarising counts that /admin/brain already
    renders from the same tables -- two pages, one source of truth, free to
    diverge. The dashboard was folded into the brain hub and kept reachable as a
    permanent redirect so bookmarks and the root device page do not 404.
    """
    with app.test_client() as client:
        _login(client)
        resp = client.get("/admin/dashboard")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/admin/brain")


def test_brain_renders(app):
    """The brain hub is now the landing page for that section."""
    with app.test_client() as client:
        _login(client)
        resp = client.get("/admin/brain")
    assert resp.status_code == 200
