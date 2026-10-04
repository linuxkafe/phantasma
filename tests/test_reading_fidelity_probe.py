"""The measuring instrument for reading fidelity, tested.

Added 2026-10-04 after two consecutive runs reported 14% and 57% for the same
kind of answer. Both numbers came from a broken detector: the models spell
numbers out ("vinte e um ponto quatro") and the first two versions only looked
for digits. A measurement that reports the model as unreliable when the
instrument is unreliable is worse than no measurement.
"""
import pytest

from src.pipeline.noise import NoiseFloor
from tests.fidelity_probe import present

SURVIVES = [
    ("entre sete e quinze graus Celsius", "7"),
    ("entre sete e quinze graus Celsius", "15"),
    ("vinte e um ponto quatro graus", "21.4"),
    ("oito e sete quilowatts-hora", "8.7"),
    ("nove virgula um quilowatt-hora", "9.1"),
    ("oito virgula sete kWh", "8.7"),
    ("12 microgramas por metro cubico e 6,1 miligramas", "12"),
    ("12 microgramas por metro cubico e 6,1 miligramas", "6.1"),
    ("vinte e quatro graus, sessenta e um por cento", "24"),
    ("vinte e quatro graus, sessenta e um por cento", "61"),
    ("vento dezoito quilometros", "18"),
    ("qualidade do ar e boa, UV 2.85", "2.85"),
    ("CO2 812 ppm, humidade 44%", "812"),
    ("CO2 812 ppm, humidade 44%", "44"),
    ("a casa consumiu 8,7 kWh hoje", "8.7"),
    # money is quoted in euros and cents, not as one decimal
    ("oito ponto sete kWh e tres euros e quarenta e dois centimos", "3.42"),
    ("três euros e quarenta e dois cêntimos", "3.42"),
    # a comma after an integer separates a list, it is not a decimal point
    ("indice 2, particulas 12", "2"),
]

LOST = [
    # paraphrase: the figure is gone, replaced by an adjective. This is the
    # failure the prompt rule exists to prevent.
    ("o tempo hesita entre frio e calor, sem decidir", "7"),
    ("o tempo hesita entre frio e calor, sem decidir", "15"),
    ("vento a dezassete quilometros por hora", "18"),
    ("nao ha numeros aqui de todo", "812"),
    ("a temperatura e alta", "21.4"),
]


@pytest.mark.parametrize("answer,value", SURVIVES)
def test_detects_figures_that_survived(answer, value):
    assert present(answer, value), f"{value!r} should be found in {answer!r}"


@pytest.mark.parametrize("answer,value", LOST)
def test_detects_figures_that_were_lost(answer, value):
    assert not present(answer, value), (
        f"{value!r} should NOT be found in {answer!r}")


# --- the wake-word calibration, added 2026-10-04 -----------------------------
#
# The house stopped answering "ola fantasma" on 2026-10-04. Not a weak model:
# a genuine utterance scored 0.7677 and the bar in force was 0.856, because the
# noise floor in this room settles at -40.5 dBFS and the calibration charged
# +0.156 for it. The threshold sat above anything the model produces for speech.
#
# What is asserted here is the arithmetic, not the room: given a measured floor,
# does the bar land where it must for a real utterance to clear it? The room
# measurement itself is in config.py, next to the defaults it produced.



def _bar(quiet_db, loud_db, max_bump, base, floor_db):
    n = NoiseFloor(quiet_db=quiet_db, loud_db=loud_db, max_bump=max_bump,
                   initial_db=floor_db)
    return min(base + n.penalty(), 0.99)


def test_the_measured_room_leaves_a_real_utterance_reachable():
    """A genuine "ola fantasma" scores 0.7677 in this room. It must clear."""
    bar = _bar(-38.0, -20.0, 0.20, 0.70, -40.5)
    assert bar <= 0.7677, (
        f"a barra em vigor seria {bar:.3f} e a fala mede 0.7677 -- a casa "
        f"fica surda ao seu proprio nome de wakes"
    )


def test_a_loud_room_still_gets_the_full_penalty():
    """The recalibration must not turn the adaptation off.

    The 03:41 false positive -- 0.80 in an empty house, playing "Sim." at 3am --
    is what max_bump exists to prevent. A loud room has to put the bar back up.
    """
    bar = _bar(-38.0, -20.0, 0.20, 0.70, -20.0)
    assert bar >= 0.85, f"numa sala alta a barra desceu para {bar:.3f}"


def test_the_old_calibration_is_what_silenced_the_house():
    """Pinned so the regression cannot come back unnoticed.

    -60/-35 was never measured on this hardware. It is kept here as the failing
    case, because "we changed it and it works" is not the same as "we know why
    it was broken".
    """
    bar = _bar(-60.0, -35.0, 0.20, 0.70, -40.5)
    assert bar > 0.7677, (
        "a calibracao antiga ja nao silenciaria a casa; revejo a medicao antes "
        "de voltar a escrever -60/-35 num default"
    )
