"""T071: the wizard's routes, end to end, without a microphone.

The browser records; these tests feed the routes the bytes a browser would send
(16 kHz mono WAV, base64) and assert on what comes back. What matters here is
the refusal behaviour, because the two failures that matter are both silent:
a bad clip accepted into the dataset, and a bad model installed.
"""

import base64
import io
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.wakeword.dataset import MIN_NEGATIVES, MIN_POSITIVES  # noqa: E402
from tests.test_wakeword import negatives, positives, speechlike  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_MODEL = os.path.join(REPO_ROOT, "models", "ola_fantasma.onnx")


def wav_b64(audio: np.ndarray) -> str:
    """What the browser would send, as base64 WAV."""
    buf = io.BytesIO()
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm.tobytes())
    return base64.b64encode(buf.getvalue()).decode("ascii")


@pytest.fixture
def wizard(monkeypatch, tmp_path):
    """An app with the wizard routes, admin signed in, and temp paths."""
    from flask import Flask

    import config as config_module
    from src.api import ui_auth

    monkeypatch.setattr(config_module, "WAKEWORD_REGISTRY_PATH",
                        str(tmp_path / "wakewords.json"))
    monkeypatch.setattr(config_module, "WAKEWORD_MODELS_DIR",
                        str(tmp_path / "models"))
    monkeypatch.setattr(config_module, "WAKEWORD_STAGING_DIR",
                        str(tmp_path / "staging"))

    monkeypatch.setattr(ui_auth, "is_authenticated", lambda: True,
                        raising=False)
    monkeypatch.setattr(ui_auth, "is_admin", lambda: True, raising=False)

    app = Flask(__name__)
    app.secret_key = "k"
    app.config["TESTING"] = True

    from src.api import wakeword_wizard

    wakeword_wizard.register(app)
    client = app.test_client()
    client.app_module = wakeword_wizard  # for monkeypatching config lookups
    yield client, config_module


def post_record(client, label, kind, audio, phrase="ola fantasma"):
    return client.post("/api/wakeword/record", json={
        "label": label, "kind": kind, "phrase": phrase,
        "audio": wav_b64(audio)})


class TestGate:
    def test_a_non_admin_can_do_nothing(self, monkeypatch, tmp_path):
        """These routes write the file that decides when the assistant starts
        listening, and they hold biometric recordings."""
        from flask import Flask

        import config as config_module
        from src.api import ui_auth

        monkeypatch.setattr(config_module, "WAKEWORD_REGISTRY_PATH",
                            str(tmp_path / "w.json"))
        monkeypatch.setattr(config_module, "WAKEWORD_STAGING_DIR",
                            str(tmp_path / "s"))
        monkeypatch.setattr(ui_auth, "is_admin", lambda: False, raising=False)

        app = Flask(__name__)
        app.config["TESTING"] = True
        from src.api import wakeword_wizard
        wakeword_wizard.register(app)
        client = app.test_client()

        assert client.get("/api/wakeword/users").status_code == 403
        assert post_record(client, "Ana", "positive", speechlike()).status_code == 403
        assert client.post("/api/wakeword/export", json={"label": "Ana"}
                           ).status_code == 403
        assert client.post("/api/wakeword/import", json={"label": "Ana"}
                           ).status_code == 403


class TestRecording:
    def test_a_good_clip_is_kept_and_counted(self, wizard):
        client, _cfg = wizard
        response = post_record(client, "Ana", "positive", speechlike(1.4))
        assert response.status_code == 200
        data = response.get_json()
        assert data["ok"] and data["check"]["ok"]
        assert data["staged_positives"] == 1

    def test_a_bad_clip_is_refused_and_not_stored(self, wizard):
        """The count must mean "usable clips". If a rejected clip is stored,
        the count is a lie and the dataset is worse than it looks."""
        client, _cfg = wizard
        silence = np.zeros(16000 * 2, dtype=np.float32)
        response = post_record(client, "Ana", "positive", silence)
        assert response.status_code == 200
        assert response.get_json()["ok"] is False
        assert "silencio" in response.get_json()["check"]["reason"].lower()

        assert client.get("/api/wakeword/users").get_json()["users"][0][
            "staged_positives"] == 0

    def test_the_server_explains_the_refusal(self, wizard):
        """In the server's own words. A generic "erro" sends the owner back to
        record everything again."""
        client, _cfg = wizard
        clipped = np.clip(speechlike() * 6.0, -1, 1).astype(np.float32)
        reason = post_record(client, "Ana", "positive", clipped
                             ).get_json()["check"]["reason"]
        assert "saturado" in reason.lower()

    def test_an_undecodable_upload_is_a_400_not_a_500(self, wizard):
        client, _cfg = wizard
        response = client.post("/api/wakeword/record", json={
            "label": "Ana", "kind": "positive",
            "audio": base64.b64encode(b"\x00\x01\x02not audio").decode()})
        assert response.status_code in (400, 422)

    def test_an_empty_upload_is_refused(self, wizard):
        client, _cfg = wizard
        assert client.post("/api/wakeword/record", json={
            "label": "Ana", "kind": "positive", "audio": ""}
        ).status_code == 400

    def test_an_unknown_kind_is_refused(self, wizard):
        client, _cfg = wizard
        assert client.post("/api/wakeword/record", json={
            "label": "Ana", "kind": "sideways",
            "audio": wav_b64(speechlike())}).status_code == 400

    def test_two_people_keep_separate_recordings(self, wizard):
        client, _cfg = wizard
        post_record(client, "Ana", "positive", speechlike(1.4, seed=1))
        post_record(client, "Bruno", "positive", speechlike(1.4, seed=2))
        users = {u["label"]: u for u in client.get(
            "/api/wakeword/users").get_json()["users"]}
        assert set(users) == {"Ana", "Bruno"}
        assert users["Ana"]["slug"] != users["Bruno"]["slug"]


