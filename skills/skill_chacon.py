import config 
import re
import unicodedata
import asyncio

try:
    # O import correto do client
    from dio_chacon_wifi_api.client import DIOChaconAPIClient
    
    # Os imports corretos das exceções
    from dio_chacon_wifi_api.exceptions import DIOChaconInvalidAuthError, DIOChaconAPIError
    
    # Criamos o alias que o resto do script usa
    DioChaconApi = DIOChaconAPIClient

except ImportError as e: 
    print(f"ERRO CRÍTICO [skill_chacon]: Falha a importar a biblioteca dio_chacon_wifi_api. Erro: {e}")
    DioChaconApi = None # Desativa a skill

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"

# Palavras-chave de Ação
ACTIONS_ON = ["liga", "ligar", "acende", "acender", "ativa", "põe"]
ACTIONS_OFF = ["desliga", "desligar", "apaga", "apagar", "desativa", "tira"]

# --- FUNÇÃO HELPER PARA REMOVER ACENTOS ---
def _normalize_string(text):
    try:
        return unicodedata.normalize('NFD', text.lower()) \
                          .encode('ascii', 'ignore') \
                          .decode('utf-8')
    except Exception:
        return text.lower()

# --- FUNÇÃO DE TRIGGERS ---
# Estas são as alcunhas que vamos procurar na conta da cloud
TRIGGERS_NICKNAMES = [
    "luz do balcao",
    "luz do balcão",
    "chacon", # (Não deve ser um nome de dispositivo, mas mantemos)
    "balcao",
    "balcão"
]
TRIGGERS = TRIGGERS_NICKNAMES + ACTIONS_ON + ACTIONS_OFF

# --- Lógica de Async (O "Cérebro" da Skill) ---

async def _async_control_chacon(action_str):
    """
    Função assíncrona que usa os MÉTODOS CORRETOS da biblioteca.
    """
    
    # 1. Obter credenciais do config.py
    username = getattr(config, 'CHACON_CLOUD_USER', None)
    password = getattr(config, 'CHACON_CLOUD_PASS', None)
    
    if not username or not password:
        return "Erro: O utilizador e password da Chacon Cloud não estão no config.py."

    api = None
    try:
        # 2. Ligar e autenticar
        print(f"Info [skill_chacon]: A ligar à Chacon Cloud como {username}...")
        api = DioChaconApi(username, password)
        
        # 3. Encontrar o dispositivo (NÃO USAMOS CONNECT)
        # Usamos search_all_devices(), que força a ligação/autenticação
        print("Info [skill_chacon]: A procurar dispositivos na conta...")
        all_devices_dict = await api.search_all_devices()
        print(f"Info [skill_chacon]: Encontrados {len(all_devices_dict)} dispositivos.")

        found_device_id = None
        found_device_name = None
        
        # Loop para encontrar um dispositivo que corresponda às nossas alcunhas
        for device_id, device_data in all_devices_dict.items():
            device_name_norm = _normalize_string(device_data.get('name', ''))
            
            if device_name_norm in TRIGGERS_NICKNAMES:
                found_device_id = device_id
                found_device_name = device_data.get('name', 'dispositivo')
                print(f"Info [skill_chacon]: Dispositivo encontrado! Nome: {found_device_name}, ID: {found_device_id}")
                break # Encontrámos

        if not found_device_id:
            return f"Erro: Autenticado, mas não encontrei um dispositivo com o nome 'luz do balcão' na tua conta Chacon."

        # 4. Enviar o comando (COM O MÉTODO CORRETO)
        target_state = (action_str == "ON")
        
        print(f"Info [skill_chacon]: A enviar comando {action_str} (state={target_state}) para {found_device_name}...")
        
        # Usamos o método 'switch_switch' que vimos no client.py
        await api.switch_switch(found_device_id, target_state)
        
        # 5. Sucesso
        action_word = "ligada" if target_state else "desligada"
        return f"{found_device_name} {action_word}."

    except DIOChaconInvalidAuthError: 
        print("ERRO [skill_chacon]: Autenticação na Chacon Cloud falhou. Verifica o user/pass no config.py.")
        return "Falha ao autenticar na Chacon Cloud. As credenciais estão corretas?"
    except DIOChaconAPIError as e: 
        print(f"ERRO [skill_chacon]: Falha no pedido à cloud: {e}")
        return "Ocorreu um erro de rede ao tentar controlar o balcão."
    except Exception as e:
        print(f"ERRO [skill_chacon]: Falha inesperada: {e}")
        return f"Ocorreu um erro inesperado: {e}"
    finally:
        # 6. Desligar sempre (MÉTODO CORRETO)
        if api:
            await api.disconnect()
            print("Info [skill_chacon]: Desligado da Chacon Cloud.")


# --- Lógica Principal (Síncrona) ---

def handle(user_prompt_lower, user_prompt_full):
    """
    Função síncrona que o Phantasma chama.
    """
    if DioChaconApi is None:
        return "A skill Chacon falhou a carregar. Vê os logs para o erro exato."

    # 1. Verifica se o dispositivo foi mencionado
    prompt_norm = _normalize_string(user_prompt_lower)
    if not any(nickname in prompt_norm for nickname in TRIGGERS_NICKNAMES):
        return None # Não é para este dispositivo

    # 2. Determina a intenção (Ligar ou Desligar)
    intent_off = any(action in user_prompt_lower for action in ACTIONS_OFF)
    intent_on = any(action in user_prompt_lower for action in ACTIONS_ON)

    action_str = None
    if intent_off:
        action_str = "OFF"
    elif intent_on: 
        action_str = "ON"
    else:
        return None # Mencionou o dispositivo mas não a ação

    # 3. Chamar o código Async
    try:
        print(f"Info [skill_chacon]: A iniciar tarefa assíncrona para {action_str}...")
        response = asyncio.run(_async_control_chacon(action_str))
        return response
    except Exception as e:
        print(f"ERRO [skill_chacon]: Falha ao executar o asyncio.run: {e}")
        return "Ocorreu um erro ao tentar correr a tarefa assíncrona."
