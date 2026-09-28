"""Memory graph — long-term topic graph with FlyBrain affinity weighting.

Persists a simple directed graph (topic nodes + associations) inside
``memory.db`` and merges it with FlyBrain's affective state: every skill_memory
save indexes its tags/edges, and every Discord reaction reward is applied to
the node of the *current* topic. Retrieval then returns the affinity-weighted
neighbourhood of the query instead of unscored text blobs.

This is the material bridge between the connectome (flybrain.db) and the
semantic store (memory.db): the graph is the shared landscape both read and
write.
"""

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

GRAPH_TABLE = "memory_graph"
TOPIC_TABLE = "topic_state"


def _connect() -> sqlite3.Connection:
    """Open a connection to memory.db (same DB as data_utils RAG store)."""
    db_path = Path(getattr(config, "DB_PATH", config.MEMORY_DB_PATH))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create graph and current-topic tables if not present (idempotent)."""
    with _connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {GRAPH_TABLE} (
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
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TOPIC_TABLE} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_key TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        logger.info("Memory graph initialized")


# ---------------------------------------------------------------------------
# Node / edge helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now().isoformat()


def upsert_node(label: str, affinity: float = 0.0) -> str:
    """Index a topic node (tag/concept); bumps touch_count on repeat."""
    label = str(label).strip()
    if not label:
        return ""
    key = f"node:{label.lower()}"
    now = _now()
    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO {GRAPH_TABLE}
                (node_key, node_type, label, affinity, touch_count,
                 created_at, updated_at)
            VALUES (?, 'node', ?, ?, 1, ?, ?)
            ON CONFLICT(node_key) DO UPDATE SET
                touch_count = touch_count + 1,
                updated_at = excluded.updated_at
            """,
            (key, label, affinity, now, now),
        )
        conn.commit()
    return key


def upsert_edge(source: str, target: str, weight: float = 1.0) -> str:
    """Index a directed association source -> target."""
    source = str(source).strip()
    target = str(target).strip()
    if not source or not target:
        return ""
    key = f"edge:{source.lower()}|{target.lower()}"
    now = _now()
    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO {GRAPH_TABLE}
                (node_key, node_type, label, source, target, weight,
                 touch_count, created_at, updated_at)
            VALUES (?, 'edge', ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(node_key) DO UPDATE SET
                weight = MAX(weight, excluded.weight),
                touch_count = touch_count + 1,
                updated_at = excluded.updated_at
            """,
            (key, f"{source} -> {target}", source, target, weight, now, now),
        )
        conn.commit()
    return key


def apply_reward(reward: float, topic_key: Optional[str] = None) -> Optional[str]:
    """Apply a FlyBrain reward to the current topic node (or a given key).

    Args:
        reward: signed reward from reaction feedback (e.g. +1.0 for 👍).
        topic_key: explicit node_key; defaults to the stored current topic.

    Returns:
        The node_key that was rewarded, or None if no current topic exists.
    """
    key = topic_key or get_current_topic()
    if not key:
        return None
    now = _now()
    with _connect() as conn:
        # Create the node if only referenced as a topic placeholder.
        conn.execute(
            f"""
            INSERT INTO {GRAPH_TABLE}
                (node_key, node_type, label, affinity, touch_count,
                 created_at, updated_at)
            VALUES (?, 'node', ?, ?, 1, ?, ?)
            ON CONFLICT(node_key) DO NOTHING
            """,
            (key, key.split(":", 1)[-1].title(), reward, now, now),
        )
        conn.execute(
            f"""
            UPDATE {GRAPH_TABLE}
            SET affinity = affinity + ?, updated_at = ?
            WHERE node_key = ?
            """,
            (reward, now, key),
        )
        conn.commit()
    logger.info(f"Reward {reward:+.1f} applied to topic node {key}")
    return key


# ---------------------------------------------------------------------------
# Current topic tracking
# ---------------------------------------------------------------------------


def find_node_for_text(text: str) -> Optional[dict]:
    """The STORED node whose label is named in `text`, or None.

    Read-only on purpose. A reaction is a judgement about one reply, and it used
    to be credited to `get_current_topic()` -- the ambient topic. Measured:
    reactions to two unrelated replies both landed on `node:capitalismo tardio`,
    so a 👍 on an answer about mortality silently reinforced a node about
    capitalism. Worse, it looked like it had worked.

    Matching is exact on the normalised label, never fuzzy: the most specific
    (longest) label that occurs in the text wins, so "vida" cannot be beaten by
    a longer label that merely contains it. Nothing is created -- this resolves
    to rows that already exist, and returns None when the text names none.
    """
    if not text or not str(text).strip():
        return None
    con = _connect()
    try:
        rows = con.execute(
            "SELECT node_key, label, weight, affinity FROM memory_graph WHERE node_type = 'node'"
        ).fetchall()
    finally:
        con.close()

    # Imported locally: this module is src.brain.memory_graph, and the
    # normaliser lives in a DIFFERENT module of the same name,
    # src.api.memory_graph. A module-level import of the bare name reads as
    # local and resolves to the wrong file.
    from src.api.memory_graph import normalise

    haystack = " " + normalise(text) + " "
    best = None
    for r in rows:
        label = normalise(r["label"])
        if not label or len(label) < 3:
            continue
        if (" " + label + " ") in haystack or haystack.strip().endswith(label):
            # Longest match wins: a specific label beats a generic one.
            if best is None or len(label) > len(normalise(best["label"])):
                best = {"node_key": r["node_key"], "label": r["label"]}
    return best


def set_current_topic(node_key: str) -> None:
    """Remember the most recently discussed topic (single-row table)."""
    if not node_key:
        return
    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO {TOPIC_TABLE} (id, current_key, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET current_key = excluded.current_key,
                                          updated_at = excluded.updated_at
            """,
            (node_key, _now()),
        )
        conn.commit()


