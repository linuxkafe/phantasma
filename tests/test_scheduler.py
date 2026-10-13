"""T070: turning a sentence into a future action, and proving it fires.

The rule under test is the owner's, decided 2026-10-13: "as 8" means today at
08:00 if that is still ahead, today at 20:00 if it is not, and tomorrow at 08:00
once both have gone. Branch 3 is the one with no specification behind it, and it
is the one that must never schedule into the past -- so it is tested at three
different clock times rather than once.
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.skill_schedule import ScheduleSkill  # noqa: E402
from src.scheduler import (  # noqa: E402
    SchedulerRunner,
    ScheduleStore,
    parse_schedule,
    resolve_moment,
)
from src.scheduler import runner as runner_module  # noqa: E402


def at(hour, minute=0):
    """A fixed clock. Never `datetime.now()` in a test of this rule."""
    return datetime(2026, 10, 13, hour, minute)


# --- the 8 / 20 / tomorrow-8 rule ------------------------------------------

class TestTheOwnersRule:
    def test_before_eight_is_this_morning(self):
        moment, rollover = resolve_moment(8, now=at(7, 50))
        assert moment == at(8, 0)
        assert rollover == "morning"

    def test_after_eight_becomes_this_evening(self):
        moment, rollover = resolve_moment(8, now=at(9, 0))
        assert moment == at(20, 0)
        assert rollover == "evening"

    def test_after_eight_pm_rolls_to_tomorrow_morning(self):
        """The branch the specification did not state.

        Branch 2 hands back 20:00 even at 21:00, which is already gone. A task
        set in the past is not "late", it is claimed-and-removed on the next
        poll and fires at an arbitrary later moment. So it rolls to tomorrow.
        """
        moment, rollover = resolve_moment(8, now=at(21, 0))
        assert moment == at(8, 0) + timedelta(days=1)
        assert rollover == "rolled-over"

    @pytest.mark.parametrize("hour", range(0, 24))
    def test_never_ever_schedules_into_the_past(self, hour):
        """The property, checked exhaustively rather than on three examples."""
        now = at(23, 59)
        moment, _ = resolve_moment(hour, now=now)
        assert moment > now, f"as {hour} at 23:59 resolved to {moment}"

    def test_exactly_eight_goes_to_the_evening(self):
        """Boundary: at 08:00 the morning slot is not "still ahead"."""
        moment, _ = resolve_moment(8, now=at(8, 0))
        assert moment == at(20, 0)

    def test_exactly_eight_pm_rolls_over(self):
        """Boundary: at 20:00 the evening slot is gone, not pending."""
        moment, rollover = resolve_moment(8, now=at(20, 0))
        assert moment == at(8, 0) + timedelta(days=1)
        assert rollover == "rolled-over"


class TestExplicitTimesSkipTheGuess:
    """The 8/20 rule exists to guess. A stated time must not be guessed at."""

    def test_twenty_stays_twenty(self):
        moment, rollover = resolve_moment(20, now=at(9, 0))
        assert moment == at(20, 0)
        assert rollover == "as-stated"

    def test_twenty_in_the_past_rolls_one_day_not_to_eight(self):
        moment, _ = resolve_moment(20, now=at(21, 0))
        assert moment == at(20, 0) + timedelta(days=1)

    def test_morning_meridiem_is_not_the_evening_slot(self):
        moment, _ = resolve_moment(8, meridiem="da manha", now=at(9, 0))
        assert moment == at(8, 0) + timedelta(days=1), (
            "'as 8 da manha' at 09:00 must become TOMORROW 08:00, not tonight"
        )

    def test_evening_meridiem_of_a_small_hour(self):
        moment, _ = resolve_moment(8, meridiem="da noite", now=at(9, 0))
        assert moment == at(20, 0)

    def test_tomorrow_is_tomorrow(self):
        moment, rollover = resolve_moment(8, tomorrow=True, now=at(21, 0))
        assert moment == at(8, 0) + timedelta(days=1)
        assert rollover == "tomorrow"

    def test_an_impossible_hour_is_refused(self):
        with pytest.raises(ValueError):
            resolve_moment(99, now=at(9))


# --- splitting the sentence --------------------------------------------------

class TestSplitting:
    def test_the_owners_sentence(self):
        result = parse_schedule("liga a luz do balcao as 8", now=at(9, 0))
        assert result.action == "liga a luz do balcao"
        assert result.fire_at == at(20, 0)

    def test_accented_and_unaccented_mean_the_same(self):
        """The INPUT may be spelled either way; the action keeps its own
        spelling, because the device nickname is registered accented and
        folding the action would make it unmatchable."""
        accented = parse_schedule("liga a luz do balcão às 8", now=at(9, 0))
        plain = parse_schedule("liga a luz do balcao as 8", now=at(9, 0))
        assert accented.action == "liga a luz do balcão"
        assert plain.action == "liga a luz do balcao"
        assert accented.fire_at == plain.fire_at == at(20, 0)

    def test_original_casing_and_accents_survive(self):
        """The action must still match the device nickname the skills expect."""
        result = parse_schedule("Liga a luz do Balcão às 8", now=at(9, 0))
        assert result.action == "Liga a luz do Balcão"

    def test_minutes_are_removed_from_the_action(self):
        """Regression: "as 20h30" once matched only the hour and left "h30"
        glued to the command, so the task could never resolve to a device."""
        result = parse_schedule("liga a luz do balcao as 20h30", now=at(9, 0))
        assert result.action == "liga a luz do balcao"
        assert result.fire_at == at(20, 30)

    def test_horas_is_not_a_24h_marker(self):
        """Regression: `"h" in "8 horas"` is true, and reading it as an
        explicit 24h time sent a morning task to tomorrow."""
        result = parse_schedule("liga a luz do balcao as 8 horas", now=at(9, 0))
        assert result.fire_at == at(20, 0)

    def test_colon_form(self):
        result = parse_schedule("liga a luz do balcao as 8:30", now=at(7, 0))
        assert result.action == "liga a luz do balcao"
        assert result.fire_at == at(8, 30)

    def test_words_for_the_hour(self):
        result = parse_schedule("liga a luz do balcao as oito", now=at(7, 0))
        assert result.action == "liga a luz do balcao"
        assert result.fire_at == at(8, 0)

    @pytest.mark.parametrize("sentence", [
        "liga a luz do balcao",
        "desliga as luzes",
        "que ta a neblina",
        "as 8",            # a time and nothing to do with it
    ])
    def test_sentences_without_a_time_are_not_schedules(self, sentence):
        assert parse_schedule(sentence, now=at(9, 0)) is None


# --- the store ---------------------------------------------------------------

class TestStore:
    def test_survives_a_restart(self, tmp_path):
        path = str(tmp_path / "schedules.json")
        store = ScheduleStore(path)
        store.add("liga a luz do balcao", at(20), source="voice")

        reopened = ScheduleStore(path)
        assert [t.action for t in reopened.pending()] == ["liga a luz do balcao"]

    def test_due_tasks_are_handed_out_exactly_once(self, tmp_path):
        """Claim-and-remove, so a restart cannot fire the same task twice."""
        store = ScheduleStore(str(tmp_path / "s.json"))
        store.add("liga a luz", at(20))
        store.add("desliga a luz", at(20, 30))

        first = store.claim_due(at(20, 15))
        assert len(first) == 1 and first[0].action == "liga a luz"
        assert store.claim_due(at(20, 15)) == []

        second = store.claim_due(at(21, 0))
        assert len(second) == 1 and second[0].action == "desliga a luz"

    def test_future_tasks_are_never_claimed(self, tmp_path):
        store = ScheduleStore(str(tmp_path / "s.json"))
        store.add("liga a luz", at(20))
        # 19:00 is before the 20:00 task. A claim at 20:15 would be correct and
        # would make this test assert the wrong thing.
        assert store.claim_due(at(19, 0)) == []
        assert len(store) == 1

    def test_a_corrupt_file_is_treated_as_empty_not_fatal(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("{not json", encoding="utf-8")
        store = ScheduleStore(str(path))
        assert store.pending() == []

    def test_cancel_by_id(self, tmp_path):
        store = ScheduleStore(str(tmp_path / "s.json"))
        task = store.add("liga a luz", at(20))
        assert store.cancel(task.id) is True
        assert store.cancel(task.id) is False
        assert store.pending() == []

    def test_no_temp_files_are_left_behind(self, tmp_path):
        store = ScheduleStore(str(tmp_path / "s.json"))
        store.add("liga a luz", at(20))
        leftovers = [n for n in os.listdir(tmp_path) if n.startswith(".schedules")]
        assert leftovers == []


# --- firing ------------------------------------------------------------------

class TestFiring:
    def _runner(self, tmp_path, **kwargs):
        store = ScheduleStore(str(tmp_path / "s.json"))
        spoken = []
        runner = SchedulerRunner(
            store,
            executor=kwargs.get("executor", lambda a: "luz do balcao ligada"),
            speaker=spoken.append,
        )
        return store, runner, spoken

    def test_fires_and_announces(self, tmp_path):
        store, runner, spoken = self._runner(tmp_path)
        store.add("liga a luz do balcao", at(20))

        fired = runner.tick(at(20, 0))
        assert [t.action for t in fired] == ["liga a luz do balcao"]
        assert spoken and "luz do balcao ligada" in spoken[0]
        assert store.pending() == []

    def test_a_task_nothing_can_run_is_reported_not_swallowed(self, tmp_path):
        """Silence reads as success. If no skill claimed the sentence, the
        owner must be told, because they are waiting for a light."""
        store, runner, spoken = self._runner(tmp_path, executor=lambda a: None)
        store.add("acende a coisa", at(20))

        runner.tick(at(20, 0))
        assert spoken and "falhou" in spoken[0].lower()

    def test_one_failing_task_does_not_stop_the_others(self, tmp_path):
        def executor(action):
            if action == "explode":
                raise RuntimeError("rede em baixo")
            return "ok"

        store, runner, spoken = self._runner(tmp_path, executor=executor)
        store.add("explode", at(20, 0))
        store.add("liga a luz", at(20, 1))

        fired = runner.tick(at(20, 5))
        assert len(fired) == 2
        assert any("rede em baixo" in line for line in spoken)
        assert any("ok" in line for line in spoken)

    def test_the_store_is_read_only_while_idle(self, tmp_path):
        store, runner, _ = self._runner(tmp_path)
        store.add("liga a luz", at(20))
        runner.tick(at(19, 0))
        assert len(store) == 1


# --- the skill ---------------------------------------------------------------

class TestSkill:
    @pytest.fixture(autouse=True)
    def _wire(self, tmp_path):
        store = ScheduleStore(str(tmp_path / "s.json"))
        self.store = store
        self.runner = SchedulerRunner(
            store, executor=lambda a: "luz do balcao ligada",
            speaker=lambda line: None)
        runner_module.register_shared_runner(self.runner)
        self.skill = ScheduleSkill()
        yield
        runner_module.register_shared_runner(None)

    def test_it_outranks_every_device_skill(self):
        """THE regression.

        "luz do balcao" is claimed by skill_chacon_udp (60), skill_tasmota (50),
        skill_chacon and skill_xiaomi. Before this skill existed, the sentence
        reached one of them, the light came on straight away, and "as 8" was
        dropped. If this priority ever drops, the bug comes back silently.
        """
        from skills.loader import SkillLoader

        loader = SkillLoader(skills_dir="skills")
        loader.load_all()
        order = loader.resolve_matching_skills("liga a luz do balcao as 8")

        assert order, "nothing matched at all"
        assert order[0] == "skill_schedule", (
            f"skill_schedule must be matched first, got {order[:4]}")

    def test_a_plain_command_still_reaches_the_device_skill(self):
        """The other half: the interception must not be so broad that an
        ordinary "liga a luz do balcao" stops working."""
        assert not self.skill.matches("liga a luz do balcao")

    def test_scheduling_confirms_the_time(self):
        reply = self.skill.handle("liga a luz do balcao as 20")
        assert "20:00" in reply
        assert "liga a luz do balcao" in reply
        assert len(self.store) == 1

    def test_rollover_is_explained_out_loud(self):
        """The one case where the answer differs from what was said. Silence
        here is how the owner ends up believing it fires tonight."""
        task_fires = at(8) + timedelta(days=1)
        import unittest.mock as mock
        with mock.patch.object(
                __import__("src.scheduler.parsing", fromlist=["datetime"]),
                "datetime") as fake:
            fake.now.return_value = at(21)
            fake.fromisoformat = datetime.fromisoformat
            reply = self.skill.handle("liga a luz do balcao as 8")
        assert "amanh" in reply.lower()
        assert task_fires > at(21)

    def test_listing_shows_the_actions(self):
        self.skill.handle("liga a luz do balcao as 20")
        reply = self.skill.handle("o que esta agendado")
        assert "luz do balcao" in reply

    def test_listing_when_empty(self):
        assert "nada" in self.skill.handle("o que esta agendado").lower()

    def test_cancel_by_hour(self):
        """Regression: "cancela o agendamento DAS 20" is the contraction. The
        parser had no word boundary before "as" inside "das", found no time,
        fell through to matching the word "das" against the pending actions,
        and replied that nothing matched while the task stayed pending."""
        self.skill.handle("liga a luz do balcao as 20")
        self.skill.handle("desliga a luz da sala as 8")
        reply = self.skill.handle("cancela o agendamento das 20")
        assert "1" in reply
        assert [t.action for t in self.store.pending()] == ["desliga a luz da sala"]

    def test_cancel_by_words(self):
        self.skill.handle("liga a luz do balcao as 20")
        self.skill.handle("desliga a luz da sala as 8")
        self.skill.handle("cancela a luz do balcao")
        assert [t.action for t in self.store.pending()] == ["desliga a luz da sala"]

    def test_cancel_all(self):
        self.skill.handle("liga a luz do balcao as 20")
        self.skill.handle("desliga a luz da sala as 8")
        self.skill.handle("cancela tudo")
        assert self.store.pending() == []

    def test_a_bare_cancel_asks_instead_of_destroying(self):
        """Cancelling everything on an ambiguous word is unrecoverable -- the
        tasks only come back by being typed again."""
        self.skill.handle("liga a luz do balcao as 20")
        self.skill.handle("desliga a luz da sala as 8")
        reply = self.skill.handle("cancela")
        assert "qual" in reply.lower()
        assert len(self.store) == 2

    def test_scheduling_a_verb_alone_is_refused(self):
        reply = self.skill.handle("agenda as 20")
        assert self.store.pending() == []
        assert "diz o que" in reply.lower()

    def test_it_says_so_when_the_runner_is_absent(self):
        """Never confirm a task that was not stored."""
        runner_module.register_shared_runner(None)
        reply = self.skill.handle("liga a luz do balcao as 20")
        assert "agendador" in reply.lower()
