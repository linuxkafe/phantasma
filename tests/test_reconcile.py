"""T047 -- the sleep cycle's reconciliation step.

The behaviour that matters here is what it does NOT do. An LLM asked to fix a
dangling reference always produces an answer, so the only defence against a
confident guess is structural: require online evidence, and refuse to write
without it. These tests pin that refusal down, because a regression would not
raise anything -- it would quietly delete a relationship or inflate the graph.

Reads go through `build_graph_from_db`, the same function the endpoint calls.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.api.memory_graph import build_graph_from_db
from src.brain import reconcile
from src.brain.graph_edit import ensure_schema

DDL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
EDGE = "edge:a|b"
QUOTE = "Plataformas digitais e dependencia de plataformas sao o mesmo conceito"


def make_brain(tmp_path, *, promote_label=None, target="Depende de Plataformas Digitais"):
    """One edge whose target has no matching node, plus two real nodes.

    `promote_label` creates a third edge instead, whose source is also
    dangling, so a single cycle has to deal with a relink and a promote.
    """
    db = tmp_path / "brain.db"
    con = sqlite3.connect(db)
    con.executescript(DDL)
    rows = [
        ("node:capitalismo tardio", "node", "Capitalismo Tardio"),
        ("node:plataformas digitais", "node", "Plataformas Digitais"),
    ]
    for key, kind, label in rows:
        con.execute(
            "INSERT INTO memory_graph (node_key,node_type,label,affinity,weight,"
            "touch_count,created_at,updated_at) VALUES (?,?,?,0,1,0,'t','t')",
            (key, kind, label),
        )
    con.execute(
        "INSERT INTO memory_graph (node_key,node_type,label,source,target,"
        "affinity,weight,touch_count,created_at,updated_at) "
        "VALUES (?,?,?,?,?,0.15,0.77,0,'t','t')",
        (EDGE, "edge", f"Capitalismo Tardio -> {target}", "Capitalismo Tardio", target),
    )
    if promote_label:
        # The target MUST be a stored node, otherwise this edge dangles on both
        # ends and the test stops being about a single-sided promote. This was
        # the first version's bug: it produced 3 pending refs, not 2, and the
        # "one bad ref does not stop the others" count was wrong.
        con.execute(
            "INSERT INTO memory_graph (node_key,node_type,label,affinity,weight,"
            "touch_count,created_at,updated_at) VALUES (?,?,?,0,1,0,'t','t')",
            ("node:marca registrada", "node", "Marca Registada"),
        )
        con.execute(
            "INSERT INTO memory_graph (node_key,node_type,label,source,target,"
            "affinity,weight,touch_count,created_at,updated_at) "
            "VALUES (?,?,?,?,?,0,1,0,'t','t')",
            (
                "edge:outro|novo",
                "edge",
                "Marca -> Marca Registada",
                promote_label,
                "Marca Registada",
            ),
        )
    con.commit()
    con.close()
    ensure_schema(sqlite3.connect(db))
    return db


def unresolved(db):
    return build_graph_from_db(db)["stats"]["unresolved_edges"]


def counts(db):
    con = sqlite3.connect(db)
    try:
        return (
            con.execute("SELECT COUNT(*) FROM memory_graph").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM graph_edit_audit").fetchone()[0],
        )
    finally:
        con.close()


def audit(db):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT op, actor, before_json, after_json FROM graph_edit_audit ORDER BY rowid"
        ).fetchall()
    finally:
        con.close()


def no_research(monkeypatch):
    """SearxNG down or unreachable."""
    monkeypatch.setattr(reconcile, "_search", lambda prompt: [])


def answering(
    monkeypatch,
    decision="relink",
    target="Plataformas Digitais",
    evidence=(QUOTE,),
    confidence=0.9,
):
    monkeypatch.setattr(
        reconcile,
        "_search",
        lambda prompt: [{"title": "t", "content": QUOTE}],
    )

    def ask(prompt, system):
        if "query de pesquisa" in prompt:
            return "plataformas digitais dependencia"
        if "Curador" in system:
            return json.dumps(
                {
                    "decision": decision,
                    "target_label": target,
                    "evidence": list(evidence),
                    "confidence": confidence,
                    "reason": "teste",
                }
            )
        return None

    monkeypatch.setattr(reconcile, "_ask", ask)


@pytest.fixture(autouse=True)
def _stub_networked_ask(monkeypatch):
    """No unit test may dial the real Ollama host.

    On 2026-09-29 the primary host stopped answering. Any reconcile test that
    stubbed only ``_search`` and not ``_ask`` dialled the network and hung in
    transport time. ``answering()`` runs later in the test body and overrides
    this with whichever decision the test actually wants.
    """

    def offline_ask(prompt, system):
        if "query de pesquisa" in prompt:
            return "consulta de teste offline"
        return json.dumps({"decision": "ambiguous", "reason": "offline stub"})

    monkeypatch.setattr(reconcile, "_ask", offline_ask)


# --- enumeration -------------------------------------------------------------


def test_finds_the_pending_endpoint_and_its_edge(tmp_path):
    found = reconcile.find_dangling(make_brain(tmp_path))
    assert len(found) == 1
    assert found[0]["edge_key"] == EDGE
    assert found[0]["side"] == "target"
    assert found[0]["label"] == "Depende de Plataformas Digitais"
    assert found[0]["candidates"], "needs the stored nodes as a shortlist"


def test_both_ends_dangling_are_both_reported(tmp_path):
    found = reconcile.find_dangling(make_brain(tmp_path, promote_label="Marca Nao Registrada"))
    assert {(f["edge_key"], f["side"]) for f in found} == {
        (EDGE, "target"),
        ("edge:outro|novo", "source"),
    }


def test_a_resolved_ref_is_not_pending(tmp_path):
    db = make_brain(tmp_path)
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE memory_graph SET target = 'Plataformas Digitais' WHERE node_key = ?",
        (EDGE,),
    )
    con.commit()
    con.close()
    assert reconcile.find_dangling(db) == []


# --- the safety guard: no evidence, no write --------------------------------


def test_without_research_nothing_is_written(tmp_path):
    db = make_brain(tmp_path)
    no_research(monkeypatch := pytest.MonkeyPatch())
    try:
        report = reconcile.reconcile_refs(db)
    finally:
        monkeypatch.undo()
    assert report["pending"] == 1
    assert report["ambiguous"] == 1
    assert report["relinked"] == 0 and report["promoted"] == 0
    assert counts(db) == (3, 0), "a decision without research must not write"
    assert unresolved(db) == 1, "and the ref must still be there for a human"
    assert report["details"][0]["outcome"] == "ambiguous"


def test_research_but_no_evidence_quote_does_not_write(tmp_path, monkeypatch):
    db = make_brain(tmp_path)
    answering(monkeypatch, evidence=())  # decision, but nothing quoted
    report = reconcile.reconcile_refs(db)
    assert report["ambiguous"] == 1
    assert report["resolved"] == 0
    assert counts(db) == (3, 0)


def test_low_confidence_does_not_write(tmp_path, monkeypatch):
    db = make_brain(tmp_path)
    answering(monkeypatch, confidence=0.4)
    report = reconcile.reconcile_refs(db)
    assert report["ambiguous"] == 1
    assert "confidence" in report["details"][0]["reason"]
    assert counts(db) == (3, 0)


def test_a_hallucinated_candidate_is_ambiguous_not_a_write(tmp_path, monkeypatch):
    """The model may only relink to a node that exists.

    A relink to a label with no node is refused by resolve_dangling, so the
    step must not claim a resolution it did not perform.
    """
    db = make_brain(tmp_path)
    answering(monkeypatch, target="Conceito Inventado")
    report = reconcile.reconcile_refs(db)
    assert report["resolved"] == 0
    assert report["skipped"] + report["ambiguous"] == 1
    assert counts(db) == (3, 0)


def test_unparseable_answer_is_ambiguous(tmp_path, monkeypatch):
    db = make_brain(tmp_path)
    monkeypatch.setattr(reconcile, "_search", lambda p: [{"content": QUOTE}])
    monkeypatch.setattr(reconcile, "_ask", lambda p, s: "não sei, é uma relação")
    report = reconcile.reconcile_refs(db)
    assert report["ambiguous"] == 1
    assert counts(db) == (3, 0)


# --- the write path ----------------------------------------------------------


def test_grounded_relink_resolves_the_ref_and_is_audited(tmp_path, monkeypatch):
    db = make_brain(tmp_path)
    answering(monkeypatch)
    report = reconcile.reconcile_refs(db)
    assert report["relinked"] == 1 and report["ambiguous"] == 0
    assert unresolved(db) == 0
    rows = audit(db)
    assert len(rows) == 1
    op, actor, before_json, after_json = rows[0]
    assert op == "edge.relink"
    assert actor == reconcile.ACTOR == "sleep-cycle", "machine writes must be attributable"
    assert "Depende de Plataformas Digitais" in before_json
    assert QUOTE in after_json, "the evidence has to be in the audit row"
    con = sqlite3.connect(db)
    try:
        assert (
            con.execute("SELECT target FROM memory_graph WHERE node_key = ?", (EDGE,)).fetchone()[0]
            == "Plataformas Digitais"
        )
    finally:
        con.close()


def test_grounded_promote_creates_the_node_and_resolves(tmp_path, monkeypatch):
    db = make_brain(tmp_path, promote_label="Marca Nao Registrada")
    monkeypatch.setattr(reconcile, "_search", lambda p: [{"content": QUOTE}])
    monkeypatch.setattr(
        reconcile,
        "_ask",
        lambda prompt, system: (
            "q"
            if "query de pesquisa" in prompt
            else json.dumps(
                {
                    "decision": "promote",
                    "target_label": "Marca Nao Registrada",
                    "evidence": [QUOTE],
                    "confidence": 0.8,
                    "reason": "conceito novo",
                }
            )
        ),
    )
    report = reconcile.reconcile_refs(db)
    assert report["promoted"] == 1
    assert unresolved(db) == 0
    assert [op for op, *_ in audit(db)] == ["node.promote"]


# --- idempotence -------------------------------------------------------------


def test_second_run_changes_nothing(tmp_path, monkeypatch):
    db = make_brain(tmp_path)
    answering(monkeypatch)
    first = reconcile.reconcile_refs(db)
    after_first = counts(db)
    second = reconcile.reconcile_refs(db)
    assert first["resolved"] == 1
    assert second["pending"] == 0, "a resolved ref must not be seen again"
    assert counts(db) == after_first, "the second run must not write"


def test_two_runs_on_two_edges_do_not_double_count(tmp_path, monkeypatch):
    db = make_brain(tmp_path, promote_label="Marca Nao Registrada")
    answering(monkeypatch)
    reconcile.reconcile_refs(db)
    n_after = counts(db)[0]
    reconcile.reconcile_refs(db)
    assert counts(db)[0] == n_after
    assert unresolved(db) == 0


# --- failure containment -----------------------------------------------------


def test_a_broken_search_does_not_raise(tmp_path, monkeypatch):
    db = make_brain(tmp_path)

    def boom(prompt):
        raise RuntimeError("searxng down")

    monkeypatch.setattr(reconcile, "_search", boom)
    report = reconcile.reconcile_refs(db)  # must not raise
    assert report["pending"] == 1
    assert report["resolved"] == 0
    assert counts(db) == (3, 0)


def test_one_bad_ref_does_not_stop_the_others(tmp_path, monkeypatch):
    db = make_brain(tmp_path, promote_label="Marca Nao Registrada")
    calls = {"n": 0}

    def search(prompt):
        calls["n"] += 1
        raise RuntimeError("searxng down")

    monkeypatch.setattr(reconcile, "_search", search)
    before_rows = counts(db)
    report = reconcile.reconcile_refs(db)
    assert report["pending"] == 2
    assert calls["n"] == 2, "both refs must be attempted even though the first failed"
    # A search that RAISES is an environment failure, not an ambiguous ref, and
    # the distinction is the point: `failed` shows up in /admin/brain/sleep/status
    # as a step failure, whereas `ambiguous` reads like "the model was undecided".
    # Reporting a broken SearxNG as the latter would hide an outage.
    assert report["failed"] == 2
    assert report["ambiguous"] == 0
    assert report["resolved"] == 0
    # Compared against the pre-run state rather than a hard-coded row
    # count: the exact number of rows is fixture trivia, "nothing moved" is
    # the invariant that matters.
    assert counts(db) == before_rows, "a failing search must not write"
    assert unresolved(db) == 2


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    db = make_brain(tmp_path)
    answering(monkeypatch)
    report = reconcile.reconcile_refs(db, dry_run=True)
    assert report["pending"] == 1
    assert report["relinked"] == 0
    assert counts(db) == (3, 0)
    assert unresolved(db) == 1
    assert report["details"][0]["outcome"] == "would relink"


def test_it_never_touches_memories(tmp_path, monkeypatch):
    db = make_brain(tmp_path)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO memories (timestamp, text) VALUES ('t','plataformas')")
    con.commit()
    con.close()
    answering(monkeypatch)
    reconcile.reconcile_refs(db)
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT id, text FROM memories").fetchall()
    finally:
        con.close()
    assert rows == [(1, "plataformas")], "reconciliation must not rewrite memories"


def test_it_never_deletes_an_edge(tmp_path, monkeypatch):
    db = make_brain(tmp_path, promote_label="Marca Nao Registrada")
    answering(monkeypatch)
    reconcile.reconcile_refs(db)
    con = sqlite3.connect(db)
    try:
        edges = con.execute(
            "SELECT node_key FROM memory_graph WHERE node_type = 'edge' ORDER BY node_key"
        ).fetchall()
    finally:
        con.close()
    assert edges == [("edge:a|b",), ("edge:outro|novo",)]


def test_a_resolved_ref_racing_a_human_is_skipped_not_failed(tmp_path, monkeypatch):
    """If a human resolved it while the cycle was deciding, that is fine."""
    db = make_brain(tmp_path)
    answering(monkeypatch)
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE memory_graph SET target = 'Plataformas Digitais' WHERE node_key = ?",
        (EDGE,),
    )
    con.commit()
    con.close()
    # find_dangling no longer reports it, so the step sees nothing to do
    report = reconcile.reconcile_refs(db)
    assert report["pending"] == 0
    assert report["failed"] == 0
    assert unresolved(db) == 0
