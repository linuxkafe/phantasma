"""The dream cycle must reduce memory to concepts, keep the links, and be safe.

Run: pytest tests/test_dream_graph.py -q

Everything here came out of one investigation into why the graph never grew:
the schema was owned by nobody, the GMIF skill was never loaded by the loader,
the write path was commented out, and the consolidation deleted rows with no
record of what it deleted.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from skills import skill_dream as dream  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "brain.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    from src.brain import memory_graph as mg

    mg.init_db()
    return path


def _insert(path, key, ntype, label, source=None, target=None, **gmif):
    con = sqlite3.connect(path)
    cols = ["node_key", "node_type", "label", "source", "target", "created_at", "updated_at"]
    vals = [key, ntype, label, source, target, "2026-01-01", "2026-01-01"]
    for k, v in gmif.items():
        cols.append(k)
        vals.append(v)
    marks = ",".join(["?"] * len(cols))
    con.execute(f"INSERT INTO memory_graph ({','.join(cols)}) VALUES ({marks})", vals)
    con.commit()
    con.close()


def test_the_gmif_cycle_runs_inside_the_loaded_skill():
    """The GMIF work must live where the loader already reaches.

    It used to sit in skills/skill_gmif_dream.py, which declared handle() but
    no TRIGGERS, so SkillLoader never loaded it and init_skill_daemon was never
    called. That is the whole reason the cycle had never run.
    """
    assert hasattr(dream, "_optimize_graph")
    assert hasattr(dream, "_analyze_graph_gaps")
    assert "gmif" in " ".join(dream.TRIGGERS).lower()


def test_perform_dreaming_extracts_concepts_before_the_purge(monkeypatch):
    """Concepts must be written before consolidation deletes their source rows.

    Consolidation is a real DELETE of the 20 most recent memories replaced by a
    tagless summary. Running the graph step afterwards would destroy every
    concept those rows held, once per night.
    """
    called = []
    monkeypatch.setattr(dream, "_reduce_to_concepts", lambda: called.append("materialize"))
    monkeypatch.setattr(dream, "_consolidate_memories", lambda: called.append("consolidate"))
    monkeypatch.setattr(dream, "_optimize_graph", lambda **_: called.append("graph"))
    monkeypatch.setattr(dream, "_perform_news_dream", lambda: called.append("news"))
    monkeypatch.setattr(dream, "_perform_web_dream", lambda: called.append("web"))

    dream.perform_dreaming(mode="news")

    assert called.index("materialize") < called.index("consolidate"), (
        "concepts were extracted after their source rows were already deleted"
    )


def test_weak_and_unevidenced_edges_are_reported_as_gaps(db):
    _insert(db, "n:a", "node", "Capitalismo Tardio")
    _insert(db, "n:b", "node", "Capitalismo Cansado")
    _insert(db, "edge:a", "edge", "A -> B", "Capitalismo Tardio", "Capitalismo Cansado",
            gmif_level="M1", gmif_extraction_confidence=0.9)

    gaps = dream._analyze_graph_gaps()

    assert gaps["node_count"] == 2
    assert any(g["label"] == "A -> B" for g in gaps["weak_edges"])


def test_an_edge_claiming_consequence_without_confidence_is_a_causal_gap(db):
    """An M3 with no validation confidence reads as knowledge and is not."""
    _insert(db, "n:a", "node", "X")
    _insert(db, "n:b", "node", "Y")
    _insert(db, "edge:x", "edge", "X -> Y", "X", "Y",
            gmif_level="M3", gmif_validation_confidence=0.0)

    gaps = dream._analyze_graph_gaps()

    assert any(g["target"] == "Y" for g in gaps["causal_gaps"])


def test_related_nodes_with_shared_words_become_a_disconnected_pair(db):
    # Two shared words, not one: a single common word is how most pairs of
    # sentences relate and would turn the graph into noise.
    _insert(db, "n:a", "node", "Capitalismo Tardio Digital")
    _insert(db, "n:b", "node", "Capitalismo Digital Cansado")

    gaps = dream._analyze_graph_gaps()

    pair = {(p["source"], p["target"]) for p in gaps["disconnected_pairs"]}
    assert ("Capitalismo Tardio Digital", "Capitalismo Digital Cansado") in pair


def test_one_shared_word_is_not_a_missing_link(db):
    _insert(db, "n:a", "node", "Capitalismo Tardio")
    _insert(db, "n:b", "node", "Capitalismo Cansado")

    gaps = dream._analyze_graph_gaps()

    assert gaps["disconnected_pairs"] == []


def test_an_edge_between_those_nodes_is_not_a_disconnected_pair(db):
    _insert(db, "n:a", "node", "Capitalismo Tardio Digital")
    _insert(db, "n:b", "node", "Capitalismo Digital Cansado")
    _insert(db, "edge:ab", "edge", "A -> B",
            "Capitalismo Tardio Digital", "Capitalismo Digital Cansado")

    gaps = dream._analyze_graph_gaps()

    assert gaps["disconnected_pairs"] == []


def test_research_without_evidence_is_refused(db):
    """The gate that matters: no evidence, no write.

    An unevidenced promotion is the false certainty this cycle exists to remove,
    so a synthesis with an empty evidence list must be refused.
    """
    _insert(db, "edge:a", "edge", "A -> B", "A", "B", gmif_level="M1")

    wrote = dream._apply_research_to_graph(
        {"conhecimento": "alguma coisa", "confianca": 0.9, "evidencia": []},
        "weak_edge",
        {"node_key": "edge:a", "source": "A", "target": "B"},
    )

    assert wrote is False
    con = sqlite3.connect(db)
    level = con.execute("SELECT gmif_level FROM memory_graph").fetchone()[0]
    con.close()
    assert level == "M1", "the edge was promoted without evidence"


def test_a_researched_edge_is_promoted_as_external_not_logical(db):
    """External, because the supporting text is a web page, not a derivation."""
    _insert(db, "edge:a", "edge", "A -> B", "A", "B", gmif_level="M1")

    wrote = dream._apply_research_to_graph(
        {
            "conhecimento": "A suporta B segundo a fonte",
            "confianca": 0.7,
            "evidencia": ["trecho"],
            "desired_level": "M3",
        },
        "weak_edge",
        {"node_key": "edge:a", "source": "A", "target": "B"},
    )

    assert wrote is True
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT gmif_level, gmif_validation_type, gmif_source_chunks FROM memory_graph"
    ).fetchone()
    con.close()
    assert row[0] == "M3"
    assert row[1] == "external"
    assert "trecho" in row[2]


def test_every_gap_type_maps_to_a_level(db):
    """The old code computed desired_level in one function and read it in
    another, where it did not exist. The mapping is the single source now."""
    assert set(dream.GMIF_DESIRED_LEVEL) == {
        "weak_edge", "disconnected_pair", "missing_requirement", "causal_gap",
    }


def test_consolidation_archives_what_it_deletes(db):
    """B was chosen for real deletion, so the deleted rows must survive."""
    from src.brain import memory_graph as mg  # noqa: F401

    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, timestamp TEXT, text TEXT)")
    for i in range(6):
        con.execute("INSERT INTO memories VALUES (?,?,?)", (i, "2026-01-01", f"memoria {i}"))
    con.commit()
    con.close()

    rows = [(i, "2026-01-01", f"memoria {i}") for i in range(6)]
    con = sqlite3.connect(db)
    archived = dream._archive_purged_memories(con, rows)
    con.commit()
    con.close()

    assert archived == 6
    con = sqlite3.connect(db)
    kept = con.execute("SELECT COUNT(*) FROM memories_purged").fetchone()[0]
    text = con.execute("SELECT text FROM memories_purged ORDER BY id LIMIT 1").fetchone()[0]
    con.close()
    assert kept == 6, "the archive is what makes consolidation recoverable"
    assert text == "memoria 0", "the archive must hold the raw text, not a summary"


def test_the_archive_is_idempotent(db):
    """Reruns must not pile up duplicates of the same row."""
    rows = [(1, "2026-01-01", "x")]
    con = sqlite3.connect(db)
    dream._archive_purged_memories(con, rows)
    dream._archive_purged_memories(con, rows)
    con.commit()
    con.close()
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM memories_purged").fetchone()[0]
    con.close()
    assert n == 1


def test_the_graph_step_respects_its_own_switch(db, monkeypatch, capsys):
    monkeypatch.setattr(dream, "GMIF_DREAM_ENABLED", False)
    called = []
    monkeypatch.setattr(dream, "_reduce_to_concepts", lambda: called.append("reduce"))
    dream._optimize_graph()
    assert called == [], "the graph step ran while switched off"


@pytest.mark.parametrize(
    "gap_type,gap",
    [
        ("disconnected_pair", {"source": "A", "target": "B", "shared_words": 2}),
        ("missing_requirement", {"node": "A", "node_key": "node:a"}),
        ("weak_edge", {"label": "liga A B", "source": "A", "target": "B"}),
        ("causal_gap", {"label": "A leva a B", "source": "A", "target": "B"}),
    ],
)
def test_every_gap_type_reaches_the_search(db, monkeypatch, gap_type, gap):
    """Research must not raise for any gap shape the analyser produces.

    On prod, `_research_gap` raised `KeyError: 'label'` for every
    `disconnected_pair`, because its templates were a dict of f-strings built
    eagerly: the `weak_edge` entry formatted `gap_data['label']` even when the
    caller asked about a disconnected pair, whose dict has no `label`. The
    exception escaped before the `gap_type not in templates` guard, so
    `_optimize_graph` died on its first gap and the whole research half of the
    cycle never ran. The caller catches it, which is why it looked like a slow
    night rather than a dead feature.

    Asserted for all four shapes, not just the one that broke: the bug was that
    the *unused* templates were evaluated, so any shape missing any key could
    take down the whole call.
    """
    asked = []

    def fake_chat(prompt, *a, **kw):
        asked.append(prompt)
        return None  # stop before the network; the prompt is what is under test

    monkeypatch.setattr(dream, "_safe_ollama_chat", fake_chat)
    monkeypatch.setattr(dream, "search_with_searxng", lambda *a, **kw: [])

    assert dream._research_gap(gap_type, gap) is None
    assert asked, f"{gap_type} never reached the LLM"
    if gap_type == "disconnected_pair":
        assert "A" in asked[0] and "B" in asked[0], asked[0]
    if gap_type == "missing_requirement":
        assert "A" in asked[0], asked[0]


def test_an_unknown_gap_type_is_ignored_rather_than_raised(db, monkeypatch):
    monkeypatch.setattr(
        dream, "_safe_ollama_chat", lambda *a, **kw: pytest.fail("asked the LLM")
    )
    assert dream._research_gap("not_a_gap_type", {"source": "A"}) is None


def test_dedupe_removes_exact_duplicate_memories(db):
    """Identical rows that sit apart were never compared, so they piled up."""
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, timestamp TEXT, text TEXT)")
    for i, text in enumerate(
        ["mesma coisa", "mesma coisa", "mesma coisa", "outra coisa"], start=1
    ):
        con.execute("INSERT INTO memories VALUES (?,?,?)", (i, "2026-01-01", text))
    con.commit()
    con.close()

    dream._dedupe_memories()

    con = sqlite3.connect(db)
    left = con.execute("SELECT id, text FROM memories ORDER BY id").fetchall()
    archived = con.execute(
        "SELECT COUNT(*) FROM memories_purged WHERE reason='dream_dedupe'"
    ).fetchone()[0]
    con.close()
    assert [r[1] for r in left] == ["mesma coisa", "outra coisa"], left
    assert left[0][0] == 1, "dedupe must keep the earliest id"
    assert archived == 2, "the deleted duplicates must be recoverable"


def test_a_weak_edge_is_found_by_its_canonical_key(db):
    """The key the research path builds must match the materialiser's.

    The gap carried the endpoints in original case and gap order; the stored
    row is lowercased and sorted. The UPDATE silently matched nothing, so the
    same weakness was re-researched every night and never fixed.
    """
    _insert(db, "edge:alfa|beta", "edge", "Alfa + Beta", "Alfa", "Beta",
            gmif_level="M1")

    wrote = dream._apply_research_to_graph(
        {
            "conhecimento": "Alfa suporta Beta",
            "confianca": 0.7,
            "evidencia": ["trecho"],
            "desired_level": "M3",
        },
        "weak_edge",
        {"source": "Beta", "target": "Alfa"},  # reversed, no node_key
    )

    assert wrote is True
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT gmif_level, gmif_classified_by FROM memory_graph WHERE node_key='edge:alfa|beta'"
    ).fetchone()
    con.close()
    assert row is not None
    assert row[0] == "M3"
    assert row[1] == "dream_gmif_research"


def test_consolidation_reports_an_unreachable_model_instead_of_succeeding(db, monkeypatch):
    """A silent no-op read as success on the page the owner watches.

    `_consolidate_memories` caught every exception and only printed, so
    /admin/brain/sleep/status reported the step as `ok` after ~180s while the
    memory count never moved and nothing was archived. On prod that is exactly
    what happened: both Ollama hosts timed out and the cycle still said the
    brain had been consolidated. The step has to fail loudly instead.
    """
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, timestamp TEXT, text TEXT)")
    for i in range(1, 9):
        con.execute("INSERT INTO memories VALUES (?,?,?)", (i, "2026-01-01", f"m{i}"))
    con.commit()
    con.close()

    monkeypatch.setattr(dream, "_safe_ollama_chat", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="Ollama"):
        dream._consolidate_memories()

    con = sqlite3.connect(db)
    left = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    con.close()
    assert left == 8, "a failed merge must not delete anything"


def test_consolidation_rejects_a_reply_with_no_summary(db, monkeypatch):
    """A model that answers but not with the field asked for is also a failure.

    Silently doing nothing here is how the brain ends up with the same
    repetitions the owner asked to have removed, while the log says the cycle
    consolidated.
    """
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, timestamp TEXT, text TEXT)")
    for i in range(1, 9):
        con.execute("INSERT INTO memories VALUES (?,?,?)", (i, "2026-01-01", f"m{i}"))
    con.commit()
    con.close()

    monkeypatch.setattr(dream, "_safe_ollama_chat", lambda *a, **kw: '{"outra_coisa": 1}')
    with pytest.raises(RuntimeError, match="memoria_consolidada"):
        dream._consolidate_memories()


def test_consolidation_with_too_few_rows_is_a_benign_no_op(db, monkeypatch):
    """Fewer than five rows is nothing to do, not an error.

    The distinction matters: the sleep cycle treats a raise as a failed cycle,
    so a fresh brain must not be reported as broken.
    """
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, timestamp TEXT, text TEXT)")
    con.execute("INSERT INTO memories VALUES (1,'2026-01-01','so uma')")
    con.commit()
    con.close()

    monkeypatch.setattr(
        dream, "_safe_ollama_chat", lambda *a, **kw: pytest.fail("asked the model")
    )
    assert dream._consolidate_memories() is False


def test_one_failing_phase_does_not_abort_the_night(db, monkeypatch):
    """Consolidate fails, the graph dream and the research still run.

    Before the phases were isolated, an exception in the merge ended
    perform_dreaming before the research half, so a slow model cost the night
    its whole second half.
    """
    called = []
    monkeypatch.setattr(dream, "DREAM_ENABLED", True)
    monkeypatch.setattr(dream, "GMIF_DREAM_ENABLED", True)
    monkeypatch.setattr(dream, "_reduce_to_concepts", lambda: called.append("reduce"))
    monkeypatch.setattr(dream, "_dedupe_memories", lambda: called.append("dedupe"))
    monkeypatch.setattr(
        dream,
        "_consolidate_memories",
        lambda: (_ for _ in ()).throw(RuntimeError("LLM inacessível")),
    )
    monkeypatch.setattr(
        dream, "_optimize_graph", lambda **_: called.append("graph")
    )
    monkeypatch.setattr(dream, "_research_half", lambda mode="auto": called.append("research"))

    dream.perform_dreaming()

    assert called == ["reduce", "dedupe", "graph", "research"], called


def _sleep_client(monkeypatch, tmp_path):
    """A test client for /admin/brain/sleep with the loopback identity."""
    from pathlib import Path

    from src.api import admin as admin_mod
    from src.api.routes import create_app

    ident = {"role": "admin", "email": "owner@example.invalid"}
    monkeypatch.setattr(admin_mod, "BRAIN_DB_PATH", Path(tmp_path / "brain.db"))
    monkeypatch.setattr(admin_mod, "_current_user_data", lambda: ident)
    monkeypatch.setattr(admin_mod, "_current_user", lambda: "owner@example.invalid")
    monkeypatch.setattr(admin_mod, "_bypass_or_none", lambda: ident)
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _wait_for_sleep_cycle(timeout=15.0):
    """The endpoint returns 202 and runs the cycle in a thread."""
    from src.api import admin as admin_mod

    deadline = time.time() + timeout
    while time.time() < deadline:
        state = admin_mod._LAST_SLEEP_CYCLE.get("state") or {}
        if state.get("status") in {"done", "failed"}:
            return state
        time.sleep(0.05)
    raise AssertionError("the sleep cycle never reached a terminal state")


def test_the_sleep_cycle_does_not_consolidate_twice(db, monkeypatch, tmp_path):
    """The manual cycle ran the merge twice: 40 rows removed, summary re-merged.

    `_run_sleep_cycle` stepped `_consolidate_memories` and `_optimize_graph`,
    then called `perform_dreaming` for its "dream" step -- which runs both of
    them again. So one press of "Dormir e Sonhar" deleted up to 40 memories
    instead of 20, and the second pass consolidated the summary the first pass
    had just written. That is the opposite of removing redundancy, and it is why
    a manual cycle was more destructive than a nightly one.
    """
    calls = []

    def _record(name, returns=True):
        def _fn(*a, **kw):
            calls.append(name)
            return returns
        return _fn

    monkeypatch.setattr(dream, "GMIF_DREAM_ENABLED", True)
    monkeypatch.setattr(dream, "_reduce_to_concepts", _record("_reduce_to_concepts"))
    monkeypatch.setattr(dream, "_dedupe_memories", _record("_dedupe_memories"))
    monkeypatch.setattr(dream, "_consolidate_memories", _record("_consolidate_memories"))
    monkeypatch.setattr(dream, "_optimize_graph", _record("_optimize_graph"))
    monkeypatch.setattr(dream, "_research_half", _record("_research_half"))

    import src.brain.reconcile as reconcile_mod
    monkeypatch.setattr(
        reconcile_mod, "reconcile_refs", lambda path: {"pending": 0, "relinked": 0}
    )

    client = _sleep_client(monkeypatch, tmp_path)
    assert client.post("/admin/brain/sleep").status_code == 202
    state = _wait_for_sleep_cycle()

    assert state["status"] == "done", state
    for phase in ("_consolidate_memories", "_optimize_graph"):
        assert calls.count(phase) == 1, (
            f"{phase} ran {calls.count(phase)} times in one cycle: {calls}"
        )
    assert calls.count("_research_half") == 1, calls
    assert calls.index("_reduce_to_concepts") < calls.index("_consolidate_memories"), (
        "concepts must be extracted before the merge deletes the rows that "
        f"held them, or the concepts are lost once per press: {calls}"
    )
    assert calls.index("_dedupe_memories") < calls.index("_consolidate_memories"), (
        "duplicates are removed before the merge, not after: " f"{calls}"
    )


def test_the_sleep_cycle_records_a_failed_merge_as_a_failed_step(db, monkeypatch, tmp_path):
    """An unreachable model must show up in /brain/sleep/status as a failure.

    The step used to be reported as `ok` after three minutes of timeouts,
    because `_consolidate_memories` swallowed the error. The status endpoint
    exists so that this is visible.
    """
    monkeypatch.setattr(dream, "GMIF_DREAM_ENABLED", False)
    monkeypatch.setattr(dream, "_dedupe_memories", lambda: None)
    monkeypatch.setattr(dream, "_reduce_to_concepts", lambda: None)
    monkeypatch.setattr(dream, "_optimize_graph", lambda **_: None)
    monkeypatch.setattr(dream, "_research_half", lambda mode="auto": None)
    monkeypatch.setattr(
        dream,
        "_consolidate_memories",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nenhum host Ollama")),
    )

    import src.brain.reconcile as reconcile_mod
    monkeypatch.setattr(
        reconcile_mod, "reconcile_refs", lambda path: {"pending": 0}
    )

    client = _sleep_client(monkeypatch, tmp_path)
    assert client.post("/admin/brain/sleep").status_code == 202
    state = _wait_for_sleep_cycle()

    step = state["steps"]["consolidate_memories"]
    assert step["ok"] is False, f"a failed merge was reported as ok: {state}"
    assert "Ollama" in step["error"], state


def test_a_failed_merge_does_not_cost_the_cycle_its_graph_and_research(
    db, monkeypatch, tmp_path
):
    """A model too slow to merge must not stop the graph from being dreamed.

    The consolidation prompt is 20 memories of JSON, measured at ~118s on the
    primary host, against a 90s per-host budget. So it times out, and when it
    did the unguarded `_step` failed the whole cycle: gmif_dream and dream
    never ran, and the graph kept the disconnected pairs that only the research
    half can resolve. The step is still reported as failed -- nothing is
    hidden -- it just no longer takes the other halves down with it.
    """
    calls = []

    monkeypatch.setattr(dream, "GMIF_DREAM_ENABLED", True)
    monkeypatch.setattr(dream, "_reduce_to_concepts", lambda: calls.append("reduce"))
    monkeypatch.setattr(dream, "_dedupe_memories", lambda: calls.append("dedupe"))
    monkeypatch.setattr(
        dream,
        "_consolidate_memories",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nenhum host Ollama")),
    )
    monkeypatch.setattr(
        dream, "_optimize_graph", lambda **_: calls.append("graph")
    )
    monkeypatch.setattr(dream, "_research_half", lambda mode="auto": calls.append("research"))

    import src.brain.reconcile as reconcile_mod
    monkeypatch.setattr(
        reconcile_mod, "reconcile_refs", lambda path: {"pending": 0}
    )

    client = _sleep_client(monkeypatch, tmp_path)
    assert client.post("/admin/brain/sleep").status_code == 202
    state = _wait_for_sleep_cycle()

    assert state["status"] == "done", state
    assert state["steps"]["consolidate_memories"]["ok"] is False, state
    assert "graph" in calls and "research" in calls, (
        f"the merge failure took the other halves down with it: {calls}"
    )
