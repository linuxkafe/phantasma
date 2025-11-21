import logging
import httpx
import time
import config

LOG = logging.getLogger(__name__)

# --- Configuração ---
TRIGGER_TYPE = "contains"

# 1. Define os Triggers (Base + Dispositivos do Config)
BASE_TRIGGERS = ["cloogy", "kiome", "lista", "listar", "consumo", "gastar", "leitura", "quanto"]

def _get_triggers():
    if hasattr(config, 'CLOOGY_DEVICES') and isinstance(config.CLOOGY_DEVICES, dict):
        return BASE_TRIGGERS + list(config.CLOOGY_DEVICES.keys())
    return BASE_TRIGGERS

TRIGGERS = _get_triggers()

# --- Estado Global (Simples) ---
# Guardamos o token numa variável global para reutilizar
CURRENT_TOKEN = None

# --- Funções de API (Lógica Pura) ---

def _get_headers():
    return {
        "Authorization": f"VPS {CURRENT_TOKEN}",
        "Accept": "application/json",
        "User-Agent": "RestSharp/106.0.0.0"
    }

def _login():
    """ Renova o token global usando as credenciais do config. """
    global CURRENT_TOKEN
    
    user = getattr(config, 'CLOOGY_USERNAME', None)
    pwd = getattr(config, 'CLOOGY_PASSWORD', None)
    
    if not user or not pwd:
        LOG.error("Cloogy: Falta user/pass no config.")
        return False

    try:
        # Endpoint de login (201 Created)
        resp = httpx.post(
            "https://api.cloogy.com/api/1.4/sessions",
            json={"Login": user, "Password": pwd},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=10, verify=False
        )
        
        if resp.status_code in [200, 201]:
            CURRENT_TOKEN = resp.json().get("Token")
            return True
            
    except Exception as e:
        LOG.error(f"Cloogy: Erro login: {e}")
    
    return False

def _ensure_auth():
    """ Garante que temos token. Se não, faz login. """
    if not CURRENT_TOKEN:
        return _login()
    return True

def _get_instant_consumption(device_id):
    """ Pede o consumo instantâneo (Watts) usando o endpoint que descobrimos. """
    if not _ensure_auth(): return None
    
    try:
        now = int(time.time() * 1000)
        start = now - (30 * 60 * 1000) # Últimos 30 min
        
        url = "https://api.cloogy.com/api/1.4/consumptions/instant"
        params = {
            "from": start, "to": now,
            "tags": f"[{device_id}]", 
            "includeForecast": "False"
        }
        
        resp = httpx.get(url, params=params, headers=_get_headers(), timeout=10, verify=False)
        
        # Se deu 401 (Expirado), renova e tenta 1 vez
        if resp.status_code == 401:
            if _login():
                resp = httpx.get(url, params=params, headers=_get_headers(), timeout=10, verify=False)
        
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, list):
                # O valor vem em kW (ex: 0.430). Convertemos para Watts.
                kw_val = data[-1].get("Read")
                if kw_val is not None:
                    return float(kw_val) * 1000
                    
    except Exception as e:
        LOG.error(f"Cloogy: Erro leitura: {e}")
        
    return None

def _set_state(device_id, state_on):
    """ Liga/Desliga (requer consentimento na cloud). """
    if not _ensure_auth(): return False
    try:
        val = "1" if state_on else "0"
        url = f"https://api.cloogy.com/api/1.4/tag/{device_id}"
        
        resp = httpx.put(url, json={"Value": val}, headers=_get_headers(), timeout=5, verify=False)
        
        if resp.status_code == 401:
            if _login():
                resp = httpx.put(url, json={"Value": val}, headers=_get_headers(), timeout=5, verify=False)
                
        return resp.status_code in [200, 204]
    except: return False


# --- Funções Principais (Interface do Phantasma) ---

def get_status_for_device(nickname):
    """ 
    Chamada pelo assistant.py para mostrar o estado na Web UI.
    """
    if not hasattr(config, 'CLOOGY_DEVICES') or nickname not in config.CLOOGY_DEVICES:
        return {"state": "unreachable"}
        
    dev_id = config.CLOOGY_DEVICES[nickname]
    
    # Tenta ler o valor
    watts = _get_instant_consumption(dev_id)
    
    if watts is None:
        return {"state": "unreachable"}
        
    # Formatação para a UI
    # Se for o 'forno' (atuador), queremos ON/OFF. Se for 'casa', queremos Watts.
    if "forno" in nickname.lower():
        return {"state": "on" if watts > 0 else "off"}
    else:
        return {
            "state": "on",
            "power_w": round(watts, 1) # Mostra os Watts a laranja
        }

def handle(user_prompt_lower, user_prompt_full):
    """ Router de comandos de voz. """
    
    if not hasattr(config, 'CLOOGY_DEVICES'): return None

    # 1. Identificar o Dispositivo
    target_id = None
    target_name = ""
    
    for name, dev_id in config.CLOOGY_DEVICES.items():
        if name in user_prompt_lower:
            target_id = dev_id; target_name = name; break
            
    # Inteligência: "consumo" = "casa"
    if not target_id and any(x in user_prompt_lower for x in ["gastar", "consumo", "casa"]):
        if "casa" in config.CLOOGY_DEVICES: 
            target_id = config.CLOOGY_DEVICES["casa"]
            target_name = "casa"

    if not target_id: return None

    # 2. Executar Ação
    # Leitura
    if any(x in user_prompt_lower for x in ["quanto", "consumo", "leitura", "gastar"]):
        val = _get_instant_consumption(target_id)
        if val is not None:
            return f"O {target_name} está a gastar {int(val)} Watts."
        return f"Não consegui ler o {target_name}."

    # Controlo (On/Off)
    is_on = any(x in user_prompt_lower for x in ["liga", "acende"])
    is_off = any(x in user_prompt_lower for x in ["desliga", "apaga"])
    
    if is_on:
        return f"Ok." if _set_state(target_id, True) else "Erro (falta consentimento?)."
    elif is_off:
        return f"Ok." if _set_state(target_id, False) else "Erro (falta consentimento?)."

    return None
