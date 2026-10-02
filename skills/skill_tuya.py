import config
from pathlib import Path
import time
import json
import os
import socket
import sys
import threading
import tempfile
import logging

logger = logging.getLogger("phantasma.tuya")

try:
    import tinytuya
    from tinytuya import OutletDevice, Device 
except ImportError:
    print("AVISO: Biblioteca 'tinytuya' não encontrada.")
    class Device: pass
    OutletDevice = Device

# --- Configuração ---
TRIGGER_TYPE = "contains"
# Resolved through config.CACHE_DIR instead of a literal /opt/phantasma path.
# A host path in code cannot be overridden, so these caches had to be forked
# per host. In production the resolved path is byte-identical to the old one.
CACHE_FILE = str(Path(config.CACHE_DIR) / "tuya_cache.json")
PORTS_TO_LISTEN = [6666, 6667]
POLL_COOLDOWN = 10
# Antes so havia poll no boot e ao recibir UDP da app. A app oficial so
# transmite quando o telemóvel esta aberto e em foreground, entao fechar a app
# deixava a cache envelhecer sem limite. 60s e o mesmo cadencia do skill_cloogy.
POLL_INTERVAL = 60
LAST_POLL = {}
# Names whose poll is currently running. See _poll_device_task for why the
# timestamp cooldown alone is not enough to prevent overlapping polls.
_INFLIGHT = set()
_INFLIGHT_LOCK = threading.Lock()
VERBOSE_LOGGING = False 

ACTIONS_ON = ["liga", "ligar", "acende", "acender", "ativa"]
ACTIONS_OFF = ["desliga", "desligar", "apaga", "apagar", "desativa"]
# "quanto" saiu daqui pela mesma raza que saiu do cloogy: colidia com o
# calculator e tornava uma conta simples inacessivel a um convidado. Substituido
# pelas formas que NAO podem aparecer numa conta -- "quanto e 2+2" contem
# "quanto e", nunca "quanto esta".
#
# Ficam em STATUS_TRIGGERS, e nao em BASE_NOUNS, de proposito: daqui sao
# leituras, e nada mais. Meter "wc" ou "cozinha" nos BASE_NOUNS faria o prompt
# autorizar o controlo do que estiver nessa divisao -- que e exactamente a
# licence que o comentario do BASE_NOUNS acrescenta.
# As formas sem acento nao saoredundancia: `matches` faz `contains` palavra-a-palavra
# sobre o texto tal e qual, e o texto vem do STT, que nao acenta. Verificado --
# "como esta o wc" -> False, "como esta o wc" -> True. Ou seja, "como esta",
# que ja estava aqui desde sempre, nunca disparou por voz. E um defeito mais largo
# que este ficheiro; nao se corrige aqui. Mas um gatilho novo nao pode nascer
# partido, senao nasce morto.
STATUS_TRIGGERS = ["como está", "estado", "temperatura", "humidade", "nível",
                   "leitura", "quanto está", "quanto esta", "quanto marca",
                   "quanto rende", "gastar", "consumo"]
DEBUG_TRIGGERS = ["diagnostico", "dps"]
BASE_NOUNS = ["sensor", "luz", "lâmpada", "desumidificador", "exaustor", "tomada", "ficha", "quarto", "sala", "luzes", "fichas"]

# "quarto" and "sala" are in BASE_NOUNS, which made them a licence to control
# anything in that room. The prompt and the nickname were compared for ANY
# shared base noun, and the room word was shared by definition -- so "ligar o
# candeeiro do quarto" matched `Sensor do Quarto`, and the owner switched a
# THERMOMETER on when he asked for a lamp. The skill answered "Sensor do Quarto
# ligado", which was true of what it touched and useless as an answer.
#
# What the branch was for: "liga a luz do quarto" -> a light in the Quarto.
# So the noun the owner used has to be a DEVICE type, and it has to appear in
# the nickname. A room word can qualify the room but never stand in for the
# device.
DEVICE_NOUNS = ["luz", "luzes", "lâmpada", "lampada", "candeeiro", "abajur",
                "desumidificador", "exaustor", "ventoinha", "ventilador",
                "tomada", "ficha", "forno", "carregador", "sensor", "aspirador"]


