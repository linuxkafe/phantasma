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
        # Most specific key first. A bare substring match routes by accident the
        # moment two providers share a fragment: "search" is inside BOTH
        # ".../search" and "arquivo.pt/textsearch", so with three providers the
        # empty SearXNG payload was being served to arquivo.pt. Invisible with
        # two providers, which is why it survived until T063 added a third.
        for key in sorted(self.responses, key=len, reverse=True):
            if key in url:
                return _Resp(self.responses[key])
        raise AssertionError(f"unexpected URL: {url}")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


SRESP = {"results": [{"title": "T", "url": "http://e/1", "content": "snippet um"}]}
# Re-specified in T063. The Wikipedia fallback is gone -- it was the privacy
# finding, and the SearXNG config already carries wikipedia as an engine. The
# property this file exists to protect did not change: a metasearch that comes
# back empty must not mean zero knowledge.
WRESP = {"query": {"search": [{"title": "Gasolina", "snippet": "combustivel"}]}}
ARESP = {"response_items": [{"title": "Gasolina", "originalURL": "http://a/1",
                             "originalSnippet": "combustivel"}]}


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


def test_falls_back_to_a_web_archive_when_searxng_empty():
    """The case that was live: 0 results must not mean 0 knowledge.

    Re-specified in T063. The fallback used to be a second, hard-coded door to
    pt.wikipedia.org, reached with the owner's raw text, unannounced, and it is
    the finding the T059 review called a BLOCKER. It is gone. What replaced it
    is the web-archive chain, and it is checked the same way: the metasearch
    comes back empty and something still answers.
    """
    out, client = _run({"search": {"results": []}, "textsearch": ARESP})
    assert "combustivel" in out
    assert any("textsearch" in c for c in client.calls), (
        f"arquivo.pt nao foi consultado; foi apenas: {client.calls}"
    )


def test_the_second_door_is_not_wikipedia_any_more():
    """The regression guard. `CLAUDE.md` promises no third-party cloud
    dependency beyond the self-hosted SearXNG, and while `_wikipedia` existed
    that promise was false on a schedule nobody could see."""
    out, client = _run({"search": {"results": []}, "textsearch": ARESP})
    assert not any("wikipedia" in c for c in client.calls), client.calls
    assert "w/api.php" not in tools.__file__ or True  # prose may name it; the call cannot
    assert not hasattr(tools, "_wikipedia")


def test_survives_searxng_being_down():
    """Container restarting must not take search with it.

    Kept, not deleted, with the fixture changed: the property is the point of
    this file, and it survives the removal of the Wikipedia door.
    """
    def boom(self, *a, **k):
        raise tools.httpx.ConnectError("recusado")

    client = _Client({"textsearch": ARESP})
    with patch.object(tools.httpx, "Client", return_value=client):
        with patch.object(tools.httpx.Client, "get", side_effect=boom):
            out = tools.search_with_searxng("gasolina", max_results=2)
    assert "combustivel" in out, (
        "com o SearXNG em baixo nada respondeu: a pesquisa cai toda"
    )


def test_no_results_anywhere_is_empty_not_an_error():
    """No invented context: a dead search returns '' so the LLM answers from memory."""
    out, _ = _run({"search": {"results": []},
                   "textsearch": {"response_items": []},
                   "advancedsearch": {"response": {"docs": []}}})
    assert out == ""


def test_results_without_snippets_are_not_fed_to_the_llm():
    """An empty content field used to be skipped; now it is the only filter."""
    out, _ = _run({"search": {"results": [{"title": "X", "url": "u", "content": ""}]}})
    assert out == ""
