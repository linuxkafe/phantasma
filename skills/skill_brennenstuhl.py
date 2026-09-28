import json
import os
import time

import config
from pathlib import Path

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
TRIGGERS = [
    "alarme de incêndio",
    "detetor de fumo",
    "detetor de incêndio",
    "está a arder",
    "há fogo",
    "fumo na casa",
    "estado do alarme",
]

# NOTE: this used to point at /opt/phantasma/tuya_cache.json, a file that does
# not exist -- the cache was silently dead. It is deliberately NOT pointed at
# the tuya skill's cache: that is a different device system, and sharing the
# file would feed it another device's datapoints. Own cache, no host literal.
CACHE_FILE = str(Path(config.CACHE_DIR) / "brennenstuhl_cache.json")


def _get_cached_data(device_name):
    """Lê os dados mais recentes da cache do daemon."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
            return data.get(device_name)
    except Exception as e:
        print(f"ERRO Brennenstuhl: Falha ao ler cache: {e}")
    return None


def handle(user_prompt_lower, user_prompt_full):
    """Verifica o estado do alarme de fumo."""

    # Procura o dispositivo no config.TUYA_DEVICES pelo nome
    target_device_name = None
    if hasattr(config, "TUYA_DEVICES"):
        for name in config.TUYA_DEVICES:
            # Se o nome contiver "incêndio", "fumo" ou "fogo"
            if any(x in name.lower() for x in ["incêndio", "fumo", "fogo", "alarme"]):
                target_device_name = name
                break

    if not target_device_name:
        return "Não encontrei nenhum alarme de incêndio configurado no sistema."

    # Lê da cache (porque o dispositivo dorme)
    cached = _get_cached_data(target_device_name)

    if not cached or "dps" not in cached:
        return f"Não tenho dados recentes do {target_device_name}. Ele pode estar a dormir há muito tempo."

    dps = cached["dps"]
    # DPS Padrão Tuya Smoke: '1' (Estado), '14' (Bateria)
    # Valores comuns DPS 1: "alarm", "normal", "1", "0"

    state = dps.get("1")
    battery = dps.get("14")  # "high", "middle", "low"

    timestamp = cached.get("timestamp", 0)
    time_str = time.strftime("%H:%M", time.localtime(timestamp))

    # Lógica de Resposta
    response = f"De acordo com a leitura das {time_str}, "

    is_safe = str(state).lower() in ["normal", "0", "false", "safe"]

    if is_safe:
        response += "está tudo seguro. Não foi detetado fumo. "
    else:
        response += f"ATENÇÃO! O alarme reporta estado: {state}. VERIFICA A CASA! "

    if battery:
        response += f"A bateria está {battery}."

    return response


# --- API Status (Web UI) ---
def get_status_for_device(nickname):
    """Retorna o estado para o dashboard web."""

    # Usa a cache, tal como a skill Tuya
    cached = _get_cached_data(nickname)

    if not cached or "dps" not in cached:
        return {"state": "unreachable"}

    dps = cached["dps"]
    state = str(dps.get("1", "unknown")).lower()

    # Mapeamento para o Frontend
    # Se state for 'normal' ou '0', está tudo bem.
    is_alarm = state not in ["normal", "0", "false", "safe", "unknown"]

    return {
        "state": "on",
        "smoke_status": "ALARM" if is_alarm else "Normal",
        "is_danger": is_alarm,
    }
