"""A dead LLM host must fail fast, not block the assistant.

Found on 2026-09-29: OLLAMA_TIMEOUT=600 was set in production .env and read by
config, but never passed to ollama.Client. The primary host (10.0.0.128:11434)
stopped answering, and every call blocked with no bound -- which is how a
voice assistant that should have said "I cannot reach the model" instead went
silent, and how the test suite came to hang rather than fail.
"""

import httpx
import pytest

import assistant


class TestTimeout:
    def test_read_budget_is_kept_generous(self, monkeypatch):
        import config

        monkeypatch.setattr(config.config.llm, "timeout", 600, raising=False)
        monkeypatch.setattr(config.config.llm, "connect_timeout", 10, raising=False)
        timeout = assistant._llm_timeout()
        assert timeout.read == 600.0, "a long generation must not be cut short"

    def test_connect_budget_is_short(self, monkeypatch):
        import config

        monkeypatch.setattr(config.config.llm, "timeout", 600, raising=False)
        monkeypatch.setattr(config.config.llm, "connect_timeout", 10, raising=False)
        assert assistant._llm_timeout().connect == 10.0

    def test_connect_never_exceeds_read(self, monkeypatch):
        """A read budget smaller than connect would be a lie about the bound."""
        import config

        monkeypatch.setattr(config.config.llm, "timeout", 5, raising=False)
        monkeypatch.setattr(config.config.llm, "connect_timeout", 30, raising=False)
        assert assistant._llm_timeout().connect == 5.0

    def test_zero_values_do_not_produce_an_infinite_timeout(self, monkeypatch):
        import config

        monkeypatch.setattr(config.config.llm, "timeout", 0, raising=False)
        monkeypatch.setattr(config.config.llm, "connect_timeout", 0, raising=False)
        timeout = assistant._llm_timeout()
        assert timeout.read > 0 and timeout.connect > 0

    def test_missing_config_falls_back_to_sane_values(self, monkeypatch):
        import config

        monkeypatch.delattr(config.config.llm, "connect_timeout", raising=False)
        timeout = assistant._llm_timeout()
        assert 0 < timeout.connect <= 10.0

    def test_the_timeout_is_an_httpx_timeout(self):
        """ollama.Client passes this straight to httpx."""

        assert isinstance(assistant._llm_timeout(), httpx.Timeout)


class TestUnreachableHost:
    """The behaviour, against a port nothing is listening on."""

    def test_a_dead_host_raises_instead_of_hanging(self):
        import socket
        import time

        # Reserve then release a port so it is almost certainly closed.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        import ollama

        client = ollama.Client(
            host=f"http://127.0.0.1:{port}",
            timeout=httpx.Timeout(5.0, connect=2.0),
        )
        started = time.monotonic()
        with pytest.raises(Exception):
            client.chat(model="llama3.1:8b", messages=[{"role": "user", "content": "oi"}])
        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"took {elapsed:.1f}s to give up on a dead host"
