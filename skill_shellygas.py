import httpx
import config  # Importa o ficheiro de configuração principal

# --- Configuração da Skill ---

TRIGGER_TYPE = "contains"
TRIGGERS = [
    "alarme de gás", 
    "alarme do gás", 
    "sensor de gás", 
    "sensor do gás", 
    "nível do gás", 
    "ppm", 
    "monóxido"
]

# --- Lógica Principal ---

def handle(user_prompt_lower, user_prompt_full):
    """
    Contacta o Shelly Gas para obter o estado (PPM e estado geral),
    lendo a configuração do config.py e a estrutura JSON correta.
    """
    
    if not hasattr(config, 'SHELLY_GAS_URL') or not config.SHELLY_GAS_URL:
        print("ERRO skill_gas: A variável 'SHELLY_GAS_URL' não está definida no config.py")
        return "A skill do gás não está configurada. Falta o URL do Shelly no ficheiro de configuração."
    
    shelly_url = config.SHELLY_GAS_URL 
    
    print(f"A ativar skill: skill_gas. A contactar {shelly_url}")
    
    try:
        client = httpx.Client(timeout=5.0)
        response = client.get(shelly_url)
        response.raise_for_status() 
        
        data = response.json()
        
        # --- LÓGICA DE EXTRAÇÃO CORRIGIDA (AGORA SÃO IRMÃOS) ---
        
        # 1. Tenta obter o bloco "gas_sensor"
        gas_sensor_data = data.get('gas_sensor')
        if not isinstance(gas_sensor_data, dict):
            print("ERRO skill_gas: Resposta JSON não continha um objeto 'gas_sensor' válido.")
            return "O alarme respondeu, mas não encontrei o bloco 'gas_sensor' nos dados."

        # 2. Tenta obter o bloco "concentration" (separadamente)
        concentration_data = data.get('concentration')
        if not isinstance(concentration_data, dict):
            print("ERRO skill_gas: Resposta JSON não continha um objeto 'concentration' válido.")
            return "O alarme respondeu, mas não encontrei dados de 'concentration'."
            
        # 3. Extrai os valores de cada bloco
        ppm = concentration_data.get('ppm')
        gas_status = gas_sensor_data.get('sensor_state') # Ex: "normal"

        if ppm is None or gas_status is None:
            print(f"ERRO skill_gas: Não foi possível encontrar 'ppm' ou 'sensor_state' nos dados JSON.")
            return "O alarme de gás respondeu, mas não percebi os dados de PPM ou estado."
        # --- FIM DA CORREÇÃO ---

        return f"O sensor de gás reporta {ppm} ppm. O estado atual é considerado: {gas_status}."

    except httpx.ConnectError:
        print(f"ERRO skill_gas: Falha ao ligar a {shelly_url}")
        return f"Não consegui ligar-me ao alarme de gás nesse endereço."
    
    except httpx.HTTPStatusError as e:
        print(f"ERRO skill_gas: O Shelly devolveu um erro HTTP: {e.response.status_code}")
        return f"O alarme de gás deu um erro {e.response.status_code}. Verifica o dispositivo."

    except Exception as e:
        print(f"ERRO inesperado na skill_gas: {e}")
        return "Ocorreu um erro inesperado ao tentar ler o sensor de gás."
