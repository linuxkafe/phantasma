"""Deferred intents: "liga a luz do balcao as 8" and "cancela o agendamento".

This sits ABOVE the device skills on purpose, and that ordering is the feature.

"luz do balcao" is claimed by four skills -- `skill_chacon_udp` (60),
`skill_tasmota` (50), `skill_chacon` and `skill_xiaomi` -- and all of them
match it by substring and ignore the rest of the sentence. Before this skill
existed, "liga a luz do balcao as 8" matched `skill_chacon_udp`, switched the
light immediately, and the "as 8" was dropped on the floor: the owner's
sentence contained a time and the house acted as though it did not.

So this skill has to be matched BEFORE any of them, and it is written as a
class-based Skill rather than a legacy module, because `LegacySkillAdapter`
hardcodes PRIORITY=0 for everything it wraps (see the note in
`skill_zigbee.py`). A legacy version of this skill would load at priority 0,
tie with the device skills, and lose on sort order -- the exact silent
no-op this is meant to fix.

Priority 100 clears every current device skill. If one ever exceeds 100, this
breaks quietly and the symptom is the original bug returning; the regression
test in tests/test_skill_schedule.py asserts the ordering so that cannot land
unnoticed.
"""

import logging
import re
from datetime import datetime

from skills.base import Skill, TriggerType
from src.scheduler import parse_schedule
from src.scheduler.runner import get_shared_store

logger = logging.getLogger(__name__)

# Deliberately word-anchored. A bare "cancela" as a substring would fire on
# "a TV cancela o som" and cancel somebody's scheduled task.
_CANCEL = (
    r"\b(?:cancela|cancele|cancelar|cancela(r)?|anula|anule|desmarca|"
    r"desmarque|tira)\b"
)
_QUERY = r"\b(?:o que|que|quais)\b.{0,24}\b(?:agendad|programad)\w*\b"

_TRIGGERS = ["agendamento", "agendada", "agendado", "agendar", "agenda",
             "cancela", "cancele", "cancelar", "anula", "anule", "desmarca",
             "desmarque", "tira"]


def _fold(text: str) -> str:
    from text_norm import fold
    return fold(text)


def _cancel_reference(text: str):
    """What the owner meant to cancel, as a matcher over the pending tasks.

    Returns one of:
      ("all", None)      -- "cancela tudo", or a bare "cancela"
      ("hour", 8)        -- "cancela o agendamento das 8"
      ("words", {...})   -- "cancela a luz do balcao"

    The three are separated because they mean different things and conflating
    them is how "cancela a luz do balcao" ends up clearing the whole list.
    """
    folded = _fold(text)

    if re.search(r"\b(tudo|todos|todas|os agendamentos|as tarefas)\b", folded):
        return ("all", None)

    # A time phrase anywhere in the sentence wins over the leftover words,
    # because "cancela o agendamento das 8" parses to an action of
    # "cancela o agendamento", which matches no task and would otherwise look
    # like a reference to something that does not exist.
    schedule = parse_schedule(text)
    if schedule is not None:
        return ("hour", schedule.fire_at.hour)

    # Strip the verb and whatever politeness came with it, then keep the words
    # that name the task.
    reference = re.sub(_CANCEL, " ", folded)
    reference = re.sub(
        r"\b(o|a|os|as|agendamento|tarefa|por favor|please|do|da|de|que|"
        r"esta|estao|nova|nova)\b", " ", reference,
    )
    words = {w for w in re.findall(r"[a-z0-9]+", reference) if len(w) > 2}
    if not words:
        # "cancela" on its own, with nothing to disambiguate. The safe reading
        # is not "clear everything" -- that is destructive and reversible only
        # by re-typing the tasks -- so it asks instead.
        return ("ask", None)
    return ("words", words)


def _matches(task_action: str, words) -> bool:
    """Is every content word of the reference present in this task's action?"""
    folded_action = _fold(task_action)
    return all(word in folded_action for word in words)


def _when_label(fire_at: datetime) -> str:
    """Spoken form of an instant, with the day named when it is not today.

    Without the day name, a task rolled over to tomorrow is announced as "as 8",
    which sounds identical to the task that was going to fire in twenty
    minutes -- and the owner has just been told the wrong thing twice.
    """
    today = datetime.now().date()
    if fire_at.date() == today:
        return f"as {fire_at:%H:%M}"
    if fire_at.date() == today.fromordinal(today.toordinal() + 1):
        return f"amanha as {fire_at:%H:%M}"
    return f"{fire_at:%d/%m} as {fire_at:%H:%M}"


# The owner says "agenda as 8 a luz do balcao" or "agenda a luz do balcao as 8".
# In the first, the verb is not part of the task and would be replayed to the
# device skills at fire time as "agenda desligar a luz da sala" -- a sentence no
# skill matches, so the light would never come on.
#
# ONLY scheduling verbs go in this list. "liga", "acende" and "desliga" are the
# command itself: an earlier version listed them here and turned "liga a luz do
# balcao" into "a luz do balcao", which no device skill matches -- the task was
# accepted, confirmed aloud, and could never succeed.
_LEADING_VERB = re.compile(
    r"^\s*(?:agenda|agendar|agendado|marca|marcar|programa|programar|"
    r"reserva|reservar|poe|aciona|acionar)\b\s*",
    re.IGNORECASE,
)


