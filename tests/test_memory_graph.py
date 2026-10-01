"""Tests for the 3D memory-explorer payload builder.

Run: pytest tests/test_memory_graph.py -q
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.memory_graph import (  # noqa: E402
    build_graph,
    build_graph_from_db,
    normalise,
    parse_memory,
    slug,
)

BRAIN_DB = Path(__file__).resolve().parent.parent / "data" / "brain.db"


def test_normalise_is_accent_and_case_insensitive():
    assert normalise("Capitalismo Tardio") == normalise("capitalismo tardio")
    assert normalise("Plataformas Digitais") != normalise("Capitalismo Tardio")
    assert normalise(" -energy  ") == "energy"


def test_slug_is_url_safe():
    assert slug("Capitalismo Tardio") == "capitalismo-tardio"
    assert slug("!!!") == "x"


def test_parse_memory_reads_tags_facts_mermaid():
    payload = json.dumps(
        {
            "tags": ["Capitalismo Tardio", "Plataformas Digitais"],
            "facts": ["depende de plataformas"],
            "mermaid": "graph TD;\nA[X]-->B[Y]",
        }
    )
    parsed = parse_memory(payload)
    assert parsed["parsed"] is True
    assert parsed["tags"] == ["Capitalismo Tardio", "Plataformas Digitais"]
    assert parsed["facts"] == ["depende de plataformas"]
    assert parsed["mermaid"].startswith("graph TD;")
    assert parsed["preview"] == "Capitalismo Tardio"


def test_parse_memory_handles_key_variants():
    parsed = parse_memory(json.dumps({"tags_pt": ["a"], "facts_en": ["b"]}))
    assert parsed["tags"] == ["a"]
    assert parsed["facts"] == ["b"]


def test_parse_memory_degrades_on_garbage():
    parsed = parse_memory("{not json at all")
    assert parsed["parsed"] is False
    assert parsed["tags"] == []
    assert parsed["preview"].startswith("{not json")


def test_parse_memory_plain_text():
    parsed = parse_memory("Conhecimento Profundo: o texto")
    assert parsed["parsed"] is False
    assert parsed["preview"].startswith("Conhecimento")


def test_build_graph_links_memory_to_its_tags():
    payload = build_graph(
        [(1, "2026-01-01", json.dumps({"tags": ["IA", "Memória"]}))],
        [],
    )
    ids = {n["id"] for n in payload["nodes"]}
    assert "mem:1" in ids
    assert "tag:ia" in ids and "tag:memoria" in ids
    assert payload["stats"]["memories"] == 1
    assert payload["stats"]["tags_distinct"] == 2
    assert payload["stats"]["links"] == 2


def test_build_graph_merges_tag_with_graph_node_by_label():
    """A tag and a memory_graph node with the same label must not duplicate."""
    rows = [(1, "t", json.dumps({"tags": ["Capitalismo Tardio"]}))]
    graph_rows = [
        (
            "node:capitalismo tardio",
            "node",
            "Capitalismo Tardio",
            None,
            None,
            0.4,
            2.0,
            7,
        )
    ]
    payload = build_graph(rows, graph_rows)
    concepts = [n for n in payload["nodes"] if n["kind"] == "concept"]
    assert len(concepts) == 1, concepts
    concept = concepts[0]
    assert concept["sources"] == ["tag", "graph"]
    assert concept["weight"] == 2.0
    assert concept["touch_count"] == 7
    assert concept["affinity"] == 0.4


def test_build_graph_flags_dangling_edge_instead_of_inventing_link():
    graph_rows = [
        ("node:a", "node", "A", None, None, 0.0, 1.0, 1),
        (
            "edge:a|b",
            "edge",
            "A -> B",
            "A",
            "B",
            0.9,
            1.0,
            1,
        ),
    ]
    payload = build_graph([], graph_rows)
    b = next(n for n in payload["nodes"] if n["label"] == "B")
    assert b["unresolved"] is True
    assert "dangling" in b["sources"]
    assert payload["stats"]["unresolved_edges"] == 1
    assert payload["stats"]["graph_edges"] == 1
    # the edge is still shown, connecting A to the flagged node
    graph_links = [link for link in payload["links"] if link["kind"] == "graph"]
    assert len(graph_links) == 1
    assert graph_links[0]["affinity"] == 0.9


def test_build_graph_never_creates_self_loops_or_duplicates():
    rows = [(1, "t", json.dumps({"tags": ["A", "A"]}))]
    graph_rows = [
        ("edge:a|a", "edge", "A -> A", "A", "A", 1.0, 1.0, 1),
    ]
    payload = build_graph(rows, graph_rows)
    assert all(link["source"] != link["target"] for link in payload["links"])
    assert len(payload["links"]) == 1  # one tagged link, self-edge dropped


def test_a_label_beyond_the_display_limit_does_not_duplicate_the_node():
    """Regression, found in the live database.

    Node labels are truncated to 60 chars for display, but edge endpoints used
    to be resolved by scanning those *truncated* labels. A label longer than
    the limit therefore looked absent, the endpoint fell into the "dangling"
    branch, ``concepts.get`` returned the SAME node, and it was appended to
    ``nodes`` a second time -- two nodes with one id. The structural-invariant
    test on the real brain.db caught it: 260 nodes, 259 unique ids.
    """
    long_label = "A" * 80  # beyond _MAX_LABEL
    rows = [(1, "t", json.dumps({"tags": [long_label]}))]
    graph_rows = [
        ("node:a", "node", long_label, None, None, 0.0, 1.0, 0),
        ("edge:a|b", "edge", "a -> b", long_label, long_label + "B", 0.5, 1.0, 0),
    ]
    payload = build_graph(rows, graph_rows)
    ids = [n["id"] for n in payload["nodes"]]
    assert len(ids) == len(set(ids)), f"duplicate node ids: {ids}"
    # The known source resolved; only the unknown target is unresolved.
    assert payload["stats"]["unresolved_edges"] == 1


def test_nodes_expose_degree_used_by_the_3d_layout():
    rows = [
        (1, "t", json.dumps({"tags": ["A"]})),
        (2, "t", json.dumps({"tags": ["A", "B"]})),
    ]
    payload = build_graph(rows, [])
    by_id = {n["id"]: n for n in payload["nodes"]}
    assert by_id["tag:a"]["degree"] == 2  # two memories tagged A
    assert by_id["tag:b"]["degree"] == 1
    assert by_id["mem:1"]["degree"] == 1
    assert by_id["mem:2"]["degree"] == 2
    assert all("degree" in n for n in payload["nodes"])


def _make_db(tmp_path, memories, flybrain_rows=()):
    """Build a deterministic brain.db so the assertions test the CODE, not the
    current contents of the production database.

    The two tests this replaces asserted stats["memories"] == 61 and
    memories_with_mermaid == 8 against the live brain.db. That is a
    snapshot of live data, not a property of build_graph_from_db: writing a
    single memory breaks the suite, and the function could be wrong in a way
    those numbers happened not to catch.
    """
    db = tmp_path / "fixture.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, timestamp TEXT, text TEXT)")
        conn.executemany(
            "INSERT INTO memories (id, timestamp, text) VALUES (?, ?, ?)",
            [(i, "2026-01-01T00:00:00", t) for i, t in enumerate(memories, start=1)],
        )
        # build_graph_from_db reads this table too, so the fixture must carry
        # it. Full column set from the production schema (memory_graph) so the
        # fixture stays valid if the query later selects more columns.
        conn.execute(
            "CREATE TABLE memory_graph ("
            "id INTEGER PRIMARY KEY, node_key TEXT, node_type TEXT, label TEXT,"
            " source TEXT, target TEXT, affinity REAL, weight REAL,"
            " touch_count INTEGER, created_at TEXT, updated_at TEXT,"
            " gmif_logical_form TEXT, gmif_validation_type TEXT,"
            " gmif_extraction_confidence REAL, gmif_validation_confidence REAL,"
            " gmif_source_chunks TEXT, gmif_level TEXT, gmif_classified_at TEXT,"
            " gmif_classified_by TEXT, node_gmif_type TEXT,"
            " node_gmif_confidence REAL, node_gmif_evidence TEXT)"
        )
        conn.execute(
            "CREATE TABLE flybrain_state (id INTEGER PRIMARY KEY, schema_version INTEGER,"
            " data TEXT, updated_at TEXT)"
        )
        for row in flybrain_rows:
            conn.execute(
                "INSERT INTO flybrain_state (id, schema_version, data, updated_at) VALUES (?,?,?,?)",
                row,
            )
        conn.commit()
    finally:
        conn.close()
    return db


def test_graph_counts_reflect_the_database_actually_read(tmp_path):
    """Two valid memories (one with mermaid), one unparseable."""
    db = _make_db(
        tmp_path,
        [
            json.dumps({"tags": ["leite"], "mermaid": "graph TD;A-->B;"}),
            json.dumps({"tags": ["agua"]}),
            "isto nao e json",
        ],
    )
    payload = build_graph_from_db(db)
    stats = payload["stats"]
    assert stats["memories"] == 3
    assert stats["memories_with_mermaid"] == 1
    assert stats["memories_unparsed"] == 1
    assert stats["nodes"] == len(payload["nodes"])
    assert stats["links"] == len(payload["links"])


def test_graph_payload_has_no_dangling_links_on_a_fixture(tmp_path):
    db = _make_db(
        tmp_path,
        [
            json.dumps({"tags": ["a", "b"], "mermaid": "graph TD;A-->B;"}),
            json.dumps({"tags": ["b"]}),
        ],
    )
    payload = build_graph_from_db(db)
    idset = {n["id"] for n in payload["nodes"]}
    assert len(idset) == len(payload["nodes"]), "duplicate node ids"
    for link in payload["links"]:
        assert link["source"] in idset, f"dangling source {link['source']}"
        assert link["target"] in idset, f"dangling target {link['target']}"


def test_empty_flybrain_table_is_reported_as_no_reinforcement(tmp_path):
    db = _make_db(tmp_path, [json.dumps({"tags": ["x"]})], flybrain_rows=())
    payload = build_graph_from_db(db)
    assert payload["stats"]["flybrain"] is None, (
        "claimed FlyBrain reinforcement with an empty table"
    )


def test_populated_flybrain_table_IS_reported(tmp_path):
    """The positive case. Against the live database flybrain_state was empty, so
    only the rows == 0 branch ever ran and the 'actually reinforced' branch was
    never exercised by anything."""
    state = json.dumps({"schema_version": 1, "steps": 3, "ring": {"orientation_deg": 12.0}})
    db = _make_db(
        tmp_path,
        [json.dumps({"tags": ["x"]})],
        flybrain_rows=[(1, 1, state, "2026-01-01T00:00:00")],
    )
    payload = build_graph_from_db(db)
    reported = payload["stats"]["flybrain"]
    assert reported is not None, (
        "a populated flybrain_state must be reported; 'no reinforcement' is "
        "otherwise indistinguishable from 'could not read it'"
    )


def test_live_database_is_structurally_consistent():
    """Structural invariants of the REAL database -- no magic numbers.

    The counts are deliberately not asserted here. Asserting them here is what
    made the suite depend on how much the assistant has happened to remember.
    """
    if not BRAIN_DB.exists():
        pytest.skip(f"live brain.db not present at {BRAIN_DB}")
    payload = build_graph_from_db(BRAIN_DB)
    idset = {n["id"] for n in payload["nodes"]}
    assert len(idset) == len(payload["nodes"]), "duplicate node ids in the live graph"
    for link in payload["links"]:
        assert link["source"] in idset, f"dangling source {link['source']}"
        assert link["target"] in idset, f"dangling target {link['target']}"
    stats = payload["stats"]
    assert stats["nodes"] == len(payload["nodes"])
    assert stats["links"] == len(payload["links"])


def test_flybrain_loader_uses_the_real_store_class():
    """Regression: a wrong class name was imported and the ImportError was
    swallowed, making 'no reinforcement' indistinguishable from 'I could not
    read it'. The class named here must exist in src.brain.persistence."""
    from src.api import memory_graph as mg

    source = Path(mg.__file__).read_text(encoding="utf-8")
    assert "FlyBrainPersistence" not in source.replace("``FlyBrainPersistence``", ""), (
        "stale FlyBrainPersistence reference outside the explanatory comment"
    )
    persistence = (Path(__file__).resolve().parent.parent / "src/brain/persistence.py").read_text(
        encoding="utf-8"
    )
    assert "class FlyBrainStore" in persistence
    assert mg._load_flybrain.__doc__


