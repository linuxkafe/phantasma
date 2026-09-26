"""
GMIF Classifier for Memory Graph relationships.

Applies Graph Memory Inference Framework (GMIF) classifications to
nodes and edges in the memory graph, following the same epistemic
rigour as shadow document classification.

Classification levels (M1-M5):
- M1: Raw observation / extracted pattern
- M2: Structured relationship (part-of, similar-to, related-to)
- M3: Logical implication (depends-on, requires, entails)
- M4: Causal mechanism (causes, triggers, produces)
- M5: Mechanistic explanation with formal proof
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ValidationType(Enum):
    LOGICAL = "logical"
    EXTERNAL = "external"
    HUMAN = "human"


class GMIFLevel(Enum):
    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    M5 = "M5"


class NodeGMIFType(Enum):
    CONCEPT = "concept"
    ENTITY = "entity"
    EVENT = "event"
    PROPERTY = "property"


@dataclass
class EdgeClassification:
    """GMIF classification for a graph edge."""

    logical_form: str
    validation_type: ValidationType
    extraction_confidence: float
    validation_confidence: float
    source_chunks: str
    gmif_level: GMIFLevel
    classified_at: str
    classified_by: str


@dataclass
class NodeClassification:
    """GMIF classification for a graph node."""

    gmif_type: NodeGMIFType
    confidence: float
    evidence: str


# Pattern-based classification rules (ordered by specificity)
EDGE_PATTERNS = [
    # (pattern, template, vtype, level, ext_conf, val_conf)
    (
        r"depende de",
        "(assert (implies (has_state {source}) (has_state {target})))",
        ValidationType.LOGICAL,
        GMIFLevel.M3,
        0.85,
        0.75,
    ),
    (
        r"causa|provoca|gera|produz",
        "(assert (=> (event {source}) (event {target})))",
        ValidationType.LOGICAL,
        GMIFLevel.M4,
        0.80,
        0.70,
    ),
    (
        r"parte de|cont[eé]m|inclui",
        "(assert (subset {source} {target}))",
        ValidationType.LOGICAL,
        GMIFLevel.M2,
        0.90,
        0.85,
    ),
    (
        r"similar a|relacionado a|associado a",
        "(assert (similar {source} {target}))",
        ValidationType.EXTERNAL,
        GMIFLevel.M2,
        0.70,
        0.60,
    ),
    (
        r"contradiz|op[oô]e-se a|incompat[ií]vel com",
        "(assert (not (and {source} {target})))",
        ValidationType.LOGICAL,
        GMIFLevel.M3,
        0.85,
        0.75,
    ),
    (
        r"requer|necessita de|precisa de",
        "(assert (implies (has_state {target}) (has_state {source})))",
        ValidationType.LOGICAL,
        GMIFLevel.M3,
        0.85,
        0.75,
    ),
]


def classify_edge(label: str, source: str, target: str) -> EdgeClassification:
    """Classify an edge based on its label using pattern matching."""
    label_lower = label.lower()

    for pattern, template, vtype, level, ext_conf, val_conf in EDGE_PATTERNS:
        if re.search(pattern, label_lower):
            logical_form = template.format(source=source, target=target)
            source_chunks = json.dumps(
                [
                    {
                        "type": "edge_label_pattern",
                        "pattern": pattern,
                        "matched_label": label,
                        "source": source,
                        "target": target,
                    }
                ]
            )
            return EdgeClassification(
                logical_form=logical_form,
                validation_type=vtype,
                extraction_confidence=ext_conf,
                validation_confidence=val_conf,
                source_chunks=source_chunks,
                gmif_level=level,
                classified_at=datetime.now().isoformat(),
                classified_by="gmif_classifier_v1",
            )

    # Default fallback
    source_chunks = json.dumps(
        [
            {
                "type": "edge_label_fallback",
                "label": label,
                "source": source,
                "target": target,
            }
        ]
    )
    return EdgeClassification(
        logical_form=f"(assert (related {source} {target}))",
        validation_type=ValidationType.EXTERNAL,
        extraction_confidence=0.60,
        validation_confidence=0.50,
        source_chunks=source_chunks,
        gmif_level=GMIFLevel.M1,
        classified_at=datetime.now().isoformat(),
        classified_by="gmif_classifier_v1",
    )


def classify_node(node_key: str, label: str) -> NodeClassification:
    """Classify a node based on its key and label."""
    key_lower = node_key.lower()
    label_lower = label.lower()

    if "conceito" in key_lower or "concept" in key_lower:
        return NodeClassification(
            gmif_type=NodeGMIFType.CONCEPT,
            confidence=0.90,
            evidence=json.dumps(
                {"node_key": node_key, "label": label, "basis": "key_pattern"}
            ),
        )
    elif "entidade" in key_lower or "entity" in key_lower:
        return NodeClassification(
            gmif_type=NodeGMIFType.ENTITY,
            confidence=0.85,
            evidence=json.dumps(
                {"node_key": node_key, "label": label, "basis": "key_pattern"}
            ),
        )
    elif "evento" in key_lower or "event" in key_lower:
        return NodeClassification(
            gmif_type=NodeGMIFType.EVENT,
            confidence=0.85,
            evidence=json.dumps(
                {"node_key": node_key, "label": label, "basis": "key_pattern"}
            ),
        )
    elif any(
        prop in label_lower
        for prop in ["propriedade", "atributo", "qualidade", "característica"]
    ):
        return NodeClassification(
            gmif_type=NodeGMIFType.PROPERTY,
            confidence=0.80,
            evidence=json.dumps(
                {"node_key": node_key, "label": label, "basis": "label_semantics"}
            ),
        )
    else:
        return NodeClassification(
            gmif_type=NodeGMIFType.CONCEPT,
            confidence=0.80,
            evidence=json.dumps(
                {"node_key": node_key, "label": label, "basis": "default"}
            ),
        )


def apply_gmif_to_edge(conn, edge_id: int) -> bool:
    """Apply GMIF classification to a specific edge in the database."""
    cur = conn.cursor()
    row = cur.execute(
        "SELECT * FROM memory_graph WHERE id = ? AND node_type = 'edge'", (edge_id,)
    ).fetchone()
    if not row:
        return False

    classification = classify_edge(
        row["label"], row["source"] or "", row["target"] or ""
    )

    cur.execute(
        """
        UPDATE memory_graph SET
            gmif_logical_form = ?,
            gmif_validation_type = ?,
            gmif_extraction_confidence = ?,
            gmif_validation_confidence = ?,
            gmif_source_chunks = ?,
            gmif_level = ?,
            gmif_classified_at = ?,
            gmif_classified_by = ?
        WHERE id = ?
    """,
        (
            classification.logical_form,
            classification.validation_type.value,
            classification.extraction_confidence,
            classification.validation_confidence,
            classification.source_chunks,
            classification.gmif_level.value,
            classification.classified_at,
            classification.classified_by,
            edge_id,
        ),
    )
    conn.commit()
    return True


def apply_gmif_to_node(conn, node_id: int) -> bool:
    """Apply GMIF classification to a specific node in the database."""
    cur = conn.cursor()
    row = cur.execute(
        "SELECT * FROM memory_graph WHERE id = ? AND node_type = 'node'", (node_id,)
    ).fetchone()
    if not row:
        return False

    classification = classify_node(row["node_key"], row["label"])

    cur.execute(
        """
        UPDATE memory_graph SET
            node_gmif_type = ?,
            node_gmif_confidence = ?,
            node_gmif_evidence = ?
        WHERE id = ?
    """,
        (
            classification.gmif_type.value,
            classification.confidence,
            classification.evidence,
            node_id,
        ),
    )
    conn.commit()
    return True


def classify_all_edges(conn) -> int:
    """Classify all unclassified edges in the graph."""
    cur = conn.cursor()
    edges = cur.execute(
        "SELECT id FROM memory_graph WHERE node_type = 'edge' AND gmif_level IS NULL"
    ).fetchall()
    count = 0
    for (edge_id,) in edges:
        if apply_gmif_to_edge(conn, edge_id):
            count += 1
    return count


def classify_all_nodes(conn) -> int:
    """Classify all unclassified nodes in the graph."""
    cur = conn.cursor()
    nodes = cur.execute(
        "SELECT id FROM memory_graph "
        "WHERE node_type = 'node' AND node_gmif_type IS NULL"
    ).fetchall()
    count = 0
    for (node_id,) in nodes:
        if apply_gmif_to_node(conn, node_id):
            count += 1
    return count


def get_gmif_stats(conn) -> dict:
    """Get statistics about GMIF coverage."""
    cur = conn.cursor()
    stats = {}
    stats["total_nodes"] = cur.execute(
        "SELECT COUNT(*) FROM memory_graph WHERE node_type = 'node'"
    ).fetchone()[0]
    stats["classified_nodes"] = cur.execute(
        "SELECT COUNT(*) FROM memory_graph "
        "WHERE node_type = 'node' AND node_gmif_type IS NOT NULL"
    ).fetchone()[0]
    stats["total_edges"] = cur.execute(
        "SELECT COUNT(*) FROM memory_graph WHERE node_type = 'edge'"
    ).fetchone()[0]
    stats["classified_edges"] = cur.execute(
        "SELECT COUNT(*) FROM memory_graph "
        "WHERE node_type = 'edge' AND gmif_level IS NOT NULL"
    ).fetchone()[0]
    stats["logical_edges"] = cur.execute(
        "SELECT COUNT(*) FROM memory_graph "
        "WHERE node_type = 'edge' AND gmif_validation_type = 'logical'"
    ).fetchone()[0]
    stats["external_edges"] = cur.execute(
        "SELECT COUNT(*) FROM memory_graph "
        "WHERE node_type = 'edge' AND gmif_validation_type = 'external'"
    ).fetchone()[0]
    stats["level_distribution"] = dict(
        cur.execute(
            "SELECT gmif_level, COUNT(*) "
            "FROM memory_graph "
            "WHERE node_type = 'edge' AND gmif_level IS NOT NULL "
            "GROUP BY gmif_level"
        ).fetchall()
    )
    return stats


if __name__ == "__main__":
    import sqlite3

    conn = sqlite3.connect("data/brain.db")
    conn.row_factory = sqlite3.Row

    print("=== GMIF Classification Stats ===")
    stats = get_gmif_stats(conn)
    for k, v in stats.items():
        print(f"  {k}: {v}")

    new_edges = classify_all_edges(conn)
    new_nodes = classify_all_nodes(conn)
    print(f"\nNewly classified: {new_edges} edges, {new_nodes} nodes")

    stats = get_gmif_stats(conn)
    for k, v in stats.items():
        print(f"  {k}: {v}")