def get_current_topic() -> Optional[str]:
    """Return the node_key of the current topic, or None."""
    with _connect() as conn:
        row = conn.execute(f"SELECT current_key FROM {TOPIC_TABLE} WHERE id = 1").fetchone()
    return row["current_key"] if row else None


# ---------------------------------------------------------------------------
# graph indexing + retrieval
# ---------------------------------------------------------------------------


_EDGE_RE = re.compile(
    r"([A-Za-zÀ-ÿ0-9_ .'\-]+)\[?([^\[\]]*)\]?\s*-->\s*"
    r"(?:\|[^|]*\|\s*)?"
    r"([A-Za-zÀ-ÿ0-9_ .'\-]+)\[?([^\[\]]*)\]?",
    re.IGNORECASE,
)


def index_memory(memory: dict) -> list[str]:
    """Index a skill_memory JSON payload (tags + mermaid) into the graph.

    Tags become topic nodes; mermaid ``A --> B`` relations become edges.
    The first tag becomes the current topic, closing the loop with FlyBrain
    orientation/reward handling.

    Args:
        memory: parsed dict from skill_memory, e.g. {"tags": [...], "mermaid": "..."}.

    Returns:
        List of node_keys written.
    """
    if not isinstance(memory, dict):
        return []

    keys: list[str] = []

    # Vector token (colocar depois do node vector): novidade/valência da memória
    novelty = float(memory.get("novelty", 0.0) or 0.0)

    tags = memory.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    for tag in tags:
        if isinstance(tag, str) and tag.strip():
            keys.append(upsert_node(tag, affinity=novelty))

    mermaid = memory.get("mermaid") or ""
    if isinstance(mermaid, str):
        for node_a, label_a, node_b, label_b in _EDGE_RE.findall(mermaid):
            src = label_a.strip() if label_a.strip() else node_a.strip()
            dst = label_b.strip() if label_b.strip() else node_b.strip()
            keys.append(upsert_edge(src, dst))

    if keys:
        set_current_topic(keys[0])

    return keys


# Below this a match is not a match. Exported so tests and the admin can
# state the rule rather than rediscover it.
MIN_AFFINITY = 0.05


