"""Build the 3D memory-explorer payload from real brain.db records.

Design rules (deliberate, to keep the explorer honest):

* Every node and every edge is derived from a stored record. Nothing is
  invented, sampled or "plausible".
* A stored reference whose target does not exist is reported as
  ``unresolved`` and surfaced in ``stats`` -- it is never silently
  dropped and never faked with a fabricated counterpart.
* Concept identity is the *normalised label*, so a tag written by the LLM
  and a ``memory_graph`` node describing the same concept collapse into a
  single node instead of producing a duplicate.
* FlyBrain state is decoded through :class:`FlyBrainStore`, which uses
  ``numpy.load(..., allow_pickle=False)``. This module never unpickles a
  database blob, and a decode failure is reported instead of hidden.

The functions here are pure: they take rows and return plain dicts, so they
can be tested without Flask, a socket, or a database.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import config

# Resolved through config so a dev checkout uses its own database. This used to
# be hardcoded to /opt/phantasma/data/brain.db, so a dev process read -- and
# could write -- the PRODUCTION database. In production the resolved path is
# identical to the old literal.
BRAIN_DB_PATH = Path(config.BRAIN_DB_PATH)

# Payload keys actually present in stored memories, including the variants
# the model emitted on different runs.
TAG_KEYS: tuple[str, ...] = ("tags", "tags_pt", "tags(PT)")
FACT_KEYS: tuple[str, ...] = ("facts", "facts_en", "facts(EN S->P->O)")
MERMAID_KEYS: tuple[str, ...] = ("mermaid", "mermaid_diagram")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_LABEL = 60


def normalise(label: str) -> str:
    """Fold a label to a comparison key (case/accent/punctuation agnostic)."""
    decomposed = unicodedata.normalize("NFKD", str(label))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _SLUG_RE.sub(" ", ascii_only.lower()).strip()


def slug(label: str) -> str:
    return _SLUG_RE.sub("-", normalise(label)).strip("-") or "x"


def _as_str_list(value: Any) -> list[str]:
    """Coerce a stored tag/fact value into a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"[,;|]", value)]
        return [p for p in parts if p]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, (int, float)):
                out.append(str(item))
        return out
    if isinstance(value, dict):
        return [f"{k}: {v}" for k, v in value.items()]
    return [str(value)]


def parse_memory(text: str) -> dict[str, Any]:
    """Parse one stored memory payload.

    Returns a dict with ``tags``, ``facts``, ``mermaid``, ``parsed`` and
    ``preview``. Malformed JSON degrades to a plain-text memory instead of
    raising, because 4 of the 61 stored rows are not valid JSON.
    """
    result: dict[str, Any] = {
        "tags": [],
        "facts": [],
        "mermaid": None,
        "parsed": False,
        "preview": (text or "").strip()[:_MAX_LABEL],
        "raw": text or "",
    }
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return result
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        return result
    if not isinstance(data, dict):
        return result

    result["parsed"] = True
    for key in TAG_KEYS:
        for tag in _as_str_list(data.get(key)):
            if tag not in result["tags"]:
                result["tags"].append(tag)
    for key in FACT_KEYS:
        for fact in _as_str_list(data.get(key)):
            if fact not in result["facts"]:
                result["facts"].append(fact)
    for key in MERMAID_KEYS:
        diagram = data.get(key)
        if isinstance(diagram, str) and diagram.strip():
            result["mermaid"] = diagram.strip()
            break
    if result["tags"]:
        result["preview"] = result["tags"][0][:_MAX_LABEL]
    return result


def _memory_label(parsed: dict[str, Any]) -> str:
    if parsed["tags"]:
        return f"#{parsed['tags'][0]}"[:_MAX_LABEL]
    preview = parsed["preview"]
    return (preview[:_MAX_LABEL] + "…") if len(preview) > _MAX_LABEL else (preview or "(vazio)")


class _ConceptRegistry:
    """Label-keyed concept nodes; merges tags and memory_graph nodes."""

    def __init__(self) -> None:
        self._by_key: dict[str, dict[str, Any]] = {}
        self._label_to_key: dict[str, str] = {}

    def get(self, label: str) -> dict[str, Any]:
        key = normalise(label)
        if key not in self._by_key:
            node = {
                "id": f"tag:{slug(label)}",
                "kind": "concept",
                "label": str(label).strip()[:_MAX_LABEL],
                "sources": [],
                "weight": 1.0,
                "touch_count": 0,
                "affinity": 0.0,
                "unresolved": False,
                "memory_ids": [],
            }
            self._by_key[key] = node
            # First spelling seen wins as canonical.
            self._label_to_key.setdefault(key, key)
        return self._by_key[key]

    def find(self, label: str) -> dict[str, Any] | None:
        """Return the existing concept for this label, or None. Does not create.

        The identity is ``normalise(label)``, the same key ``get`` uses, and not
        the node's stored ``label`` -- that one is truncated to ``_MAX_LABEL``.
        Resolving edges against the truncated label made a label longer than 60
        chars look absent, so the endpoint fell into the "dangling" branch and
        ``get`` returned the SAME node, which was then appended to ``nodes`` a
        second time: two nodes, one id. See test_long_labels_do_not_duplicate.
        """
        return self._by_key.get(normalise(label))

    def key_for(self, label: str) -> str:
        return normalise(label)

    def nodes(self) -> list[dict[str, Any]]:
        return list(self._by_key.values())


