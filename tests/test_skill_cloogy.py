"""Tests for the Cloogy skill's readings.

Two things are pinned here, both of them regressions rather than features:

* the plug's switch position used to be reported as "on" whenever *any*
  reading existed, so a plug drawing 0 W still showed as on in the UI;
* only the instantaneous power was read, even though the endpoint returns
  accumulated energy, money and carbon -- and answering "quanto gastei?"
  from instantaneous watts was never going to be right.

No test touches the network: the API is stubbed at the request boundary.
"""

from __future__ import annotations

import json

import pytest

from skills import skill_cloogy

READING = {
    "Read": 1.5,  # kW
    "Consumption": 123.456,
    "ReadCurrency": 18.42,
    "CurrencySymbol": "€",
    "ReadCarbon": 4210,
    "Granularity": "instant",
}


def _series(values):
    """Build the window of samples the instant endpoint returns."""
    return [{**READING, "Read": v} for v in values]


@pytest.fixture()
def fake_api(monkeypatch, tmp_path):
    """Stub the Cloogy endpoints and point the cache at a temp file."""
    calls = {"n": 0}

    monkeypatch.setattr(skill_cloogy, "CURRENT_TOKEN", "test-token")
    monkeypatch.setattr(skill_cloogy, "CACHE_FILE", str(tmp_path / "cloogy.json"))
    monkeypatch.setattr(
        skill_cloogy.config, "CLOOGY_DEVICES", {"forno": "169809", "casa": "169806"}
    )

    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(*_args, **_kwargs):
        calls["n"] += 1
        return _Resp(_series([1.5, 1.4, 1.5, 1.5]))

    monkeypatch.setattr(skill_cloogy.httpx, "get", fake_get)
    return calls


def test_readings_expose_every_useful_field(fake_api):
    d = skill_cloogy._fetch_readings("169809")
    assert d["power_w"] == 1500.0  # Read is kW
    assert d["consumption_kwh"] == 123.456
    assert d["currency"] == 18.42
    assert d["carbon_g"] == 4210
    assert d["granularity"] == "instant"
    # The window comes back too, so a frozen plug is visible without waiting
    # for the cache to fill.
    assert len(d["series"]) == 4


def test_old_reading_helper_still_works(fake_api):
    """`_fetch_reading` is the old entry point; it must keep its contract."""
    assert skill_cloogy._fetch_reading("169809") == 1500.0


def test_zero_watts_is_reported_off_not_on(fake_api):
    """The regression: any reading at all used to mean "on".

    A plug that is off draws 0 W, so the old code rendered a switched-off oven
    as if it were running.
    """
    skill_cloogy._update_single_value("169809", 0.0)
    status = skill_cloogy.get_status_for_device("forno")
    assert status["state"] == "off"
    assert status["state_inferred"] is True
    assert status["power_w"] == 0.0


def test_real_draw_is_reported_on(fake_api):
    skill_cloogy._update_single_value("169809", 1500.0)
    status = skill_cloogy.get_status_for_device("forno")
    assert status["state"] == "on"
    assert status["power_w"] == 1500.0


def test_standby_does_not_flip_the_switch_on(fake_api):
    """A plug idling at 3W is not a running oven."""
    skill_cloogy._update_single_value("169809", 3.0)
    assert skill_cloogy.get_status_for_device("forno")["state"] == "off"


def test_tile_carries_the_extra_readings(fake_api):
    d = skill_cloogy._fetch_readings("169809")
    skill_cloogy._update_single_value("169809", d["power_w"], extra={
        k: v for k, v in d.items() if k not in ("power_w", "series")
    })
    status = skill_cloogy.get_status_for_device("forno")
    assert status["consumption_kwh"] == 123.456
    assert status["carbon_g"] == 4210


def test_voice_answer_mentions_only_real_values(fake_api):
    reply = skill_cloogy.handle("quanto gastou o forno", "")
    assert "1500 Watts" in reply
    assert "123.46 kWh" in reply
    assert "18.42" in reply
    assert "media dos ultimos 15 minutos" in reply


def test_answer_omits_fields_the_plug_reports_as_zero(fake_api):
    """Cloogy always returns the same keys; most are zero. Quoting a 0 EUR
    spend as fact is as wrong as the old always-on state was."""

    class _Resp:
        status_code = 200

        def json(self):
            return [{**READING, "ReadCurrency": 0, "ReadCarbon": 0}]

    skill_cloogy.httpx.get = lambda *a, **k: _Resp()
    reply = skill_cloogy.handle("quanto gastou o forno", "")
    assert "1500 Watts" in reply
    assert "CO2" not in reply
    assert "€0.00" not in reply


def test_cached_reading_is_labelled_as_cached(fake_api, monkeypatch):
    """When the live read fails, the cache is used -- but said out loud."""
    skill_cloogy._update_single_value("169809", 1500.0)

    def _boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(skill_cloogy.httpx, "get", _boom)
    reply = skill_cloogy.handle("quanto gastou o forno", "")
    assert "ultima leitura guardada" in reply
    assert "1500 Watts" in reply


def test_no_reading_and_no_cache_says_so(fake_api, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(skill_cloogy.httpx, "get", _boom)
    reply = skill_cloogy.handle("quanto gastou o forno", "")
    assert "Não consegui ler" in reply


def test_unknown_device_is_not_answered(fake_api):
    assert skill_cloogy.handle("quanto gastei no frigorifico", "") is None


def test_cache_file_is_valid_json_after_a_write(fake_api):
    skill_cloogy._update_single_value("169809", 1500.0, extra={"carbon_g": 10})
    with open(skill_cloogy.CACHE_FILE) as fh:
        stored = json.load(fh)
    assert stored["169809"]["val"] == 1500.0
    assert stored["169809"]["carbon_g"] == 10


def test_frozen_reading_is_not_reported_as_on(fake_api):
    """The oven plug (TagId 169809) reports exactly 1.0 kW for every one of 96
    samples over 24h, population stddev 0.0000. That is impossible for a real
    socket, so it must not become "the oven is on"."""
    for _ in range(skill_cloogy.STUCK_SAMPLES + 1):
        skill_cloogy._update_single_value("169809", 1000.0)
    status = skill_cloogy.get_status_for_device("forno")
    assert status["state"] == "unknown"
    assert status["reading_suspect"] is True
    assert "parada" in status["note"]
    assert status["power_w"] == 1000.0  # the raw value is still shown


def test_frozen_api_window_is_caught_on_the_live_read(fake_api, monkeypatch):
    """Same fault, seen in the API window rather than the cache: the live read
    returns the same value for every sample and the answer must say the sensor
    is stuck instead of quoting 1000 W."""
    skill_cloogy.httpx.get = lambda *a, **k: type(
        "R", (), {"status_code": 200, "json": staticmethod(lambda: _series([1.0] * 96))}
    )()
    reply = skill_cloogy.handle("quanto gastou o forno", "")
    assert "parada" in reply
    assert "1000 Watts" not in reply


def test_a_moving_reading_is_still_trusted(fake_api):
    """Guard against the stuck-detector swallowing real data: a socket whose
    power actually varies must keep reporting a state."""
    for w in (0.0, 1400.0, 0.0, 900.0, 20.0, 0.0):
        skill_cloogy._update_single_value("169809", w)
    status = skill_cloogy.get_status_for_device("forno")
    assert status.get("reading_suspect") is not True
    assert status["state"] in ("on", "off")



