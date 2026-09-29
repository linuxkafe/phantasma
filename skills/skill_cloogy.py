import json
import os
import threading
import time

import httpx

import config
from pathlib import Path

# --- Configuração ---
TRIGGER_TYPE = "contains"
BASE_TRIGGERS = ["cloogy", "kiome", "lista", "listar", "consumo", "gastar", "leitura", "quanto"]
# Resolved through config.CACHE_DIR instead of a literal /opt/phantasma path.
# A host path in code cannot be overridden, so these caches had to be forked
# per host. In production the resolved path is byte-identical to the old one.
CACHE_FILE = str(Path(config.CACHE_DIR) / "cloogy_cache.json")

def _get_triggers():
    if hasattr(config, 'CLOOGY_DEVICES') and isinstance(config.CLOOGY_DEVICES, dict):
        return BASE_TRIGGERS + list(config.CLOOGY_DEVICES.keys())
    return BASE_TRIGGERS

TRIGGERS = _get_triggers()

# --- Gestão de Cache ---
def _ensure_permissions():
    if os.path.exists(CACHE_FILE):
        try: os.chmod(CACHE_FILE, 0o666)
        except Exception: pass

def _load_cache():
    if not os.path.exists(CACHE_FILE): return {}
    try:
        with open(CACHE_FILE, 'r') as f: return json.load(f)
    except Exception: return {}

def _update_single_value(device_id, watts, extra=None):
    if watts is None: return
    try:
        data = _load_cache()
        entry = data.get(str(device_id), {})
        # Keep a short history so a frozen reading is detectable: one sample
        # cannot show that a value is stuck, four identical ones can.
        history = (entry.get("history") or [])[-7:]
        history.append(watts)
        entry = {"val": watts, "ts": time.time(), "history": history}
        entry.update(extra or {})
        data[str(device_id)] = entry
        with open(CACHE_FILE, 'w') as f: json.dump(data, f)
        _ensure_permissions()
    except Exception: pass

# --- API ---
CURRENT_TOKEN = None
_TAG_CATALOG = None
def _get_headers(): return {"Authorization": f"VPS {CURRENT_TOKEN}", "Accept": "application/json"}

def _login():
    global CURRENT_TOKEN
    user = getattr(config, 'CLOOGY_USERNAME', None); pwd = getattr(config, 'CLOOGY_PASSWORD', None)
    if not user or not pwd: return False
    try:
        resp = httpx.post("https://api.cloogy.com/api/1.4/sessions", json={"Login": user, "Password": pwd}, headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=15, verify=False)
        if resp.status_code in [200, 201]: CURRENT_TOKEN = resp.json().get("Token"); return True
    except Exception: pass
    return False

def _ensure_auth(): return _login() if not CURRENT_TOKEN else True

def _fetch_reading(device_id):
    """Instantaneous power in W, or None."""
    d = _fetch_readings(device_id)
    return d["power_w"] if d else None

def _fetch_readings(device_id):
    """All the instant readings Cloogy exposes for one device, in native units.

    The endpoint (/consumptions/instant) returns 12 fields; the skill used to
    read only `Read` (kW -> W) and threw the rest away. What is actually
    useful is: instantaneous power (W), accumulated energy (kWh), money spent
    (EUR) and carbon (g CO2) -- so a user asking "quanto gastei?" gets an
    answer from a real field instead of an inference. Returns None on any
    failure so callers keep the old all-or-nothing behaviour.
    """
    if not _ensure_auth(): return None
    try:
        now = int(time.time() * 1000); start = now - (60 * 60 * 1000)
        url = "https://api.cloogy.com/api/1.4/consumptions/instant"
        params = {"from": start, "to": now, "tags": f"[{device_id}]", "includeForecast": "False"}
        resp = httpx.get(url, params=params, headers=_get_headers(), timeout=20, verify=False)
        if resp.status_code == 401:
            if _login(): resp = httpx.get(url, params=params, headers=_get_headers(), timeout=20, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, list) and len(data) > 0:
                last = data[-1]
                if last.get("Read") is not None:
                    # The whole window, not just the last point: a plug stuck
                    # on one value is only visible across samples. The oven
                    # plug reports 1.0 kW for every sample in the window.
                    series = [d["Read"] for d in data if d.get("Read") is not None]
                    return {
                        "power_w": float(last["Read"]) * 1000,
                        "consumption_kwh": last.get("Consumption"),
                        "currency": last.get("ReadCurrency"),
                        "currency_symbol": last.get("CurrencySymbol"),
                        "carbon_g": last.get("ReadCarbon"),
                        "granularity": last.get("Granularity"),
                        "series": series,
                        "tag_name": _tag_catalog().get(str(device_id), {}).get("name", ""),
                        "tag_kind": _tag_kind(device_id),
                    }
    except Exception: pass
    return None

