import os
import sys
import time
import numpy as np
import whisper
import ollama
import threading
import logging
import concurrent.futures
import re
import random
import glob
import importlib.util
import webrtcvad
import subprocess
import uuid 
from flask import Flask, request, jsonify
from collections import deque
import sounddevice as sd
import onnxruntime as ort
from datetime import datetime  # <--- A LINHA QUE FALTAVA

# --- CONFIGURAÇÕES DE AFINAÇÃO ---
DEBUG_MODE = False         # False = Logs limpos
WAKEWORD_THRESHOLD = 0.85 
TRIGGER_PERSISTENCE = 4    
WARMUP_SECONDS = 2        

# --- HORÁRIO SILENCIOSO (Modo Noturno) ---
QUIET_START = 23  # Começa às 23
QUIET_END = 8     # Acaba às 08:00

# --- GLOBAIS DE CONTROLO DE FLUXO (BARGE-IN) ---
CURRENT_REQUEST_ID = None  
IS_SPEAKING = False        

# --- MÓDULOS EXTERNOS (Config e Utils) ---
import config
try:
    from audio_utils import *
except ImportError:
    # Fallback se faltar audio_utils
    def play_tts(text, use_cache=False): print(f"[TTS]: {text}")
    def record_audio(): return np.zeros(16000, dtype=np.int16)
    def clean_old_cache(): pass

try:
    from data_utils import setup_database, retrieve_from_rag, get_cached_response, save_cached_response
except ImportError:
    def setup_database(): pass
    def retrieve_from_rag(p): return ""
    def get_cached_response(p): return None
    def save_cached_response(p, r): pass

try:
    from tools import search_with_searxng
except ImportError:
    def search_with_searxng(p): return ""

# --- MOTOR MANUAL PHANTASMA ---
class PhantasmaEngine:
    def __init__(self, model_path):
        self.ready = False
        try:
            import openwakeword
            oww_path = os.path.dirname(openwakeword.__file__)
            mel_path = os.path.join(oww_path, "resources", "models", "melspectrogram.onnx")
            
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            
            self.mel_sess = ort.InferenceSession(mel_path, opts, providers=['CPUExecutionProvider'])
            self.clf_sess = ort.InferenceSession(model_path, opts, providers=['CPUExecutionProvider'])
            
            self.mel_input = self.mel_sess.get_inputs()[0].name
            self.clf_input = self.clf_sess.get_inputs()[0].name
            
            self.buffer = deque(maxlen=16) 
            self.ready = True
            print(f"👻 Motor Phantasma pronto: {os.path.basename(model_path)}")
        except Exception as e:
            print(f"❌ Erro ao iniciar motor: {e}")

    def predict(self, audio_chunk_int16):
        if not self.ready: return 0.0
        # Filtro de Ruído Elétrico
        if np.sqrt(np.mean(audio_chunk_int16.astype(float)**2)) < 300: return 0.0
        try:
            audio_tensor = audio_chunk_int16.astype(np.float32) / 32768.0
            audio_tensor = audio_tensor[None, :] 
            mel_out = self.mel_sess.run(None, {self.mel_input: audio_tensor})
            features = mel_out[0].squeeze()
            if features.ndim == 2:
                for row in features: self.buffer.append(row)
            else:
                self.buffer.append(features)
            if len(self.buffer) != 16: return 0.0
            
            input_vector = np.array(self.buffer).flatten().astype(np.float32)
            input_vector = input_vector[None, :] 
            clf_out = self.clf_sess.run(None, {self.clf_input: input_vector})
            probs = clf_out[1]
            return probs[0].get(1, 0.0) if isinstance(probs, list) else probs[0][1]
        except: return 0.0

    def reset(self):
        self.buffer.clear()

# --- GLOBAIS APP ---
whisper_model = None
ollama_client = None
conversation_history = []
SKILLS_LIST = []
app = Flask(__name__)

# --- FILTROS DE ALUCINAÇÃO ---
WHISPER_HALLUCINATIONS = [
    "Mais sobre isso", "Mais sobre isso.", "Obrigado.", "Obrigado",
    "Sous-titres réalisés par", "Amara.org", "MBC", "S.A.", ".", "?",
    "P.S.", "Entrando a", "A p", "O p"
]

def is_hallucination(text):
    if not text or len(text.strip()) < 2: return True
    if text.strip() in WHISPER_HALLUCINATIONS: return True
    # Deteta Cirílico (Bug comum do Whisper)
    if re.search(r'[а-яА-Я]', text): return True
    return False

def is_quiet_time():
    """Retorna True se estivermos no horário de silêncio."""
    now = datetime.now().hour
    if QUIET_START > QUIET_END:
        return now >= QUIET_START or now < QUIET_END
    return QUIET_START <= now < QUIET_END

