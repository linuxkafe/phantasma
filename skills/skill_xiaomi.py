import config
import time # NOVO: Necessário para o polling e timestamps
import threading # NOVO: Necessário para o daemon

try:
    from miio import DeviceException
    from miio import ViomiVacuum
    from miio import Yeelight
except ImportError:
    print("AVISO: Biblioteca 'python-miio' não encontrada. A skill_xiaomi será desativada.")
    class Yeelight: pass
    class ViomiVacuum: pass
    pass

# --- CACHE GLOBAL (Em Memória) ---
MIIO_CACHE = {} 
POLL_INTERVAL = 60 # Poll a cada 60 segundos
# -----------------------------------

# --- Listas de Palavras-Chave para Deteção de Tipo ---
KEYWORDS_LAMP = ["candeeiro", "luz", "abajur", "lâmpada"]
KEYWORDS_VACUUM = ["aspirador", "robot", "viomi"]

# --- Comandos de Ação ---
LAMP_ON = ["liga", "ligar", "acende", "acender"]
LAMP_OFF = ["desliga", "desligar", "apaga", "apagar"]

VACUUM_START = ["aspira", "limpa", "começa", "inicia"]
VACUUM_STOP = ["para", "pára", "pausa"]
VACUUM_HOME = ["base", "casa", "volta", "carrega", "recolhe"]

# --- Configuração da Skill (Triggers Dinâmicos) ---
TRIGGER_TYPE = "contains"

def _get_triggers():
    """ Gera triggers baseados nos dispositivos configurados no config.py """
    device_names = []
    if hasattr(config, 'MIIO_DEVICES') and isinstance(config.MIIO_DEVICES, dict):
        device_names = list(config.MIIO_DEVICES.keys())
    
    return device_names + LAMP_ON + LAMP_OFF + VACUUM_START + VACUUM_STOP + VACUUM_HOME

TRIGGERS = _get_triggers()


# --- Helpers ---

def _detect_device_type(nickname):
    """ Retorna 'lamp' ou 'vacuum' baseado no nome do dispositivo. """
    nick_lower = nickname.lower()
    if any(k in nick_lower for k in KEYWORDS_LAMP):
        return 'lamp'
    if any(k in nick_lower for k in KEYWORDS_VACUUM):
        return 'vacuum'
    return None

# --- DAEMON/POLLING LOGIC ---

def _update_cache(nickname, state):
    """ Atualiza o estado na cache global em memória. """
    MIIO_CACHE[nickname] = {"state": state, "timestamp": time.time()}

def _poll_xiaomi_status():
    """ Tenta comunicar com cada dispositivo e atualiza a cache. """
    if not hasattr(config, 'MIIO_DEVICES'): return

    for nickname, details in config.MIIO_DEVICES.items():
        ip = details.get('ip'); token = details.get('token'); dev_type = _detect_device_type(nickname)
        if not ip or not token or not dev_type: continue
        
        try:
            if dev_type == 'lamp':
                # Ligação direta para obter o estado
                dev = Yeelight(ip, token)
                props = dev.get_properties(['power'])
                is_on = props and props[0] == 'on'
                _update_cache(nickname, 'on' if is_on else 'off')

            elif dev_type == 'vacuum':
                # Ligação direta para obter o estado
                dev = ViomiVacuum(ip, token)
                status = dev.status()
                # Se estiver a limpar ou a carregar (assumimos "on")
                is_on = status.is_on 
                _update_cache(nickname, 'on' if is_on else 'off')
            
            print(f"[Xiaomi Daemon] Cache atualizada para {nickname}: {MIIO_CACHE[nickname]['state']}")

        except Exception as e:
            # Em caso de falha (timeout), a cache não é atualizada. 
            # O estado antigo persiste, evitando o flicker.
            print(f"[Xiaomi Daemon] ERRO polling {nickname}: {e}")
            pass 

