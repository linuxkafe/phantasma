"""The measuring instrument for reading fidelity, tested.

Added 2026-10-04 after two consecutive runs reported 14% and 57% for the same
kind of answer. Both numbers came from a broken detector: the models spell
numbers out ("vinte e um ponto quatro") and the first two versions only looked
for digits. A measurement that reports the model as unreliable when the
instrument is unreliable is worse than no measurement.
"""
import pytest

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
