"""Night mode: 23:00 to 07:00, per day, configurable from the admin.

The overnight case is the one that matters and the one that is easy to get
wrong: `start <= hour < end` is false for 00:00 through 06:59 when start=23
and end=7, which is exactly the half of the night the owner asked for. The
tests below pin each boundary of the window, per weekday, and they include
the hour where a naive comparison silently fails.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from src.pipeline.quiet import (  # noqa: E402
    DEFAULT_END,
    DEFAULT_START,
    QuietSchedule,
    QuietWindow,
    is_quiet,
    set_schedule,
)


def at(hh, mm=0, weekday=2):
    """A real date for each weekday.

    Deriving the day by adding to a fixed date overflows into the next month
    and lands on the wrong weekday -- which is a bug in the test, not in the
    schedule. Build the date from the weekday instead.
    """
    # 2026-09-28 is a Monday -> weekday 0.
    return datetime(2026, 9, 28) + timedelta(days=weekday, hours=hh, minutes=mm)


class TestDefaultWindow:
    def test_default_is_23_to_7(self):
        assert (DEFAULT_START, DEFAULT_END) == (23, 7)

    @pytest.mark.parametrize("hour", [23, 0, 1, 3, 6])
    def test_quiet_across_midnight(self, hour):
        """00:00-06:59 is the half a naive start<end test gets wrong."""
        assert QuietWindow(23, 7).contains(at(hour).time())

    @pytest.mark.parametrize("hour", [7, 12, 18, 22])
    def test_loud_outside_the_window(self, hour):
        assert not QuietWindow(23, 7).contains(at(hour).time())

    def test_boundaries_are_half_open(self):
        """23:00 is quiet, 07:00 is not -- the window ends when it ends."""
        assert QuietWindow(23, 7).contains(at(23, 0).time())
        assert not QuietWindow(23, 7).contains(at(7, 0).time())


class TestSameHour:
    def test_start_equals_end_is_all_day(self):
        """23->23 is a 24h window, not an empty one."""
        assert QuietWindow(23, 23).contains(at(23).time())
        assert QuietWindow(23, 23).contains(at(12).time())

    def test_same_day_window(self):
        w = QuietWindow(9, 18)
        assert w.contains(at(12).time())
        assert not w.contains(at(19).time())


class TestPerDay:
    def test_each_day_can_differ(self):
        s = QuietSchedule.parse(
            {"default": {"start": 23, "end": 7}, "sat": {"start": 0, "end": 23}}
        )
        # Saturday runs 00:00-22:59. The window is half-open, so 23:00 is out.
        assert s.is_quiet(at(12, weekday=5)), "sabado 12h dentro de 0-23"
        assert not s.is_quiet(at(23, weekday=5)), "sabado 23h fora de 0-23"
        assert not s.is_quiet(at(12, weekday=0)), "segunda usa o default 23-7"

    def test_absent_day_falls_back_to_default(self):
        s = QuietSchedule.parse({"default": {"start": 23, "end": 7}, "sat": {"start": 1, "end": 2}})
        assert s.is_quiet(at(23, weekday=2)), "quarta nao foi definida -> default"

    def test_empty_schedule_is_all_default(self):
        s = QuietSchedule.parse({})
        assert s.is_quiet(at(2)) and not s.is_quiet(at(12))


class TestParsing:
    def test_json_string_from_the_admin_form(self):
        s = QuietSchedule.parse('{"default":{"start":22,"end":6}}')
        assert s.is_quiet(at(23)) and not s.is_quiet(at(12))

    def test_bad_json_falls_back_without_raising(self):
        s = QuietSchedule.parse("{not json")
        assert s.is_quiet(at(2)) and not s.is_quiet(at(12))

    def test_out_of_range_hour_is_clamped(self):
        assert QuietWindow.parse({"start": 99, "end": 5}).start == DEFAULT_START
        assert QuietWindow.parse({"start": "abc", "end": 5}).start == DEFAULT_START

    def test_non_dict_input_is_safe(self):
        assert QuietSchedule.parse(None).is_quiet(at(2)) is True

    def test_round_trip(self):
        raw = {"default": {"start": 23, "end": 7}, "fri": {"start": 20, "end": 4}}
        assert QuietSchedule.parse(raw).as_dict() == raw


class TestRuntime:
    def test_set_schedule_applies_without_restart(self):
        try:
            set_schedule({"default": {"start": 10, "end": 11}})
            assert is_quiet(at(10)) and not is_quiet(at(12))
            set_schedule({"default": {"start": 23, "end": 7}})
            assert is_quiet(at(2))
        finally:
            set_schedule({"default": {"start": DEFAULT_START, "end": DEFAULT_END}})


class TestSpeakIsGated:
    """The behaviour that matters: at 02:00 nothing is played, at noon it is.

    Success alone cannot tell these apart -- both paths return Result.ok --
    so the assertion is on play_tts actually being reached.
    """

    @staticmethod
    def _pipeline():
        import assistant

        obj = assistant.PhantasmaPipeline.__new__(assistant.PhantasmaPipeline)
        obj.audio_capture = None
        obj._speaking = False
        obj._feedback_window_seconds = 0
        return obj

    def test_quiet_hour_does_not_speak(self, monkeypatch):
        import assistant
        from src.pipeline import quiet

        played = []
        import audio_utils
        monkeypatch.setattr(audio_utils, "play_tts", lambda *a, **k: played.append(a))
        monkeypatch.setattr(quiet, "is_quiet", lambda *a, **k: True)
        monkeypatch.setattr(assistant.time, "sleep", lambda *a: None)
        self._pipeline()._speak("boa noite")
        assert played == [], "falou durante o periodo noturno"

    def test_daytime_does_speak(self, monkeypatch):
        import assistant
        from src.pipeline import quiet

        played = []
        import audio_utils
        monkeypatch.setattr(audio_utils, "play_tts", lambda *a, **k: played.append(a))
        monkeypatch.setattr(quiet, "is_quiet", lambda *a, **k: False)
        monkeypatch.setattr(assistant.time, "sleep", lambda *a: None)
        self._pipeline()._speak("bom dia")
        assert played, "nao falou fora do periodo noturno"


class TestAdminForm:
    """The owner edits /admin/config, not the database, so the form is the API.

    Writes are intercepted: the POST handler calls set_setting (the real dev
    database) and quiet.set_schedule (the process-global cache). Neither may be
    touched by a test run, and the assertions are about what it WOULD have
    written.

    The conftest admin_client fixture is unused here on purpose: it references
    routes.app, an attribute that does not exist, so every test that asks for it
    errors during setup. create_app() is the documented entry point.
    """

    @staticmethod
    def _client():
        from src.api import admin as admin_mod
        from src.api.routes import create_app

        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[admin_mod.SESSION_KEY] = "test-admin@example.invalid"
        return client

    def _post(self, monkeypatch, **overrides):
        import src.api.admin as admin_mod

        admin_client = self._client()

        written = {}
        applied = {}

        monkeypatch.setattr(
            admin_mod, "set_setting",
            lambda key, value, **kw: written.__setitem__(key, value),
        )
        # Spying with a bare store would leave a raw dict and hide the fact
        # that the running process parses what the form sends. Parse it the
        # way set_schedule really does, so the assertion is about behaviour.
        monkeypatch.setattr(
            admin_mod.quiet, "set_schedule",
            lambda sched: applied.__setitem__(
                "schedule", admin_mod.quiet.QuietSchedule.parse(sched)
            ),
        )

        form = {
            "action": "save_quiet",
            "quiet_start_default": "23", "quiet_end_default": "7",
        }
        for key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            form[f"quiet_start_{key}"] = "23"
            form[f"quiet_end_{key}"] = "7"
        form.update(overrides)

        resp = admin_client.post("/admin/config", data=form, follow_redirects=False)
        return resp, written, applied

    def test_saved_hours_reach_the_running_process(self, monkeypatch):
        import json

        resp, written, applied = self._post(monkeypatch)
        assert resp.status_code in (301, 302)
        saved = json.loads(written["quiet_schedule"])
        assert saved["default"] == {"start": 23, "end": 7}
        # The in-process apply is the point: without it the owner saves 23-7
        # and the assistant keeps the old window until someone restarts it.
        assert applied["schedule"].default.as_dict() == {"start": 23, "end": 7}

    def test_per_day_override(self, monkeypatch):
        import json

        _resp, written, applied = self._post(
            monkeypatch,
            quiet_start_fri="22", quiet_end_fri="6",
        )
        saved = json.loads(written["quiet_schedule"])
        assert saved["fri"] == {"start": 22, "end": 6}
        assert applied["schedule"].days["fri"].as_dict() == {"start": 22, "end": 6}

    def test_missing_end_does_not_become_all_day(self, monkeypatch):
        """A dropped end field must not default to start, i.e. a 24h window."""
        import json

        _resp, written, _applied = self._post(monkeypatch, quiet_end_default="")
        saved = json.loads(written["quiet_schedule"])
        assert saved["default"] == {"start": 23, "end": 7}

    def test_out_of_range_hour_is_clamped(self, monkeypatch):
        import json

        _resp, written, _applied = self._post(monkeypatch, quiet_start_default="99")
        saved = json.loads(written["quiet_schedule"])
        assert saved["default"]["start"] == 23

    def test_form_renders_all_days(self):
        html = self._client().get("/admin/config").get_data(as_text=True)
        assert "quiet_start_default" in html
        for key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            assert f"quiet_start_{key}" in html
            assert f"quiet_end_{key}" in html


class TestNightActivation:
    """Night mode must stop the assistant WAKING, not just stop it talking.

    The 2026-09-29 03:41 event is the specification. A false positive (0.80 vs
    0.70) inside quiet hours reached _play_audio_feedback(), which speaks
    directly rather than through _speak, so the silence gate never saw it: the
    assistant said "Sim." in an empty house and spent 23 s on an STT call that
    returned nothing. These tests drive the real state machine.
    """

    @staticmethod
    def _pipeline(detected=True, model="ola_fantasma", score=0.80):
        import assistant

        obj = assistant.PhantasmaPipeline.__new__(assistant.PhantasmaPipeline)
        obj._collecting_feedback = False
        obj._feedback_start_time = None
        obj._collecting_speech = False
        obj._hotword_detected = False
        obj._last_level_log = 0.0
        obj._last_score_log = 0.0
        obj._feedback_window_seconds = 0

        calls = []

        # The 03:41 event as the docstring above states it: 0.80 against a 0.70
        # bar, in a quiet house. A quiet room pays no noise penalty, so the bar
        # in force is the base threshold -- which is what the double reports.
        bar = 0.70

        class _Hotword:
            def process(self, frame):
                return (detected, model if detected else None)

            def reject_detection(self):
                calls.append("reject")

            def reset(self):
                calls.append("reset")

            # The real HotwordDetector has this since 2026-10-04, and the
            # score-logging branch now goes through it rather than reading
            # `_thresholds` behind its back. Without it this double raised
            # AttributeError on a method the production collaborator really
            # has -- a stub that lied about the interface, not a test that
            # asserted the wrong thing.
            def effective_threshold(self, name):
                return bar

            def noise_state(self):
                return ""

        class _Vad:
            def reset(self):
                calls.append("vad-reset")

            def process_chunk(self, frame):
                return []

        obj.vad = _Vad()
        obj.hotword = _Hotword()
        obj.hotword.last_predictions = {model: score} if detected else {}
        obj._ignore_while_speaking = lambda: False
        obj._play_audio_feedback = lambda: calls.append("SPOKE")
        obj._process_speech = lambda: calls.append("stt")
        return obj, calls

    @staticmethod
    def _frame():
        import numpy as np

        return np.zeros(1280, dtype=np.int16)

    def test_quiet_hours_do_not_speak_on_detection(self, monkeypatch):
        from src.pipeline import quiet

        monkeypatch.setattr(quiet, "is_quiet", lambda *a, **k: True)
        obj, calls = self._pipeline()
        obj._process_audio_frame(self._frame())
        assert "SPOKE" not in calls, "falou durante as quiet hours"
        assert calls == ["reject"]

    def test_quiet_hours_do_not_start_collecting_speech(self, monkeypatch):
        """No STT either: the 03:41 call burned 23s transcribing nothing."""
        from src.pipeline import quiet

        monkeypatch.setattr(quiet, "is_quiet", lambda *a, **k: True)
        obj, calls = self._pipeline()
        obj._process_audio_frame(self._frame())
        assert obj._collecting_speech is False
        assert obj._hotword_detected is False
        assert "stt" not in calls

    def test_the_cooldown_survives_a_rejection(self, monkeypatch):
        """reset() clears _last_detection, so a steady hum would re-score and
        re-reject every frame. Rejection must keep the detector quiet."""
        import numpy as np

        from src.pipeline import quiet
        from src.pipeline.audio import HotwordDetector

        monkeypatch.setattr(quiet, "is_quiet", lambda *a, **k: True)
        d = object.__new__(HotwordDetector)
        d._streaks = {"ola_fantasma": 3}
        d._patience = {"ola_fantasma": 2}
        d._last_detection = 0.0
        d._accum = np.zeros(0, dtype=np.int16)
        d.oww_model = None
        d._prime_buffer = lambda *a, **k: None

        d.reject_detection()
        assert d._last_detection > 0.0, "cooldown was cleared, expect a reset storm"
        assert d._streaks == {} and d._patience == {}

    def test_outside_quiet_hours_nothing_changes(self, monkeypatch):
        from src.pipeline import quiet

        monkeypatch.setattr(quiet, "is_quiet", lambda *a, **k: False)
        obj, calls = self._pipeline()
        obj._process_audio_frame(self._frame())
        assert "SPOKE" in calls, "o assistente deixou de funcionar fora das quiet hours"
        assert obj._collecting_speech is True
        assert obj._hotword_detected is True
        assert "reject" not in calls
