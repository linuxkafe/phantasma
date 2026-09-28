"""Tests for the loopback authentication bypass.

The bypass is an authentication exception, so its tests are primarily about
what it REFUSES. An exception is judged by the boundary it draws, not by the
access it grants.

The three properties that matter:
1. Off unless explicitly enabled.
2. Loopback ONLY. Not RFC1918 -- this service has been scanned from 10.x.
3. Every use is logged. An invisible exception is a compromise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import localauth  # noqa: E402

# The synthetic admin the shared conftest fixture creates.
TEST_ADMIN_EMAIL = "test-admin@example.invalid"


class TestDisabledByDefault:
    def test_off_when_flag_absent(self, monkeypatch):
        monkeypatch.delenv(localauth.ENV_FLAG, raising=False)
        assert localauth.enabled() is False

    @pytest.mark.parametrize("value", ["0", "true", "yes", "TRUE", "", "on", " "])
    def test_only_exact_one_enables(self, monkeypatch, value):
        """Anything but the exact string "1" leaves it off.

        Loose truthiness here would be a vulnerability: "0" and "false" are
        exactly what someone writes to turn a flag OFF.
        """
        monkeypatch.setenv(localauth.ENV_FLAG, value)
        assert localauth.enabled() is False

    def test_enabled_by_exactly_one(self, monkeypatch):
        monkeypatch.setenv(localauth.ENV_FLAG, "1")
        assert localauth.enabled() is True

    def test_surrounding_whitespace_tolerated(self, monkeypatch):
        monkeypatch.setenv(localauth.ENV_FLAG, " 1 ")
        assert localauth.enabled() is True

    def test_bypass_refused_when_disabled(self, monkeypatch):
        monkeypatch.delenv(localauth.ENV_FLAG, raising=False)
        assert localauth.bypass_identity("127.0.0.1") is None


class TestLoopbackOnly:
    """The boundary. A bypass that accepts a LAN address is not a bypass,
    it is a hole -- and this service is scanned from 10.x."""

    @pytest.mark.parametrize(
        "addr", ["127.0.0.1", "127.0.0.53", "127.1.2.3", "::1", "[::1]", "::1%eth0"]
    )
    def test_loopback_accepted(self, addr):
        assert localauth.is_loopback(addr) is True

    @pytest.mark.parametrize(
        "addr",
        [
            "10.0.0.111",  # this host's own LAN address
            "10.0.0.114",  # the observed browser client
            "192.168.1.1",
            "172.17.0.2",  # a docker bridge peer
            "172.18.0.1",
            "8.8.8.8",
            "0.0.0.0",
            "169.254.1.1",  # link-local
            "100.64.0.1",  # CGNAT
        ],
    )
    def test_non_loopback_refused(self, addr):
        assert localauth.is_loopback(addr) is False

    @pytest.mark.parametrize("addr", [None, "", "   ", "not-an-ip", "127.0.0.999", "999"])
    def test_garbage_refused(self, addr):
        assert localauth.is_loopback(addr) is False

    def test_identity_refused_for_lan_even_when_enabled(self, monkeypatch):
        monkeypatch.setenv(localauth.ENV_FLAG, "1")
        assert localauth.bypass_identity("10.0.0.114") is None
        assert localauth.bypass_identity("127.0.0.1") is not None

    def test_source_does_not_ask_for_private(self):
        """Pins the decision against a future "helpful" widening to is_private."""
        src = Path(localauth.__file__).read_text(encoding="utf-8")
        assert "is_private" not in src
        assert "is_loopback" in src


class TestIdentityShape:
    def test_identity_is_admin(self, monkeypatch):
        monkeypatch.setenv(localauth.ENV_FLAG, "1")
        ident = localauth.bypass_identity("127.0.0.1")
        assert ident["role"] == "admin"
        assert ident["is_active"] == 1
        assert ident["via_bypass"] is True

    def test_identity_does_not_name_a_real_account(self, monkeypatch):
        """It must not collide with a real user in the store."""
        monkeypatch.setenv(localauth.ENV_FLAG, "1")
        ident = localauth.bypass_identity("127.0.0.1")
        assert "@" in ident["email"]
        assert "localhost" in ident["email"]

    def test_describe_reports_the_caveat(self, monkeypatch):
        monkeypatch.setenv(localauth.ENV_FLAG, "1")
        d = localauth.describe()
        assert d["enabled"] is True
        assert "loopback only" in d["scope"]
        assert "RFC1918" in d["scope"]
        assert "log every bypass" in d["warning"]


class TestBypassIsAuditable:
    def test_every_use_is_logged(self, monkeypatch, caplog):
        """An authentication exception with no trace is indistinguishable
        from a compromise. The log is the control."""
        import logging

        from src.api import admin as A

        monkeypatch.setenv(localauth.ENV_FLAG, "1")
        # Exercise the real path through a request context.
        import flask

        app = flask.Flask(__name__)
        with app.test_request_context("/admin/users", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            with caplog.at_level(logging.WARNING, logger="phantasma.api"):
                ident = A._bypass_or_none()
        assert ident is not None
        warnings = [r for r in caplog.records if "BYPASS" in r.getMessage()]
        assert warnings, "the bypass was used without logging anything"
        assert "127.0.0.1" in warnings[0].getMessage()
        assert "/admin/users" in warnings[0].getMessage()

    def test_no_log_when_bypass_refused(self, monkeypatch, caplog):
        import logging

        from src.api import admin as A

        monkeypatch.setenv(localauth.ENV_FLAG, "1")
        import flask

        app = flask.Flask(__name__)
        with app.test_request_context("/admin/users", environ_base={"REMOTE_ADDR": "10.0.0.114"}):
            with caplog.at_level(logging.WARNING, logger="phantasma.api"):
                assert A._bypass_or_none() is None
        assert not [r for r in caplog.records if "BYPASS" in r.getMessage()]


class TestDecoratorsHonourIt:
    def _client(self, addr, flag="1"):
        import flask

        from src.api import admin as A

        app = flask.Flask(__name__)
        app.register_blueprint(A.admin_bp)
        app.secret_key = "test"
        return app

    def test_login_required_redirects_when_disabled(self, monkeypatch):
        import flask

        from src.api import admin as A

        monkeypatch.delenv(localauth.ENV_FLAG, raising=False)
        app = flask.Flask(__name__)
        app.register_blueprint(A.admin_bp)
        app.secret_key = "t"

        @app.route("/probe")
        @A.login_required
        def probe():
            return "ok"

        with app.test_client() as c:
            r = c.get("/probe", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        assert r.status_code == 302, "loopback must NOT bypass while the flag is off"

    def test_login_required_passes_when_enabled(self, monkeypatch):
        import flask

        from src.api import admin as A

        monkeypatch.setenv(localauth.ENV_FLAG, "1")
        app = flask.Flask(__name__)
        app.secret_key = "t"

        @app.route("/probe")
        @A.login_required
        def probe():
            return "ok"

        with app.test_client() as c:
            r = c.get("/probe", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        assert r.status_code == 200


class TestStaleSessionCannotSeeAnything:
    """A session naming an address that is not in the store grants nothing.

    Reported by the owner: /admin/users returned 403 while /admin/brain rendered
    normally in the same session. Both were wrong, in opposite directions.

    The cause was that the decorators validated the COOKIE rather than the
    resolved IDENTITY. _current_user() returns whatever string the session
    holds, so a stale cookie for a deleted address passed login_required and
    rendered the admin area, then failed admin_required with a bare 403.

    The invariant: an identity that cannot be resolved is not an identity, and
    every protected route must agree about that.
    """

    def _app(self, users):
        import flask

        from src.api import admin as A

        app = flask.Flask(__name__)
        app.register_blueprint(A.admin_bp)
        app.secret_key = "t"
        return app, A

    def test_stale_session_is_redirected_on_every_protected_route(self, monkeypatch):
        """Not 403, not 200. Redirected to login, consistently."""
        import flask

        from src.api import admin as A

        monkeypatch.setenv(localauth.ENV_FLAG, "0")  # bypass explicitly off
        # The blueprint must be registered: the redirect calls
        # url_for("admin.login"), which has no endpoint on a bare app. Found by
        # the test itself -- a 500 where a 302 was expected.
        app = flask.Flask(__name__)
        app.register_blueprint(A.admin_bp)
        app.secret_key = "t"
        seen = {}

        @app.route("/probe")
        @A.login_required
        def probe():
            seen["ok"] = True
            return "rendered"

        with app.test_client() as c:
            with c.session_transaction() as s:
                s[A.SESSION_KEY] = "deleted-address@example.com"
            r = c.get("/probe", environ_overrides={"REMOTE_ADDR": "10.0.0.114"})
        assert seen.get("ok") is None, "a stale identity reached the view"
        assert r.status_code == 302
        assert "/admin/login" in r.headers["Location"]

    def test_authenticated_admin_still_passes(self, monkeypatch, admin_store):
        """The gate must not have been "fixed" by closing it."""
        import flask

        from src.api import admin as A

        monkeypatch.setenv(localauth.ENV_FLAG, "0")
        app = flask.Flask(__name__)
        app.secret_key = "t"

        @app.route("/probe")
        @A.login_required
        def probe():
            return "rendered"

        with app.test_client() as c:
            with c.session_transaction() as s:
                s[A.SESSION_KEY] = TEST_ADMIN_EMAIL
            r = c.get("/probe", environ_overrides={"REMOTE_ADDR": "10.0.0.114"})
        assert r.status_code == 200
        assert r.get_data(as_text=True) == "rendered"
