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

import hashlib
import json
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

# The GMIF claim columns, in the order they were added to production.
#
# `init_db()` created `memory_graph` without any of these and used
# CREATE TABLE IF NOT EXISTS, which never alters a table that already exists.
# The eleven columns therefore lived only in /opt/phantasma/data/brain.db,
# added by hand and owned by no code. Any tree without them -- which was dev,
# and is any fresh checkout -- had every GMIF query raise "no such column",
# and the blanket `except` in the dream cycle turned that into a print. That
# is the "runs and always fails" recorded in docs/ROADMAP.md:111, and it is
# why the graph stopped growing with eleven nodes sitting at
# gmif_validation_confidence = 0.0.
#
# Declarations are copied from the production table, defaults included. The
# CREATE TABLE below deliberately does NOT inline them: one migration path
# serves both a fresh database and an existing one, so the two cannot end up
# with different columns. tests/test_graph_schema.py pins this list.
GMIF_COLUMNS: tuple[tuple[str, str], ...] = (
    ("gmif_logical_form", "TEXT"),
    ("gmif_validation_type", "TEXT"),
    ("gmif_extraction_confidence", "REAL DEFAULT 0.0"),
    ("gmif_validation_confidence", "REAL DEFAULT 0.0"),
    ("gmif_source_chunks", "TEXT"),
    ("gmif_level", "TEXT"),
    ("gmif_classified_at", "TEXT"),
    ("gmif_classified_by", "TEXT"),
    ("node_gmif_type", "TEXT"),
    ("node_gmif_confidence", "REAL DEFAULT 0.0"),
    ("node_gmif_evidence", "TEXT"),
)


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
                updated_at TEXT
            )
            """
        )
        # Bring an existing graph up to the full claim schema. Additive only:
        # every declaration is nullable or has a DEFAULT, so no row is touched
        # and no data is lost. Idempotent, because the presence check below
        # skips columns that are already there -- which is what production
        # looks like, so a deploy is a no-op there rather than a rewrite.
        _present = {row[1] for row in conn.execute(f"PRAGMA table_info({GRAPH_TABLE})")}
        _added = []
        for _name, _decl in GMIF_COLUMNS:
            if _name not in _present:
                conn.execute(f"ALTER TABLE {GRAPH_TABLE} ADD COLUMN {_name} {_decl}")
                _added.append(_name)
        if _added:
            logger.info("Memory graph GMIF columns added: %s", ", ".join(_added))
        conn.commit()
        logger.info("Memory graph initialized")


# ---------------------------------------------------------------------------
# Materialising stored memories into the graph
# ---------------------------------------------------------------------------

# Tags this short are pronouns, greetings and single letters. They are the
# noise found in the stored rows ("Olá", "Ok", "Eu") and indexing them produces
# a graph of the conversation rather than of what was discussed.
MIN_CONCEPT_LEN = 4
MAX_CONCEPT_LEN = 60


def concept_edge_key(a: str, b: str) -> str:
    """Canonical node_key for the UNDIRECTED edge between two concepts.

    The key used to be built in the order the tags appeared in the row, so
    "leite" and "pecuária" produced both ``edge:leite|pecuária`` and
    ``edge:pecuária|leite``. The two rows then grew apart and the same
    relationship looked like two facts -- 11 such pairs were found in
    production. Sorting the lowercased endpoints gives one key per
    relationship, whichever order the tags arrived in.
    """
    x, y = str(a).strip().lower(), str(b).strip().lower()
    if y < x:
        x, y = y, x
    return f"edge:{x}|{y}"


def materialize_memories(limit: int = 0, only_unparsed: bool = False) -> dict[str, int]:
    """Write the concepts held in stored memories into `memory_graph`.

    The graph used to be filled only by :func:`index_memory`, which runs when
    skill_memory stores a memory. Rows that reached the table another way --
    a migration, an import, the consolidation step writing a summary -- were
    never indexed, so `/admin` reported an empty graph while the memories were
    demonstrably there. The admin derives its own nodes per request and never
    writes them back, so nothing else would ever close that gap.

    Concepts come from the parsed payload the model already produced: tags
    become nodes, and each memory's tags become edges between each other, so
    "these two subjects were discussed together" is a stored relationship
    rather than something recomputed on every page load. Provenance is kept in
    ``gmif_source_chunks``: the ids of the memories the node came from, so a
    concept can always be traced to the text that produced it.

    Idempotent: nodes and edges are upserted by key, so running it twice writes
    the same graph and only bumps ``touch_count``. Safe to call every cycle.

    Args:
        limit: stop after this many memories; 0 means all of them.
        only_unparsed: index rows that have no node yet, instead of every row.

    Returns:
        Counts of what was written.
    """
    from src.api.memory_graph import parse_memory

    report = {"memories_read": 0, "nodes_written": 0, "edges_written": 0, "skipped": 0}
    with _connect() as conn:
        already = {
            r[0]
            for r in conn.execute(
                f"SELECT node_key FROM {GRAPH_TABLE} WHERE node_key LIKE 'memory:%'"
            )
        }
        sql = "SELECT id, text FROM memories ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()

        for row in rows:
            memory_id, text = row[0], row[1]
            # The marker carries a content fingerprint, not just the id. Row ids
            # are reused: consolidation deletes the highest-numbered memories and
            # inserts one new row, which then takes a freed id. An id-only marker
            # would make only_unparsed skip that brand-new memory as if it had
            # already been indexed, silently losing its concepts.
            marker = f"memory:{memory_id}:{_fingerprint(text or '')}"
            if only_unparsed and marker in already:
                report["skipped"] += 1
                continue
            report["memories_read"] += 1

            parsed = parse_memory(text or "")
            concepts = [
                c.strip()
                for c in parsed["tags"]
                if isinstance(c, str)
                and MIN_CONCEPT_LEN <= len(c.strip()) <= MAX_CONCEPT_LEN
            ]
            if not concepts:
                report["skipped"] += 1
                continue

            now = _now()
            keys: list[str] = []
            for concept in concepts:
                key = f"node:{concept.lower()}"
                is_new = not conn.execute(
                    f"SELECT 1 FROM {GRAPH_TABLE} WHERE node_key = ?", (key,)
                ).fetchone()
                conn.execute(
                    f"""
                    INSERT INTO {GRAPH_TABLE}
                        (node_key, node_type, label, affinity, touch_count,
                         created_at, updated_at, gmif_source_chunks, gmif_classified_by)
                    VALUES (?, 'node', ?, 0.0, 1, ?, ?, ?, ?)
                    ON CONFLICT(node_key) DO UPDATE SET
                        touch_count = touch_count + 1,
                        updated_at = excluded.updated_at
                    """,
                    (key, concept, now, now,
                     json.dumps([{"memory_id": memory_id}]), "materialize_memories"),
                )
                keys.append(concept)
                if is_new:
                    report["nodes_written"] += 1

            # Concepts from one memory are co-mentioned, so they get an edge.
            # Each is recorded at M1 with validation_type 'external': the only
            # warrant is that the two appeared in the same stored row, which is
            # an observation about the text, not a derivation. Marking it
            # 'logical' would claim the link was reasoned out, and it was not --
            # the same mistake that made the research path unsafe.
            for i, a in enumerate(keys):
                for b in keys[i + 1:]:
                    edge_key = concept_edge_key(a, b)
                    edge_is_new = not conn.execute(
                        f"SELECT 1 FROM {GRAPH_TABLE} WHERE node_key = ?", (edge_key,)
                    ).fetchone()
                    conn.execute(
                        f"""
                        INSERT INTO {GRAPH_TABLE}
                            (node_key, node_type, label, source, target, weight,
                             touch_count, created_at, updated_at, gmif_level,
                             gmif_validation_type, gmif_extraction_confidence,
                             gmif_source_chunks, gmif_classified_at, gmif_classified_by)
                        VALUES (?, 'edge', ?, ?, ?, 1.0, 1, ?, ?, 'M1', 'external',
                                0.0, ?, ?, 'materialize_memories')
                        ON CONFLICT(node_key) DO UPDATE SET
                            touch_count = touch_count + 1,
                            updated_at = excluded.updated_at
                        """,
                        (edge_key, f"{a} + {b}", a, b, now, now,
                         json.dumps([{"memory_id": memory_id}]), now),
                    )
                    if edge_is_new:
                        report["edges_written"] += 1

            # Mark the row as indexed so only_unparsed can skip it next time.
            # node_type 'marker', never 'node': this is bookkeeping, not a
            # concept. As a 'node' it rendered in the admin graph and inflated
            # the node count (47 phantom nodes in production) and, being a
            # sentence-shaped label, fed the gap scan with noise.
            conn.execute(
                f"""
                INSERT INTO {GRAPH_TABLE}
                    (node_key, node_type, label, affinity, touch_count,
                     created_at, updated_at)
                VALUES (?, 'marker', ?, 0.0, 0, ?, ?)
                ON CONFLICT(node_key) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (marker, f"memória #{memory_id}", now, now),
            )
        conn.commit()
    return report


