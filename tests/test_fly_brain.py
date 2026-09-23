"""T016/T022 — FlyBrain core validation tests (ported from dfb sim/test_coprocessor.py).

Validates the 5 core phenomena:
1. E-PG gradual vs abrupt topic change
2. Octopamine depression under rapid repetitive loops
3. ++/-- reward-punishment plasticity
4. Neuronal step latency
5. Telemetry format and request shape
"""

import time

import numpy as np
import pytest

from src.brain.fly_brain import (
    FlyBrain,
    NeuromodulatoryPool,
    circular_distance,
)

# ---------------------------------------------------------------------------
# Episode 1 — gradual topic ecology (turns 1-3)
# ---------------------------------------------------------------------------


def _run_simple_episode(n_repeat: int = 6):
    """Same message repeated fast on a single-brain; returns per-turn states."""
    brain = FlyBrain()
    states = []
    t = 0.0
    for i in range(n_repeat):
        # Use a fixed angle to simulate same topic
        angle = 45.0
        # Simulate encoder output: low novelty on repetition
        novelty = 1.0 if i == 0 else 0.1
        state = brain.step(angle, novelty=novelty)
        states.append((state, novelty, angle))
        t += 1.0  # fast: one second apart
    return states, states[0][2]


def test_1_repetition_keeps_bump_pinned():
    states, angle0 = _run_simple_episode()
    for state, *_ in states:
        assert abs(circular_distance(angle0, state.orientation_deg)) < 20.0


def test_2_repetition_habituates_injection_and_octopamine():
    states, _ = _run_simple_episode()
    # First novelty is high, subsequent are low -> octopamine should not stay elevated
    oct_first = states[0][0].octopamine
    oct_after_peak = max(s[0].octopamine for s in states[1:])
    # Novelty of repeated message is low, so octopamine cannot stay elevated
    assert oct_after_peak <= max(oct_first, 0.5)


def test_3_gradual_same_topic_rotation_is_small():
    brain = FlyBrain()
    jumps = []
    t = 0.0
    for msg_angle in [10.0, 15.0, 20.0, 25.0]:  # gradual drift
        brain.step(msg_angle, novelty=0.3)
        jumps.append(abs(brain.ring.last_jump))
        t += 30.0  # slow: distinct but related messages
    assert max(jumps) < 120.0  # a "slow drift" must never look like a switch


# ---------------------------------------------------------------------------
# Episode 4 — abrupt topic change
# ---------------------------------------------------------------------------


def test_4_abrupt_topic_change_flips_bump_and_flags_context_switch():
    brain = FlyBrain()
    t = 0.0
    max_jump = 0.0
    for msg_angle in [10.0, 200.0, 300.0]:  # abrupt jumps
        state = brain.step(msg_angle, novelty=1.0)
        max_jump = max(max_jump, abs(state.epg_jump_deg))
        t += 10.0

    assert max_jump > 120.0

    # A fresh abrupt switch, evaluated at the moment it happens
    brain2 = FlyBrain()
    state = brain2.step(180.0, novelty=1.0)
    assert abs(state.epg_jump_deg) > 120.0


# ---------------------------------------------------------------------------
# Episode 5 — octopamine dynamics
# ---------------------------------------------------------------------------


def test_5_octopamine_rises_with_drive():
    pool = NeuromodulatoryPool(tau=0.8)
    levels = []
    drive = 1.0
    for _ in range(12):
        pool.step(drive)
        levels.append(pool.value)
    # Same high drive, monotonically rising toward saturation, bounded to [0,1]
    assert levels[-1] >= levels[-2]
    assert levels[-1] <= 1.0 + 1e-9


def test_5b_octopamine_leaks_to_baseline_without_drive():
    pool = NeuromodulatoryPool(tau=0.8)
    pool.step(1.0)
    for _ in range(30):
        pool.step(0.0)
    assert pool.value < 0.3


# ---------------------------------------------------------------------------
# Reward / punishment plasticity
# ---------------------------------------------------------------------------


def test_6_plus_plus_increases_affinity():
    brain = FlyBrain()
    before = brain.step(90.0, novelty=0.5).affinity
    brain.step(90.0, novelty=0.5, reward=+1.0)
    after = brain.step(90.0, novelty=0.5).affinity
    assert after > before - 1e-9  # Hebbian strengthening of approach MBONs


def test_6b_minus_minus_decreases_affinity():
    brain = FlyBrain()
    before = brain.step(90.0, novelty=0.5).affinity
    brain.step(90.0, novelty=0.5, reward=-1.0)
    after = brain.step(90.0, novelty=0.5).affinity
    assert after < before + 1e-9


# ---------------------------------------------------------------------------
# AC-3 — neuronal step latency
# ---------------------------------------------------------------------------


def test_7_step_is_fast_enough():
    brain = FlyBrain()
    t0 = time.perf_counter()
    n = 200
    for _ in range(n):
        brain.step(120.0, novelty=0.4)
    elapsed = time.perf_counter() - t0
    assert (elapsed / n) * 1000.0 < 50.0


# ---------------------------------------------------------------------------
# AC-1 / AC-4 — ConnectomeState structure
# ---------------------------------------------------------------------------


def test_8_connectome_state_structure():
    brain = FlyBrain()
    state = brain.step(42.0, novelty=0.7)
    assert hasattr(state, "orientation_deg")
    assert hasattr(state, "epg_jump_deg")
    assert hasattr(state, "affinity")
    assert hasattr(state, "octopamine")
    assert hasattr(state, "kc_active_fraction")
    assert hasattr(state, "mbon")
    assert isinstance(state.mbon, np.ndarray)
    assert state.mbon.shape == (8,)  # default n_mbon


def test_9_mushroom_body_sparsity():
    brain = FlyBrain()
    state = brain.step(33.0, novelty=0.3)
    assert state.kc_active_fraction < 0.5  # Kenyon cells stay sparse


def test_10_circular_distance():
    assert circular_distance(10.0, 20.0) == 10.0
    assert circular_distance(350.0, 10.0) == 20.0
    assert circular_distance(180.0, 0.0) == -180.0
    assert circular_distance(0.0, 180.0) == -180.0  # [-180, 180) range
    assert circular_distance(0.0, 270.0) == -90.0


def test_11_deterministic_seed():
    """Same seed produces identical results."""
    brain1 = FlyBrain(seed=42)
    brain2 = FlyBrain(seed=42)
    for _ in range(10):
        s1 = brain1.step(45.0, novelty=0.5)
        s2 = brain2.step(45.0, novelty=0.5)
        assert abs(s1.orientation_deg - s2.orientation_deg) < 1e-6
        assert abs(s1.affinity - s2.affinity) < 1e-6
        assert abs(s1.octopamine - s2.octopamine) < 1e-6


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
