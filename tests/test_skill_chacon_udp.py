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
