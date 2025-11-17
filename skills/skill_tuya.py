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
STATUS_TRIGGERS = ["como está", "estado", "temperatura", "humidade", "nível"]
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
    
    # --- ESTA É A LINHA CORRIGIDA ---
    if hasattr(config, 'TUYA_DEVICES') and isinstance(config.TUYA_DEVICES, dict):
    # ---------------------------------
        device_nicknames = list(config.TUYA_DEVICES.keys())
        return BASE_NOUNS + device_nicknames + all_actions
    return BASE_NOUNS + all_actions

TRIGGERS = _get_tuya_triggers()


def _try_connect_with_versioning(dev_id, dev_ip, dev_key):
    """
    Função auxiliar para tentar ligar-se a um dispositivo
    usando múltiplas versões de protocolo.
    
    Retorna (objeto_tinytuya, status_obtido) ou (None, ultimo_erro, codigo_erro)
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
            
            print(f"Skill_Tuya: A tentar versão {version}...", end="", flush=True)
            status = d.status()
            
            if 'dps' in status:
                print(" SUCESSO!")
                return (d, status, None)
            else:
                print(f" Falhou (Resposta inválida: {status})")
                last_error_payload = status
                last_error_code = status.get('Err')
                
                if last_error_code == '905':
                    print("Skill_Tuya: Erro 905 (Device Unreachable/Key) detetado. A parar de tentar.")
                    break # Para o loop de versões

        except Exception as e:
            print(f" Falhou (Erro de rede: {e})")
            last_error_payload = f"Erro de Rede: {e}"
            last_error_code = '901' # Assumimos 901 para qualquer erro de rede
            break # Para o loop de versões
            
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

    # --- LÓGICA DE MATCHING CORRIGIDA (O Mais Específico Ganha) ---
    matched_devices = []
    
    # Pass 1: Direct matches (nickname é substring do prompt)
    direct_matches = []
    for nickname, details in config.TUYA_DEVICES.items():
        if nickname in user_prompt_lower:
            direct_matches.append((nickname, details))

    if direct_matches:
        # Se encontrou matches directos, ordena por tamanho (descendente)
        direct_matches.sort(key=lambda x: len(x[0]), reverse=True)
        
        # Escolhe APENAS o mais longo (ex: "luz da sala" em vez de "sala")
        best_match = direct_matches[0]
        matched_devices = [best_match]
        print(f"Skill_Tuya: Match directo e específico encontrado: {best_match[0]}")

    else:
        # Pass 2: Noun-based matches (só corre se NENHUM directo for encontrado)
        nouns_in_prompt = [noun for noun in BASE_NOUNS if noun in user_prompt_lower]
        if nouns_in_prompt:
            print(f"Skill_Tuya: Match directo falhou. A procurar por nouns: {nouns_in_prompt}")
            for nickname, details in config.TUYA_DEVICES.items():
                if any(noun in nickname for noun in nouns_in_prompt):
                    matched_devices.append((nickname, details))
    # --- FIM DA LÓGICA DE MATCHING ---

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
            
            # (Manter a verificação de sensor)
            if "sensor" in nickname.lower():
                print(f"Skill_Tuya: A ignorar '{nickname}' para ação ON/OFF (é um sensor).")
                continue 

            # (Manter a verificação de IP inválido)
            dev_ip = details.get('ip')
            if not dev_ip or 'x' in dev_ip.lower():
                print(f"Skill_Tuya: A ignorar '{nickname}' para ação ON/OFF (IP inválido: {dev_ip}).")
                continue
            
            dps_index = 20 if "luz" in nickname or "lâmpada" in nickname else 1
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
            return f"Falha ao executar o comando. Detalhes: {', '.join(failed_reports)}."
        else:
            return f"Comando executado em {', '.join(success_nicknames)}, mas falhou em {', '.join(failed_reports)}."

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
    
    (d, result, err_code) = _try_connect_with_versioning(
        details['id'], details['ip'], details['key']
    )
    
    print(f"OUTPUT RAW (DPSs): {result}")
    print("************************************\n")

    if not d:
        if err_code in ['901', '905']:
             return f"Diagnóstico: O {nickname} está incontactável. Verifique se está ligado."
        return f"Diagnóstico: Falha ao ligar ao {nickname}. ({result})"
        
    return f"Diagnóstico concluído. O estado RAW (DPSs) foi enviado para o log."


def _handle_switch(nickname, details, action, dps_index):
    """Tenta ligar/desligar um dispositivo com multi-versão"""
    
    (d, status, err_code) = _try_connect_with_versioning(
        details['id'], details['ip'], details['key']
    )
    
    if not d:
        if err_code in ['901', '905']:
            raise Exception(f"está incontactável. Verifique se está ligado.")
        raise Exception(f"não respondeu ({status})")

    try:
        value = True if action == "ON" else False
        print(f"Skill_Tuya: A enviar set_value({dps_index}, {value})")
        d.set_value(dps_index, value, nowait=True)
    except Exception as e:
        raise Exception(f"falha ao dar o comando ({e})")


def _handle_sensor(nickname, details, prompt):
    """Tenta ler um sensor com multi-versão"""
    
    (d, data, err_code) = _try_connect_with_versioning(
        details['id'], details['ip'], details['key']
    )

    if not d:
        if err_code in ['901', '905']:
            return f"O {nickname} está incontactável. Verifique se está ligado."
        return f"O {nickname} não respondeu. ({data})"
        
    if 'dps' not in data:
         return f"O {nickname} respondeu, mas não percebi os dados. ({data})"

    dps = data['dps']
    temp_raw = dps.get('1') or dps.get('102') # DPS 1 (antigo) ou 102 (novo)
    humid_raw = dps.get('2') or dps.get('103') # DPS 2 (antigo) ou 103 (novo)

    response = f"No {nickname}: "
    responses_found = 0
    
    if temp_raw is not None and ("temperatura" in prompt or "estado" in prompt):
        # A 'temp' dos sensores novos é 10x
        temp = float(temp_raw) / 10.0 if temp_raw > 100 else float(temp_raw) 
        response += f"a temperatura é {temp} graus"
        responses_found += 1
        
    if humid_raw is not None and ("humidade" in prompt or "estado" in prompt):
        if responses_found > 0:
            response += " e "
        humid = int(humid_raw)
        response += f"a humidade é {humid} por cento"
        responses_found += 1

    if responses_found == 0:
        return f"Não consegui ler essa informação (Temperatura/Humidade) do {nickname}. DPSs encontrados: {dps}"
        
    return response + "."