def _poll_loop():
    """ Loop principal do Daemon Xiaomi (Roda no background). """
    while True:
        _poll_xiaomi_status()
        time.sleep(POLL_INTERVAL)

def init_skill_daemon():
    """ Starts the polling thread (Required by assistant.py). """
    if not hasattr(config, 'MIIO_DEVICES') or not config.MIIO_DEVICES: return
    print(f"[Xiaomi Daemon] A iniciar polling a cada {POLL_INTERVAL} segundos...")
    # Corre a thread de polling em background
    threading.Thread(target=_poll_loop, daemon=True).start()


# --- Router Principal (Mantido) ---

def handle(user_prompt_lower, user_prompt_full):
    """
    Descobre qual o dispositivo mencionado e encaminha para a função correta.
    (O código de controlo direto (on/off/start) é mantido aqui)
    """
    if not hasattr(config, 'MIIO_DEVICES') or not config.MIIO_DEVICES:
        return None

    matched_device = None
    matched_name = ""

    for name, details in config.MIIO_DEVICES.items():
        if name.lower() in user_prompt_lower:
            matched_name = name
            matched_device = details
            break 
    
    if not matched_device:
        return None 

    dev_type = _detect_device_type(matched_name)
    
    if not dev_type:
        print(f"Skill Xiaomi: Dispositivo '{matched_name}' encontrado, mas não sei se é luz ou aspirador.")
        return None

    ip = matched_device.get('ip')
    token = matched_device.get('token')
    
    if not ip or not token:
        return f"O dispositivo {matched_name} não tem IP ou Token configurado."

    if dev_type == 'lamp':
        return _handle_lamp(matched_name, ip, token, user_prompt_lower)
    elif dev_type == 'vacuum':
        return _handle_vacuum(matched_name, ip, token, user_prompt_lower)

    return None


# --- Controladores Específicos (Mantidos) ---

def _handle_lamp(name, ip, token, prompt):
    try:
        dev = Yeelight(ip, token)
        
        if any(action in prompt for action in LAMP_OFF):
            dev.off()
            # Tenta atualizar cache imediatamente após a ação bem-sucedida
            _update_cache(name, 'off') 
            return f"{name.capitalize()} desligado."
        
        if any(action in prompt for action in LAMP_ON):
            dev.on()
            _update_cache(name, 'on')
            return f"{name.capitalize()} ligado."

    except DeviceException as e:
        print(f"ERRO Xiaomi ({name}): {e}")
        return f"Não consegui comunicar com o {name}."
    except Exception as e:
        print(f"ERRO Crítico Xiaomi ({name}): {e}")
        return f"Ocorreu um erro ao controlar o {name}."
    
    return None

def _handle_vacuum(name, ip, token, prompt):
    try:
        dev = ViomiVacuum(ip, token)
        
        if any(action in prompt for action in VACUUM_HOME):
            dev.home()
            # O estado será "off" (na base) ou "on" (a voltar)
            return f"{name.capitalize()} a voltar à base."
        
        if any(action in prompt for action in VACUUM_STOP):
            dev.stop()
            # O estado deve ser "off" (parado)
            return f"{name.capitalize()} parado."
        
        if any(action in prompt for action in VACUUM_START):
            dev.start()
            # O estado deve ser "on" (a limpar)
            return f"{name.capitalize()} a iniciar limpeza."

    except DeviceException as e:
        print(f"ERRO Xiaomi ({name}): {e}")
        return f"Não consegui comunicar com o {name}."
    except Exception as e:
        print(f"ERRO Crítico Xiaomi ({name}): {e}")
        return f"Ocorreu um erro ao controlar o {name}."

    return None


# --- API: Status para a Interface Web (Lê da Cache!) ---

def get_status_for_device(nickname):
    """
    Obtém o estado (ON/OFF) lendo da cache em memória.
    """
    # Esta função é muito leve, pois só lê um dicionário.
    if nickname in MIIO_CACHE:
        return MIIO_CACHE[nickname]
    return {"state": "unreachable"}
