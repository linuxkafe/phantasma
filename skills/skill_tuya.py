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
BASE_NOUNS = ["sensor", "luz", "lâmpada", "desumidificador", "exaustor", "tomada", "ficha", "quarto", "sala"]
VERSIONS_TO_TRY = [3.3, 3.1, 3.4, 3.5]

def _get_tuya_triggers():
    base = BASE_NOUNS + ACTIONS_ON + ACTIONS_OFF + STATUS_TRIGGERS + DEBUG_TRIGGERS
    if hasattr(config, 'TUYA_DEVICES'):
        base += list(config.TUYA_DEVICES.keys())
    return base

TRIGGERS = _get_tuya_triggers()

# --- Helpers de Cache ---
def _ensure_permissions():
    if os.path.exists(CACHE_FILE):
        try: os.chmod(CACHE_FILE, 0o666)
        except: pass

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

# --- Lógica do Daemon ---
def _poll_device_task(name, details, force=False):
    ip = details.get('ip')
    if not ip or ip.endswith('x'): return 

    global LAST_POLL
    if not force and (time.time() - LAST_POLL.get(name, 0) < POLL_COOLDOWN): return
    LAST_POLL[name] = time.time()

    dps = None
    connected_ver = None

    if VERBOSE_LOGGING: print(f"[Tuya] A sondar '{name}' ({ip})...")

    for ver in VERSIONS_TO_TRY:
        try:
            d = OutletDevice(details['id'], ip, details['key'])
            d.set_socketTimeout(3); d.set_version(ver)
            status = d.status()
            if status and 'dps' in status:
                dps = status['dps']; connected_ver = ver
                if VERBOSE_LOGGING: print(f"[Tuya] SUCESSO '{name}' (v{ver}): {dps}")
                break
        except: continue

    if dps:
        try:
            cache = _load_cache()
            prev_dps = cache.get(name, {}).get("dps", {})
            if prev_dps != dps: print(f"[Tuya] Estado alterado: '{name}'")
            if name not in cache: cache[name] = {}
            cache[name]["dps"] = dps; cache[name]["timestamp"] = time.time(); cache[name]["version"] = connected_ver
            _save_cache(cache)
        except Exception as e: print(f"[Tuya] Erro ao guardar dados: {e}")
    else:
        if force or VERBOSE_LOGGING: print(f"[Tuya] Falha ao comunicar com '{name}'.")

def _udp_listener(port):
    if "tinytuya" not in globals(): return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: sock.bind(('', port))
    except Exception as e: print(f"[Tuya] Erro bind porta {port}: {e}"); return

    while True:
        try:
            sock.settimeout(None)
            data, addr = sock.recvfrom(4096)
            name, details = _get_device_name_by_ip(addr[0])
            if name: 
                if VERBOSE_LOGGING: print(f"[Tuya] Broadcast recebido de '{name}'! A atualizar...")
                threading.Thread(target=_poll_device_task, args=(name, details, True)).start()
        except Exception as e: print(f"[Tuya] Erro UDP: {e}"); time.sleep(1)

def init_skill_daemon():
    if "tinytuya" not in globals() or not hasattr(config, 'TUYA_DEVICES'): return
    print("[Tuya] A iniciar daemon integrado...")
    for name, details in config.TUYA_DEVICES.items(): threading.Thread(target=_poll_device_task, args=(name, details, True)).start()
    for port in PORTS_TO_LISTEN: threading.Thread(target=_udp_listener, args=(port,), daemon=True).start()