# ---------------------------------------------------------------------------
# Node / edge helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now().isoformat()


def _fingerprint(text: str) -> str:
    """A short content hash, so a reused row id is not mistaken for a seen row."""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


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

# ---------------------------------------------------------------------------
# THE GATE (T060). What is allowed to become a node.
#
# There was no gate. `upsert_node` accepts any non-empty string, so an
# utterance the user said out loud -- "Olá! O nome \"Bimby\" parece ser
# associado a gatos que têm nomes próprios..." -- became a concept, and the
# assistant then answered from it. T057 found the GMIF admitting conversation
# fragments through its own door; this is the second door, and cleaning the
# stored rows without adding this would only postpone the next cycle.
#
# Three signals, all measured against production before being written down.
# A fourth candidate -- "more than N words" -- was REJECTED during analysis
# because it also rejects "brechas e fissuras na lógica das plataformas
# digitais", which is a research finding, not speech. See
# aes/tickets/T060-portao-promoteabilidade-grafo.md.
#
# Radius, measured on the live graph: 4 of 190 nodes and 5 of 197 distinct edge
# endpoints. No false positive among the eight shortest surviving nodes
# (Tag, casa, gato, milk, nome, Deus, Porto, Vegan).
# ---------------------------------------------------------------------------

# No bare "a"/"the": "A saúde pública" and "The Body" are concepts, and a bare
# English article would reject both.
_GREETING_RE = re.compile(
    r"^(ol[aá]|ah|ei|opa|olha|bom|hey|hi|hello|ok|ent[aã]o|desculpa|"
    r"obrigad[oa]|yep|well|so)\b",
    re.IGNORECASE,
)

