import config
import asyncio
import json
import os
import time
import threading
import traceback

# --- CONFIGURAÇÃO ---
# ALTERADO: Caminho da cache para a pasta correta
CACHE_FILE = "/opt/phantasma/cache/ewelink_cache.json"
POLL_INTERVAL = 60  

TRIGGER_TYPE = "contains"
TRIGGERS = ["carregador", "carro", "ewelink", "tomada do carro"]

ACTIONS_ON = ["liga", "ligar", "acende", "ativa", "inicia", "põe a carregar"]
ACTIONS_OFF = ["desliga", "desligar", "apaga", "desativa", "para", "pára"]
STATUS_TRIGGERS = ["consumo", "gastar", "leitura", "quanto", "estado", "como está", "a carregar", "carregar"]

try:
    import ewelink
except ImportError:
    ewelink = None
    print("[eWeLink] ERRO: Biblioteca não encontrada.")

# --- Helpers de Cache ---

def _ensure_permissions():
    """ Garante que a pasta cache existe e tem permissões. """
    try:
        directory = os.path.dirname(CACHE_FILE)
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        if os.path.exists(CACHE_FILE):
            os.chmod(CACHE_FILE, 0o666)
    except: pass

def _save_cache(data):
    try:
        _ensure_permissions()
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, 'w') as f: json.dump(data, f)
        os.replace(tmp, CACHE_FILE)
        os.chmod(CACHE_FILE, 0o666)
    except Exception as e:
        print(f"[eWeLink] Erro ao escrever cache: {e}")

def _get_cached_data(device_id=None):
    if not os.path.exists(CACHE_FILE): return {} if device_id is None else None
    try:
        with open(CACHE_FILE, 'r') as f:
            data = json.load(f)
            if device_id:
                return data.get(device_id)
            return data
    except: return {} if device_id is None else None

def _update_local_state_optimistic(target_id, new_state):
    """ 
    Força a atualização imediata do ficheiro de cache sem esperar pela cloud.
    Isto resolve o delay na UI.
    """
    try:
        data = _get_cached_data() # Lê tudo
        if target_id in data:
            data[target_id]['state'] = new_state
            # Se desligarmos, assumimos que o consumo cai para 0 imediatamente
            if new_state == "off":
                data[target_id]['power'] = 0
            _save_cache(data)
            print(f"[eWeLink] Cache atualizada localmente para {new_state} (Otimista)")
    except Exception as e:
        print(f"[eWeLink] Erro update otimista: {e}")

# --- Lógica do Daemon (Polling em Background) ---

async def _poll_task():
    if not ewelink: return

    region = getattr(config, 'EWELINK_REGION', 'eu')
    
    try:
        client = ewelink.Client(
            password=config.EWELINK_PASSWORD, 
            email=config.EWELINK_USERNAME, 
            region=region
        )
        await client.login()
        
        if client.devices:
            # Lê cache existente para não perder dados se a API falhar parcialmente
            cache_data = _get_cached_data() or {}
            
            for device in client.devices:
                dev_id = getattr(device, 'deviceid', None)
                if not dev_id: dev_id = getattr(device, 'device_id', None)
                if not dev_id: dev_id = getattr(device, 'id', None)
                if not dev_id and hasattr(device, 'raw_data'):
                    dev_id = device.raw_data.get('deviceid')
                
                if not dev_id: continue

                params = getattr(device, 'params', {}) or {}
                
                # Alguns dispositivos reportam power em string, outros float
                power = params.get('power')
                current = params.get('current')
                voltage = params.get('voltage')

                cache_data[dev_id] = {
                    "name": device.name,
                    "state": "on" if device.state else "off",
                    "online": device.online,
                    "timestamp": time.time(),
                    "power": power,
                    "current": current,
                    "voltage": voltage
                }
            
            if cache_data:
                _save_cache(cache_data)
            
    except Exception as e:
        print(f"[eWeLink Daemon] Erro: {e}")
    finally:
        try:
            if client and client.http and client.http.session and not client.http.session.closed:
                await client.http.session.close()
        except: pass

