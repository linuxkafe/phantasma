"""T046 -- resolving a dangling edge endpoint, and editing a node's weight.

The behaviours these lock down are the ones a silent failure would hide:
a "resolved" ref that the next read still counts, a mutation with no audit
row, a weight written straight from the request, and a relink that invents the
node it was asked to point at.

Every assertion is made against `build_graph_from_db`, the SAME function the
`/api/memory/graph` endpoint calls, so a test cannot pass by re-implementing
the read path more leniently than production does.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.api.memory_graph import build_graph_from_db
from src.brain.graph_edit import EditError, ensure_schema, resolve_dangling, update_node

EDGE = "edge:capitalismo tardio|depende de plataformas digitais"
NODE_SRC = "node:capitalismo tardio"
NODE_TGT = "node:plataformas digitais"


@pytest.fixture()
def brain(tmp_path):
    """A brain.db with one edge whose target label has no matching node.

    Reproduces the real production shape found on 2026-09-27: the edge says
    "Depende de Plataformas Digitais" while the node row says "Plataformas
    Digitais", so the reader counts the endpoint as dangling.
    """
    db = tmp_path / "brain.db"
    con = sqlite3.connect(db)
    # The real DDL, from the same file the rest of the suite uses. A
    # hand-written CREATE TABLE here drifted from the reader and failed with
    # "no such column: timestamp" -- the fixture, not the code, was wrong.
    schema_sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    con.executescript(
        schema_sql
        + """
        INSERT INTO memory_graph
            (node_key,node_type,label,source,target,affinity,weight,
             touch_count,created_at,updated_at)
        VALUES
            ('node:capitalismo tardio','node','Capitalismo Tardio',NULL,NULL,0,1,0,
             '2026-01-01T00:00:00Z','2026-01-01T00:00:00Z'),
            ('node:plataformas digitais','node','Plataformas Digitais',NULL,NULL,0,1,0,
             '2026-01-01T00:00:00Z','2026-01-01T00:00:00Z'),
            ('edge:capitalismo tardio|depende de plataformas digitais','edge',
             'Capitalismo Tardio -> Depende de Plataformas Digitais',
             'Capitalismo Tardio','Depende de Plataformas Digitais',0.15,0.77,0,
             '2026-01-01T00:00:00Z','2026-01-01T00:00:00Z');
        """
    )
    con.commit()
    # graph_edit_audit is created by ensure_schema(), not by schema.sql --
    # so the fixture must run it too, or the audit assertions read a table
    # that does not exist yet.
    ensure_schema(con)
    con.commit()
    con.close()
    return db


def refs(db):
    """Unresolved count + labels, read exactly as the endpoint reads them."""
    payload = build_graph_from_db(db)
    orphans = [n["label"] for n in payload["nodes"] if n.get("unresolved")]
    return payload["stats"]["unresolved_edges"], orphans


def audit_rows(db):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT op, target, before_json, after_json FROM graph_edit_audit ORDER BY rowid"
        ).fetchall()
    finally:
        con.close()


# --- the payload tells the UI what is pending ---------------------------------


def test_dangling_node_carries_edge_and_side(brain):
    """Without edge_key+side the UI cannot know what to fix (T046 AC1).

    This was the actual blocker: the synthetic node had no reference to its
    edge, so any "resolve" button built on the old payload was decorative.
    """
    payload = build_graph_from_db(brain)
    orphans = [n for n in payload["nodes"] if n.get("unresolved")]
    assert len(orphans) == 1
    assert orphans[0]["dangling"] == [{"edge_key": EDGE, "side": "target"}]


def test_a_resolved_node_is_an_edge_ending_in_a_dangling_label(brain):
    before, labels = refs(brain)
    assert before == 1 and labels == ["Depende de Plataformas Digitais"]


# --- relink ------------------------------------------------------------------


def test_relink_actually_lowers_the_unresolved_count(brain):
    after_count, orphans = None, None
    out = resolve_dangling(brain, EDGE, "target", "relink", target_label="Plataformas Digitais")
    after_count, orphans = refs(brain)
    assert after_count == 0, "relink reported success but the reader still dangles"
    assert orphans == []
    assert out["from"] == "Depende de Plataformas Digitais"
    assert out["to"] == "Plataformas Digitais"
    assert out["target_node_key"] == NODE_TGT


def test_relink_writes_an_audit_row(brain):
    resolve_dangling(
        brain,
        EDGE,
        "target",
        "relink",
        target_label="Plataformas Digitais",
        actor="tester",
        rationale="mesmo conceito",
    )
    rows = audit_rows(brain)
    assert len(rows) == 1
    op, target, before_json, after_json = rows[0]
    assert op == "edge.relink" and target == EDGE
    assert "Depende de Plataformas Digitais" in before_json
    assert "Plataformas Digitais" in after_json
    assert "mesmo conceito" in after_json


def test_relink_matches_case_and_accents_like_the_reader(brain):
    """The resolver must agree with normalise(), or "resolved" is a lie."""
    resolve_dangling(brain, EDGE, "target", "relink", target_label="PLATAFORMAS digitais")
    assert refs(brain)[0] == 0


def test_relink_refuses_a_label_that_is_not_a_stored_node(brain):
    """Never invent the target: a typo must not become a new node."""
    with pytest.raises(EditError, match="no node with label"):
        resolve_dangling(brain, EDGE, "target", "relink", target_label="Nao Existe")
    assert refs(brain)[0] == 1, "a refused relink must leave the ref in place"
    assert audit_rows(brain) == [], "a refused relink must not write an audit row"
    con = sqlite3.connect(brain)
    try:
        n = con.execute("SELECT COUNT(*) FROM memory_graph").fetchone()[0]
    finally:
        con.close()
    assert n == 3, "a refused relink must not create a node"


def test_relink_refuses_a_label_that_is_not_stored_even_if_it_is_the_endpoint(brain):
    """The endpoint's own text is not a node, so the lookup guard fires first.

    Recorded rather than reshaped: the guard order (is it a node? -> is it the
    same endpoint?) is intentional, because "there is no node called X" is more
    useful to an operator than "you pointed at yourself".
    """
    with pytest.raises(EditError, match="no node with label"):
        resolve_dangling(
            brain,
            EDGE,
            "target",
            "relink",
            target_label="Depende de Plataformas Digitais",
        )


def test_relink_refuses_to_point_at_the_endpoint_it_already_is(brain):
    """Both a stored node AND the same endpoint: nothing left to resolve."""
    with pytest.raises(EditError, match="already this endpoint"):
        resolve_dangling(
            brain,
            EDGE,
            "source",
            "relink",
            target_label="Capitalismo Tardio",
        )


# --- promote -----------------------------------------------------------------


def test_promote_with_the_existing_label_resolves_the_ref(brain):
    out = resolve_dangling(brain, EDGE, "target", "promote")
    assert out["to"] == "Depende de Plataformas Digitais"
    assert refs(brain)[0] == 0, "promote created a node but the ref still dangles"


def test_promote_with_a_new_label_relinks_the_edge_too(brain):
    """A promote with a different label must still resolve the ref.

    Creating the node alone would leave the edge pointing at the old text, so
    `unresolved_edges` would not move and the operator would be told a ref was
    resolved when it was not. This was a real no-op found in review.
    """
    resolve_dangling(brain, EDGE, "target", "promote", target_label="Marca Registada")
    assert refs(brain)[0] == 0
    con = sqlite3.connect(brain)
    try:
        target = con.execute(
            "SELECT target FROM memory_graph WHERE node_key = ?", (EDGE,)
        ).fetchone()[0]
    finally:
        con.close()
    assert target == "Marca Registada"


def test_promote_refuses_when_a_node_already_owns_that_label(brain):
    with pytest.raises(EditError, match="already exists"):
        resolve_dangling(brain, EDGE, "target", "promote", target_label="Plataformas Digitais")
    assert audit_rows(brain) == []


def test_promote_writes_an_audit_row(brain):
    resolve_dangling(brain, EDGE, "target", "promote", actor="tester")
    rows = audit_rows(brain)
    assert len(rows) == 1 and rows[0][0] == "node.promote"
    assert "node:" in rows[0][1]


# --- rejected input ----------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(side="lado", action="relink"), "side must be"),
        (dict(side="target", action="auto"), "action must be"),
        (dict(side="target", action="relink", target_label=None), "target_label"),
        # source is already "Capitalismo Tardio" AND that label is a stored node,
        # so this is the "nothing to resolve" guard rather than the
        # "no such node" guard.
        (
            dict(side="source", action="relink", target_label="Capitalismo Tardio"),
            "already this endpoint",
        ),
    ],
)
def test_invalid_resolve_requests_are_refused_without_writing(brain, kwargs, match):
    with pytest.raises(EditError, match=match):
        resolve_dangling(brain, EDGE, **kwargs)
    assert audit_rows(brain) == []
    assert refs(brain)[0] == 1


def test_unknown_edge_is_refused(brain):
    with pytest.raises(EditError, match="no such edge"):
        resolve_dangling(brain, "edge:nao-existe", "target", "relink", "X")


# --- node weight -------------------------------------------------------------


def test_node_weight_is_written(brain):
    out = update_node(brain, NODE_SRC, {"weight": 0.42})
    assert out["weight"] == pytest.approx(0.42)


@pytest.mark.parametrize("given, stored", [(5.0, 1.0), (-9.0, -1.0), (1.0, 1.0), (-1.0, -1.0)])
def test_node_weight_is_clamped_like_the_edges(brain, given, stored):
    out = update_node(brain, NODE_SRC, {"weight": given})
    assert out["weight"] == pytest.approx(stored)


def test_node_weight_rejects_a_non_number(brain):
    with pytest.raises(EditError, match="not a number"):
        update_node(brain, NODE_SRC, {"weight": "alto"})


def test_node_weight_edit_leaves_the_other_fields_alone(brain):
    con = sqlite3.connect(brain)
    try:
        before = con.execute(
            "SELECT label, affinity, touch_count FROM memory_graph WHERE node_key = ?",
            (NODE_SRC,),
        ).fetchone()
    finally:
        con.close()
    update_node(brain, NODE_SRC, {"weight": 0.5})
    con = sqlite3.connect(brain)
    try:
        after = con.execute(
            "SELECT label, affinity, touch_count FROM memory_graph WHERE node_key = ?",
            (NODE_SRC,),
        ).fetchone()
    finally:
        con.close()
    assert after == before


def test_node_weight_is_audited(brain):
    update_node(brain, NODE_SRC, {"weight": 0.5}, actor="tester")
    rows = audit_rows(brain)
    assert len(rows) == 1 and rows[0][0] == "node.update"


def test_node_weight_cannot_be_written_through_an_unlisted_field(brain):
    with pytest.raises(EditError, match="not editable"):
        update_node(brain, NODE_SRC, {"gmif_validation_type": "logical"})


# --- both ends can dangle ----------------------------------------------------


def test_both_ends_dangling_is_resolved_one_side_at_a_time(brain):
    """side is mandatory precisely because an edge can dangle at both ends."""
    con = sqlite3.connect(brain)
    try:
        con.execute(
            "INSERT INTO memory_graph (node_key,node_type,label,source,target,"
            "affinity,weight,touch_count,created_at,updated_at) VALUES "
            "('edge:outro|novo','edge','Marca -> Alvo','Marca Nao Registrada',"
            "'Alvo Nao Registrado',0,1,0,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
        )
        con.commit()
    finally:
        con.close()
    # 1 from the fixture edge + 2 from the new one (both its ends dangle).
    assert refs(brain)[0] == 3
    resolve_dangling(brain, "edge:outro|novo", "source", "promote", target_label="Marca Registada")
    assert refs(brain)[0] == 2, "resolving one end must leave the other counted"
    resolve_dangling(brain, "edge:outro|novo", "target", "promote", target_label="Alvo Registado")
    assert refs(brain)[0] == 1, "only the fixture's own ref should be left"
    resolve_dangling(brain, EDGE, "target", "relink", target_label="Plataformas Digitais")
    assert refs(brain)[0] == 0
