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
