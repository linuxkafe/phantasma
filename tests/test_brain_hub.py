"""brain_hub shows all four brain subsystems on one screen.

The point of the hub was to stop four separate page loads. That is easy to
claim in a docstring and easy to break silently: a region can render its
heading, show an empty table, and no test notices. These assert that the
values the hub claims to unify actually come from the database.

The editor it hosts is covered in test_knowledge_editor_routes.py; this
module is about the hub's own claim.
"""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import admin as admin_mod  # noqa: E402


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """A brain.db with one known fact in each subsystem."""
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    db = tmp_path / "brain.db"
    con = sqlite3.connect(db)
    con.executescript(schema)
    con.execute(
        "INSERT INTO memories (id, timestamp, text) VALUES"
        " (7, '2026-01-01', 'a memoria marcadora do hub')"
    )
    con.execute(
        "INSERT INTO memory_graph"
        " (id, node_key, node_type, label, source, affinity, weight,"
        "  touch_count, created_at, updated_at)"
        " VALUES (1, 'node:marcador', 'node', 'no marcador do hub',"
        " 'memory', 3.0, 1.0, 1, '2026-01-01', '2026-01-01')"
    )
    con.execute(
        "INSERT INTO flybrain_state (id, schema_version, data, updated_at)"
        " VALUES (1, 1, ?, '2026-01-01')"
    , (sqlite3.Binary(
        b'{"schema_version": 1, "steps": 3, "ring": {"orientation_deg": 12}}'
    ),))
    con.execute(
        "INSERT INTO topic_state (id, current_key, updated_at)"
        " VALUES (1, 'marcador', '2026-01-01')"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(admin_mod, "BRAIN_DB_PATH", Path(db))
    from src.brain import graph_edit
    graph_edit.ensure_schema(str(db))
    return db


@pytest.fixture
def get_hub(hub, monkeypatch):
    def _get():
        from src.api.routes import create_app

        app = create_app()
        app.config["TESTING"] = True
        ident = {"role": "admin", "email": "owner@example.invalid"}
        monkeypatch.setattr(admin_mod, "_current_user_data", lambda: ident)
        monkeypatch.setattr(admin_mod, "_current_user", lambda: "owner@example.invalid")
        monkeypatch.setattr(admin_mod, "_bypass_or_none", lambda: ident)
        return app.test_client().get("/admin/brain").get_data(as_text=True)

    return _get


def test_hub_renders(get_hub):
    assert get_hub(), "/admin/brain returned nothing"


def test_hub_shows_the_memory_it_claims_to_unify(get_hub):
    """A region that renders its heading and an empty table is not a unified
    view of anything."""
    body = get_hub()
    assert "a memoria marcadora do hub" in body, (
        "the memory is in the database and missing from the page"
    )


def test_hub_shows_the_node_and_its_affinity(get_hub):
    body = get_hub()
    assert "no marcador do hub" in body
    assert "3.0" in body, "the node's affinity is not shown"


def test_hub_shows_the_topic(get_hub):
    assert "marcador" in get_hub()


def test_hub_offers_the_sleep_cycle(get_hub):
    """The dream is run from here, so the control has to be on this page."""
    body = get_hub()
    assert "data-sleep" in body, "no sleep/dream control on /admin/brain"
    assert "/admin/brain/sleep" in body, "the sleep endpoint is not referenced"


def test_hub_reports_an_empty_brain_honestly(hub, monkeypatch):
    """The inverse case: with no rows the page must not invent them. An empty
    table plus a "nothing here" note is correct; a table with a fake row is
    not."""
    con = sqlite3.connect(hub)
    con.execute("DELETE FROM memories")
    con.execute("DELETE FROM memory_graph")
    con.execute("DELETE FROM flybrain_state")
    con.execute("DELETE FROM topic_state")
    con.commit()
    con.close()

    from src.api.routes import create_app

    app = create_app()
    app.config["TESTING"] = True
    ident = {"role": "admin", "email": "owner@example.invalid"}
    monkeypatch.setattr(admin_mod, "_current_user_data", lambda: ident)
    monkeypatch.setattr(admin_mod, "_current_user", lambda: "owner@example.invalid")
    monkeypatch.setattr(admin_mod, "_bypass_or_none", lambda: ident)
    body = app.test_client().get("/admin/brain").get_data(as_text=True)
    assert "a memoria marcadora do hub" not in body