def retrieve_neighborhood(prompt: str, max_results: int = 5) -> list[dict]:
    """Retrieve affinity-weighted graph context for a prompt.

    Matches nodes whose label appears in the prompt, then expands to their
    direct edges + positive-affinity neighbours, all sorted by affinity
    descending so the LLM sees what the connectome "likes" first.

    Args:
        prompt: user text to match topic labels against.
        max_results: cap on returned graph rows.

    Returns:
        List of dicts: {type, label, affinity, weight, relation}.
    """
    if not prompt:
        return []
    words = [w.lower() for w in re.findall(r"[a-zà-ÿ0-9_]{3,}", prompt.lower())]
    if not words:
        return []

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT node_type, label, source, target, affinity, weight
            FROM {GRAPH_TABLE}
            WHERE node_type = 'node'
            ORDER BY affinity DESC
            LIMIT 200
            """
        ).fetchall()

    nodes = [r for r in rows if r["label"].lower() in words]
    if not nodes:
        # Fallback: partial substring containment on significant words.
        nodes = [r for r in rows if any(w in r["label"].lower() for w in words)]
    if not nodes:
        return []

    matched = [r["label"].lower() for r in nodes]
    # An edge stores node_keys in source/target ("node:plataformas"), never
    # labels. Comparing LOWER(source) against the label -- which is what this
    # did -- can never be equal, so the edge expansion returned nothing at all
    # and every answer saw an isolated node. The map below turns the matched
    # labels into the keys the edge rows actually hold.
    with _connect() as conn:
        key_by_label, label_by_key = {}, {}
        for r in conn.execute(
            f"SELECT node_key, label FROM {GRAPH_TABLE} WHERE node_type = 'node'"
        ).fetchall():
            key_by_label[r["label"].lower()] = r["node_key"]
            label_by_key[(r["node_key"] or "").lower()] = r["label"]
        endpoints = {k for label in matched
                     for k in (key_by_label.get(label),) if k}
        edges: list[dict] = []
        for key in endpoints:
            edge_rows = conn.execute(
                f"""
                SELECT node_type, label, source, target, affinity, weight
                FROM {GRAPH_TABLE}
                WHERE node_type = 'edge'
                  AND (LOWER(source) = LOWER(?) OR LOWER(target) = LOWER(?))
                """,
                (key, key),
            ).fetchall()
            for e in edge_rows:
                # Render the relation in labels, not keys: "node:plataformas
                # -> node:capitalismo" is a database detail, and the consumer
                # of this string is the LLM and the 3D view.
                src_label = label_by_key.get((e["source"] or "").lower(),
                                             e["source"])
                tgt_label = label_by_key.get((e["target"] or "").lower(),
                                             e["target"])
                edges.append(
                    {
                        "type": "edge",
                        "label": e["label"],
                        "affinity": e["affinity"],
                        "weight": e["weight"],
                        "relation": f"{src_label} -> {tgt_label}",
                    }
                )

    result = []
    for r in nodes:
        result.append(
            {
                "type": "node",
                "label": r["label"],
                "affinity": r["affinity"],
                "weight": r["weight"],
                "relation": None,
            }
        )
    result.extend(edges)
    result.sort(key=lambda x: x["affinity"], reverse=True)
    # A zero-affinity neighbour is not context, it is noise. Without this the
    # graph handed back a node for "ola" that was the assistant's own previous
    # answer about Bimby, at "afinidade 0.00" -- so every reply could quote the
    # last reply, and a bad answer fed itself: ask about cats, get an answer
    # about cats, that answer becomes a node, and the next unrelated question
    # retrieves it. Threshold at the source so callers cannot forget it.
    relevant = [it for it in result if float(it.get("affinity") or 0.0) >= MIN_AFFINITY]
    return relevant[:max_results]


def graph_context_text(prompt: str, max_results: int = 5) -> str:
    """Render graph neighbourhood as a compact text snippet for the LLM."""
    items = retrieve_neighborhood(prompt, max_results=max_results)
    if not items:
        return ""
    lines = []
    for it in items:
        if it["type"] == "node":
            aff = f"{it['affinity']:+.2f}" if it["affinity"] else "0.00"
            lines.append(f"- {it['label']} (afinidade {aff})")
        else:
            aff = f"{it['affinity']:+.2f}" if it["affinity"] else "0.00"
            rel = f"- {it['relation']}"
            lines.append(f"{rel} (peso {it['weight']:.1f}, afinidade {aff})")
    return "\n".join(lines)
