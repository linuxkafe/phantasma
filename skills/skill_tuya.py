import config
import time
import re
import json
import os

try:
    import tinytuya
except ImportError:
    print("AVISO: Biblioteca 'tinytuya' não encontrada. A skill_tuya será desativada.")
    pass

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
ACTIONS_ON = ["liga", "ligar", "acende", "acender"]
ACTIONS_OFF = ["desliga", "desligar", "apaga", "apagar"]
STATUS_TRIGGERS = ["como está", "estado", "temperatura", "humidade", "nível", "leitura"]
DEBUG_TRIGGERS = ["diagnostico", "dps"]
BASE_NOUNS = [
    "sensor", "luz", "lâmpada", "desumidificador", 
    "exaustor", "tomada", "ficha", 
    "quarto", "sala", "wc" 
]
VERSIONS_TO_TRY = [3.3, 3.1, 3.2, 3.4, 3.5]
CACHE_FILE = "tuya_cache.json" # Ficheiro gerado pelo daemon

def _get_tuya_triggers():
    all_actions = ACTIONS_ON + ACTIONS_OFF + STATUS_TRIGGERS + DEBUG_TRIGGERS
    if hasattr(config, 'TUYA_DEVICES') and isinstance(config.TUYA_DEVICES, dict):
        device_nicknames = list(config.TUYA_DEVICES.keys())
        return BASE_NOUNS + device_nicknames + all_actions
    return BASE_NOUNS + all_actions

TRIGGERS = _get_tuya_triggers()

# --- Helper: Ler Cache Local ---
def _get_cached_status(nickname):
    """ Tenta ler o estado do ficheiro JSON se a conexão direta falhar. """
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r') as f:
            data = json.load(f)
            device_data = data.get(nickname)
            if device_data and 'dps' in device_data:
                # Podemos adicionar lógica aqui para verificar se o timestamp é muito antigo
                return device_data
    except Exception as e:
        print(f"Erro ao ler cache Tuya: {e}")
    return None

def _try_connect_with_versioning(dev_id, dev_ip, dev_key):
    if 'x' in dev_ip.lower():
        return (None, f"IP inválido ('{dev_ip}')", None)
        
    print(f"Skill_Tuya: A tentar ligar a {dev_id} @ {dev_ip}...")
    
    for version in VERSIONS_TO_TRY:
        try:
            d = tinytuya.Device(dev_id, dev_ip, dev_key)
            d.set_socketTimeout(2)
            d.set_version(version)
            status = d.status()
            if 'dps' in status:
                return (d, status, None)
            else:
                if status.get('Err') == '905': break 
        except:
            break 
            
    return (None, None, None)

# --- Lógica Principal ---
def handle(user_prompt_lower, user_prompt_full):
    if "tinytuya" not in globals(): return "Falta biblioteca tinytuya."
    if not hasattr(config, 'TUYA_DEVICES'): return None

    final_action = None
    if any(a in user_prompt_lower for a in ACTIONS_OFF): final_action = "OFF"
    elif any(a in user_prompt_lower for a in ACTIONS_ON): final_action = "ON"
    elif any(a in user_prompt_lower for a in DEBUG_TRIGGERS): final_action = "DEBUG"
    elif any(a in user_prompt_lower for a in STATUS_TRIGGERS): final_action = "STATUS"
    
    if not final_action: return None

    matched_devices = []
    # Matching (cópia da lógica corrigida anterior)
    direct_matches = []
    for nickname, details in config.TUYA_DEVICES.items():
        if nickname.lower() in user_prompt_lower:
            direct_matches.append((nickname, details))
    if direct_matches:
        direct_matches.sort(key=lambda x: len(x[0]), reverse=True)
        matched_devices = [direct_matches[0]]
    else:
        nouns_in_prompt = [noun for noun in BASE_NOUNS if noun in user_prompt_lower]
        if nouns_in_prompt:
            for nickname, details in config.TUYA_DEVICES.items():
                if any(noun in nickname.lower() for noun in nouns_in_prompt):
                    matched_devices.append((nickname, details))

    if not matched_devices: return None 
    
    nickname, details = matched_devices[0]

    if final_action == "DEBUG":
        return _handle_debug_status(nickname, details)

    if final_action in ["ON", "OFF"]:
        # (Código de Switch mantém-se, ignorar sensores)
        if "sensor" in nickname.lower(): return None
        dps_index = 20 if "luz" in nickname.lower() or "lâmpada" in nickname.lower() else 1
        try:
            _handle_switch(nickname, details, final_action, dps_index)
            return f"{nickname.capitalize()} {'ligado' if final_action=='ON' else 'desligado'}."
        except Exception as e:
            return f"Erro no {nickname}: {e}"

    if final_action == "STATUS":
        return _handle_sensor(nickname, details, user_prompt_lower)

    return None

# --- Processadores ---

