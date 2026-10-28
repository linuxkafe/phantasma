"""skill_ohmigo — find cheapest EV charging via the Ohmigo MCP server.

Triggers on charging-related keywords and queries the Ohmigo MCP
(https://ohmigo.ocarroeletrico.com/mcp) for the cheapest public charger
near a place, for the owner's car (Peugeot iOn / Citroën C-Zero / Mitsubishi
i-MiEV family).

The car parameters are hardcoded for the iOn (14.5 kWh usable, 3.7 kW AC,
~25 kW DC, ~30 min DC 10-80%). Override via environment if needed.
"""

import json
import re
import unicodedata

import httpx

import config

PRIORITY = 100

TRIGGER_TYPE = "contains"
TRIGGERS = [
    "carregar o carro",
    "carregar carro",
    "onde carregar",
    "ohmigo",
    "bateria do carro",
    "mais barato carregar",
]

MCP_URL = "https://ohmigo.ocarroeletrico.com/mcp"
MCP_TIMEOUT = 90.0

# Peugeot iOn / Citroën C-Zero / Mitsubishi i-MiEV (hardcoded; override via env)
CAR_BATTERY_KWH = float(getattr(config, "OHMIGO_BATTERY_KWH", 14.5))
CAR_AC_KW = float(getattr(config, "OHMIGO_AC_KW", 3.7))
CAR_DC_KW = float(getattr(config, "OHMIGO_DC_KW", 25.0))
CAR_DC_10_80_MIN = float(getattr(config, "OHMIGO_DC_10_80_MIN", 30.0))
DEFAULT_PLACE = getattr(config, "OHMIGO_DEFAULT_PLACE", "Leiria")
DEFAULT_START_PCT = float(getattr(config, "OHMIGO_START_PCT", 20.0))
DEFAULT_TARGET_PCT = float(getattr(config, "OHMIGO_TARGET_PCT", 80.0))


def _normalize(text):
    try:
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    except Exception:
        return text.lower().strip()


def _extract_place(text):
    """Best-effort place extraction: 'em X', 'perto de X', 'para X'."""
    t = _normalize(text)
    patterns = (
        r"\bem\s+(?:o\s+|a\s+)?([a-z]+(?:\s+[a-z]+)?)",
        r"\bno\s+([a-z]+(?:\s+[a-z]+)?)",
        r"\bna\s+([a-z]+(?:\s+[a-z]+)?)",
        r"\bperto de\s+([a-z]+(?:\s+[a-z]+)?)",
        r"\bpara\s+([a-z]+(?:\s+[a-z]+)?)",
    )
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            place = m.group(1).strip().title()
            # drop trailing filler
            for stop in ("carregar", "o", "a", "um", "uma"):
                if place.lower().endswith(" " + stop):
                    place = place[: -(len(stop) + 1)].strip()
            if place and len(place) >= 3:
                return place
    return None


def _mcp_call(tool_name, arguments):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    try:
        resp = httpx.post(MCP_URL, json=payload, headers=headers, timeout=MCP_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        return f"Erro ao contactar o serviço de carregamento: {e}"
    # Parse SSE
    for line in resp.text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
            except Exception:
                continue
            if "result" in data:
                result = data["result"]
                # content is a list of {type,text}
                content = result.get("content", []) if isinstance(result, dict) else []
                texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                return "\n".join(t for t in texts if t).strip()
            if "error" in data:
                err = data["error"]
                return f"Erro do serviço: {err.get('message', err)}"
    return "Sem resposta do serviço de carregamento."


def handle(user_prompt_lower, user_prompt_full):
    place = _extract_place(user_prompt_full) or DEFAULT_PLACE

    args = {
        "place": place,
        "battery_kwh": CAR_BATTERY_KWH,
        "start_pct": DEFAULT_START_PCT,
        "target_pct": DEFAULT_TARGET_PCT,
        "car_ac_kw": CAR_AC_KW,
        "car_dc_kw": CAR_DC_KW,
        "dc_10_80_minutes": CAR_DC_10_80_MIN,
        "lang": "pt",
    }

    result = _mcp_call("find_cheapest_charging", args)
    if not result:
        return None

    # Truncate to the top entries to keep the prompt within context limits.
    # Each entry can be long, so cap both the count and the per-line length.
    lines = result.splitlines()
    lines = [ln[:200] for ln in lines]
    if len(lines) > 5:
        lines = lines[:5] + ["... (mais resultados omitidos)"]
    result = "\n".join(lines)

    # If the user asked "onde" / "qual o mais barato", prepend a short header.
    header = f"Carregamento mais barato perto de {place} (iOn, {DEFAULT_START_PCT:.0f}%→{DEFAULT_TARGET_PCT:.0f}%):\n"
    return header + result
