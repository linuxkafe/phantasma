"""A migration that fails must leave nothing behind.

Measured 2026-10-03, on production, first run of scripts/merge_memory_stores.py:

    inserted 25 memories
    destination now: memories=53
      all 50 source memories are present in the destination
      FAILED: 1566 graph node keys from memory.db are missing
      edge:madrugada solar|tumultos das silagens
      edge:desigualdade social|efeitos sociais
    -> exit 1

Two things wrong, and the first one is the dangerous one.

The 25 memories were COMMITTED. The verification ran after d.commit(), so it
could return 1 while the rows stayed -- a merge reported as failed and applied
anyway. The backups were the only way back.

The verification was also wrong. It unioned the source keys with dest_keys,
which had been read BEFORE the write, then compared the result against the
destination read AFTER it. So it demanded that 1566 keys the script never
touched had survived -- keys that belong to brain.db, not to memory.db -- and
then printed them while blaming memory.db. It compared the destination against
a stale snapshot of itself, on a run that had copied nothing into the graph.

These tests run against copies in tmp_path. A test that used the real
brain.db would either mutate production or have to be read-only, and a
read-only test cannot prove the rollback -- the rollback is the thing.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "merge_memory_stores.py")


def _module():
    spec = importlib.util.spec_from_file_location("merge_stores", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["merge_stores"] = mod
    spec.loader.exec_module(mod)
    return mod


def _store(path, memories, graph=()):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            text TEXT
        );
        CREATE TABLE memory_graph (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_key TEXT,
            node_type TEXT NOT NULL,
            label TEXT,
            source TEXT,
            target TEXT,
            affinity REAL DEFAULT 0.0,
            weight REAL DEFAULT 1.0,
            touch_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO memories (timestamp, text) VALUES (?,?)", memories
    )
    con.executemany(
        "INSERT INTO memory_graph (node_key, node_type, label, source, target,"
        " affinity, created_at, updated_at) VALUES (?,?,?,?,?,?,'t','t')",
        graph,
    )
    con.commit()
    con.close()
    return path


def _run(monkeypatch, argv):
    mod = _module()
    monkeypatch.setattr(sys, "argv", ["merge_memory_stores.py", *argv])
    return mod.main()


# --- the defect that mattered: a failed merge must not persist -------------

def test_a_failed_verification_rolls_the_memories_back(monkeypatch, tmp_path):
    """25 rows committed, exit 1, merge half-applied. That was the bug.

    The rollback has to cover the verification, not just the writes. Which
    verification failed does not matter -- any of them must leave the
    destination untouched, so the failure is simulated rather than engineered
    (engineering it needed a second connection to a database with an open write
    transaction, which is "database is locked" and a different test).
    """
    dest = _store(tmp_path / "brain.db", [("t1", "existing")])
    src = _store(
        tmp_path / "memory.db",
        [("t2", "nova"), ("t3", "outra")],
        [("node:x", "node", "X", None, None, 1.0)],
    )

    mod = _module()
    monkeypatch.setattr(mod, "_verify", lambda d, s, source_path: False)
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(dest), "--src", str(src)])
    rc = mod.main()
    assert rc == 1, f"esperava falha, veio {rc}"

    con = sqlite3.connect(dest)
    texts = {r[0] for r in con.execute("SELECT text FROM memories")}
    keys = {r[0] for r in con.execute("SELECT node_key FROM memory_graph")}
    con.close()
    assert texts == {"existing"}, (
        f"as memórias sobreviveram a uma migração que reportou falha: {texts}. "
        f"Um merge que falha tem de não deixar nada."
    )
    assert "node:x" not in keys, f"o grafo também sobreviveu: {keys}"


def test_an_exception_also_rolls_back(monkeypatch, tmp_path):
    """The other half of the same contract: a crash mid-write leaves nothing."""
    dest = _store(tmp_path / "b.db", [("t0", "a")])
    src = _store(tmp_path / "m.db", [("t1", "b")])

    mod = _module()
    real = mod._verify

    def boom(d, s, source_path):
        raise RuntimeError("algo partiu a meio da escrita")

    monkeypatch.setattr(mod, "_verify", boom)
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(dest), "--src", str(src)])
    with pytest.raises(RuntimeError):
        mod.main()

    con = sqlite3.connect(dest)
    texts = {r[0] for r in con.execute("SELECT text FROM memories")}
    con.close()
    assert texts == {"a"}, f"restou algo de uma escrita que partiu: {texts}"
    assert real is not None


def test_verify_does_not_compare_the_destination_against_itself(tmp_path):
    """The 1566 phantom keys.

    brain.db holds 1566 graph rows that memory.db never had. If the check
    demands they survive *as part of the source*, every run fails on keys the
    script did not touch -- and blames the wrong file for them.
    """
    mod = _module()
    # Everything the source has is already in the destination, so the only
    # thing under test is whether brain's OWN keys are demanded as if they came
    # from memory.db. A genuinely missing key fails correctly, which is a
    # different test.
    dest = _store(
        tmp_path / "b.db",
        [("t1", "a")],
        [
            *[(f"node:brain{i}", "node", f"B{i}", None, None, 1.0) for i in range(50)],
            ("node:src", "node", "S", None, None, 1.0),
        ],
    )
    src = _store(
        tmp_path / "m.db",
        [("t1", "a")],
        [("node:src", "node", "S", None, None, 1.0)],
    )
    d = sqlite3.connect(dest)
    s = sqlite3.connect(src)
    d.row_factory = s.row_factory = sqlite3.Row
    assert mod._verify(d, s, src) is True, (
        "a verificação falhou com 50 chaves que já estavam no destino antes de "
        "qualquer escrita -- a mesma comparação que reportou 1566 em produção"
    )
    d.close()
    s.close()


# --- and the merge itself ---------------------------------------------------

def test_duplicate_rows_in_the_source_are_copied_once(monkeypatch, tmp_path):
    """One blob stored six times must not arrive six times.

    Measured: memory.db holds a JSON blob six times and a sentence three times,
    each write with its own timestamp, so all 61 rows have a distinct
    (text, timestamp). A dedup on that pair admits every copy.
    """
    dest = _store(tmp_path / "b.db", [("t0", "ja existe")])
    src = _store(
        tmp_path / "m.db",
        [
            ("t1", "igual"),
            ("t2", "igual"),
            ("t3", "igual"),
            ("t4", "igual"),
            ("t5", "igual"),
            ("t6", "igual"),
            ("t7", "outra"),
        ],
    )
    assert _run(monkeypatch, ["--db", str(dest), "--src", str(src)]) == 0
    con = sqlite3.connect(dest)
    rows = [r[0] for r in con.execute("SELECT text FROM memories ORDER BY text")]
    con.close()
    assert rows == ["igual", "ja existe", "outra"], rows


def test_rerunning_is_a_no_op(monkeypatch, tmp_path):
    """A second run must add nothing, or it gets run twice on a bad night."""
    dest = _store(tmp_path / "b.db", [("t0", "uma")])
    src = _store(tmp_path / "m.db", [("t1", "duas")], [
        ("node:n", "node", "N", None, None, 1.0),
    ])
    argv = ["--db", str(dest), "--src", str(src)]
    assert _run(monkeypatch, argv) == 0
    con = sqlite3.connect(dest)
    after_first = con.execute("SELECT count(*) FROM memories").fetchone()[0]
    con.close()
    assert _run(monkeypatch, argv) == 0
    con = sqlite3.connect(dest)
    after_second = con.execute("SELECT count(*) FROM memories").fetchone()[0]
    con.close()
    assert after_first == after_second == 2, (after_first, after_second)


def test_a_backup_exists_for_both_files(monkeypatch, tmp_path):
    """The only way back after a bad merge, so it must be unconditional."""
    dest = _store(tmp_path / "b.db", [("t0", "a")])
    src = _store(tmp_path / "m.db", [("t1", "b")])
    assert _run(monkeypatch, ["--db", str(dest), "--src", str(src)]) == 0
    backups = [p.name for p in tmp_path.iterdir() if ".bak-" in p.name]
    assert len(backups) == 2, backups


def test_check_writes_nothing(monkeypatch, tmp_path):
    dest = _store(tmp_path / "b.db", [("t0", "a")])
    src = _store(tmp_path / "m.db", [("t1", "b")])
    before = dest.read_bytes()
    assert _run(monkeypatch, ["--check", "--db", str(dest), "--src", str(src)]) == 0
    assert dest.read_bytes() == before
    assert not [p for p in tmp_path.iterdir() if ".bak-" in p.name]


def test_it_reports_the_graph_by_type_rather_than_as_node_count(tmp_path):
    """200 nodes, 1306 edges, 60 markers -- not "1566 nodes".

    Reporting count(*) under the label "nodes" is how the owner was told the
    graph had 1566 nodes when it had 200.
    """
    mod = _module()
    path = _store(
        tmp_path / "g.db",
        [],
        [
            *[(f"n{i}", "node", f"N{i}", None, None, 1.0) for i in range(3)],
            *[(f"e{i}", "edge", f"E{i}", "a", "b", 0.0) for i in range(9)],
            *[(f"m{i}", "marker", f"M{i}", None, None, 0.0) for i in range(2)],
        ],
    )
    con = sqlite3.connect(path)
    breakdown = mod.graph_breakdown(con)
    con.close()
    assert breakdown == "edge=9  marker=2  node=3", breakdown


def test_missing_file_is_refused_not_created(monkeypatch, tmp_path):
    """A typo in a path must not create an empty database and call it merged."""
    mod = _module()
    monkeypatch.setattr(
        sys,
        "argv",
        ["x", "--db", str(tmp_path / "nope.db"), "--src", str(tmp_path / "no2.db")],
    )
    assert mod.main() == 2, "uma loja em falta tem de ser recusada"
    assert not (tmp_path / "nope.db").exists()


def test_a_bare_where_on_a_text_column_finds_nothing(tmp_path):
    """`WHERE node_key` returns 0 rows for text keys. Not a subtle one.

    SQLite coerces a bare `WHERE column` to NUMERIC. Against a TEXT column every
    non-numeric string becomes 0 and drops out of the sequence, so the filter
    matches nothing. Measured on production memory.db:

        SELECT node_key FROM memory_graph WHERE node_key        -> 0
        SELECT node_key FROM memory_graph                      -> 3

    The migration's own verification reported "all 0 source graph node keys are
    present" -- a green check that had verified nothing at all, and which would
    have stayed green if every node in memory.db had been lost. The same filter
    decided which nodes to copy.

    Asserted here so the predicate cannot go back to a bare column name.
    """
    mod = _module()
    assert "node_key IS NOT NULL" in mod._SQL_NONEMPTY, (
        f"_SQL_NONEMPTY volta a ser um nome de coluna nu: {mod._SQL_NONEMPTY!r}. "
        f"Em SQLite isso devolve 0 linhas para texto."
    )

    path = _store(
        tmp_path / "nonempty_probe.db",
        [],
        [
            ("node:a", "node", "A", None, None, 1.0),
            ("node:b", "node", "B", None, None, 1.0),
        ],
    )
    con = sqlite3.connect(path)
    try:
        bare = con.execute("SELECT count(*) FROM memory_graph WHERE node_key").fetchone()[0]
        explicit = con.execute(
            "SELECT count(*) FROM memory_graph WHERE " + mod._SQL_NONEMPTY
        ).fetchone()[0]
    finally:
        con.close()
    assert bare == 0, "o SQLite deixou de se comportar assim; reve o teste"
    assert explicit == 2, explicit


def test_an_edge_already_present_by_key_is_not_offered_as_work(monkeypatch, tmp_path):
    """--check said "1 graph edge to add" on every run, forever.

    The one edge memory.db holds is in brain.db already, under the same
    node_key and the same label, but with a different target:

        node_key  edge:capitalismo tardio|depende de plataformas digitais  (both)
        label     Capitalismo Tardio -> Depende de Plataformas Digitais    (both)
        target    'Depende de Plataformas Digitais'   memory.db, affinity 0.0
                  'Plataformas Digitais'               brain.db,  affinity 0.15

    Edges were matched on (label, source, target), so the diverging target made
    it look absent; the insert then hit the unique key and a bare
    `except: pass` made it disappear from the count. The report was wrong in
    both directions at once -- work offered, work not done -- and it said so on
    every single run, which is exactly how a report stops being read.

    Reconciling by node_key. brain.db's row wins: its affinity is the larger and
    its target is what the rest of the graph points at. Overwriting a production
    graph edge with a zero-affinity copy is not this script's call.
    """
    dest = _store(
        tmp_path / "b.db",
        [("t1", "a")],
        [
            (
                "edge:capitalismo tardio|depende de plataformas digitais",
                "edge",
                "Capitalismo Tardio -> Depende de Plataformas Digitais",
                "Capitalismo Tardio",
                "Plataformas Digitais",
                0.15,
            )
        ],
    )
    src = _store(
        tmp_path / "m.db",
        [("t1", "a")],
        [
            (
                "edge:capitalismo tardio|depende de plataformas digitais",
                "edge",
                "Capitalismo Tardio -> Depende de Plataformas Digitais",
                "Capitalismo Tardio",
                "Depende de Plataformas Digitais",
                0.0,
            )
        ],
    )

    out = _stdout_of(
        lambda: _run(monkeypatch, ["--check", "--db", str(dest), "--src", str(src)])
    )
    assert "0 graph edges to add" in out, (
        f"a aresta continua a ser oferecida como trabalho:\n{out}"
    )

    assert _run(monkeypatch, ["--db", str(dest), "--src", str(src)]) == 0
    con = sqlite3.connect(dest)
    rows = con.execute(
        "SELECT target, affinity FROM memory_graph WHERE node_type='edge'"
    ).fetchall()
    con.close()
    assert rows == [("Plataformas Digitais", 0.15)], (
        f"a versão do brain.db foi sobrescrita: {rows}. A afinidade 0.15 é a "
        f"que o resto do grafo usa."
    )


def _stdout_of(fn) -> str:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue()