def test_flybrain_rows_that_cannot_be_decoded_are_reported_not_hidden(tmp_path):
    """If the table HAS rows, returning None would be a lie. The loader must
    surface an error instead."""
    from src.api import memory_graph as mg

    fake = tmp_path / "brain.db"
    conn = sqlite3.connect(fake)
    try:
        conn.execute(
            "CREATE TABLE flybrain_state (id INTEGER PRIMARY KEY, schema_version INTEGER,"
            " data BLOB, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO flybrain_state (id, schema_version, data, updated_at)"
            " VALUES (1, 1, X'00ff00ff', '2026-01-01')"
        )
        conn.commit()
    finally:
        conn.close()

    result = mg._load_flybrain(fake)
    assert result is not None, "rows exist, so None would be a false 'no data'"
    assert "error" in result, f"expected a reported error, got {result}"


def test_flybrain_empty_table_yields_none(tmp_path):
    from src.api import memory_graph as mg

    fake = tmp_path / "empty.db"
    conn = sqlite3.connect(fake)
    try:
        conn.execute(
            "CREATE TABLE flybrain_state (id INTEGER PRIMARY KEY, schema_version INTEGER,"
            " data BLOB, updated_at TEXT)"
        )
        conn.commit()
    finally:
        conn.close()
    assert mg._load_flybrain(fake) is None


def test_missing_database_degrades_without_raising():
    payload = build_graph_from_db("/nonexistent/path/brain.db")
    assert payload["nodes"] == []
    assert "error" in payload["stats"]
