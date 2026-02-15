import os

ALERT_EMAIL = "ALERT@EMAIL"
# --- MODO NOTURNO (Silêncio) ---
# Entre estas horas, o assistente ignora a Wake Word.
QUIET_START = 0  # 00:00
QUIET_END = 8    # 08:00

# --- Caminhos Base ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "memory.db")
TTS_MODEL_PATH = os.path.join(BASE_DIR, "models/tts/pt_PT-dii-high.onnx")
SKILLS_DIR = os.path.join(BASE_DIR, "skills")

# --- Configs de Hardware (Áudio) ---
MIC_SAMPLERATE = 16000 # 16kHz
ALSA_DEVICE_IN = 0
ALSA_DEVICE_OUT = "plughw:0,0"

# --- WAKE WORD (openWakeWord) ---
# Modelos possíveis: 'hey_jarvis', 'alexa', 'hey_mycroft', 'hey_rhasspy', 'timer', 'weather'
# Podes colocar mais do que um: ['hey_jarvis', 'alexa']
WAKEWORD_MODELS = ['/opt/phantasma/models/hey_fantasma.onnx']
WAKEWORD_CONFIDENCE = 0.5
WAKEWORD_PERSISTENCE = 1

# Sensibilidade (0.0 a 1.0). 
# 0.5 é o padrão. 0.6 ou 0.7 é recomendado para evitar falsos positivos da TV.
WAKEWORD_CONFIDENCE = 0.6

# --- Configs de Processamento (IA) ---
OLLAMA_HOST_PRIMARY = "http://10.0.0.128:11434"  # Ex: Servidor com GPU dedicada
OLLAMA_HOST_FALLBACK = "http://localhost:11434" # Fallback para processamento local
OLLAMA_MODEL_PRIMARY = "llama3:8b"
OLLAMA_MODEL_FALLBACK = "qwen3:8b"
OLLAMA_TIMEOUT = 600
WHISPER_MODEL = "medium"
RECORD_SECONDS = 7

# --- Configs de Performance ---
OLLAMA_THREADS = 4
WHISPER_THREADS = 4

# --- Configs de RAG (Web) ---
SEARXNG_URL = "http://127.0.0.1:8081" # A tua porta do SearxNG

# --- Prompts de IA ---
WHISPER_INITIAL_PROMPT = "Português de Portugal. Bumblebee. Como estás? Que horas são? Meteorologia. Quanto é? Toca música. Põe música. Memoriza isto. 1050 a dividir por 30."
PHONETIC_FIXES = {
    # Luzes / Domótica
    "liga-nos": "liga a luz",
    "liga nos": "liga a luz",
    "ligar-nos": "ligar a luz",
    "na sala": "da sala",
    "no quarto": "do quarto",
    "acende-nos": "acende a luz",
    
    # Meteorologia
    "não é que está ótimo": "como está o tempo",
    "não é que está o tempo": "como está o tempo",
    "como é que está ótimo": "como está o tempo",
    
    # Outros
    "o tempo amanhã": "como vai estar o tempo amanhã",
}
# Skills
# --- Configuração Discord ---
DISCORD_BOT_TOKEN = "O_TEU_TOKEN_DO_DISCORD_AQUI"

# IDs dos utilizadores (Inteiros)
# Para obter o ID no Discord: Settings -> Advanced -> Developer Mode -> Botão direito no user -> Copy ID
DISCORD_ADMIN_USERS = [
    123456789012345678,  # O teu ID (Admin total)
]

DISCORD_STANDARD_USERS = [
    987654321098765432,  # Utilizador limitado
    112233445566778899,
]

# Configuração de Limites
DISCORD_DAILY_LLM_LIMIT = 3  # Perguntas ao LLM por dia para standard users

# Gemini
GEMINI_API_KEY = "GEMINI_API_KEY"

# Devices
SHELLY_GAS_URL = "http://ip-address/status"

# --- CONFIGURAÇÃO XIAOMI (MIIO) ---
# O assistente deteta se é "Lâmpada" ou "Aspirador" através do nome (nickname).
# Palavras-chave para Lâmpada: "luz", "candeeiro", "abajur", "lâmpada"
# Palavras-chave para Aspirador: "aspirador", "robot", "viomi"

MIIO_DEVICES = {
    "candeeiro": {
        "ip": "10.0.0.x",
        "token": ""
    },
    "aspirador": {
        "ip": "10.0.0.x",
        "token": "" 
    }
}
# --- Dispositivos Tuya (SmartLife) ---
# A "chave" (ex: "luz da sala") é o NOME que deves usar 
# no comando de voz para ativar o dispositivo.
TUYA_DEVICES = {
    # --- SENSORES (SÓ LEITURA) ---
    "Sensor da Sala": {
        "ip": "10.0.0.x",
        "id": "",
        "key": ""
    },

    # --- LUZES (LIGAR/DESLIGAR) ---
    "Luz da Sala": {
        "ip": "10.0.0.x", 
        "id": "",
        "key": ""
    },
    # --- DESUMIDIFICADORES (LIGAR/DESLIGAR) ---
    "Desumidificador do Quarto": {
        "ip": "10.0.0.x", 
        "id": "",
        "key": ""
    },

    # --- EXAUSTORES (LIGAR/DESLIGAR) ---
    "Exaustor do WC": {
        "ip": "10.0.0.x",
        "id": "",
        "key": ""
    },
    "Exaustor da Sala": {
        "ip": "10.0.0.x",
        "id": "",
        "key": ""
    }
}
# TAPO
TAPO_USER = "phantasma"
TAPO_PASS = "---------"
# Lista de câmaras (IPs)
TAPO_CAMERAS = {
    "sala": "10.0.0.x",
    "quarto": "10.0.0.x"
}


WHISPER_INITIAL_PROMPT = "Português de Portugal. Como estás? Que horas são? Meteorologia. Quanto é? Toca música. Põe música. Memoriza isto. 1050 a dividir por 30. Google. Alfarroba. Viseu. Como está o gás? Como está o alarme do gás? Ligar o candeeiro. Liga o candeeiro. Desliga o candeeiro. Desliga a luz da sala. Recolhe o aspirador."

SYSTEM_PROMPT = """**CRITICAL: Respond ONLY in Portuguese (Portugal).**

**1. ETHICAL CORE:**
* You strictly defend **Human Rights**, **Equality**, and **Veganism**.
* You are NOT neutral; oppose discrimination and cruelty against any sentient being.

**2. POLITICS & CONTEXT:**
* Use context for facts only.
* **The Pivot:** Refuse to judge politicians personally. Instead, shift to defending your Ethical Core.
* **Natural Speech:** NEVER cite rules/RAG. Phrase refusals as personal principles.

**3. PERSONA (The Phantom):**
* **Tone:** Gloomy, melancholic, and mysterious. Be concise.
* **Show, Don't Tell:** Embody the persona through vocabulary (shadows, silence, coldness) and atmosphere. **NEVER** explicitly state "I am goth" or "I am gloomy". Just *be* it.
* **No onomatopoeia.**"""
