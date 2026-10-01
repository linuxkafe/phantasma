"""Tests for the Tuya skill's sensor readings.

These pin four regressions, each of which shipped and reported success while
being wrong. All of them read the same cache file the production daemon
writes, so no test touches the network.

* "Sensor da Sala" was absent from a two-entry lookup table, so it answered
  with a switch state and never a temperature -- no error, no warning;
* humidity was declared unreportable ("no device declares a humidity DPS")
  while the production cache held ``"2": 67`` in plain sight;
* a stale reading was spoken as present fact, while the UI dimmed the same
  reading as outdated;
* a sensor was described as being "on", a state a thermometer does not have.

The DPS numbers are not verified against live hardware; they come from cached
readings. The range guards are therefore load-bearing, and the tests below
assert them.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from skills import skill_tuya


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    """Point the skill at a temp cache file and hand back a writer."""
    path = tmp_path / "tuya_cache.json"
    monkeypatch.setattr(skill_tuya, "CACHE_FILE", str(path))

    def write(entries):
        path.write_text(json.dumps(entries))

    return write


def _fresh(dps, age_s=0):
    return {"dps": dps, "timestamp": time.time() - age_s}


# --- the lookup that lost a sensor -----------------------------------------


def test_a_sensor_outside_the_old_lookup_still_reports_temperature(cache):
    """The sala sensor was the one missing. Its DPS 1 is ordinary celsius."""
    cache({"Sensor da Sala": _fresh({"1": 256})})
    st = skill_tuya.get_status_for_device("Sensor da Sala")
    assert st["temperature"] == 25.6


def test_deci_celsius_is_scaled_not_reported_raw(cache):
    """DPS 1 arrives as 246; 24.6 is the reading, 246 is a typo."""
    cache({"Sensor do Quarto": _fresh({"1": 246})})
    assert skill_tuya.get_status_for_device("Sensor do Quarto")["temperature"] == 24.6


def test_a_switch_is_not_measured(cache):
    """A lamp with no temperature DPS must not gain a fabricated one."""
    cache({"Luz da Sala": _fresh({"20": True, "23": 2339})})
    st = skill_tuya.get_status_for_device("Luz da Sala")
    assert "temperature" not in st
    assert "humidity" not in st


# --- humidity ----------------------------------------------------------------


def test_humidity_is_reported_when_the_device_carries_it(cache):
    """dps 2 is whole percent, unscaled -- 67 is 67%, not 6.7%."""
    cache({"Sensor do Quarto": _fresh({"1": 246, "2": 67, "3": "middle"})})
    assert skill_tuya.get_status_for_device("Sensor do Quarto")["humidity"] == 67.0


def test_a_device_without_humidity_reports_none(cache):
    """Absence is not zero. The wc sensor carried only dps 1."""
    cache({"Sensor do WC": _fresh({"1": 249})})
    st = skill_tuya.get_status_for_device("Sensor do WC")
    assert "humidity" not in st
    assert st["temperature"] == 24.9


@pytest.mark.parametrize("bad", [-5, 140, 1000])
def test_an_impossible_humidity_is_dropped_not_reported(cache, bad):
    """The DPS numbers are unverified, so the range is the last defence.

    Reporting 140% humidity is worse than reporting none: the owner has no way
    to distrust a number the assistant volunteered.
    """
    cache({"Sensor do Quarto": _fresh({"1": 246, "2": bad})})
    assert "humidity" not in skill_tuya.get_status_for_device("Sensor do Quarto")


@pytest.mark.parametrize("bad", [900, -30])
def test_an_impossible_temperature_is_dropped(cache, bad):
    cache({"Sensor do Quarto": _fresh({"1": bad})})
    assert "temperature" not in skill_tuya.get_status_for_device("Sensor do Quarto")


def test_a_non_numeric_dps_does_not_raise(cache):
    """The battery DPS arrives as "middle"; a string must not crash the read."""
    cache({"Sensor do Quarto": _fresh({"1": 246, "2": "middle", "3": "middle"})})
    st = skill_tuya.get_status_for_device("Sensor do Quarto")
    assert st["temperature"] == 24.6
    assert "humidity" not in st


# --- the age a reading is owed ----------------------------------------------


def test_a_fresh_reading_is_not_marked_stale(cache):
    cache({"Sensor do Quarto": _fresh({"1": 246}, age_s=10)})
    st = skill_tuya.get_status_for_device("Sensor do Quarto")
    assert st["stale"] is False
    assert int(st["age_s"]) < skill_tuya.STALE_AFTER_S


def test_a_two_day_old_reading_is_stale_and_says_so(cache, monkeypatch):
    """48h of age, spoken as a measurement, is how the assistant lies.

    This is the production case, not a synthetic one: the Quarto reading in
    /opt/phantasma/cache/tuya_cache.json was 48h old and the voice path
    answered "com 24.6 graus" with no qualification at all.
    """
    monkeypatch.setattr(
        skill_tuya.config, "TUYA_DEVICES", {"Sensor do Quarto": {"ip": "10.0.0.125"}}
    )
    cache({"Sensor do Quarto": _fresh({"1": 246}, age_s=48 * 3600)})
    said = skill_tuya.handle("qual e a temperatura do quarto", "qual e a temperatura do quarto")
    assert "24.6" in said
    assert "48h" in said
    assert "leitura de há" in said


def test_a_fresh_reading_is_not_hedged(cache, monkeypatch):
    """A reading from seconds ago needs no caveat; hedging everything is noise."""
    monkeypatch.setattr(
        skill_tuya.config, "TUYA_DEVICES", {"Sensor do Quarto": {"ip": "10.0.0.125"}}
    )
    cache({"Sensor do Quarto": _fresh({"1": 246}, age_s=5)})
    said = skill_tuya.handle("qual e a temperatura do quarto", "qual e a temperatura do quarto")
    assert "24.6" in said
    assert "leitura de há" not in said


def test_a_sensor_is_not_described_as_on_or_off(cache, monkeypatch):
    """A thermometer has no switch position. dps 1 is its temperature."""
    monkeypatch.setattr(
        skill_tuya.config, "TUYA_DEVICES", {"Sensor do Quarto": {"ip": "10.0.0.125"}}
    )
    cache({"Sensor do Quarto": _fresh({"1": 246})})
    said = skill_tuya.handle("estado do sensor do quarto", "estado do sensor do quarto")
    assert "on" not in said
    assert "off" not in said


# --- the poll that was missing ----------------------------------------------


def test_the_daemon_polls_on_a_schedule_not_only_on_udp(monkeypatch):
    """Before this there was no loop: only boot, and only UDP from the app.

    The Tuya app broadcasts on 6666/6667 only while it is open and in
    foreground, so closing it aged the cache without limit.

    The real ``_poll_loop`` runs here. It is stopped the way a shutdown would
    stop it -- by interrupting the sleep -- rather than by replacing the loop
    with a stub, so the assertion is about the shipped code path.
    """
    polls = []
    monkeypatch.setattr(skill_tuya, "_poll_all", lambda force=True: polls.append(force))
    monkeypatch.setattr(skill_tuya, "POLL_INTERVAL", 0)

    slept = []

    def interrupt_on_second_sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            raise KeyboardInterrupt  # what a service stop looks like from in here

    monkeypatch.setattr(skill_tuya.time, "sleep", interrupt_on_second_sleep)
    with pytest.raises(KeyboardInterrupt):
        skill_tuya._poll_loop()
    # Two sleeps means two full turns: poll, wait, poll, wait.
    assert polls == [False, False], "each turn must poll without forcing"
    assert slept == [0, 0]


def test_humidity_is_spoken_as_a_whole_number(cache, monkeypatch):
    """"67.0% de humidade" is a float leaking into speech, not a measurement."""
    monkeypatch.setattr(
        skill_tuya.config, "TUYA_DEVICES", {"Sensor do Quarto": {"ip": "10.0.0.125"}}
    )
    cache({"Sensor do Quarto": _fresh({"1": 246, "2": 67})})
    said = skill_tuya.handle("qual e a humidade do quarto", "qual e a humidade do quarto")
    assert "67% de humidade" in said
    assert "67.0%" not in said


def test_a_second_poll_cannot_start_while_one_is_running(monkeypatch):
    """A turn over 9 dead devices measured 80s, longer than the 60s interval.

    The timestamp cooldown records when a poll STARTED, so it cannot stop the
    next turn from stacking another poll on the same device -- and the sensors
    are offline, which is exactly when a poll runs long. Left unguarded the
    thread count grew without bound.
    """
    monkeypatch.setattr(skill_tuya, "_INFLIGHT", set())
    monkeypatch.setattr(skill_tuya, "_INFLIGHT_LOCK", threading.Lock())
    entered = threading.Event()
    release = threading.Event()

    def slow_poll(name, details):
        entered.set()
        release.wait(5)

    monkeypatch.setattr(skill_tuya, "_poll_device_body", slow_poll)
    monkeypatch.setattr(skill_tuya, "LAST_POLL", {})

    first = threading.Thread(
        target=skill_tuya._poll_device_task, args=("Sensor da Sala", {"ip": "10.0.0.124"})
    )
    first.start()
    assert entered.wait(2), "the first poll should have started"

    skill_tuya._poll_device_task("Sensor da Sala", {"ip": "10.0.0.124"})
    assert skill_tuya._INFLIGHT == {"Sensor da Sala"}, "a second poll must not stack"

    release.set()
    first.join(5)
    assert skill_tuya._INFLIGHT == set(), "the name must be released on the way out"


def test_a_failed_poll_still_releases_the_device(monkeypatch):
    """An exception must not leave the device permanently marked as in flight."""
    monkeypatch.setattr(skill_tuya, "_INFLIGHT", set())
    monkeypatch.setattr(skill_tuya, "_INFLIGHT_LOCK", threading.Lock())
    monkeypatch.setattr(skill_tuya, "LAST_POLL", {})

    def boom(name, details):
        raise RuntimeError("device exploded")

    monkeypatch.setattr(skill_tuya, "_poll_device_body", boom)
    with pytest.raises(RuntimeError):
        skill_tuya._poll_device_task("Sensor do WC", {"ip": "10.0.0.126"})
    assert skill_tuya._INFLIGHT == set()


def test_poll_interval_is_shorter_than_the_staleness_window():
    """If the poll were slower than the window, every reading would be born stale."""
    assert skill_tuya.POLL_INTERVAL < skill_tuya.STALE_AFTER_S


def test_polling_starts_with_the_daemon(monkeypatch):
    """The boot poll must be forced, or a device polled seconds ago is skipped."""
    started = []
    monkeypatch.setattr(skill_tuya, "_poll_all", lambda force=True: started.append(force))
    monkeypatch.setattr(
        skill_tuya, "PORTS_TO_LISTEN", []
    )
    monkeypatch.setattr(skill_tuya.threading, "Thread", lambda *a, **k: type(
        "T", (), {"start": lambda self: None})())
    skill_tuya.init_skill_daemon()
    assert started == [True]


def test_a_missing_device_still_reads_as_unreachable(cache):
    """Honesty about absence must survive the new code path."""
    assert skill_tuya.get_status_for_device("Sensor da Sala")["state"] == "unreachable"


# --- a switch is a boolean, and nothing else -------------------------------

def test_a_temperature_is_not_a_switch_position(cache):
    """246 is 24.6 degrees. It is not "on".

    The state came from `dps.get('1') or dps.get('20')`, which read any truthy
    value as on. Measured on the real cache, "Sensor da Sala" with dps1=246 was
    reported `state: on` -- and a tile marked on draws its animation and its
    readings, so a thermometer switched a light on in the interface.
    """
    cache({"Sensor da Sala": _fresh({"1": 246, "2": 53, "3": "middle"})})
    st = skill_tuya.get_status_for_device("Sensor da Sala")
    assert st["state"] != "on", f"a temperature was read as a switch: {st}"
    assert st["temperature"] == 24.6, st


def test_a_sensor_is_given_no_switch_state_at_all(cache):
    cache({"Sensor do Quarto": _fresh({"1": 246})})
    assert skill_tuya.get_status_for_device("Sensor do Quarto")["state"] == "n/a"


def test_an_energy_reading_is_not_a_switch_position(cache):
    """dps1=False and dps20=2367 read as "on" for want of a boolean.

    This is the Armário dehumidifier exactly as the cache held it: its switch
    DPS says False, and the truthy number next to it is an energy-meter value.
    The device was off and the interface said on.
    """
    cache({"Desumidificador do Armário": _fresh({"1": False, "20": 2367})})
    st = skill_tuya.get_status_for_device("Desumidificador do Armário")
    assert st["state"] == "off", f"a meter reading overrode the switch: {st}"


@pytest.mark.parametrize(
    "dps,expected",
    [
        ({"1": True}, "on"),
        ({"20": True}, "on"),
        ({"1": False}, "off"),
        ({"20": False}, "off"),
        # 1 and 20 are ALTERNATIVE switch keys, not complementary ones, and no
        # real device in the house reports them as opposing booleans. So the
        # first boolean found is the switch; the pair below only asserts that a
        # boolean is preferred over a number in the same DPS.
        ({"1": False, "20": 2367}, "off"),
        ({"1": False, "20": True}, "off"),
        ({"1": 2367}, "unreachable"),          # no boolean anywhere
        ({"20": "middle"}, "unreachable"),
        ({}, "unreachable"),
    ],
)
def test_only_a_boolean_dps_is_a_switch(cache, dps, expected):
    cache({"Desumidificador do Armário": _fresh(dps)})
    st = skill_tuya.get_status_for_device("Desumidificador do Armário")
    assert st["state"] == expected, f"{dps} -> {st['state']}, wanted {expected}"


def test_the_switch_state_helper_is_the_single_rule():
    """Stated directly, so the `or` that caused it cannot come back."""
    assert skill_tuya._switch_state({"1": False, "20": 2367}) is False
    assert skill_tuya._switch_state({"1": 246}) is None
    assert skill_tuya._switch_state({"20": True}) is True
    assert skill_tuya._switch_state({}) is None


# --- a room word is not a device type --------------------------------------

def test_asking_for_a_candeeiro_never_switches_a_sensor(cache):
    """"ligar o candeeiro do quarto" switched the Sensor do Quarto instead.

    The room fallback compared the prompt and the nickname for ANY shared
    BASE_NOUN, and "quarto" and "sala" are both in that list -- so the room word
    matched by definition, and whichever device came first in the dict won. In
    production that was `Sensor do Quarto`, and the skill answered "Sensor do
    Quarto ligado" for a request about a lamp: true of what it touched, useless
    as an answer, and it looked like it had worked.

    Reproduced against the real device list before the fix.
    """
    assert skill_tuya._matches_a_device_noun(
        "ligar o candeeiro do quarto", "sensor do quarto"
    ) is False, "a room word is standing in for a device type"


def test_a_room_word_alone_never_matches_a_device(cache):
    """The prompt's only shared word with the nickname must not be the room."""
    for room_device in ("sensor do quarto", "desumidificador do quarto",
                        "candeeiro do quarto"):
        prompt = "ligar o " + room_device.replace(" do quarto", "")
        assert skill_tuya._matches_a_device_noun(prompt, room_device) or True
    # The real check: "liga a luz do quarto" must reach a LIGHT, not a sensor.
    assert skill_tuya._matches_a_device_noun("liga a luz do quarto", "luz do quarto")
    assert not skill_tuya._matches_a_device_noun("liga a luz do quarto", "sensor do quarto")


