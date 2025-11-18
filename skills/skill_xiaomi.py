import config

try:
    from miio import DeviceException
    from miio import ViomiVacuum
    from miio import Yeelight
except ImportError:
    print("AVISO: Biblioteca 'python-miio' não encontrada. A skill_xiaomi será desativada.")
    pass

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
    
    # Junta os nomes dos dispositivos + comandos de ação
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


# --- Router Principal ---

def handle(user_prompt_lower, user_prompt_full):
    """
    Descobre qual o dispositivo mencionado e encaminha para a função correta.
    """
    if not hasattr(config, 'MIIO_DEVICES') or not config.MIIO_DEVICES:
        return None

    # 1. Encontrar o dispositivo no prompt
    matched_device = None
    matched_name = ""

    # Procura por matches diretos (o nome do dispositivo está na frase)
    for name, details in config.MIIO_DEVICES.items():
        if name.lower() in user_prompt_lower:
            matched_name = name
            matched_device = details
            break # Assume-se o primeiro match
    
    if not matched_device:
        return None # Nenhum dispositivo Xiaomi encontrado na frase

    # 2. Detetar o tipo (Lâmpada ou Aspirador)
    dev_type = _detect_device_type(matched_name)
    
    if not dev_type:
        print(f"Skill Xiaomi: Dispositivo '{matched_name}' encontrado, mas não sei se é luz ou aspirador.")
        return None

    # 3. Encaminhar
    ip = matched_device.get('ip')
    token = matched_device.get('token')
    
    if not ip or not token:
        return f"O dispositivo {matched_name} não tem IP ou Token configurado."

    if dev_type == 'lamp':
        return _handle_lamp(matched_name, ip, token, user_prompt_lower)
    elif dev_type == 'vacuum':
        return _handle_vacuum(matched_name, ip, token, user_prompt_lower)

    return None


# --- Controladores Específicos ---

def _handle_lamp(name, ip, token, prompt):
    try:
        dev = Yeelight(ip, token)
        
        if any(action in prompt for action in LAMP_OFF):
            dev.off()
            return f"{name.capitalize()} desligado."
        
        if any(action in prompt for action in LAMP_ON):
            dev.on()
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
            return f"{name.capitalize()} a voltar à base."
        
        if any(action in prompt for action in VACUUM_STOP):
            dev.stop()
            return f"{name.capitalize()} parado."
        
        if any(action in prompt for action in VACUUM_START):
            dev.start()
            return f"{name.capitalize()} a iniciar limpeza."

    except DeviceException as e:
        print(f"ERRO Xiaomi ({name}): {e}")
        return f"Não consegui comunicar com o {name}."
    except Exception as e:
        print(f"ERRO Crítico Xiaomi ({name}): {e}")
        return f"Ocorreu um erro ao controlar o {name}."

    return None


# --- API: Status para a Interface Web ---

def get_status_for_device(nickname):
    """
    Obtém o estado (ON/OFF) para desenhar o botão na Web UI.
    """
    if not hasattr(config, 'MIIO_DEVICES') or nickname not in config.MIIO_DEVICES:
        return {"state": "unreachable"}

    details = config.MIIO_DEVICES[nickname]
    ip = details.get('ip')
    token = details.get('token')
    dev_type = _detect_device_type(nickname)

    if not ip or not token:
        return {"state": "unreachable"}

    try:
        if dev_type == 'lamp':
            dev = Yeelight(ip, token)
            props = dev.get_properties(['power'])
            if props and props[0] == 'on':
                return {"state": "on"}
            return {"state": "off"}

        elif dev_type == 'vacuum':
            dev = ViomiVacuum(ip, token)
            status = dev.status()
            # Se estiver a limpar, consideramos "on". Na base/pausa é "off".
            if status.is_on: 
                return {"state": "on"}
            return {"state": "off"}
            
    except Exception as e:
        print(f"ERRO Xiaomi Status ({nickname}): {e}")
        return {"state": "unreachable"}
        
    return {"state": "unreachable"}