# Language and locale markers the tagger emits. These are not concepts: they
# describe the payload, not the world. An explicit list, deliberately, rather
# than "three characters or fewer", which would also swallow gato, casa, milk
# and nome -- all of which are real concepts in this graph.
LANGUAGE_TAGS = frozenset(
    {"pt", "en", "br", "pt-pt", "ptbr", "pt-br", "en-gb", "en-us", "es", "fr", "de"}
    # The dev/build markers the tagger also emits. Same category: metadata.
    | {"dev", "aoe"}
)

# ``_EDGE_RE`` is not anchored to a line (T058/B6: it swallows the `graph TD;`
# header), so the label arriving here can be ';\nolha, nao estou a vontade...' --
# the speech with punctuation glued to its face. Stripping leading non-word
# characters is defence in depth for that, not a fix for it: B6 is still owed,
# and until the regex is anchored the edge KEY it produces is still
# 'edge:;<junk>|<node>'.
_LEADING_JUNK_RE = re.compile(r"^[^\wÀ-ſ]+")


def is_speech_label(label: str) -> bool:
    """True when ``label`` is an utterance rather than a concept.

    Deliberately narrow, because the cost of a false positive is a real
    concept the owner can no longer see in the graph. Three signals:

    * trailing ellipsis -- how the LLM transcribed trailing speech
    * ends in ``!`` or ``?``
    * a greeting/interjection opener followed by more than one further word, so
      "Bom dia" survives and "Olha, não estou muito à vontade hoje..." does not

    "Ah, a chuva..." is rejected by the first rule even though it reads as a
    plausible conclusion rather than speech. That is a judgement call and it is
    one line: delete the ellipsis clause to reverse it.
    """
    text = _LEADING_JUNK_RE.sub("", (label or "").strip())
    if not text:
        return False
    if "..." in text or "…" in text:
        return True
    if text.endswith(("!", "?")):
        return True
    return bool(_GREETING_RE.match(text)) and len(text.split()) > 2


def is_language_tag(tag: str) -> bool:
    """True when a tag describes the payload rather than the world."""
    return (tag or "").strip().lower() in LANGUAGE_TAGS


def index_memory(memory: dict) -> list[str]:
    """Index a skill_memory JSON payload (tags + mermaid) into the graph.

    Tags become topic nodes; mermaid ``A --> B`` relations become edges.
    The first tag becomes the current topic, closing the loop with FlyBrain
    orientation/reward handling.

    Everything passes the gate in :func:`is_speech_label` and
    :func:`is_language_tag` first (T060). Both doors -- tags and mermaid edges --
    are gated, and so is the current-topic assignment: with the old code a
    rejected tag could still become the brain's ambient topic, which is what
    `apply_reward` credits on a 👍 that carries no text.

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
        if not isinstance(tag, str) or not tag.strip():
            continue
        if is_language_tag(tag) or is_speech_label(tag):
            continue
        keys.append(upsert_node(tag, affinity=novelty))

    mermaid = memory.get("mermaid") or ""
    if isinstance(mermaid, str):
        for node_a, label_a, node_b, label_b in _EDGE_RE.findall(mermaid):
            src = label_a.strip() if label_a.strip() else node_a.strip()
            dst = label_b.strip() if label_b.strip() else node_b.strip()
            if is_speech_label(src) or is_speech_label(dst):
                continue
            if is_language_tag(src) or is_language_tag(dst):
                continue
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
