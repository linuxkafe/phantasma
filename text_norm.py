"""One way to fold Portuguese text, instead of four.

Every skill that matches an owner phrase has to decide what counts as the same
word, and each one decided privately. `skill_chacon` and `skill_tasmota` strip
accents (NFD to ASCII). `skill_tuya` does not, at all. The loader folds both
sides of a trigger comparison but not the phrases inside `handle`. So a device
the owner spells correctly in writing is unreachable by voice, and the
measurable cost was concrete: Whisper transcribes "liga o exaustor" as "Liga o
exaustório" -- the accent is how the word sounds -- `BASE_NOUNS` holds
"exaustor", `"exaustor" in "liga o exaustorio"` is False, the skill returns
None, and the phrase falls through to the model, which searches the web for
"Liga o exaustório" and replies that it does not know how to turn one on.

Note what the accent costs here: fold it and "exaustorio" CONTAINS "exaustor"
as a prefix, so the variant lands on the real noun without a synonym list. The
word the owner said and the word the code holds are the same word, written two
ways.
"""

import unicodedata

__all__ = ["fold", "fold_all", "contains_word", "devices_in",
           "strip_accents"]


def strip_accents(text: str) -> str:
    """`ó` becomes `o`. Everything else is left alone."""
    if not text:
        return ""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(ch) != "Mn"
    )


def fold(text: str) -> str:
    """Lowercase, lose the accents, collapse the whitespace.

    Deliberately NOT fuzzy. Every nearby spelling that has to be understood is
    either an accent, a plural `s`, or a word in `DEVICE_VARIANTS`. Fuzzy
    matching here would blur `luz` into `lousa` and turn "acende a luz" into a
    command for the wrong device -- a wrong switch is not a wrong answer, it is
    the wrong thing done to the house. Determinism is the feature.
    """
    return " ".join(strip_accents(text).split())


def fold_all(items) -> list:
    """`fold` over an iterable of phrases, preserving order."""
    return [fold(x) for x in items]


def contains_word(haystack_folded: str, needle: str) -> bool:
    """Is `needle` in `haystack_folded`, allowing a plural on either side?

    `luz` has to find "luzes" and `luzes` has to find "luz": the device is named
    one way in the config and the other way out of the owner's mouth, and
    guessing the direction is how "liga as luzes" ends up not matching a
    `Luz`.

    Both sides must already be folded; this does not fold for you, because
    folding twice per comparison per device is how the four normalisations
    drifted apart in the first place.
    """
    n = fold(needle)
    if not n:
        return False
    if n in haystack_folded:
        return True
    # Portuguese plurals are not "add an s": luz -> luzes, so the singular is
    # the -es form with two letters off, not the -s form with one.
    candidates = {n + "s"}
    if n.endswith("es"):
        candidates.add(n[:-2])
    elif n.endswith("s"):
        candidates.add(n[:-1])
    return any(c and c in haystack_folded for c in candidates)


def devices_in(prompt: str, table=None) -> set:
    """Canonical device names whose ANY known variant appears in `prompt`.

    This is the inverse of the naive substring test, and it is what makes a
    variant list worth writing. "ventoinha" does not contain "exaustor", so
    `contains_word(prompt, "exaustor")` cannot find it however the accent is
    folded -- the mapping has to go the other way: look for every word the owner
    might use, and return the device it stands for.

    Deterministic and finite. Fuzzy matching would blur `luz` into `lousa` and
    switch the wrong thing, which is not a wrong answer but the wrong action.
    """
    table = DEVICE_VARIANTS if table is None else table
    folded = fold(prompt)
    found = set()
    for device, variants in table.items():
        if any(contains_word(folded, v) for v in variants):
            found.add(device)
    return found


# The owner's word for a device, and the words Whisper hands back. Each entry is
# an assertion that these name the same object, and each is checkable: a phrase
# that names one of these has to reach the device it names.
#
# "fan"/"exhaustor" are here because Whisper transcribes them in English when
# the room is noisy, which is the same class of failure as the accent and costs
# the same thing: the command lands on nothing.
DEVICE_VARIANTS = {
    "exaustor": ("exaustor", "exaustorio", "exaustao", "ventoinha",
                 "ventilador", "fan", "extractor"),
    "desumidificador": ("desumidificador", "desumidificadora", "desumidificacao"),
    "luz": ("luz", "luzes", "lampada", "candeeiro", "abajur", "light"),
    "aspirador": ("aspirador", "aspiradora", "vao", "vacuum"),
}
