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
CACHE_FILE = "/opt/phantasma/tuya_cache.json"
PORTS_TO_LISTEN = [6666, 6667]
POLL_COOLDOWN = 5
LAST_POLL = {}

ACTIONS_ON = ["liga", "ligar", "acende", "acender"]
ACTIONS_OFF = ["desliga", "desligar", "apaga", "apagar"]
# EDITADO: Adicionadas palavras "quanto", "gastar", "consumo"
STATUS_TRIGGERS = ["como está", "estado", "temperatura", "humidade", "nível", "leitura", "quanto", "gastar", "consumo"]
DEBUG_TRIGGERS = ["diagnostico", "dps"]
BASE_NOUNS = ["sensor", "luz", "lâmpada", "desumidificador", "exaustor", "tomada", "ficha", "quarto", "sala"]
VERSIONS_TO_TRY = [3.3, 3.1, 3.4, 3.2]

def _get_tuya_triggers():
    base = BASE_NOUNS + ACTIONS_ON + ACTIONS_OFF + STATUS_TRIGGERS + DEBUG_TRIGGERS
    if hasattr(config, 'TUYA_DEVICES'):
        base += list(config.TUYA_DEVICES.keys())
    return base

TRIGGERS = _get_tuya_triggers()
# --- Helpers de Cache e Daemon ---

def _load_cache():
    if not os.path.exists(CACHE_FILE): return {}
    try:
        with open(CACHE_FILE, 'r') as f: return json.load(f)
    except: return {}

def _save_cache(data):
    """
    Guarda a cache usando um ficheiro temporário ÚNICO para cada escrita.
    Isto resolve conflitos entre threads ou processos simultâneos.
    """
    # Garante que o ficheiro temporário é criado na mesma pasta (importante para o atomic rename)
    dir_name = os.path.dirname(CACHE_FILE)
    
    try:
        # Cria um ficheiro temporário com nome aleatório (ex: tmpxyz123)
        # delete=False porque queremos renomeá-lo no fim
        with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False) as tf:
            json.dump(data, tf, indent=4)
            tf.flush()
            os.fsync(tf.fileno()) # Força a escrita física no disco
            temp_name = tf.name # Guarda o nome para usar no replace

        # Substituição atómica. 
        # Como o nome temp_name é único, ninguém mais está a mexer nele.
        os.replace(temp_name, CACHE_FILE)
        
        # Define permissões para que todos (daemon e skill) possam ler/escrever
        os.chmod(CACHE_FILE, 0o666)

    except Exception as e:
        print(f"[Tuya] Erro cache: {e}")
        # Limpeza em caso de erro
        if 'temp_name' in locals() and os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass

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
    global LAST_POLL
    if not force and (time.time() - LAST_POLL.get(name, 0) < POLL_COOLDOWN): return
    LAST_POLL[name] = time.time()

    dps = None
    for ver in VERSIONS_TO_TRY:
        try:
            d = OutletDevice(details['id'], ip, details['key'])
            d.set_socketTimeout(2); d.set_version(ver)
            status = d.status()
            if 'dps' in status:
                dps = status['dps']
                break
        except: continue

    if dps:
        try:
            cache = _load_cache()
            # Só loga se mudou DPS importantes
            prev = cache.get(name, {}).get('dps')
            if prev != dps: 
                print(f"[Tuya] '{name}' atualizado: {dps}")
            
            cache[name] = {"dps": dps, "timestamp": time.time()}
            _save_cache(cache)
        except: pass

def _udp_listener(port):
    if "tinytuya" not in globals(): return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: sock.bind(('', port))
    except: return

    while True:
        try:
            sock.settimeout(1.0)
            data, addr = sock.recvfrom(4096)
            name, details = _get_device_name_by_ip(addr[0])
            if name: threading.Thread(target=_poll_device_task, args=(name, details, False)).start()
        except: continue