def _strip_leading_verb(action: str) -> str:
    """Drop a leading "agenda"/"marca" so the action is the bare command."""
    return re.sub(r"\s+", " ", _LEADING_VERB.sub("", action)).strip()


class ScheduleSkill(Skill):
    """Create, list and cancel deferred tasks."""

    NAME = "skill_schedule"
    TRIGGERS = _TRIGGERS
    TRIGGER_TYPE = TriggerType.CONTAINS
    # Above skill_chacon_udp (60). See the module docstring for why this must
    # stay above the device skills.
    PRIORITY = 100
    def matches(self, text: str) -> bool:
        """Three ways in, and no default.

        Overridden rather than left to the base class because the base
        substring test cannot express "this sentence has a time in it", which
        is most of what this skill is for.
        """
        folded = _fold(text)
        if re.search(_CANCEL, folded):
            return True
        if re.search(_QUERY, folded):
            return True
        # Only intercept a schedule-shaped sentence. A plain "liga a luz do
        # balcao" must fall through to skill_chacon_udp exactly as it does
        # today, and returning True for everything would break the house.
        return parse_schedule(text) is not None

    def handle(self, text: str) -> str:
        folded = _fold(text)
        if re.search(_CANCEL, folded):
            return self._cancel(text)
        if re.search(_QUERY, folded):
            return self._list()
        return self._schedule(text)

    # --- create ----------------------------------------------------------

    def _schedule(self, text: str) -> str:
        schedule = parse_schedule(text)
        if schedule is None:  # pragma: no cover - matches() already proved it
            return ""

        store = get_shared_store()
        if store is None:
            # Say the true thing. Returning a confirmation here would be a lie
            # the owner acts on: they would set their alarm around a task that
            # does not exist.
            logger.error("skill_schedule sem store registado; nada foi agendado")
            return ("Não consegui agendar isto: o agendador não está a correr. "
                    "Reinicia o serviço.")

        action = _strip_leading_verb(schedule.action)
        if not action:
            # "agenda as 8" names a time and nothing else. There is no task to
            # remember, and confirming one anyway would put an entry on the
            # page that can never succeed.
            return ("Diz o que queres que faça, por exemplo "
                    "\"agenda desligar a luz da sala às 8\".")

        store.add(action, schedule.fire_at, source="voice")

        when = _when_label(schedule.fire_at)
        if schedule.rollover == "rolled-over":
            return (f"As 8 da manhã e as 8 da noite de hoje já passaram, "
                    f"por isso agendei para {when}: {action}.")
        return f"Agendado para {when}: {action}."

    # --- list ------------------------------------------------------------

    def _list(self) -> str:
        store = get_shared_store()
        if store is None:
            return "O agendador não está a correr, não tenho nada para mostrar."

        pending = store.pending()
        if not pending:
            return "Não tens nada agendado."

        lines = [f"Tens {len(pending)} tarefa(s) agendada(s):"]
        for task in pending:
            # Each item ends in a full stop. TTS turns "as 08:00: liga a luz do
            # balcao as 20:00: desliga a luz da sala" into one run-on breath,
            # and the owner cannot tell where one task stops and the next
            # begins.
            lines.append(f"{_when_label(task.when())}: {task.action}.")
        lines.append("Diz 'cancela tudo' para as remover.")
        return " ".join(lines)

    # --- cancel ----------------------------------------------------------
    def _cancel(self, text: str) -> str:
        """Remove what the owner named, and say plainly whether anything went.

        Every branch distinguishes "removed N" from "there was nothing to
        remove". The owner is standing in a room deciding whether to go to bed,
        and a reply that does not say which happened leaves them guessing.
        """
        store = get_shared_store()
        if store is None:
            return "O agendador não está a correr, não há nada para cancelar."

        kind, reference = _cancel_reference(text)
        pending = store.pending()

        if kind == "all":
            removed = store.cancel_where(lambda _t: True)
            if not removed:
                return "Não tens nada agendado."
            return f"Cancei {removed} tarefa(s) agendada(s)."

        if kind == "ask":
            # A bare "cancela" with nothing to narrow it. Clearing everything
            # on an ambiguous instruction is destructive and the tasks are only
            # recoverable by being typed again, so this asks.
            if not pending:
                return "Não tens nada agendado."
            lines = ["Qual destes queres cancelar?"]
            for task in pending:
                lines.append(f"{_when_label(task.when())}: {task.action}")
            return " ".join(lines)

        if kind == "hour":
            removed = store.cancel_where(lambda t: t.when().hour == reference)
            if not removed:
                return f"Não há nada agendado para as {reference:02d}h."
            return f"Cancei {removed} tarefa(s) das {reference:02d}h."

        if not isinstance(reference, set):  # pragma: no cover - defensive
            return "Não percebi qual tarefa queres cancelar."

        removed = store.cancel_where(lambda t: _matches(t.action, reference))
        if not removed:
            wanted = " ".join(sorted(reference))
            return f"Não encontrei nenhuma tarefa agendada com '{wanted}'."
        return f"Cancei {removed} tarefa(s)."
