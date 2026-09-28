"""Tests for the structured access log.

The properties that matter for an audit trail:
1. It records WHICH route was hit, so "is this endpoint used?" stops being a
   question about werkzeug lines in the journal.
2. It NEVER records credentials -- the login query string and the OTP.
3. It cannot take the service down when the log is unwritable.
4. It is transparent: response body, status and headers pass through unchanged.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.access_log import (  # noqa: E402
    SENSITIVE_PATH_PREFIXES,
    AccessLogMiddleware,
    _quote,
    _redact_query,
    install,
)


def _env(method="GET", path="/api/memory", query="", agent="curl/8", ref="-"):
    return {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "REMOTE_ADDR": "10.0.0.5",
        "HTTP_USER_AGENT": agent,
        "HTTP_REFERER": ref,
    }


def _app(body=b"ok", status="200 OK"):
    def app(environ, start_response):
        start_response(status, [("Content-Type", "text/plain")])
        return [body]

    return app


class TestRedaction:
    @pytest.mark.parametrize("prefix", SENSITIVE_PATH_PREFIXES)
    def test_login_query_never_logged(self, prefix):
        out = _redact_query(f"{prefix}?email=admin@secret.com&otp=123456")
        assert "secret.com" not in out
        assert "123456" not in out
        assert "<redacted>" in out

    def test_normal_query_is_kept(self):
        """Needed to tell /device_status?nickname=A from ?nickname=B apart."""
        out = _redact_query("/device_status?nickname=Luz")
        assert out == "/device_status?nickname=Luz"

    def test_no_query_is_unchanged(self):
        assert _redact_query("/help") == "/help"

    def test_a_lookalike_path_is_not_redacted_by_accident(self):
        """Redaction is prefix-based and must not over-match."""
        assert _redact_query("/api/memory?key=x") == "/api/memory?key=x"


class TestQuoting:
    def test_plain_value_unquoted(self):
        assert _quote("/api/memory") == "/api/memory"

    def test_space_forces_quoting(self):
        assert _quote("Luz do Quarto") == '"Luz do Quarto"'

    def test_quote_is_escaped(self):
        assert _quote('a"b') == '"a\\"b"'

    def test_equals_forces_quoting(self):
        assert _quote("a=b") == '"a=b"'

    def test_empty_is_quoted(self):
        assert _quote("") == '""'


class TestLineFormat:
    def test_writes_one_line_with_the_required_fields(self, tmp_path):
        log = tmp_path / "access.log"
        mw = AccessLogMiddleware(_app(b"hello"), str(log))
        list(
            mw(
                _env(method="POST", path="/admin/config", query="a=1"),
                lambda s, h, e=None: None,
            )
        )
        line = log.read_text(encoding="utf-8").strip()
        for field in (
            "ts=",
            "method=POST",
            "path=",
            "status=200",
            "bytes=5",
            "dur_ms=",
            "ip=10.0.0.5",
            "ua=",
        ):
            assert field in line, f"missing {field} in: {line}"

    def test_records_the_path_so_usage_is_queryable(self, tmp_path):
        """This is the whole point: a future audit greps for path= here."""
        log = tmp_path / "access.log"
        mw = AccessLogMiddleware(_app(), str(log))
        for _ in range(3):
            list(mw(_env(path="/api/memory/graph"), lambda s, h, e=None: None))
        list(mw(_env(path="/other"), lambda s, h, e=None: None))
        lines = log.read_text(encoding="utf-8").splitlines()
        assert sum("path=/api/memory/graph" in line for line in lines) == 3

    def test_records_non_200_status(self, tmp_path):
        log = tmp_path / "access.log"
        mw = AccessLogMiddleware(_app(status="429 TOO MANY REQUESTS"), str(log))
        list(mw(_env(), lambda s, h, e=None: None))
        assert "status=429" in log.read_text(encoding="utf-8")

    def test_unparseable_status_does_not_crash(self, tmp_path):
        log = tmp_path / "access.log"
        mw = AccessLogMiddleware(_app(), str(log))
        list(mw(_env(), lambda s, h, e=None: ("weird status", [], None) and None))
        assert log.exists()


class TestNeverLeaksCredentials:
    def test_login_post_writes_no_email_or_otp(self, tmp_path):
        log = tmp_path / "access.log"
        mw = AccessLogMiddleware(_app(), str(log))
        list(
            mw(
                _env(
                    method="POST",
                    path="/admin/login",
                    query="email=phantasma@linuxkafe.com&password=hunter2",
                ),
                lambda s, h, e=None: None,
            )
        )
        text = log.read_text(encoding="utf-8")
        assert "hunter2" not in text
        assert "phantasma@linuxkafe.com" not in text
        assert "<redacted>" in text

    def test_verify_post_writes_no_otp(self, tmp_path):
        log = tmp_path / "access.log"
        mw = AccessLogMiddleware(_app(), str(log))
        list(
            mw(
                _env(method="POST", path="/admin/verify", query="otp=424242"),
                lambda s, h, e=None: None,
            )
        )
        assert "424242" not in log.read_text(encoding="utf-8")

    def test_user_agent_is_bounded(self, tmp_path):
        log = tmp_path / "access.log"
        mw = AccessLogMiddleware(_app(), str(log))
        list(mw(_env(agent="A" * 5000), lambda s, h, e=None: None))
        for line in log.read_text(encoding="utf-8").splitlines():
            assert len(line) < 1000, "a huge UA must not bloat the log line"


class TestTransparency:
    def test_body_and_status_pass_through(self, tmp_path):
        log = tmp_path / "access.log"
        seen = {}

        def start_response(status, headers, exc_info=None):
            seen["status"] = status

        mw = AccessLogMiddleware(_app(b"payload"), str(log))
        chunks = list(mw(_env(), start_response))
        assert chunks == [b"payload"]
        assert seen["status"] == "200 OK"

    def test_exception_is_logged_and_re_raised(self, tmp_path):
        log = tmp_path / "access.log"

        def boom(environ, start_response):
            raise RuntimeError("handler failed")

        mw = AccessLogMiddleware(boom, str(log))
        with pytest.raises(RuntimeError):
            list(mw(_env(), lambda s, h, e=None: None))
        text = log.read_text(encoding="utf-8")
        assert "outcome=exception" in text
        assert "status=500" in text


class TestResilience:
    def test_unwritable_log_does_not_break_the_request(self, tmp_path):
        """An audit trail must never be able to take the service down."""
        if os.geteuid() == 0:
            pytest.skip("root ignores file permissions")
        log = tmp_path / "ro" / "access.log"
        log.parent.mkdir()
        log.write_text("", encoding="utf-8")
        log.chmod(stat.S_IRUSR)  # read-only
        mw = AccessLogMiddleware(_app(b"still works"), str(log))
        assert list(mw(_env(), lambda s, h, e=None: None)) == [b"still works"]
        assert mw._disabled is True

    def test_disabled_when_env_is_unset(self, monkeypatch):
        """No log path anywhere -> pass-through, no file.

        ACCESS_LOG_PATH is set in production's .env, so the environment has to
        be cleared explicitly. A test that relies on the ambient environment
        passes in dev and fails in prod; this one behaves identically in both.
        """
        monkeypatch.delenv("ACCESS_LOG_PATH", raising=False)
        mw = AccessLogMiddleware(_app())
        assert mw._disabled is True
        assert list(mw(_env(), lambda s, h, e=None: None)) == [b"ok"]

    def test_explicit_path_overrides_env(self, monkeypatch, tmp_path):
        """An explicit argument wins over ACCESS_LOG_PATH."""
        monkeypatch.setenv("ACCESS_LOG_PATH", "/nonexistent-should-not-be-used.log")
        target = tmp_path / "explicit.log"
        mw = AccessLogMiddleware(_app(), str(target))
        assert mw._disabled is False
        list(mw(_env(), lambda s, h, e=None: None))
        assert target.exists()

    def test_is_disabled_when_env_var_absent(self, monkeypatch):
        monkeypatch.delenv("ACCESS_LOG_PATH", raising=False)
        mw = AccessLogMiddleware(_app())
        assert mw._disabled is True

    def test_install_is_a_noop_without_env(self, monkeypatch):
        monkeypatch.delenv("ACCESS_LOG_PATH", raising=False)
        sentinel = object()
        assert install(sentinel) is sentinel

    def test_install_mutates_wsgi_app_and_returns_the_same_flask_app(self, monkeypatch, tmp_path):
        """Regression: install() must NOT return a replacement object.

        Returning a middleware instead of the Flask app removes test_client()
        and .config, which breaks every Flask-level test -- and it did, once.
        """
        monkeypatch.setenv("ACCESS_LOG_PATH", str(tmp_path / "a.log"))
        from flask import Flask

        app = Flask(__name__)
        original = app.wsgi_app
        returned = install(app)
        assert returned is app, "install() must return the same Flask app"
        assert app.wsgi_app is not original
        assert isinstance(app.wsgi_app, AccessLogMiddleware)
        # the Flask surface is still there
        assert app.test_client() is not None
        assert app.config is not None
