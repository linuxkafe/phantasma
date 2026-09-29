"""Tests for skill_chacon_udp.

The old skill_chacon reached the Chacon/DIO cloud, whose account is dead (see
aes/tickets/T036). skill_chacon_udp controls the same plug directly over LAN
UDP using the plaintext (bEncrypt=0) path of the Hi-Flying Lumitek firmware,
so it needs no vendor account and no AES key.

These tests do NOT contact the plug. The wires-on-the-table bytes below are
the exact packets validated live against 10.0.0.116:18530 on 2026-09-29.
"""

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


def test_status_admits_ignorance_instead_of_guessing():
    """The plug cannot be read back, so the tile must say so.

    GET_GPIO_STATUS is accepted by the device but the reback body is encrypted
    with the per-device AES key, so there is no state to report. Returning
    "unreachable" would be wrong too -- the device does answer. A fabricated
    on/off is the one answer that is definitely a lie, so it is not offered.
    """
    status = skill_chacon_udp.get_status_for_device("luz do balcão")
    assert status["state"] == "unknown"
    assert status["readable"] is False
    assert skill_chacon_udp.get_status_for_device("desumidificador") == {}


def test_status_is_reachable_from_the_skill_class():
    """`/device_status` looks on the instance as well as on the module."""
    skill = skill_chacon_udp.ChaconUdpSkill()
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


def test_get_devices_lists_the_chacon_plug():
    """The UI builds its tiles from `/get_devices`, which only knew the cloud
    device dicts, so a locally-controlled device had no way to show up."""
    from unittest.mock import MagicMock

    from src.api.routes import create_app

    app = create_app(pipeline=MagicMock())
    app.config["TESTING"] = True
    resp = app.test_client().get("/get_devices")

    assert resp.status_code == 200
    toggles = resp.get_json()["devices"]["toggles"]
    assert "luz do balcão" in toggles
