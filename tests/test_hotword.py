"""End-to-end gate for wake word detection.

These tests are the reason the wake word is claimed to work. Every previous
"it works" claim in this project was untested; the two real defects below were
found only by running real audio through the real detector:

  1. ``HotwordDetector.reset()`` was defined twice. Python kept the second
     definition, so the branch that cleared ``_streaks`` was dead code.
  2. ``reset()`` never called ``oww_model.reset()``. openWakeWord's Model holds
     a rolling mel-spectrogram buffer seeded with RANDOM audio at construction;
     without resetting it, the first frames of a wake word are scored against
     that random buffer. Measured effect on "olá fantasma": 2 frames above
     threshold instead of 3 -> streak never reached persistence -> no fire.
  3. ``patience`` was a local in ``process()``, re-initialised every frame, so
     the legacy's dip tolerance never engaged.

The fixtures in tests/fixtures/ are 16 kHz mono int16 PCM generated with the
project's own piper voice (pt_PT-dii-high). They are synthetic, so they prove
the *chain* works end to end and that the models discriminate speech from
non-speech. They are not a substitute for a human speaking into the real
microphone.
"""

import wave
from pathlib import Path

import numpy as np
import pytest

import config
from src.pipeline.audio import HotwordDetector

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> np.ndarray:
    path = FIXTURES / f"{name}.wav"
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000, f"{name} must be 16 kHz"
        assert w.getnchannels() == 1, f"{name} must be mono"
        assert w.getsampwidth() == 2, f"{name} must be 16-bit"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


@pytest.fixture(scope="module")
def detector() -> HotwordDetector:
    if not config.WAKEWORD_MODELS:
        pytest.skip("no WAKEWORD_MODELS configured")
    for model in config.WAKEWORD_MODELS:
        if not Path(model).exists():
            pytest.skip(f"wake word model missing: {model}")
    return HotwordDetector(models=config.WAKEWORD_MODELS)


@pytest.fixture(autouse=True)
def restore_tuning(detector):
    """The detector fixture is module-scoped (model load is expensive), so any
    test that overrides tuning must not leak it into later tests."""
    saved = (detector._persistence, detector.threshold, dict(detector._thresholds))
    yield
    (
        detector._persistence,
        detector.threshold,
        detector._thresholds,
    ) = saved


# Tuning the assistant must not be able to break these tests. Previously the
# detector inherited persistence and the per-model thresholds straight from the
# live .env, so changing WAKEWORD_PERSISTENCE in production turned a passing
# test red -- and the failure looked like a code bug rather than a config edit.
# These are the values the test suite is calibrated against, stated here so the
# suite is self-contained.
TEST_PERSISTENCE = 2
TEST_THRESHOLD = 0.70
TEST_THRESHOLDS = {"ola_fantasma": 0.70}


@pytest.fixture(autouse=True)
def pin_tuning(detector):
    """Pin tuning to the values these tests are calibrated for, then restore.

    Applies before restore_tuning's teardown so the original values are still
    captured correctly regardless of fixture ordering.
    """
    saved = (detector._persistence, detector.threshold, dict(detector._thresholds))
    detector._persistence = TEST_PERSISTENCE
    detector.threshold = TEST_THRESHOLD
    detector._thresholds = dict(TEST_THRESHOLDS)
    yield
    (
        detector._persistence,
        detector.threshold,
        detector._thresholds,
    ) = saved


