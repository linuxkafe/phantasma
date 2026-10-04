"""Regression + UI tests for /admin/config controls.

Covers two fixes on the same page:
1. "Guardar pesos" returned 500 because the weight inputs were named
   ``w_0``..``w_5`` while set_reaction_weights only accepts emoji keys, so
   every submitted key was rejected and the empty result raised. The fields
   now carry the emoji itself (``w_👍``) and the handler turns a ValueError
   into a flash instead of a 500.
2. The config grid is now driven by CONFIG_CONTROLS (friendly labels,
   sliders for numbers, toggles for booleans) and saves reach app_settings,
   the store the runtime reads at boot.
"""

import os
import sqlite3

import pytest

from src.api import admin as admin_mod
from src.api.routes import create_app


def _client():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[admin_mod.SESSION_KEY] = "test-admin@example.invalid"
    return client


@pytest.fixture(scope="module")
def _seeded_cfg_db():
    """Seed one registry row per control shape into the isolated config.db.

    The conftest seeds only "geral"/"audio" rows that the registry does not
    know, and the page now renders only registry categories, so without this
    the rendering tests would be vacuous.
    """
    import config as cfg_mod

    conn = sqlite3.connect(cfg_mod.CONFIG_DB_PATH)
    try:
        for category in ("Audio", "General", "LLM", "Security"):
            conn.execute(
                "INSERT OR IGNORE INTO config_categories (name, description) "
                "VALUES (?, ?)",
                (category, f"{category} settings"),
            )
        for category, key, value in (
            ("Audio", "ALSA_VOLUME_PERCENT", "85"),
            ("Audio", "WAKEWORD_CONFIDENCE", "0.70"),
            ("General", "AUDIO_FEEDBACK_ENABLED", "true"),
            ("LLM", "WHISPER_MODEL", "medium"),
            ("Security", "DEBUG_MODE", "false"),
        ):
            conn.execute(
                "INSERT INTO config (category, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(category, key) DO NOTHING",
                (category, key, value),
            )
        conn.commit()
    finally:
        conn.close()


class TestReactionWeights:
    def test_emoji_named_fields_reach_set_reaction_weights(self, monkeypatch):
        captured = {}

        def _spy(weights, **kwargs):
            captured.update(weights)

        monkeypatch.setattr(admin_mod, "set_reaction_weights", _spy)
        resp = _client().post(
            "/admin/config",
            data={
                "action": "save_weights",
                "w_👍": "1.5",
                "w_🔥": "-0.5",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (301, 302)
        assert captured == {"👍": "1.5", "🔥": "-0.5"}

    def test_digit_named_fields_do_not_500(self):
        # Legacy/malformed clients may still send w_1/w_2. Those keys are not
        # emojis, so the real set_reaction_weights rejects them all and raises
        # ValueError. The handler must surface it, not bubble a 500.
        resp = _client().post(
            "/admin/config",
            data={"action": "save_weights", "w_1": "1.0", "w_2": "-0.5"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", "replace")
        assert "valido" in body or "Pesos" in body

    def test_persona_page_uses_emoji_names_too(self):
        body = _client().get("/admin/persona").data.decode("utf-8", "replace")
        assert 'name="w_👍"' in body
        assert 'name="w_0"' not in body


class TestFriendlyControls:
    def _post(self, monkeypatch, data):
        written = {}
        updated = {}

        def _set_setting(key, value, **kwargs):
            written[key] = value

        def _update_config(key, value, **kw):
            updated[key] = value

        monkeypatch.setattr(admin_mod, "set_setting", _set_setting)
        monkeypatch.setattr(admin_mod, "update_config", _update_config)
        resp = _client().post("/admin/config", data=data, follow_redirects=False)
        return resp, written, updated

    def test_grid_renders_slider_toggle_and_select(self, _seeded_cfg_db):
        body = _client().get("/admin/config").data.decode("utf-8", "replace")
        assert 'type="range"' in body and 'name="config_ALSA_VOLUME_PERCENT"' in body
        assert 'type="checkbox"' in body and 'name="config_AUDIO_FEEDBACK_ENABLED"' in body
        assert 'name="config_WHISPER_MODEL"' in body
        assert 'name="config_DEBUG_MODE"' in body
        assert "Som de confirmação" in body
        assert "Confiança mínima para ativar" in body
        # Service secrets must no longer reach the page HTML.
        assert 'type="password"' not in body
        assert "DISCORD_BOT_TOKEN" not in body

    def test_number_is_clamped_to_its_range(self, monkeypatch):
        resp, written, updated = self._post(
            monkeypatch, {"config_ALSA_VOLUME_PERCENT": "999", "config_OLLAMA_TIMEOUT": "-5"}
        )
        assert resp.status_code in (301, 302)
        assert written["ALSA_VOLUME_PERCENT"] == "100"
        assert written["OLLAMA_TIMEOUT"] == "10"
        assert updated["ALSA_VOLUME_PERCENT"] == "100"

    def test_unchecked_bool_is_saved_as_false(self, monkeypatch):
        # A submitted grid without the AUDIO_FEEDBACK_ENABLED field must still
        # persist "false": an absent checkbox means off, not "leave alone".
        resp, written, _ = self._post(monkeypatch, {"config_GREETING_PATH": "audio/g.wav"})
        assert resp.status_code in (301, 302)
        assert written["AUDIO_FEEDBACK_ENABLED"] == "false"
        assert written["GREETING_PATH"] == "audio/g.wav"

    def test_non_whitelisted_keys_are_not_treated_as_controls(self, monkeypatch):
        resp, written, _ = self._post(monkeypatch, {"config_DISCORD_BOT_TOKEN": "leak"})
        assert resp.status_code in (301, 302)
        assert "DISCORD_BOT_TOKEN" not in written


class TestOverlay:
    def test_whitelists_match_the_ui_registry(self):
        import config as cfg_mod

        assert cfg_mod._OVERLAY_KEYS == set(admin_mod.CONFIG_CONTROLS)

    def test_a_readonly_control_still_reaches_the_overlay(self):
        """The factual temperature is listed but has no slider.

        It is in CONFIG_CONTROLS so the page can explain its absence, and
        _OVERLAY_KEYS must stay equal to CONFIG_CONTROLS -- so it is whitelisted
        for the overlay too. That is harmless: the save loop skips readonly keys,
        so no row is ever written, and the whitelist only permits a row that
        exists.

        Worth pinning because the two sets are compared for EQUALITY. The day
        someone excludes the readonly key from the overlay to "keep the lists
        tidy", this fails -- which is the moment to find out that the exclusion
        would have broken the invariant the equality exists to protect.
        """
        import config as cfg_mod

        assert "LLM_TEMPERATURE_FACTUAL" in cfg_mod._OVERLAY_KEYS
        assert "LLM_TEMPERATURE_CONVERSATION" in cfg_mod._OVERLAY_KEYS

    def test_overlay_pushes_only_whitelisted_settings(self, tmp_path, monkeypatch):
        import config as cfg_mod

        db = tmp_path / "brain.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            [("ALSA_VOLUME_PERCENT", "42"), ("DISCORD_BOT_TOKEN", "secret")],
        )
        conn.commit()
        conn.close()
        monkeypatch.delenv("ALSA_VOLUME_PERCENT", raising=False)
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        cfg_mod._overlay_owner_settings(str(db))
        assert os.environ["ALSA_VOLUME_PERCENT"] == "42"
        # A secret not in the whitelist must never be injected into the env.
        assert "DISCORD_BOT_TOKEN" not in os.environ
