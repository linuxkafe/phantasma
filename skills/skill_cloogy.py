# skills/skill_cloogy.py

import logging
import requests
import time
import re
import config 

LOG = logging.getLogger(__name__)

# --- METADADOS DA SKILL PARA O PHANTASMA CORE ---
TRIGGER_TYPE = "contains"

# --- Ações (para parsear dentro do handle) ---
ON_TRIGGERS = ["liga", "ligar", "acende", "acender"]
OFF_TRIGGERS = ["desliga", "desligar", "apaga", "apagar"]
STATUS_TRIGGERS = ["leitura", "como está", "estado"] # 'leitura' é o trigger da Cloogy original

# --- Nomes (Nouns) ---
def _get_cloogy_triggers():
    """
    Lê os nomes dos dispositivos do config.CLOOGY_DEVICES 
    para usar como os triggers principais.
    """
    if hasattr(config, 'CLOOGY_DEVICES') and isinstance(config.CLOOGY_DEVICES, dict):
        # Os triggers são os nomes (chaves) dos dispositivos
        return list(config.CLOOGY_DEVICES.keys())
    return []

# Os triggers principais são os NOMES dos dispositivos
TRIGGERS = _get_cloogy_triggers()
# --- FIM METADADOS ---


# --- INSTÂNCIA GLOBAL DA SKILL ---
SKILL_INSTANCE = None
# --- FIM INSTÂNCIA GLOBAL ---


#
# A CLASSE CloogySkill (que gere a API) fica EXATAMENTE IGUAL À QUE FIZEMOS ANTES.
# Não precisa de qualquer alteração.
#
class CloogySkill:
    """
    Classe interna para gerir o estado da API Cloogy (autenticação, sessão).
    """
    def __init__(self, credentials, device_map):
        self.creds = credentials
        self.device_map = device_map 
        self.api_url = self.creds.get("API_URL")
        self.session = requests.Session()
        self._access_token = None
        self._token_expiry = 0

    def _authenticate(self):
        """Obtém ou renova o Access Token da API Cloogy."""
        LOG.info("Cloogy: A autenticar na API...")
        auth_url = self.creds.get("AUTH_URL")
        payload = {
            'grant_type': 'client_credentials',
            'client_id': self.creds.get("CLIENT_ID"),
            'client_secret': self.creds.get("CLIENT_SECRET")
        }
        try:
            response = self.session.post(auth_url, data=payload)
            response.raise_for_status() 
            data = response.json()
            self._access_token = data.get('access_token')
            expires_in = data.get('expires_in', 3600)
            self._token_expiry = time.time() + expires_in - 60
            self.session.headers.update({
                'Authorization': f'Bearer {self._access_token}',
                'Content-Type': 'application/json'
            })
            LOG.info("Cloogy: Autenticação bem-sucedida.")
            return True
        except requests.exceptions.RequestException as e:
            LOG.error(f"Cloogy: Falha grave na autenticação da API: {e}")
            self._access_token = None
            return False

    def _check_token(self):
        """Verifica se o token expirou (ou nunca existiu) e renova."""
        if time.time() > self._token_expiry or self._access_token is None:
            LOG.info("Cloogy: Token expirado ou inexistente. A renovar...")
            return self._authenticate()
        return True

    def handle_api_command(self, device_name, command_type):
        """Executa um comando (Ligar/Desligar/Leitura) num dispositivo Cloogy."""
        
        if not self._check_token():
            LOG.error("Cloogy: Não foi possível executar o comando. Falha ao obter/renovar token.")
            return "Desculpe, chefe, não me consigo ligar à API da Cloogy."
            
        device_info = self.device_map.get(device_name.lower())
        if not device_info:
            LOG.warning(f"Cloogy: Dispositivo '{device_name}' não mapeado internamente.")
            return f"Desculpe, chefe, não encontrei um dispositivo chamado {device_name}."

        device_id = device_info.get("device_id")
        endpoint_control = f"{self.api_url}/devices/{device_id}/control" # Hipotético
        payload = {}

        try:
            if command_type == "ON":
                payload = {"state": "ON"} # Hipotético
                response = self.session.post(endpoint_control, json=payload)
                response.raise_for_status()
                acao_str = "ligada"
                
            elif command_type == "OFF":
                payload = {"state": "OFF"} # Hipotético
                response = self.session.post(endpoint_control, json=payload)
                response.raise_for_status()
                acao_str = "desligada"
                
            elif command_type == "STATUS":
                endpoint_status = f"{self.api_url}/devices/{device_id}/status" # Hipotético
                response = self.session.get(endpoint_status)
                response.raise_for_status()
                data = response.json() 
                # A resposta da API é hipotética
                power = data.get("current_power_w", "desconhecida") 
                LOG.info(f"Cloogy: Leitura de '{device_name}' bem-sucedida: {power}W.")
                return f"A {device_name} está a consumir {power} watts."
                
            else:
                return None 

            LOG.info(f"Cloogy: Comando '{command_type}' executado com sucesso em '{device_name}'.")
            return f"A {device_name} foi {acao_str}, chefe."

        except requests.exceptions.RequestException as e:
            LOG.error(f"Cloogy: Erro ao executar comando em '{device_name}': {e}")
            return f"Ocorreu um erro ao tentar comandar a {device_name}."