# --- API Pública ---
def get_status_for_device(nickname):
    cached = _get_cached_status(nickname)
    if not cached or 'dps' not in cached: return {"state": "unreachable"}
    dps = cached['dps']; result = {}

    is_on = dps.get('1') or dps.get('20')
    if is_on is not None: result['state'] = 'on' if is_on else 'off'
    else: result['state'] = 'on' 

    power_raw = dps.get('19') or dps.get('104')
    if power_raw is not None:
        try: result['power_w'] = float(power_raw) / 10.0
        except: pass

    temp = None
    for k in ['1', '101', '102', 'va_temperature', 'temp_current']:
        if k in dps: temp = dps[k]; break
    hum = None
    for k in ['2', '103', '104', 'va_humidity', 'humidity_value']:
        if k in dps: hum = dps[k]; break

    if temp is not None: 
        try: result['temperature'] = float(temp) / 10.0
        except: result['temperature'] = float(temp)
    if hum is not None:
        try: result['humidity'] = int(hum)
        except: pass
    return result

# --- Router de Voz (COM MULTI-AÇÃO) ---
def handle(user_prompt_lower, user_prompt_full):
    if not hasattr(config, 'TUYA_DEVICES'): return None

    # 1. Determinar Ação
    action = None
    if any(x in user_prompt_lower for x in ACTIONS_OFF): action = "off"
    elif any(x in user_prompt_lower for x in ACTIONS_ON): action = "on"
    elif any(x in user_prompt_lower for x in STATUS_TRIGGERS): action = "status"
    if not action: return None

    targets = [] # Lista de (nome, config)

    # 2. Tentar encontrar alvo ESPECÍFICO (ex: "Exaustor da Sala")
    for nick, conf in config.TUYA_DEVICES.items():
        if nick.lower() in user_prompt_lower:
            targets.append((nick, conf))
            break # Encontrou exato, para aqui

    # 3. Se não encontrou específico, procurar GENÉRICO (ex: "Exaustor")
    if not targets:
        for noun in BASE_NOUNS:
            # Se a palavra-chave estiver na frase (ex: "liga os exaustores")
            # Nota: usamos plural simples "s" para apanhar "exaustores" se "exaustor" for a base
            if noun in user_prompt_lower or (noun + "es") in user_prompt_lower or (noun + "s") in user_prompt_lower:
                for nick, conf in config.TUYA_DEVICES.items():
                    # Se o nome do dispositivo contiver a palavra-chave (ex: "Exaustor da Sala" contém "Exaustor")
                    if noun in nick.lower():
                        targets.append((nick, conf))
    
    if not targets: return None

    # 4. Executar Ação (Loop pelos alvos)
    
    # Se for "status" e houver muitos, pode ser chato, então respondemos só ao primeiro ou fazemos um resumo
    if action == "status":
        target_nick, _ = targets[0] # Só o primeiro para não falar demais
        st = get_status_for_device(target_nick)
        if st['state'] == 'unreachable': return f"Não consigo aceder ao {target_nick}."
        resp = f"O {target_nick} está {st['state']}"
        if 'power_w' in st: resp += f" a gastar {st['power_w']} Watts"
        if 'temperature' in st: resp += f", temperatura {st['temperature']} graus"
        return resp + "."

    # Se for ON/OFF, executamos em todos
    success_count = 0
    fail_count = 0
    
    for nick, conf in targets:
        try:
            d = OutletDevice(conf['id'], conf['ip'], conf['key'])
            d.set_socketTimeout(2); d.set_version(3.3)
            idx = 20 if any(x in nick.lower() for x in ['luz', 'lâmpada']) else 1
            d.set_value(idx, action == "on", nowait=True)
            success_count += 1
            # Pequena pausa para não engasgar a rede se forem muitos
            if len(targets) > 1: time.sleep(0.2)
        except: 
            fail_count += 1

    # 5. Resposta Final
    action_str = 'ligado' if action == 'on' else 'desligado'
    
    if len(targets) == 1:
        nick = targets[0][0]
        if fail_count > 0: return f"Erro ao controlar {nick}."
        return f"{nick} {action_str}."
    else:
        # Resposta de grupo
        if fail_count == 0:
            return f"Feito. {len(targets)} dispositivos {action_str}s."
        else:
            return f"Consegui controlar {success_count} dispositivos, mas {fail_count} falharam."
