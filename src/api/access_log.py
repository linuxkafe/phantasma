"""Structured request logging.

Why this exists
---------------
A previous route audit had to count lines of werkzeug output in the systemd
journal to answer "is anything actually calling this?". That is not an access
log: it has no stable fields, it mixes with application output, and the window
is bounded by journal retention. The audit's conclusion had to be hedged as
"no traffic observed in 30 days" rather than a fact.

This writes one line per request to a dedicated file, in a format designed to be
queried rather than read:

    ts=2026-09-27T15:21:38Z method=GET path=/api/memory status=200 bytes=1234
    dur_ms=12.4 ip=10.0.0.5 ua="curl/8.0" ref="-"

Deliberately NOT logged: request bodies, query values on auth endpoints, and
session data. The file records that a route was hit, never what was submitted.
Query strings are recorded for non-auth routes because they are needed to
distinguish, say, /device_status?nickname=A from ...?nickname=B -- but for
/admin/login and /admin/verify the query is dropped, since a login query string
is exactly the kind of thing that must not accumulate on disk.

Format is ``key=value`` with quoting, not JSON: it greps with awk, streams
without a parser, and does not require the fields to be added in a fixed order.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Iterable

# Endpoints whose query strings are never written. Login/verify carry the
# e-mail address and the OTP; those must not accumulate in a log file.
SENSITIVE_PATH_PREFIXES = (
    "/admin/login",
    "/admin/verify",
    "/verify",
)


def _quote(value: str) -> str:
    """Quote a value so a space or quote cannot break the line format."""
    if value == "":
        return '""'
    if any(ch in value for ch in ' "\t\n='):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _redact_query(path: str) -> str:
    """Drop the query string on auth endpoints; keep it elsewhere."""
    base = path.split("?", 1)[0]
    if "?" not in path:
        return path
    if base.startswith(SENSITIVE_PATH_PREFIXES):
        return base + "?<redacted>"
    return path


class AccessLogMiddleware:
    """WSGI middleware writing one structured line per request.

    Wraps ``app`` and delegates everything, so it cannot change routing,
    auth or response bodies. If logging fails the request still proceeds: an
    audit trail must never be able to take the service down.
    """

    def __init__(self, app: Callable, log_path: str | None = None) -> None:
        self.app = app
        configured = log_path or os.getenv("ACCESS_LOG_PATH", "")
        self.log_path = Path(configured) if configured else None
        self._disabled = self.log_path is None
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, environ, start_response):
        if self._disabled:
            return self.app(environ, start_response)

        started = time.monotonic()
        method = environ.get("REQUEST_METHOD", "?")
        raw_path = environ.get("PATH_INFO", "")
        query = environ.get("QUERY_STRING", "")
        path = _redact_query(raw_path + (("?" + query) if query else ""))
        peer = environ.get("REMOTE_ADDR", "-")
        agent = environ.get("HTTP_USER_AGENT", "-")
        referer = environ.get("HTTP_REFERER", "-")

        status_holder = {"status": 0, "bytes": 0}

        def counting_start_response(status, headers, exc_info=None):
            try:
                status_holder["status"] = int(str(status).split(" ", 1)[0])
            except (ValueError, IndexError):
                status_holder["status"] = 0
            return start_response(status, headers, exc_info)

        try:
            result = self.app(environ, counting_start_response)
        except Exception:
            self._write(
                method,
                path,
                500,
                0,
                (time.monotonic() - started) * 1000.0,
                peer,
                agent,
                referer,
                outcome="exception",
            )
            raise

        def counting_iter():
            for chunk in result:
                try:
                    status_holder["bytes"] += len(chunk)
                except TypeError:
                    pass
                yield chunk
            self._write(
                method,
                path,
                status_holder["status"],
                status_holder["bytes"],
                (time.monotonic() - started) * 1000.0,
                peer,
                agent,
                referer,
            )

        return counting_iter()

    def _write(
        self, method, path, status, size, dur_ms, peer, agent, referer, outcome="ok"
    ) -> None:
        """Append one line. Never raises."""
        if self.log_path is None:
            return
        try:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            line = (
                f"ts={ts} method={method} path={_quote(path)} status={status} "
                f"bytes={size} dur_ms={dur_ms:.1f} ip={_quote(peer)} "
                f"ua={_quote(agent[:120])} ref={_quote(referer[:120])} "
                f"outcome={outcome}\n"
            )
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            # An unwritable log must not break the request it is logging.
            self._disabled = True

    def read_lines(self) -> Iterable[str]:
        if self.log_path is None or not self.log_path.exists():
            return []
        return self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()


def install(app):
    """Wrap a Flask app's WSGI layer with access logging, if configured.

    Deliberately assigns to ``app.wsgi_app`` rather than returning a new
    object. Returning a middleware would replace the Flask instance with a
    plain WSGI callable, and then ``app.test_client()`` and ``app.config``
    stop existing -- which silently breaks every Flask-level test and any
    code that treats the app as a Flask app. Mutating ``wsgi_app`` keeps the
    Flask object intact and wraps only the layer below it.

    Returns the same app it was given, unconditionally, so callers can write
    ``app = install(app)`` or ignore the return value.
    """
    configured = os.getenv("ACCESS_LOG_PATH", "").strip()
    if not configured:
        return app
    app.wsgi_app = AccessLogMiddleware(app.wsgi_app, configured)
    return app