# --- FIM DA CLASSE ---


# --- INICIALIZAÇÃO DA SKILL (Mantém-se igual) ---
def _initialize_skill():
    global SKILL_INSTANCE
    LOG.info("Cloogy: A inicializar skill...")
    
    credentials = getattr(config, "CLOOGY_CREDENTIALS", None)
    devices = getattr(config, "CLOOGY_DEVICES", None)
    
    if credentials and devices:
        SKILL_INSTANCE = CloogySkill(credentials, devices)
        LOG.info(f"Cloogy: Skill carregada. {len(devices)} dispositivos mapeados (triggers).")
    else:
        LOG.error("Cloogy: Credenciais (CLOOGY_CREDENTIALS) ou Dispositivos (CLOOGY_DEVICES) não encontrados no config.py")
        LOG.error("Cloogy: A skill ficará DESATIVADA.")

_initialize_skill()
# --- FIM DA INICIALIZAÇÃO ---


# --- FUNÇÃO DE PONTO DE ENTRADA DO CORE (LÓGICA INVERTIDA) ---

def handle(user_prompt_lower, user_prompt_full):
    """
    Função obrigatória chamada pelo Phantasma Core.
    Agora é ativada por um NOME de dispositivo, e não por uma AÇÃO.
    """
    if not SKILL_INSTANCE:
        LOG.error("Cloogy: A skill foi chamada (handle) mas não está inicializada.")
        return None 

    # 1. Encontrar a Ação (Ex: "ligar", "desligar", "leitura")
    # Damos prioridade a OFF
    final_action = None
    if any(action in user_prompt_lower for action in OFF_TRIGGERS):
        final_action = "OFF"
    elif any(action in user_prompt_lower for action in ON_TRIGGERS):
        final_action = "ON"
    elif any(action in user_prompt_lower for action in STATUS_TRIGGERS):
        final_action = "STATUS"
    
    if not final_action:
        # Foi dito o nome do dispositivo (ex: "luz da sala") mas sem ação
        return None # Deixa o Ollama perguntar "o que fazer com a luz da sala?"

    # 2. Encontrar o Dispositivo que foi mencionado
    # O router SÓ nos chamou porque um dos TRIGGERS (nomes de devices) foi dito.
    
    found_device_name = None
    for mapped_name in TRIGGERS: # TRIGGERS = SKILL_INSTANCE.device_map.keys()
        if mapped_name in user_prompt_lower:
            found_device_name = mapped_name
            # NOTA: Isto só apanha o *primeiro* dispositivo que encontrar.
            # É suficiente por agora.
            break 
            
    if not found_device_name:
        # Isto não deve acontecer se o router funcionou
        LOG.warning(f"Cloogy: Fui ativado, mas não encontrei o trigger no prompt: {user_prompt_lower}")
        return None 

    # 3. Delegar para a classe
    LOG.debug(f"Cloogy: A processar comando '{final_action}' para o dispositivo mapeado '{found_device_name}'")
    return SKILL_INSTANCE.handle_api_command(found_device_name, final_action)

# --- Fim skill_cloogy.py ---
