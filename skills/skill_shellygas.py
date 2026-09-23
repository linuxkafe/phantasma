import httpx
import config

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
TRIGGERS = [
    "alarme de gás", 
    "alarme do gás", 
    "sensor de gás", 
    "sensor do gás", 
    "nível do gás", 
    "está o gás",
    "estado do gás",
    "monóxido"
]

# --- Lógica Principal (Voz) ---
def handle(user_prompt_lower, user_prompt_full):
    """ Contacta o Shelly Gas para obter o estado via voz. """
    
    if not hasattr(config, 'SHELLY_GAS_URL') or not config.SHELLY_GAS_URL:
        return "A skill do gás não está configurada."
    
    try:
        client = httpx.Client(timeout=5.0)
        response = client.get(config.SHELLY_GAS_URL)
        response.raise_for_status() 
        data = response.json()
        
        gas_sensor = data.get('gas_sensor', {})
        concentration = data.get('concentration', {})
        
        ppm = concentration.get('ppm')
        status = gas_sensor.get('sensor_state')

        if ppm is None or status is None:
            return "O alarme respondeu, mas os dados não são claros."

        return f"O sensor de gás reporta {ppm} ppm. O estado é: {status}."

    except Exception as e:
        print(f"ERRO skill_gas: {e}")
        return "Não consegui ler o sensor de gás."

# --- API Status (Web UI) ---
def get_status_for_device(nickname):
    """ 
    Retorna o estado para o dashboard web. 
    """
    # 1. VALIDAÇÃO: Se não perguntaram pelo gás, ignorar (retorna unreachable)
    if "gás" not in nickname.lower() and "gas" not in nickname.lower():
        return {"state": "unreachable"}

    if not hasattr(config, 'SHELLY_GAS_URL') or not config.SHELLY_GAS_URL:
        return {"state": "unreachable"}

    try:
        client = httpx.Client(timeout=3.0)
        response = client.get(config.SHELLY_GAS_URL)
        if response.status_code != 200:
            return {"state": "unreachable"}
            
        data = response.json()
        
        # Extração segura
        ppm = data.get('concentration', {}).get('ppm', 0)
        status_str = data.get('gas_sensor', {}).get('sensor_state', 'unknown')
        
        # Mapeia para o formato que o frontend espera
        return {
            "state": "on",      # "on" para aparecer ativo (não cinzento)
            "ppm": ppm,         # Valor específico para mostrarmos no JS
            "status": status_str
        }
        
    except Exception as e:
        print(f"ERRO Gas Status: {e}")
        return {"state": "unreachable"}
