import config
import time

try:
    import tinytuya
except ImportError:
    print("AVISO: Biblioteca 'tinytuya' não encontrada. A skill_tuya será desativada.")
    print("Para ativar, corra: pip install tinytuya")
    pass

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"

# --- Ações ---
ON_TRIGGERS = ["liga", "ligar", "acende", "acender"]
OFF_TRIGGERS = ["desliga", "desligar", "apaga", "apagar"]
STATUS_TRIGGERS = ["como está", "estado", "temperatura", "humidade", "nível"]
DEBUG_TRIGGERS = ["diagnostico", "dps"] # <--- TRIGGER PARA DEBUG

# --- Nomes (Nouns) ---
BASE_NOUNS = [
    "sensor", "luz", "lâmpada", "desumidificador", 
    "exaustor", "tomada", "ficha", 
    "quarto", "sala", "wc" 
]

def _get_tuya_triggers():
    """Lê os nomes dos dispositivos do config para os triggers."""
    if hasattr(config, 'TUYA_DEVICES') and isinstance(config.TUYA_DEVICES, dict):
        device_nicknames = list(config.TUYA_DEVICES.keys())
        return BASE_NOUNS + device_nicknames + DEBUG_TRIGGERS
    return BASE_NOUNS + DEBUG_TRIGGERS

TRIGGERS = _get_tuya_triggers()


# --- Lógica Principal (Router) ---

def handle(user_prompt_lower, user_prompt_full):
    """
    Controla dispositivos Tuya (SmartLife) localmente.
    """
    if "tinytuya" not in globals():
        return "A skill Tuya está instalada, mas falta a biblioteca 'tinytuya'."
    if not hasattr(config, 'TUYA_DEVICES') or not config.TUYA_DEVICES:
        return None

    # 1. Encontrar a Ação (Prioridade: OFF > ON > DEBUG)
    is_off = any(action in user_prompt_lower for action in OFF_TRIGGERS)
    is_on = any(action in user_prompt_lower for action in ON_TRIGGERS)
    is_status = any(action in user_prompt_lower for action in STATUS_TRIGGERS)
    is_debug = any(action in user_prompt_lower for action in DEBUG_TRIGGERS)
    
    final_action = None
    if is_off:
        final_action = "OFF"
    elif is_on:
        final_action = "ON"
    elif is_debug:
        final_action = "DEBUG" # <--- Prioridade para DEBUG
    elif is_status:
        final_action = "STATUS"
    
    if not final_action:
        return None

    # 2. Encontrar Dispositivos Correspondentes
    matched_devices = []
    
    for nickname, details in config.TUYA_DEVICES.items():
        if nickname in user_prompt_lower:
            matched_devices.append((nickname, details))
            
    if not matched_devices:
        for noun in BASE_NOUNS:
            if noun in user_prompt_lower:
                for nickname, details in config.TUYA_DEVICES.items():
                    if noun in nickname: 
                         matched_devices.append((nickname, details))
                break 

    if len(matched_devices) == 0:
        return None 

    # 3. EXECUTAR AÇÃO
    
    # --- LÓGICA DE DEBUG (Separada para evitar erros de matching) ---
    if final_action == "DEBUG":
        if len(matched_devices) != 1:
            return "Por favor, especifica apenas um dispositivo para o diagnóstico (ex: diagnostico exaustor 1)."
            
        nickname, details = matched_devices[0]
        return _handle_debug_status(nickname, details)
    # --- FIM LÓGICA DE DEBUG ---


    if final_action in ["ON", "OFF"]:
        success_nicknames = []
        failed_reports = [] 
        action_str = final_action
        
        for nickname, details in matched_devices:
            # Determina o DPS a usar com base no nome do dispositivo
            dps_index = 1 # Padrão para tomadas/exaustores
            if "luz" in nickname or "lâmpada" in nickname:
                dps_index = 20 # DPS 20 é para as luzes
            
            try:
                _handle_switch(nickname, details, action_str, dps_index)
                success_nicknames.append(nickname)
            except Exception as e:
                failed_reports.append(f"{nickname} (Erro: {e})") 
        
        if not failed_reports:
            action_word = "ligados" if final_action == "ON" else "desligados"
            return f"{', '.join(success_nicknames).capitalize()} {action_word}."
        elif not success_nicknames:
            return f"Falha ao executar o comando. Detalhes: {', '.join(failed_reports)}."
        else:
            action_word = "ligados" if final_action == "ON" else "desligados"
            return f"Comando executado em {', '.join(success_nicknames)}, mas falhou em {', '.join(failed_reports)}."

    if final_action == "STATUS":
        if len(matched_devices) > 1:
            nomes = ", ".join([dev[0] for dev in matched_devices])
            return f"Encontrei vários dispositivos ({nomes}). Por favor, pede o estado de um de cada vez."
        
        nickname, details = matched_devices[0]
        try:
            return _handle_sensor(nickname, details, user_prompt_lower)
        except Exception as e:
            return str(e) 

    return None

