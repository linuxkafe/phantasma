"""The ambient noise floor that moves the wake word threshold.

Anchored on a real event: 2026-09-29, 03:41, inside quiet hours, a hotword
fired at 0.80 against a flat 0.70, said "Sim.", transcribed nothing, and woke
an empty house. These tests pin the two properties that make the fix work and
the one trap that would make it harmful.
"""

import numpy as np
import pytest

from src.pipeline.noise import NoiseFloor, rms_dbfs


def _tone(dbfs: float, n: int = 1280, seed: int = 0) -> np.ndarray:
    """White noise at approximately the given dBFS."""
    rms = 32768.0 * (10 ** (dbfs / 20.0))
    rng = np.random.default_rng(seed)
    return (rng.normal(0.0, rms, n)).astype(np.int16)


def _sustained(floor: NoiseFloor, samples: np.ndarray, windows: int) -> None:
    for _ in range(windows):
        floor.update(samples)


class TestLevel:
    def test_silence_is_not_minus_infinity(self):
        assert rms_dbfs(np.zeros(1280, dtype=np.int16)) == -90.0

    def test_empty_input_is_safe(self):
        assert rms_dbfs(np.array([], dtype=np.int16)) == -90.0

    def test_level_is_ordered(self):
        quiet = rms_dbfs(_tone(-60))
        loud = rms_dbfs(_tone(-30))
        assert quiet < loud


class TestColdStart:
    """An unheard room must not raise anything.

    The floor is None until the room has been heard, and None means no
    penalty. Otherwise a restart at 3am would make the assistant harder to
    wake than it is configured to be, silently.
    """

    def test_unknown_floor_has_no_penalty(self):
        assert NoiseFloor().penalty() == 0.0

    def test_describe_says_unknown(self):
        assert "unknown" in NoiseFloor().describe()

    def test_floor_becomes_known_after_one_window(self):
        floor = NoiseFloor()
        assert floor.known is False
        floor.update(_tone(-45))
        assert floor.known is True


class TestAsymmetry:
    """The trap: the loudest sound is the user saying the wake word.

    If the estimate tracked the instantaneous level, the bar would rise
    exactly when someone is trying to be heard, and the assistant would go
    deaf in precisely the moment that matters.
    """

    def test_quieter_room_is_adopted_immediately(self):
        floor = NoiseFloor()
        floor.update(_tone(-30))
        floor.update(_tone(-70))
        assert floor.db == pytest.approx(rms_dbfs(_tone(-70)), abs=0.5)

    def test_one_second_of_speech_cannot_lift_the_floor(self):
        """The invariant that matters is the threshold, not the raw dB.

        alpha=0.005 does move the floor a couple of dB over 12 windows; what
        must not happen is the BAR moving enough to miss a real wake word.
        """
        floor = NoiseFloor(rise_alpha=0.005, quiet_db=-60.0, loud_db=-35.0, max_bump=0.20)
        _sustained(floor, _tone(-70), 20)
        before = floor.penalty()
        # A wake word is about a second: 12 windows of 80ms.
        _sustained(floor, _tone(-20, seed=1), 12)
        assert floor.penalty() - before < 0.02, "one second of speech raised the bar"

    def test_a_minute_of_hum_does_lift_the_bar(self):
        """The contrast that makes the previous test meaningful."""
        floor = NoiseFloor(rise_alpha=0.005, quiet_db=-60.0, loud_db=-35.0, max_bump=0.20)
        _sustained(floor, _tone(-70), 20)
        before = floor.penalty()
        _sustained(floor, _tone(-30, seed=2), 750)  # 60s
        assert floor.penalty() - before > 0.10

    def test_sustained_noise_does_lift_the_floor(self):
        floor = NoiseFloor(rise_alpha=0.05)
        _sustained(floor, _tone(-70), 10)
        _sustained(floor, _tone(-30), 100)
        assert floor.db > -60.0

    def test_digital_silence_does_not_drag_the_floor_down(self):
        floor = NoiseFloor()
        floor.update(_tone(-30))
        before = floor.db
        _sustained(floor, np.zeros(1280, dtype=np.int16), 50)
        assert floor.db == before, "zeros walked the floor towards -inf"