def peak_score(det: HotwordDetector, audio: np.ndarray) -> float:
    """Highest score across the clip, with a clean model state."""
    det.reset()
    best = 0.0
    for i in range(len(audio) // 1280):
        scores = det.oww_model.predict(audio[i * 1280 : (i + 1) * 1280])
        if scores:
            best = max(best, max(scores.values()))
    return best


def run_detector(det: HotwordDetector, audio: np.ndarray, chunk: int = 1280):
    """Feed audio through the real process() path and return the first hit.

    Defaults to 1280 because HotwordDetector now buffers to that internally.
    Pass a different chunk to assert framing independence -- production runs at
    512 (config.audio.block_size) and that mismatch is exactly what let the
    persistence counter silently count repeated scores.
    """
    det.reset()
    for i in range(len(audio) // chunk):
        detected, model = det.process(audio[i * chunk : (i + 1) * chunk])
        if detected:
            return i, model
    return None, None


# --- Structural guards for the two silent regressions ------------------------


def test_reset_is_defined_exactly_once(detector):
    """A duplicated reset() silently shadows the correct implementation."""
    import ast
    import inspect

    from src.pipeline import audio as audio_mod

    tree = ast.parse(Path(audio_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "HotwordDetector":
            names = [f.name for f in node.body if isinstance(f, ast.FunctionDef)]
            dupes = sorted({n for n in names if names.count(n) > 1})
            assert not dupes, f"duplicated method(s) in HotwordDetector: {dupes}"
    assert "oww_model.reset()" in inspect.getsource(HotwordDetector.reset)


def test_reset_clears_streaks_patience_and_model(detector):
    detector._streaks["x"] = 5
    detector._patience["x"] = 2
    detector._last_detection = 12345.0
    detector.reset()
    assert detector._streaks == {}
    assert detector._patience == {}
    assert detector._last_detection == 0.0


def test_patience_tolerates_dips_like_the_legacy(detector, monkeypatch):
    """Legacy sequence 0.9, 0.9, dip, dip, 0.9 must FIRE on the last frame.

    Two dips are tolerated (MAX_PATIENCE=2), so the streak survives to 3.
    Persistence is pinned to 3 here so this test exercises the state machine
    rather than whatever tuning .env currently selects.
    """
    sequence = [0.9, 0.9, 0.1, 0.1, 0.9]
    detector.reset()
    detector._persistence = 3
    fired_at = None
    for idx, value in enumerate(sequence):
        monkeypatch.setattr(
            detector.oww_model, "predict", lambda _x, _v=value: {"ola_fantasma": _v}
        )
        detected, _ = detector.process(np.zeros(1280, dtype=np.int16))
        if detected:
            fired_at = idx
            break
    assert fired_at == 4, f"expected fire on final frame, got {fired_at}"


def test_streak_resets_after_patience_exhausted(detector, monkeypatch):
    """A third dip with no recovery must zero the streak (no false trigger)."""
    sequence = [0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
    detector.reset()
    detector._persistence = 3
    for value in sequence:
        monkeypatch.setattr(
            detector.oww_model, "predict", lambda _x, _v=value: {"ola_fantasma": _v}
        )
        detected, _ = detector.process(np.zeros(1280, dtype=np.int16))
        assert not detected, "fired without ever reaching persistence"


# --- Real audio --------------------------------------------------------------


def test_wake_word_models_are_registered(detector):
    keys = set(detector.oww_model.models.keys())
    assert keys, "no wake word models loaded"
    # hey_fantasma was removed from production on 2026-09-27 by owner
    # decision. Its per-model threshold made it work, but one phrase is one
    # less thing to misfire. The model file is still on disk.
    assert keys & {"ola_fantasma"}, keys


@pytest.mark.parametrize(
    "fixture,expected_model",
    [("wake_ola_fantasma", "ola_fantasma")],
)
def test_wake_phrase_fires_end_to_end(detector, fixture, expected_model):
    frame, model = run_detector(detector, load_fixture(fixture))
    assert model == expected_model, f"expected {expected_model}, got {model} at frame {frame}"


@pytest.mark.parametrize("chunk", [512, 640, 1280])
def test_detection_is_independent_of_block_size(detector, chunk):
    """Production feeds 512-sample blocks; the fixtures are 1280.

    Before the detector buffered to a full 80ms window, these two disagreed:
    the same audio fired at persistence 3 with 512-sample input and never fired
    with 1280, because openWakeWord only emits a new score every 1280 samples
    and the repeats were being counted as independent evidence.
    """
    frame, model = run_detector(detector, load_fixture("wake_ola_fantasma"), chunk=chunk)
    assert model == "ola_fantasma", f"block size {chunk} changed the verdict ({model})"


def test_wake_phrase_beats_the_noise_floor(detector):
    """The wake phrase must score far above the non-speech floor.

    This is the discrimination claim: the model responds to the phrase, not to
    any 16 kHz audio. Absolute firing also depends on how many 80 ms frames
    clear the threshold, which synthetic speech compresses.
    """
    score = peak_score(detector, load_fixture("wake_ola_fantasma"))
    assert score > 0.5, f"wake_ola_fantasma peak score {score:.4f} is not above 0.5"


@pytest.mark.parametrize("fixture", ["negative_boa_tarde", "negative_silence"])
def test_non_wake_phrases_never_fire(detector, fixture):
    frame, model = run_detector(detector, load_fixture(fixture))
    assert model is None, f"{fixture} falsely triggered {model} at frame {frame}"
    assert peak_score(detector, load_fixture(fixture)) < 0.5


def test_wake_scores_exceed_negative_scores_by_a_wide_margin(detector):
    """The separation must be orders of magnitude, not a rounding difference."""
    positive = peak_score(detector, load_fixture("wake_ola_fantasma"))
    negative = max(
        peak_score(detector, load_fixture("negative_boa_tarde")),
        peak_score(detector, load_fixture("negative_silence")),
    )
    assert positive > negative * 50, f"positive={positive:.4f} negative={negative:.4f}"


def test_model_paths_are_absolute(detector):
    """Relative model paths resolve only if the process CWD happens to match."""
    for model in config.WAKEWORD_MODELS:
        assert Path(model).is_absolute(), f"relative wake word model path: {model}"


# --- VAD framing -------------------------------------------------------------


def test_vad_does_not_discard_audio(detector):
    """512-sample blocks must not lose 6.25% of themselves into 480-byte frames.

    process_chunk used to compute len(chunk) // frame_size and return, throwing
    away the 32-sample remainder on every block -- a 2ms gap every 32ms, with
    no carry-over. VAD ran on permanently chopped audio, which is what made
    end-of-speech unreliable. Guard the arithmetic directly rather than inferring
    it from VAD behaviour.
    """
    from src.pipeline.audio import VADProcessor

    vad = VADProcessor(aggressiveness=2, sample_rate=16000, frame_duration_ms=30)
    assert vad.frame_size == 480, vad.frame_size

    total_in = 0
    total_out = 0
    vad.reset()
    # Production block size is 512; feed enough to span several 480 frames.
    for _ in range(100):
        vad.process_chunk(np.zeros(512, dtype=np.int16))
    # Reconstruct what the VAD actually emitted by feeding a known signal and
    # counting frames: 100 blocks * 512 = 51200 samples -> 106 full frames.
    vad.reset()
    for _ in range(100):
        frames = vad.process_chunk(np.zeros(512, dtype=np.int16))
        total_out += len(frames)
        total_in += 512

    expected_full = total_in // 480
    assert total_out == expected_full, (
        f"emitted {total_out} frames, expected {expected_full} "
        f"({total_in} samples in) -- audio is being dropped"
    )
    # The remainder must be carried, not discarded: it is worth up to one frame.
    assert len(vad._accum) == total_in - expected_full * 480


def test_vad_reset_drops_partial_frame():
    from src.pipeline.audio import VADProcessor

    vad = VADProcessor(aggressiveness=2, sample_rate=16000, frame_duration_ms=30)
    vad.process_chunk(np.zeros(500, dtype=np.int16))
    assert len(vad._accum) > 0, "expected a carried partial frame"
    vad.reset()
    assert len(vad._accum) == 0, "reset must not leave a stale partial frame"


def test_negative_silence_fixture_is_actually_silence(detector):
    """The 'negative_silence' fixture must BE silence.

    It used to be -15.3 dBFS -- louder than live speech is quiet -- so the VAD
    reported speech on 84% of it and the name was a lie. It was regenerated on
    2026-09-27 from 3s of real room capture on the Jabra (-53 dBFS). Guard the
    amplitude, not just the score, so a mislabelled file cannot come back.
    """
    audio = load_fixture("negative_silence").astype(np.float32)
    rms = float(np.sqrt(np.mean(audio**2)))
    dbfs = 20 * np.log10(rms / 32768) if rms > 0 else -120.0
    assert dbfs < -40, (
        f"negative_silence is at {dbfs:.1f} dBFS -- that is not silence. "
        f"Live room noise measures about -53 dBFS."
    )
