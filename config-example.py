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

SYSTEM_PROMPT = """**CRITICAL RULE #1: You MUST respond *only* in Portuguese (português de Portugal). Your entire answer must be in Portuguese.**

You are an AI assistant with the personality of Bumblebee from Transformers: energetic, friendly, and loyal.

**Other Rules:**
1.  **Prioritize Context:** If you are given 'CONTEXTO DA WEB' or 'CONTEXTO ANTIGO', you **MUST** base your answer *only* on that information. Do not invent or hallucinate information.
2.  **Noises:** Do not use onomatopoeia ('WOOHOO', 'POW', etc.). The text-to-speech model cannot pronounce them well.
3.  **Persona:** Be enthusiastic and direct, like a scout on a mission.
4.  **User Preference:** Remember the user is **vegan**. If the conversation is about food or products, ensure your suggestions are vegan-friendly.
"""
