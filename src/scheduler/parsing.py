"""Read the WHEN out of a sentence, and hand back only the WHAT.

The owner says "liga a luz do balcao as 8" and means two different things at
once: *what* to do ("liga a luz do balcao") and *when* ("as 8"). Today the
second half is silently dropped -- `skill_chacon_udp` matches the nickname
"luz do balcao", ignores the rest, and switches the light immediately. This
module is the part that was missing.

Folding happens through `text_norm.fold`, because the input arrives from the
STT which does not accent ("as 8") and from a typed chat which does ("as 8"),
and both have to mean the same thing. See `text_norm.fold` for why the folding
here is deliberately not fuzzy: a fuzzy match that blurs "as 8" into something
else schedules the wrong thing at the wrong hour.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

__all__ = ["Schedule", "parse_schedule", "resolve_moment", "MORNING_HOUR",
           "EVENING_HOUR"]

# The two fixed slots the owner named, not constants invented by me.
MORNING_HOUR = 8
EVENING_HOUR = 20

# Words the STT produces for numbers when a digit was expected. Only the ones
# that name a plausible hour; "quarenta e cinco" as an hour does not exist, and
# a partial table that guessed would schedule the wrong hour.
_WORD_HOURS = {
    "uma": 1, "duas": 2, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11,
    "doze": 12, "meia": None,  # "meia" carries no hour of its own
    "vinte": 20, "vinte e uma": 21, "vinte e dois": 22, "vinte e tres": 23,
}

# "da manha" / "da tarde" / "da noite" -> how a 1..12 becomes 0..23.
# Morning takes the bare number (8 -> 08:00). Afternoon and evening add twelve,
# but only while the number is still in the afternoon half; "as 8 da noite" is
# 20:00 and "as 20 da noite" would be 32:00, so anything already >= 13 is left
# alone instead of being pushed past midnight.
_MERIDIEM_OFFSETS = {"da manha": 0, "de manha": 0, "da tarde": 12,
                     "da noite": 12}


@dataclass(frozen=True)
class Schedule:
    """A sentence split into the thing to do and the instant to do it.

    Attributes:
        action: The sentence with the time phrase removed, which is what gets
            replayed through the skill pipeline when the moment arrives.
        fire_at: The resolved local datetime. Never in the past.
        phrase: The time phrase exactly as it was matched, for confirmation.
        rollover: How the instant was arrived at. Carried for the
            confirmation sentence, which is the one place the owner finds out
            that "as 8" became tomorrow's 8 and not tonight's.
    """

    action: str
    fire_at: datetime
    phrase: str
    rollover: str = "explicit"


def _fold_with_map(text: str):
    """Fold to lowercase/unaccented, remembering where each character came from.

    `text_norm.fold` collapses whitespace, which is exactly what makes its
    output safe to match on but unsafe to index back into the original. Doing
    the fold per character keeps the two aligned, so the time phrase can be
    excised from the *original* casing and accents -- "Liga a luz do Balcão às
    8" has to leave "Liga a luz do Balcão", not a lowercased, accent-stripped
    sentence that no longer matches the device nickname.

    Done per character rather than on the whole string so the rare case where
    one character lowers to several (German sharp s) still maps correctly.
    """
    out = []
    index_map = []
    for index, char in enumerate(text):
        decomposed = unicodedata.normalize("NFD", char.lower())
        for piece in decomposed:
            if unicodedata.category(piece) == "Mn":
                continue
            out.append(piece)
            index_map.append(index)
    return "".join(out), index_map


_WORD_HOUR_ALT = "|".join(sorted(_WORD_HOURS, key=len, reverse=True))

# Built by concatenation, not str.format: the pattern is full of `{1,2}` and
# `{2}` quantifier braces, which format() would read as fields.
#
# Every alternative carries its own named group rather than one shared "minutes"
# group, because the alternatives are tried in order and a single group would be
# reset by whichever branch did not participate. That was the bug in the first
# version: "as 20h30" matched the bare hour and left a literal "h30" glued to
# the action, and the light would have been scheduled for "liga a luz do balcao
# h30" -- a command no skill could resolve, twenty minutes late.
_TIME_CORE = (
    r"(?P<hour>\d{1,2}|" + _WORD_HOUR_ALT + r")"
    r"(?:[:h]\s*(?P<minute_a>\d{2})|(?P<minute_b>\d{2})\s*h\b)?"  # 20:30 / 20h30
    r"(?P<hsuffix>\s*h\b)?"                                        # 8h
    r"(?:\s*horas?\b)?"                                            # 8 horas
    r"(?:\s*(?P<meridiem>da\s+manha|de\s+manha|da\s+tarde|da\s+noite)\b)?"
)

# The Portuguese preposition is required. Without it "liga a luz as 8" is a
# different sentence and "as" is also the plural article, so dropping the
# requirement would let "desliga as luzes" be read as a time.
_PATTERN = re.compile(
    r"(?P<tomorrow>\bamanha\b\s+)?"
    # The optional "d" is the contraction: "cancela o agendamento DAS 20" is
    # how the owner actually says it, and without it that sentence parsed no
    # time at all -- so cancelling by hour silently became a search for a task
    # containing the word "das", which matched none, and the reply said so
    # while the task stayed pending.
    r"\b(?:d?(?:as|a))\s+"
    r"(?P<time>" + _TIME_CORE + r")",
    re.IGNORECASE,
)


def _hours_in_range(hour: Optional[int]) -> bool:
    return hour is not None and 0 <= hour <= 23


def resolve_moment(hour: int, minute: int = 0, meridiem: Optional[str] = None,
                   explicit_24h: bool = False, tomorrow: bool = False,
                   now: Optional[datetime] = None) -> tuple[datetime, str]:
    """Turn "as 8" into a concrete instant, never in the past.

    The three-branch rule is the owner's, decided 2026-10-13, and the middle
    branch is the part that is easy to get wrong:

    1. now < 08:00            -> today 08:00
    2. now >= 08:00           -> today 20:00
    3. now >= 20:00           -> TOMORROW 08:00

    Branch 3 exists because branch 2 produces a time that has itself already
    gone by. Saying "as 8" at 21:00 and scheduling 20:00 would create a task
    that is late on arrival, and the light would come on at some arbitrary
    later point when the runner next looked. Rolling to tomorrow's 08:00 is
    the only reading of "as 8" that is still in the future.

    Args:
        hour: The hour named, 0-23 as parsed.
        minute: Minutes, 0 by default.
        meridiem: "da manha" / "da tarde" / "da noite", already folded.
        explicit_24h: True when the owner wrote the hour unambiguously
            (a "h" suffix, minutes, or a number above 12). Those skip the
            8/20 rule entirely, because the rule exists to guess an intent the
            owner did not spell out.
        tomorrow: True when the sentence said "amanha".
        now: Injectable clock, so the tests do not depend on what time it is.

    Returns:
        (fire_at, rollover) where rollover is "as-stated", "tomorrow",
        "morning", "evening" or "rolled-over" and names the branch taken.

    Raises:
        ValueError: If the hour is not a real hour.
    """
    if not _hours_in_range(hour):
        raise ValueError(f"hora invalida: {hour!r}")
    now = now or datetime.now()
    midnight_today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # An explicit meridiem pins the hour, so "as 8 da manha" is 08:00 and does
    # not fall through to the evening branch when asked for at 09:00.
    if meridiem:
        offset = _MERIDIEM_OFFSETS.get(meridiem)
        if offset is None:
            raise ValueError(f"meridiem desconhecida: {meridiem!r}")
        if offset and hour < 12:
            hour += 12
        target = midnight_today + timedelta(days=1 if tomorrow else 0,
                                            hours=hour, minutes=minute)
        if target <= now:
            target += timedelta(days=1)
        return target, "tomorrow" if tomorrow else "as-stated"

    # "amanha as 8" is 08:00 tomorrow. The 8/20 rule has no work to do here:
    # the owner already said which day they meant.
    if tomorrow:
        return (midnight_today + timedelta(days=1, hours=hour, minutes=minute),
                "tomorrow")

    if explicit_24h or hour > 12:
        target = midnight_today + timedelta(hours=hour, minutes=minute)
        if target <= now:
            target += timedelta(days=1)
        return target, "as-stated"

    # Branch 1: the morning slot is still ahead today.
    morning = midnight_today + timedelta(hours=MORNING_HOUR, minutes=minute)
    if morning > now:
        return morning, "morning"

    # Branch 2: the evening slot, which is what "as 8" becomes once 08:00 has
    # passed. Only if the evening slot itself is still ahead.
    evening = midnight_today + timedelta(hours=EVENING_HOUR, minutes=minute)
    if evening > now:
        return evening, "evening"

    # Branch 3: both slots are gone. Tomorrow's morning slot.
    return morning + timedelta(days=1), "rolled-over"


def parse_schedule(text: str, now: Optional[datetime] = None) -> Optional[Schedule]:
    """Split "liga a luz do balcao as 8" into action and instant.

    Returns None when there is no time phrase, so the caller can let the normal
    skill path handle the sentence unchanged. That matters: this runs on every
    utterance that reaches it, and a plain "liga a luz do balcao" must reach
    `skill_chacon_udp` exactly as it does today.

    Args:
        text: The owner's sentence, accented or not.
        now: Injectable clock.

    Returns:
        A Schedule, or None.
    """
    if not text:
        return None

    folded, index_map = _fold_with_map(text)
    match = _PATTERN.search(folded)
    if not match:
        return None

    raw_hour = match.group("hour")
    minute_raw = match.group("minute_a") or match.group("minute_b")
    minute = int(minute_raw or 0)
    # A written "h" is the owner being unambiguous. Tested against its own
    # capture group rather than with `"h" in phrase`, because that substring
    # test is true for "8 horas" -- and reading "as 8 horas" as a stated 24h
    # time sent it to tomorrow even when the morning slot was hours away.
    wrote_h = bool(match.group("hsuffix") or minute_raw)

    if raw_hour.isdigit():
        hour = int(raw_hour)
        explicit_24h = wrote_h or hour > 12
    else:
        word_hour = _WORD_HOURS.get(re.sub(r"\s+", " ", raw_hour.strip()))
        if word_hour is None:
            return None
        hour = word_hour
        # "as vinte e uma" is unambiguous; "as oito" is not, so only a number
        # written as digits counts as explicit here.
        explicit_24h = wrote_h or hour > 12

    meridiem = re.sub(r"\s+", " ", (match.group("meridiem") or "")).strip() or None
    if meridiem:
        meridiem = meridiem if meridiem in _MERIDIEM_OFFSETS else None

    try:
        fire_at, rollover = resolve_moment(
            hour=hour,
            minute=minute,
            meridiem=meridiem,
            explicit_24h=bool(explicit_24h),
            tomorrow=bool(match.group("tomorrow")),
            now=now,
        )
    except ValueError:
        # A number that cannot be an hour is not a schedule; it is something
        # else in the sentence that happened to follow an "a".
        return None

    start = index_map[match.start()]
    end = index_map[match.end() - 1] + 1
    action = (text[:start] + text[end:]).strip()
    action = re.sub(r"\s+", " ", action).strip(" ,;:-")
    if not action:
        # "as 8" on its own is not a schedule; it is a fragment.
        return None

    return Schedule(action=action, fire_at=fire_at,
                    phrase=match.group("time").strip(), rollover=rollover)