# --- FUNÇÕES DE CONTROLO (Barge-in) ---
def stop_audio_output():
    """Mata processos de áudio"""
    global IS_SPEAKING
    IS_SPEAKING = False
    try:
        subprocess.run(['pkill', '-f', 'aplay'], check=False, stderr=subprocess.DEVNULL)
    except: pass

def safe_play_tts(text, use_cache=False, request_id=None, speak=True):
    global CURRENT_REQUEST_ID, IS_SPEAKING
    if not speak: return 
    if request_id and request_id != CURRENT_REQUEST_ID:
        print(f"🔇 Falar cancelado (Interrompido)")
        return
    IS_SPEAKING = True
    play_tts(text, use_cache=use_cache)
    IS_SPEAKING = False

# --- SKILLS ---
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
            
            if hasattr(module, 'register_routes'):
                module.register_routes(app)
            
            SKILLS_LIST.append({
                "name": skill_name, 
                "handle": getattr(module, 'handle', None),
                "trigger_type": getattr(module, 'TRIGGER_TYPE', 'contains'),
                "triggers_lower": triggers_lower,
                "module": module,
                "get_status": getattr(module, 'get_status_for_device', None)
            })
            print(f"  -> Skill '{skill_name}' OK.")
        except Exception as e: 
            print(f"❌ Erro skill {f}: {e}")

def play_cached_greeting():
    try:
        wavs = glob.glob(os.path.join(config.BASE_DIR, "sounds/greetings", "*.wav"))
        if wavs: 
            subprocess.run(['aplay', '-D', config.ALSA_DEVICE_OUT, '-q', random.choice(wavs)], check=False)
        else: 
            play_tts("Sim?", use_cache=True)
    except: pass

# --- TRANSCRIÇÃO ---
def transcribe_audio(audio_data):
    if audio_data.size == 0: return ""
    print(f"A transcrever...")
    try:
        res = whisper_model.transcribe(
            audio_data, language='pt', fp16=False,
            initial_prompt=getattr(config, 'WHISPER_INITIAL_PROMPT', None),
            no_speech_threshold=0.8
        )
        text = res['text'].strip()
        
        if is_hallucination(text): 
            print(f"⚠️ Alucinação ignorada: {text}")
            return ""

        # Correções Fonéticas
        if hasattr(config, 'PHONETIC_FIXES') and text:
            for mistake, correction in config.PHONETIC_FIXES.items():
                if mistake.lower() in text.lower():
                    pattern = re.compile(re.escape(mistake), re.IGNORECASE)
                    text = pattern.sub(correction, text)

        return text
    except: return ""

# --- ROTEAMENTO INTELIGENTE ---
def route_and_respond(user_prompt, my_request_id, speak=True):
    global conversation_history
    
    try:
        if my_request_id != CURRENT_REQUEST_ID: return
        user_prompt_lower = user_prompt.lower()
        
        # 1. Skills
        for skill in SKILLS_LIST:
            triggered = False
            if skill["trigger_type"] == "startswith":
                if any(user_prompt_lower.startswith(t) for t in skill["triggers_lower"]): triggered = True
            elif any(t in user_prompt_lower for t in skill["triggers_lower"]): triggered = True
            
            if triggered and skill["handle"]:
                print(f"Skill: {skill['name']}")
                resp = skill["handle"](user_prompt_lower, user_prompt)
                
                if my_request_id != CURRENT_REQUEST_ID: return
                if resp: 
                    final_txt = resp.get("response", "") if isinstance(resp, dict) else resp
                    safe_play_tts(final_txt, use_cache=False, request_id=my_request_id, speak=speak)
                    return final_txt
        
        # 2. Cache
        cached = get_cached_response(user_prompt)
        if cached:
            safe_play_tts(cached, use_cache=True, request_id=my_request_id, speak=speak)
            return cached

        # 3. LLM + RAG
        safe_play_tts("Deixa-me pensar...", use_cache=True, request_id=my_request_id, speak=speak)
        if my_request_id != CURRENT_REQUEST_ID: return

        rag_res, web_res = retrieve_from_rag(user_prompt), search_with_searxng(user_prompt)
        full_prompt = f"{web_res}\n{rag_res}\nPERGUNTA: {user_prompt}"
        
        if my_request_id != CURRENT_REQUEST_ID: return
        
        temp_history = conversation_history.copy()
        temp_history.append({'role': 'user', 'content': full_prompt})
        
        resp = ollama_client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=temp_history)
        content = resp['message']['content']
        
        if my_request_id != CURRENT_REQUEST_ID: return

        conversation_history.append({'role': 'user', 'content': full_prompt})
        conversation_history.append({'role': 'assistant', 'content': content})
        save_cached_response(user_prompt, content)
        
        safe_play_tts(content, use_cache=False, request_id=my_request_id, speak=speak)
        return content

    except Exception as e:
        print(f"Erro Thread: {e}")
        return f"Erro: {e}"

