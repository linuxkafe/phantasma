import config
import time
import json
import os
import socket
import sys
import threading
import tempfile

try:
    import tinytuya
    from tinytuya import OutletDevice, Device 
except ImportError:
    print("AVISO: Biblioteca 'tinytuya' não encontrada.")
    class Device: pass
    OutletDevice = Device

# --- Configuração ---
TRIGGER_TYPE = "contains"
CACHE_FILE = "/opt/phantasma/cache/tuya_cache.json"
PORTS_TO_LISTEN = [6666, 6667]
POLL_COOLDOWN = 10 
LAST_POLL = {}
VERBOSE_LOGGING = False 

ACTIONS_ON = ["liga", "ligar", "acende", "acender", "ativa"]
ACTIONS_OFF = ["desliga", "desligar", "apaga", "apagar", "desativa"]
STATUS_TRIGGERS = ["como está", "estado", "temperatura", "humidade", "nível", "leitura", "quanto", "gastar", "consumo"]
DEBUG_TRIGGERS = ["diagnostico", "dps"]
BASE_NOUNS = ["sensor", "luz", "lâmpada", "desumidificador", "exaustor", "tomada", "ficha", "quarto", "sala", "luzes", "fichas"]
VERSIONS_TO_TRY = [3.3, 3.1, 3.4, 3.5]

def _get_tuya_triggers():
    base = BASE_NOUNS + ACTIONS_ON + ACTIONS_OFF + STATUS_TRIGGERS + DEBUG_TRIGGERS
    if hasattr(config, 'TUYA_DEVICES'):
        base += list(config.TUYA_DEVICES.keys())
    return base

TRIGGERS = _get_tuya_triggers()

# --- Helpers de Cache ---
def _load_cache():
    if not os.path.exists(CACHE_FILE): return {}
    try:
        with open(CACHE_FILE, 'r') as f: return json.load(f)
    except: return {}

def _save_cache(data):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(CACHE_FILE), delete=False) as tf:
            json.dump(data, tf, indent=4)
            tf.flush()
            os.fsync(tf.fileno())
            temp_name = tf.name
        os.replace(temp_name, CACHE_FILE)
        os.chmod(CACHE_FILE, 0o666)
    except Exception as e:
        print(f"[Tuya] Erro cache: {e}")
        if 'temp_name' in locals() and os.path.exists(temp_name): os.remove(temp_name)

def _get_cached_status(nickname):
    data = _load_cache()
    return data.get(nickname)

def _get_device_name_by_ip(ip):
    if not hasattr(config, 'TUYA_DEVICES'): return None, None
    for name, details in config.TUYA_DEVICES.items():
        if details.get('ip') == ip: return name, details
    return None, None

def _poll_device_task(name, details, force=False):
    ip = details.get('ip')
    if not ip or ip.endswith('x'): return 
    global LAST_POLL
    if not force and (time.time() - LAST_POLL.get(name, 0) < POLL_COOLDOWN): return
    LAST_POLL[name] = time.time()
    dps = None
    for ver in VERSIONS_TO_TRY:
        try:
            d = OutletDevice(details['id'], ip, details['key'])
            d.set_socketTimeout(3); d.set_version(ver)
            status = d.status()
            if status and 'dps' in status:
                dps = status['dps']
                break
        except: continue
    if dps:
        cache = _load_cache()
        if name not in cache: cache[name] = {}
        cache[name]["dps"] = dps; cache[name]["timestamp"] = time.time()
        _save_cache(cache)