def _handle_debug_status(nickname, details):
    print(f"*** DIAGNÓSTICO {nickname.upper()} ***")
    (d, result, err) = _try_connect_with_versioning(details['id'], details['ip'], details['key'])
    
    if d: return f"Diagnóstico Online: {result}"
    
    # Se falhar, tentar cache
    cached = _get_cached_status(nickname)
    if cached:
        ts = time.strftime('%H:%M', time.localtime(cached['timestamp']))
        return f"Dispositivo offline, mas tenho dados em cache das {ts}: {cached['dps']}"
        
    return f"Diagnóstico: Falha total. Dispositivo offline e sem cache."

def _handle_switch(nickname, details, action, dps_index):
    (d, status, err) = _try_connect_with_versioning(details['id'], details['ip'], details['key'])
    if not d: raise Exception("incontactável")
    d.set_value(dps_index, True if action == "ON" else False, nowait=True)

def _handle_sensor(nickname, details, prompt):
    """ Tenta ler direto; se falhar, usa a cache. """
    
    # 1. Tenta conexão direta (vai falhar se estiver a dormir)
    (d, data, err) = _try_connect_with_versioning(details['id'], details['ip'], details['key'])
    
    origin_msg = ""
    dps = {}

    if d and 'dps' in data:
        dps = data['dps']
    else:
        # 2. Fallback para Cache
        cached = _get_cached_status(nickname)
        if cached and 'dps' in cached:
            dps = cached['dps']
            ts = time.strftime('%H:%M', time.localtime(cached['timestamp']))
            origin_msg = f" (última leitura às {ts})"
        else:
            return f"O {nickname} está a dormir e não tenho dados guardados."

    # Processar DPS (igual à lógica anterior)
    temp_raw = dps.get('1') or dps.get('102') 
    humid_raw = dps.get('2') or dps.get('103')
    
    response_parts = []
    wants_all = "como está" in prompt or "leitura" in prompt or "estado" in prompt
    
    if temp_raw is not None and ("temperatura" in prompt or wants_all):
        val = float(temp_raw)
        temp = val / 10.0 if val > 100 else val
        response_parts.append(f"a temperatura é {temp} graus")
        
    if humid_raw is not None and ("humidade" in prompt or wants_all):
        response_parts.append(f"a humidade é {int(humid_raw)} por cento")

    if not response_parts:
        return f"Tenho dados do {nickname}{origin_msg}, mas não percebo os valores: {dps}"
        
    return f"No {nickname}{origin_msg}, " + " e ".join(response_parts) + "."

# --- API Status ---

def get_status_for_device(nickname):
    """
    Função pública chamada pelo assistant.py para obter o estado de um dispositivo.
    Suporta: Sensores (Temp/Hum), Tomadas de Energia (Watts) e Interruptores Simples.
    """
    if not hasattr(config, 'TUYA_DEVICES') or nickname not in config.TUYA_DEVICES:
        return {"state": "unreachable"}

    details = config.TUYA_DEVICES[nickname]

    # 1. Tenta Conexão Direta
    (d, status, err) = _try_connect_with_versioning(details['id'], details['ip'], details['key'])

    dps = {}

    # 2. Se falhar (comum em sensores ou leituras rápidas), tenta cache
    if not d:
        cached = _get_cached_status(nickname)
        if cached:
            dps = cached['dps']
            d = True # Temos dados

    if not d: return {"state": "unreachable"}

    # Se ligou direto, usa os dados frescos, senão usa a cache
    if not dps and 'dps' in status: dps = status['dps']

    # --- CASO 1: SENSORES DE TEMP/HUMIDADE ---
    if "sensor" in nickname.lower():
        temp_raw = dps.get('1') or dps.get('102')
        humid_raw = dps.get('2') or dps.get('103')
        result = {"state": "on"}
        if temp_raw is not None:
            val = float(temp_raw)
            result["temperature"] = val / 10.0 if val > 100 else val
        if humid_raw is not None:
            result["humidity"] = int(humid_raw)
        return result

    # --- CASO 2: TOMADAS COM MONITORIZAÇÃO (Desumidificadores) ---
    # Como indicaste, são tomadas. Vamos ler o DPS 19 (Potência).
    if "desumidificador" in nickname.lower():
        # DPS 1: Estado do Relé (Ligado/Desligado)
        is_on = dps.get('1')

        # DPS 19: Potência em W (Escala 10x -> 1944 = 194.4W)
        power_raw = dps.get('19')
        power_w = 0.0

        if power_raw is not None:
             power_w = float(power_raw) / 10.0

        result = {
            "state": "on" if is_on else "off",
            "power_w": power_w
        }
        return result

    # --- CASO 3: INTERRUPTORES GENÉRICOS ---
    dps_index_str = "20" if "luz" in nickname.lower() or "lâmpada" in nickname.lower() else "1"
    current_state = dps.get(dps_index_str)

    if current_state is True: return {"state": "on"}
    elif current_state is False: return {"state": "off"}

    return {"state": "unreachable"}