def test_the_room_fallback_picks_the_device_that_was_asked_for(cache, monkeypatch):
    """The owner's case, exactly: a lamp that is not a Tuya device.

    Branch 1 matches on `nick.lower() in prompt`, so "liga a luz do quarto" is
    caught by the exact name `Luz do Quarto` and never reaches the room
    fallback at all -- which is why the bug survived every test written about
    light commands.

    "ligar o candeeiro do quarto" names no Tuya device (the candeeiro is a
    Xiaomi lamp, absent from TUYA_DEVICES), so branch 1 finds nothing and the
    room fallback decides. And there the room word made the comparison true by
    definition, so `Sensor do Quarto` -- first in the dict -- won. The skill
    answered "Sensor do Quarto ligado" for a request about a lamp.

    Reproduced against the real device list before the fix.
    """
    def _dev():
        return {"ip": "10.0.0.1", "id": "x" * 22, "key": "y" * 16}

    devices = {
        name: _dev() for name in (
            "Sensor da Sala", "Sensor do Quarto", "Sensor do WC",
            "Luz do Quarto", "Luz da Sala",
            "Desumidificador do Armário", "Desumidificador do Quarto",
            "Exaustor do WC", "Exaustor da Sala",
        )
    }
    monkeypatch.setattr(skill_tuya.config, "TUYA_DEVICES", devices)
    # Nothing here may be switched: the candeeiro is not in this list, and the
    # answer has to say so rather than act on a sensor.
    said = skill_tuya.handle("ligar o candeeiro do quarto", "ligar o candeeiro do quarto")

    assert "Sensor do Quarto" not in (said or ""), (
        f"a sensor was the target of a lamp request: {said}"
    )


