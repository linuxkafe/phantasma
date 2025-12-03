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
from flask import Flask, request, jsonify
import pvporcupine
import sounddevice as sd 
import subprocess 

# --- NOSSOS MÓDULOS ---
import config
from audio_utils import *
from data_utils import setup_database, retrieve_from_rag, get_cached_response, save_cached_response
from tools import search_with_searxng

# --- Carregamento Dinâmico de Skills ---
SKILLS_LIST = []

def load_skills():
    print("A carregar skills...")
    skill_files = glob.glob(os.path.join(config.SKILLS_DIR, "skill_*.py"))
    for f in skill_files:
        try:
            skill_name = os.path.basename(f)[:-3]
            spec = importlib.util.spec_from_file_location(skill_name, f)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            raw_triggers = getattr(module, 'TRIGGERS', [])
            triggers_lower = [t.lower() for t in raw_triggers]

            # --- FIX: handle é agora opcional (para skills como skill_ui) ---
            handle_func = getattr(module, 'handle', None)

            SKILLS_LIST.append({
                "name": skill_name,
                "module": module, 
                "trigger_type": getattr(module, 'TRIGGER_TYPE', 'contains'),
                "triggers": raw_triggers,
                "triggers_lower": triggers_lower,
                "handle": handle_func,
                "get_status": getattr(module, 'get_status_for_device', None)
                })
            print(f"  -> Skill '{skill_name}' carregada.")
        except Exception as e:
            print(f"AVISO: Falha ao carregar {f}: {e}")

# --- Globais ---
whisper_model = None
ollama_client = None
conversation_history = []
GREETINGS_CACHE_DIR = os.path.join(config.BASE_DIR, "sounds/greetings")

# --- IA Core ---
def transcribe_audio(audio_data):
    if audio_data.size == 0: return ""
    print(f"A transcrever (Modelo: {config.WHISPER_MODEL})...")
    try:
        res = whisper_model.transcribe(audio_data, language='pt', fp16=False, initial_prompt=config.WHISPER_INITIAL_PROMPT, no_speech_threshold=0.6)
        return res['text'].strip()
    except Exception as e: print(f"Erro transcrição: {e}"); return ""

def process_with_ollama(prompt):
    global conversation_history
    if not prompt: return "Não percebi."
    
    # Execução Paralela RAG + Web
    rag_content = ""
    web_content = ""
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            f_rag = executor.submit(retrieve_from_rag, prompt)
            f_web = executor.submit(search_with_searxng, prompt)
            rag_content = f_rag.result()
            web_content = f_web.result()
    except Exception as e:
        print(f"Aviso Contexto: {e}")

    final = f"{web_content}\n{rag_content}\nPERGUNTA: {prompt}"
    conversation_history.append({'role': 'user', 'content': final})
    
    try:
        print(f"A pensar ({config.OLLAMA_MODEL_PRIMARY})...")
        cli = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        resp = cli.chat(
            model=config.OLLAMA_MODEL_PRIMARY,
            messages=conversation_history,
            options={'num_ctx': config.OLLAMA_CONTEXT_SIZE}
        )
        content = resp['message']['content']
        conversation_history.append({'role': 'assistant', 'content': content})
        return content
    except: return "Erro no cérebro."

def route_and_respond(user_prompt, speak_response=True):
    try:
        llm_response = None
        user_prompt_lower = user_prompt.lower()

        # 1. ROUTER DE SKILLS
        for skill in SKILLS_LIST:
            # Skills de UI ou sem triggers são ignoradas aqui
            if skill["trigger_type"] == "none": continue
            # Segurança extra: se a skill não tiver handle, salta
            if not skill["handle"]: continue

            triggered = False
            triggers_to_check = skill.get("triggers_lower", [])

            if skill["trigger_type"] == "startswith":
                if any(user_prompt_lower.startswith(trigger) for trigger in triggers_to_check):
                    triggered = True
            elif skill["trigger_type"] == "contains":
                if any(trigger in user_prompt_lower for trigger in triggers_to_check):
                    triggered = True

            if triggered:
                print(f"A ativar skill: {skill['name']}")
                llm_response = skill["handle"](user_prompt_lower, user_prompt)
                if llm_response:
                    break 

        # 2. FALLBACK: CACHE E OLLAMA
        if llm_response is None:
            cached_text = get_cached_response(user_prompt)
            if cached_text:
                llm_response = cached_text
                conversation_history.append({'role': 'user', 'content': user_prompt})
                conversation_history.append({'role': 'assistant', 'content': cached_text})

            if llm_response is None:
                try:
                    cpu_cores = os.cpu_count() or 1
                    load_threshold = cpu_cores * 0.75
                    load_1min, _, _ = os.getloadavg()
                    if load_1min > load_threshold:
                        print(f"AVISO: Carga alta ({load_1min:.2f}). Ollama ignorado.")
                        llm_response = "O sistema está ocupado. Tenta mais tarde."
                except: pass

                if llm_response is None:
                    if speak_response:
                        thinking_phrases = ["Deixa-me pensar...", "Ok, deixa ver...", "Um segundo.", "A verificar."]
                        play_tts(random.choice(thinking_phrases))

                    llm_response = process_with_ollama(prompt=user_prompt)

                    if llm_response and "Ocorreu um erro" not in llm_response:
                        save_cached_response(user_prompt, llm_response)

        # --- Processamento da Resposta ---
        if isinstance(llm_response, dict):
            if llm_response.get("stop_processing"):
                return llm_response.get("response", "") 
            llm_response = llm_response.get("response", str(llm_response))

        if speak_response:
            play_tts(llm_response)

        return llm_response

    except Exception as e:
        print(f"ERRO CRÍTICO no router de intenções: {e}")
        error_msg = f"Ocorreu um erro ao processar: {e}"
        if speak_response: play_tts(error_msg)
        return error_msg