def init_skill_daemon():
    if "tinytuya" not in globals() or not hasattr(config, 'TUYA_DEVICES'): return
    print("[Tuya] A iniciar daemon integrado...")
    for name, details in config.TUYA_DEVICES.items():
        threading.Thread(target=_poll_device_task, args=(name, details, True)).start()
    for port in PORTS_TO_LISTEN:
        threading.Thread(target=_udp_listener, args=(port,), daemon=True).start()

# --- API Pública (Web UI) ---

def get_status_for_device(nickname):
    """ Determina estado e consumo para a barra de topo """
    cached = _get_cached_status(nickname)
    if not cached or 'dps' not in cached: return {"state": "unreachable"}
    
    dps = cached['dps']
    result = {}

    # 1. Estado ON/OFF
    is_on = dps.get('1') or dps.get('20')
    result['state'] = 'on' if is_on else 'off'

    # 2. Consumo (Watts)
    power_raw = dps.get('19') or dps.get('104')
    if power_raw is not None:
        try:
            result['power_w'] = float(power_raw) / 10.0
        except: pass

    # 3. Sensores
    if "sensor" in nickname.lower():
        result['state'] = 'on'
        t = dps.get('1') or dps.get('102')
        h = dps.get('2') or dps.get('103')
        if t: result['temperature'] = float(t) / 10.0
        if h: result['humidity'] = int(h)

    return result

# --- Router de Voz ---
def handle(user_prompt_lower, user_prompt_full):
    if not hasattr(config, 'TUYA_DEVICES'): return None

    action = None

    # LÓGICA DE PRIORIDADE: OFF > ON > STATUS
    # Verificamos sempre o OFF primeiro por segurança.
    if any(x in user_prompt_lower for x in ACTIONS_OFF): action = "off"
    elif any(x in user_prompt_lower for x in ACTIONS_ON): action = "on"
    elif any(x in user_prompt_lower for x in STATUS_TRIGGERS): action = "status"

    if not action: return None

    target_nick = None
    target_conf = None

    # 1. Procura direta pelo nickname completo (ex: "luz da sala")
    for nick, conf in config.TUYA_DEVICES.items():
        if nick.lower() in user_prompt_lower:
            target_nick = nick; target_conf = conf; break

    # 2. Procura heurística (ex: "luz" + "sala")
    if not target_nick:
        for noun in BASE_NOUNS:
            if noun in user_prompt_lower:
                for nick, conf in config.TUYA_DEVICES.items():
                    if noun in nick.lower() and any(loc in user_prompt_lower for loc in ["sala", "quarto", "wc"]):
                        target_nick = nick; target_conf = conf; break
                if target_nick: break

    if not target_nick: return None

    # Execução da Ação STATUS
    if action == "status":
        # Nota: esta função depende da cache atualizada pelo daemon
        st = get_status_for_device(target_nick)
        if st['state'] == 'unreachable': return f"Não consigo aceder ao {target_nick}."

        resp = f"O {target_nick} está {st['state']}"
        if 'power_w' in st: resp += f" a gastar {st['power_w']} Watts"
        if 'temperature' in st: resp += f", temperatura {st['temperature']} graus"

        # Pequeno ajuste gramatical se a resposta for curta
        return resp + "."

    # Execução das Ações ON/OFF
    if action in ["on", "off"]:
        try:
            # Tenta ligação direta rápida para ação imediata
            d = OutletDevice(target_conf['id'], target_conf['ip'], target_conf['key'])
            d.set_socketTimeout(2); d.set_version(3.3)

            # Determina o DPS (20 para luzes, 1 para tomadas genéricas/desumidificadores)
            idx = 20 if any(x in target_nick.lower() for x in ['luz', 'lâmpada']) else 1

            # Envia o comando (True para ON, False para OFF)
            d.set_value(idx, action == "on", nowait=True)

            return f"{target_nick} {'ligado' if action=='on' else 'desligado'}."
        except: return f"Erro ao controlar {target_nick}."

    return None
