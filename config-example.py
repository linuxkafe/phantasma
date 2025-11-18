import os

ALERT_EMAIL = "ALERT@EMAIL"

# --- Caminhos Base ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "memory.db")
TTS_MODEL_PATH = os.path.join(BASE_DIR, "models/pt_PT-dii-high.onnx")
SKILLS_DIR = os.path.join(BASE_DIR, "skills") # <--- NOVO

# --- Configs de Hotword (Porcupine) ---
ACCESS_KEY = "CHAVEPORCUPINE"
HOTWORD_KEYWORD = "bumblebee"

# --- Configs de Hardware (Áudio) ---
MIC_SAMPLERATE = 16000 # 16kHz
ALSA_DEVICE_IN = 0
ALSA_DEVICE_OUT = "plughw:0,0"

# --- Configs de Processamento (IA) ---
OLLAMA_MODEL_PRIMARY = "llama3:8b-instruct-8k" # O teu modelo 8K
OLLAMA_MODEL_FALLBACK = "phi3:mini"
OLLAMA_TIMEOUT = 120
WHISPER_MODEL = "medium"
RECORD_SECONDS = 8 # Aumentado

# --- Configs de Performance ---
OLLAMA_THREADS = 4
WHISPER_THREADS = 4

# --- Configs de RAG (Web) ---
SEARXNG_URL = "http://127.0.0.1:8081" # A tua porta do SearxNG

# --- Prompts de IA ---
WHISPER_INITIAL_PROMPT = "Português de Portugal. Bumblebee. Como estás? Que horas são? Meteorologia. Quanto é? Toca música. Põe música. Memoriza isto. 1050 a dividir por 30."

# Skills
GEMINI_API_KEY = "GEMINI_API_KEY"
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
