"""A cache that never filled.

2026-10-04. The owner asked "qual o teu limite para a estupidez humana?" once,
asked again over Discord, and the house spent 42-65 s of qwen3:8b generating an
answer it already had. `save_cached_response` existed, was never called anywhere
in dev or prod, and the `cache` table held 0 rows. The read side was there and
the docstring promised "Cache -> RAG -> Ollama"; only the write was missing. A
cache that is never written can never hit, and it presents as a cache-key problem
when it is a write problem.

These tests drive the real sqlite file and the real routing, because the defect
lived precisely in the gap between the function that existed and the code that
called it.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

import config
import data_utils
from data_utils import (
    CACHE_KIND_CONVERSATION,
    CACHE_KIND_LIVE,
    get_cached_response,
    save_cached_response,
)


@pytest.fixture
def cache_db(tmp_path, monkeypatch):
    db = tmp_path / "cache_test.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE cache (prompt TEXT PRIMARY KEY, response TEXT NOT NULL, "
        "timestamp DATETIME NOT NULL)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", str(db))
    return db


def _migrate(db):
    """Run the same migration the service runs on start.

    The fixture builds the pre-2026-10-04 three-column table on purpose: that is
    the schema prod's brain.db still has, and the migration has to survive it.
    """
    conn = sqlite3.connect(db)
    data_utils._ensure_cache_schema(conn.cursor())
    conn.commit()
    conn.close()


def _rows(db):
    conn = sqlite3.connect(db)
    out = conn.execute("SELECT prompt, response, kind FROM cache").fetchall()
    conn.close()
    return out


def test_a_saved_answer_comes_back(cache_db):
    """The round trip that never happened before."""
    save_cached_response("qual o teu limite para a estupidez humana?", "42")
    assert get_cached_response("qual o teu limite para a estupidez humana?") == "42"
    assert _rows(cache_db), "nothing was written"


def test_the_same_question_by_discord_hits_the_same_row(cache_db):
    """Voice and Discord must agree on what "the same question" means.

    The key is the normalised text. An exact `WHERE prompt = ?` on the raw
    string would miss whenever one front door stripped punctuation and the
    other did not, which is a miss that looks like a cache bug forever.
    """
    save_cached_response("Qual o teu limite para a estupidez humana", "42")
    assert get_cached_response("qual o teu limite para a estupidez humana") == "42"
    assert get_cached_response("  QUAL O TEU LIMITE PARA A ESTUPIDEZ HUMANA!  ") == "42"
    assert len(_rows(cache_db)) == 1, "normalisation created a second row"


def test_a_different_question_is_not_served_the_first_answer(cache_db):
    """A wrong hit is worse than a slow answer.

    The house would confidently repeat an answer to a question nobody asked,
    and the user has no way to tell it apart from knowing.
    """
    save_cached_response("qual o teu limite para a estupidez humana?", "42")
    assert get_cached_response("qual o teu limite para a bondade humana?") is None


def test_a_reading_expires_quickly_and_a_conversation_does_not(cache_db):
    """Both branches are cached. They do not have the same shelf life.

    A conversation can be served for a day. A sensor reading cannot: the UV of
    Lisbon went 0.1 -> 4.35 -> 0.0 within one day on 2026-10-04, and that stale
    figure is the second time this week the owner was told a number that no
    longer described the world.
    """
    assert data_utils.CACHE_TTL_HOURS[CACHE_KIND_CONVERSATION] >= 24
    assert data_utils.CACHE_TTL_HOURS[CACHE_KIND_LIVE] < 1

    _migrate(cache_db)
    conn = sqlite3.connect(cache_db)
    # One hour old: fresh against the 24 h conversation TTL, long expired
    # against the 15 min one. The single timestamp is the point -- identical
    # age, opposite verdicts, decided only by the kind.
    one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
    conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)",
                 ("qual o tempo", "20 graus", one_hour_ago, CACHE_KIND_CONVERSATION))
    conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)",
                 ("e a temperatura", "20 graus", one_hour_ago, CACHE_KIND_LIVE))
    conn.commit()
    conn.close()

    assert get_cached_response("qual o tempo") == "20 graus"
    assert get_cached_response("e a temperatura") is None


def test_an_unreadable_timestamp_is_treated_as_expired(cache_db):
    """If we cannot tell how old it is, we cannot claim it is fresh.

    The old code returned the answer forever on a ValueError, which is the one
    outcome a cache must never produce.
    """
    _migrate(cache_db)
    conn = sqlite3.connect(cache_db)
    conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)",
                 ("pergunta", "resposta", "nao-e-um-timestamp", CACHE_KIND_CONVERSATION))
    conn.commit()
    conn.close()
    assert get_cached_response("pergunta") is None


def test_an_unknown_kind_falls_back_to_the_short_ttl(cache_db):
    """A mislabelled reading must not inherit 24 hours."""
    save_cached_response("leitura", "18 graus", kind="inventado")
    kind = _rows(cache_db)[0][2]
    assert kind == CACHE_KIND_LIVE


def test_the_migration_is_idempotent_on_an_existing_table(cache_db):
    """It runs on every start, so it has to be safe on every start."""
    _migrate(cache_db)
    save_cached_response("a", "b")
    save_cached_response("c", "d")  # second call re-runs the schema check
    assert len(_rows(cache_db)) == 2


def test_the_skill_branch_is_cached_too(cache_db):
    """The branch that had no cache at all.

    Before this, only the model's answers were stored, so a repeated lookup by
    voice and then by Discord paid full price twice -- the mirror image of the
    bug above, in the other branch.
    """
    pipeline = _pipeline_double()
    first = pipeline.respond_to_text("como esta o tempo em lisboa?")
    assert first is not None
    assert _rows(cache_db), "the direct-skill branch stored nothing"

    # Second call must be served from the table, not from the skill.
    pipeline.skill_calls = 0
    second = pipeline.respond_to_text("como esta o tempo em lisboa?")
    assert second == first
    assert pipeline.skill_calls == 0, "the skill ran again on a cache hit"


def _pipeline_double():
    """A PhantasmaPipeline with only what the routing actually touches."""
    import assistant

    obj = assistant.PhantasmaPipeline.__new__(assistant.PhantasmaPipeline)
    obj._fly_brain = _BrainDouble()
    obj._skill_loader = _LoaderDouble()
    obj.skill_calls = 0

    def skill(text):
        obj.skill_calls += 1
        return "Today: partly cloudy, 17-27 degrees."

    obj._execute_with_paused_shared_audio = skill
    obj._is_opinion = lambda text: False
    obj._respond_with_llm = lambda text, skill_data=None: "resposta do modelo"
    return obj


class _BrainDouble:
    def __init__(self):
        self.steps = 0

    def step(self, **kwargs):
        self.steps += 1


class _LoaderDouble:
    """Reports the weather skill, which is in the read-only set."""

    last_skill_name = "skill_weather"


def test_an_action_skill_never_seeds_the_cache(cache_db):
    """The guarantee, stated as what the pipeline actually does.

    An earlier version of this test hand-wrote an action into the table as a
    `live` row and then asserted the pipeline would refuse to replay it. It
    would not: the early read filters by kind, not by skill identity. The safety
    is entirely on the WRITE side, and pretending otherwise would have been a
    test that documents a protection the code does not have.

    So this asserts the thing that is true and that matters: a skill reporting
    itself as an action leaves the table empty.
    """
    pipeline = _pipeline_double()
    pipeline._skill_loader.last_skill_name = "skill_chacon"
    answer = pipeline.respond_to_text("acende a luz do balcao")

    assert answer == "Today: partly cloudy, 17-27 degrees.", "a skill correu"
    assert _rows(cache_db) == [], (
        "uma skill que age sobre o mundo guardou a resposta: a proxima vez "
        "a casa responderia 'luz ligada' sem tocar no rele"
    )


def test_a_reading_does_seed_the_cache_and_is_replayed(cache_db):
    """The other half: a reading is safe to keep and safe to replay."""
    pipeline = _pipeline_double()
    first = pipeline.respond_to_text("como esta o tempo em lisboa?")
    assert first is not None
    assert _rows(cache_db), "a leitura nao foi guardada"

    pipeline.skill_calls = 0
    second = pipeline.respond_to_text("como esta o tempo em lisboa?")
    assert second == first
    assert pipeline.skill_calls == 0, "a skill correu apesar da cache"


def test_only_read_only_skills_are_written(cache_db):
    """A skill that switched something must never seed the cache."""
    from assistant import _READ_ONLY_SKILLS

    assert "skill_weather" in _READ_ONLY_SKILLS
    for action_skill in ("skill_chacon", "skill_shellygas", "skill_music",
                         "skill_tuya", "skill_ewelink"):
        assert action_skill not in _READ_ONLY_SKILLS, (
            f"{action_skill} actua sobre o mundo e nao pode ser reexecutado "
            f"a partir de uma resposta guardada"
        )


def test_a_cached_conversation_answer_is_not_served_as_a_reading(cache_db):
    """The conversation TTL and the reading TTL must not bleed into each other.

    Otherwise a 24 h-old opinion about the weather becomes a "reading" and gets
    replayed by the early lookup that is supposed to be safe.
    """
    save_cached_response("o que achas do tempo?", "uma opiniao de ontem",
                         kind=CACHE_KIND_CONVERSATION)
    assert get_cached_response("o que achas do tempo?",
                               kinds=(CACHE_KIND_LIVE,)) is None


def test_a_read_only_database_is_reported_not_silently_disabled(cache_db, monkeypatch):
    """The failure that looks like a bad cache key.

    The migration runs on the READ path, so a database the service cannot write
    makes every read fail. The answer is still served -- the whole thing is
    wrapped -- but the cache is then permanently dead while presenting as
    "nothing was ever cached". That is precisely how this bug survived: a cache
    that is read-only, fed by a writer nobody called, on a table whose key was
    the raw string. All three looked like the same thing.
    """
    readonly = tmp_readonly_db(cache_db)
    monkeypatch.setattr(config, "DB_PATH", str(readonly))
    data_utils._CACHE_READ_WARNED = False

    assert get_cached_response("qualquer coisa") is None  # degrades, no raise

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        get_cached_response("outra")
    assert "inoperacional" in buf.getvalue() or data_utils._CACHE_READ_WARNED, (
        "a cache morta nao disse nada -- e naoavenir para parecer um problema "
        "de chave"
    )


def tmp_readonly_db(source):
    """A database file that exists, and refuses every write."""
    import os
    import stat
    import tempfile

    d = tempfile.mkdtemp()
    path = os.path.join(d, "ro.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE cache (prompt TEXT PRIMARY KEY, response TEXT NOT NULL, "
        "timestamp DATETIME NOT NULL)"
    )
    conn.commit()
    conn.close()
    # Read permission only. A directory that cannot be written also blocks
    # SQLite's journal, which is the real-world shape of this failure.
    os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return path
