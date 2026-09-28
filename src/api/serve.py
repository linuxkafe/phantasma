"""Production WSGI serving for pHantasma.

Why not ``app.run``
-------------------
The service was served by werkzeug's development server. It works, but it has
no request timeouts, no connection limits, no keep-alive management, and Flask's
own documentation says it is not for production.

Why waitress and NOT gunicorn
-----------------------------
gunicorn was the obvious choice and it was tried first. It does not work here,
and the reason is architectural rather than incidental:

* gunicorn's arbiter **forks** workers, and its arbiter must run in the **main
  thread** -- it installs signal handlers, and ``signal.signal`` raises
  ``ValueError: signal only works in main thread of the main interpreter``
  anywhere else. Verified, not assumed.
* Forcing gunicorn into the main thread means **inverting the service
  lifecycle**: the arbiter would own the main process, the pipeline would have
  to be constructed inside the forked worker (a pipeline built in the arbiter
  would be a *copy* in the worker, so a command arriving at ``/comando`` would
  be enqueued into a copy and never reach the loop that owns the microphone),
  and the voice loop would move into a thread inside the worker.

That is a real refactor of the lifecycle the validated voice pipeline depends
on, to obtain timeouts and connection limits.

waitress is a production WSGI server that is pure Python and does not fork. It
runs in any thread, so:

* the pipeline stays in the main process, exactly where it is today, and
  ``app.pipeline is pipeline`` holds -- the ``/comando`` contract is untouched;
* the sound card keeps its single owner, so the exclusive-device problem does
  not arise and there is no ``--workers`` footgun;
* the lifecycle is unchanged, so the rollback is one environment variable.

The sound card remains a single-process resource either way. This module is
single-process by design: it is called from one thread, and nothing here forks.

Rolling back
------------
``PHANTASMA_SERVE_MODE=flask`` restores the previous behaviour exactly. The flag
exists so the rollback is an environment variable, not a redeploy.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger("phantasma.serve")

DEFAULT_THREADS = 8
DEFAULT_PORT = 5000
DEFAULT_HOST = "0.0.0.0"


def serve_mode() -> str:
    """``waitress`` (default) or ``flask`` (previous development server)."""
    return os.getenv("PHANTASMA_SERVE_MODE", "waitress").strip().lower()


def _host() -> str:
    return os.getenv("PHANTASMA_API_HOST", DEFAULT_HOST)


def _port() -> int:
    return int(os.getenv("PHANTASMA_API_PORT", str(DEFAULT_PORT)))


def serve_waitress(pipeline_factory, host: str | None = None, port: int | None = None):
    """Serve under waitress in a daemon thread. Returns the thread.

    The pipeline is constructed by ``pipeline_factory`` in THIS process, so the
    object the API holds is the same object the voice loop owns. That is the
    whole reason waitress was chosen; do not "upgrade" this to a forking
    server without reading the module docstring.
    """
    from waitress import serve as _waitress_serve

    from src.api.routes import create_app

    host = host or _host()
    port = port or _port()
    threads = int(os.getenv("PHANTASMA_API_THREADS", str(DEFAULT_THREADS)))
    connection_limit = int(os.getenv("PHANTASMA_API_CONNECTION_LIMIT", str(max(threads * 4, 100))))
    channel_timeout = int(os.getenv("PHANTASMA_API_CHANNEL_TIMEOUT", "120"))

    app = create_app(pipeline=pipeline_factory())
    thread = threading.Thread(
        target=_waitress_serve,
        kwargs={
            "app": app,
            "host": host,
            "port": port,
            "threads": threads,
            "connection_limit": connection_limit,
            "channel_timeout": channel_timeout,
            # No waitress access log: src/api/access_log.py already writes a
            # richer, queryable line. Two logs would double disk for the same
            # requests.
            "clear_untrusted_proxy_headers": True,
        },
        daemon=True,
        name="api-server",
    )
    thread.start()
    logger.info(
        "Serving via waitress on %s:%s (%s threads, connection_limit %s, channel_timeout %ss)",
        host,
        port,
        threads,
        connection_limit,
        channel_timeout,
    )
    return thread


def serve_flask(pipeline_factory, host: str | None = None, port: int | None = None):
    """The previous arrangement, kept for rollback. Returns the thread."""
    from src.api.routes import create_app

    host = host or _host()
    port = port or _port()
    app = create_app(pipeline=pipeline_factory())
    thread = threading.Thread(
        target=app.run,
        kwargs={
            "host": host,
            "port": port,
            "debug": False,
            "use_reloader": False,
            "threaded": True,
        },
        daemon=True,
        name="api-server",
    )
    thread.start()
    logger.warning(
        "Serving via the werkzeug development server on %s:%s. This is NOT a "
        "production server; set PHANTASMA_SERVE_MODE=waitress.",
        host,
        port,
    )
    return thread


def start_http_server(pipeline_factory):
    """Start the HTTP server according to PHANTASMA_SERVE_MODE.

    Non-fatal by contract: a failure here must not take the voice assistant
    down, so the exception is swallowed and reported, exactly as before.
    """
    mode = serve_mode()
    try:
        if mode == "flask":
            return serve_flask(pipeline_factory)
        return serve_waitress(pipeline_factory)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        logger.warning("REST API failed to start (non-fatal): %s", exc)
        return None
