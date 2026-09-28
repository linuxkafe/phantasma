"""Editable graph operations for the brain hub and the 3D explorer.

Why this exists
---------------
The graph was read-only. ``/admin/brain`` and the 3D explorer could display
nodes, edges, weights and the RAG, and nothing could be corrected: a wrong
affinity, a missing edge or a stale RAG chunk had to be fixed by editing
SQLite by hand, against a live database, with no record of who did it.

Scope, decided with the owner
-----------------------------
* Adjust weights and affinities, create edges, retarget them, delete them.
* Edit the RAG: correct a chunk's summary, tags or facts.
* Audit every change -- who, when, what, before and after.
* **No retraining.** A weight edit is a stored correction, not a learning
  event. Clicking a slider must not move the brain's learned state, because an
  accidental drag would train the wrong thing and the change would be
  indistinguishable from deliberate feedback. Training stays where it
  belongs: the reaction path (``src/brain/reactions.py``).

Why the audit table exists
-------------------------
A brain that can be edited is a brain that can be corrupted. The rows here are
what makes a bad edit attributable rather than mysterious, and they are written
in the SAME transaction as the change -- an audit record that could fail to
persist is not an audit record.

Validation
----------
Weight and affinity are bounded to [-1, 1]. An unbounded weight is the kind of
value that makes a layout explode and a query unusable, and the range is
enforced here rather than trusted from the client.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.api.memory_graph import slug

logger = logging.getLogger("phantasma.graph_edit")

# Column allowlists. Every write goes through one of these -- a caller-supplied
# column name in an UPDATE is an injection surface, and this is the boundary.
NODE_COLUMNS = {
    "label",
    # Added 2026-09-27 (T046): a node had no weight, so the strength of a
    # concept could only be changed on its edges. `memory_graph.py` already
    # reads `weight` when aggregating a concept, so this is a real, visible
    # control and not a new concept. Clamped by the same _clamp as the edges.
    "weight",
    "node_type",
    "node_gmif_type",
    "node_gmif_evidence",
}
EDGE_COLUMNS = {"weight", "affinity", "label"}
RAG_FIELDS = {"summary", "tags", "facts"}

AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS graph_edit_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT,
    op TEXT NOT NULL,
    target TEXT,
    before_json TEXT,
    after_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_graph_edit_audit_at
    ON graph_edit_audit(at DESC);
"""


