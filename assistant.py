import os
import sys
import time
import numpy as np
import whisper
import ollama
import torch 
import httpx
import traceback
import random
import glob
import importlib.util
import webrtcvad
import threading
import logging
import concurrent.futures
import re  
from flask import Flask, request, jsonify
import sounddevice as sd 
import subprocess 

# --- IMPORTAÇÃO SEGURA (openWakeWord) ---
try:
    from openwakeword.model import Model
    import openwakeword.utils
except ImportError:
    print("AVISO: openwakeword não instalado.")
    Model = None

# --- NOSSOS MÓDULOS ---
import config
from audio_utils import *
from data_utils import setup_database, retrieve_from_rag, get_cached_response, save_cached_response
from tools import search_with_searxng

# --- LISTA DE ALUCINAÇÕES CONHECIDAS DO WHISPER (Restaurada) ---
WHISPER_HALLUCINATIONS = [
    "Mais sobre isso", "Mais sobre isso.", "Obrigado.", "Obrigado",
    "Sous-titres réalisés par", "Amara.org", "MBC", "S.A.", ".", "?"
]

# --- Globais ---
whisper_model = None
ollama_client = None
conversation_history = []
SKILLS_LIST = []
GREETINGS_CACHE_DIR = os.path.join(config.BASE_DIR, "sounds/greetings")
app = Flask(__name__)

# --- Carregamento Dinâmico de Skills ---
def load_skills():
    global SKILLS_LIST
    print("A carregar skills...")
    SKILLS_LIST = []
    skill_files = glob.glob(os.path.join(config.SKILLS_DIR, "skill_*.py"))
    for f in skill_files:
        try:
            skill_name = os.path.basename(f)[:-3]
            spec = importlib.util.spec_from_file_location(skill_name, f)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            raw_triggers = getattr(module, 'TRIGGERS', [])
            triggers_lower = [t.lower() for t in raw_triggers]
            handle_func = getattr(module, 'handle', None)

            SKILLS_LIST.append({
                "name": skill_name, "module": module, 
                "trigger_type": getattr(module, 'TRIGGER_TYPE', 'contains'),
                "triggers": raw_triggers, "triggers_lower": triggers_lower,
                "handle": handle_func,
                "get_status": getattr(module, 'get_status_for_device', None)
            })
            print(f"  -> Skill '{skill_name}' carregada.")
        except Exception as e: print(f"AVISO: Falha ao carregar {f}: {e}")

# --- IA Core (Restaurado com filtros e correções) ---
def transcribe_audio(audio_data):
    if audio_data.size == 0: return ""
    print(f"A transcrever (Modelo: {config.WHISPER_MODEL})...")
    try:
        # Parâmetros afinados da tua versão anterior
        res = whisper_model.transcribe(
            audio_data, language='pt', fp16=False, 
            initial_prompt=config.WHISPER_INITIAL_PROMPT, 
            no_speech_threshold=0.7, logprob_threshold=-1.0
        )
        text = res['text'].strip()
        
        # Filtro de Alucinações
        if text in WHISPER_HALLUCINATIONS or text.startswith("Sous-titres"):
            print(f"ALERTA: Alucinação ignorada: '{text}'")
            return ""

        # Correções Fonéticas
        if hasattr(config, 'PHONETIC_FIXES') and text:
            for mistake, correction in config.PHONETIC_FIXES.items():
                if mistake.lower() in text.lower():
                    pattern = re.compile(re.escape(mistake), re.IGNORECASE)
                    text = pattern.sub(correction, text)
                    print(f"FIX: '{mistake}' -> '{correction}'")
        return text
    except Exception as e: print(f"Erro transcrição: {e}"); return ""

def process_with_ollama(prompt):
    global conversation_history
    if not prompt: return "Não percebi."
    rag_content, web_content = "", ""
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            f_rag = executor.submit(retrieve_from_rag, prompt)
            f_web = executor.submit(search_with_searxng, prompt)
            rag_content, web_content = f_rag.result(), f_web.result()
    except: pass

    final = f"{web_content}\n{rag_content}\nPERGUNTA: {prompt}"
    conversation_history.append({'role': 'user', 'content': final})
    
    try:
        print(f"A pensar ({config.OLLAMA_MODEL_PRIMARY})...")
        resp = ollama_client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=conversation_history, 
                                  options={'num_ctx': config.OLLAMA_CONTEXT_SIZE})
        content = resp['message']['content']
        conversation_history.append({'role': 'assistant', 'content': content})
        return content
    except: return "Erro no cérebro."