def _matches_a_device_noun(prompt_lower, nickname_lower):
    """Does this nickname's device type appear in what the owner asked for?

    Returns False for anything that is not a device type, which is what stops a
    room word from standing in for one.
    """
    return any(noun in prompt_lower and noun in nickname_lower
               for noun in DEVICE_NOUNS)
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
    # One poll in flight per device. LAST_POLL records the START of a poll, not
    # its end, so it cannot prevent a pile-up: a dead device burns up to 4
    # versions x 3s, and measured across 9 dead devices one turn took 80s --
    # longer than POLL_INTERVAL, so the next turn started while the last was
    # still running and threads accumulated without bound. The sensor fleet is
    # currently offline, which is precisely when this happens.
    with _INFLIGHT_LOCK:
        if name in _INFLIGHT: return
        _INFLIGHT.add(name)
    try:
        _poll_device_body(name, details)
    finally:
        with _INFLIGHT_LOCK: _INFLIGHT.discard(name)

def _poll_device_body(name, details):
    ip = details.get('ip')
    dps = None
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

def _poll_all(force=True):
    """Um poll por dispositivo, cada um na sua thread.

    Threads separadas porque _poll_device_task tenta 4 versoes com timeout de
    3s: um dispositivo morto custa ate 12s, e um loop sequencial sobre 9
    dispositivos passaria o intervalo de poll antes de recomecar.
    """
    if not hasattr(config, 'TUYA_DEVICES'): return
    for name, details in config.TUYA_DEVICES.items():
        threading.Thread(target=_poll_device_task, args=(name, details, force), daemon=True).start()

def _poll_loop():
    while True:
        _poll_all(force=False)
        time.sleep(POLL_INTERVAL)

def init_skill_daemon():
    if not hasattr(config, 'TUYA_DEVICES'): return
    print("[Tuya] A iniciar daemon...")
    _poll_all(force=True)
    for port in PORTS_TO_LISTEN: threading.Thread(target=_udp_listener, args=(port,), daemon=True).start()
    threading.Thread(target=_poll_loop, daemon=True).start()

# Declared DPS schemas for Tuya sensors, based on observed cache values.
# dps 1 arrives in deci-celsius (245 -> 24.5 °C). dps 2 arrives in whole
# percent and is only present on devices that report it -- in the production
# cache only "Sensor do Quarto" carries it, so it is read per device rather
# than declared per device: a sensor that grows a humidity sensor later
# reports it without a code change.
#
# These are NOT verified against a live device. The DPS numbers come from
# cached readings only, and a wrong guess here fabricates a measurement the
# owner has no way to distrust. The range guards below are the last defence:
# a value outside a physically possible range is dropped, never reported.
SENSOR_DPS_TEMP = 1
SENSOR_DPS_HUMIDITY = 2
SENSOR_TEMP_SCALE = 10.0
SENSOR_TEMP_MIN, SENSOR_TEMP_MAX = 5.0, 45.0
SENSOR_HUMIDITY_MIN, SENSOR_HUMIDITY_MAX = 0.0, 100.0
STALE_AFTER_S = 300  # 5 minutes - after this the reading is considered stale

def _is_sensor(nickname):
    """Um sensor e um dispositivo que mede, nao um que liga e desliga.

    Antes, o schema vinha de duas entradas fixas na lista e qualquer sensor
    novo nao tinha entrada: "Sensor da Sala" devolvia 'on'/'off' e nunca
    temperatura, sem erro e sem aviso. O nome e o unico sinal disponivel.
    """
    return 'sensor' in nickname.lower()

def _switch_state(dps):
    """True, False or None, from whichever DPS is actually a boolean.

    Was `dps.get('1') or dps.get('20')`, which read any truthy value as "on".
    Measured on the real cache:

      Sensor da Sala          dps1=246   dps20=None   -> "on"
      Desumidificador do Armário dps1=False dps20=2367 -> "on"
      Exaustor da Sala        dps1=False dps20=None   -> "off"

    The sensor was "on" because 246 is a temperature in deci-celsius, and the
    dehumidifier was "on" while its own switch DPS said False, because 2367 is
    not a switch -- it is one of the energy-meter readings. So the whole house
    showed states nobody had measured, and a tile marked on also draws its
    animation and its readings, which is what made the second symptom look
    like the first.

    The type is the test, not the value. Tuya switch DPS arrive as JSON
    booleans; a measurement arrives as a number or a string. "middle", 246 and
    2367 are all truthy and none of them is a position.
    """
    for key in ('1', '20'):
        value = dps.get(key)
        if isinstance(value, bool):
            return value
    return None