def process_user_query():
    try:
        text = transcribe_audio(record_audio())
        print(f"User: {text}")
        if text: route_and_respond(text)
    except: pass

# --- OTIMIZAÇÃO: Cache de Greetings ---
def prepare_greetings_cache():
    greetings = {"diz_coisas": "Diz coisas!", "aqui_estou": "Aqui estou!", "diz_la": "Diz lá.", "ei": "Ei!", "sim": "Sim?"}
    if not os.path.exists(GREETINGS_CACHE_DIR): os.makedirs(GREETINGS_CACHE_DIR)
    for filename, text in greetings.items():
        wav_path = os.path.join(GREETINGS_CACHE_DIR, f"{filename}.wav")
        if not os.path.exists(wav_path):
            try: subprocess.run(f"echo '{text}' | piper --model {config.TTS_MODEL_PATH} --output_file {wav_path}", shell=True, check=True)
            except: pass

def play_cached_greeting():
    try:
        wav_files = glob.glob(os.path.join(GREETINGS_CACHE_DIR, "*.wav"))
        if not wav_files: play_tts("Sim?"); return
        subprocess.run(['aplay', '-D', config.ALSA_DEVICE_OUT, '-q', random.choice(wav_files)], check=False)
    except: pass

# --- API Server ---
app = Flask(__name__)

@app.route("/comando", methods=['POST'])
def api_command():
    d = request.json; p = d.get('prompt')
    if not p: return jsonify({"status":"err"}), 400
    if p.lower().startswith("diz "): play_tts(p[4:].strip()); return jsonify({"status":"ok"})
    return jsonify({"status":"ok", "response": route_and_respond(p, False)})

@app.route("/device_status")
def api_status():
    nick = request.args.get('nickname')
    if not nick: return jsonify({"state": "unknown"}), 400
    nick_lower = nick.lower()
    for s in SKILLS_LIST:
        status_func = s.get('get_status')
        if status_func:
            if s["name"] == "skill_shellygas" and "gás" not in nick_lower and "gas" not in nick_lower: continue 
            try:
                res = status_func(nick)
                if res and res.get('state') != 'unreachable': return jsonify(res)
            except: pass
    return jsonify({"state": "unreachable"})

@app.route("/device_action", methods=['POST'])
def api_action():
    d = request.json
    return jsonify({"status":"ok", "response": route_and_respond(f"{d.get('action')} o {d.get('device')}", False)})

@app.route("/get_devices")
def api_devices():
    toggles = []; status = []
    def get_device_keys(attr):
        if hasattr(config, attr) and isinstance(getattr(config, attr), dict): return list(getattr(config, attr).keys())
        return []
    for n in get_device_keys('TUYA_DEVICES'):
        if any(x in n.lower() for x in ['sensor','temperatura','humidade']): status.append(n)
        else: toggles.append(n)
    for n in get_device_keys('MIIO_DEVICES'): toggles.append(n)
    for n in get_device_keys('CLOOGY_DEVICES'):
        if 'casa' in n.lower(): status.append(n)
        else: toggles.append(n)
    for n in get_device_keys('EWELINK_DEVICES'): toggles.append(n)
    if hasattr(config, 'SHELLY_GAS_URL') and config.SHELLY_GAS_URL: status.append("Sensor de Gás")
    return jsonify({"status":"ok", "devices": {"toggles": toggles, "status": status}})

@app.route("/help", methods=['GET'])
def get_help():
    try:
        commands = {}
        commands["diz"] = "TTS. Ex: diz olá"
        for skill in SKILLS_LIST:
            skill_name_short = skill["name"].replace("skill_", "")
            triggers = skill.get("triggers", [])
            if triggers:
                trigger_summary = ', '.join(triggers[:4])
                if len(triggers) > 4: trigger_summary += ', ...'
                description = f"Ativado por '{skill.get('trigger_type', 'N/A')}': {trigger_summary}"
            else: description = "Comando ativo"
            commands[skill_name_short] = description
        return jsonify({"status": "ok", "commands": commands})
    except: return jsonify({"status": "erro"}), 500