def build_graph(
    memory_rows: Iterable[Sequence[Any]],
    graph_rows: Iterable[Sequence[Any]] = (),
) -> dict[str, Any]:
    """Assemble the explorer payload.

    ``memory_rows`` are ``(id, timestamp, text)`` tuples.
    ``graph_rows`` are ``(node_key, node_type, label, source, target,
    affinity, weight, touch_count)`` tuples.
    """
    concepts = _ConceptRegistry()
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    seen_link: set[tuple[str, str, str]] = set()

    stats = {
        "memories": 0,
        "memories_parsed": 0,
        "memories_unparsed": 0,
        "memories_with_mermaid": 0,
        "facts_total": 0,
        "concepts": 0,
        "tags_distinct": 0,
        "graph_nodes": 0,
        "graph_edges": 0,
        "unresolved_edges": 0,
        "flybrain": None,
    }

    def add_link(
        source: str,
        target: str,
        kind: str,
        affinity: float = 0.0,
        node_key: Optional[str] = None,
        weight: Optional[float] = None,
    ) -> None:
        """Add a link, carrying the identity needed to edit it.

        ``node_key`` and ``weight`` are the stored row's key and weight, not
        the merged concept's. Without them the 3D explorer could draw an edge
        but not correct it: saving a weight needs a key to write against, and
        the concept's own weight is a max() across several rows, so editing it
        would silently rewrite the wrong one.
        """
        sig = (source, target, kind)
        if source == target or sig in seen_link:
            return
        seen_link.add(sig)
        links.append(
            {
                "source": source,
                "target": target,
                "kind": kind,
                "affinity": float(affinity),
                "node_key": node_key,
                "weight": None if weight is None else float(weight),
            }
        )

    # --- memory rows -> memory nodes + tag links -------------------------
    for row in memory_rows:
        mem_id, timestamp, text = row[0], row[1], row[2]
        parsed = parse_memory(text)
        stats["memories"] += 1
        if parsed["parsed"]:
            stats["memories_parsed"] += 1
        else:
            stats["memories_unparsed"] += 1
        if parsed["mermaid"]:
            stats["memories_with_mermaid"] += 1
        stats["facts_total"] += len(parsed["facts"])

        node_id = f"mem:{mem_id}"
        nodes.append(
            {
                "id": node_id,
                "kind": "memory",
                "label": _memory_label(parsed),
                "timestamp": timestamp,
                "preview": parsed["preview"],
                "tags": parsed["tags"],
                "facts": parsed["facts"],
                "mermaid": parsed["mermaid"],
                "parsed": parsed["parsed"],
                "raw": parsed["raw"],
            }
        )

        for tag in parsed["tags"]:
            concept = concepts.get(tag)
            if "tag" not in concept["sources"]:
                concept["sources"].append("tag")
            if mem_id not in concept["memory_ids"]:
                concept["memory_ids"].append(mem_id)
            add_link(node_id, concept["id"], "tagged")

    # --- memory_graph rows -> concept nodes + graph edges ----------------
    graph_edge_labels: list[tuple[str, str, float]] = []
    for row in graph_rows:
        node_key, node_type, label, source, target, affinity, weight, touch = (
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5] if len(row) > 5 else 0.0,
            row[6] if len(row) > 6 else 1.0,
            row[7] if len(row) > 7 else 0,
        )
        if (node_type or "").lower() == "edge":
            stats["graph_edges"] += 1
            if source and target:
                graph_edge_labels.append((str(source), str(target), float(affinity or 0.0)))
            continue
        if (node_type or "").lower() != "node":
            # Indexing markers ("memory:<id>:<hash>") are bookkeeping, not
            # knowledge; drawing them as concepts inflated the count and the
            # 3D view. Anything that is neither a node nor an edge is skipped.
            continue

        stats["graph_nodes"] += 1
        concept = concepts.get(label or node_key)
        if "graph" not in concept["sources"]:
            concept["sources"].append("graph")
        # An explicit, operator-set weight on a stored node is a CORRECTION and
        # outranks the aggregate, instead of being maxed into it. With `max`
        # here, lowering a node weight wrote it to the database and then the very
        # next read still showed the old value, because some other source (a
        # memory-derived default) still weighed 1.0 -- so the edit looked like
        # it had not stuck. `sources` records that a stored node backs this
        # concept, which is what makes the node's own weight authoritative.
        if "graph" in concept["sources"]:
            concept["weight"] = float(weight if weight is not None else 1.0)
        else:
            concept["weight"] = max(float(concept["weight"]), float(weight or 1.0))
        concept["touch_count"] = max(int(concept["touch_count"]), int(touch or 0))
        concept["affinity"] = max(float(concept["affinity"]), float(affinity or 0.0))
        concept["graph_key"] = node_key

    concept_nodes = concepts.nodes()
    for concept in concept_nodes:
        if "tag" in concept["sources"]:
            stats["tags_distinct"] += 1
        nodes.append(concept)

    # Resolve stored graph edges by label; flag the ones that dangle.
    for src_label, tgt_label, affinity in graph_edge_labels:
        # Through the registry, not a scan of ``concept_nodes``: see find().
        src = concepts.find(src_label)
        tgt = concepts.find(tgt_label)
        if src is None:
            src = concepts.get(src_label)
            src["unresolved"] = True
            src["sources"].append("dangling")
            nodes.append(src)
            concept_nodes.append(src)
            stats["unresolved_edges"] += 1
            src.setdefault("dangling", []).append({"edge_key": node_key, "side": "source"})
        if tgt is None:
            tgt = concepts.get(tgt_label)
            tgt["unresolved"] = True
            tgt["sources"].append("dangling")
            nodes.append(tgt)
            tgt.setdefault("dangling", []).append({"edge_key": node_key, "side": "target"})
            concept_nodes.append(tgt)
            stats["unresolved_edges"] += 1
        add_link(src["id"], tgt["id"], "graph", affinity, node_key=node_key, weight=weight)

    stats["concepts"] = len(concept_nodes)
    stats["nodes"] = len(nodes)
    stats["links"] = len(links)

    # Degree is derived from the links just built: the client uses it to size
    # nodes and to hide the long tail of one-off tags.
    degree: dict[str, int] = {}
    for link in links:
        degree[link["source"]] = degree.get(link["source"], 0) + 1
        degree[link["target"]] = degree.get(link["target"], 0) + 1
    for node in nodes:
        node["degree"] = degree.get(node["id"], 0)

    return {"nodes": nodes, "links": links, "stats": stats}