def get_status_for_device(nickname):
    cached = _get_cached_status(nickname)
    if not cached or 'dps' not in cached: return {"state": "unreachable"}
    dps = cached['dps']; result = {}
    # A sensor has no switch position, so it gets no state at all rather than
    # one derived from its temperature.
    if _is_sensor(nickname):
        result['state'] = 'n/a'
    else:
        switch = _switch_state(dps)
        result['state'] = 'unreachable' if switch is None else ('on' if switch else 'off')
    power_raw = dps.get('19') or dps.get('104')
    if power_raw: result['power_w'] = float(power_raw) / 10.0

    # Age and staleness: sensors are useless without recência.
    ts = cached.get('timestamp')
    now = time.time()
    age_s = max(0, int(now - (ts or now)))
    result['age_s'] = age_s
    result['stale'] = age_s > STALE_AFTER_S

    # Measurements: only for a device the caller identifies as a sensor, and
    # only from a DPS the device actually reported. This keeps the previous
    # heuristic (guessing a DPS number from the value's range) removed --
    # that one fabricated temperature and humidity, OPS-005.
    if _is_sensor(nickname):
        raw_t = dps.get(str(SENSOR_DPS_TEMP))
        if raw_t is not None:
            try: temp = float(raw_t) / SENSOR_TEMP_SCALE
            except (ValueError, TypeError): temp = None
            if temp is not None and SENSOR_TEMP_MIN <= temp <= SENSOR_TEMP_MAX:
                result['temperature'] = round(temp, 1)
        raw_h = dps.get(str(SENSOR_DPS_HUMIDITY))
        if raw_h is not None:
            try: hum = float(raw_h)
            except (ValueError, TypeError): hum = None
            if hum is not None and SENSOR_HUMIDITY_MIN <= hum <= SENSOR_HUMIDITY_MAX:
                result['humidity'] = round(hum, 1)
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
                elif _matches_a_device_noun(user_prompt_lower, nick_l):
                    targets.append((nick, conf)); break

    # 3. Lógica Genérica (Fallback): "Liga o exaustor" -> Liga TODOS os exaustores
    if not targets:
        for noun in BASE_NOUNS:
            if noun in user_prompt_lower:
                for nick, conf in config.TUYA_DEVICES.items():
                    if noun in nick.lower(): targets.append((nick, conf))
                if targets: break

    # A thermometer is not something you switch. Both branches above can land
    # on a sensor -- "sensor" is a device noun and a room full of sensors
    # shares a room word -- and answering "Sensor do Quarto ligado" for a lamp
    # request is worse than saying nothing: it looks like it worked.
    if targets and action in ('on', 'off'):
        switched = [t for t in targets if not _is_sensor(t[0])]
        if not switched:
            logger.warning(
                "Refused to switch %s: it only measures", targets[0][0],
            )
            return f"O {targets[0][0]} mede, não se liga."
        targets = switched

    if not targets: return None

    # Processar STATUS
    if action == "status":
        target_nick, _ = targets[0]
        st = get_status_for_device(target_nick)
        if st['state'] == 'unreachable': return f"O {target_nick} não responde das sombras."
        # A sensor has no on/off. Saying "o Sensor do Quarto está on" is a
        # switch statement about a device that only measures.
        res_parts = [f"O {target_nick}"]
        if not _is_sensor(target_nick): res_parts.append(f"está {st['state']}")
        if 'temperature' in st: res_parts.append(f"com {st['temperature']} graus")
        if 'humidity' in st: res_parts.append(f"e {st['humidity']:g}% de humidade")
        if 'power_w' in st: res_parts.append(f"a gastar {st['power_w']} Watts")
        # The reading's age is the difference between a measurement and a lie.
        # The UI already dims a stale reading, but this path never said so: it
        # answered "24.6 graus" for a value 48h old, with no qualification.
        if st.get('stale'):
            age_min = max(1, int(st['age_s']) // 60)
            age = f"{age_min // 60}h" if age_min >= 60 else f"{age_min} minutos"
            res_parts.append(f"mas é uma leitura de há {age}")
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
