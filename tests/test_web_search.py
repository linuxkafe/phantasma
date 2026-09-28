"""Web search must work even when SearXNG does not.

Measured 2026-09-28: the local SearXNG had 7 of its 8 engines disabled by
SearXNG's own default settings, so every query returned 0 results. The tests
below pin the two properties that actually matter to the owner: search
returns something usable, and it keeps returning it when the metasearch is
down. A fallback that is never exercised is not a fallback.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    """Minimal httpx.Client stand-in: routes by URL, no network."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(url)
        for key, payload in self.responses.items():
            if key in url:
                return _Resp(payload)
        raise AssertionError(f"unexpected URL: {url}")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


SRESP = {"results": [{"title": "T", "url": "http://e/1", "content": "snippet um"}]}
WRESP = {"query": {"search": [{"title": "Gasolina", "snippet": "combustivel"}]}}


def _run(responses):
    client = _Client(responses)
    with patch.object(tools.httpx, "Client", return_value=client):
        return tools.search_with_searxng("gasolina", max_results=2), client


def test_searxng_hit_carries_title_and_source():
    """A snippet with no title and no URL is unusable in an answer."""
    out, _ = _run({"search": SRESP})
    assert "snippet um" in out
    assert "T" in out, "titulo ausente"
    assert "http://e/1" in out, "fonte ausente"


def test_falls_back_to_wikipedia_when_searxng_empty():
    """The case that was live: 0 results must not mean 0 knowledge."""
    out, client = _run({"search": {"results": []}, "api.php": WRESP})
    assert "combustivel" in out
    assert any("api.php" in c for c in client.calls), "wikipedia nao foi consultada"


def test_survives_searxng_being_down():
    """Container restarting must not take search with it."""
    def boom(self, *a, **k):
        raise tools.httpx.ConnectError("recusado")

    client = _Client({"api.php": WRESP})
    with patch.object(tools.httpx, "Client", return_value=client):
        with patch.object(tools.httpx.Client, "get", side_effect=boom):
            out = tools.search_with_searxng("gasolina", max_results=2)
    assert "combustivel" in out


def test_no_results_anywhere_is_empty_not_an_error():
    """No invented context: a dead search returns '' so the LLM answers from memory."""
    out, _ = _run({"search": {"results": []}, "api.php": {"query": {"search": []}}})
    assert out == ""


def test_results_without_snippets_are_not_fed_to_the_llm():
    """An empty content field used to be skipped; now it is the only filter."""
    out, _ = _run({"search": {"results": [{"title": "X", "url": "u", "content": ""}]}})
    assert out == ""
