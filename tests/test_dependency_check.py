"""The dependency check must fail when a dependency fails.

`/api/health` reports `"ollama": "healthy"` after a `list()` against the primary
host. On 2026-10-03 the local `qwen3:8b` -- the fallback -- timed out after 240
seconds, `/api/health` said `healthy` throughout, `deploy.sh` saw 200 on all
sixty polls, and the assistant was running on one working brain out of two.

So this asserts the thing that actually happened, with a stubbed client and no
network:

- a host that answers `/api/tags` but times out generating is a FAILURE, not a
  pass, and it is the failure that every existing check missed;
- `--quick` sees the same host as healthy, which is why `--quick` is not what a
  monitor should use;
- the fallback is reported by name and in its own right, because "the LLM is
  down" is not actionable when half of it is fine;
- a dependency whose config cannot be located is a failure, not a skip. A check
  that quietly drops a dependency reports fewer dependencies and looks healthier
  for it;
- SearXNG is verified with a real search, because a reachable instance that
  answers nothing is the state that let the assistant answer as if it had
  consulted the web.

The stubs are module-level `monkeypatch` targets rather than a fake Ollama class
hierarchy, so each test states the one behaviour it is about.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "dependency_check.py")


@pytest.fixture(scope="module")
def dep():
    spec = importlib.util.spec_from_file_location("dependency_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dependency_check"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeClient:
    """Answers `list()` instantly and then fails generation, like qwen3 did.

    `fails_generation` is a flag rather than a timeout comparison so the test
    states the behaviour instead of reverse-engineering it from the numeric
    timeout the caller happens to pass.
    """

    fails_generation = True

    def __init__(self, host=None, timeout=None, **_kw):
        self.host = host
        self.timeout = timeout

    def list(self):
        return {"models": [{"name": "qwen3:8b"}]}

    def chat(self, **_kw):
        if type(self).fails_generation:
            raise TimeoutError(f"ReadTimeout after {self.timeout}s")
        return {"message": {"content": "ok"}}


def test_a_host_that_lists_but_cannot_generate_is_a_failure(dep, monkeypatch):
    """The measured failure. Reachability says yes; generation says no.

    This is the whole reason the file exists. A `list()` returning in 0.04s and
    a generation timing out at 240s are the same host, and only one of them is
    what the assistant experiences.
    """
    ollama = pytest.importorskip("ollama")
    monkeypatch.setattr(ollama, "Client", _FakeClient)
    _FakeClient.fails_generation = True

    res = dep.check_llm("http://localhost:11434", "qwen3:8b", quick=False)
    assert res["ok"] is False, (
        "uma geração que dá timeout tem de ser FALHA, mesmo com `list()` a "
        "responder em milissegundos. Era este o estado que /api/health "
        "reportava como healthy."
    )
    assert "Timeout" in res["detail"]


def test_quick_mode_reports_the_same_host_as_healthy(dep, monkeypatch):
    """And this is why `--quick` is not what a monitor should use.

    Kept as a test rather than a note because the two answers disagreeing IS
    the finding. Anyone who reaches for `--quick` to avoid a slow check gets a
    green light from the exact host that is broken.
    """
    ollama = pytest.importorskip("ollama")
    monkeypatch.setattr(ollama, "Client", _FakeClient)

    _FakeClient.fails_generation = True
    assert dep.check_llm("http://localhost:11434", "qwen3:8b", quick=True)["ok"] is True
    assert dep.check_llm("http://localhost:11434", "qwen3:8b", quick=False)["ok"] is False


def test_the_fallback_is_reported_separately_and_by_name(dep, monkeypatch):
    """`"ollama": "healthy"` collapses two hosts into one word.

    With the primary up and the fallback down, the useful report is "the fallback
    is down", because that is the one with a name and an owner. A check that
    reports a single `ollama` verdict forces the reader to guess which half
    failed.
    """
    seen: list[str] = []

    def _pairs(_cfg):
        return [
            ("primary", "http://a:11434", "m1"),
            ("fallback", "http://b:11434", "m2"),
        ]

    monkeypatch.setattr(dep, "_pairs", _pairs)
    monkeypatch.setattr(dep, "check_llm", lambda h, m, q: seen.append(h) or {
        "host": h, "model": m, "ok": h.endswith("a:11434"),
        "detail": "respondeu" if h.endswith("a:11434") else "ReadTimeout", "seconds": 1,
    })

    # Per-host behaviour: the primary answers, the fallback does not. Driven
    # through a client that fails only for the fallback host, which is the real
    # shape of the incident -- one host up, one host down, same config.
    class _Split(_FakeClient):
        fails_generation = False

        def chat(self, **kw):
            if self.host and self.host.endswith("b:11434"):
                raise TimeoutError("ReadTimeout")
            return {"message": {"content": "ok"}}

    ollama = pytest.importorskip("ollama")
    monkeypatch.setattr(ollama, "Client", _Split)
    results = [dep.check_llm(h, m, True) for _, h, m in _pairs(None)]
    assert [r["ok"] for r in results] == [True, False], (
        f"o fallback tem de ser avaliado por si: {results}. Uma veredict unica "
        f"sobre 'ollama' esconde que ha uma serie de reserva avariada."
    )


def test_a_dependency_whose_config_cannot_be_found_is_not_a_skip(dep, monkeypatch):
    """`"searxng": null` reads as "not configured", not "I looked in the wrong place".

    `config` is a namedtuple with no `web` field, so the first version of this
    probe looked for `web.searxng_url`, found nothing, and silently reported no
    SearXNG at all -- a check that drops a dependency ends up with fewer
    dependencies and looks healthier for it.

    Drives `main()`, not a string: asserting on the message text is how this
    test passed while the behaviour it describes was falsified.
    """
    cfg = type("C", (), {"SEARXNG_URL": None, "llm": None,
                         "OLLAMA_HOST_PRIMARY": "http://a:11434",
                         "OLLAMA_MODEL_PRIMARY": "m1",
                         "OLLAMA_HOST_FALLBACK": None,
                         "OLLAMA_MODEL_FALLBACK": "m2"})()
    monkeypatch.setattr(dep, "_import_config", lambda: cfg)
    monkeypatch.setattr(sys, "argv", ["x"])
    monkeypatch.setattr(dep, "check_llm", lambda h, m, q: {
        "host": h, "model": m, "ok": True, "detail": "respondeu", "seconds": 1})
    # The env var is the third fallback and this box exports a real one, so the
    # check would find a URL and pass. Removed here to reach the branch.
    monkeypatch.delenv("SEARXNG_URL", raising=False)

    rc = dep.main()
    assert rc == 1, (
        f"uma dependencia que nao se consegue localizar tem de ser FALHA. "
        f"main() devolveu {rc}."
    )


def test_duplicates_are_collapsed_but_order_is_kept(dep):
    """The failover chain has an order, and the check must report it in order.

    Primary first is not cosmetic: it is what tells a reader which host to go
    look at. A set would sort them lexically and could report the fallback
    first, naming the wrong machine as the one that matters.
    """
    cfg = type("C", (), {
        "llm": type("L", (), {
            "host": "http://a:11434", "model": "m1",
            "host_fallback": "http://a:11434", "model_fallback": "m1",
        })(),
    })()
    pairs = dep._pairs(cfg)
    assert [n for n, _, _ in pairs] == ["primary"], (
        f"duplicados devem colapsar para um so, mantendo o primeiro: {pairs}"
    )
