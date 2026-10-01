"""Stored memories must become concepts in the graph, traceably and once.

Run: pytest tests/test_memory_materialize.py -q

`memory_graph` used to be written only by index_memory, which runs when
skill_memory stores a memory. Rows that reached the table another way were never
indexed, so the admin reported an empty graph while 62 memories sat in the
database. The admin derives nodes per request and never writes them back, so
nothing else closed that gap.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from src.brain import memory_graph as mg  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "brain.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    mg.init_db()
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, timestamp TEXT, text TEXT)")
    con.commit()
    con.close()
    return path


def _memory(path, memory_id, payload):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO memories VALUES (?,?,?)",
        (memory_id, "2026-01-01", payload if isinstance(payload, str) else json.dumps(payload)),
    )
    con.commit()
    con.close()


def _graph(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    nodes = {r["node_key"]: dict(r) for r in con.execute(
        "SELECT * FROM memory_graph WHERE node_type='node'")}
    edges = {r["node_key"]: dict(r) for r in con.execute(
        "SELECT * FROM memory_graph WHERE node_type='edge'")}
    con.close()
    return nodes, edges


def test_tags_become_concept_nodes(db):
    _memory(db, 1, {"tags": ["leite", "pecuária de leite"]})

    report = mg.materialize_memories()

    nodes, _ = _graph(db)
    assert "node:leite" in nodes
    assert "node:pecuária de leite" in nodes
    assert report["nodes_written"] == 2


def test_co_mentioned_concepts_get_an_edge(db):
    _memory(db, 1, {"tags": ["leite", "pecuária de leite", "vacinas"]})

    mg.materialize_memories()

    _, edges = _graph(db)
    # Three concepts, so three unordered pairs.
    assert len(edges) == 3
    assert "edge:leite|pecuária de leite" in edges


def test_an_unordered_pair_gets_one_key_whichever_order_it_arrives(db):
    """The relation is undirected; two tag orders are still one relationship.

    Keyed in arrival order, "leite" + "pecuária" and "pecuária" + "leite"
    produced two rows that then grew apart -- 11 such pairs in production.
    """
    _memory(db, 1, {"tags": ["leite", "pecuária"]})
    _memory(db, 2, {"tags": ["pecuária", "leite"]})

    report = mg.materialize_memories()

    _, edges = _graph(db)
    assert list(edges) == ["edge:leite|pecuária"], edges
    assert edges["edge:leite|pecuária"]["touch_count"] == 2
    assert report["edges_written"] == 1


def test_index_markers_are_bookkeeping_not_concepts(db):
    """A marker says "this row is indexed"; it is not something believed.

    Stored as node_type='node' these 47 rows rendered as concepts and inflated
    the node count, and their sentence-shaped labels fed the gap scan.
    """
    _memory(db, 1, {"tags": ["leite"]})
    mg.materialize_memories()

    con = sqlite3.connect(db)
    markers = con.execute(
        "SELECT COUNT(*) FROM memory_graph WHERE node_type='marker'"
    ).fetchone()[0]
    as_nodes = con.execute(
        "SELECT COUNT(*) FROM memory_graph WHERE node_type='node' AND node_key LIKE 'memory:%'"
    ).fetchone()[0]
    con.close()

    assert markers == 1
    assert as_nodes == 0, "an indexing marker was stored as a concept"


def test_a_co_occurrence_edge_is_external_not_logical(db):
    """The only warrant is that both appeared in one row: an observation."""
    _memory(db, 1, {"tags": ["leite", "pecuária"]})

    mg.materialize_memories()

    _, edges = _graph(db)
    edge = edges["edge:leite|pecuária"]
    assert edge["gmif_level"] == "M1"
    assert edge["gmif_validation_type"] == "external", (
        "co-occurrence was recorded as a logical derivation"
    )


def test_every_concept_traces_back_to_its_memory(db):
    """Provenance, so a concept can always be traced to the text."""
    _memory(db, 7, {"tags": ["leite"]})

    mg.materialize_memories()

    nodes, _ = _graph(db)
    chunks = json.loads(nodes["node:leite"]["gmif_source_chunks"])
    assert chunks == [{"memory_id": 7}]


def test_short_and_long_tags_are_not_concepts(db):
    _memory(db, 1, {"tags": ["Olá", "Ok", "x", "a" * 80, "leite"]})

    mg.materialize_memories()

    nodes, _ = _graph(db)
    concepts = {k for k in nodes if not k.startswith("memory:")}
    assert concepts == {"node:leite"}, "greetings and filler leaked into the graph"


def test_an_unparsed_memory_is_skipped_not_guessed(db):
    """Prose with no structure must not become invented concepts."""
    _memory(db, 1, "isto o capitalismo funciona, e das alternativas testadas, é mau.")
    _memory(db, 2, {"tags": ["leite"]})

    report = mg.materialize_memories()

    nodes, _ = _graph(db)
    concepts = {k for k in nodes if not k.startswith("memory:")}
    assert concepts == {"node:leite"}
    assert report["skipped"] >= 1


def test_materializing_twice_writes_nothing_new(db):
    _memory(db, 1, {"tags": ["leite", "pecuária"]})

    first = mg.materialize_memories()
    second = mg.materialize_memories()

    assert first["nodes_written"] == 2 and first["edges_written"] == 1
    assert second["nodes_written"] == 0 and second["edges_written"] == 0, (
        "the cycle runs nightly; a rerun must not rewrite the graph"
    )


def test_only_unparsed_skips_what_was_already_indexed(db):
    _memory(db, 1, {"tags": ["leite"]})
    _memory(db, 2, {"tags": ["pecuária"]})
    mg.materialize_memories()

    _memory(db, 3, {"tags": ["vaccinas"]})
    report = mg.materialize_memories(only_unparsed=True)

    nodes, _ = _graph(db)
    assert "node:vaccinas" in nodes
    assert "node:leite" in nodes, "already-indexed concepts must survive"
    assert report["memories_read"] < 3


def test_a_reused_row_id_is_reindexed_not_skipped(db):
    """Consolidation frees high row ids; a new row can take one back.

    An id-only "already indexed" marker would then skip the new memory and its
    concepts would never reach the graph.
    """
    _memory(db, 5, {"tags": ["leite"]})
    mg.materialize_memories()

    con = sqlite3.connect(db)
    con.execute("DELETE FROM memories WHERE id = 5")
    con.execute(
        "INSERT INTO memories VALUES (?,?,?)",
        (5, "2026-01-02", json.dumps({"tags": ["azeite"]})),
    )
    con.commit()
    con.close()

    mg.materialize_memories(only_unparsed=True)

    nodes, _ = _graph(db)
    assert "node:azeite" in nodes, "the new row reusing id 5 was never indexed"


def test_limit_caps_how_many_memories_are_read(db):
    for i in range(1, 6):
        _memory(db, i, {"tags": [f"conceito{i}"]})

    report = mg.materialize_memories(limit=2)

    assert report["memories_read"] == 2


def test_a_repeated_concept_keeps_one_node_and_counts_touches(db):
    _memory(db, 1, {"tags": ["leite"]})
    _memory(db, 2, {"tags": ["leite"]})

    mg.materialize_memories()

    nodes, _ = _graph(db)
    assert nodes["node:leite"]["touch_count"] == 2


def test_the_admin_reads_the_same_graph(db):
    """The gap this closes: the admin must see what the cycle writes."""
    from src.api.memory_graph import build_graph_from_db

    _memory(db, 1, {"tags": ["leite", "pecuária"]})
    mg.materialize_memories()

    payload = build_graph_from_db(db)

    assert payload["stats"]["graph_nodes"] >= 2
    assert payload["stats"]["graph_edges"] >= 1


def test_the_admin_does_not_draw_index_markers(db):
    """The explorer must show concepts, not the bookkeeping that indexes them."""
    from src.api.memory_graph import build_graph_from_db

    _memory(db, 1, {"tags": ["leite"]})
    _memory(db, 2, "sem tags nenhumas")
    mg.materialize_memories()

    payload = build_graph_from_db(db)

    leaked = [n for n in payload["nodes"] if str(n.get("label", "")).startswith("memória #")]
    assert leaked == [], f"markers leaked into the graph view: {leaked}"
    assert payload["stats"]["graph_nodes"] == 1