class TestExport:
    def _record_all(self, client, n_pos=MIN_POSITIVES, n_neg=MIN_NEGATIVES):
        for sample in positives(n_pos):
            post_record(client, "Ana", "positive", sample.audio)
        for sample in negatives(n_neg):
            post_record(client, "Ana", "negative", sample.audio)

    def test_export_is_refused_until_there_are_enough(self, wizard):
        """422, not 500: 'you did not record enough' is an ordinary outcome."""
        client, _cfg = wizard
        post_record(client, "Ana", "positive", speechlike())
        response = client.post("/api/wakeword/export", json={"label": "Ana"})
        assert response.status_code == 422
        assert str(MIN_POSITIVES) in response.get_json()["reason"]

    def test_export_is_refused_without_negatives(self, wizard):
        client, _cfg = wizard
        self._record_all(client, n_pos=MIN_POSITIVES, n_neg=0)
        response = client.post("/api/wakeword/export", json={"label": "Ana"})
        assert response.status_code == 422
        assert "negativo" in response.get_json()["reason"].lower()

    def test_a_complete_dataset_exports(self, wizard):
        client, _cfg = wizard
        self._record_all(client)
        response = client.post("/api/wakeword/export", json={"label": "Ana"})
        assert response.status_code == 200
        data = response.get_json()
        assert data["ok"]
        assert data["metadata"]["positives"] == MIN_POSITIVES

    def test_the_bundle_can_then_be_downloaded(self, wizard):
        client, _cfg = wizard
        self._record_all(client)
        client.post("/api/wakeword/export", json={"label": "Ana"})
        response = client.get("/api/wakeword/bundle/ana")
        assert response.status_code == 200
        assert len(response.data) > 0


class TestImport:
    def _record_a_few(self, client, n=3):
        for sample in positives(n):
            post_record(client, "Ana", "positive", sample.audio)

    def test_a_valid_model_is_installed(self, wizard):
        client, _cfg = wizard
        self._record_a_few(client)
        response = client.post("/api/wakeword/import", json={
            "label": "Ana",
            "model": base64.b64encode(open(REAL_MODEL, "rb").read()).decode()})
        assert response.status_code == 200
        data = response.get_json()
        assert data["ok"] and data["loaded"]
        assert data["sample_count"] == 3
        assert data["restart_required"] is True

    def test_a_broken_model_is_refused_and_installs_nothing(self, wizard):
        """The whole point. A model that does not load takes the assistant down
        at startup, so the previous one must survive untouched."""
        client, _cfg = wizard
        self._record_a_few(client, 1)
        client.post("/api/wakeword/import", json={
            "label": "Ana",
            "model": base64.b64encode(open(REAL_MODEL, "rb").read()).decode()})
        good = client.get("/api/wakeword/users").get_json()["users"][0]
        before = open(good["model_path"], "rb").read()

        broken = client.post("/api/wakeword/import", json={
            "label": "Ana", "model": base64.b64encode(b"nope" * 5000).decode()})
        assert broken.status_code == 422
        assert not broken.get_json()["ok"]

        after = client.get("/api/wakeword/users").get_json()["users"][0]
        assert after["model_path"] == good["model_path"]
        assert open(after["model_path"], "rb").read() == before

    def test_a_truncated_model_is_refused(self, wizard):
        """What a half-finished download from the notebook actually looks like."""
        client, _cfg = wizard
        self._record_a_few(client, 1)
        client.post("/api/wakeword/import", json={
            "label": "Ana",
            "model": base64.b64encode(open(REAL_MODEL, "rb").read()[:120_000]
                                       ).decode()})
        assert not client.get("/api/wakeword/users").get_json()[
            "users"][0]["trained"]


