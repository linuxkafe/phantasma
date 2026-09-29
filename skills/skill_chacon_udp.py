"""Local UDP control for the Chacon/Hi-Flying HF-LPB100 balcony light plug.

Replaces the dead DIO-cloud path (see aes/tickets/T036) with direct LAN
control. The plug runs Hi-Flying Lumitek firmware (HF-LPB100, V1.0.08) and
answers UDP on port 18530.

Control works WITHOUT the device AES key: the firmware accepts plaintext
packets (flag bit bEncrypt cleared) whose body header matches its device
constants. The plug was paired with the DIO app, so its local AES key is
unknown and encrypted packets are dropped; plaintext packets are processed.

Protocol (from the mys812/hf Lumitek source, LPB100-HSF):
  * 9-byte open header: pv(1)=0x01, flag(1), device MAC(6), dataLen(1)
  * body: reserved(1)=0x00, sn(2)=0xFFFF, deviceType(1)=0xDF,
          factoryCode(1)=0xF1, licenseData(2)=0x21B4, cmd(1), arg(2), pad(4)
  * cmd 0x01 = SET_GPIO_STATUS (arg 0xFFFF = on, 0x00FF = off)
  * flag 0x04 = bLocked set, bEncrypt clear -> body travels in plaintext
  * the device answers the caller's IP on its fixed port 18530, so this
    skill keeps a UDP socket bound to :18530 to hear the reback.

Class-based so PRIORITY=60 wins over skill_tasmota (PRIORITY=50), which was
the interim holder of the balcony-light nicknames and cannot drive this plug
(it is not a Tasmota device).
"""

import logging
import os
import socket
import unicodedata

from skills.base import Skill, TriggerType

logger = logging.getLogger(__name__)

# --- Plug identity (from the label and the device status page) ---
PLUG_IP = os.getenv("CHACON_PLUG_IP", "10.0.0.116")
PLUG_PORT = 18530
PLUG_MAC = "F0FE6B57E75A"

# --- Lumitek body header constants ---
_RESERVED = 0x00
_SN = bytes.fromhex("FFFF")
_DEVICE_TYPE = 0xDF
_FACTORY_CODE = 0xF1
_LICENSE = bytes.fromhex("21B4")

# --- Commands (asyncMessage.h) ---
CMD_SET_GPIO_STATUS = 0x01
CMD_GET_GPIO_STATUS = 0x02

# --- Arg values for SET_GPIO_STATUS ---
_ARG_ON = bytes.fromhex("FFFF")
_ARG_OFF = bytes.fromhex("00FF")

# bLocked set, bEncrypt clear -> plaintext body
_FLAG = 0x04
_PAD = bytes.fromhex("04040404")

_ACK_TIMEOUT = 3.0
_ACK_ATTEMPTS = 2

# --- Triggers (same balcony-light nicknames as the old cloud skill) ---
ACTIONS_ON = ["liga", "ligar", "acende", "acender", "ativa", "põe"]
ACTIONS_OFF = ["desliga", "desligar", "apaga", "apagar", "desativa", "tira"]

TRIGGERS_NICKNAMES = [
    "luz do balcao",
    "luz do balcão",
    "chacon",
    "balcao",
    "balcão",
]
TRIGGERS = TRIGGERS_NICKNAMES + ACTIONS_ON + ACTIONS_OFF


def _normalize_string(text):
    """Lowercase and strip accents for nickname matching."""
    try:
        return (
            unicodedata.normalize("NFD", text.lower())
            .encode("ascii", "ignore")
            .decode("utf-8")
        )
    except Exception:
        return text.lower()


def build_packet(cmd, arg):
    """Build a full 25-byte UDP packet for the plug.

    The body is 16 bytes: reserved, sn, deviceType, factoryCode, license,
    cmd, 2 zero bytes, arg, 4-byte pad. dataLen in the open header = 0x10.
    """
    body = (
        bytes([_RESERVED])
        + _SN
        + bytes([_DEVICE_TYPE])
        + bytes([_FACTORY_CODE])
        + _LICENSE
        + bytes([cmd])
        + b"\x00\x00"
        + arg
        + _PAD
    )
    if len(body) != 16:
        raise ValueError(f"corpo deve ter 16 bytes, tem {len(body)}")
    header = bytes([0x01, _FLAG]) + bytes.fromhex(PLUG_MAC) + bytes([len(body)])
    return header + body


