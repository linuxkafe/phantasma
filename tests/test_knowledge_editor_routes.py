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


def test_the_knowledge_page_is_not_the_env_editor(client):
    """/admin/brain/knowledge used to render the Configuração (.env) page.

    ADMIN_TEMPLATE -- the base every derived page is built on -- was a verbatim
    copy of the .env editor: same <title>, same heading, the whole
    CHAVE=VALOR textarea and the {{ env }} it needs. The knowledge editor was
    ADMIN_TEMPLATE + its own markup, so the env form came first and the memory
    editor was appended below it.

    So the owner's own report -- opening /admin/brain/knowledge?mem_id=85 and
    being shown "Configuração (.env)", "Edite as variáveis de ambiente" and a
    textarea for CHAVE=VALOR -- was not a misclick or a bad id. The page they
    were trying to reach had the wrong page on it. Answered 200 the whole time,
    so nothing looked broken from the outside.

    Asserted on the absence of the env form, because its presence is what made
    the memory unreachable.
    """
    body = client.get("/admin/brain/knowledge?mem_id=1").get_data(as_text=True)

    assert 'name="env"' not in body, (
        "the knowledge page is rendering the .env editor: it inherits it from "
        "ADMIN_TEMPLATE"
    )
    assert "variáveis de ambiente" not in body
    assert "Configuração (.env)" not in body
    # And it is still the memory editor it claims to be.
    assert "Editar conhecimento" in body, body[:400]
    assert 'name="text"' in body


def test_opening_a_memory_by_id_shows_that_memory(client):
    """The reason the owner used ?mem_id= in the first place.

    A silent failure here is what made the env editor look like the feature:
    the id was in the URL and the page answered 200 either way.
    """
    body = client.get("/admin/brain/knowledge?mem_id=1").get_data(as_text=True)
    assert "o gato chama-se Bimby" in body, (
        "mem_id=1 did not put that memory in the editor"
    )


def test_the_env_editor_is_still_its_own_page(client):
    """Splitting the shell out must not cost /admin/env its editor.

    The route is /admin/env, not /admin/config: /admin/config is the separate
    categorized-settings page and has its own template.
    """
    body = client.get("/admin/env").get_data(as_text=True)
    assert 'name="env"' in body, "the .env editor lost its textarea"
    assert "Configuração (.env)" in body


def test_no_admin_page_leaks_the_env_form_unless_it_is_the_env_editor(client):
    """The bug was structural: a base template carrying another page's body.

    ADMIN_TEMPLATE is used by two pages. Neither is the env editor, so neither
    may contain its form, and both must still open and close one document.

    Counted with a regex rather than `body.count("<html")`: every template here
    carries the WCAG comment "lang em <html> e WCAG 3.1.1", so a naive count
    sees two and reports a nesting bug that is not there.
    """
    import re

    for path in ("/admin/brain/knowledge", "/admin/persona"):
        body = client.get(path).get_data(as_text=True)
        assert 'name="env"' not in body, f"{path} renders the .env editor"
        # Strip HTML comments first: every template here carries the WCAG note
        # "lang em <html> e WCAG 3.1.1", which is text, not a tag.
        stripped = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
        assert len(re.findall(r"<html[ >]", stripped)) == 1, (
            f"{path} nests or loses the document"
        )
        assert body.count("</html>") == 1, f"{path} never closes the document"
