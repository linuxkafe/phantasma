"""What the dream stores, and what the cycle does.

skill_dream asked the model for tags in its own prompt, then threw them away
and stored f"Noticia: {fact}". Retrieval matches with LIKE on keywords, so
those memories were ones the RAG could not find -- the same class of defect as
"bimby?" being carried into the LIKE pattern as a literal. These tests pin the
storage shape, because the shape is what retrieval depends on.

The second half covers brain_hub's cycle: which steps run, in what order, and
what happens when one of them fails. A step that aborts the cycle silently
looks exactly like a step that succeeded.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills import skill_dream  # noqa: E402
from src.api import admin as admin_mod  # noqa: E402

# --- _as_memory: the storage shape -----------------------------------------

def test_tags_and_facts_are_kept():
    """The whole point: keywords the RAG can match on must survive."""
    out = skill_dream._as_memory(
        "O gato chama-se Bimby", tags=["gato", "Bimby"])
    data = json.loads(out)
    assert data["tags"] == ["gato", "Bimby"]
    assert data["facts"] == ["O gato chama-se Bimby"]


def test_facts_fall_back_to_the_text():
    """Tags with no facts still has to store the fact, not an empty list."""
    data = json.loads(skill_dream._as_memory("Factual", tags=["x"]))
    assert data["facts"] == ["Factual"]


def test_plain_text_is_stored_unchanged():
    """No tags, no facts: this is what the store already holds and what
    retrieve_from_rag matches on. Inventing tags here would be a guess."""
    text = "O Capitalismo Tardio depende de Plataformas Digitais"
    assert skill_dream._as_memory(text) == text


def test_the_models_own_refusal_is_not_stored_as_knowledge():
    """The web dream asked for 'deep knowledge' and filed the model's
    'o texto fornecido nao contem informacoes' as a fact. It was retrieved as
    knowledge for the rest of the store's life."""
    assert skill_dream._as_memory(
        "Conhecimento Profundo: O texto fornecido não contém informações "
        "sobre um tema específico"
    ) is None


def test_empty_and_short_text_is_dropped():
    assert skill_dream._as_memory("") is None
    assert skill_dream._as_memory("   ") is None
    assert skill_dream._as_memory("ok") is None


def test_tag_and_fact_lists_are_bounded():
    """A model that returns 200 tags is a failure mode, not knowledge."""
    data = json.loads(skill_dream._as_memory(
        "Factual", tags=[f"t{i}" for i in range(50)],
        facts=[f"f{i}" for i in range(50)]))
    assert len(data["tags"]) == 8
    assert len(data["facts"]) == 8


# --- the news write path ---------------------------------------------------

def test_news_dream_stores_keywords_not_a_bare_sentence(monkeypatch):
    """The model returns tags; they must reach the store."""
    stored = []
    monkeypatch.setattr(skill_dream, "save_to_rag", stored.append)
    monkeypatch.setattr(
        skill_dream, "search_with_searxng", lambda q, max_results=3: ["resultado"])
    monkeypatch.setattr(skill_dream, "_safe_ollama_chat", lambda *a, **k: json.dumps({
        "noticias": ["A assemblia aprovou a lei X", "A cidade Y abriu uma escola"],
        "tags": ["politica", "educacao"],
    }))
    skill_dream._perform_news_dream()
    assert stored, "the news dream stored nothing"
    for entry in stored:
        data = json.loads(entry)
        assert "politica" in data["tags"], f"tags discarded: {entry[:60]}"


def test_news_dream_does_not_store_a_refusal(monkeypatch):
    stored = []
    monkeypatch.setattr(skill_dream, "save_to_rag", stored.append)
    monkeypatch.setattr(
        skill_dream, "search_with_searxng", lambda q, max_results=3: ["r"])
    monkeypatch.setattr(skill_dream, "_safe_ollama_chat", lambda *a, **k: json.dumps({
        "noticias": ["O texto fornecido não contém informações úteis"],
        "tags": [],
    }))
    skill_dream._perform_news_dream()
    assert stored == [], f"a refusal was stored as knowledge: {stored}"


# --- the sleep/dream cycle -------------------------------------------------

@pytest.fixture
def cycle(tmp_path, monkeypatch):
    """An isolated brain.db plus a recording of the cycle's steps."""
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    db = tmp_path / "brain.db"
    con = sqlite3.connect(db)
    con.executescript(schema)
    con.commit()
    con.close()
    monkeypatch.setattr(admin_mod, "BRAIN_DB_PATH", Path(db))
    from src.brain import graph_edit
    graph_edit.ensure_schema(str(db))
    return db


def _client(monkeypatch):
    """A test client that passes the admin gates without a real session."""
    from src.api.routes import create_app

    app = create_app()
    app.config["TESTING"] = True
    ident = {"role": "admin", "email": "owner@example.invalid"}
    monkeypatch.setattr(admin_mod, "_current_user_data", lambda: ident)
    monkeypatch.setattr(admin_mod, "_current_user", lambda: "owner@example.invalid")
    monkeypatch.setattr(admin_mod, "_bypass_or_none", lambda: ident)
    return app, ident