def start_api_server(host='0.0.0.0', port=5000):
    logging.getLogger('werkzeug').setLevel(logging.ERROR); app.run(host=host, port=port)

def main():
    """ O loop principal: ESCUTAR com Porcupine, PROCESSAR, REPETIR """
    pv = None
    pa = os.path.dirname(pvporcupine.__file__)
    stream = None

    try:
        models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
        ppn_files = glob.glob(os.path.join(models_dir, '*.ppn'))

        if not ppn_files:
            HOTWORD_CUSTOM_PATH = '/opt/phantasma/models/ei-fantasma_pt_linux_v3_0_0.ppn'
            ppn_path = HOTWORD_CUSTOM_PATH
        else:
            ppn_path = ppn_files[0]

        HOTWORD_NAME = os.path.basename(ppn_path).replace('.ppn', '')

        pv = pvporcupine.create(
                access_key=config.ACCESS_KEY,
                keyword_paths=[ppn_path],   
                model_path=os.path.join(pa, 'lib/common/porcupine_params_pt.pv'),
                sensitivities=[0.4] 
                )
        chunk_size = pv.frame_length

        while True:
            print(f"\n--- A escutar pela hotword '{HOTWORD_NAME}' (Stream: {config.ALSA_DEVICE_IN}) ---")
            stream = sd.InputStream(
                    device=config.ALSA_DEVICE_IN, 
                    channels=1, 
                    samplerate=pv.sample_rate, 
                    dtype='int16', 
                    blocksize=chunk_size
                    )
            stream.start()

            while True: 
                chunk, overflowed = stream.read(chunk_size)
                if overflowed: pass 

                chunk_flat = chunk.flatten()
                keyword_index = pv.process(chunk_flat)

                if keyword_index == 0: 
                    print(f"\n\n**** HOTWORD '{HOTWORD_NAME}' DETETADA! ****\n")

                    stream.stop()
                    stream.close()
                    stream = None
                    
                    play_cached_greeting()
                    process_user_query() 

                    print("Processamento concluído. A voltar à escuta...")
                    break 

    except KeyboardInterrupt:
        print("\nA sair...")
    except Exception as e:
        print(f"\nOcorreu um erro inesperado no loop de escuta (Porcupine): {e}")
        print(traceback.format_exc())
    finally:
        if stream is not None:
            stream.stop()
            stream.close()
        if pv is not None:
            pv.delete()
            print("Recursos do Porcupine libertados.")
        sys.exit(0)

if __name__ == "__main__":
    if config.OLLAMA_THREADS > 0:
        os.environ['OLLAMA_NUM_THREAD'] = str(config.OLLAMA_THREADS)
    try:
        if config.WHISPER_THREADS > 0:
            torch.set_num_threads(config.WHISPER_THREADS)
        if not torch.cuda.is_available():
            print("INFO: CUDA não disponível. Whisper em CPU.")
    except Exception as e:
        print(f"AVISO: Threads Torch: {e}")

    setup_database()
    load_skills()
    
    # --- NOVO: Registar rotas web das skills (UI, etc) ---
    print("A registar rotas web das skills...")
    for skill in SKILLS_LIST:
        module = skill["module"]
        if hasattr(module, 'register_routes'):
            try:
                module.register_routes(app)
                print(f"  -> Rotas registadas para '{skill['name']}'")
            except Exception as e:
                print(f"ERRO ao registar rotas de '{skill['name']}': {e}")
    # ------------------------------------------------
    
    prepare_greetings_cache()

    try:
        print(f"A carregar modelos (Whisper: {config.WHISPER_MODEL}, Ollama: {config.OLLAMA_MODEL_PRIMARY})...")
        whisper_model = whisper.load_model(config.WHISPER_MODEL, device="cpu")
        print("A aquecer o modelo Whisper...")
        whisper_model.transcribe(np.zeros(16000, dtype=np.float32), language='pt') 
        ollama_client = ollama.Client()
        print("Modelos carregados.")
    except Exception as e:
        print(f"ERRO: Falha ao carregar modelos: {e}")
        sys.exit(1)

    print("A inicializar histórico...")
    conversation_history = [{'role': 'system', 'content': config.SYSTEM_PROMPT}]
    
    threading.Thread(target=start_api_server, daemon=True).start()

    print("\n--- A iniciar daemons de skills ---")
    for skill in SKILLS_LIST:
        module = skill["module"]
        if hasattr(module, 'init_skill_daemon'):
            try:
                module.init_skill_daemon()
            except Exception as e:
                print(f"ERRO CRÍTICO skill '{skill['name']}': {e}")

    main()
