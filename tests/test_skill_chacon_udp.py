"""Tests for skill_chacon_udp.

The old skill_chacon reached the Chacon/DIO cloud, whose account is dead (see
aes/tickets/T036). skill_chacon_udp controls the same plug directly over LAN
UDP using the plaintext (bEncrypt=0) path of the Hi-Flying Lumitek firmware,
so it needs no vendor account and no AES key.

These tests do NOT contact the plug. The wires-on-the-table bytes below are
the exact packets validated live against 10.0.0.116:18530 on 2026-09-29.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile

from skills import skill_chacon_udp


def test_module_registers_chacon_udp_skill():
    """The skill is class-based so its PRIORITY wins over skill_tasmota."""
    from skills.base import TriggerType

    assert hasattr(skill_chacon_udp, "TRIGGERS")
    assert hasattr(skill_chacon_udp, "handle")
    assert skill_chacon_udp.ChaconUdpSkill.PRIORITY > 50  # > skill_tasmota
    assert skill_chacon_udp.ChaconUdpSkill.TRIGGER_TYPE == TriggerType.CONTAINS


def test_triggers_cover_the_balcao_nicknames():
    for nickname in ("luz do balcao", "luz do balcão", "balcao"):
        assert nickname in skill_chacon_udp.TRIGGERS


def test_build_packet_on_has_exact_live_bytes():
    """ON packet bytes, as validated against the real plug."""
    expected = bytes.fromhex(
        "0104F0FE6B57E75A1000FFFFDFF121B4010000FFFF04040404"
    )
    assert skill_chacon_udp.build_packet(0x01, skill_chacon_udp._ARG_ON) == expected


def test_build_packet_off_has_exact_live_bytes():
    """OFF packet bytes, as validated against the real plug."""
    expected = bytes.fromhex(
        "0104F0FE6B57E75A1000FFFFDFF121B401000000FF04040404"
    )
    assert skill_chacon_udp.build_packet(0x01, skill_chacon_udp._ARG_OFF) == expected


def test_build_packet_body_is_always_16_bytes():
    pkt = skill_chacon_udp.build_packet(0x01, skill_chacon_udp._ARG_ON)
    assert pkt[8] == 0x10  # dataLen
    assert len(pkt) == 25


def test_intent_is_disjoint():
    assert skill_chacon_udp._intent("desliga a luz do balcao") == "OFF"
    assert skill_chacon_udp._intent("liga a luz do balcao") == "ON"
    assert skill_chacon_udp._intent("que horas sao") is None


def test_handle_returns_none_for_unrelated_prompt():
    assert skill_chacon_udp.handle("que horas sao", "que horas sao") is None


def test_handle_returns_none_without_action():
    """Mentioning the device without an action must not fire a command."""
    assert skill_chacon_udp.handle("e a luz do balcao", "e a luz do balcao") is None


@contextlib.contextmanager
def _acked(value):
    """Pretend the plug rebacked (or did not), without touching the network."""
    original = skill_chacon_udp.send_command
    skill_chacon_udp.send_command = lambda cmd, arg: value
    try:
        yield
    finally:
        skill_chacon_udp.send_command = original


@contextlib.contextmanager
def _temp_state_dir():
    """Point the state file at a throwaway directory for one test."""
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("PHANTASMA_STATE_DIR")
        os.environ["PHANTASMA_STATE_DIR"] = tmp
        try:
            yield tmp
        finally:
            if old is None:
                os.environ.pop("PHANTASMA_STATE_DIR", None)
            else:
                os.environ["PHANTASMA_STATE_DIR"] = old


def test_status_admits_ignorance_instead_of_guessing():
    """Before any command is sent there is nothing to show.

    The answer is `unknown`, not `unreachable`: /device_status reserves that
    word for devices nobody answers for, and this plug does answer. It just
    cannot be read back -- GET_GPIO_STATUS is accepted, but the reback body is
    encrypted with the per-device AES key that only the DIO pairing holds.
    """
    with _temp_state_dir():
        status = skill_chacon_udp.get_status_for_device("luz do balcão")
    assert status["state"] == "unknown"
    assert status["readable"] is False
    assert skill_chacon_udp.get_status_for_device("desumidificador") == {}


def test_status_reports_the_last_command_and_labels_it():
    """The tile shows what we last commanded, and says that is what it is.

    A confirmed command is recorded. A command the plug did not acknowledge is
    not, because then we do not know it acted and writing the intent would be a
    guess dressed up as a fact.
    """
    with _temp_state_dir() as tmp, _acked(True):
        assert skill_chacon_udp.handle("liga a luz do balcao", "") is not None
        status = skill_chacon_udp.get_status_for_device("luz do balcão")
        assert status["state"] == "on"
        assert status["source"] == "last_command"
        assert status["readable"] is False  # an inference, not a reading
        # A command the plug did not acknowledge must not overwrite it.
        skill_chacon_udp.send_command = lambda cmd, arg: False
        skill_chacon_udp.handle("desliga a luz do balcao", "")
        assert skill_chacon_udp.get_status_for_device("luz do balcão")["state"] == "on"
        assert os.path.exists(os.path.join(tmp, "chacon_plug_state.json"))


def test_state_survives_a_new_process(tmp_path, monkeypatch):
    """The record must outlive the request or the tile flickers on every load.

    Written to a temp file and renamed, so a crash mid-write cannot leave
    half-written JSON that would then read as "no state at all".
    """
    monkeypatch.setenv("PHANTASMA_STATE_DIR", str(tmp_path))
    with _acked(True):
        skill_chacon_udp.handle("liga a luz do balcao", "")
    stored = json.loads((tmp_path / "chacon_plug_state.json").read_text())
    assert stored["state"] == "on"
    assert stored["source"] == "last_command"
    assert stored["ts"] > 0


def test_class_and_module_share_one_implementation():
    """The class delegates to the module handler.

    They used to carry byte-identical copies of the send/ack/format block,
    which is precisely how two copies drift apart.
    """
    skill = skill_chacon_udp.ChaconUdpSkill()
    with _temp_state_dir(), _acked(True):
        assert skill.handle("liga a luz do balcao") == skill_chacon_udp.handle(
            "liga a luz do balcao", "liga a luz do balcao"
        )
    assert skill.handle("que horas sao") == ""


def test_status_is_reachable_from_the_skill_class():
    """`/device_status` looks on the instance as well as on the module."""
    skill = skill_chacon_udp.ChaconUdpSkill()
    with _temp_state_dir():
        assert skill.get_status_for_device("luz do balcao")["state"] == "unknown"


def test_ui_places_the_balcony_light_in_the_sala():
    """Reported: the balcony light had no tile in `/` at all.

    Two separate causes, and fixing only one leaves it invisible: the name
    carries no room word, so it fell through to "Geral" instead of "Sala".
    """
    from skills.skill_ui import handle_request

    page = handle_request()
    assert 'n.includes("balcao") || n.includes("balcão")' in page
    # The icon must stay the existing bulb rule rather than the default ⚡,
    # which is what a generic plug would get.
    assert "n.includes('luz')||n.includes('candeeiro')" in page


def test_get_devices_lists_the_chacon_plug(monkeypatch):
    """The UI builds its tiles from `/get_devices`, which only knew the cloud
    device dicts, so a locally-controlled device had no way to show up.

    Carries a session because that endpoint is no longer public: the service is
    reachable from the internet, and `GET /get_devices` returned the inventory of
    a private home to anyone who asked. This test is about whether the plug is in
    the list, not about who may read it, so it presents the credential the real
    page presents rather than assuming the endpoint is open.
    """
    from unittest.mock import MagicMock

    from src.api import ui_auth
    from src.api.routes import create_app
    from tests.helpers_ui_auth import seeded_store

    seeded_store(monkeypatch)
    app = create_app(pipeline=MagicMock())
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[ui_auth.SESSION_KEY] = "b@t.test"
        sess[ui_auth.ADMIN_SESSION_KEY] = "b@t.test"
    resp = client.get("/get_devices")

    assert resp.status_code == 200
    toggles = resp.get_json()["devices"]["toggles"]
    assert "luz do balcão" in toggles