def _daemon_loop():
    # Executa imediatamente no arranque
    try: asyncio.run(_poll_task())
    except: pass

    while True:
        time.sleep(POLL_INTERVAL)
        try:
            asyncio.run(_poll_task())
        except Exception as e:
            print(f"[eWeLink Daemon] Crash: {e}")

def init_skill_daemon():
    if ewelink:
        print("[eWeLink] A iniciar polling em background...")
        t = threading.Thread(target=_daemon_loop, daemon=True)
        t.start()

# --- Ações de Controlo (Imediatas) ---

async def _execute_control_action(action, target_id):
    try:
        region = getattr(config, 'EWELINK_REGION', 'eu')
        client = ewelink.Client(password=config.EWELINK_PASSWORD, email=config.EWELINK_USERNAME, region=region)
        await client.login()
        
        device = client.get_device(target_id)
        
        if not device: 
            await client.http.session.close()
            return {"success": False, "error": "Dispositivo não encontrado."}
        
        # 1. Envia comando para a Cloud
        if action == "on": await device.on()
        elif action == "off": await device.off()
        
        # 2. Fecha conexão
        await client.http.session.close()
        
        # 3. ATUALIZAÇÃO OTIMISTA (CRÍTICO PARA A UI)
        # Escreve logo no ficheiro cache que o estado mudou, sem esperar pelo poll
        _update_local_state_optimistic(target_id, action)
        
        # 4. Agenda um poll real para daqui a 5 segundos (dar tempo à cloud de atualizar)
        threading.Timer(5.0, lambda: asyncio.run(_poll_task())).start()
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- APIs da Skill ---

def get_status_for_device(nickname):
    if nickname not in getattr(config, 'EWELINK_DEVICES', {}): return {"state": "unreachable"}
    
    conf = config.EWELINK_DEVICES[nickname]
    target_id = conf.get("device_id") if isinstance(conf, dict) else None
    
    data = _get_cached_data(target_id)
    if not data: return {"state": "unreachable"}
    
    ui_res = {"state": data.get("state", "off")}
    
    # Tratamento seguro de Watts
    raw_power = data.get("power")
    if raw_power:
        try: 
            ui_res["power_w"] = float(raw_power)
        except: pass
        
    return ui_res

def handle(user_prompt_lower, user_prompt_full):
    if not hasattr(config, 'EWELINK_DEVICES'): return None

    action = None
    if any(x in user_prompt_lower for x in ACTIONS_OFF): action = "off"
    elif any(x in user_prompt_lower for x in ACTIONS_ON): action = "on"
    elif any(x in user_prompt_lower for x in STATUS_TRIGGERS): action = "status"
    
    if not action: return None

    target_conf = None; target_nickname = ""
    for nickname, conf in config.EWELINK_DEVICES.items():
        if nickname.lower() in user_prompt_lower: 
            target_conf = conf; target_nickname = nickname; break
    
    if not target_conf and "carro" in user_prompt_lower:
        if config.EWELINK_DEVICES:
            target_nickname = list(config.EWELINK_DEVICES.keys())[0]
            target_conf = config.EWELINK_DEVICES[target_nickname]

    if not target_conf: return None
    target_id = target_conf.get("device_id")

    if action == "status":
        data = _get_cached_data(target_id)
        if not data: return f"A recolher dados do {target_nickname}..."
        
        if "carregar" in user_prompt_lower:
            try: power = float(data.get('power', 0))
            except: power = 0.0
            if power > 10: return f"Sim, a carregar a {power} Watts."
            elif power < 5: return f"Não, não está a carregar."
            else: return f"Ligado mas em espera ({power} Watts)."

        parts = [f"O {target_nickname} está {data.get('state')}"]
        if data.get('power'): parts.append(f"a gastar {data['power']} Watts")
        return ", ".join(parts) + "."

    print(f"eWeLink: A executar '{action}' em '{target_nickname}'...")
    res = asyncio.run(_execute_control_action(action, target_id))
    
    if not res.get("success"): return f"Erro: {res.get('error')}"
    return f"{target_nickname.capitalize()} {'ligado' if action=='on' else 'desligado'}."
