"""The graph schema must be owned by code, not by a hand-edited database.

Run: pytest tests/test_graph_schema.py -q

`init_db()` used to create `memory_graph` without any GMIF column and could only
ever say CREATE TABLE IF NOT EXISTS, so it could never add them to a database
that already had the table. The eleven claim columns lived only in production's
brain.db, added by hand. Dev, and any fresh checkout, had none -- so every query
in the GMIF dream cycle raised "no such column" and the blanket `except` turned
it into a print. These tests fail if that ever recurs.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brain import memory_graph as mg  # noqa: E402

# Copied verbatim from `sqlite3 .schema memory_graph` on
# /opt/phantasma/data/brain.db. Pinned on purpose: if this list and production
# ever disagree, the test fails here instead of the graph quietly stopping
# growing in production three weeks later.
PROD_SCHEMA = (
    ("gmif_logical_form", "TEXT", None),
    ("gmif_validation_type", "TEXT", None),
    ("gmif_extraction_confidence", "REAL", "0.0"),
    ("gmif_validation_confidence", "REAL", "0.0"),
    ("gmif_source_chunks", "TEXT", None),
    ("gmif_level", "TEXT", None),
    ("gmif_classified_at", "TEXT", None),
    ("gmif_classified_by", "TEXT", None),
    ("node_gmif_type", "TEXT", None),
    ("node_gmif_confidence", "REAL", "0.0"),
    ("node_gmif_evidence", "TEXT", None),
)

# The base table as init_db() created it before this ticket: no GMIF columns.
LEGACY_SCHEMA = """
CREATE TABLE memory_graph (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_key TEXT NOT NULL UNIQUE,
    node_type TEXT NOT NULL,
    label TEXT NOT NULL,
    source TEXT,
    target TEXT,
    affinity REAL NOT NULL DEFAULT 0.0,
    weight REAL NOT NULL DEFAULT 1.0,
    touch_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A graph database at a temporary path, created by the module itself."""
    path = tmp_path / "brain.db"
    monkeypatch.setattr(mg.config, "DB_PATH", str(path))
    return path


def _columns(path):
    con = sqlite3.connect(path)
    try:
        return {r[1]: (r[2], r[4]) for r in con.execute("PRAGMA table_info(memory_graph)")}
    finally:
        con.close()


def test_the_declared_columns_match_production():
    """GMIF_COLUMNS is the contract with production, not a local preference."""
    assert [name for name, _ in mg.GMIF_COLUMNS] == [n for n, _, _ in PROD_SCHEMA]
    for (_name, decl), (name, decl_type, default) in zip(mg.GMIF_COLUMNS, PROD_SCHEMA):
        assert decl.startswith(decl_type), f"{name}: {decl!r} is not a {decl_type}"
        if default is not None:
            assert f"DEFAULT {default}" in decl, f"{name} lost its production default"


def test_a_fresh_database_gets_every_gmif_column(db):
    mg.init_db()
    cols = _columns(db)
    for name, decl_type, default in PROD_SCHEMA:
        assert name in cols, f"{name} missing from a freshly created graph"
        assert cols[name][0] == decl_type
        if default is None:
            assert cols[name][1] is None
        else:
            assert cols[name][1] == default


def test_a_legacy_database_is_migrated_and_keeps_its_rows(db):
    """The dev case: table exists, claim columns do not, data must survive."""
    con = sqlite3.connect(db)
    con.executescript(LEGACY_SCHEMA)
    con.execute(
        "INSERT INTO memory_graph (node_key, node_type, label, affinity, weight,"
        " touch_count, created_at, updated_at)"
        " VALUES ('node:capitalismo', 'node', 'Capitalismo Tardio', 0.5, 1.0, 3,"
        " '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()

    mg.init_db()

    cols = _columns(db)
    for name, _, _ in PROD_SCHEMA:
        assert name in cols, f"{name} was not migrated onto an existing graph"
    con = sqlite3.connect(db)
    row = con.execute("SELECT label, touch_count FROM memory_graph").fetchone()
    con.close()
    assert row == ("Capitalismo Tardio", 3), "migration lost an existing row"


def test_init_db_is_idempotent(db):
    mg.init_db()
    first = _columns(db)
    mg.init_db()
    mg.init_db()
    assert _columns(db) == first


def test_init_db_is_a_no_op_on_an_already_migrated_database(db):
    """A deploy must not rewrite production, only converge an unmigrated one."""
    mg.init_db()
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO memory_graph (node_key, node_type, label, touch_count,"
        " created_at, updated_at)"
        " VALUES ('n', 'node', 'x', 1, '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()
    before = _columns(db)

    mg.init_db()

    assert _columns(db) == before, "a second init_db altered the schema"
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM memory_graph").fetchone()[0] == 1
    con.close()


def test_the_gmif_dream_queries_run_against_a_migrated_graph(db):
    """The regression that mattered: these are skill_gmif_dream's own queries.

    Copied from `_analyze_graph_gaps()`. Before this ticket they raised
    "no such column: node_gmif_type" on any database that had not been fixed by
    hand, which the cycle's blanket `except` swallowed into a print.
    """
    mg.init_db()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    nodes = con.execute(
        "SELECT id, node_key, label, node_gmif_type, node_gmif_confidence "
        "FROM memory_graph WHERE node_type = 'node'"
    ).fetchall()
    edges = con.execute(
        "SELECT id, source, target, label, gmif_level, gmif_validation_type, "
        "gmif_extraction_confidence, gmif_validation_confidence "
        "FROM memory_graph WHERE node_type = 'edge'"
    ).fetchall()
    con.close()
    assert nodes == [] and edges == []


def test_a_migrated_graph_reports_the_production_defaults(db):
    """Defaults must match prod, or old and new rows read back differently."""
    mg.init_db()
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO memory_graph (node_key, node_type, label, touch_count,"
        " created_at, updated_at)"
        " VALUES ('n', 'node', 'x', 1, '2026-01-01', '2026-01-01')"
    )
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM memory_graph").fetchone()
    con.close()
    assert row["gmif_extraction_confidence"] == 0.0
    assert row["gmif_validation_confidence"] == 0.0
    assert row["node_gmif_confidence"] == 0.0
    assert row["gmif_level"] is None