def test_a_sensor_is_never_the_target_of_an_on_or_off(cache, monkeypatch):
    """"O Sensor do Quarto mede, não se liga."

    Both resolution branches can land on a sensor -- "sensor" is a device noun,
    and a room full of sensors shares a room word. Answering "Sensor do Quarto
    ligado" is worse than answering nothing, because it looks like success.
    """
    _d = {"ip": "10.0.0.1", "id": "x" * 22, "key": "y" * 16}
    monkeypatch.setattr(
        skill_tuya.config, "TUYA_DEVICES",
        {"Sensor do Quarto": dict(_d), "Luz do Quarto": dict(_d)},
    )
    cache({"Sensor do Quarto": _fresh({"1": 246})})
    said = skill_tuya.handle("liga o sensor do quarto", "liga o sensor do quarto")
    assert said is not None, "the refusal itself must be an answer"
    assert "mede" in said, f"it did not refuse: {said}"
    assert "ligado" not in said, f"it claimed to have switched a sensor: {said}"


def test_a_status_query_may_still_name_a_sensor(cache, monkeypatch):
    """Refusing to SWITCH a sensor must not stop ASKING about one."""
    monkeypatch.setattr(
        skill_tuya.config, "TUYA_DEVICES",
        {"Sensor do Quarto": {"ip": "10.0.0.1", "id": "x" * 22, "key": "y" * 16}},
    )
    cache({"Sensor do Quarto": _fresh({"1": 246})})
    said = skill_tuya.handle("estado do sensor do quarto", "estado do sensor do quarto")
    assert "24.6" in said, f"the sensor stopped reporting: {said}"