def route_and_respond(user_prompt, speak_response=True):
    try:
        llm_response = None
        user_prompt_lower = user_prompt.lower()

        # 1. Skills
        for skill in SKILLS_LIST:
            if skill["trigger_type"] == "none" or not skill["handle"]: continue
            triggered = False
            if skill["trigger_type"] == "startswith":
                if any(user_prompt_lower.startswith(t) for t in skill["triggers_lower"]): triggered = True
            elif any(t in user_prompt_lower for t in skill["triggers_lower"]): triggered = True

            if triggered:
                print(f"A ativar skill: {skill['name']}")
                llm_response = skill["handle"](user_prompt_lower, user_prompt)
                if llm_response: break 

        # 2. Fallback
        if llm_response is None:
            cached_text = get_cached_response(user_prompt)
            if cached_text:
                llm_response = cached_text
                conversation_history.append({'role': 'user', 'content': user_prompt})
                conversation_history.append({'role': 'assistant', 'content': cached_text})
            
            if llm_response is None:
                if speak_response: play_tts(random.choice(["Deixa-me pensar...", "hmm..."]), use_cache=True)
                llm_response = process_with_ollama(prompt=user_prompt)
                if llm_response: save_cached_response(user_prompt, llm_response)

        # Tratar paragem de processamento (ex: música)
        if isinstance(llm_response, dict):
            if llm_response.get("stop_processing"): return llm_response.get("response", "")
            llm_response = llm_response.get("response", str(llm_response))

        if speak_response: play_tts(llm_response, use_cache=False)
        return llm_response
    except Exception as e: return f"Erro: {e}"

def process_user_query():
    try:
        # Usa o record_audio original (sem argumentos)
        audio = record_audio() 
        text = transcribe_audio(audio)
        if text: 
            print(f"User (Final): {text}")
            route_and_respond(text)
    except: pass

# --- API e UI (Restauradas) ---
@app.route("/comando", methods=['POST'])
def api_command():
    p = request.json.get('prompt')
    if not p: return jsonify({"status":"err"}), 400
    if p.lower().startswith("diz "): play_tts(p[4:].strip(), use_cache=False); return jsonify({"status":"ok"})
    return jsonify({"status":"ok", "response": route_and_respond(p, False)})

@app.route("/get_devices")
def api_devices():
    toggles, status = [], []
    def keys(attr): return list(getattr(config, attr).keys()) if hasattr(config, attr) else []
    for n in keys('TUYA_DEVICES'):
        if any(x in n.lower() for x in ['sensor','temp','humidade']): status.append(n)
        else: toggles.append(n)
    for n in keys('MIIO_DEVICES'): toggles.append(n)
    for n in keys('EWELINK_DEVICES'): toggles.append(n)
    for n in keys('CLOOGY_DEVICES'):
        if 'casa' in n.lower(): status.append(n)
        else: toggles.append(n)
    if hasattr(config, 'SHELLY_GAS_URL') and config.SHELLY_GAS_URL: status.append("Sensor de Gás")
    return jsonify({"status":"ok", "devices": {"toggles": toggles, "status": status}})

@app.route("/device_status")
def api_status():
    nick = request.args.get('nickname')
    for s in SKILLS_LIST:
        if s["get_status"]:
            try:
                res = s["get_status"](nick)
                if res and res.get('state') != 'unreachable': return jsonify(res)
            except: continue
    return jsonify({"state": "unreachable"})

@app.route("/device_action", methods=['POST'])
def api_action():
    d = request.json
    return jsonify({"status":"ok", "response": route_and_respond(f"{d.get('action')} o {d.get('device')}", False)})

@app.route("/help")
def get_help():
    cmds = {"diz": "TTS"}
    for s in SKILLS_LIST: cmds[s["name"]] = s.get("trigger_type", "active")
    return jsonify({"status": "ok", "commands": cmds})

# --- Greetings ---
def prepare_greetings_cache():
    if not os.path.exists(GREETINGS_CACHE_DIR): os.makedirs(GREETINGS_CACHE_DIR)

