#!/usr/bin/env python3
"""One-time migration: merge the split dev stores into the unified brain.db.

Dev had `data/memory.db` (memories, memory_graph, topic_state, caches) and
`data/flybrain.db` (flybrain_state). Production has always used a single
`data/brain.db` for both, so dev was the outlier and the split is what made the
graph the writer builds invisible to the reader.

This copies both sources into the unified file. It never moves or deletes a
source file -- the originals stay where they are, so a mistake is recoverable by
deleting the destination and re-running.

Usage:
    python scripts/migrate_unified_brain_db.py --dry-run   # report only
    python scripts/migrate_unified_brain_db.py --apply     # do it

Run --apply with the tree's own venv: ./venv/bin/python
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "data" / "brain.db"
MEMORY_SRC = ROOT / "data" / "memory.db"
FLYBRAIN_SRC = ROOT / "data" / "flybrain.db"

# Copied from the dev sources. flybrain_state is a single-row blob store, so it
# is attached rather than iterated; the rest are plain rows.
TABLES = ("memories", "memory_graph", "topic_state", "cache", "response_cache", "app_settings")


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _copy_rows(src: sqlite3.Connection, dst: sqlite3.Connection, table: str, dry: bool) -> int:
    have_src = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )]
    if not have_src:
        return 0
    have_dst = [r[0] for r in dst.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )]
    if not have_dst:
        src_sql = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        verb = "criaria" if dry else "criou"
        print(f"  {table}: destino sem tabela — {verb} a partir do schema de origem")
        if not dry:
            dst.execute(src_sql)
    _src_cols = _columns(src, table)
    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0
    shared = [c for c in _src_cols if c in _columns(dst, table)]
    if not shared:
        return 0
    marks = ",".join("?" * len(shared))
    verb = "copiaria" if dry else "copiou"
    print(f"  {table}: {verb} {len(rows)} linhas ({', '.join(shared)})")
    if dry:
        return len(rows)
    dst.executemany(
        f"INSERT INTO {table} ({','.join(shared)}) VALUES ({marks})",
        [tuple(r[_src_cols.index(c)] for c in shared) for r in rows],
    )
    return len(rows)


def migrate(dry: bool) -> int:
    if not MEMORY_SRC.exists():
        print(f"ERRO: {MEMORY_SRC} não existe — nada para migrar.")
        return 1

    if DEST.exists() and not dry:
        backup = DEST.with_suffix(".db.bak")
        shutil.copy2(DEST, backup)
        print(f"Backup do destino: {backup.name}")

    print(f"origem memória : {MEMORY_SRC.name} ({MEMORY_SRC.stat().st_size} bytes)")
    print(f"origem flybrain: {FLYBRAIN_SRC.name if FLYBRAIN_SRC.exists() else '(ausente)'}")
    print(f"destino       : {DEST.name}")
    print()

    src = sqlite3.connect(MEMORY_SRC)
    src.row_factory = sqlite3.Row
    # The dry run must open the real destination, or it reports tables as
    # missing that init_db() already created and the report is a fiction.
    dst = sqlite3.connect(DEST)
    dst.row_factory = sqlite3.Row
    try:
        total = 0
        print("memórias e tabelas:")
        for table in TABLES:
            total += _copy_rows(src, dst, table, dry)

        if FLYBRAIN_SRC.exists():
            if not dst.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='flybrain_state'"
            ).fetchone():
                state_sql = sqlite3.connect(FLYBRAIN_SRC).execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='flybrain_state'"
                ).fetchone()[0]
                print(f"  flybrain_state: destino sem tabela — {'criaria' if dry else 'criou'}")
                dst.execute(state_sql)

            state = sqlite3.connect(FLYBRAIN_SRC).execute(
                "SELECT schema_version, data, updated_at FROM flybrain_state WHERE id=1"
            ).fetchone()
            if state:
                verb = "copiaria" if dry else "copiou"
                print(f"  flybrain_state: {verb} 1 linha "
                      f"(schema_version={state[0]}, {len(state[1])} bytes)")
                if not dry:
                    dst.execute(
                        "INSERT OR REPLACE INTO flybrain_state"
                        " (id, schema_version, data, updated_at) VALUES (1,?,?,?)",
                        (state[0], state[1], state[2]),
                    )
                total += 1

        if not dry:
            dst.commit()
            print()
            print("Destino:")
            for table in ("memories", "memory_graph", "flybrain_state", "topic_state"):
                n = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table}: {n}")
            gmif = dst.execute(
                "SELECT COUNT(*) FROM pragma_table_info('memory_graph') WHERE name LIKE '%gmif%'"
            ).fetchone()[0]
            print(f"  colunas GMIF em memory_graph: {gmif}")
    finally:
        src.close()
        dst.close()

    print()
    print("DRY-RUN: nada foi escrito." if dry else f"OK: {total} linhas escritas.")
    print("As origens não foram movidas nem apagadas.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.dry_run == a.apply:
        ap.error("escolhe --dry-run ou --apply")
    sys.exit(migrate(dry=a.dry_run))
