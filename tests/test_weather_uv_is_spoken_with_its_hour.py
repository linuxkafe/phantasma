"""A reading that swings must be spoken with the hour it belongs to.

2026-10-04: the assistant said "o UV está moderado (3.45)". Nothing was
numerically false. Open-Meteo really does serve 3.45 for Lisboa -- at 12:00 and
again at 15:00 -- but the value it returned was an hourly observation and the
sentence claimed it in the bare present tense. Over that day the same index ran
0.1 -> 4.35 -> 0.0. A number without its hour is not a reading; it is a rumour.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from skills.skill_weather import _air_and_uv_sentence, _uv_clock  # noqa: E402


def test_the_hour_is_spoken():
    s = _air_and_uv_sentence(
        {"uv_index": 3.45, "uv_observed_at": "2026-10-04T15:00"},
        "moderada", "moderado")
    assert "3.45" in s
    assert "15h" in s, f"a hora de observacao desapareceu: {s!r}"


def test_no_bare_present_tense_when_the_hour_is_unknown():
    """Without an hour we must not claim the present.

    This is the honest degradation: admit the gap rather than assert a stale
    number as current.
    """
    s = _air_and_uv_sentence({"uv_index": 3.45}, "moderada", "moderado")
    assert "3.45" in s
    assert "medido" not in s
    assert "não" in s or "hora" in s or "atualiz" in s, (
        f"sem hora conhecida a frase ainda afirma o presente: {s!r}"
    )


def test_missing_uv_is_not_invented():
    s = _air_and_uv_sentence({"uv_index": None}, "boa", "desconhecido")
    assert "None" not in s
    assert "desconhecido" in s


def test_the_clock_tolerates_junk():
    for junk in (None, "", "ontem", 17, []):
        assert _uv_clock(junk) is None or isinstance(_uv_clock(junk), int)
