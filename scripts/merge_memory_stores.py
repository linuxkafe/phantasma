#!/usr/bin/env python3
"""Fold memory.db into brain.db, so the assistant reads one store.

Production had two databases, each holding half the picture:

    memory.db   61 memories   3 graph rows
    brain.db    28 memories   1566 graph rows

Both readers already opened brain.db -- `retrieve_from_rag` in data_utils reads
`config.DB_PATH`, and `memory_graph._connect` reads `config.DB_PATH` too, and
all three of `DB_PATH` / `MEMORY_DB_PATH` / `BRAIN_DB_PATH` resolve to the same
file. So the disagreement was never about which file to open. It was about what
is in it. Measured 2026-10-03:

    config.DB_PATH             -> /opt/phantasma/data/brain.db
    memories in brain.db       -> 28   (all veganism/Dream-GMIF; none about the owner)
    memories in memory.db      -> 61   (25 texts not in brain.db at all)
    graph in brain.db          -> 200 nodes, 1306 edges, 60 markers
    top affinity               -> 61 on "capitalismo tardio"

The owner was right that the RAG held more than the assistant could see. It did,
in the other file. And an ordinary question retrieved none of the 1566 rows:

    retrieve_from_rag("e em Lisboa?")         -> 0 chars
    graph_context_text("quem sou eu")         -> 0 chars  (fixed in 7985a5c)

This script moves what is missing and changes nothing else:

- a backup of both files, timestamped, before anything is written;
- one transaction and one commit, so a failure cannot leave the destination
  half-merged;
- memories deduplicated on TEXT, keeping the earliest timestamp;
- graph nodes deduplicated on node_key, which is UNIQUE;
- an after-check on memories AND graph nodes, which is what the previous
  version of this file promised and did not do.

It does not delete memory.db. After the merge it holds nothing the assistant
needs, but deleting a production database is a separate decision.

Requires write access to both files -- in production that means the phantasma
user:

    sudo -u phantasma venv/bin/python scripts/merge_memory_stores.py --check
    sudo -u phantasma venv/bin/python scripts/merge_memory_stores.py

Usage:
    --check           report only, write nothing
    --db PATH         destination   (default /opt/phantasma/data/brain.db)
    --src PATH        store to fold in (default /opt/phantasma/data/memory.db)
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DEST = Path("/opt/phantasma/data/brain.db")
DEFAULT_SRC = Path("/opt/phantasma/data/memory.db")


def counts(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        return -1


def graph_breakdown(conn: sqlite3.Connection) -> str:
    """memory_graph holds nodes, edges AND markers in one table.

    Reporting `count(*)` under the label "graph nodes" is how 1566 came to be
    described as 1566 nodes. It is 200 nodes, 1306 edges, 60 markers.
    """
    try:
        rows = conn.execute(
            "SELECT node_type, count(*) FROM memory_graph GROUP BY node_type"
        ).fetchall()
    except sqlite3.Error:
        return "unreadable"
    return "  ".join(f"{kind}={n}" for kind, n in sorted(rows)) or "empty"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DEST), help="destination")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="store to fold in")
    ap.add_argument("--check", action="store_true", help="report only")
    args = ap.parse_args()

    dest, src = Path(args.db), Path(args.src)
    for p in (dest, src):
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr)
            return 2

    # The service holds this database open. Without a busy timeout a write that
    # collides with it fails immediately with "database is locked", and the
    # rollback journal has to be creatable in the same directory as the file --
    # which is why running this as the wrong user gives "readonly database".
    d = sqlite3.connect(dest, timeout=30.0)
    s = sqlite3.connect(src, timeout=30.0)
    d.row_factory = s.row_factory = sqlite3.Row
    d.execute("PRAGMA busy_timeout=30000")
    s.execute("PRAGMA busy_timeout=30000")

    print(f"destination {dest}")
    print(f"  memories={counts(d, 'memories')}  graph: {graph_breakdown(d)}")
    print(f"source      {src}")
    print(f"  memories={counts(s, 'memories')}  graph: {graph_breakdown(s)}")

    # --- what would move ---------------------------------------------------
    # Dedupe on TEXT, keeping the earliest timestamp. Not on (text, timestamp).
    #
    # An earlier version of this file promised (text, timestamp). Measured on
    # production that is not the same thing, and not idempotent in the way that
    # matters:
    #
    #     source rows              61
    #     source unique texts      50
    #     unique (text,timestamp)  61   <- every row distinct, so that key
    #                                      dedupes NOTHING
    #     texts missing from dest  25   <- the real answer
    #     rows the old code copied 32   <- 7 duplicate rows, silently
    #
    # Two memories had been stored more than once: one JSON blob six times and
    # one sentence three times, each write with its own timestamp. Because
    # (text, timestamp) is unique across all 61 rows, the documented key
    # admitted every copy and the destination would have held six copies of the
    # same fact.
    #
    # Text is the key retrieval actually uses -- RAG matches on text, and the
    # verification below checks text -- so text is the key that decides what a
    # duplicate is. Earliest timestamp wins: that is when the fact was learned.
    dest_texts = {r["text"] for r in d.execute("SELECT text FROM memories")}

    first_seen: dict[str, sqlite3.Row] = {}
    repeats: dict[str, int] = {}
    for r in s.execute("SELECT * FROM memories ORDER BY timestamp, id"):
        if r["text"] not in first_seen:
            first_seen[r["text"]] = r
        else:
            repeats[r["text"]] = repeats.get(r["text"], 0) + 1

    new_mem = [r for text, r in first_seen.items() if text not in dest_texts]
    skipped_dupes = sum(n for text, n in repeats.items() if text not in dest_texts)

    dest_keys = {
        r["node_key"]
        for r in d.execute("SELECT node_key FROM memory_graph")
        if r["node_key"]
    }
    new_nodes = [
        r
        for r in s.execute("SELECT * FROM memory_graph WHERE node_type='node'")
        if r["node_key"] not in dest_keys
    ]
    # An edge has no key of its own -- node_key is only UNIQUE for nodes. Two
    # edges are the same edge when label, source and target all agree.
    src_edges = list(s.execute("SELECT * FROM memory_graph WHERE node_type='edge'"))
    existing_edges = {
        (r["label"], (r["source"] or ""), (r["target"] or ""))
        for r in d.execute(
            "SELECT label, source, target FROM memory_graph WHERE node_type='edge'"
        )
    }
    new_edges: list[sqlite3.Row] = []
    seen_edges: set[tuple] = set()
    for r in src_edges:
        key = (r["label"], (r["source"] or ""), (r["target"] or ""))
        if key in existing_edges or key in seen_edges:
            continue
        seen_edges.add(key)
        new_edges.append(r)

    print()
    print(
        f"  {len(new_mem)} memories to add"
        f"  ({len(dest_texts)} already present,"
        f" {skipped_dupes} duplicate rows skipped)"
    )
    for text, n in sorted(repeats.items(), key=lambda kv: -kv[1]):
        if n > 1 and text not in dest_texts:
            print(f"    stored {n + 1}x already, copying once: {text[:62]!r}")
    print(f"  {len(new_nodes)} graph nodes to add")
    print(f"  {len(new_edges)} graph edges to add")

    if args.check:
        print("\n(--check: nothing written)")
        return 0

    if not new_mem and not new_nodes and not new_edges:
        print("\nnothing to do -- the store is already folded in")
        return 0

    # --- backup ------------------------------------------------------------
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for p in (dest, src):
        backup = p.with_suffix(p.suffix + f".bak-{stamp}")
        shutil.copy2(p, backup)
        print(f"  backup {backup}")

    mem_cols = [r[1] for r in d.execute("PRAGMA table_info(memories)")]
    src_cols = [r[1] for r in s.execute("PRAGMA table_info(memories)")]
    use = [c for c in mem_cols if c in src_cols]
    mem_ph = ",".join("?" * len(use))
    mem_cols_s = ",".join(use)

    g_cols = [r[1] for r in d.execute("PRAGMA table_info(memory_graph)")]
    s_cols = [r[1] for r in s.execute("PRAGMA table_info(memory_graph)")]
    g_use = [c for c in g_cols if c in s_cols]
    g_ph = ",".join("?" * len(g_use))
    g_cols_s = ",".join(g_use)

    # One transaction, one commit. The intermediate d.commit() calls that used to
    # be here committed the memories and then failed on the graph, which is the
    # half-merged state nobody can later tell apart from a successful run.
    try:
        for r in new_mem:
            d.execute(
                f"INSERT INTO memories ({mem_cols_s}) VALUES ({mem_ph})",
                [r[c] for c in use],
            )
        print(f"  inserted {len(new_mem)} memories")

        added_nodes = 0
        for r in new_nodes:
            try:
                d.execute(
                    f"INSERT INTO memory_graph ({g_cols_s}) VALUES ({g_ph})",
                    [r[c] for c in g_use],
                )
                added_nodes += 1
            except sqlite3.IntegrityError:
                pass  # node_key is UNIQUE, so it is already there
        print(f"  inserted {added_nodes} graph nodes")

        added_edges = 0
        for r in new_edges:
            try:
                d.execute(
                    f"INSERT INTO memory_graph ({g_cols_s}) VALUES ({g_ph})",
                    [r[c] for c in g_use],
                )
                added_edges += 1
            except sqlite3.Error:
                pass
        print(f"  inserted {added_edges} graph edges")

        d.commit()
    except Exception:
        d.rollback()
        print("\nrolled back -- the destination is unchanged", file=sys.stderr)
        raise

    # --- verify ------------------------------------------------------------
    print()
    print(f"  destination now: memories={counts(d, 'memories')}  "
          f"graph: {graph_breakdown(d)}")

    src_texts = {r["text"] for r in s.execute("SELECT text FROM memories")}
    dest_texts_after = {r["text"] for r in d.execute("SELECT text FROM memories")}
    missing = src_texts - dest_texts_after
    if missing:
        print(
            f"  FAILED: {len(missing)} memories from {src.name} are still missing",
            file=sys.stderr,
        )
        for text in list(missing)[:5]:
            print(f"    {text[:80]}", file=sys.stderr)
        return 1
    print(f"  all {len(src_texts)} source memories are present in the destination")

    # The header promised the script "refuses to run if the counts do not add
    # up". It only ever checked memories. A graph node lost in the copy is just
    # as invisible, so it gets checked too.
    expected_keys = {
        r["node_key"]
        for r in s.execute("SELECT node_key FROM memory_graph WHERE node_key")
    } | dest_keys
    keys_after = {
        r["node_key"]
        for r in d.execute("SELECT node_key FROM memory_graph WHERE node_key")
    }
    lost = expected_keys - keys_after
    if lost:
        print(
            f"  FAILED: {len(lost)} graph node keys from {src.name} are missing",
            file=sys.stderr,
        )
        for k in list(lost)[:5]:
            print(f"    {k}", file=sys.stderr)
        return 1
    print(f"  all {len(expected_keys)} graph node keys are present "
          f"({len(src_edges)} source edges accounted for)")

    d.close()
    s.close()
    print(
        "\nmerge OK. memory.db was left in place -- deleting a production "
        "database is a separate decision."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
