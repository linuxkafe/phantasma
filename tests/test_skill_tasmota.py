"""Tests for skill_tasmota (T053) — controlo local Tasmota sem network.

All HTTP is mocked. No config.py access required: the skill reads
TASMOTA_DEVICES_JSON from the environment, so the tests monkeypatch
os.environ. The skill is class-based, so it is imported and instantiated
directly (no loader dependency).
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.skill_tasmota import TasmotaSkill, _load_devices, _normalize  # noqa: E402


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    monkeypatch.delenv("TASMOTA_DEVICES_JSON", raising=False)


@pytest.fixture
def devices_env(monkeypatch):
    monkeypatch.setenv("TASMOTA_DEVICES_JSON", json.dumps({"balcao": "10.0.0.108"}))
    return "10.0.0.108"


def _fake_power_response(cmnd):
    if cmnd in ("Power ON", "Power"):
        return {"POWER": "ON"}
    if cmnd in ("Power OFF",):
        return {"POWER": "OFF"}
    return {"POWER": "TOGGLE"}


def _install_fake(monkeypatch, host="10.0.0.108", record=None):
    def fake_get(url, *, params=None, timeout=None):
        if record is not None:
            record.append((url, params))
        cmnd = (params or {}).get("cmnd", "")
        resp = httpx.Response(200, json=_fake_power_response(cmnd))
        return resp

    monkeypatch.setattr(httpx, "get", fake_get)


def test_normalize_strips_accents():
    assert _normalize("Luz do Balcão") == "luz do balcao"


def test_load_devices_from_env(devices_env):
    assert _load_devices() == {"balcao": "10.0.0.108"}


def test_load_devices_empty_without_env():
    assert _load_devices() == {}


def test_load_devices_invalid_json_tolerated(monkeypatch):
    monkeypatch.setenv("TASMOTA_DEVICES_JSON", "não é json {")
    assert _load_devices() == {}


def test_handle_on_sends_power_on(monkeypatch, devices_env):
    calls = []
    _install_fake(monkeypatch, devices_env, calls)
    skill = TasmotaSkill()
    reply = skill.handle("liga a luz do balcão")
    assert "ligada" in reply
    assert any("Power ON" in c[1]["cmnd"] for c in calls)


def test_handle_off_sends_power_off(monkeypatch, devices_env):
    calls = []
    _install_fake(monkeypatch, devices_env, calls)
    skill = TasmotaSkill()
    reply = skill.handle("desliga o balcão")
    assert "desligada" in reply
    assert any("Power OFF" in c[1]["cmnd"] for c in calls)


def test_handle_status_queries_power(monkeypatch, devices_env):
    calls = []
    _install_fake(monkeypatch, devices_env, calls)
    skill = TasmotaSkill()
    reply = skill.handle("está a luz do balcão ligada?")
    assert "está ligada" in reply
    assert any(c[1]["cmnd"] == "Power" for c in calls)


def test_handle_ignores_unrelated_prompt(monkeypatch, devices_env):
    _install_fake(monkeypatch, devices_env)
    skill = TasmotaSkill()
    assert skill.handle("liga a câmara da sala") is None


def test_handle_returns_error_when_unreachable(monkeypatch, devices_env):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", fail)
    skill = TasmotaSkill()
    reply = skill.handle("liga o balcão")
    assert "não consegui" in reply.lower()


def test_handle_error_when_no_devices_configured(monkeypatch):
    skill = TasmotaSkill()
    reply = skill.handle("liga o balcão")
    assert "não estão configuradas" in reply


def test_get_status_for_device_on(monkeypatch, devices_env):
    _install_fake(monkeypatch, devices_env)
    skill = TasmotaSkill()
    assert skill.get_status_for_device("balcão") == {
        "state": "on",
        "device": "balcao",
    }


def test_get_status_for_device_unknown_returns_unreachable(monkeypatch, devices_env):
    _install_fake(monkeypatch, devices_env)
    skill = TasmotaSkill()
    assert skill.get_status_for_device("forno") == {"state": "unreachable"}


def test_get_status_for_device_unreachable_on_failure(monkeypatch, devices_env):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", fail)
    skill = TasmotaSkill()
    assert skill.get_status_for_device("balcão") == {"state": "unreachable"}


def test_matches_covers_accented_and_plain_forms():
    skill = TasmotaSkill()
    assert skill.matches("liga a luz do balcão")
    assert skill.matches("o balcao")
    assert not skill.matches("fala-me do jogo")


def test_priority_wins_over_legacy_skills():
    """O loader ordena por PRIORITY desc: 50 > 0 (chacon/tuya legacy)."""
    assert TasmotaSkill.PRIORITY == 50
    assert TasmotaSkill.PRIORITY > 0


def test_toggle_command(monkeypatch, devices_env):
    calls = []

    def fake_get(url, *, params=None, timeout=None):
        calls.append(params["cmnd"])
        return httpx.Response(200, json={"POWER": "ON"})

    monkeypatch.setattr(httpx, "get", fake_get)
    skill = TasmotaSkill()
    reply = skill.handle("alterna o balcão")
    assert "Power TOGGLE" in calls
    assert "ligada" in reply
