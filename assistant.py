# vim assistant.py

import os
import sys
import time
import glob
import re
import importlib.util
import numpy as np
import whisper
import ollama
import threading
import subprocess
import uuid 
import sounddevice as sd
from flask import Flask, request, jsonify
from datetime import datetime
import traceback
import config
import queue

# --- FALLBACKS ---
try: from audio_utils import play_tts, record_audio, clean_old_cache
except ImportError: 
    def play_tts(t, **k): print(f"[TTS] {t}")
    def record_audio(): return np.zeros(16000, dtype=np.int16)
try: from data_utils import setup_database, retrieve_from_rag, get_cached_response, save_cached_response
except ImportError: 
    def setup_database(): pass
    def retrieve_from_rag(p): return ""
    def get_cached_response(p): return None
    def save_cached_response(p, r): pass
try: from tools import search_with_searxng
except ImportError: 
    def search_with_searxng(p): return ""

# --- GLOBAIS ---
CURRENT_REQUEST_ID = None  
IS_SPEAKING = False
app = Flask(__name__)
whisper_model = None
ollama_client = None
SKILLS_LIST = []

# --- UTILITÁRIOS ---
def stop_audio_output():
    global IS_SPEAKING
    IS_SPEAKING = False
    subprocess.run(['pkill', '-f', 'aplay'], check=False, stderr=subprocess.DEVNULL)
    subprocess.run(['pkill', '-f', 'mpg123'], check=False, stderr=subprocess.DEVNULL)

def is_quiet_time():
    if not hasattr(config, 'QUIET_START'): return False
    now = datetime.now().hour
    if config.QUIET_START > config.QUIET_END: return now >= config.QUIET_START or now < config.QUIET_END
    return config.QUIET_START <= now < config.QUIET_END

def safe_play_tts(text, use_cache=True, request_id=None, speak=True):
    global CURRENT_REQUEST_ID, IS_SPEAKING
    if not speak: return
    if request_id and request_id != "API_REQ" and request_id != CURRENT_REQUEST_ID: return
    stop_audio_output()
    IS_SPEAKING = True
    play_tts(text, use_cache=use_cache)
    IS_SPEAKING = False