class TestTheReportedFacts:
    def test_false_positive_rate_is_unknown_until_measured(self, wizard):
        client, _cfg = wizard
        post_record(client, "Ana", "positive", speechlike())
        assert client.get("/api/wakeword/users").get_json()[
            "users"][0]["false_positive_rate"] is None

    def test_a_measured_rate_is_recorded(self, wizard):
        client, _cfg = wizard
        post_record(client, "Ana", "positive", speechlike())
        assert client.post("/api/wakeword/ana/fp?rate=0.5").status_code == 200
        assert client.get("/api/wakeword/users").get_json()[
            "users"][0]["false_positive_rate"] == 0.5

    def test_loaded_models_reflects_the_registry(self, wizard):
        client, _cfg = wizard
        self_n = client.get("/api/wakeword/users").get_json()
        assert self_n["loaded_models"] == []


class TestThePageGate:
    """`/treino` rendered 200 to an anonymous caller until 2026-10-13."""

    def test_anonymous_is_sent_to_login(self, monkeypatch, tmp_path):
        from flask import Flask

        import config as config_module
        from src.api import ui_auth

        monkeypatch.setattr(config_module, "WAKEWORD_REGISTRY_PATH",
                            str(tmp_path / "w.json"))
        monkeypatch.setattr(ui_auth, "is_authenticated", lambda: False,
                            raising=False)
        monkeypatch.setattr(ui_auth, "is_admin", lambda: True, raising=False)
        app = Flask(__name__)
        app.config["TESTING"] = True
        from src.api import wakeword_wizard
        wakeword_wizard.register(app)
        response = app.test_client().get("/treino")
        assert response.status_code in (302, 401, 403)
        assert b"Palavra de acordar" not in response.data

    def test_a_signed_in_non_admin_is_refused(self, monkeypatch, tmp_path):
        from flask import Flask

        import config as config_module
        from src.api import ui_auth

        monkeypatch.setattr(config_module, "WAKEWORD_REGISTRY_PATH",
                            str(tmp_path / "w.json"))
        monkeypatch.setattr(ui_auth, "is_authenticated", lambda: True,
                            raising=False)
        monkeypatch.setattr(ui_auth, "is_admin", lambda: False, raising=False)
        app = Flask(__name__)
        app.config["TESTING"] = True
        from src.api import wakeword_wizard
        wakeword_wizard.register(app)
        response = app.test_client().get("/treino")
        assert response.status_code == 403
        assert b"Palavra de acordar" not in response.data


class TestThePage:
    def test_it_names_the_exception(self, monkeypatch, wizard):
        """The page must say out loud that recordings leave the machine.
        A wizard that quietly uploads to Google is exactly what the project's
        own rules exist to prevent."""
        import config as config_module
        from src.api import ui_auth

        monkeypatch.setattr(config_module, "WAKEWORD_REGISTRY_PATH",
                            str("/tmp/does-not-exist.json"))
        monkeypatch.setattr(ui_auth, "is_authenticated", lambda: True,
                            raising=False)
        client, _cfg = wizard
        page = client.get("/treino").get_data(as_text=True)
        assert "Colab" in page
        assert "CLAUDE.md" in page

    def test_it_does_not_claim_to_identify(self, monkeypatch, wizard):
        client, _cfg = wizard
        page = client.get("/treino").get_data(as_text=True)
        assert "inscricao" in page.lower()
        assert "identificacao" in page.lower()

    def test_it_offers_the_terminal_wizard(self, monkeypatch, wizard):
        """The browser cannot reach the microphone over plain http on the LAN,
        and that is the normal way this page is opened. The page must say so
        and name the way that does work, rather than offering a button that
        silently does nothing."""
        client, _cfg = wizard
        page = client.get("/treino").get_data(as_text=True)
        assert "src.wakeword.cli record" in page

    def test_it_checks_for_a_microphone_before_offering_one(self, monkeypatch, wizard):
        """The defect that made Gravar look broken: `navigator.mediaDevices` is
        undefined outside a secure context, `getUserMedia` threw on a property
        of undefined, nothing was caught and nothing was drawn."""
        client, _cfg = wizard
        page = client.get("/treino").get_data(as_text=True)
        assert "navigator.mediaDevices" in page
        assert "micUnavailable" in page

    def test_it_says_the_false_positive_rate_is_not_measured_here(self, wizard):
        client, _cfg = wizard
        page = client.get("/treino").get_data(as_text=True)
        assert "nao e medida aqui" in page