# --- Processador de Debug ---
def _handle_debug_status(nickname, details):
    """Liga-se ao dispositivo e imprime o seu estado raw (DPSs)"""
    try:
        if "10.0.0.X" in details['ip']:
             raise Exception(f"IP não configurado (ainda é 10.0.0.X)")
             
        # Usamos o objeto genérico Device para obter o status raw
        d = tinytuya.Device(
            details['id'], details['ip'], details['key']
        )
        d.set_version(3.3) 
        data = d.status()
        
        # Imprime o output no journalctl
        print(f"\n*** DIAGNÓSTICO {nickname.upper()} ***")
        print(f"OUTPUT RAW (DPSs): {data}")
        print("************************************\n")
        
        if not data or 'dps' not in data:
            return f"Diagnóstico: O {nickname} respondeu, mas o payload está vazio ou ilegível. (DPS/Key incorreta?)"

        return f"Diagnóstico concluído. O estado RAW (DPSs) foi enviado para o log do sistema (journalctl)."

    except Exception as e:
        error_msg = f"Falha no Diagnóstico de '{nickname}' (IP: {details['ip']}). Erro: {e}"
        print(f"ERRO skill_tuya (Debug): {error_msg}")
        return f"Falha no Diagnóstico. Ocorreu um erro: {e}"

# --- Processador de Ligar/Desligar (Sem alterações) ---
def _handle_switch(nickname, details, action, dps_index):
    try:
        if "10.0.0.X" in details['ip']:
             raise Exception(f"IP não configurado (ainda é 10.0.0.X)")
             
        d = tinytuya.Device(
            details['id'], details['ip'], details['key']
        )
        d.set_version(3.3) 

        value = True if action == "ON" else False
        d.set_value(dps_index, value, nowait=True)

    except Exception as e:
        error_msg = f"Falha ao controlar '{nickname}' (IP: {details['ip']}). Erro: {e}"
        print(f"ERRO skill_tuya (Switch): {error_msg}")
        if "10.0.0.X" in str(e):
            raise Exception(f"IP não configurado")
        else:
            raise Exception(f"não respondeu (Timeout?)")

# --- Processador de Sensores (Protocolo 3.1) ---
def _handle_sensor(nickname, details, prompt):
    try:
        if "10.0.0.X" in details['ip']:
             raise Exception(f"IP não configurado (ainda é 10.0.0.X)")
             
        d = tinytuya.Device(
            details['id'], details['ip'], details['key']
        )
        d.set_version(3.1) 
        data = d.status()
        
        if not data or 'dps' not in data:
            return f"O {nickname} respondeu, mas não percebi os dados."

        dps = data['dps']
        temp_raw = dps.get('1') 
        humid_raw = dps.get('2')

        response = f"No {nickname}: "
        responses_found = 0
        
        if temp_raw is not None and ("temperatura" in prompt or "estado" in prompt):
            temp = float(temp_raw) / 10.0
            response += f"a temperatura é {temp} graus"
            responses_found += 1
            
        if humid_raw is not None and ("humidade" in prompt or "estado" in prompt):
            if responses_found > 0:
                response += " e "
            humid = int(humid_raw)
            response += f"a humidade é {humid} por cento"
            responses_found += 1

        if responses_found == 0:
            return f"Não consegui ler essa informação (Temperatura/Humidade) do {nickname}. (DPSs não são 1 e 2)."
            
        return response + "."

    except Exception as e:
        error_msg = f"Falha ao ler '{nickname}' (IP: {details['ip']}). Erro: {e}"
        print(f"ERRO skill_tuya (Sensor): {error_msg}")
        if "10.0.0.X" in str(e):
            raise Exception(f"IP não configurado")
        else:
            raise Exception(f"não respondeu (Timeout?)")
