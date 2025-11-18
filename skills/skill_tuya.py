import config
import time
import re

try:
    import tinytuya
except ImportError:
    print("AVISO: Biblioteca 'tinytuya' não encontrada. A skill_tuya será desativada.")
    print("Para ativar, corra: pip install tinytuya")
    pass

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
ACTIONS_ON = ["liga", "ligar", "acende", "acender"]
ACTIONS_OFF = ["desliga", "desligar", "apaga", "apagar"]
STATUS_TRIGGERS = ["como está", "estado", "temperatura", "humidade", "nível", "leitura"]
DEBUG_TRIGGERS = ["diagnostico", "dps"]
BASE_NOUNS = [
    "sensor", "luz", "lâmpada", "desumidificador", 
    "exaustor", "tomada", "ficha", 
    "quarto", "sala", "wc" 
]
VERSIONS_TO_TRY = [3.3, 3.1, 3.2, 3.4, 3.5]

def _get_tuya_triggers():
    """Lê os nomes dos dispositivos do config para os triggers."""
    all_actions = ACTIONS_ON + ACTIONS_OFF + STATUS_TRIGGERS + DEBUG_TRIGGERS
    
    if hasattr(config, 'TUYA_DEVICES') and isinstance(config.TUYA_DEVICES, dict):
        device_nicknames = list(config.TUYA_DEVICES.keys())
        return BASE_NOUNS + device_nicknames + all_actions
    return BASE_NOUNS + all_actions

TRIGGERS = _get_tuya_triggers()


def _try_connect_with_versioning(dev_id, dev_ip, dev_key):
    """
    Função auxiliar para tentar ligar-se a um dispositivo
    usando múltiplas versões de protocolo.
    """
    if 'x' in dev_ip.lower():
        return (None, f"IP inválido ('{dev_ip}')", None)
        
    print(f"Skill_Tuya: A tentar ligar a {dev_id} @ {dev_ip}...")
    
    last_error_payload = None
    last_error_code = None
    
    for version in VERSIONS_TO_TRY:
        try:
            d = tinytuya.Device(dev_id, dev_ip, dev_key)
            d.set_socketTimeout(2)
            d.set_version(version)
            
            # Sensor devices often don't respond to status() instantly if sleeping,
            # but for powered sensors it should be fine.
            status = d.status()
            
            if 'dps' in status:
                return (d, status, None)
            else:
                last_error_payload = status
                last_error_code = status.get('Err')
                if last_error_code == '905':
                    break # Erro de chave/dispositivo

        except Exception as e:
            last_error_payload = f"Erro de Rede: {e}"
            last_error_code = '901' 
            break 
            
    return (None, last_error_payload, last_error_code)


# --- Lógica Principal (Router) ---
def handle(user_prompt_lower, user_prompt_full):
    if "tinytuya" not in globals():
        return "A skill Tuya está instalada, mas falta a biblioteca 'tinytuya'."
    if not hasattr(config, 'TUYA_DEVICES') or not config.TUYA_DEVICES:
        return None

    is_off = any(action in user_prompt_lower for action in ACTIONS_OFF)
    is_on = any(action in user_prompt_lower for action in ACTIONS_ON)
    is_status = any(action in user_prompt_lower for action in STATUS_TRIGGERS)
    is_debug = any(action in user_prompt_lower for action in DEBUG_TRIGGERS)
    
    final_action = None
    if is_off: final_action = "OFF"
    elif is_on: final_action = "ON"
    elif is_debug: final_action = "DEBUG"
    elif is_status: final_action = "STATUS"
    
    if not final_action: return None

    # --- LÓGICA DE MATCHING (CASE-INSENSITIVE) ---
    matched_devices = []
    
    # Pass 1: Direct matches
    direct_matches = []
    for nickname, details in config.TUYA_DEVICES.items():
        if nickname.lower() in user_prompt_lower:
            direct_matches.append((nickname, details))

    if direct_matches:
        # Ordena por tamanho para apanhar o nome mais específico ("luz da sala" vs "sala")
        direct_matches.sort(key=lambda x: len(x[0]), reverse=True)
        best_match = direct_matches[0]
        matched_devices = [best_match]
        print(f"Skill_Tuya: Match directo encontrado: {best_match[0]}")

    else:
        # Pass 2: Noun-based matches
        nouns_in_prompt = [noun for noun in BASE_NOUNS if noun in user_prompt_lower]
        if nouns_in_prompt:
            print(f"Skill_Tuya: A procurar por nouns: {nouns_in_prompt}")
            for nickname, details in config.TUYA_DEVICES.items():
                if any(noun in nickname.lower() for noun in nouns_in_prompt):
                    matched_devices.append((nickname, details))

    if len(matched_devices) == 0: 
        return None 
    
    if final_action == "DEBUG":
        if len(matched_devices) != 1:
            return "Por favor, especifica apenas um dispositivo para o diagnóstico."
        nickname, details = matched_devices[0]
        return _handle_debug_status(nickname, details)

    if final_action in ["ON", "OFF"]:
        success_nicknames, failed_reports = [], []
        
        for nickname, details in matched_devices:
            
            # Ignora sensores para comandos de Ligar/Desligar
            if "sensor" in nickname.lower():
                print(f"Skill_Tuya: A ignorar '{nickname}' para ação ON/OFF (é um sensor).")
                continue 

            dev_ip = details.get('ip')
            if not dev_ip or 'x' in dev_ip.lower():
                print(f"Skill_Tuya: A ignorar '{nickname}' (IP inválido).")
                continue
            
            # CORREÇÃO: .lower() para detetar corretamente "Luz" vs "luz"
            dps_index = 20 if "luz" in nickname.lower() or "lâmpada" in nickname.lower() else 1
            
            try:
                _handle_switch(nickname, details, final_action, dps_index)
                success_nicknames.append(nickname)
            except Exception as e:
                failed_reports.append(f"{nickname} ({e})") 
        
        action_word = "a ligar" if final_action == "ON" else "a desligar"
        
        if not success_nicknames and not failed_reports:
            return None 

        if not failed_reports:
            return f"{', '.join(success_nicknames).capitalize()} {action_word}."
        elif not success_nicknames:
            return f"Falha ao executar: {', '.join(failed_reports)}."
        else:
            return f"Executado em {', '.join(success_nicknames)}, mas falhou em {', '.join(failed_reports)}."

    if final_action == "STATUS":
        if len(matched_devices) > 1:
            nomes = ", ".join([dev[0] for dev in matched_devices])
            return f"Encontrei vários dispositivos ({nomes}). Pede o estado de um de cada vez."
        
        nickname, details = matched_devices[0]
        try:
            return _handle_sensor(nickname, details, user_prompt_lower)
        except Exception as e:
            return str(e) 

    return None


