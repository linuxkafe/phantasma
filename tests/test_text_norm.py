"""The accent is how the word sounds.

Measured on 2026-10-04: the owner said "liga o exautor", Whisper transcribed
"Liga o exaustório", `skill_tuya` looked for "exaustor" in it, found nothing
because of the accent, returned None, and the phrase went to the model, which
searched the web for "Liga o exaustório" and answered that it did not know how
to turn one on. There were two devices configured the whole time --
`Exaustor do WC` and `Exaustor da Sala`.
"""

from text_norm import (
    DEVICE_VARIANTS,
    contains_word,
    devices_in,
    fold,
    strip_accents,
)


def test_the_accent_is_what_costs_the_command():
    assert "exaustor" not in "liga o exaustório."
    assert "exaustor" in fold("liga o exaustório.")
    # Which is the whole repair: fold, and the variant is a prefix of the noun.
    assert fold("exaustório").startswith("exaustor")


def test_folding_is_idempotent():
    once = fold("Liga o EXAUSTÓRIO.  do   WC")
    assert once == fold(once)
    assert once == "liga o exaustorio. do wc"


def test_strip_accents_leaves_the_rest_alone():
    assert strip_accents("Coração,ÃO!") == "coracao,ao!"
    assert strip_accents("") == ""
    assert strip_accents(None) == ""


def test_a_plural_is_not_a_different_device():
    assert contains_word("liga as luzes da sala", "luz")
    assert contains_word("liga a luz", "luzes")


def test_every_variant_reaches_its_device():
    """The mapping runs variant -> device, not device -> substring.

    An earlier version of this test asserted the naive thing, that every variant
    CONTAINS the device noun. It does not, and it cannot: "ventoinha" does not
    contain "exaustor", so no amount of accent folding reaches it. Which is why
    the lookup is a table walk and not a substring test -- and the test now
    asserts the property that actually matters, which is that the owner gets
    the device.
    """
    for device, variants in DEVICE_VARIANTS.items():
        for v in variants:
            assert device in devices_in(f"liga o {v}"), (
                f"a variante {v!r} não chega a {device!r}: o dono disse uma "
                f"palavra que a casa não conhece"
            )


def test_the_two_ways_the_owner_says_it_both_land():
    for phrase in ("liga o exaustório", "liga a ventoinha do wc",
                   "acende o fan da sala", "liga o extractor"):
        assert devices_in(phrase) == {"exaustor"}, phrase


def test_a_noun_absent_from_the_phrase_is_not_invented():
    assert devices_in("qual e a temperatura do quarto") == set()
    assert devices_in("acende a luz do quarto") == {"luz"}


def test_folding_is_deterministic_not_fuzzy():
    """A wrong switch is the wrong thing done to the house.

    Determinism is the feature: `luz` must never blur into `lousa`, because
    "acende a luz" has to reach the lamp and nothing else.
    """
    assert not contains_word("acende a lousa do quarto", "luz")
    assert not contains_word("acende o aspirador", "luz")