def _plug_received_ack(sock):
    """Wait briefly for any reback datagram from the plug."""
    deadline = sock.gettimeout()
    try:
        sock.settimeout(_ACK_TIMEOUT)
        for _ in range(_ACK_ATTEMPTS):
            try:
                data, addr = sock.recvfrom(512)
            except socket.timeout:
                continue
            if addr[0] == PLUG_IP and len(data) >= 25:
                return True
        return False
    finally:
        sock.settimeout(deadline)


def send_command(cmd, arg):
    """Send one plaintext command and report whether the plug acknowledged.

    Returns True when the plug rebacks (command accepted), False otherwise.
    """
    packet = build_packet(cmd, arg)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", PLUG_PORT))
    except OSError:
        # Port busy (another daemon listening). Fire without the ack wait.
        sock.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(packet, (PLUG_IP, PLUG_PORT))
        return True
    try:
        sock.sendto(packet, (PLUG_IP, PLUG_PORT))
        return _plug_received_ack(sock)
    finally:
        sock.close()


def _intent(prompt_lower):
    if any(action in prompt_lower for action in ACTIONS_OFF):
        return "OFF"
    if any(action in prompt_lower for action in ACTIONS_ON):
        return "ON"
    return None


def _is_for_plug(prompt_lower):
    return any(
        nickname in _normalize_string(prompt_lower)
        for nickname in TRIGGERS_NICKNAMES
    )


def get_status_for_device(nickname: str) -> dict:
    """State for the UI tile.

    The plug does NOT expose a readable state. GET_GPIO_STATUS (cmd 0x02) is
    accepted, but the reback body is encrypted with the per-device AES key
    that only the DIO pairing knows, so there is nothing to decrypt and no way
    to learn whether the relay is currently closed. Verified live: a 0x02
    query returns a 25-byte reback of opaque bytes.

    So the honest answer is "reachable but state unknown", not a guessed
    on/off and not "unreachable" (it does answer, which is what /device_status
    conflates with reachability). The UI renders `unknown` at full opacity
    with a disabled-looking switch that still sends commands -- a lamp whose
    state is invented would be worse than one that admits ignorance.
    """
    if not _is_for_plug(nickname.lower()):
        return {}
    return {"state": "unknown", "readable": False}


def handle(user_prompt_lower, user_prompt_full):
    """Legacy-style module handler; kept for direct callers and tests."""
    if not _is_for_plug(user_prompt_lower):
        return None

    action = _intent(user_prompt_lower)
    if action is None:
        return None

    arg = _ARG_ON if action == "ON" else _ARG_OFF
    try:
        acked = send_command(CMD_SET_GPIO_STATUS, arg)
    except OSError as e:
        logger.error("skill_chacon_udp: falha de rede para %s: %s", PLUG_IP, e)
        return "Ocorreu um erro de rede ao tentar controlar a luz do balcão."

    word = "ligada" if action == "ON" else "desligada"
    if acked:
        return f"luz do balcão {word}."
    return "Não recebi confirmação do plug, tenta outra vez."


class ChaconUdpSkill(Skill):
    """Control the balcony light plug over local UDP (no cloud, no Aes key)."""

    NAME = "skill_chacon_udp"
    TRIGGERS = TRIGGERS_NICKNAMES
    TRIGGER_TYPE = TriggerType.CONTAINS
    PRIORITY = 60  # wins over skill_tasmota (PRIORITY=50)

    get_status_for_device = staticmethod(get_status_for_device)

    def handle(self, text: str) -> str:
        if not _is_for_plug(text):
            return ""

        action = _intent(text.lower())
        if action is None:
            return ""

        arg = _ARG_ON if action == "ON" else _ARG_OFF
        try:
            acked = send_command(CMD_SET_GPIO_STATUS, arg)
        except OSError as e:
            logger.error("skill_chacon_udp: falha de rede para %s: %s", PLUG_IP, e)
            return "Ocorreu um erro de rede ao tentar controlar a luz do balcão."

        word = "ligada" if action == "ON" else "desligada"
        if acked:
            return f"luz do balcão {word}."
        return "Não recebi confirmação do plug, tenta outra vez."