# --- Processadores ---

def _handle_debug_status(nickname, details):
    """Liga-se ao dispositivo e imprime o seu estado raw (DPSs)"""
    print(f"*** DIAGNÓSTICO {nickname.upper()} ***")
    (d, result, err_code) = _try_connect_with_versioning(details['id'], details['ip'], details['key'])
    print(f"OUTPUT RAW (DPSs): {result}")
    print("************************************\n")

    if not d:
        return f"Diagnóstico: Falha ao ligar ao {nickname}. ({result})"
    return f"Diagnóstico concluído. O estado RAW foi enviado para o log."


def _handle_switch(nickname, details, action, dps_index):
    """Tenta ligar/desligar um dispositivo"""
    (d, status, err_code) = _try_connect_with_versioning(details['id'], details['ip'], details['key'])
    
    if not d:
        raise Exception(f"incontactável")

    try:
        value = True if action == "ON" else False
        print(f"Skill_Tuya: set_value({dps_index}, {value})")
        d.set_value(dps_index, value, nowait=True)
    except Exception as e:
        raise Exception(f"falha no comando ({e})")


def _handle_sensor(nickname, details, prompt):
    """
    Lê um sensor. Agora mais robusto a diferentes prompts.
    """
    (d, data, err_code) = _try_connect_with_versioning(details['id'], details['ip'], details['key'])

    if not d:
        return f"O {nickname} está incontactável."
        
    if 'dps' not in data:
         return f"O {nickname} respondeu sem dados válidos."

    dps = data['dps']
    
    # Compatibilidade: Sensores Antigos (1, 2) e Novos (102, 103)
    temp_raw = dps.get('1') or dps.get('102') 
    humid_raw = dps.get('2') or dps.get('103')

    response_parts = []
    
    # Flag para saber se o utilizador pediu algo específico ou "geral"
    wants_all = "como está" in prompt or "leitura" in prompt or "estado" in prompt or "nível" in prompt
    wants_temp = "temperatura" in prompt or wants_all
    wants_humid = "humidade" in prompt or wants_all

    if temp_raw is not None and wants_temp:
        # Alguns sensores devolvem 245 para 24.5, outros devolvem 24
        val = float(temp_raw)
        temp = val / 10.0 if val > 100 else val
        response_parts.append(f"a temperatura é {temp} graus")
        
    if humid_raw is not None and wants_humid:
        response_parts.append(f"a humidade é {int(humid_raw)} por cento")

    if not response_parts:
        return f"Não consegui ler os dados do {nickname}. DPSs disponíveis: {list(dps.keys())}"
        
    return f"No {nickname}, " + " e ".join(response_parts) + "."


# --- FUNÇÃO DE STATUS (API) ---

def get_status_for_device(nickname):
    """
    Chamada pela API (assistant.py) para obter estado.
    Agora suporta Sensores (devolve valores) e Switches (devolve on/off).
    """
    if not hasattr(config, 'TUYA_DEVICES') or nickname not in config.TUYA_DEVICES:
        return {"state": "unreachable"}

    details = config.TUYA_DEVICES[nickname]
    
    (d, status, err_code) = _try_connect_with_versioning(
        details['id'], details['ip'], details['key']
    )
    
    if not d:
        return {"state": "unreachable"}

    # 1. LÓGICA PARA SENSORES
    if "sensor" in nickname.lower():
        dps = status.get('dps', {})
        temp_raw = dps.get('1') or dps.get('102')
        humid_raw = dps.get('2') or dps.get('103')
        
        result = {"state": "on"} # "on" aqui significa "online/respondendo"
        
        if temp_raw is not None:
            val = float(temp_raw)
            result["temperature"] = val / 10.0 if val > 100 else val
            
        if humid_raw is not None:
            result["humidity"] = int(humid_raw)
            
        print(f"Skill_Tuya (API): Leitura Sensor {nickname}: {result}")
        return result

    # 2. LÓGICA PARA SWITCHES / LUZES
    # CORREÇÃO: .lower() para garantir que "Luz" funciona
    dps_index_str = "20" if "luz" in nickname.lower() or "lâmpada" in nickname.lower() else "1"
    
    current_state = status.get('dps', {}).get(dps_index_str)
    
    if current_state is True:
        return {"state": "on"}
    elif current_state is False:
        return {"state": "off"}
    else:
        print(f"Skill_Tuya (API): Estado não-booleano para {nickname} (DPS {dps_index_str}): {current_state}")
        return {"state": "unreachable"}