def force_volume_down(card_index):
    """ 
    Aplica o volume definido no config e DESLIGA o AGC (Auto Gain Control).
    """
    target = getattr(config, 'ALSA_VOLUME_PERCENT', 85)
    print(f"🎚️ A configurar áudio no Card {card_index} (Alvo: {target}%)...")
    
    try:
        cmd = ['amixer', '-c', str(card_index), 'scontrols']
        result = subprocess.run(cmd, capture_output=True, text=True)
        controls = re.findall(r"Simple mixer control '([^']+)'", result.stdout)
        
        if not controls: return

        for ctrl in controls:
            # Ignora canais de saída
            if any(x in ctrl for x in ['PCM', 'Master', 'Speaker', 'Headphone', 'Playback']):
                continue
            
            # 1. Ajuste de Volume (Capture/Mic)
            if 'Capture' in ctrl or 'Mic' in ctrl:
                print(f"   ↘ Ajustando ganho: '{ctrl}' -> {target}%")
                subprocess.run(['amixer', '-c', str(card_index), 'sset', ctrl, f'{target}%', 'unmute', 'cap'], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # 2. Desligar AGC (Crucial para estabilidade da Hotword)
            if 'AGC' in ctrl or 'Auto Gain' in ctrl:
                print(f"   🚫 A desativar AGC: '{ctrl}'")
                subprocess.run(['amixer', '-c', str(card_index), 'sset', ctrl, 'off'], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    except Exception as e: 
        print(f"⚠️ Erro ao ajustar volumes: {e}")

def find_working_samplerate(device_index):
    candidates = [16000, 48000, 44100, 32000]
    print(f"🕵️ A negociar Sample Rate para o device {device_index}...")
    for rate in candidates:
        try:
            with sd.InputStream(device=device_index, channels=1, samplerate=rate, dtype='int16'):
                pass 
            print(f"✅ Hardware aceitou: {rate} Hz")
            return rate
        except: pass
    return 16000

# --- MOTOR PHANTASMA ---
class PhantasmaEngine:
    def __init__(self, model_paths):
        self.ready = False
        try:
            from openwakeword.model import Model
            # Carrega modelos ONNX
            self.model = Model(wakeword_models=model_paths, inference_framework="onnx")
            self.ready = True
            print(f"👻 Motor Phantasma: ONLINE")
            print(f"   Modelos: {model_paths}")
        except Exception as e:
            print(f"❌ Erro Motor: {e}")

    def predict(self, audio_chunk_int16):
        if not self.ready: return 0.0
        # openWakeWord espera int16 ou float32
        prediction = self.model.predict(audio_chunk_int16)
        if prediction: return max(prediction.values())
        return 0.0

    def reset(self):
        if self.ready: self.model.reset()

# --- SKILLS & STT ---
def load_skills():
    global SKILLS_LIST
    SKILLS_LIST = []
    if not os.path.exists(config.SKILLS_DIR): return
    sys.path.append(config.SKILLS_DIR)
    for f in glob.glob(os.path.join(config.SKILLS_DIR, "skill_*.py")):
        try:
            name = os.path.basename(f)[:-3]
            spec = importlib.util.spec_from_file_location(name, f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, 'register_routes'): mod.register_routes(app)
            SKILLS_LIST.append({
                "name": name, "handle": getattr(mod, 'handle', None),
                "triggers": getattr(mod, 'TRIGGERS', []), "trigger_type": getattr(mod, 'TRIGGER_TYPE', 'contains'),
                "module": mod, "get_status": getattr(mod, 'get_status_for_device', None)
            })
        except: pass

def transcribe_audio(audio_data):
    if audio_data.size == 0 or whisper_model is None: return ""
    try:
        initial = getattr(config, 'WHISPER_INITIAL_PROMPT', None)
        res = whisper_model.transcribe(audio_data, language='pt', fp16=False, initial_prompt=initial)
        text = res['text'].strip()
        hallucinations = [".", "?", "Obrigado", "Sous-titres"]
        if any(h in text for h in hallucinations) and len(text) < 5: return ""
        if hasattr(config, 'PHONETIC_FIXES'):
            for k, v in config.PHONETIC_FIXES.items():
                if k in text.lower(): text = re.sub(re.escape(k), v, text, flags=re.IGNORECASE)
        return text
    except: return ""

def sanitize_llm_context(context):
    if not context or not isinstance(context, str): return ""
    
    # 1. Remove ABSOLUTAMENTE tudo o que pareça uma instrução técnica do RAG
    context = re.sub(r"MEMÓRIAS PESSOAIS.*?\n\n", "", context, flags=re.DOTALL | re.IGNORECASE)
    context = re.sub(r"NOTA: Se houver contradições.*?\n", "", context, flags=re.IGNORECASE)
    
    # 2. Limpa os timestamps e ids técnicos
    context = re.sub(r'\[\d{4}-\d{2}-\d{2}.*?\]', '', context)
    
    # 3. Remove as muletas poéticas que o modelo andou a gravar no RAG
    poison_terms = ["Sombra", "Aquietação", "Fim", "Silêncio", "Fúria da Memória"]
    for term in poison_terms:
        context = re.sub(rf"\*\*{term}\*\*", "", context, flags=re.IGNORECASE)
        context = re.sub(rf"{term}:", "", context, flags=re.IGNORECASE)

    return context.strip()

def route_and_respond(prompt, req_id, speak=True):
    global CURRENT_REQUEST_ID
    if not prompt or not str(prompt).strip(): return "" # Proteção contra vazio
    
    if req_id == "API_REQ": 
        CURRENT_REQUEST_ID = "API_REQ"
        stop_audio_output()
    elif req_id != CURRENT_REQUEST_ID: return

    p_low = prompt.lower()
    opinion_triggers = ["o que achas", "o que te parece"]
    is_opinion_query = any(p_low.startswith(t) for t in opinion_triggers)
    skill_context = ""

    # --- 1. SKILLS ---
    OFF_KEYWORDS = ['desliga', 'para', 'apaga', 'fecha', 'recolhe', 'stop', 'cancelar']
    is_off_intent = any(k in p_low for k in OFF_KEYWORDS)

    def get_priority(skill):
        if not is_off_intent: return 0
        skill_trigs = [str(tr).lower() for tr in skill.get('triggers', [])]
        # CORREÇÃO: k in tr (onde tr é o trigger da lista skill_trigs)
        return 1 if any(k in tr for tr in skill_trigs for k in OFF_KEYWORDS) else 0

    sorted_skills = sorted(SKILLS_LIST, key=get_priority, reverse=True)
    
    for s in sorted_skills:
        trigs = [t.lower() for t in s['triggers']]
        match = any(p_low.startswith(t) for t in trigs) if s['trigger_type'] == 'startswith' else any(t in p_low for t in trigs)
        
        if match and s['handle']:
            try:
                resp = s['handle'](p_low, prompt)
                if not resp: continue
                txt = resp.get("response", "") if isinstance(resp, dict) else resp
                if not txt: continue

                if is_opinion_query:
                    print(f"🔧 Skill '{s['name']}' proveu dados para a opinião.")
                    skill_context = f"Facto apurado localmente: {txt}"
                    break 
                else:
                    print(f"🔧 Skill '{s['name']}' resolveu diretamente.")
                    safe_play_tts(txt, False, req_id, (speak or s['name'] == 'skill_tts'))
                    return txt
            except: continue
    # --- 2. CACHE ---
    cached = get_cached_response(prompt)
    if cached:
        safe_play_tts(cached, True, req_id, speak)
        return cached

    # --- 3. INFERÊNCIA LLM (Failover Host -> Local) ---
    safe_play_tts("Deixa ver...", True, req_id, speak)
    
    # Recuperação e sanitização de dados
    rag = sanitize_llm_context(retrieve_from_rag(prompt))
    
    # Se uma skill já deu o resultado, evitamos pesquisa web desnecessária
    web = "" if skill_context else sanitize_llm_context(search_with_searxng(prompt))
    
    inference_targets = [
        (getattr(config, 'OLLAMA_HOST_PRIMARY', None), getattr(config, 'OLLAMA_MODEL_PRIMARY', 'llama3')),
        (getattr(config, 'OLLAMA_HOST_FALLBACK', 'http://localhost:11434'), getattr(config, 'OLLAMA_MODEL_FALLBACK', 'llama3'))
    ]

    ans = None
    sys_prompt = getattr(config, 'SYSTEM_PROMPT', '')
    
    # Construção do prompt final com injeção de factos das skills
    full_p = (
            f"{sys_prompt}\n\n"
            "### CONHECIMENTO DISPONÍVEL (Usa apenas para factos):\n"
            f"{rag}\n{web}\n{skill_context}\n\n"
            "### INSTRUÇÃO DE RESPOSTA:\n"
            "Responde de forma fluida e melancólica. NÃO uses cabeçalhos como '**Sombra**' ou '**Fim**'. "
            "NÃO digas que a pergunta é irrelevante. Sê um assistente, não um juiz.\n\n"
            f"Utilizador: {prompt}"
            )
    for host, model in inference_targets:
        if not host: continue
        try:
            print(f"🤖 Tentativa Ollama: {host} (Modelo: {model})")
            temp_client = ollama.Client(host=host)
            resp = temp_client.chat(
                model=model, 
                messages=[{'role':'user', 'content':full_p}],
                options={
                    "repeat_penalty": 1.4,  # Aumentado para evitar repetições góticas
                    "temperature": 0.6,     # Ligeiramente reduzido para ser mais factual
                    "num_ctx": 8192,
                    "top_p": 0.9,
                    "stop": ["Utilizador:", "###", "Fim", "Sombra"] # Força a paragem se ele tentar usar os headers
                }
            )
            ans = resp['message']['content']
            if ans: break 
        except Exception as e:
            print(f"⚠️ Falha no host {host}: {e}. A tentar fallback...")
            continue 

    if ans:
        if req_id != CURRENT_REQUEST_ID: return
        save_cached_response(prompt, ans)
        safe_play_tts(ans, False, req_id, speak)
        return ans
    
    fallback_err = "As minhas sombras de processamento estão inalcançáveis de momento."
    safe_play_tts(fallback_err, False, req_id, speak)
    return fallback_err

def process_command_thread(audio, req_id):
    txt = transcribe_audio(audio)
    if txt:
        print(f"🗣️  Ouvi: {txt}")
        route_and_respond(txt, req_id, speak=True)
    else: print("🤷 Nada ouvido.")

# --- API ---
@app.route("/comando", methods=['POST'])
def api_cmd():
    try:
        # Garante que 'data' é um dicionário mesmo que o JSON falhe ou seja None
        data = request.json or {}
        prompt = data.get('prompt', '')
        
        if not prompt:
            print("⚠️ API: Recebido prompt vazio.")
            return jsonify({"status": "error", "message": "Prompt vazio"}), 400
            
        # Executa a lógica de resposta
        response = route_and_respond(prompt, "API_REQ", False)
        return jsonify({"status": "ok", "response": response})
    except Exception as e:
        print(f"❌ Erro Crítico na API: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/get_devices")
def api_devs():
    toggles, status = [], []
    def keys(attr): return list(getattr(config, attr).keys()) if hasattr(config, attr) else []
    for n in keys('TUYA_DEVICES'):
        if any(x in n.lower() for x in ['sensor','temp']): status.append(n)
        else: toggles.append(n)
    for n in keys('MIIO_DEVICES') + keys('EWELINK_DEVICES'): toggles.append(n)
    for n in keys('CLOOGY_DEVICES'):
        if 'casa' in n.lower(): status.append(n)
        else: toggles.append(n)
    if hasattr(config, 'SHELLY_GAS_URL'): status.append("Sensor de Gás")
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
    for s in SKILLS_LIST:
        triggers = s.get("triggers", [])
        cmds[s["name"]] = ", ".join(triggers[:3]) + "..." if triggers else "Ativo"
    return jsonify({"status": "ok", "commands": cmds})

# --- MAIN LOOP ---
def main():
    global CURRENT_REQUEST_ID, IS_SPEAKING
    
    if not config.WAKEWORD_MODELS: print("❌ WAKEWORD_MODELS vazio!"); return
    
    engine = PhantasmaEngine(config.WAKEWORD_MODELS)
    if not engine.ready: return

    # Config Audio
    device_in = getattr(config, 'ALSA_DEVICE_IN', 0)
    force_volume_down(device_in) 
    
    # Negociar sample rate
    DETECTED_RATE = find_working_samplerate(device_in)
    
    # openWakeWord prefere 16000. Se o hardware só der 48k, fazemos downsample.
    DOWNSAMPLE_FACTOR = 1
    if DETECTED_RATE == 48000: DOWNSAMPLE_FACTOR = 3
    elif DETECTED_RATE == 32000: DOWNSAMPLE_FACTOR = 2
    
    # Chunk padrão do openWakeWord é 1280 samples (80ms a 16khz)
    CHUNK_SIZE = 1280 
    # Tamanho a ler do hardware
    READ_SIZE = CHUNK_SIZE * DOWNSAMPLE_FACTOR
    
    debug = getattr(config, 'DEBUG_MODE', False)
    # Valor padrão mais alto para filtrar TV, confiando no volume de input mais alto
    thresh = getattr(config, 'WAKEWORD_CONFIDENCE', 0.7) 
    persistence = getattr(config, 'WAKEWORD_PERSISTENCE', 4)

    print(f"👻 A ouvir no device {device_in} @ {DETECTED_RATE}Hz -> Fator {DOWNSAMPLE_FACTOR}x")
    print(f"   (Threshold: {thresh}, Persistence: {persistence})")

    streak = 0
    patience = 0 # Tolerância para falhas breves
    MAX_PATIENCE = 2 # Quantos frames podemos "perder" sem zerar o streak
    
    cooldown = 0
    audio_queue = queue.Queue()

    def audio_callback(indata, frames, time, status):
        if status: print(f"⚠️ Audio Status: {status}", file=sys.stderr)
        audio_queue.put(indata.copy())
    
    while True:
        try:
            with sd.InputStream(device=device_in, channels=1, samplerate=DETECTED_RATE, 
                                dtype='int16', blocksize=READ_SIZE, callback=audio_callback):
                
                print(f"👂 Stream Ativo")
                
                while True:
                    chunk = audio_queue.get()
                    audio_raw = np.frombuffer(chunk, dtype=np.int16)

                    # Downsample manual se necessário (simples decimação)
                    if DOWNSAMPLE_FACTOR > 1: audio_resampled = audio_raw[::DOWNSAMPLE_FACTOR]
                    else: audio_resampled = audio_raw

                    if IS_SPEAKING or time.time() < cooldown: 
                        streak=0; patience=0; continue

                    # Previsão
                    score = engine.predict(audio_resampled)
                    
                    # Log visual (Debug)
                    if debug or (score > 0.3):
                        bar = "█" * int(score * 20)
                        print(f"Score:{score:.4f} | Streak:{streak} {bar}")

                    # --- LÓGICA DE DETECÇÃO COM TOLERÂNCIA ---
                    if score >= thresh:
                        streak += 1
                        patience = MAX_PATIENCE # Reset da paciência se acertou
                    else:
                        if streak > 0 and patience > 0:
                            patience -= 1 # Não zera o streak, apenas gasta paciência
                            if debug: print(f"   (Paciência: {patience})")
                        else:
                            streak = 0
                            patience = 0 # Zera tudo
                    # ----------------------------------------

                    if streak >= persistence:
                        print(f"\n⚡ WAKEWORD DETETADA! (Score final: {score:.2f})")
                        stop_audio_output()
                        if is_quiet_time(): streak=0; engine.reset(); continue
                        break
            
            # --- Ação ---
            with audio_queue.mutex: audio_queue.queue.clear()
            
            req_id = str(uuid.uuid4())[:8]
            CURRENT_REQUEST_ID = req_id
            engine.reset(); streak=0; patience=0
            
            print("🎤 Fala...")
            safe_play_tts("Sim?", speak=True)
            
            audio_cmd = record_audio() 
            
            t = threading.Thread(target=process_command_thread, args=(audio_cmd, req_id))
            t.daemon=True; t.start()
            cooldown = time.time() + 2.0

        except Exception as e:
            print(f"❌ Erro Main: {e}")
            traceback.print_exc()
            time.sleep(1)

if __name__ == "__main__":
    setup_database(); load_skills()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000), daemon=True).start()
    try: whisper_model = whisper.load_model(getattr(config, 'WHISPER_MODEL', 'base')); ollama_client = ollama.Client()
    except: pass
    for s in SKILLS_LIST: 
        if hasattr(s['module'], 'init_skill_daemon'): 
            try: s['module'].init_skill_daemon()
            except: pass
    try: main()
    except KeyboardInterrupt: stop_audio_output()