def background_worker(audio, my_id):
    text = transcribe_audio(audio)
    if my_id != CURRENT_REQUEST_ID: return 
    if text:
        print(f"User (Thread {my_id}): {text}")
        route_and_respond(text, my_id, speak=True)
    else:
        print("Sem áudio útil.")

# --- API ENDPOINTS ---
@app.route("/comando", methods=['POST'])
def api_command():
    p = request.json.get('prompt')
    if p: return jsonify({"status":"ok", "response": route_and_respond(p, "API_REQ", speak=False)})
    return jsonify({"error": "no prompt"}), 400

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
    return jsonify({"status":"ok", "devices": {"toggles": toggles, "status": status}})

@app.route("/device_status")
def api_status():
    nick = request.args.get('nickname')
    for s in SKILLS_LIST:
        if s.get("get_status"):
            try:
                res = s["get_status"](nick)
                if res and res.get('state') != 'unreachable': return jsonify(res)
            except: continue
    return jsonify({"state": "unreachable"})

@app.route("/device_action", methods=['POST'])
def api_action():
    d = request.json
    return jsonify({"status":"ok", "response": route_and_respond(f"{d.get('action')} o {d.get('device')}", "API_REQ", False)})

@app.route("/help")
def get_help():
    cmds = {"diz": "TTS"}
    for s in SKILLS_LIST: cmds[s["name"]] = s.get("trigger_type", "active")
    return jsonify({"status": "ok", "commands": cmds})

# --- MAIN LOOP ---
def main():
    global CURRENT_REQUEST_ID
    models_dir = os.path.join(config.BASE_DIR, 'models')
    custom_model = os.path.join(models_dir, "hey_fantasma.onnx")
    
    if not os.path.exists(custom_model):
        print(f"⚠️ MODELO NÃO ENCONTRADO: {custom_model}")
        return

    engine = PhantasmaEngine(custom_model)
    if not engine.ready: return

    print("--- Phantasma ONLINE (Barge-in + UI) ---")
    if is_quiet_time(): 
        print(f"🌙 Modo Noturno Ativo ({QUIET_START}h - {QUIET_END}h)")
    
    trigger_streak = 0
    start_time = time.time()
    
    while True:
        try:
            with sd.InputStream(device=config.ALSA_DEVICE_IN, channels=1, samplerate=16000, dtype='int16', blocksize=1280) as stream:
                print("👂 À escuta...")
                while True:
                    chunk, overflow = stream.read(1280)
                    if overflow: pass
                    if time.time() - start_time < WARMUP_SECONDS: continue

                    audio_numpy = np.frombuffer(chunk, dtype=np.int16)
                    
                    if IS_SPEAKING: score = 0.0
                    else: score = engine.predict(audio_numpy)
                    
                    if DEBUG_MODE:
                        bar = "#" * trigger_streak
                        sys.stdout.write(f"\rScore: {score:.4f} | Streak: {bar}")
                        sys.stdout.flush()

                    if score > WAKEWORD_THRESHOLD: trigger_streak += 1
                    else: trigger_streak = 0

                    if trigger_streak >= TRIGGER_PERSISTENCE:
                        # --- CHECK MODO NOTURNO ---
                        if is_quiet_time():
                            if DEBUG_MODE: print("\n🌙 Shhh... (Modo Noturno)")
                            trigger_streak = 0
                            engine.reset()
                            continue

                        print(f"\n\n⚡ HEY FANTASMA! (Score: {score:.4f})")
                        stop_audio_output()
                        new_id = str(uuid.uuid4())[:8]
                        CURRENT_REQUEST_ID = new_id 
                        
                        stream.stop(); stream.close(); engine.reset()
                        trigger_streak = 0
                        play_cached_greeting()
                        
                        print("🎤 A ouvir comando...")
                        audio_comando = record_audio()
                        
                        print(f"🚀 A processar (ID: {new_id})...")
                        t = threading.Thread(target=background_worker, args=(audio_comando, new_id))
                        t.daemon = True
                        t.start()
                        
                        start_time = time.time() - WARMUP_SECONDS 
                        break 
        except Exception as e:
            print(f"\n❌ Erro Loop: {e}")
            time.sleep(2)

if __name__ == "__main__":
    if config.OLLAMA_THREADS > 0: os.environ['OLLAMA_NUM_THREAD'] = str(config.OLLAMA_THREADS)
    setup_database()
    try: clean_old_cache() 
    except: pass
    load_skills()
    
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000), daemon=True).start()
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    
    try:
        whisper_model = whisper.load_model(config.WHISPER_MODEL, device="cpu")
        whisper_model.transcribe(np.zeros(16000, dtype=np.float32), language='pt')
        ollama_client = ollama.Client()
    except: pass
    
    for skill in SKILLS_LIST:
        if hasattr(skill["module"], 'init_skill_daemon'):
            try: skill["module"].init_skill_daemon()
            except: pass
    main()