# --- Daemon Interno ---
def _poll_loop():
    while True:
        try:
            if hasattr(config, 'CLOOGY_DEVICES'):
                for name, dev_id in config.CLOOGY_DEVICES.items():
                    d = _fetch_readings(dev_id)
                    if d is not None:
                        _update_single_value(dev_id, d["power_w"], extra={
                            k: v for k, v in d.items() if k != "power_w"
                        })
        except Exception: pass
        time.sleep(60)

def init_skill_daemon():
    print("[Cloogy Daemon] A iniciar polling em background...")
    threading.Thread(target=_poll_loop, daemon=True).start()

# --- Helpers de Identificação ---
def _find_id_by_name(nickname_lower):
    if not hasattr(config, 'CLOOGY_DEVICES'): return None
    for name, dev_id in config.CLOOGY_DEVICES.items():
        if name.lower() == nickname_lower: return str(dev_id)
    if nickname_lower in ['casa', 'geral', 'total']:
        for name, dev_id in config.CLOOGY_DEVICES.items():
            if name.lower() in ['casa', 'geral', 'total', 'main']: return str(dev_id)
    return None

# --- Interface Web UI ---

# A smart plug with something plugged in draws power; one that is "on" but idle
# (or off) draws ~0W. Cloogy exposes no discrete on/off state, so the switch
# position has to be inferred from the measurement -- and 3W of standby is a
# real reading on these devices, not zero. Below this the plug is treated as
# off; above it, on. It is a heuristic and it is documented as one, because the
# alternative this code used to have -- always reporting "on" whenever a
# reading existed -- was not a heuristic, it was a lie the UI rendered.
ON_THRESHOLD_W = 5.0

# A frozen reading must not be turned into a switch position. Verified on the
# oven plug (TagId 169809): 96 consecutive 15-min samples over 24h, every one
# exactly 1.0 kW, population stddev 0.0000 -- physically impossible for a real
# socket. Treating that as "the oven is on" is a lie with a plausible number
# attached. So: if a device has been reporting a bit-identical value for
# STUCK_SAMPLES consecutive polls, the state is reported as unknown and the
# reading is labelled as suspect, rather than believed.
STUCK_SAMPLES = 4
STUCK_EPSILON = 0.0001


def _is_stuck(entry):
    """True when the last readings are all the same to within epsilon."""
    history = entry.get("history") or []
    if len(history) < STUCK_SAMPLES:
        return False
    recent = history[-STUCK_SAMPLES:]
    return (max(recent) - min(recent)) < STUCK_EPSILON


def _stuck_for(history):
    """True when a list of samples holds one unchanging value.

    Used for the live read as well as the cache: the question is whether the
    API itself keeps repeating the same number, which is what the oven plug
    does, not whether our last write happened to equal this one.
    """
    if len(history) < STUCK_SAMPLES:
        return False
    recent = history[-STUCK_SAMPLES:]
    return (max(recent) - min(recent)) < STUCK_EPSILON


def get_status_for_device(nickname):
    target_id = _find_id_by_name(nickname.lower())
    if not target_id: return {"state": "unreachable"}

    cache = _load_cache()
    if target_id in cache:
        entry = cache[target_id]
        watts = entry["val"]
        out = {"power_w": round(watts, 1)}
        kind = entry.get("tag_kind") or _tag_kind(target_id)

        if kind == "actuator":
            # An actuator tag holds a switch state, not a measurement. This tag
            # (169809) returned a rock-steady 1.0, which the on/off threshold
            # read as "the oven is drawing 1000 W" -- a switch position dressed
            # up as a wattage. Say what the tag is instead.
            out["state"] = "unknown"
            out["state_inferred"] = True
            out["tag_kind"] = "actuator"
            out["note"] = "esta etiqueta e um atuador (on/off), nao uma potencia"
        elif _is_stuck(entry):
            # A reading that has not moved in an hour is not a measurement.
            out["state"] = "unknown"
            out["state_inferred"] = True
            out["reading_suspect"] = True
            out["note"] = "leitura parada: o valor nao muda, estado desconhecido"
        else:
            out["state"] = "on" if watts > ON_THRESHOLD_W else "off"
            out["state_inferred"] = True

        if entry.get("tag_name"):
            out["tag_name"] = entry["tag_name"]
        out.update({k: v for k, v in entry.items() if k not in ("val", "ts", "history")})
        return out

    return {"state": "unreachable"}

# --- Interface Voz ---
def _set_state(device_id, state_on):
    if not _ensure_auth(): return False
    try:
        val = "1" if state_on else "0"
        url = f"https://api.cloogy.com/api/1.4/tag/{device_id}"
        resp = httpx.put(url, json={"Value": val}, headers=_get_headers(), timeout=10, verify=False)
        return resp.status_code in [200, 204]
    except Exception: return False

