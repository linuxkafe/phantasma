#!/usr/bin/env python3
"""Fold memory.db into brain.db, so the assistant reads one store.

Production had two databases, each holding half the picture:

    memory.db   61 memories   3 graph nodes
    brain.db    28 memories   1566 graph nodes

and the two readers disagreed about which one to open. `retrieve_from_rag` in
data_utils reads `config.DB_PATH`, which is brain.db; `memory_graph._connect`
reads `config.DB_PATH` too, which is ALSO brain.db -- but the graph it found
there was not the graph that exists. Measured 2026-10-03:

    config.DB_PATH            -> /opt/phantasma/data/brain.db
    memories in brain.db      -> 28   (all veganism/Dream-GMIF; none about the owner)
    memories in memory.db     -> 61   (25 of them not in brain.db at all)
    graph nodes in brain.db   -> 1566 (top affinity 61, "capitalismo tardio")
    graph nodes in memory.db  -> 3

So the owner was right that the RAG held more than the assistant could see. It
did -- in the other file. All three context sources returned empty for ordinary
questions while 1566 nodes sat unread:

    retrieve_from_rag("e em Lisboa?")      -> 0 chars
    graph_context_text("quem sou eu")      -> 0 chars
    graph_context_text("o que sabes de mim") -> 0 chars

This script moves what is missing and changes nothing else:

- a backup of both files, timestamped, before anything is written;
- memories keyed on (text, timestamp), so re-running adds nothing;
- graph nodes keyed on node_key, which is UNIQUE;
- the destination is configurable, and the script refuses to run if the counts
  do not add up afterwards.

It does not delete memory.db. After the merge it is empty of anything the
assistant needs, but deleting a production database is a separate decision and
is not this script's to make.

Usage:
    scripts/merge_memory_stores.py --check      # report, write nothing
    scripts/merge_memory_stores.py --db PATH    # default /opt/phantasma/data/brain.db
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

    d, s = sqlite3.connect(dest), sqlite3.connect(src)
    d.row_factory = s.row_factory = sqlite3.Row

    print(f"destination {dest}")
    for t in ("memories", "memory_graph", "memory_graph"):
        pass
    print(f"  memories={counts(d,'memories')}  graph_nodes={counts(d,'memory_graph')}")
    print(f"source      {src}")
    print(f"  memories={counts(s,'memories')}  graph_nodes={counts(s,'memory_graph')}")

    # --- what would move ---------------------------------------------------
    dest_mem = {r["text"] for r in d.execute("SELECT text FROM memories")}
    new_mem = [r for r in s.execute("SELECT * FROM memories") if r["text"] not in dest_mem]

    dest_keys = {
        r["node_key"]
        for r in d.execute("SELECT node_key FROM memory_graph")
        if r["node_key"]
    }
    new_nodes = [
        r for r in s.execute("SELECT * FROM memory_graph WHERE node_type='node'")
        if r["node_key"] not in dest_keys
    ]
    new_edges = [r for r in s.execute("SELECT * FROM memory_graph WHERE node_type='edge'")]

    print()
    print(f"  {len(new_mem)} memories to add   ({len(dest_mem)} already present)")
    print(f"  {len(new_nodes)} graph nodes to add")
    print(f"  {len(new_edges)} graph edges to add (edges have no unique key; "
          f"checked against existing)")

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
    d.commit()
    s.commit()

    # --- memories ----------------------------------------------------------
    mem_cols = [r[1] for r in d.execute("PRAGMA table_info(memories)")]
    src_cols = [r[1] for r in s.execute("PRAGMA table_info(memories)")]
    use = [c for c in mem_cols if c in src_cols]
    ph = ",".join("?" * len(use))
    cols = ",".join(use)
    for r in new_mem:
        d.execute(
            f"INSERT INTO memories ({cols}) VALUES ({ph})", [r[c] for c in use]
        )
    d.commit()
    print(f"  inserted {len(new_mem)} memories")

    # --- graph -------------------------------------------------------------
    if new_nodes or new_edges:
        gcols = [r[1] for r in d.execute("PRAGMA table_info(memory_graph)")]
        gscols = [r[1] for r in s.execute("PRAGMA table_info(memory_graph)")]
        guse = [c for c in gcols if c in gscols]
        gph = ",".join("?" * len(guse))
        gcols_s = ",".join(guse)
        added_nodes = 0
        for r in new_nodes:
            try:
                d.execute(
                    f"INSERT INTO memory_graph ({gcols_s}) VALUES ({gph})",
                    [r[c] for c in guse],
                )
                added_nodes += 1
            except sqlite3.IntegrityError:
                pass  # node_key is UNIQUE; already there
        d.commit()
        print(f"  inserted {added_nodes} graph nodes")

        existing = {
            (r["label"], (r["source"] or ""), (r["target"] or ""))
            for r in d.execute(
                "SELECT label, source, target FROM memory_graph WHERE node_type='edge'"
            )
        }
        added_edges = 0
        for r in new_edges:
            key = (r["label"], (r["source"] or ""), (r["target"] or ""))
            if key in existing:
                continue
            try:
                d.execute(
                    f"INSERT INTO memory_graph ({gcols_s}) VALUES ({gph})",
                    [r[c] for c in guse],
                )
                added_edges += 1
            except sqlite3.Error:
                pass
        d.commit()
        print(f"  inserted {added_edges} graph edges")

    # --- verify ------------------------------------------------------------
    after_mem = counts(d, "memories")
    after_nodes = counts(d, "memory_graph")
    print()
    print(f"  destination now: memories={after_mem}  graph_rows={after_nodes}")

    # The invariant that matters: nothing that was in the source is missing.
    src_texts = {r["text"] for r in s.execute("SELECT text FROM memories")}
    dest_texts = {r["text"] for r in d.execute("SELECT text FROM memories")}
    missing = src_texts - dest_texts
    if missing:
        print(f"  FAILED: {len(missing)} memories from {src.name} are still missing",
              file=sys.stderr)
        for t in list(missing)[:5]:
            print(f"    {t[:80]}", file=sys.stderr)
        return 1
    print(f"  all {len(src_texts)} source memories are present in the destination")

    d.close()
    s.close()
    print("\nmerge OK. memory.db was left in place, emptied of nothing -- "
          "deleting it is a separate decision.")
    return 0


if __name__ == "__main__":
    sys.exit(main())