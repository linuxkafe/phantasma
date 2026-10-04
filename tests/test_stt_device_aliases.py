"""The model has to be told, and the registry has to agree.

Measured 2026-10-04. The owner said "liga o exautor"; Whisper transcribed
"Liga o exaustório"; `skill_tuya` searched for "exaustor" and found nothing,
because the accent is how the word sounds. The phrase then fell through to the
model, which searched the WEB for "Liga o exaustório" and replied that it did
not know how to turn an extractor on. Two extractors were configured the whole
time.

Two repairs, and only one of them is a repair of the model:

  * `WHISPER_INITIAL_PROMPT` was set in prod's .env and never passed to
    `transcribe`. The prompt already said "Liga o exaustor." three lines above
    the failure. A setting written down and never read is indistinguishable
    from not having it.
  * `initial_prompt` biases a decoder, it does not constrain it. So the alias
    pass below rewrites a misheard device name to the canonical noun -- the
    guarantee that the bias failed to give.
"""

import pytest

import skills.skill_tuya as tuya
from config import config
from src.pipeline.stt import _canonical_device_names
from text_norm import fold


@pytest.fixture
def house(monkeypatch):
    monkeypatch.setattr(config, "TUYA_DEVICES", {
        "Exaustor do WC": {}, "Exaustor da Sala": {},
        "Luz do Quarto": {}, "Desumidificador do Armário": {},
    }, raising=False)
    return config


def test_the_accent_alone_is_enough_because_the_noun_is_a_prefix(house):
    assert "exaustor" not in "liga o exaustório."
    assert _canonical_device_names("Liga o exaustório.") == "Liga o exaustor."


def test_a_word_the_owner_already_says_correctly_is_left_alone(house):
    assert _canonical_device_names("liga o exaustor") is None


def test_every_alias_reaches_the_canonical_noun(house):
    for spoken, expected in (
        ("liga a ventoinha do wc", "liga a exaustor do wc"),
        ("acende o fan da sala", "acende o exaustor da sala"),
        ("liga o extractor", "liga o exaustor"),
    ):
        assert _canonical_device_names(spoken) == expected, spoken


def test_a_room_is_not_turned_into_a_device(house):
    """Naming the type must not pin the command to one device.

    An earlier version rewrote the noun into the full registered nickname, so
    "liga o exaustor do wc" came out as "liga o Exaustor do WC do wc" and a
    request for every extractor became a request for one.
    """
    out = _canonical_device_names("liga o exaustório do wc")
    assert out == "liga o exaustor do wc", f"a sala foi duplicada: {out!r}"
    assert out.count("wc") == 1, f"a sala foi duplicada: {out!r}"

    # Already canonical: nothing to do, and saying so is part of the contract.
    assert _canonical_device_names("liga o exaustor do wc") is None


def test_a_device_this_house_does_not_have_is_not_invented(house, monkeypatch):
    monkeypatch.setattr(config, "TUYA_DEVICES", {"Luz do Quarto": {}},
                        raising=False)
    assert _canonical_device_names("liga o exaustor") is None, (
        "a casa passou a concordar sobre um dispositivo que nao tem"
    )


def test_nothing_is_rewritten_when_no_devices_are_registered(monkeypatch):
    monkeypatch.setattr(config, "TUYA_DEVICES", {}, raising=False)
    assert _canonical_device_names("Liga o exaustório.") is None


def test_a_question_with_no_device_word_is_untouched(house):
    assert _canonical_device_names("como está o tempo em Lisboa") is None


def test_the_full_path_from_transcription_to_the_switch(house):
    """The wiring, not the helper.

    Two independent repairs meet here: the alias makes the word legible, and the
    skill folds accents so it can read it. Testing either alone would have
    passed while the command still went nowhere.
    """
    spoken = "Liga o exaustório."
    fixed = _canonical_device_names(spoken) or spoken
    assert tuya.handle(fold(fixed), fixed) == "2 dispositivos ligados."


def test_the_initial_prompt_is_actually_passed_to_the_decoder():
    """The setting existed in prod's .env and reached nothing."""
    import inspect

    src = inspect.getsource(__import__("src.pipeline.stt", fromlist=["x"]))
    assert 'decode_kwargs["initial_prompt"] = initial_prompt' in src, (
        "o initial_prompt deixou de ser passado ao transcribe"
    )
