"""Reactions that named no node, waiting for the sleep cycle to place them.

A reaction judges one reply. `find_node_for_text` resolves which node that
reply is about, and when it finds nothing the graph is deliberately left alone
and the report says `no_match` -- an honest miss beats a confident wrong node.

That left the reply stranded. The front end offered a button, "Guardar esta
resposta como nó do grafo", and the owner had to notice it and press it. So the
common case -- a good answer the graph has never heard of -- ended in nothing.

The alternative is not to create the node at the moment of the reaction. It is
to QUEUE the reply and let the sleep cycle decide where it belongs. Two reasons,
both of which the button did not have:

  * a reaction is a sentiment about one text; whether that text deserves a
    concept is a different judgement, and it is better made where the graph is
    already open -- with the other 185 nodes visible and SearXNG to check the
    claim, instead of from a chat bubble;
  * and it is not worth showing the owner at all. The button was the interface
    telling them to do the brain's housekeeping for it, on every miss.

So the queue is append-only and small: the reply text, the reward, when it
arrived, and what happened to it. The sleep cycle drains it in
`_place_unplaced_reactions`, promotes what the evidence supports, and discards
the rest with a reason. Nothing is written to the graph by a click.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS unplaced_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_text TEXT NOT NULL,
    emoji TEXT,
    reward REAL,
    source TEXT,
    actor TEXT,
    created_at REAL NOT NULL,
    outcome TEXT,
    node_key TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_unplaced_pending
    ON unplaced_reactions (outcome, id);
"""

# A reply longer than this is not a concept; it is a transcript. Queued
# truncated, and the length is kept so the sleep cycle can refuse it honestly
# instead of promoting the first sentence of a wall of text.
MAX_TEXT = 600


def ensure_schema(db_path: "str | Path | sqlite3.Connection") -> None:
    """Create the queue if absent. Safe to call on every reaction.

    Accepts an OPEN connection, like graph_edit.ensure_schema, so a reaction
    and its queue row commit together. A miss that is recorded in one place and
    queued in another is a miss that can be lost.
    """
    if isinstance(db_path, sqlite3.Connection):
        db_path.executescript(DDL)
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(DDL)
        conn.commit()
    finally:
        conn.close()


def enqueue(
    conn: sqlite3.Connection,
    message_text: str,
    emoji: Optional[str] = None,
    reward: Optional[float] = None,
    source: Optional[str] = None,
    actor: Optional[str] = None,
) -> Optional[int]:
    """Record a reply the graph could not place. Returns the row id.

    Returns None for text that is too long to be a concept, and says so in the
    reason rather than queueing something the sleep cycle can only discard.
    """
    text = (message_text or "").strip()
    if not text:
        return None
    if len(text) > MAX_TEXT:
        logger.info(
            "unplaced reaction not queued: %d chars, over the %d a concept can be",
            len(text),
            MAX_TEXT,
        )
        return None
    cur = conn.execute(
        "INSERT INTO unplaced_reactions"
        " (message_text, emoji, reward, source, actor, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (text, emoji, reward, source, actor, time.time()),
    )
    return cur.lastrowid


def pending(conn: sqlite3.Connection, limit: int = 20) -> List[Dict[str, Any]]:
    """The oldest unresolved rows, oldest first: a reply judged today is a
    reply about today's graph, and the graph changes underneath the queue."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM unplaced_reactions WHERE outcome IS NULL"
        " ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark(
    conn: sqlite3.Connection,
    row_id: int,
    outcome: str,
    node_key: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Resolve one queued row. `outcome` says what was decided.

    Kept as a plain string rather than an enum so a new reason does not need a
    migration, and so the history of what the brain refused to believe stays
    readable in the table itself.
    """
    conn.execute(
        "UPDATE unplaced_reactions SET outcome = ?, node_key = ?, note = ?"
        " WHERE id = ?",
        (outcome, node_key, (note or "")[:500] or None, row_id),
    )


def counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """How many are waiting and how many were kept, for the admin counters."""
    rows = conn.execute(
        "SELECT COALESCE(outcome, 'pending') AS outcome, COUNT(*) AS n"
        " FROM unplaced_reactions GROUP BY 1"
    ).fetchall()
    return {str(r["outcome"]): int(r["n"]) for r in rows}


def to_json(row: Dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, default=str)
