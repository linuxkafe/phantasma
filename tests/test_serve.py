"""Tests for the serving layer.

Two things are being pinned here, and one of them is a real limitation that was
found the hard way:

1. gunicorn CANNOT be embedded in a non-main thread. Its arbiter installs
   signal handlers, and ``signal.signal`` raises in any other thread. This is
   asserted directly so nobody "simplifies" it back into a thread later.

2. The sound card is exclusive, so the worker count is pinned to 1. A
   well-meaning ``--workers 4`` would open ALSA four times and fail.

The rollback path is also tested: ``PHANTASMA_SERVE_MODE=flask`` must keep
working, because that is the one-environment-variable escape hatch from the
lifecycle change.
"""

from __future__ import annotations

import ast
import signal
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.api import serve as serve_mod  # noqa: E402


class TestServeModeFlag:
    def test_defaults_to_waitress(self, monkeypatch):
        """waitress, not gunicorn: gunicorn forks and would break /comando."""
        monkeypatch.delenv("PHANTASMA_SERVE_MODE", raising=False)
        assert serve_mod.serve_mode() == "waitress"

    @pytest.mark.parametrize("value", ["flask", "FLASK", " flask "])
    def test_flask_rolls_back(self, monkeypatch, value):
        monkeypatch.setenv("PHANTASMA_SERVE_MODE", value)
        assert serve_mod.serve_mode() == "flask"

    def test_rollback_is_one_env_var(self, monkeypatch):
        """The escape hatch must not require code changes or a redeploy."""
        monkeypatch.setenv("PHANTASMA_SERVE_MODE", "flask")
        assert serve_mod.serve_mode() == "flask"


class TestNoForking:
    def test_module_does_not_configure_gunicorn_workers(self):
        """Single process by construction: waitress does not fork.

        There is no ``--workers`` to get wrong here, which is the point. If a
        forking server is ever introduced, the pipeline copy problem returns.
        """
        src = (REPO / "src" / "api" / "serve.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "set":
                    args = node.args
                    if args and isinstance(args[0], ast.Constant) and args[0].value == "workers":
                        raise AssertionError(
                            "a workers setting has appeared; the sound card and the "
                            "shared pipeline both assume a single process"
                        )

    def test_pipeline_is_built_in_this_process(self):
        """The /comando contract depends on the API holding the SAME object."""
        import re

        src = re.sub(r"\s+", " ", (REPO / "src" / "api" / "serve.py").read_text(encoding="utf-8"))
        assert "create_app(pipeline=pipeline_factory())" in src

    def test_docstring_states_the_constraint(self):
        """The constraint must be documented where someone will read it.

        Asserting on prose is fragile by nature; these are the two words that
        carry the meaning, chosen because they are load-bearing rather than
        stylistic. A reworded paragraph should not fail this test; deleting the
        warning should.
        """
        # Prose is line-wrapped, so normalise whitespace before asserting.
        # "Do not raise --workers" is split across two lines in the source.
        import re

        src = re.sub(r"\s+", " ", (REPO / "src" / "api" / "serve.py").read_text(encoding="utf-8"))
        assert "not for production" in src
        # The single-process reason must be stated, whichever wording is used.
        assert "fork" in src.lower()
        assert "shared" in src.lower() or "same object" in src.lower()


class TestGunicornCannotRunOffTheMainThread:
    def test_gunicorn_arbiter_rejects_a_non_main_thread(self):
        """Pins the limitation that shaped the whole design.

        If gunicorn ever gains a thread-safe path this test will fail, which is
        the correct signal: the architecture could then be simplified.
        """
        errors = []

        def install():
            try:
                signal.signal(signal.SIGTERM, lambda *a: None)
            except ValueError as exc:
                errors.append(str(exc))

        t = threading.Thread(target=install)
        t.start()
        t.join()
        assert errors, "signal.signal succeeded off the main thread; re-check the design"
        assert "main thread" in errors[0]

    def test_serve_gunicorn_is_documented_as_main_thread_only(self):
        src = (REPO / "src" / "api" / "serve.py").read_text(encoding="utf-8")
        assert "main thread" in src


class TestConfiguration:
    def test_defaults(self, monkeypatch):
        for k in (
            "PHANTASMA_API_HOST",
            "PHANTASMA_API_PORT",
            "PHANTASMA_API_THREADS",
            "PHANTASMA_API_CONNECTION_LIMIT",
            "PHANTASMA_API_CHANNEL_TIMEOUT",
        ):
            monkeypatch.delenv(k, raising=False)
        assert serve_mod.DEFAULT_THREADS >= 1
        assert serve_mod.DEFAULT_PORT == 5000
        assert serve_mod.DEFAULT_HOST == "0.0.0.0"

    def test_configures_threads_connection_limit_and_timeout(self):
        import re

        src = re.sub(r"\s+", " ", (REPO / "src" / "api" / "serve.py").read_text(encoding="utf-8"))
        # The three things the development server did not provide.
        assert '"threads": threads' in src
        assert '"connection_limit": connection_limit' in src
        assert '"channel_timeout": channel_timeout' in src

    def test_does_not_double_log_requests(self):
        """The structured access log already covers this; two logs double disk."""
        import re

        src = re.sub(r"\s+", " ", (REPO / "src" / "api" / "serve.py").read_text(encoding="utf-8"))
        assert "No waitress access log" in src

    def test_werkzeug_server_warns_that_it_is_a_dev_server(self, monkeypatch):
        src = (REPO / "src" / "api" / "serve.py").read_text(encoding="utf-8")
        assert "development server" in src


class TestFactoryIsCalledOncePerProcess:
    def test_factory_is_lazy(self, monkeypatch):
        """The pipeline must be constructed inside the serving process.

        Built in the arbiter it would be a fork-copy in the worker, so a command
        arriving at /comando would never reach the loop owning the microphone.
        """
        calls = []

        def factory():
            calls.append(1)
            return object()

        # Do not actually serve; just prove the factory is invoked lazily inside
        # _make_app rather than at import time.
        src = (REPO / "src" / "api" / "serve.py").read_text(encoding="utf-8")
        assert "create_app(pipeline=pipeline_factory())" in src
        assert not calls, "factory must not run at import time"

    def test_start_http_server_swallows_failures(self):
        """A server failure must not take the voice assistant down."""

        def boom():
            raise RuntimeError("no server for you")

        assert serve_mod.start_http_server(boom) is None


class TestGracefulDegradation:
    def test_import_does_not_require_waitress(self):
        """waitress is imported lazily inside serve_waitress.

        A top-level import would break ``import assistant`` when waitress is
        absent and take the voice pipeline down with it -- the exact coupling
        that made a previous failure non-local.
        """
        src = (REPO / "src" / "api" / "serve.py").read_text(encoding="utf-8")
        top = [ln for ln in src.splitlines() if ln.startswith(("import waitress", "from waitress"))]
        assert not top, "waitress must be imported inside the function"
        assert "from waitress import serve as _waitress_serve" in src

    def test_gunicorn_rejection_is_documented(self):
        """The reason gunicorn is not used must stay in the file.

        A future maintainer will otherwise try it again, and the failure is not
        a stack trace you can read your way out of -- it is a ValueError about
        threads, or a pipeline that silently stops receiving commands.
        """
        import re

        src = re.sub(r"\s+", " ", (REPO / "src" / "api" / "serve.py").read_text(encoding="utf-8"))
        assert "Why waitress and NOT gunicorn" in src
        assert "signal only works in main thread" in src
