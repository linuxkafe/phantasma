import config
import time
import re
import json
import os
import socket # NECESSÁRIO para o daemon
import sys # NECESSÁRIO para o daemon
import threading # NECESSÁRIO para o daemon

try:
    import tinytuya
    # Importar OutletDevice para o polling do daemon (mais robusto)
    from tinytuya import OutletDevice, Device 
except ImportError:
    print("AVISO: Biblioteca 'tinytuya' não encontrada. A skill_tuya será desativada.")
    class Device: pass
    OutletDevice = Device

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

# --- CONSTANTES DO DAEMON (Integradas) ---
# Usamos o caminho absoluto do daemon original para o ficheiro de cache
CACHE_FILE = "/opt/phantasma/tuya_cache.json" 
PORTS_TO_LISTEN = [6666, 6667] 
LAST_POLL = {} 
POLL_COOLDOWN = 5 
# ------------------------------------------

def _get_tuya_triggers():
    all_actions = ACTIONS_ON + ACTIONS_OFF + STATUS_TRIGGERS + DEBUG_TRIGGERS
    if hasattr(config, 'TUYA_DEVICES') and isinstance(config.TUYA_DEVICES, dict):
        device_nicknames = list(config.TUYA_DEVICES.keys())
        return BASE_NOUNS + device_nicknames + all_actions
    return BASE_NOUNS + all_actions

TRIGGERS = _get_tuya_triggers()

# --- Funções de Dados e Cache (Do Daemon) ---

def _load_cache():
    """ Carrega a cache persistente. """
    if not os.path.exists(CACHE_FILE): return {}
    try:
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    except: return {}

def _save_cache(data):
    """ Guarda a cache persistentemente. """
    try:
        os.makedirs(os.path.dirname(CACHE_FILE) or '.', exist_ok=True)
        with open(CACHE_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[ERRO IO] Ao guardar cache: {e}")

def _get_cached_status(nickname):
    """ Obtém o status de um dispositivo a partir da cache. """
    data = _load_cache()
    device_data = data.get(nickname)
    if device_data and 'dps' in device_data:
        return device_data
    return None

def _get_device_name_by_ip(ip):
    """ Procura o nome do dispositivo pelo IP nas configs. """
    if not hasattr(config, 'TUYA_DEVICES'): return None, None
    for name, details in config.TUYA_DEVICES.items():
        if details.get('ip') == ip:
            return name, details
    return None, None

def _poll_device_task(name, details, force=False):
    """ Tenta conectar e ler o estado do dispositivo (Lógica do Daemon). """
    ip = details.get('ip')
    global LAST_POLL 

    if not force:
        if time.time() - LAST_POLL.get(name, 0) < POLL_COOLDOWN:
            return
    
    LAST_POLL[name] = time.time()

    versions = [3.3, 3.1, 3.4, 3.2]
    success = False
    data_dps = None
    error_msg = ""
    DeviceClass = OutletDevice if 'OutletDevice' in globals() else Device

    for ver in versions:
        try:
            d = DeviceClass(details['id'], ip, details['key'])
            d.set_socketTimeout(3)
            d.set_version(ver)
            status = d.status()
            
            if 'dps' in status:
                data_dps = status['dps']
                success = True
                break
            elif status.get('Err') == '905':
                error_msg = "Erro 905 (Chave/IP)"
                break
        except Exception as e:
            error_msg = str(e)
            continue

    if success and data_dps:
        try:
            current_cache = _load_cache()
            prev_data = current_cache.get(name, {})
            prev_dps = prev_data.get('dps')

            current_cache[name] = {"dps": data_dps, "timestamp": time.time()}
            
            if prev_dps != data_dps:
                print(f"[Tuya Daemon] [NOVO] '{name}': {data_dps}")
                _save_cache(current_cache)
            elif force:
                print(f"[Tuya Daemon] [OK] '{name}' (Online)")
                _save_cache(current_cache)
            else:
                _save_cache(current_cache)
                
        except Exception as e:
            print(f"[Tuya Daemon] [ERRO Cache] {e}")
    else:
        if force or "905" in error_msg:
             print(f"[Tuya Daemon] [FALHA] '{name}' ({ip}): {error_msg}")


# --- Listener UDP (Do Daemon) ---

def _udp_listener(port):
    """ Mantém a escuta de heartbeats Tuya para triggerar o polling silencioso. """
    if "tinytuya" not in globals(): return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('', port))
        print(f"[Tuya Daemon] À escuta na porta UDP {port}...")
    except Exception as e:
        print(f"[Tuya Daemon] [ERRO] Falha ao bind na porta {port}: {e}")
        return

    while True:
        try:
            sock.settimeout(1.0) 
            data, addr = sock.recvfrom(4096)
            ip = addr[0]
            name, details = _get_device_name_by_ip(ip)
            if name and details:
                threading.Thread(target=_poll_device_task, args=(name, details, False)).start()
        except socket.timeout:
            continue
        except Exception as e:
            time.sleep(1)


# --- Função de Inicialização (Para chamar do assistant.py) ---

def start_tuya_daemon():
    """ 
    Executa o warm-up e inicia as threads UDP listeners. 
    Chamada a partir do assistant.py no startup.
    """
    if "tinytuya" not in globals() or not hasattr(config, 'TUYA_DEVICES') or not config.TUYA_DEVICES: 
        print("[Tuya Daemon] Não iniciado: Requisitos em falta.")
        return

    print("--- Phantasma Tuya Daemon (Integrado) ---")
    
    # 1. Warm-up: Scan explícito no arranque
    print("[Tuya Daemon] A verificar dispositivos (warm-up)...")
    for name, details in config.TUYA_DEVICES.items():
        threading.Thread(target=_poll_device_task, args=(name, details, True)).start()

    # 2. Inicia listeners UDP em threads separadas
    for port in PORTS_TO_LISTEN:
        t = threading.Thread(target=_udp_listener, args=(port,))
        t.daemon = True 
        t.start()
    
    print("[Tuya Daemon] Listeners UDP iniciados.")


