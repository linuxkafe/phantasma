"""T071: refusing a bad dataset, and refusing a model that does not load.

Two refusals and one install, and the refusals are the interesting ones.

A bundle built from thirty clipped recordings of a fan teaches the assistant to
wake on a fan, and that arrives in the house weeks later as "acorda sozinho".
A model that does not load takes the whole assistant down, because
`HotwordDetector.__init__` raises on a bad `.onnx`. So these tests are mostly
about what is REFUSED, and the fixtures are built to be refused.
"""

import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.wakeword.dataset import (  # noqa: E402
    MIN_NEGATIVES,
    MIN_POSITIVES,
    Sample,
    build_bundle,
    validate_samples,
)
from src.wakeword.importer import import_model, validate_onnx  # noqa: E402
from src.wakeword.registry import WakeWordEntry, WakeWordRegistry, slugify  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_MODEL = os.path.join(REPO_ROOT, "models", "ola_fantasma.onnx")


def speechlike(seconds=1.4, seed=0):
    """Something with speech-like envelope and transients, not a sine.

    A pure tone would pass every loudness check and teach a model to wake on a
    tone, which is the bug the whole module exists to catch.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * 16000)
    t = np.arange(n) / 16000.0
    carrier = sum(np.sin(2 * np.pi * f * t) / (i + 1)
                  for i, f in enumerate([140, 320, 700, 1500, 2800]))
    # An amplitude envelope with syllable-sized gaps, so the clip has the
    # structure of speech rather than a continuous tone.
    envelope = (0.35 + 0.65 * (np.sin(2 * np.pi * 3.1 * t) > -0.2)).astype(float)
    signal = carrier * envelope + 0.01 * rng.standard_normal(n)
    peak = np.max(np.abs(signal))
    return (signal / peak * 0.45).astype(np.float32) if peak else signal.astype(np.float32)


def positives(count, seconds=1.4):
    return [Sample(label="positive", audio=speechlike(seconds, seed=i), index=i)
            for i in range(count)]


def negatives(count, seconds=1.6):
    return [Sample(label="negative", audio=speechlike(seconds, seed=100 + i),
                   index=i) for i in range(count)]


# --- sample quality ---------------------------------------------------------

class TestSampleChecks:
    def test_a_good_clip_passes(self):
        checks = validate_samples(positives(1), [])
        assert checks[0].ok
        assert checks[0].reason == ""

    def test_too_short_is_refused(self):
        clip = Sample(label="positive", audio=speechlike(0.2), index=0)
        result = validate_samples([clip], [])[0]
        assert not result.ok
        assert "curto" in result.reason.lower()

    def test_too_long_is_refused(self):
        clip = Sample(label="positive", audio=speechlike(7.0), index=0)
        assert not validate_samples([clip], [])[0].ok

    def test_silence_is_refused(self):
        clip = Sample(label="positive", audio=np.zeros(16000 * 2, np.float32), index=0)
        result = validate_samples([clip], [])[0]
        assert not result.ok
        assert "silencio" in result.reason.lower()

    def test_clipping_is_refused(self):
        """A clipped recording has its consonants flattened, and those are
        most of what tells the wake word from ordinary speech."""
        clipped = np.clip(speechlike(1.4) * 6.0, -1.0, 1.0).astype(np.float32)
        result = validate_samples([Sample("positive", clipped, 0)], [])[0]
        assert not result.ok
        assert "saturado" in result.reason.lower()

    def test_an_empty_clip_is_refused_not_crashed(self):
        result = validate_samples([Sample("positive", np.array([], np.float32), 0)], [])[0]
        assert not result.ok

    def test_each_clip_is_judged_on_its_own(self):
        """One bad clip in thirty must name itself. A single pass/fail sends
        the owner back to re-record everything."""
        samples = positives(5)
        samples[3] = Sample("positive", np.zeros(16000 * 2, np.float32), 3)
        results = validate_samples(samples, [])
        assert [r.ok for r in results] == [True, True, True, False, True]
        assert results[3].index == 3


# --- the bundle -------------------------------------------------------------

class TestBundleRefusals:
    def test_too_few_positives_is_refused(self, tmp_path):
        result = build_bundle("dono", positives(5), negatives(30), str(tmp_path))
        assert not result["ok"]
        assert str(MIN_POSITIVES) in result["reason"]

    def test_no_negatives_is_refused_and_says_why(self, tmp_path):
        """The refusal that bites. Trained without negatives, a wake-word model
        fires on ordinary speech, and the symptom is indistinguishable from a
        broken microphone."""
        result = build_bundle("dono", positives(40), negatives(0), str(tmp_path))
        assert not result["ok"]
        assert "negativo" in result["reason"].lower()
        assert str(MIN_NEGATIVES) in result["reason"]

    def test_one_bad_clip_stops_the_whole_bundle(self, tmp_path):
        """Half-good is not good enough for a dataset whose failure mode is
        silent."""
        samples = positives(40)
        samples[7] = Sample("positive", np.zeros(16000 * 2, np.float32), 7)
        result = build_bundle("dono", samples, negatives(25), str(tmp_path))
        assert not result["ok"]
        assert result["bad"][0]["index"] == 7

    def test_a_refused_bundle_writes_nothing(self, tmp_path):
        target = tmp_path / "out"
        target.mkdir()
        build_bundle("dono", positives(2), negatives(2), str(target))
        assert list(target.iterdir()) == []


class TestBundleContents:
    @pytest.fixture
    def built(self, tmp_path):
        return build_bundle("Dono", positives(MIN_POSITIVES), negatives(MIN_NEGATIVES),
                            phrase="ola fantasma", out_dir=str(tmp_path))

    def test_it_builds(self, built):
        assert built["ok"]
        assert built["metadata"]["positives"] == MIN_POSITIVES
        assert built["metadata"]["negatives"] == MIN_NEGATIVES

    def test_the_wavs_are_16k_mono_16bit(self, built):
        """The notebook expects 16 kHz mono. A float WAV here looks fine and
        needs converting on the other side."""
        path = os.path.join(built["dir_path"], "positive", "pos_000.wav")
        with wave.open(path) as handle:
            assert handle.getnchannels() == 1
            assert handle.getsampwidth() == 2
            assert handle.getframerate() == 16000

    def test_it_says_the_false_positive_rate_is_unknown(self, built):
        """Recorded as None on purpose. A default of 0 would be read as a
        measurement, and it is not one."""
        assert built["metadata"]["false_positive_rate"] is None
        assert "not measured" in built["metadata"]["false_positive_note"].lower()

    def test_it_ships_the_colab_instructions(self, built):
        readme = os.path.join(built["dir_path"], "LEIA-ME.txt")
        body = open(readme, encoding="utf-8").read()
        assert "Colab" in body
        assert "falsos positivos" in body.lower()
        # The offline-first exception must be named where the upload happens.
        assert "CLAUDE.md" in body

    def test_the_zip_carries_everything(self, built):
        import zipfile
        with zipfile.ZipFile(built["zip_path"]) as archive:
            names = archive.namelist()
        assert any(n.endswith("metadata.json") for n in names)
        assert any("/positive/" in n for n in names)
        assert any("/negative/" in n for n in names)


# --- the registry -----------------------------------------------------------

class TestRegistry:
    def test_survives_a_restart(self, tmp_path):
        path = str(tmp_path / "w.json")
        WakeWordRegistry(path).upsert(WakeWordEntry(label="Ana", slug="ana"))
        assert [e.label for e in WakeWordRegistry(path).all()] == ["Ana"]

    @pytest.mark.parametrize("label,expected", [
        ("Ana Silva", "ana-silva"),
        ("José  Avelar", "jose-avelar"),
        ("ana silva", "ana-silva"),
        ("", "utilizador"),
        ("...", "utilizador"),
    ])
    def test_slugify(self, label, expected):
        assert slugify(label) == expected

    def test_enrolled_ignores_an_entry_with_no_model(self, tmp_path):
        """Pointing HotwordDetector at a missing .onnx is the failure
        check-wakewords.sh exists to catch before the container starts."""
        registry = WakeWordRegistry(str(tmp_path / "w.json"))
        registry.upsert(WakeWordEntry(label="Ana", slug="ana", model_path=None))
        assert registry.model_paths() == []

    def test_model_paths_drops_a_deleted_file(self, tmp_path):
        registry = WakeWordRegistry(str(tmp_path / "w.json"))
        registry.upsert(WakeWordEntry(label="Ana", slug="ana",
                                      model_path="/tmp/does-not-exist.onnx"))
        assert registry.model_paths() == []

    def test_a_measured_zero_survives_a_reload(self, tmp_path):
        """`rate or None` would turn a real measurement of 0.0 into "unknown"."""
        path = str(tmp_path / "w.json")
        registry = WakeWordRegistry(path)
        registry.upsert(WakeWordEntry(label="Ana", slug="ana"))
        registry.set_false_positive_rate("ana", 0.0)
        assert WakeWordRegistry(path).get("ana").false_positive_rate == 0.0

    def test_unmeasured_is_none_never_zero(self, tmp_path):
        registry = WakeWordRegistry(str(tmp_path / "w.json"))
        registry.upsert(WakeWordEntry(label="Ana", slug="ana"))
        assert registry.get("ana").false_positive_rate is None

    def test_remove_forgets_but_keeps_the_file(self, tmp_path):
        model = tmp_path / "ana.onnx"
        model.write_bytes(b"x")
        registry = WakeWordRegistry(str(tmp_path / "w.json"))
        registry.upsert(WakeWordEntry(label="Ana", slug="ana",
                                      model_path=str(model)))
        assert registry.remove("ana") is True
        assert model.exists(), "a web button is not consent to delete a voice"


# --- the importer -----------------------------------------------------------

class TestModelValidation:
    def test_a_real_model_validates(self):
        result = validate_onnx(REAL_MODEL)
        assert result.ok
        assert result.loaded

    def test_a_missing_file_is_refused(self, tmp_path):
        assert not validate_onnx(str(tmp_path / "nope.onnx")).ok

    def test_a_truncated_download_is_refused(self, tmp_path):
        """Plausible size, broken graph. This is what a half-finished export
        from the notebook actually looks like."""
        truncated = tmp_path / "t.onnx"
        truncated.write_bytes(open(REAL_MODEL, "rb").read()[:120_000])
        result = validate_onnx(str(truncated))
        assert not result.ok
        assert "anterior" in result.reason.lower()

    def test_a_tiny_file_is_refused_before_it_is_opened(self, tmp_path):
        tiny = tmp_path / "t.onnx"
        tiny.write_bytes(b"x" * 50)
        result = validate_onnx(str(tiny))
        assert not result.ok
        assert "bytes" in result.reason

    def test_nonsense_is_refused(self, tmp_path):
        junk = tmp_path / "j.onnx"
        junk.write_bytes(b"nope" * 5000)
        assert not validate_onnx(str(junk)).ok


class TestModelImport:
    def _registry(self, tmp_path):
        path = tmp_path / "w.json"
        registry = WakeWordRegistry(str(path))
        registry.upsert(WakeWordEntry(label="Dono", slug="dono"))
        return registry

    def test_a_good_model_is_installed_and_recorded(self, tmp_path):
        registry = self._registry(tmp_path)
        result = import_model(registry, "dono",
                              open(REAL_MODEL, "rb").read(),
                              models_dir=str(tmp_path / "models"))
        assert result.ok
        assert os.path.isfile(result.path)
        assert registry.get("dono").model_path == result.path
        assert registry.model_paths() == [result.path]

    def test_a_bad_model_changes_nothing(self, tmp_path):
        """The point of the whole module. The previous model must still be
        installed and still in the registry."""
        registry = self._registry(tmp_path)
        good = os.path.join(tmp_path, "models", "dono.onnx")
        os.makedirs(os.path.dirname(good), exist_ok=True)
        open(good, "wb").write(open(REAL_MODEL, "rb").read())
        registry.get("dono").model_path = good

        result = import_model(registry, "dono", b"nope" * 5000,
                              models_dir=os.path.dirname(good))
        assert not result.ok
        assert registry.get("dono").model_path == good
        assert open(good, "rb").read() == open(REAL_MODEL, "rb").read()

    def test_no_staging_file_is_left_behind_on_failure(self, tmp_path):
        registry = self._registry(tmp_path)
        import_model(registry, "dono", b"nope" * 5000,
                     models_dir=str(tmp_path / "models"))
        leftovers = [n for n in os.listdir(tmp_path / "models")
                     if n.endswith(".incoming")]
        assert leftovers == []

    def test_replacing_reports_what_it_replaced(self, tmp_path):
        """Otherwise the owner concludes the retrain did nothing: the old voice
        is still installed and still wakes the assistant."""
        registry = self._registry(tmp_path)
        first = import_model(registry, "dono",
                             open(REAL_MODEL, "rb").read(),
                             models_dir=str(tmp_path / "models"))
        second = import_model(registry, "dono",
                              open(REAL_MODEL, "rb").read(),
                              models_dir=str(tmp_path / "models"))
        assert second.ok
        assert second.replaced == first.path
        assert registry.model_paths() == [second.path], (
            "replacing must not leave two models for one voice")

    def test_empty_upload_is_refused(self, tmp_path):
        assert not import_model(self._registry(tmp_path), "dono", b"").ok