def _udp_listener(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: sock.bind(('', port))
    except: return
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            name, details = _get_device_name_by_ip(addr[0])
            if name: threading.Thread(target=_poll_device_task, args=(name, details, True)).start()
        except: continue

def init_skill_daemon():
    if not hasattr(config, 'TUYA_DEVICES'): return
    print("[Tuya] A iniciar daemon...")
    for name, details in config.TUYA_DEVICES.items(): threading.Thread(target=_poll_device_task, args=(name, details, True)).start()
    for port in PORTS_TO_LISTEN: threading.Thread(target=_udp_listener, args=(port,), daemon=True).start()

def get_status_for_device(nickname):
    cached = _get_cached_status(nickname)
    if not cached or 'dps' not in cached: return {"state": "unreachable"}
    dps = cached['dps']; result = {}
    is_on = dps.get('1') or dps.get('20')
    result['state'] = 'on' if is_on else 'off'
    power_raw = dps.get('19') or dps.get('104')
    if power_raw: result['power_w'] = float(power_raw) / 10.0
    temp = dps.get('1') or dps.get('102') or dps.get('va_temperature')
    hum = dps.get('2') or dps.get('103') or dps.get('va_humidity')
    if "sensor" in nickname.lower() or any(x in nickname.lower() for x in ["temp", "hum"]):
        if temp: result['temperature'] = float(temp) / 10.0 if float(temp) > 100 else float(temp)
        if hum: result['humidity'] = int(hum)
    return result

def handle(user_prompt_lower, user_prompt_full):
    if not hasattr(config, 'TUYA_DEVICES'): return None

    # Lógica de prioridade: Desliga > Liga
    action = None
    if any(x in user_prompt_lower for x in ACTIONS_OFF): action = "off"
    elif any(x in user_prompt_lower for x in ACTIONS_ON): action = "on"
    elif any(x in user_prompt_lower for x in STATUS_TRIGGERS): action = "status"
    if not action: return None

    targets = []
    # 1. Procurar alcunha direta (Exata)
    for nick, conf in config.TUYA_DEVICES.items():
        if nick.lower() in user_prompt_lower:
            targets.append((nick, conf))
    
    # 2. Lógica inteligente para Sensores e Locais
    if not targets:
        locations = ["sala", "quarto", "wc", "cozinha", "entrada"]
        mentioned_loc = next((loc for loc in locations if loc in user_prompt_lower), None)
        is_sensor_query = any(x in user_prompt_lower for x in ["temperatura", "humidade"])
        
        for nick, conf in config.TUYA_DEVICES.items():
            nick_l = nick.lower()
            if mentioned_loc and mentioned_loc in nick_l:
                if is_sensor_query and "sensor" in nick_l:
                    targets.append((nick, conf)); break
                elif any(noun in user_prompt_lower for noun in BASE_NOUNS) and any(noun in nick_l for noun in BASE_NOUNS):
                    targets.append((nick, conf)); break

    # 3. Lógica Genérica (Fallback): "Liga o exaustor" -> Liga TODOS os exaustores
    if not targets:
        for noun in BASE_NOUNS:
            if noun in user_prompt_lower:
                for nick, conf in config.TUYA_DEVICES.items():
                    if noun in nick.lower(): targets.append((nick, conf))
                if targets: break

    if not targets: return None

    # Processar STATUS
    if action == "status":
        target_nick, _ = targets[0]
        st = get_status_for_device(target_nick)
        if st['state'] == 'unreachable': return f"O {target_nick} não responde das sombras."
        res_parts = [f"O {target_nick} está {st['state']}"]
        if 'temperature' in st: res_parts.append(f"com {st['temperature']} graus")
        if 'humidity' in st: res_parts.append(f"e {st['humidity']}% de humidade")
        if 'power_w' in st: res_parts.append(f"a gastar {st['power_w']} Watts")
        return ", ".join(res_parts) + "."

    # Processar AÇÃO (On/Off)
    success = 0
    for nick, conf in targets:
        try:
            d = OutletDevice(conf['id'], conf['ip'], conf['key'])
            d.set_version(3.3); d.set_socketTimeout(2)
            idx = 20 if any(x in nick.lower() for x in ["luz", "lâmpada", "candeeiro"]) else 1
            
            # Executa sem esperar retorno (nowait=True não retorna bool útil)
            d.set_value(idx, action == "on", nowait=True)
            
            # Se não houve exceção, contamos como sucesso
            success += 1
            print(f"[Tuya] {nick} -> {action}")
        except Exception as e: 
            print(f"[Tuya] Erro ao controlar {nick}: {e}")
            continue

    action_pt = "ligado" if action == "on" else "desligado"
    
    if len(targets) > 1:
        return f"{success} dispositivos {action_pt}s."
    elif len(targets) == 1:
        return f"{targets[0][0]} {action_pt}."
    else:
        return "Tentei, mas nenhum dispositivo respondeu."