def _load_flybrain(db_path: Path) -> dict[str, Any] | None:
    """Return real FlyBrain reinforcement state, or ``None`` when there is none.

    Three rules, learned the hard way:

    1. The concrete class is ``FlyBrainStore`` (``src.brain.persistence``) --
       an earlier version of this function imported a non-existent
       ``FlyBrainPersistence``, and the resulting ImportError was swallowed by a
       bare ``except``, so the UI reported "no reinforcement" even when the
       table had rows. A wrong import must never look like an empty database.
    2. The row count is checked on a read-only connection *before* the store is
       constructed, because ``FlyBrainStore.__init__`` issues DDL.
    3. If rows exist but decoding fails, the error is returned rather than
       hidden. ``None`` must mean "no rows", never "I could not read it".

    Decoding uses FlyBrainStore.load(), which calls
    ``numpy.load(..., allow_pickle=False)``; this module never unpickles a
    database blob.
    """
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as probe:
            row = probe.execute("SELECT COUNT(*) FROM flybrain_state").fetchone()
    except sqlite3.Error:
        return None
    if not row or row[0] == 0:
        return None

    try:
        from src.brain.persistence import FlyBrainStore

        state = FlyBrainStore(db_path=str(db_path)).load()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, reported below
        return {"steps": None, "error": f"{type(exc).__name__}: {exc}"}

    if not state:
        return {"steps": None, "error": "estado presente mas vazio após deserialização"}
    summary: dict[str, Any] = {"steps": state.get("steps", 0)}
    for key in ("total_rewards", "total_punishments", "avg_reward", "orientation_deg"):
        if key in state:
            summary[key] = state[key]
    return summary


def build_graph_from_db(db_path: Path | str = BRAIN_DB_PATH) -> dict[str, Any]:
    """Read brain.db and return the explorer payload."""
    db = Path(db_path)
    if not db.exists():
        return {
            "nodes": [],
            "links": [],
            "stats": {
                "nodes": 0,
                "links": 0,
                "memories": 0,
                "error": "brain.db not found",
            },
        }

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        memory_rows = conn.execute(
            "SELECT id, timestamp, text FROM memories ORDER BY id"
        ).fetchall()
        graph_rows = conn.execute(
            "SELECT node_key, node_type, label, source, target, affinity, weight,"
            " touch_count FROM memory_graph"
        ).fetchall()
    finally:
        conn.close()

    payload = build_graph(memory_rows, graph_rows)
    payload["stats"]["flybrain"] = _load_flybrain(db)
    return payload
