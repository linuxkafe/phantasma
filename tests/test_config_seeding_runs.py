"""Does the seeding actually seed, and does it leave what was already set alone?

Structural tests check the code; this runs it. A missing row is the failure that
hid the temperature slider, and the way to be sure it is fixed is to add the
rows and see them appear.

Uses a temp database and monkeypatches the connection, so nothing here can touch
the production `config.db`.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.api import admin as admin_mod  # noqa: E402


@pytest.fixture()
def config_db(tmp_path, monkeypatch):
    """A `config` table with two rows and an empty third, isolated."""
    path = tmp_path / "config.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT, key TEXT UNIQUE, value TEXT,
            description TEXT, is_sensitive INTEGER DEFAULT 0
        );
        INSERT INTO config (category, key, value) VALUES ('Audio','WAKEWORD_MODELS','m.onnx');
        INSERT INTO config (category, key, value) VALUES ('Audio','GREETING_PATH','/g.wav');
        """
    )
    con.commit()
    con.close()

    def connect():
        # row_factory matters: the production accessor returns Rows and the code
        # indexes by column name.
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(admin_mod, "get_db_connection", connect)
    return path


def _keys(path):
    con = sqlite3.connect(str(path))
    try:
        return {r[0]: r[1] for r in con.execute("SELECT key, value FROM config")}
    finally:
        con.close()


def test_seeding_adds_the_rows_that_were_missing(config_db):
    before = _keys(config_db)
    assert "LLM_TEMPERATURE_CONVERSATION" not in before, (
        "a base de teste ja tem a linha, e o teste nao prova nada"
    )

    added = admin_mod.seed_missing_config_controls()
    after = _keys(config_db)

    assert added > 0, "nada foi inserido"
    for key in admin_mod.CONFIG_CONTROLS:
        assert key in after, f"{key} continua sem linha depois de semear"
    # A row that did not exist has no value of its own. The page shows the
    # effective value, and the resolver falls back to the default.
    assert after["LLM_TEMPERATURE_CONVERSATION"] == ""


def test_seeding_twice_changes_nothing(config_db):
    """A page reloads. The second read must not duplicate or fail."""
    first = admin_mod.seed_missing_config_controls()
    snapshot = _keys(config_db)
    second = admin_mod.seed_missing_config_controls()

    assert first > 0, "a primeira semeadura nao inseriu nada"
    assert second == 0, f"a segunda inseriu {second} linhas; deveria ser idempotente"
    assert _keys(config_db) == snapshot


def test_seeding_does_not_overwrite_a_saved_value(config_db):
    """The owner set a value. Opening the page must not reset it.

    This is the failure mode a REPLACE-based seed would introduce, and it is
    worse than the missing row: the control would appear to save and then
    silently revert on the next page load.
    """
    con = sqlite3.connect(config_db)
    con.execute(
        "UPDATE config SET value='0.25' WHERE key='WAKEWORD_MODELS'"
    )
    con.commit()
    con.close()

    admin_mod.seed_missing_config_controls()

    after = _keys(config_db)
    # WAKEWORD_MODELS is a row that EXISTED before the seed, holding a value the
    # owner had set. The new rows have no value of their own.
    assert after["WAKEWORD_MODELS"] == "0.25", (
        "um valor guardado antes da semeadura foi alterado: {0}".format(
            after["WAKEWORD_MODELS"]
        )
    )
    assert after["LLM_TEMPERATURE_CONVERSATION"] == ""


def test_the_page_renders_the_slider_after_seeding(config_db):
    """The end of the whole chain: registry -> table -> page.

    Everything else checked the pieces. This asks the reading function, which is
    what the browser calls, and looks for the key that was invisible.
    """
    by_category = admin_mod.get_configs_by_category()
    rendered = {
        row["key"]
        for rows in by_category.values()
        for row in rows
        if isinstance(row, dict)
    }
    assert "LLM_TEMPERATURE_CONVERSATION" in rendered, (
        "a chave continua a nao aparecer na pagina depois de semear -- o que "
        "significa que o problema nao era a linha em falta"
    )
    assert "LLM_TEMPERATURE_FACTUAL" in rendered


def test_a_read_only_database_does_not_break_the_read(config_db, monkeypatch):
    """A page that used to render must keep rendering.

    The seeding writes. Before it, reading worked on a read-only database and
    showed whatever had rows -- which is every control except the new ones. After
    it, an uncaught write failure would turn a working page into a 500.

    Set up as a read-only file so `get_db_connection` succeeds and the INSERT is
    what fails, which is the case that matters.
    """
    # NOT seeded first. Seeding first made every row exist, so the INSERT had
    # nothing to do and raised nothing -- and the test passed with the `except`
    # replaced by `raise`, which is the whole defect it exists to catch. The
    # read-only case is only real when there is a row MISSING and writing it is
    # what fails.
    readonly = str(config_db) + "-ro"
    con = sqlite3.connect(str(config_db))
    con.execute("VACUUM INTO ?", (readonly,))
    con.close()
    os.chmod(readonly, 0o444)
    try:
        def connect_ro():
            conn = sqlite3.connect(readonly)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(admin_mod, "get_db_connection", connect_ro)
        # There IS a missing row here, so the INSERT is attempted and fails. Must
        # not raise, and the controls that have rows still render.
        assert "LLM_TEMPERATURE_CONVERSATION" not in _keys(config_db), (
            "a base de teste ja tem a linha, e nada vai tentar escrever"
        )
        by_category = admin_mod.get_configs_by_category()
        rendered = sum(len(v) for v in by_category.values())
        assert rendered > 0, (
            "a pagina ficou vazia em vez de parcial: uma falha de escrita "
            "transformou uma pagina que funcionava num 500"
        )
    finally:
        os.chmod(readonly, 0o644)