class EditError(Exception):
    """A rejected edit, with a reason the caller can show.

    Raised instead of returning a bare False so the API can distinguish
    "refused because it was wrong" from "refused because it did not exist",
    and report which.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(db_path: "str | Path | sqlite3.Connection") -> None:
    """Create the audit table if absent. Safe to call on every request.

    Accepts an OPEN connection as well as a path, and this matters: the callers
    pass their own connection so the audit row commits in the SAME transaction
    as the edit. An earlier version took only a path and reconnected, which
    would have committed the audit separately from -- or before -- the change
    it is supposed to describe.
    """
    if isinstance(db_path, sqlite3.Connection):
        db_path.executescript(AUDIT_DDL)
        return
    conn = _connect(db_path)
    try:
        conn.executescript(AUDIT_DDL)
        conn.commit()
    finally:
        conn.close()


def _audit(
    conn: sqlite3.Connection,
    actor: str,
    op: str,
    target: Optional[str],
    before: Optional[dict],
    after: Optional[dict],
) -> None:
    """Write the audit row. Called inside the caller's transaction.

    Same connection and same transaction as the change: if the edit commits,
    the record commits. If either fails, neither lands.
    """
    conn.execute(
        "INSERT INTO graph_edit_audit (at, actor, op, target, before_json, after_json) "
        "VALUES (?,?,?,?,?,?)",
        (
            _now(),
            (actor or "unknown")[:64],
            op,
            (target or "")[:200],
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
        ),
    )


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def get_edge(db_path: str | Path, node_key: str) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM memory_graph WHERE node_key = ?", (node_key,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_edge(db_path: str | Path, node_key: str, changes: dict, actor: str = "local") -> dict:
    """Adjust an edge's weight / affinity / label.

    Returns the updated row. Raises EditError with a reason on rejection.
    """
    unknown = set(changes) - EDGE_COLUMNS
    if unknown:
        raise EditError(f"fields not editable on an edge: {sorted(unknown)}")
    if not changes:
        raise EditError("no changes supplied")

    for field in ("weight", "affinity"):
        if field in changes:
            try:
                changes[field] = _clamp(changes[field])
            except (TypeError, ValueError):
                raise EditError(f"{field} must be a number")

    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        before_row = conn.execute(
            "SELECT * FROM memory_graph WHERE node_key = ?", (node_key,)
        ).fetchone()
        if before_row is None:
            raise EditError("no such edge")
        if before_row["node_type"] != "edge":
            raise EditError("that key is a node, not an edge")

        before = {k: before_row[k] for k in changes}
        sets = ", ".join(f"{col} = ?" for col in changes)
        conn.execute(
            f"UPDATE memory_graph SET {sets}, updated_at = ? WHERE node_key = ?",
            (*changes.values(), _now(), node_key),
        )
        after_row = conn.execute(
            "SELECT * FROM memory_graph WHERE node_key = ?", (node_key,)
        ).fetchone()
        after = {k: after_row[k] for k in changes}
        _audit(conn, actor, "edge.update", node_key, before, after)
        conn.commit()
        return dict(after_row)
    finally:
        conn.close()


def create_edge(
    db_path: str | Path,
    source: str,
    target: str,
    weight: float = 1.0,
    affinity: float = 0.0,
    label: str = "",
    actor: str = "local",
) -> dict:
    """Link two existing nodes, or create the edge between them if absent.

    Idempotent on (source, target): re-linking an existing pair adjusts the
    weight instead of creating a duplicate. A graph with two edges between the
    same pair is a bug the user cannot see in a layout.
    """
    source = (source or "").strip()
    target = (target or "").strip()
    if not source or not target:
        raise EditError("source and target are required")
    if source == target:
        raise EditError("an edge cannot point at itself")

    weight = _clamp(weight)
    affinity = _clamp(affinity)
    key = f"edge:{source} -> {target}"
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        existing = conn.execute("SELECT * FROM memory_graph WHERE node_key = ?", (key,)).fetchone()
        if existing is not None:
            before = {"weight": existing["weight"], "affinity": existing["affinity"]}
            conn.execute(
                "UPDATE memory_graph SET weight = ?, affinity = ?, updated_at = ? "
                "WHERE node_key = ?",
                (weight, affinity, _now(), key),
            )
            _audit(
                conn,
                actor,
                "edge.relink",
                key,
                before,
                {"weight": weight, "affinity": affinity},
            )
            conn.commit()
            row = conn.execute("SELECT * FROM memory_graph WHERE node_key = ?", (key,)).fetchone()
            return dict(row)

        now = _now()
        conn.execute(
            "INSERT INTO memory_graph (node_key, node_type, label, source, target, "
            "affinity, weight, touch_count, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,0,?,?)",
            (
                key,
                "edge",
                label or f"{source} → {target}",
                source,
                target,
                affinity,
                weight,
                now,
                now,
            ),
        )
        _audit(
            conn,
            actor,
            "edge.create",
            key,
            None,
            {
                "source": source,
                "target": target,
                "weight": weight,
                "affinity": affinity,
            },
        )
        conn.commit()
        row = conn.execute("SELECT * FROM memory_graph WHERE node_key = ?", (key,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def delete_edge(db_path: str | Path, node_key: str, actor: str = "local") -> dict:
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM memory_graph WHERE node_key = ?", (node_key,)).fetchone()
        if row is None:
            raise EditError("no such edge")
        if row["node_type"] != "edge":
            # Deleting a node through the edge endpoint would orphan every
            # other edge that points at it.
            raise EditError("that key is a node; delete it as a node")
        before = {k: row[k] for k in ("source", "target", "weight", "affinity")}
        conn.execute("DELETE FROM memory_graph WHERE node_key = ?", (node_key,))
        _audit(conn, actor, "edge.delete", node_key, before, None)
        conn.commit()
        return {"deleted": node_key, "was": before}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def update_node(db_path: str | Path, node_key: str, changes: dict, actor: str = "local") -> dict:
    unknown = set(changes) - NODE_COLUMNS
    if unknown:
        raise EditError(f"fields not editable on a node: {sorted(unknown)}")
    if not changes:
        raise EditError("no changes supplied")
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM memory_graph WHERE node_key = ?", (node_key,)).fetchone()
        if row is None:
            raise EditError("no such node")
        if row["node_type"] != "node":
            raise EditError("that key is an edge, not a node")
        # Same clamp as the edges (update_edge). Without it a node weight could
        # be written straight from the request as 5.0 or -1e9, and nothing
        # downstream clamps on read.
        if "weight" in changes:
            changes = dict(changes)
            try:
                changes["weight"] = _clamp(float(changes["weight"]))
            except (TypeError, ValueError) as exc:
                raise EditError("weight is not a number: " + repr(changes["weight"])) from exc
        before = {k: row[k] for k in changes}
        sets = ", ".join(f"{col} = ?" for col in changes)
        conn.execute(
            f"UPDATE memory_graph SET {sets}, updated_at = ? WHERE node_key = ?",
            (*changes.values(), _now(), node_key),
        )
        _audit(conn, actor, "node.update", node_key, before, changes)
        conn.commit()
        new = conn.execute("SELECT * FROM memory_graph WHERE node_key = ?", (node_key,)).fetchone()
        return dict(new)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------


def update_rag(db_path: str | Path, key: str, changes: dict, actor: str = "local") -> dict:
    """Correct a RAG entry: summary, tags, facts or mermaid.

    The RAG payload is stored in the ``memories.text`` column as JSON, and
    ``/admin/rag`` renders that same column -- so this edits the stored value,
    not a derived index that would drift from it. The previous value is kept in
    the audit row: a chunk summarised badly and then corrected is exactly the
    case where "what did it used to say" matters.

    An earlier draft of this function wrote to ``topic_state``, which is the
    *current topic pointer*, not the RAG. That would have reported success while
    changing nothing the user can see.
    """
    unknown = set(changes) - RAG_FIELDS
    if unknown:
        raise EditError(f"fields not editable in a RAG entry: {sorted(unknown)}")
    if not changes:
        raise EditError("no changes supplied")

    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        row = conn.execute("SELECT id, text FROM memories WHERE id = ?", (key,)).fetchone()
        if row is None:
            raise EditError("no such memory")
        try:
            data = json.loads(row["text"] or "{}")
        except ValueError:
            raise EditError("this memory has no JSON RAG payload to edit")
        if not isinstance(data, dict):
            raise EditError("this memory's RAG payload is not an object")

        before = {f: data.get(f) for f in changes}
        data.update(changes)
        conn.execute(
            "UPDATE memories SET text = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), key),
        )
        after = {f: data.get(f) for f in changes}
        _audit(conn, actor, "rag.update", str(key), before, after)
        conn.commit()
        return {"key": str(key), "updated": after}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# FlyBrain
# ---------------------------------------------------------------------------


def _norm_label(value: str) -> str:
    """The SAME comparison key the read path uses, or the resolver lies.

    `memory_graph.build_graph` decides an endpoint is dangling by comparing
    `normalise(endpoint)` against `normalise(each concept label)`, and that
    function is NFKD-folded, lowercased and stripped of punctuation. A private
    copy of the rule here would be a silent second source of truth: relink
    would happily "resolve" a ref that the very next read still counts, or
    refuse a relink the read path would have accepted.

    So it is imported, not reimplemented. The import direction (brain -> api)
    is deliberate and documented rather than restructured here: agreeing with
    the reader is a correctness requirement of this function, and duplicating
    the rule is how that agreement breaks later. If the import fails we raise,
    because the alternative -- resolving by guesswork -- is exactly the silent
    wrong answer this is meant to prevent.
    """
    try:
        from src.api.memory_graph import normalise
    except Exception as exc:  # noqa: BLE001
        raise EditError(
            "cannot import the reader's label normaliser; refusing to resolve "
            "a reference by a different rule than the one that counts it"
        ) from exc
    return normalise(value)


def delete_node(db_path: "str | Path", node_key: str, actor: str = "local") -> dict:
    """Remove a stored node, refusing if anything still points at it.

    The counterpart to `create_node_from_text` and to `delete_edge`. Without it
    the feature could only ever grow the graph: the node created by the "save
    this reply as a node" action had no way back out, because `delete` on the
    edge endpoint refuses node keys ("that key is a node; delete it as a node").

    Refuses while an edge still names the node, because deleting it would leave
    a dangling reference -- exactly the condition this whole reconciliation work
    exists to remove. The caller is told which edges are in the way.
    """
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM memory_graph WHERE node_key = ?", (node_key,)).fetchone()
        if row is None:
            raise EditError(f"no such node: {node_key}")
        if row["node_type"] != "node":
            raise EditError("that key is an edge, not a node")
        label = row["label"]
        blocking = conn.execute(
            "SELECT node_key FROM memory_graph WHERE node_type = 'edge' "
            "AND (source = ? OR target = ? OR label = ?)",
            (label, label, label),
        ).fetchall()
        if blocking:
            raise EditError("still referenced by: " + ", ".join(b["node_key"] for b in blocking))
        _audit(
            conn,
            actor,
            "node.delete",
            node_key,
            {"node_key": node_key, "label": label},
            None,
        )
        conn.execute("DELETE FROM memory_graph WHERE node_key = ?", (node_key,))
        conn.commit()
        return {"node_key": node_key, "label": label, "deleted": True}
    finally:
        conn.close()


def create_node_from_text(
    db_path: "str | Path",
    label: str,
    text: str = "",
    actor: str = "ui",
    rationale: Optional[str] = None,
) -> dict:
    """Create a node from a piece of text the operator chose to keep.

    Deliberately NOT automatic. A reaction with no graph match leaves the graph
    alone, because a reaction is a sentiment and auto-creating a node per
    unanswered text would bloat the graph with whatever happened to be said.
    This is the explicit action behind that choice: the operator reads the note,
    decides the reply is worth remembering, and presses the button.

    The label is the truncated text because there is no better name for a reply;
    `rationale` records the text it came from so the audit can explain the node
    later, which a bare label could not.
    """
    label = (label or "").strip()
    if not label:
        raise EditError("label is required")
    # Prefer the first sentence: a reply truncated at 120 characters mid-clause
    # makes a poor node name ("...where the light of knowledge is only a distant
    # and faint memory"). Cutting at a sentence boundary gives a name that stands
    # on its own, and still falls back to a hard cap when there is no punctuation.
    import re as _re

    first = _re.split(r"(?<=[.!?])\s+", label, maxsplit=1)[0].strip()
    label = first if 12 <= len(first) <= 120 else label[:117].rstrip() + "..."
    key = "node:" + slug(label)
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        clash = conn.execute(
            "SELECT node_key FROM memory_graph WHERE node_key = ?", (key,)
        ).fetchone()
        if clash is not None:
            raise EditError(f"that node already exists: {key}")
        before = {"node_key": None}
        after = {"node_key": key, "label": label}
        conn.execute(
            "INSERT INTO memory_graph (node_key, node_type, label, source, "
            "affinity, weight, touch_count, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (key, "node", label, "chat", 0.0, 1.0, 0, _now(), _now()),
        )
        _audit(
            conn,
            actor,
            "node.create_from_text",
            key,
            before,
            {**after, "rationale": rationale or text[:500]},
        )
        conn.commit()
        return {"node_key": key, "label": label}
    finally:
        conn.close()


def resolve_dangling(
    db_path: "str | Path",
    edge_key: str,
    side: str,
    action: str,
    target_label: Optional[str] = None,
    actor: str = "local",
    rationale: Optional[str] = None,
) -> dict:
    """Resolve one dangling edge endpoint. Two distinct decisions, never merged.

    `relink`  the pending label is the SAME concept as an existing node under a
              different name. The stored endpoint is rewritten to the existing
              node's label, so the read path stops counting it as dangling.
    `promote` the pending label is a genuine concept that simply had no node
              row. One is inserted.

    Why two operations and not an auto-fix: they are opposite judgements about
    the same observation, and only a person (or, in T047, a model with recorded
    evidence) can make that call. Silently choosing one would turn a naming
    mismatch into a lost edge, or a real concept into a duplicate.

    The write and the audit row share one transaction, like every other edit
    here: if the audit fails, the data does not move.
    """
    if side not in ("source", "target"):
        raise EditError(f"side must be 'source' or 'target', got {side!r}")
    if action not in ("relink", "promote"):
        raise EditError(f"action must be 'relink' or 'promote', got {action!r}")
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM memory_graph WHERE node_key = ? AND node_type = 'edge'",
            (edge_key,),
        ).fetchone()
        if row is None:
            raise EditError(f"no such edge: {edge_key!r}")
        old_label = row[side]
        if not old_label:
            raise EditError(f"edge {edge_key!r} has no {side} to resolve")

        if action == "relink":
            if not target_label:
                raise EditError("relink requires target_label")
            want = _norm_label(target_label)
            match = conn.execute(
                "SELECT node_key, label, weight FROM memory_graph WHERE node_type = 'node'"
            ).fetchall()
            hit = next((m for m in match if _norm_label(m["label"]) == want), None)
            if hit is None:
                # Never invent the target: a typo would otherwise create a node
                # the operator did not ask for and quietly mask a real dangling
                # ref as "resolved".
                raise EditError(
                    f"no node with label {target_label!r}; promote it first if "
                    f"it is a genuine new concept"
                )
            if _norm_label(hit["label"]) == _norm_label(old_label):
                raise EditError("target is already this endpoint; nothing to resolve")
            before = {side: old_label}
            after = {side: hit["label"], "target_node_key": hit["node_key"]}
            conn.execute(
                f"UPDATE memory_graph SET {side} = ?, updated_at = ? WHERE node_key = ?",
                (hit["label"], _now(), edge_key),
            )
            _audit(
                conn,
                actor,
                "edge.relink",
                edge_key,
                before,
                {**after, "rationale": rationale, "action": action},
            )
            conn.commit()
            return {
                "action": action,
                "side": side,
                "from": old_label,
                "to": hit["label"],
                "target_node_key": hit["node_key"],
                "edge_key": edge_key,
            }

        # promote
        if not target_label:
            target_label = old_label
        want = _norm_label(target_label)
        clash = conn.execute(
            "SELECT node_key FROM memory_graph WHERE node_type = 'node'"
        ).fetchall()
        if any(
            _norm_label(c["node_key"].split(":", 1)[-1]) == want
            or _norm_label(c["node_key"]) == want
            for c in clash
        ):
            raise EditError(f"a node for {target_label!r} already exists -- use relink instead")
        node_key = "node:" + "-".join(str(target_label).lower().split())
        before = {"promoted": None}
        after = {"node_key": node_key, "label": target_label}
        conn.execute(
            "INSERT INTO memory_graph (node_key, node_type, label, source, "
            "affinity, weight, touch_count, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (node_key, "node", target_label, "graph", 0.0, 1.0, 0, _now(), _now()),
        )
        # Promoting a node that does not yet carry the edge's endpoint label
        # would leave the ref dangling: the reader still finds no concept
        # matching the old text, so `unresolved_edges` would not move and the
        # operator would be told a ref was resolved when it was not. Point the
        # endpoint at the node we just created so the two agree by construction.
        if _norm_label(target_label) != _norm_label(old_label):
            conn.execute(
                f"UPDATE memory_graph SET {side} = ?, updated_at = ? WHERE node_key = ?",
                (target_label, _now(), edge_key),
            )
            after = {**after, f"edge_{side}": {"from": old_label, "to": target_label}}
        _audit(
            conn,
            actor,
            "node.promote",
            node_key,
            before,
            {**after, "rationale": rationale, "action": action, "edge_key": edge_key},
        )
        conn.commit()
        return {
            "action": action,
            "side": side,
            "from": old_label,
            "to": target_label,
            "target_node_key": node_key,
            "edge_key": edge_key,
        }
    finally:
        conn.close()


def flybrain_state(db_path: str | Path) -> Optional[dict]:
    """The FlyBrain ring state, decoded, or None if never persisted."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT data FROM flybrain_state WHERE id = 1").fetchone()
        if row is None or not row["data"]:
            return None
        raw = row["data"]
        if isinstance(raw, (bytes, bytearray)):
            import zlib

            try:
                raw = zlib.decompress(bytes(raw))
            except zlib.error:
                pass
            raw = raw.decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {"_raw": str(raw)[:4000]}
    finally:
        conn.close()


def list_audit(db_path: str | Path, limit: int = 50) -> list[dict]:
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM graph_edit_audit ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for field in ("before_json", "after_json"):
                if d.get(field):
                    try:
                        d[field.replace("_json", "")] = json.loads(d[field])
                    except ValueError:
                        pass
            out.append(d)
        return out
    finally:
        conn.close()
