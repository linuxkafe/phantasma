import config

try:
    from miio import DeviceException
    from miio import ViomiVacuum
    from miio import Yeelight
    
except ImportError:
    print("AVISO: Biblioteca 'python-miio' não encontrada. A skill_xiaomi será desativada.")
    print("Para ativar, corra: pip install python-miio")
    pass

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"

LAMP_OBJECTS = ["candeeiro", "luz da mesinha", "abajur"]
LAMP_ON_TRIGGERS = ["liga", "ligar", "acende", "acender"]
LAMP_OFF_TRIGGERS = ["desliga", "desligar", "apaga", "apagar"]

VACUUM_OBJECTS = ["aspirador", "robot", "viomi"]
VACUUM_START_TRIGGERS = ["aspira", "limpa", "começa", "inicia"]
VACUUM_STOP_TRIGGERS = ["para", "pára", "pausa"]
VACUUM_HOME_TRIGGERS = ["base", "casa", "volta", "carrega", "recolhe"]

TRIGGERS = LAMP_OBJECTS + VACUUM_OBJECTS

# --- Lógica Principal (Router) ---

def handle(user_prompt_lower, user_prompt_full):
    """
    Router principal da skill Xiaomi.
    """
    if "Yeelight" not in globals() or "ViomiVacuum" not in globals():
        print("ERRO skill_xiaomi: Biblioteca 'python-miio' não importada ou incompleta.")
        return "A skill Xiaomi está instalada, mas falta a biblioteca 'python-miio'."

    if any(obj in user_prompt_lower for obj in LAMP_OBJECTS):
        print("Skill Xiaomi: Intenção detetada para o Candeeiro.")
        return _handle_lamp(user_prompt_lower)

    if any(obj in user_prompt_lower for obj in VACUUM_OBJECTS):
        print("Skill Xiaomi: Intenção detetada para o Aspirador.")
        return _handle_vacuum(user_prompt_lower)

    return None

# --- Processador do Candeeiro (ORDEM CORRIGIDA) ---

def _handle_lamp(prompt):
    """ Processa os comandos do candeeiro (Ligar/Desligar) """

    if not hasattr(config, 'XIAOMI_LAMP_IP') or not config.XIAOMI_LAMP_TOKEN:
        return "O candeeiro está configurado na skill, mas falta o IP ou Token no config.py."
        
    ip = config.XIAOMI_LAMP_IP
    token = config.XIAOMI_LAMP_TOKEN

    try:
        dev = Yeelight(ip, token)
        
        # --- [ CORREÇÃO: VERIFICAR 'OFF' PRIMEIRO ] ---
        if any(action in prompt for action in LAMP_OFF_TRIGGERS):
            dev.off()
            return "Candeeiro desligado."
        # --- [ FIM DA CORREÇÃO ] ---
        
        if any(action in prompt for action in LAMP_ON_TRIGGERS):
            dev.on()
            return "Candeeiro ligado."

    except DeviceException as e:
        print(f"ERRO skill_xiaomi (Candeeiro): Falha ao ligar a {ip}: {e}")
        return "Não consegui comunicar com o candeeiro. O IP ou o Token estão corretos?"
    except Exception as e:
        print(f"ERRO inesperado skill_xiaomi (Candeeiro): {e}")
        return "Ocorreu um erro inesperado na skill do candeeiro."
        
    return None 

# --- Processador do Aspirador (ORDEM CORRIGIDA) ---

def _handle_vacuum(prompt):
    """ Processa os comandos do aspirador (Limpar, Parar, Casa) """
    
    if not hasattr(config, 'XIAOMI_VACUUM_IP') or not config.XIAOMI_VACUUM_TOKEN:
        return "O aspirador está configurado na skill, mas falta o IP ou Token no config.py."
        
    ip = config.XIAOMI_VACUUM_IP
    token = config.XIAOMI_VACUUM_TOKEN
    
    try:
        dev = ViomiVacuum(ip, token)
        
        # --- [ CORREÇÃO: VERIFICAR 'HOME' E 'STOP' PRIMEIRO ] ---
        if any(action in prompt for action in VACUUM_HOME_TRIGGERS):
            dev.home()
            return "Aspirador a voltar à base."
        
        if any(action in prompt for action in VACUUM_STOP_TRIGGERS):
            dev.stop()
            return "Aspirador parado."
        # --- [ FIM DA CORREÇÃO ] ---
        
        if any(action in prompt for action in VACUUM_START_TRIGGERS):
            dev.start()
            return "Aspirador a iniciar a limpeza."

    except DeviceException as e:
        print(f"ERRO skill_xiaomi (Aspirador): Falha ao ligar a {ip}: {e}")
        return "Não consegui comunicar com o aspirador."
    except Exception as e:
        print(f"ERRO inesperado skill_xiaomi (Aspirador): {e}")
        return "Ocorreu um erro inesperado na skill do aspirador."

    return None

# --- NOVA FUNÇÃO DE STATUS ---

def get_status_for_device(nickname):
    """
    Função pública chamada pelo assistant.py para obter o estado de um dispositivo.
    Retorna: {"state": "on" | "off" | "unreachable"}
    """
    # Esta skill só trata do candeeiro como um toggle
    if nickname not in LAMP_OBJECTS:
        return {"state": "unreachable"} # Não é um candeeiro que esta skill conheça

    if "Yeelight" not in globals():
        return {"state": "unreachable"} # Biblioteca não carregada

    if not hasattr(config, 'XIAOMI_LAMP_IP') or not config.XIAOMI_LAMP_TOKEN:
        return {"state": "unreachable"} # Não configurado
        
    ip = config.XIAOMI_LAMP_IP
    token = config.XIAOMI_LAMP_TOKEN

    try:
        dev = Yeelight(ip, token)
        # Pedimos apenas a propriedade "power"
        props = dev.get_properties(['power'])
        
        if props and props[0] == 'on':
            return {"state": "on"}
        else:
            return {"state": "off"} # Inclui 'off' e qualquer resposta inesperada

    except DeviceException as e:
        print(f"ERRO skill_xiaomi (get_status): Falha ao ligar a {ip}: {e}")
        return {"state": "unreachable"}
    except Exception as e:
        print(f"ERRO inesperado skill_xiaomi (get_status): {e}")
        return {"state": "unreachable"}
