"""The knowledge editor on /admin/brain must actually work when you press the
button.

The first version of this editor rendered a complete form -- textarea, node
buttons, 60 memory options -- and a POST returned 405 Method Not Allowed,
because the route was declared `@admin_bp.route("/brain")` with no `methods`.
Every earlier test here read the source as text and asserted that a handler
mentioned the action name. A string is not a route. This module drives the
real route instead.
"""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import admin as admin_mod  # noqa: E402
from src.brain import graph_edit  # noqa: E402


@pytest.fixture
def brain(tmp_path, monkeypatch):
    """An isolated brain.db, so no test ever writes the owner's data."""
    db = tmp_path / "brain.db"
    con = sqlite3.connect(db)
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    con.executescript(schema)
    con.execute(
        "INSERT INTO memories (id, timestamp, text) VALUES"
        " (1, '2026-01-01', 'o gato chama-se Bimby'),"
        " (2, '2026-01-02', '{\"tags\": [\"Vegan\"]}')"
    )
    con.execute(
        "INSERT INTO memory_graph"
        " (id, node_key, node_type, label, source, affinity, weight,"
        "  touch_count, created_at, updated_at)"
        " VALUES (71, 'node:resposta', 'node',"
        " 'Olá! O nome Bimby parece associado a gatos', 'assistant',"
        "  0.0, 1.0, 1, '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()
    # graph_edit_audit is created at runtime, not in schema.sql. Asking the
    # module that owns it is more honest than copying its DDL here.
    graph_edit.ensure_schema(str(db))
    # BRAIN_DB_PATH is bound at import time, so patching the attribute name
    # on the module is not enough -- the helpers close over the constant.
    monkeypatch.setattr(admin_mod, "BRAIN_DB_PATH", Path(db))
    return db


@pytest.fixture
def client(brain, monkeypatch):
    """A test client that passes the admin gates without a real session."""

    from src.api.routes import create_app

    app = create_app()
    app.config["TESTING"] = True
    ident = {"role": "admin", "email": "owner@example.invalid"}
    monkeypatch.setattr(admin_mod, "_current_user_data", lambda: ident)
    monkeypatch.setattr(admin_mod, "_current_user", lambda: "owner@example.invalid")
    monkeypatch.setattr(admin_mod, "_bypass_or_none", lambda: ident)
    with app.test_client() as c:
        yield c


def test_brain_renders_the_editor(client):
    body = client.get("/admin/memory").get_data(as_text=True)
    for field in ('name="node_id"', 'value="delete_node"', 'name="text"'):
        assert field in body, f"/admin/brain is missing {field}"


def test_brain_accepts_post(client):
    """The 405 that made the whole editor decorative.

    A form rendered by a GET-only view is indistinguishable, to the owner,
    from a working one: the buttons look identical and do nothing.
    """
    resp = client.post("/admin/memory", data={"op": "delete_node", "node_id": "71"})
    assert resp.status_code in (302, 303), (
        f"POST /admin/brain returned {resp.status_code}; the editor is "
        f"decorative again"
    )


def test_deleting_a_node_works_and_is_audited(client, brain):
    client.post("/admin/memory", data={"op": "delete_node", "node_id": "71"})
    con = sqlite3.connect(brain)
    left = con.execute("SELECT COUNT(*) FROM memory_graph WHERE id=71").fetchone()[0]
    audit = con.execute(
        "SELECT op, actor FROM graph_edit_audit WHERE op='node.delete'"
    ).fetchall()
    con.close()
    assert left == 0, "the node survived its own delete button"
    assert audit, "an edit with no audit row is an edit nobody can account for"


def test_editing_a_memory_persists_the_new_text(client, brain):
    client.post("/admin/memory", data={
        "op": "save_memory", "mem_id": "1", "text": "o gato chama-se Bimby, corrigido",
    })
    con = sqlite3.connect(brain)
    text = con.execute("SELECT text FROM memories WHERE id=1").fetchone()[0]
    con.close()
    assert "corrigido" in text


def test_empty_memory_text_is_refused(client, brain):
    client.post("/admin/memory", data={"op": "save_memory", "mem_id": "1", "text": "  "})
    con = sqlite3.connect(brain)
    text = con.execute("SELECT text FROM memories WHERE id=1").fetchone()[0]
    con.close()
    assert text == "o gato chama-se Bimby", "an empty save wiped a memory"


def test_menu_no_longer_offers_persona_or_knowledge(client):
    """The owner asked for these removed from the hamburger: the persona
    lives in /admin/config and the knowledge editor in /admin/brain."""
    body = client.get("/admin/brain").get_data(as_text=True)
    nav = body.split("</nav>")[0]
    assert "/admin/persona" not in nav, "persona is back in the menu"
    assert "/admin/brain/knowledge" not in nav, "knowledge editor is back in the menu"