def test_sleep_endpoint_starts_a_cycle_that_runs_the_dream_step(cycle, monkeypatch):
    """Drive the real endpoint and wait for the thread it starts.

    The previous version of this test called a nested function that does not
    exist at module level, and then asserted `... or True` -- a test that
    cannot fail. The cycle body is a closure inside brain_sleep, so the only
    honest way to observe it is through the route and the status it writes.

    This used to assert that `perform_dreaming` itself was called by the cycle.
    It no longer is: the cycle runs each dream phase as its own step, and the
    "dream" step is the research half alone (`_research_half`). Calling
    `perform_dreaming` from the cycle would run consolidation and the graph
    dream a second time, on top of the steps above it. The regression guard for
    that is in tests/test_dream_graph.py
    (`test_the_sleep_cycle_does_not_consolidate_twice`); here we only assert the
    endpoint reaches a terminal state with a dream step that ran.
    """
    import time

    ran = []

    def fake_research_half(mode="auto"):
        ran.append(mode)
        return ""

    monkeypatch.setattr(skill_dream, "_research_half", fake_research_half)
    monkeypatch.setattr(skill_dream, "_reduce_to_concepts", lambda: None)
    monkeypatch.setattr(skill_dream, "_dedupe_memories", lambda: None)
    monkeypatch.setattr(skill_dream, "_consolidate_memories", lambda: None)
    monkeypatch.setattr(skill_dream, "_optimize_graph", lambda **_: None)
    monkeypatch.setattr(
        skill_dream, "perform_dreaming",
        lambda *a, **kw: pytest.fail(
            "the cycle must not call perform_dreaming: it re-runs the "
            "consolidation and graph-dream steps the cycle already did"
        ),
    )
    from src.pipeline import gmif_classifier
    monkeypatch.setattr(gmif_classifier, "classify_all_edges", lambda conn: None)
    monkeypatch.setattr(gmif_classifier, "classify_all_nodes", lambda conn: None)

    app, ident = _client(monkeypatch)
    resp = app.test_client().post("/admin/brain/sleep")
    assert resp.status_code in (202, 302), f"sleep returned {resp.status_code}"

    deadline = time.time() + 15
    status = {}
    while time.time() < deadline:
        status = app.test_client().get("/admin/brain/sleep/status").get_json()
        if status.get("status") in ("done", "failed"):
            break
        time.sleep(0.2)

    assert status.get("status") == "done", f"cycle ended as {status}"
    assert "dream" in (status.get("steps") or {}), (
        f"the dream step never ran; steps seen: {sorted(status.get('steps') or {})}"
    )
    assert ran, "the research half (_research_half) was never called"


def test_dream_step_is_registered_in_the_cycle(cycle):
    """Read the registered step names, because the cycle body runs in a
    background thread and a thread assertion would be a race."""
    src = Path(admin_mod.__file__).read_text(encoding="utf-8")
    body = src[src.index("def _run_sleep_cycle():"):]
    body = body[:body.index("\ndef ", 10)]
    assert '_step("dream"' in body, "perform_dreaming is not a cycle step"
    assert '_step("gmif_dream"' in body, "the GMIF dream was dropped"


def test_a_failing_dream_step_does_not_abort_the_cycle(cycle, monkeypatch):
    """Consolidation happens before the dreams. If a dream raised out of the
    cycle, the work above it would be lost and the status would say failed
    without saying why."""
    src = Path(admin_mod.__file__).read_text(encoding="utf-8")
    body = src[src.index("def _run_sleep_cycle():"):]
    body = body[:body.index("\ndef ", 10)]
    dream = body[body.index('_step("dream"'):]
    guarded = dream[:dream.index(")", dream.index("except"))]
    assert "except" in guarded, "the dream step is unguarded"


# --- the classifier and the cycle that drives it ---------------------------

def test_classifier_reads_rows_by_name_on_a_plain_connection(tmp_path, monkeypatch):
    """This raised "TypeError: tuple indices must be integers or slices, not
    str" on the first edge, so the whole sleep cycle failed in 0.0s and the
    owner saw "Ciclo terminou com estado: failed".

    The classifier indexed rows by column name while the cycle handed it a
    bare sqlite3.connect() with no row_factory. __main__ opens one with
    row_factory = sqlite3.Row, which is why it worked there and nowhere
    else. A function that reads by name must not depend on how its caller
    opened the database.
    """
    from pathlib import Path as _Path

    from src.pipeline import gmif_classifier as g

    schema = (_Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    db = tmp_path / "brain.db"
    con = sqlite3.connect(db)
    con.executescript(schema)
    con.execute(
        "INSERT INTO memory_graph (node_key, node_type, label, source, target,"
        " affinity, weight, touch_count, created_at, updated_at)"
        " VALUES ('edge:a', 'edge', 'A -> B', 'node:a', 'node:b', 0.2, 1.0, 1,"
        " '2026-01-01', '2026-01-01')"
    )
    con.execute(
        "INSERT INTO memory_graph (node_key, node_type, label, source,"
        " affinity, weight, touch_count, created_at, updated_at)"
        " VALUES ('node:a', 'node', 'Assunto A', 'memory', 1.0, 1.0, 1,"
        " '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()

    # A bare connection: exactly what the sleep cycle passes in.
    plain = sqlite3.connect(db)
    assert plain.row_factory is None, "the fixture stopped reproducing it"
    g.classify_all_edges(plain)
    g.classify_all_nodes(plain)
    plain.close()

    # The assertion is the absence of the TypeError, which is what the owner
    # saw. Whether a given row gets a level depends on the classifier's own
    # thresholds, which are not what regressed.
    out = sqlite3.connect(db)
    out.execute(
        "SELECT node_key, gmif_level, node_gmif_type FROM memory_graph"
        " WHERE node_key IN ('edge:a', 'node:a')")
    out.close()