def _tag_catalog(token=None):
    """Every tag in the account: id -> metadata (name, device, unit).

    The tag list is the only place that says WHAT a tag measures. A tag id in
    CLOOGY_DEVICES is opaque, and on this install the same kind of number means
    different things: 169806 is "Active Power+" of the oven plug, and 169809 is
    its "Actuator". Reading an Actuator as watts produced a rock-steady 1.0 kW
    that looked like a plausible measurement and was not one at all.

    Cached because it changes only when the owner re-pairs a device.
    """
    global _TAG_CATALOG
    if _TAG_CATALOG is not None:
        return _TAG_CATALOG
    _TAG_CATALOG = {}
    if not _ensure_auth():
        return _TAG_CATALOG
    try:
        r = httpx.get("https://api.cloogy.com/api/1.4/tags", headers=_get_headers(), timeout=15, verify=False)
        if r.status_code == 200:
            for t in r.json().get("List", []) or []:
                _TAG_CATALOG[str(t.get("Id"))] = {
                    "name": t.get("Name") or "",
                    "device_id": t.get("DeviceId"),
                    "communicate": t.get("Communicate"),
                }
    except Exception:
        pass
    return _TAG_CATALOG


def _tag_kind(device_id):
    """'actuator' | 'power' | 'energy' | '' for a tag id."""
    meta = _tag_catalog().get(str(device_id))
    if not meta:
        return ""
    name = (meta.get("name") or "").lower()
    if "actuator" in name:
        return "actuator"
    if "power" in name:
        return "power"
    if "energy" in name:
        return "energy"
    return ""


def _describe(name, d):
    """Answer from a reading, naming only the fields that actually have values.

    The API always returns the same 12 keys, most of them zero on any given
    plug, so a canned sentence would quote empty numbers. Each part is added
    only when Cloogy reported something for it.
    """
    bits = [f"{int(round(d['power_w']))} Watts"]
    if d.get("consumption_kwh") is not None:
        bits.append(f"{d['consumption_kwh']:.2f} kWh acumulados")
    if d.get("currency"):
        sym = d.get("currency_symbol") or ""
        bits.append(f"{sym}{d['currency']:.2f}".strip())
    if d.get("carbon_g"):
        bits.append(f"{d['carbon_g']:.0f} g de CO2")
    gran = d.get("granularity")
    if gran == "instant":
        bits.append("media dos ultimos 15 minutos")
    return f"O {name} esta a {', '.join(bits)}."

def _describe_stuck(name):
    """A reading that never moves is a broken sensor, not a measurement.

    Announcing "1000 Watts" from a value the plug has been repeating
    unchanged for days would be reporting a defect as a fact.
    """
    return (
        f"A leitura do {name} esta parada no mesmo valor, portanto nao consigo "
        f"dizer o estado. Pode ser a tomada ou a telemetria do Cloogy."
    )

def handle(user_prompt_lower, user_prompt_full):
    if not hasattr(config, 'CLOOGY_DEVICES'): return None

    target_id = None; target_name = ""
    for name, dev_id in config.CLOOGY_DEVICES.items():
        if name.lower() in user_prompt_lower: target_id = dev_id; target_name = name; break

    if not target_id and any(x in user_prompt_lower for x in ["casa", "geral", "total"]):
         tid_str = _find_id_by_name("casa")
         if tid_str: target_id = tid_str; target_name = "casa"

    if not target_id: return None

    # 1. Leitura de consumo
    if any(x in user_prompt_lower for x in ["quanto", "consumo", "leitura", "gastar"]):
        d = _fetch_readings(target_id)
        if d is None:
            # A leitura viva falhou: o cache ainda e melhor do que nada, mas
            # tem de ser rotulado como cacheado para nao parecer(actual).
            cache = _load_cache()
            entry = cache.get(str(target_id))
            if entry is None:
                return f"Não consegui ler o sensor {target_name}."
            d = {"power_w": entry["val"], **{k: v for k, v in entry.items() if k not in ("val", "ts")}}
            return f"O {target_name} marcava {int(round(entry['val']))} Watts na ultima leitura guardada."

        _update_single_value(target_id, d["power_w"], extra={
            k: v for k, v in d.items() if k not in ("power_w", "series")
        })
        if d.get("tag_kind") == "actuator":
            return (
                f"O {target_name} esta ligado a um atuador, nao a um medidor de "
                f"consumo, por isso nao tenho leituras para te dar."
            )
        if _stuck_for(d.get("series") or []):
            return _describe_stuck(target_name)
        return _describe(target_name, d)

    # 2. Controlo (Ligar / Desligar)
    is_on = any(x in user_prompt_lower for x in ["liga", "acende"])
    is_off = any(x in user_prompt_lower for x in ["desliga", "apaga"])

    # FIX: Prioridade ao DESLIGAR para evitar conflito de string ("desliga" contém "liga")
    if is_off:
        return "Ok." if _set_state(target_id, False) else "Erro."
    elif is_on:
        return "Ok." if _set_state(target_id, True) else "Erro."

    return None
