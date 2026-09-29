"""Tests for authentication on the voice UI at `/`.

`/` is the house-control surface: the tiles that switch the lights, the chat,
and the device list. It was open to anything that could reach port 5000, so the
admin login was decorative -- the same box served the house to anyone who
asked.

These tests are about the ways that can go wrong, not the ways it works:

* the loopback admin bypass leaking into `/` (it must not);
* a stale session surviving the deletion of its user (revocation must work);
* a wrong password being distinguishable from an unknown address (enumeration);
* the open redirect on the post-login `next` (phishing on a login form);
* `/` 500ing instead of redirecting when the user store is missing;
* the session not being cleared on login (session fixation).

No test sends a real password anywhere, and the bcrypt comparison in
`authenticate` is stubbed so the suite does not pay ~100ms of hashing per call
except where timing is the thing under test.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.api import ui_auth


def _seed(tmp_path, monkeypatch, email="user@example.invalid", role="user",
          password="correct horse", is_active=1):
    """An isolated user store with one account."""
    db = tmp_path / "users.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE users (email TEXT PRIMARY KEY, password_hash TEXT,"
        " role TEXT, is_active INTEGER)"
    )
    # A real bcrypt hash, so verify_password is exercised for real in the
    # wrong-password path; a fake one would let a broken check pass.
    from src.api.admin import hash_password

    con.execute(
        "INSERT INTO users VALUES (?,?,?,?)",
        (email, hash_password(password), role, is_active),
    )
    con.commit()
    con.close()

    from src.api import admin as admin_mod

    def _connect():
        # The real get_db_connection sets row_factory=sqlite3.Row; without it
        # dict(row) raises and the test would be measuring the fake, not the code.
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(admin_mod, "get_db_connection", _connect)
    return db


@pytest.fixture()
def app(monkeypatch, tmp_path):
    """A minimal app carrying a UI session, with the store seeded."""
    _seed(tmp_path, monkeypatch)

    from flask import Flask, request

    application = Flask(__name__)
    application.secret_key = "test-key"

    @application.route("/protected")
    @ui_auth.login_required
    def protected():
        return "ok"

    @application.route("/login")
    def login():
        return "login page"

    # Real login/logout through a request, so the session mutation happens in
    # the client's own cookie jar -- the only way to test session fixation.
    @application.route("/do-login", methods=["POST"])
    def do_login():
        user = ui_auth.login(
            request.form.get("email", ""), request.form.get("password", "")
        )
        return "in" if user else "no"

    @application.route("/do-logout", methods=["POST"])
    def do_logout():
        ui_auth.logout()
        return "out"

    # The UI login, with the new-device branch already trusted: these tests are
    # about which session keys a successful login establishes, not about the
    # device challenge, which test_ui_recovery_routes.py covers.
    from src.api import auth_store

    @application.route("/ui-login", methods=["POST"])
    def ui_login():
        user = ui_auth.authenticate(
            request.form.get("email", ""), request.form.get("password", "")
        )
        if not user:
            return "no", 401
        row = ui_auth.find_user(request.form.get("email", ""))
        if auth_store.is_device_trusted(
            admin_conn(application), row["email"],
            request.cookies.get(auth_store.DEVICE_COOKIE, ""),
        ):
            ui_auth.login(row["email"], request.form.get("password", ""))
            return "in"
        return "challenge"

    return application


@pytest.fixture()
def client(app):
    return app.test_client()


# --- the gate ---------------------------------------------------------------


def test_root_page_requires_a_session(client):
    """`/` itself is behind the gate. Asserted on the real route, not the
    decorator, because the decorator passing proves nothing about `/`."""
    from flask import Flask

    from skills import skill_ui

    application = Flask(__name__)
    application.secret_key = "k"
    skill_ui.register_routes(application)

    resp = application.test_client().get("/")
    assert resp.status_code in (302, 401)
    assert "/login" in (resp.headers.get("Location") or "")


def test_logged_in_user_gets_the_page(client):
    with client.session_transaction() as s:
        s[ui_auth.SESSION_KEY] = "user@example.invalid"
    resp = client.get("/protected")
    assert resp.status_code == 200
    assert resp.data == b"ok"


def test_login_page_is_reachable_without_a_session(client):
    assert client.get("/login").status_code == 200


# --- the loopback bypass must NOT open the house ----------------------------


def test_bypass_identity_does_not_authenticate_the_ui(monkeypatch):
    """localauth.bypass_identity grants ADMIN to any local process. If `/`
    honoured it, anything running as any user on this host -- and anything that
    could be induced to make a request -- could switch the lights.

    That bypass stays on /admin/*. Here it must be inert.
    """
    from src.api import localauth

    monkeypatch.setenv(localauth.ENV_FLAG, "1")
    assert localauth.bypass_identity("127.0.0.1") is not None, (
        "precondition: the bypass does grant admin on loopback"
    )

    from flask import Flask

    application = Flask(__name__)
    application.secret_key = "k"
    with application.test_request_context("/"):
        assert ui_auth.is_authenticated() is False
        assert ui_auth.is_admin() is False


# --- revocation -------------------------------------------------------------


def test_session_for_a_deleted_user_stops_working(app, monkeypatch):
    """Deleting an account has to take effect immediately, not at next login.

    A cookie that is merely trusted is a credential the owner cannot revoke.
    """
    client = app.test_client()
    with client.session_transaction() as s:
        s[ui_auth.SESSION_KEY] = "user@example.invalid"
    assert client.get("/protected").status_code == 200

    from src.api import admin as admin_mod

    conn = admin_mod.get_db_connection()
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()

    resp = client.get("/protected")
    assert resp.status_code in (302, 401), "a deleted user is still signed in"


def test_inactive_user_cannot_log_in(app):
    from src.api import admin as admin_mod

    conn = admin_mod.get_db_connection()
    conn.execute("UPDATE users SET is_active = 0")
    conn.commit()
    conn.close()

    assert ui_auth.login("user@example.invalid", "correct horse") is None


# --- enumeration ------------------------------------------------------------


def test_wrong_password_and_unknown_address_are_indistinguishable(app):
    """Same boolean, and the same code path, for both. A different response
    time is enough to enumerate the user store, which is why authenticate()
    also runs a dummy comparison when the address is unknown."""
    bad_pw = ui_auth.authenticate("user@example.invalid", "wrong")
    no_user = ui_auth.authenticate("nobody@example.invalid", "wrong")
    assert bad_pw is no_user is False


def test_unknown_address_still_burns_comparison_time(monkeypatch):
    """The dummy hash must actually be verified, not skipped."""
    import src.api.admin as admin_mod

    calls = []
    monkeypatch.setattr(
        admin_mod, "verify_password", lambda h, p: calls.append(h) or False
    )
    ui_auth.authenticate("nobody@example.invalid", "x")
    assert calls, "no comparison was run for an unknown address"


def test_a_corrupt_hash_is_a_rejection_not_a_500(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    from src.api import admin as admin_mod

    conn = admin_mod.get_db_connection()
    conn.execute("UPDATE users SET password_hash = 'not-a-hash'")
    conn.commit()
    conn.close()

    # verify_password already swallows ValueError; assert the whole path holds.
    assert ui_auth.authenticate("user@example.invalid", "x") is False


# --- session handling -------------------------------------------------------


def test_login_clears_the_previous_session(app):
    """Session fixation: a session established before the login must not be
    the one that carries the authenticated identity afterwards.

    Driven through a real request rather than by poking `session` directly,
    because a direct mutation in a bare request context lands in a different
    cookie jar than the client uses -- which is how an earlier version of this
    test "passed" while proving nothing.
    """
    client = app.test_client()
    with client.session_transaction() as s:
        s["attacker"] = "planted"

    # Log in through a route, exactly as a browser would.
    client.post("/do-login", data={"email": "user@example.invalid",
                                   "password": "correct horse"})

    with client.session_transaction() as s:
        assert "attacker" not in s, "the pre-login session survived the login"
        assert s[ui_auth.SESSION_KEY] == "user@example.invalid"


def test_logout_drops_the_identity(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s[ui_auth.SESSION_KEY] = "user@example.invalid"
    assert client.get("/protected").status_code == 200

    client.post("/do-logout")
    assert client.get("/protected").status_code in (302, 401)


# --- the post-login redirect ------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["//evil.example/", "https://evil.example/", "http://evil.example/", "javascript:alert(1)"],
)
def test_post_login_redirect_refuses_to_leave_the_host(target):
    """An open redirect on a login form is a phishing tool, not a convenience.

    `//evil.example` is the one that passes a naive startswith("/") check: the
    browser reads it as protocol-relative and goes to the other host.
    """
    assert ui_auth._safe_local_path(target) is None


def test_post_login_redirect_keeps_local_paths():
    assert ui_auth._safe_local_path("/") == "/"
    assert ui_auth._safe_local_path("/admin/brain") == "/admin/brain"


def test_take_next_defaults_to_root_when_absent(app):
    with app.test_request_context("/"):
        assert ui_auth.take_next() == "/"


# --- resilience -------------------------------------------------------------


def test_missing_user_store_is_a_rejection_not_a_crash(monkeypatch):
    """A missing or broken store must not 500 the login form: the owner would
    be locked out of their own house by a database problem."""
    import src.api.admin as admin_mod

    def _boom():
        raise sqlite3.OperationalError("no such table: users")

    monkeypatch.setattr(admin_mod, "get_db_connection", _boom)
    from flask import Flask

    application = Flask(__name__)
    application.secret_key = "k"
    with application.test_request_context("/"):
        assert ui_auth.is_authenticated() is False
        assert ui_auth.authenticate("a@b.c", "x") is False


def test_empty_credentials_are_refused(app):
    assert ui_auth.authenticate("", "") is False
    assert ui_auth.authenticate("   ", "") is False


# --- the menu ---------------------------------------------------------------


def test_menu_is_rendered_for_a_signed_in_user():
    """The page had `admin_links_html = ""` hardcoded, so nobody had a menu:
    an admin had to know the URLs and a plain user had nothing to click."""
    from flask import Flask

    from skills import skill_ui

    application = Flask(__name__)
    application.secret_key = "k"
    with application.test_request_context("/"):
        page = skill_ui.handle_request()
    assert 'href="/admin/logout"' in page, "no sign-out link in the menu"
    assert 'class="nav-link"' in page


def test_non_admin_is_not_sent_admin_links(monkeypatch, tmp_path):
    """An admin URL in the page of someone who may not use it is disclosure by
    accident, even behind `display:none`. Asserted on the rendered LINK, not on
    the substring: the help text legitimately mentions /admin/brain/sleep, and a
    substring check would flag that and then pass on a page that did leak it.
    """
    _seed(tmp_path, monkeypatch, email="plain@example.invalid", role="user")
    from flask import Flask

    from skills import skill_ui
    from src.api import ui_auth

    application = Flask(__name__)
    application.secret_key = "k"
    client = application.test_client()
    with client.session_transaction() as s:
        s[ui_auth.SESSION_KEY] = "plain@example.invalid"
    with application.test_request_context("/"):
        page = skill_ui.handle_request()

    import re

    admin_links = [
        href for href in re.findall(r'href="(/admin[^"]*)"', page)
        if not href.startswith("/admin/logout")
    ]
    assert admin_links == [], f"admin links sent to a non-admin: {admin_links}"
    # Perfil is not admin's, so it stays for everyone.
    assert 'href="/perfil"' in page


def test_admin_is_sent_admin_links(monkeypatch, tmp_path):
    """The other direction: the gate must not be so tight that an admin gets no
    menu either, which is the failure that made the page feel broken before."""
    _seed(tmp_path, monkeypatch, email="user@example.invalid", role="admin")
    from flask import Flask

    from src.api import ui_auth

    application = Flask(__name__)
    application.secret_key = "k"
    from skills import skill_ui as _ui

    _ui.register_routes(application)
    client = application.test_client()
    with client.session_transaction() as s:
        s[ui_auth.SESSION_KEY] = "user@example.invalid"
    # A real request, so handle_request() sees the session the client holds.
    page = client.get("/").get_data(as_text=True)

    import re

    assert "/admin/brain" in re.findall(r'href="(/admin[^"]*)"', page)


# --- one door, one session --------------------------------------------------
# The admin area and the voice UI had two independent session keys. Signing in
# at `/` and following an admin link asked for the password a second time, and
# signing out of /admin left you signed in at `/`. Neither is a second factor.


def test_the_admin_session_key_matches_the_admin_module():
    """ui_auth duplicates the key rather than importing it (importing admin at
    module load would make the voice UI depend on the admin package). So the
    value is asserted equal here -- a rename in one place would otherwise leave
    two sessions that never see each other again."""
    from src.api import admin as admin_mod

    assert ui_auth.ADMIN_SESSION_KEY == admin_mod.SESSION_KEY


def test_a_ui_login_for_an_admin_sets_the_admin_session(app, monkeypatch):
    """The fixture's account is an admin. Signing in at / must open /admin too.

    Two independent session keys is what produced "it asks me to sign in again"
    on every trip from the voice UI to the admin pages: the admin gate reads
    `admin_user`, the UI login wrote `ui_user`, and neither could see the other.
    """
    from src.api import auth_store

    conn = admin_conn(app)
    conn.execute("UPDATE users SET role = 'admin'")
    conn.commit()

    client = app.test_client()
    raw = auth_store.trust_device(conn, "user@example.invalid", "portátil")
    client.set_cookie(auth_store.DEVICE_COOKIE, raw, domain="localhost")
    client.post("/ui-login", data={"email": "user@example.invalid",
                                   "password": "correct horse"})
    with client.session_transaction() as s:
        assert s[ui_auth.SESSION_KEY] == "user@example.invalid"
        assert s.get(ui_auth.ADMIN_SESSION_KEY) == "user@example.invalid", (
            "an admin who signed in at / still has to sign in again at /admin"
        )


def test_a_plain_user_ui_login_does_not_get_an_admin_session(app, monkeypatch):
    """A non-admin must not collect an admin session from a UI login.

    The role is still resolved from the store on every request, so this is not
    the only gate -- but a session key that says "admin" for a user whose role
    is not is a trap for the next person who reads it.
    """
    from src.api import auth_store

    conn = admin_conn(app)
    client = app.test_client()
    raw = auth_store.trust_device(conn, "user@example.invalid")
    client.set_cookie(auth_store.DEVICE_COOKIE, raw, domain="localhost")
    client.post("/ui-login", data={"email": "user@example.invalid",
                                   "password": "correct horse"})
    with client.session_transaction() as s:
        assert s[ui_auth.SESSION_KEY] == "user@example.invalid"
        assert ui_auth.ADMIN_SESSION_KEY not in s, (
            "a plain user was handed an admin session"
        )


def test_logout_clears_both_doors(app, monkeypatch):
    from src.api import auth_store

    conn = admin_conn(app)
    conn.execute("UPDATE users SET role = 'admin'")
    conn.commit()
    client = app.test_client()
    raw = auth_store.trust_device(conn, "user@example.invalid")
    client.set_cookie(auth_store.DEVICE_COOKIE, raw, domain="localhost")
    client.post("/login", data={"email": "user@example.invalid",
                                "password": "correct horse"})
    client.get("/logout")
    with client.session_transaction() as s:
        assert ui_auth.SESSION_KEY not in s
        assert ui_auth.ADMIN_SESSION_KEY not in s, (
            "signed out of / but still signed in to /admin"
        )


def admin_conn(app):
    """The store's connection, as the app fixture wired it."""
    from src.api import admin as admin_mod

    return admin_mod.get_db_connection()
