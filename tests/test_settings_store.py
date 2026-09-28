"""The settings the owner changes in /admin must actually take effect.

Each test here exists because the corresponding setting was silently inert:
the persona never reached the model at all, the reaction weights were a module
constant, and both looked configurable in the code without being so.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings_store  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Point the store at a throwaway database.

    Without this the tests would write the real owner's settings, and a test
    that mutates production state is worse than no test.
    """
    import sqlite3

    db = tmp_path / "settings_test.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT, timestamp TIMESTAMP)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings_store.config, "DB_PATH", str(db))
    yield
    for key in (
        settings_store.PERSONA_KEY,
        settings_store.REACTION_WEIGHTS_KEY,
    ):
        settings_store.clear_setting(key)


def test_default_persona_is_non_empty():
    """A persona that resolves to "" is the failure this whole store exists
    to prevent -- the assistant would fall back to no instructions at all."""
    assert settings_store.default_persona().strip(), (
        "no default persona: the assistant would run with no instructions"
    )


def test_persona_round_trip():
    settings_store.set_persona("Falo devagar.", updated_by="tester")
    assert settings_store.get_persona() == "Falo devagar."
    assert settings_store.persona_is_overridden() is True


def test_persona_survives_a_fresh_read():
    """The point of storing it: a new process must see the same persona."""
    settings_store.set_persona("Persistente.", updated_by="tester")
    stored = settings_store.get_setting(settings_store.PERSONA_KEY)
    assert stored == "Persistente."


def test_empty_persona_is_refused():
    """Silently accepting an empty persona would return the assistant to the
    no-instructions state that started all of this."""
    with pytest.raises(ValueError):
        settings_store.set_persona("   ")


def test_reset_restores_the_default():
    original = settings_store.default_persona()
    settings_store.set_persona("Temporária.", updated_by="tester")
    settings_store.reset_persona()
    assert settings_store.get_persona() == original
    assert settings_store.persona_is_overridden() is False


def test_default_weights_are_the_shipped_six():
    assert len(settings_store.DEFAULT_REACTION_WEIGHTS) == 6


def test_weight_override_is_visible_to_reactions():
    """The end-to-end claim: a weight set in /admin changes what the
    assistant is rewarded for. Asserted through reactions, not just the
    store, because the gap between those two is where the bug lived."""
    from src.brain import reactions

    emoji = "👍"
    reactions.reload_reaction_weights()
    assert reactions.reward_for(emoji) == 1.0

    weights = settings_store.get_reaction_weights()
    weights[emoji] = 0.25
    settings_store.set_reaction_weights(weights, updated_by="tester")

    reactions.reload_reaction_weights()
    assert reactions.reward_for(emoji) == 0.25


def test_unset_emoji_keeps_its_default():
    """Merged, not replaced: adding an emoji in code must not lose its weight
    on an install that already has an override."""
    from src.brain import reactions

    emoji = "👍"
    weights = settings_store.get_reaction_weights()
    weights[emoji] = 0.25
    settings_store.set_reaction_weights(weights, updated_by="tester")

    merged = settings_store.get_reaction_weights()
    assert merged[emoji] == 0.25
    assert len(merged) == len(settings_store.DEFAULT_REACTION_WEIGHTS)
    reactions.reload_reaction_weights()


def test_unmapped_emoji_is_none_not_zero():
    from src.brain import reactions

    reactions.reload_reaction_weights()
    assert reactions.reward_for("😀") is None


def test_corrupt_weight_row_does_not_silence_the_others():
    """One bad emoji must not cost the owner the other five."""
    settings_store.set_setting(settings_store.REACTION_WEIGHTS_KEY, "{not json at all")
    weights = settings_store.get_reaction_weights()
    assert len(weights) == len(settings_store.DEFAULT_REACTION_WEIGHTS)


def test_invalid_weight_is_rejected_loudly():
    with pytest.raises(ValueError):
        settings_store.set_reaction_weights({"x": "not-a-number"})


# --- the eighteen-row regression -------------------------------------------

def test_only_known_emoji_are_ever_stored():
    """/admin/config rendered eighteen weight rows: six emojis and the digits
    0..11. Another form on the same route named its inputs w_0..w_11 and the
    collector -- {k[2:]: v for k in request.form if k.startswith("w_")} --
    swept them up. A weight is keyed by an emoji; a digit is a mistake."""
    weights = settings_store.get_reaction_weights()
    weights.update({"0": 1.0, "11": 0.5})
    settings_store.set_reaction_weights(weights, updated_by="tester")
    stored = settings_store.get_reaction_weights()
    assert not [k for k in stored if k not in
                settings_store.DEFAULT_REACTION_WEIGHTS], (
        f"non-emoji keys survived: "
        f"{[k for k in stored if k not in settings_store.DEFAULT_REACTION_WEIGHTS]}"
    )
    assert len(stored) == len(settings_store.DEFAULT_REACTION_WEIGHTS)


def test_a_form_with_no_valid_emojis_is_refused():
    """Silently storing nothing would leave the owner with a form that
    appears to save and does not."""
    with pytest.raises(ValueError):
        settings_store.set_reaction_weights({"0": 1.0, "1": 2.0})


def test_corrupt_stored_rows_never_reach_the_admin_page():
    """Already-saved bad keys must not render even before they are cleared:
    the filter belongs at both ends, the write and the read."""
    settings_store.set_setting(
        settings_store.REACTION_WEIGHTS_KEY,
        '{"0": 1.0, "1": -0.5, "\\ud83d\\udc4d": 2.0}',
    )
    weights = settings_store.get_reaction_weights()
    assert "0" not in weights and "1" not in weights, (
        "digit keys in the store reached the caller that renders the page"
    )
    assert weights["\U0001F44D"] == 2.0, "a valid owner-set weight was lost"
