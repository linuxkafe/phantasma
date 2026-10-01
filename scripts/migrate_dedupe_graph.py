#!/usr/bin/env python3
"""One-time convergence pass over brain.db: merge duplicate edges, reclassify
indexing markers, and drop exactly-duplicated memories.

Three defects were measured in production:

1. Co-occurrence edges were keyed in the order the tags appeared in the row,
   so the same unordered pair produced two rows ("edge:a|b" and "edge:b|a").
   11 such pairs existed. The code now writes ``concept_edge_key``; this pass
   brings the existing rows to that same canonical key and merges the pair
   (touch_count summed, weight maxed, evidence unioned).
2. The "already indexed" markers ("memory:<id>:<hash>") were stored as
   node_type='node', so they rendered as concepts and inflated the node count
   (47 phantom nodes). They become node_type='marker'.
3. Three memory texts held 14 rows between them, and nothing compared rows
   apart in the table. Exact duplicates are archived (memories_purged) and
   deleted, keeping the earliest id.

Only edges classified_by='materialize_memories' are re-keyed: those are the
undirected co-occurrence edges. Directed/edited edges written by other paths
are left exactly as they are.

Usage:
    python scripts/migrate_dedupe_graph.py --db data/brain.db --dry-run
    python scripts/migrate_dedupe_graph.py --db data/brain.db --apply

Run with the tree's own venv: ./venv/bin/python
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.brain.memory_graph import concept_edge_key  # noqa: E402


def _merge_chunks(*values: str | None) -> str:
    """Union of gmif_source_chunks JSON lists, order-preserving."""
    out: list = []
    for v in values:
        if not v:
            continue
        try:
            items = json.loads(v)
        except (TypeError, ValueError):
            continue
        if isinstance(items, dict):
            items = [items]
        for it in items:
            if it not in out:
                out.append(it)
    return json.dumps(out)


def merge_edges(conn: sqlite3.Connection, dry: bool) -> tuple[int, int]:
    """Canonicalise the materialiser's undirected edges.

    Returns (rows_merged, rows_deleted).
    """
    conn.row_factory = sqlite3.Row
    all_edges = conn.execute(
        "SELECT id, node_key, source, target, weight, touch_count, gmif_source_chunks, "
        "gmif_classified_by FROM memory_graph WHERE node_type = 'edge'"
    ).fetchall()
    key_owner = {r["node_key"]: r["id"] for r in all_edges}

    groups: dict[str, list[sqlite3.Row]] = {}
    for r in all_edges:
        if r["gmif_classified_by"] != "materialize_memories":
            continue
        groups.setdefault(concept_edge_key(r["source"] or "", r["target"] or ""), []).append(r)

    merged = deleted = 0
    for ck, rows in groups.items():
        owner = key_owner.get(ck)
        target = next((r for r in rows if r["id"] == owner), None)
        if target is None:
            target = min(rows, key=lambda r: r["id"])
        victims = [r for r in rows if r["id"] != target["id"]]

        touch = sum(int(r["touch_count"] or 0) for r in rows)
        weight = max(float(r["weight"] or 1.0) for r in rows)
        chunks = _merge_chunks(*[r["gmif_source_chunks"] for r in rows])

        needs_rename = target["node_key"] != ck and owner is None
        if not victims and touch == int(target["touch_count"] or 0) and not needs_rename:
            continue

        label = f"{target['node_key']}"
        action = "rekey" if needs_rename else ""
        if victims:
            action = (action + "+merge").lstrip("+")
        print(f"  {ck}: {len(rows)} rows -> 1 ({action or 'noop'}) [{label}]")

        if dry:
            merged += 1
            deleted += len(victims)
            continue

        if victims:
            marks = ",".join("?" * len(victims))
            conn.execute(
                f"DELETE FROM memory_graph WHERE id IN ({marks})",
                [r["id"] for r in victims],
            )
        if needs_rename:
            conn.execute(
                "UPDATE memory_graph SET node_key = ? WHERE id = ?",
                (ck, target["id"]),
            )
        conn.execute(
            "UPDATE memory_graph SET weight = ?, touch_count = ?, gmif_source_chunks = ? "
            "WHERE id = ?",
            (weight, touch, chunks, target["id"]),
        )
        merged += 1
        deleted += len(victims)
    return merged, deleted


def reclassify_markers(conn: sqlite3.Connection, dry: bool) -> int:
    n = conn.execute(
        "SELECT COUNT(*) FROM memory_graph WHERE node_type = 'node' "
        "AND node_key LIKE 'memory:%'"
    ).fetchone()[0]
    if n and not dry:
        conn.execute(
            "UPDATE memory_graph SET node_type = 'marker' "
            "WHERE node_type = 'node' AND node_key LIKE 'memory:%'"
        )
    return n


def dedupe_memories(conn: sqlite3.Connection, dry: bool) -> int:
    rows = conn.execute("SELECT id, timestamp, text FROM memories ORDER BY id").fetchall()
    seen: set[str] = set()
    dupes = []
    for rid, ts, text in rows:
        key = (text or "").strip()
        if not key:
            continue
        if key in seen:
            dupes.append((rid, ts, text))
        else:
            seen.add(key)
    if not dupes or dry:
        return len(dupes)

    conn.execute(
        "CREATE TABLE IF NOT EXISTS memories_purged ("
        " id INTEGER PRIMARY KEY, timestamp DATETIME, text TEXT,"
        " purged_at TEXT NOT NULL, reason TEXT NOT NULL)"
    )
    purged_at = datetime.now().isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO memories_purged (id, timestamp, text, purged_at, reason)"
        " VALUES (?,?,?,?,?)",
        [(r[0], r[1], r[2], purged_at, "dream_dedupe") for r in dupes],
    )
    marks = ",".join("?" * len(dupes))
    conn.execute(f"DELETE FROM memories WHERE id IN ({marks})", [d[0] for d in dupes])
    return len(dupes)


def _counts(conn: sqlite3.Connection) -> dict:
    return {
        "nodes": conn.execute(
            "SELECT COUNT(*) FROM memory_graph WHERE node_type='node'"
        ).fetchone()[0],
        "edges": conn.execute(
            "SELECT COUNT(*) FROM memory_graph WHERE node_type='edge'"
        ).fetchone()[0],
        "markers": conn.execute(
            "SELECT COUNT(*) FROM memory_graph WHERE node_type='marker'"
        ).fetchone()[0],
        "memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
    }


def migrate(db_path: Path, dry: bool) -> int:
    if not db_path.exists():
        print(f"ERRO: {db_path} não existe.")
        return 1

    if not dry:
        backup = db_path.with_suffix(f".db.bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(db_path, backup)
        print(f"Backup: {backup}")

    print(f"Alvo: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        before = _counts(conn)
        print(f"Antes: {before}")

        print("\nArestas (canonicalizar + fundir):")
        merged, deleted = merge_edges(conn, dry)

        print("\nMarcadores (node -> marker):")
        markers = reclassify_markers(conn, dry)
        print(f"  {markers} marcadores reclassificados")

        print("\nMemórias (remover duplicados exatos):")
        dupes = dedupe_memories(conn, dry)
        print(f"  {dupes} memórias duplicadas removidas")

        if not dry:
            conn.commit()
        after = _counts(conn)
        print(f"\nDepois: {after}")
        print(
            f"Resumo: {merged} grupos de arestas, {deleted} arestas removidas, "
            f"{markers} marcadores, {dupes} memórias."
        )
    finally:
        conn.close()

    print("\nDRY-RUN: nada foi escrito." if dry else "OK: migração aplicada.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.dry_run == a.apply:
        ap.error("escolhe --dry-run ou --apply")
    sys.exit(migrate(a.db, dry=a.dry_run))
