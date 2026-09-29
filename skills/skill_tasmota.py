"""Tasmota skill — controlo local de tomadas/relays Chacon flashados (T053).

Substitui o caminho cloud da skill_chacon (conta DIO morta) por controlo HTTP
directo a um plug com Tasmota: ``http://<ip>/cm?cmnd=Power ON``.

Class-based de propósito: o loader instancia a subclasse e a PRIORITY=50 vence
as skills legacy (priority 0) com triggers sobrepostos (chacon, tuya) sem
alterar nenhuma delas.

Não importa ``config`` de propósito: lê ``TASMOTA_DEVICES_JSON`` do ambiente
para não tocar em config.py (tem alterações uncommitted de outra sessão).
"""

import httpx
import json
import logging
import os
import re
import unicodedata
from typing import Any, Dict, Optional

from skills.base import Skill, SkillContext, TriggerType

logger = logging.getLogger(__name__)

# --- Configuração da Skill ---
PRIORITY = 50
NAME = "tasmota"

# Alcunhas dos dispositivos Tasmota (apenas nomes, não acções): evita triggers
# curtos tipo "liga" roubarem frases não relacionadas.
TRIGGERS = [
    "luz do balcao",
    "luz do balcão",
    "balcao",
    "balcão",
    "tasmota",
    "chacon",
]

STATUS_WORDS = [
    "estado",
    "como esta",
    "ta ligada",
    "esta ligada",
    "esta acesa",
    "esta desligada",
    "ta desligada",
]

OFF_WORDS = [
    "desliga",
    "desligar",
    "desligada",
    "desligado",
    "desativa",
    "desativar",
    "apaga",
    "apagar",
    "apagada",
    "apagado",
    "tira",
    "tirar",
    "desligue",
    "off",
]

ON_WORDS = [
    "liga",
    "ligar",
    "ligada",
    "ligado",
    "ativa",
    "ativar",
    "ativo",
    "acende",
    "acender",
    "acesa",
    "aceso",
    "poe",
    "põe",
    "põem",
    "on",
]

TOGGLE_WORDS = ["alterna", "comuta", "inverte", "toggle"]

_TIMEOUT = 5.0


def _normalize(text: str) -> str:
    """Lowercase + strip accents (NFD→ASCII) para matching robusto."""
    try:
        return (
            unicodedata.normalize("NFD", text.lower())
            .encode("ascii", "ignore")
            .decode("utf-8")
        )
    except Exception:
        return text.lower()


def _load_devices() -> Dict[str, str]:
    """Lê TASMOTA_DEVICES_JSON ({"nome": "ip-ou-url"}). Nunca falha a import."""
    raw = os.getenv("TASMOTA_DEVICES_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception as e:
        logger.error("skill_tasmota: TASMOTA_DEVICES_JSON inválido: %s", e)
        return {}
    devices: Dict[str, str] = {}
    for name, host in data.items():
        if isinstance(name, str) and isinstance(host, str):
            devices[name] = host
    return devices


def _base_url(host: str) -> str:
    return host if host.startswith(("http://", "https://")) else f"http://{host}"


def _query_power(host: str, cmnd: str) -> Optional[str]:
    """Envia um comando Tasmota; devolve o field POWER (ON/OFF) ou None."""
    url = _base_url(host)
    try:
        resp = httpx.get(url, params={"cmnd": cmnd}, timeout=_TIMEOUT)
        if resp.status_code < 200 or resp.status_code >= 300:
            logger.error(
                "skill_tasmota: %s (%s) respondeu HTTP %s",
                host,
                cmnd,
                resp.status_code,
            )
            return None
        data = resp.json()
    except Exception as e:
        logger.error("skill_tasmota falhou em %s (%s): %s", host, cmnd, e)
        return None
    power = data.get("POWER")
    if isinstance(power, str):
        return power.upper()
    return None


class TasmotaSkill(Skill):
    """Controla tomadas/relays com Tasmota via HTTP local."""

    TRIGGERS = TRIGGERS
    TRIGGER_TYPE = TriggerType.CONTAINS
    PRIORITY = PRIORITY
    NAME = NAME

    def matches(self, text: str) -> bool:
        ntext = _normalize(text)
        for trig in self.TRIGGERS:
            if not trig:
                continue
            if re.search(r"\b" + re.escape(_normalize(trig)) + r"\b", ntext):
                return True
        return False

    def handle(self, text: str) -> Optional[str]:
        if not self.matches(text):
            return None

        devices = _load_devices()
        if not devices:
            return "As tomadas Tasmota não estão configuradas."

        ntext = _normalize(text)
        device = self._find_device(devices, ntext)
        if device is None:
            return None

        host = devices[device]

        if any(w in ntext for w in STATUS_WORDS) or ntext.rstrip().endswith("?"):
            return self._status(device, host)

        if any(w in ntext for w in OFF_WORDS):
            return self._command(device, host, "OFF")
        if any(w in ntext for w in TOGGLE_WORDS):
            return self._command(device, host, "TOGGLE")
        if any(w in ntext for w in ON_WORDS):
            return self._command(device, host, "ON")
        return None

    def _find_device(self, devices: Dict[str, str], ntext: str) -> Optional[str]:
        for name in devices:
            if _normalize(name) in ntext:
                return name
        for trig in self.TRIGGERS:
            if not trig:
                continue
            if re.search(r"\b" + re.escape(_normalize(trig)) + r"\b", ntext):
                if len(devices) == 1:
                    return next(iter(devices))
                return None
        return None

    def _command(self, device: str, host: str, state: str) -> str:
        power = _query_power(host, f"Power {state}")
        if power is None:
            return f"Não consegui contactar a tomada do {device}."
        word = "ligada" if power == "ON" else "desligada"
        return f"{device} {word}."

    def _status(self, device: str, host: str) -> str:
        power = _query_power(host, "Power")
        if power is None:
            return f"Não consegui contactar a tomada do {device}."
        word = "ligada" if power == "ON" else "desligada"
        return f"A tomada do {device} está {word}."

    def get_status_for_device(self, nickname: str) -> Dict[str, Any]:
        """Para o dashboard (/device_status). state on/off ou unreachable."""
        devices = _load_devices()
        if not devices:
            return {"state": "unreachable"}
        nnick = _normalize(nickname)
        name = self._find_device(devices, nnick) or (
            nnick if nnick in devices else None
        )
        if name is None:
            return {"state": "unreachable"}
        power = _query_power(devices[name], "Power")
        if power is None:
            return {"state": "unreachable"}
        return {"state": "on" if power == "ON" else "off", "device": name}