# --- Lógica Principal da Skill (Quase inalterada) ---

def _try_connect_with_versioning(dev_id, dev_ip, dev_key):
    # Função para controlo direto (ON/OFF), que requer ligação imediata
    if 'x' in dev_ip.lower(): return (None, "IP inválido", None)
    
    for version in VERSIONS_TO_TRY:
        try:
            d = tinytuya.Device(dev_id, dev_ip, dev_key)
            d.set_socketTimeout(2); d.set_version(version)
            status = d.status()
            if 'dps' in status: return (d, status, None)
            elif status.get('Err') == '905': break
        except: break 
    return (None, None, None)

def handle(user_prompt_lower, user_prompt_full):
    if "tinytuya" not in globals(): return "Falta biblioteca tinytuya."
    if not hasattr(config, 'TUYA_DEVICES'): return None

    final_action = None
    if any(a in user_prompt_lower for a in ACTIONS_OFF): final_action = "OFF"
    elif any(a in user_prompt_lower for a in ACTIONS_ON): final_action = "ON"
    elif any(a in user_prompt_lower for a in DEBUG_TRIGGERS): final_action = "DEBUG"
    elif any(a in user_prompt_lower for a in STATUS_TRIGGERS): final_action = "STATUS"
    
    if not final_action: return None

    # Matching Inteligente (MANTIDO)
    targets = []
    target_keyword = None
    for noun in BASE_NOUNS:
        if noun in user_prompt_lower: target_keyword = noun; break
            
    if target_keyword:
        potential_matches = {n: d for n, d in config.TUYA_DEVICES.items() if target_keyword in n.lower()}
        if potential_matches:
            specific_targets = []
            for name, details in potential_matches.items():
                identifier = name.lower().replace(target_keyword, "").strip()
                if identifier and identifier in user_prompt_lower: specific_targets.append((name, details))
            targets = specific_targets if specific_targets else list(potential_matches.items())
    else:
        for nickname, details in config.TUYA_DEVICES.items():
            if nickname.lower() in user_prompt_lower: targets.append((nickname, details)); break

    if not targets: return None

    responses = []
    for nickname, details in targets:
        if final_action == "DEBUG":
            responses.append(_handle_debug_status(nickname, details))
        elif final_action in ["ON", "OFF"]:
            if "sensor" in nickname.lower(): continue
            dps_index = 20 if "luz" in nickname.lower() or "lâmpada" in nickname.lower() else 1
            try:
                _handle_switch(nickname, details, final_action, dps_index)
                state = 'ligado' if final_action == 'ON' else 'desligado'
                responses.append(f"{nickname} {state}")
            except: responses.append(f"Erro no {nickname}")
        elif final_action == "STATUS":
            responses.append(_handle_sensor(nickname, details, user_prompt_lower))

    if not responses: return None
    return ", ".join(responses) + "."

# --- Processadores (Ajustados para usar as helpers de cache) ---
def _handle_debug_status(nickname, details):
    (d, result, err) = _try_connect_with_versioning(details['id'], details['ip'], details['key'])
    if d: return f"{nickname}: Online"
    cached = _get_cached_status(nickname)
    return f"{nickname}: Offline (Cache: {bool(cached)})"

def _handle_switch(nickname, details, action, dps_index):
    (d, status, err) = _try_connect_with_versioning(details['id'], details['ip'], details['key'])
    if not d: raise Exception("incontactável")
    d.set_value(dps_index, True if action == "ON" else False, nowait=True)

def _handle_sensor(nickname, details, prompt):
    (d, data, err) = _try_connect_with_versioning(details['id'], details['ip'], details['key'])
    dps = data.get('dps') if d and data else None
    
    if not dps:
        cached = _get_cached_status(nickname)
        if cached and 'dps' in cached: dps = cached['dps']
        else: return f"Sem dados do {nickname}."
        
    temp = dps.get('1') or dps.get('102'); hum = dps.get('2') or dps.get('103')
    parts = []
    if temp is not None: parts.append(f"{float(temp)/10}°C")
    if hum is not None: parts.append(f"{int(hum)}%")
    return f"{nickname}: {' '.join(parts)}" if parts else f"{nickname}: Dados estranhos."

# skill_tuya.py (Substituir função get_status_for_device)

def get_status_for_device(nickname):
    if not hasattr(config, 'TUYA_DEVICES') or nickname not in config.TUYA_DEVICES: return {"state": "unreachable"}
    details = config.TUYA_DEVICES[nickname]

    # 1. PRIORIZAÇÃO EXCLUSIVA DA CACHE
    dps = None
    cached = _get_cached_status(nickname)
    if cached: dps = cached.get('dps')

    if not dps:
        # Se a cache está vazia, o dispositivo está a dormir ou inacessível.
        # Não tentamos polling, confiamos apenas no daemon.
        return {"state": "unreachable"}


    if "sensor" in nickname.lower():
        res = {"state": "on"}
        t = dps.get('1') or dps.get('102'); h = dps.get('2') or dps.get('103')
        if t is not None: res["temperature"] = float(t)/10
        if h is not None: res["humidity"] = int(h)
        return res

    if "desumidificador" in nickname.lower():
        power = float(dps.get('19', 0))/10
        return {"state": "on" if dps.get('1') else "off", "power_w": power}

    idx = "20" if "luz" in nickname.lower() else "1"
    return {"state": "on" if dps.get(idx) else "off"}
