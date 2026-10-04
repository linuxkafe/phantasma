"""A calibration is only as good as the loudest window of the room.

2026-10-04, second pass. The first calibration (-42/-22) cleared the MEDIAN
window and failed the loudest 3.2% of them: the bar reached 0.771 while a real
"ola fantasma" scores 0.7677. That is the worst failure mode there is -- it
works most of the time, so it looks fixed right up until it is not.

So the room is modelled here as a distribution, not a point, and the bar is
checked at every measured level with the real NoiseFloor dynamics (which lag
and track) rather than a single static penalty. Anyone who retunes the
calibration gets told which window they broke.
"""

import numpy as np

from src.pipeline.noise import NoiseFloor

# Measured in this room on 2026-10-04: service stopped, mic at the service's
# own gain, 15 s of room tone through the same 80 ms windows the detector scores.
MEASURED = {
    "floor": -40.5,
    "median": -38.2,
    "p90": -35.8,
    "max": -34.5,
}

# One genuine utterance, scored by the service on 2026-10-04.
REAL_SPEECH = 0.7677

QUIET_DB = -38.0
LOUD_DB = -20.0
BASE = 0.70


def _steady_state_bar(level_db, quiet, loud, seconds=40.0):
    """Feed sustained noise at `level_db` and return the settled bar.

    NoiseFloor has memory; its penalty is not a pure function of the level it is
    handed. A calibration validated only against a single static penalty can be
    wrong in production, so this drives the real object.
    """
    rng = np.random.default_rng(7)
    n_win = int(seconds / 0.08)
    amp = 32768.0 * (10.0 ** (level_db / 20.0))
    x = rng.normal(0.0, amp, n_win * 1280).astype(np.int16)
    n = NoiseFloor(quiet_db=quiet, loud_db=loud, max_bump=0.20)
    bar = BASE
    for i in range(n_win):
        n.update(x[i * 1280:(i + 1) * 1280])
        bar = min(BASE + n.penalty(), 0.99)
    return bar


def test_real_speech_clears_every_measured_window_of_the_room():
    """The whole distribution, not its average.

    This is the assertion that rejects -42. It exists because a calibration can
    be right on paper and fail in the top few percent of windows, and the top
    few percent is where "works most of the time" comes from.
    """
    for name, level in MEASURED.items():
        bar = _steady_state_bar(level, QUIET_DB, LOUD_DB)
        assert bar <= REAL_SPEECH, (
            f"janela '{name}' ({level} dBFS) poe a barra em {bar:.3f} e a fala "
            f"real mede {REAL_SPEECH}: a casa volta a nao ouvir-se em {name}"
        )


def test_the_rejected_calibration_is_pinned_as_the_failing_case():
    """-42/-22 is kept only as evidence.

    "We changed it and it works" is weaker than "we know precisely what was
    wrong", so the broken numbers stay in the tree next to the working ones.
    """
    bar = _steady_state_bar(MEASURED["max"], -42.0, -22.0)
    assert bar > REAL_SPEECH, (
        "-42/-22 ja nao falha na janela mais alta; revejo a medicao antes de "
        "voltar a escrever -42 num default"
    )


def test_a_genuinely_loud_room_still_gets_the_full_penalty():
    """The adaptation must survive being retuned.

    max_bump is what stands between the assistant and another 03:41: speaking
    "Sim." at three in the morning because an empty house scored 0.80.
    """
    bar = _steady_state_bar(-20.0, QUIET_DB, LOUD_DB)
    assert bar >= 0.85, f"numa sala alta a barra desceu para {bar:.3f}"