class TestPenalty:
    def test_quiet_room_is_untouched(self):
        floor = NoiseFloor(quiet_db=-60.0, loud_db=-35.0, max_bump=0.20)
        _sustained(floor, _tone(-75), 5)
        assert floor.penalty() == 0.0

    def test_loud_room_gets_the_full_bump(self):
        floor = NoiseFloor(quiet_db=-60.0, loud_db=-35.0, max_bump=0.20)
        _sustained(floor, _tone(-25), 5)
        assert floor.penalty() == pytest.approx(0.20)

    def test_penalty_ramps_between_the_bounds(self):
        floor = NoiseFloor(quiet_db=-60.0, loud_db=-35.0, max_bump=0.20)
        _sustained(floor, _tone(-47.5), 5)
        assert 0.0 < floor.penalty() < 0.20

    def test_disabling_removes_every_penalty(self):
        floor = NoiseFloor(max_bump=0.0)
        _sustained(floor, _tone(-20), 5)
        assert floor.penalty() == 0.0

    def test_degenerate_span_does_not_divide_by_zero(self):
        floor = NoiseFloor(quiet_db=-40.0, loud_db=-40.0, max_bump=0.20)
        _sustained(floor, _tone(-20), 5)
        assert floor.penalty() == pytest.approx(0.20)


class TestThe0341Regression:
    """The exact event, at the size it happened.

    Score 0.80 in a room noisy enough to trigger it, against a base of 0.70.
    """

    def test_noisy_room_raises_the_bar_above_the_false_positive(self):
        floor = NoiseFloor(quiet_db=-60.0, loud_db=-35.0, max_bump=0.20)
        _sustained(floor, _tone(-22), 60)
        assert 0.70 + floor.penalty() > 0.80

    def test_quiet_room_leaves_the_false_positive_reachable(self):
        """Not a fix for one score: a genuinely quiet room is unchanged."""
        floor = NoiseFloor()
        _sustained(floor, _tone(-75), 60)
        assert 0.70 + floor.penalty() == 0.70


class TestEffectiveThreshold:
    """The detector's own arithmetic, without loading a model."""

    @staticmethod
    def _detector(base=0.70, per_model=None, max_bump=0.20):
        from src.pipeline.audio import HotwordDetector

        # Built by hand: the real __init__ loads openWakeWord models, which is
        # not what is under test here.
        d = object.__new__(HotwordDetector)
        d.threshold = base
        d._thresholds = dict(per_model or {})
        d._noise = NoiseFloor(quiet_db=-60.0, loud_db=-35.0, max_bump=max_bump)
        return d

    def test_base_threshold_until_the_room_is_heard(self):
        d = self._detector()
        assert d.effective_threshold("ola_fantasma") == 0.70

    def test_per_model_base_is_preserved(self):
        d = self._detector(per_model={"hey_fantasma": 0.50})
        assert d.effective_threshold("hey_fantasma") == 0.50

    def test_noisy_room_raises_the_per_model_bar_too(self):
        d = self._detector(per_model={"hey_fantasma": 0.50})
        _sustained(d._noise, _tone(-22), 60)
        assert d.effective_threshold("hey_fantasma") == pytest.approx(0.70)

    def test_threshold_can_never_reach_one(self):
        d = self._detector(base=0.95, max_bump=0.20)
        _sustained(d._noise, _tone(-10), 60)
        assert d.effective_threshold("ola_fantasma") == 0.99

    def test_noise_state_is_reportable(self):
        d = self._detector()
        _sustained(d._noise, _tone(-25), 5)
        assert "dBFS" in d.noise_state()
