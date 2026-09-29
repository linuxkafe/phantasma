"""Shared fixtures: isolate the admin blueprint from production data.

Why this exists
--------------
``src/api/admin.py`` resolves its database paths at import time. Those paths
used to be hardcoded to ``/opt/phantasma/data/``, so the test suite silently
reached the PRODUCTION database: it authenticated as the real admin and
rendered admin pages against live data. Making the paths host-relative exposed
the dependency, and the correct fix is isolation -- not pointing the tests back
at production.

The schema is not written here. It is loaded from ``tests/schema.sql``, which is
mirrored from the real databases. A hand-written fixture guessed column names
(including a column-per-scalar ``flybrain_state`` when the real table stores a
single serialised BLOB) and therefore asserted against a schema that does not
exist in production. Regenerate that file from sqlite_master when a table drifts.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from src.api import localauth  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SCHEMA_SQL = (Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")

# A synthetic admin so @admin_required has a row to find. Invalid domain and an
# unusable hash: it satisfies an existence check and is not a working account.
TEST_ADMIN_EMAIL = "test-admin@example.invalid"


# ---------------------------------------------------------------------------
# Redirect the database paths AT IMPORT TIME.
#
# pytest imports conftest.py before any test module, so patching config here
# means a test module's ordinary `from src.api import admin as admin_mod`
# picks up the isolated paths. That keeps every existing test signature
# untouched, which matters because admin.py resolves its paths at import time.
#
# A session-scoped temp directory is used so the databases exist once for the
# whole run and are never the production ones.
# ---------------------------------------------------------------------------
import atexit
import shutil
import tempfile

_ISOLATED_DIR = Path(tempfile.mkdtemp(prefix="pHantasma-test-dbs-"))
atexit.register(shutil.rmtree, _ISOLATED_DIR, True)


def _build_session_databases() -> tuple[Path, Path]:
    cfg_db = _ISOLATED_DIR / "config.db"
    brain_db = _ISOLATED_DIR / "brain.db"
    for db in (cfg_db, brain_db):
        con = sqlite3.connect(db)
        con.executescript(SCHEMA_SQL)
        con.commit()
        con.close()
    con = sqlite3.connect(cfg_db)
    con.execute(
        "INSERT INTO users (email, password_hash, role, is_active) VALUES (?, ?, 'admin', 1)",
        (TEST_ADMIN_EMAIL, "not-a-real-hash"),
    )
    # Seed config rows. /admin/config renders one input per stored config entry,
    # grouped by config_categories, so an empty store renders no inputs and every
    # a11y assertion about those controls becomes vacuously true. Seeding keeps
    # the test meaningful without borrowing production data as its fixture.
    # The /admin/config grid now renders only the categories the CONFIG_CONTROLS
    # registry knows, so those are the categories seeded here.
    con.executemany(
        "INSERT INTO config_categories (name, description, display_order) VALUES (?, ?, ?)",
        [
            ("General", "General settings", 1),
            ("Audio", "Audio configuration", 2),
            ("LLM", "Large language model", 3),
            ("Security", "Security settings", 4),
        ],
    )
    con.executemany(
        "INSERT INTO config (category, key, value, description, is_sensitive) "
        "VALUES (?, ?, ?, ?, 0)",
        [
            ("Audio", "ALSA_VOLUME_PERCENT", "85", "Audio volume"),
            ("Audio", "WAKEWORD_CONFIDENCE", "0.70", "Wake word threshold"),
            ("General", "AUDIO_FEEDBACK_ENABLED", "true", "Audio feedback"),
            ("LLM", "WHISPER_MODEL", "medium", "Speech to text model"),
            ("Security", "DEBUG_MODE", "false", "Debug mode"),
        ],
    )
    con.commit()
    con.close()
    return cfg_db, brain_db


_CFG_DB, _BRAIN_DB = _build_session_databases()

import config as _cfg  # noqa: E402

_cfg.CONFIG_DB_PATH = str(_CFG_DB)
_cfg.BRAIN_DB_PATH = str(_BRAIN_DB)
_cfg.config.config_db_path = str(_CFG_DB)
_cfg.config.brain_db_path = str(_BRAIN_DB)


@pytest.fixture
def admin_store():
    """The admin module, guaranteed to point at the isolated databases."""
    import src.api.admin as admin_mod

    return admin_mod


@pytest.fixture
def test_admin_email() -> str:
    return TEST_ADMIN_EMAIL


@pytest.fixture
def admin_client(admin_store):
    """Flask test client for the admin routes, signed in as the synthetic admin."""
    from src.api import routes as routes_mod

    flask_app = routes_mod.app
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        with client.session_transaction() as sess:
            sess[admin_store.SESSION_KEY] = TEST_ADMIN_EMAIL
        yield client


@pytest.fixture(autouse=True)
def _bypass_off_by_default(request, monkeypatch):
    """Disable the loopback auth bypass for every test unless asked for.

    Production's .env sets PHANTASMA_LOCAL_ADMIN_BYPASS=1, and the test client
    requests arrive from 127.0.0.1 -- so without this the suite would run with
    authentication switched off and every "anonymous is redirected" assertion
    would pass or fail depending on the environment rather than the code.

    A test that genuinely wants the bypass asks for it with
    ``use_local_bypass``. The failure mode this prevents is the worst kind: a
    green suite that proved nothing because the thing under test was disabled.
    """
    # A module-scoped fixture cannot pre-empt this one: monkeypatch is
    # function-scoped, so it deletes the flag at the START of every test, after
    # any module-level fixture has set it. Modules that need the bypass opt in
    # per test with `use_local_bypass`, or declare the need on the test itself.
    if request.node.get_closest_marker("use_local_bypass"):
        return
    if "use_local_bypass" in request.fixturenames:
        return
    monkeypatch.delenv(localauth.ENV_FLAG, raising=False)


@pytest.fixture
def use_local_bypass(request, monkeypatch):
    """Opt in to the loopback bypass for this test only.

    Requests the marker from conftest itself so that the module-level
    autouse fixture above stands down for this test.
    """
    request.node.add_marker(pytest.mark.use_local_bypass)
    """Opt in to the loopback bypass for this test only."""
    monkeypatch.setenv(localauth.ENV_FLAG, "1")
    return True