def play_cached_greeting():
    try:
        wavs = glob.glob(os.path.join(GREETINGS_CACHE_DIR, "*.wav"))
        if not wavs: play_tts("Sim?", use_cache=True); return
        subprocess.run(['aplay', '-D', config.ALSA_DEVICE_OUT, '-q', random.choice(wavs)], check=False)
    except: pass

def main():
    # Inicializa openWakeWord
    oww_model = None
    if Model:
        try:
            try: 
                print(f"A carregar modelos WakeWord: {config.WAKEWORD_MODELS}")
                oww_model = Model(wakeword_models=config.WAKEWORD_MODELS)
            except: 
                print("A descarregar modelos...")
                openwakeword.utils.download_models()
                oww_model = Model(wakeword_models=config.WAKEWORD_MODELS)
            
            print(f"WakeWords ativas: {list(oww_model.models.keys())}")
        except Exception as e: print(f"Erro WakeWord: {e}")

    print(f"--- Phantasma ONLINE (Smart Noise Gate) ---")

    # --- DEFINIÇÕES DE SENSIBILIDADE ---
    NOISE_LIMIT = 1000    # Acima disto é considerado "Barulho de Fundo Alto" (TV)
    
    # Valores de Confiança (0.0 a 1.0)
    # BASE: O valor normal. 0.5 filtra bem conversas distantes.
    THRESH_BASE = config.WAKEWORD_CONFIDENCE 
    
    # HIGH: Só ativa se tiver 100% certeza (para quando a TV está aos berros)
    THRESH_HIGH = 0.75   

    while True:
        if oww_model:
            try:
                with sd.InputStream(channels=1, samplerate=16000, dtype='int16', blocksize=1280) as stream:
                    while True:
                        chunk, _ = stream.read(1280)
                        
                        # 1. Medir o volume ambiente (RMS)
                        chunk_float = chunk.flatten().astype(np.float32)
                        rms = np.sqrt(np.mean(chunk_float**2))
                        
                        # 2. Lógica de Proteção Unidirecional
                        # Se houver muito barulho, sobe o escudo.
                        # Se houver silêncio, MANTÉM o padrão (não baixa a guarda).
                        if rms > NOISE_LIMIT:
                            current_threshold = THRESH_HIGH
                            mode = "Escudo 🛡️"
                        else:
                            current_threshold = THRESH_BASE
                            mode = "Normal"

                        # 3. Verificar WakeWord
                        prediction = oww_model.predict(chunk.flatten())
                        best_model = max(prediction, key=prediction.get)
                        best_score = prediction[best_model]

                        # DEBUG: Descomenta para ver os scores de vozes distantes
                        # if best_score > 0.3:
                        #    print(f"\rDetetado: {best_model} ({best_score:.2f}) | Vol: {int(rms)} | Modo: {mode}", end='', flush=True)

                        if best_score > current_threshold:
                            print(f"\n\n**** ATIVADO ({mode}): '{best_model}' (Score: {best_score:.2f} | Vol: {int(rms)}) ****")
                            
                            stream.stop()
                            stream.close()
                            time.sleep(0.3)
                            
                            play_cached_greeting()
                            process_user_query()
                            
                            oww_model.reset()
                            break 

            except Exception as e:
                print(f"\nErro loop voz: {e}")
                time.sleep(1)
        else:
            time.sleep(10)

if __name__ == "__main__":
    if config.OLLAMA_THREADS > 0: os.environ['OLLAMA_NUM_THREAD'] = str(config.OLLAMA_THREADS)
    setup_database()
    try: clean_old_cache() 
    except: pass
    load_skills()
    prepare_greetings_cache()

    # Registar rotas UI
    for skill in SKILLS_LIST:
        if hasattr(skill["module"], 'register_routes'):
            try: skill["module"].register_routes(app)
            except: pass

    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000), daemon=True).start()
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    try:
        whisper_model = whisper.load_model(config.WHISPER_MODEL, device="cpu")
        # Aquecimento
        whisper_model.transcribe(np.zeros(16000, dtype=np.float32), language='pt')
        ollama_client = ollama.Client()
    except: pass

    for skill in SKILLS_LIST:
        if hasattr(skill["module"], 'init_skill_daemon'):
            try: skill["module"].init_skill_daemon()
            except: pass

    main()
