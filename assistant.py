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

# Imports para API e Threading
import threading
import logging
from flask import Flask, request, jsonify
import pvporcupine

# --- NOSSOS MÓDULOS ---
import config
from audio_utils import *
from data_utils import *
from tools import search_with_searxng
# ----------------------

# --- Carregamento Dinâmico de Skills ---
SKILLS_LIST = []

def load_skills():
    """ Carrega dinamicamente todas as 'skills' da pasta /skills """
    print("A carregar skills...")
    skill_files = glob.glob(os.path.join(config.SKILLS_DIR, "skill_*.py"))
    
    for f in skill_files:
        try:
            skill_name = os.path.basename(f)[:-3]
            spec = importlib.util.spec_from_file_location(skill_name, f)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Regista a skill
            SKILLS_LIST.append({
                "name": skill_name,
                "trigger_type": module.TRIGGER_TYPE,
                "triggers": module.TRIGGERS,
                "handle": module.handle
            })
            print(f"  -> Skill '{skill_name}' carregada.")
        except Exception as e:
            print(f"AVISO: Falha ao carregar a skill {f}: {e}")
# -----------------------------------

# --- Declaração de Variáveis Globais ---
whisper_model = None
ollama_client = None
conversation_history = []

# --- Funções de Processamento de IA (Dependentes de Globais) ---

def transcribe_audio(audio_data):
    """ Converte dados de áudio numpy para texto usando Whisper """
    if audio_data.size == 0:
        return ""
        
    print(f"A transcrever áudio (Modelo: {config.WHISPER_MODEL})...")
    try:
        result = whisper_model.transcribe(
            audio_data, 
            language='pt', 
            fp16=False,
            initial_prompt=config.WHISPER_INITIAL_PROMPT, 
            no_speech_threshold=0.6 # Filtro anti-"uhh"
        )
        
        text = result['text'].strip()
        if not text:
            print("Transcrição (VAD): Nenhum discurso detetado.")
            return "" 
            
        return text
    except Exception as e:
        print(f"Erro na transcrição: {e}")
        return ""

def process_with_ollama(prompt):
    """ Envia o prompt para o Ollama, mantendo o histórico da conversa. """
    global conversation_history
    if not prompt:
        return "Desculpe, não o consegui ouvir."

    # --- MODIFICADO: RAG Duplo (BD + Web) ---
    
    # 1. Recupera os contextos
    rag_context = retrieve_from_rag(prompt)
    web_context = search_with_searxng(prompt)
    
    # 2. Constrói o prompt final para o LLM
    final_prompt = prompt # A pergunta original
    
    # Adiciona o contexto da web (se existir)
    if web_context:
        final_prompt = f"{web_context}\n\nPERGUNTA: {prompt}"
    
    # Adiciona o contexto da BD (se existir, vem primeiro)
    if rag_context:
        final_prompt = f"{rag_context}\n\n{final_prompt}"
    
    # ----------------------------------------

    # 3. Adiciona a pergunta (com contexto) ao histórico
    current_user_message = {'role': 'user', 'content': final_prompt}
    conversation_history.append(current_user_message)
    
    try:
        print(f"A pensar (Ollama: {config.OLLAMA_MODEL_PRIMARY}, Timeout: {config.OLLAMA_TIMEOUT}s)...")
        primary_client = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        response = primary_client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=conversation_history)
        llm_response_content = response['message']['content']
        conversation_history.append({'role': 'assistant', 'content': llm_response_content})
            
        return llm_response_content
        
    except httpx.TimeoutException as e_timeout:
        print(f"\nAVISO: Timeout de {config.OLLAMA_TIMEOUT}s atingido com {config.OLLAMA_MODEL_PRIMARY}.")
        print(f"A tentar com o modelo fallback: {config.OLLAMA_MODEL_FALLBACK}...\n")
        try:
            response = ollama_client.chat(model=config.OLLAMA_MODEL_FALLBACK, messages=conversation_history)
            llm_response_content = response['message']['content']
            conversation_history.append({'role': 'assistant', 'content': llm_response_content})
            return llm_response_content
        except Exception as e_fallback:
            print(f"ERRO: O modelo fallback {config.OLLAMA_MODEL_FALLBACK} também falhou: {e_fallback}")
            conversation_history.pop() # Remove o 'user'
            return "Ocorreu um erro ao processar o seu pedido em ambos os modelos."
            
    except Exception as e:
        print(f"ERRO Ollama ({config.OLLAMA_MODEL_PRIMARY}): {e}")
        conversation_history.pop() # Remove o 'user'
        return "Ocorreu um erro ao processar o seu pedido."
# --- Funções "Cérebro" (Lógica Principal) ---

def route_and_respond(user_prompt):
    """
    Esta é a função "cérebro" central.
    Tenta executar skills; se falhar, envia para o Ollama.
    """
    try:
        llm_response = None
        user_prompt_lower = user_prompt.lower()
        
        # --- NOVO ROUTER DE SKILLS ---
        for skill in SKILLS_LIST:
            triggered = False
            if skill["trigger_type"] == "startswith":
                if any(user_prompt_lower.startswith(trigger) for trigger in skill["triggers"]):
                    triggered = True
            elif skill["trigger_type"] == "contains":
                if any(trigger in user_prompt_lower for trigger in skill["triggers"]):
                    triggered = True
            
            if triggered:
                print(f"A ativar skill: {skill['name']}")
                llm_response = skill["handle"](user_prompt_lower, user_prompt)
                if llm_response:
                    break # Skill foi executada
        # --- FIM DO ROUTER ---

        # 5. FALLBACK: OLLAMA (Se nenhuma skill foi ativada)
        if llm_response is None:
            
            # --- Verificação de Carga do Sistema ---
            try:
                cpu_cores = os.cpu_count() or 1 # Obtém o nº de núcleos (fallback para 1)
                load_threshold = cpu_cores * 0.75 # Define o limite em 75% da capacidade
                load_1min, _, _ = os.getloadavg() # Pega no 'load average' de 1 min
                
                if load_1min > load_threshold:
                    print(f"AVISO: Carga do sistema alta ({load_1min:.2f} > {load_threshold:.2f}). A chamada ao Ollama foi ignorada.")
                    llm_response = "O sistema está um pouco ocupado agora, chefe. Tenta perguntar-me isso daqui a um bocado."
                    
            except Exception as e:
                print(f"AVISO: Não foi possível verificar a carga do sistema: {e}")
            # --------------------------------------------------

            if llm_response is None:
                thinking_phrases = [
                    "Deixa-me pensar sobre esse assunto um bocadinho...",
                    "Ok, chefe. A processar isso!",
                    "Estou a ver... espera um segundo.",
                    "Boa pergunta! Vou verificar os meus circuitos."
                ]
                play_tts(random.choice(thinking_phrases))
                
                # <--- LINHA REMOVIDA DAQUI ---
                # save_to_rag(user_prompt) # <-- REMOVIDO
                # -----------------------------
                
                llm_response = process_with_ollama(prompt=user_prompt)
        
        # --- Processamento da Resposta ---
        
        if isinstance(llm_response, dict):
            if llm_response.get("stop_processing"):
                return llm_response.get("response", "") # Retorna para a API, mas não fala

        play_tts(llm_response)
        return llm_response
        
    except Exception as e:
        print(f"ERRO CRÍTICO no router de intenções: {e}")
        error_msg = f"Ocorreu um erro ao processar: {e}"
        play_tts(error_msg)
        return error_msg

def process_user_query():
    """ Pipeline apenas para ÁUDIO: Ouve, transcreve, e envia para o router. """
    try:
        audio_data = record_audio()
        user_prompt = transcribe_audio(audio_data)
        
        print(f"Utilizador: {user_prompt}")
        
        if user_prompt: # Só processa se o Whisper tiver detetado fala
            route_and_respond(user_prompt)
        else:
            print("Nenhum texto transcrito, a voltar à escuta.")
        
    except Exception as e:
        print(f"ERRO CRÍTICO no pipeline de processamento de áudio: {e}")

# --- Bloco do Servidor API (Flask) ---
app = Flask(__name__)
@app.route("/comando", methods=['POST'])
def handle_command():
    """ Endpoint da API para receber comandos por texto. """
    try:
        data = request.json
        prompt = data.get('prompt')
        if not prompt:
            return jsonify({"status": "erro", "message": "Prompt em falta"}), 400
        print(f"\n[Comando API Recebido]: {prompt}")
        if prompt.lower().startswith("diz "):
            text_to_say = prompt[len("diz "):].strip()
            print(f"API: A executar TTS direto.")
            play_tts(text_to_say)
            return jsonify({"status": "ok", "action": "tts_directo", "text": text_to_say})
        else:
            print("API: A enviar prompt para o router de intenções...")
            response_text = route_and_respond(prompt)
            return jsonify({"status": "ok", "action": "comando_processado", "response": response_text})
    except Exception as e:
        print(f"ERRO no endpoint /comando: {e}")
        return jsonify({"status": "erro", "message": str(e)}), 500

@app.route("/help", methods=['GET'])
def get_help():
    """ Endpoint da API para listar os comandos (skills) disponíveis. """
    try:
        commands = {}

        # 1. Adiciona o comando 'diz' (que está no 'handle_command')
        commands["diz"] = "Faz o assistente dizer (TTS) o texto. Ex: diz olá"

        # 2. Adiciona as skills carregadas dinamicamente
        # (Lê a variável global SKILLS_LIST)
        for skill in SKILLS_LIST:
            name = skill["name"].replace("skill_", "") # ex: "calculator"

            # Tenta criar uma descrição a partir dos triggers
            desc = f"Ativado por '{skill['trigger_type']}': {', '.join(skill['triggers'])}"
            commands[name] = desc

        # 3. Adiciona o fallback
        commands["[outra frase]"] = "Envia o prompt para o Ollama (com RAG e pesquisa web)."

        return jsonify({"status": "ok", "commands": commands})

    except Exception as e:
        print(f"ERRO no endpoint /help: {e}")
        return jsonify({"status": "erro", "message": str(e)}), 500

def start_api_server(host='localhost', port=5000):
    """ Inicia o servidor Flask (sem os logs normais). """
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    print(f"\n--- Servidor API a escutar em http://{host}:{port} ---")
    app.run(host=host, port=port)

# --- BLOCO 3: Loop Principal (Ouvinte de Hotword) ---
def main_loop():
    """ O loop principal: ESCUTAR com Porcupine, PROCESSAR, REPETIR """
    porcupine = None
    stream = None
    try:
        print(f"A carregar o modelo de hotword: '{config.HOTWORD_KEYWORD}' (via Porcupine)...")
        
        porcupine = pvporcupine.create(
            access_key=config.ACCESS_KEY,
            keywords=[config.HOTWORD_KEYWORD],
            sensitivities=[0.3]
        )
        
        chunk_size = porcupine.frame_length

        while True:
            print(f"\n--- A escutar pela hotword '{config.HOTWORD_KEYWORD}' (via Porcupine) ---")
            
            stream = sd.InputStream(
                device=config.ALSA_DEVICE_IN, 
                channels=1, 
                samplerate=porcupine.sample_rate, 
                dtype='int16', 
                blocksize=chunk_size
            )
            stream.start()

            while True: 
                chunk, overflowed = stream.read(chunk_size)
                if overflowed:
                    print("AVISO: Overflow de áudio (Input não está a ser lido a tempo)")
                
                chunk = chunk.flatten()
                keyword_index = porcupine.process(chunk)
                
                if keyword_index == 0: # 0 é o índice de "bumblebee"
                    print(f"\n\n**** HOTWORD '{config.HOTWORD_KEYWORD}' DETETADA! ****\n")
                    stream.stop()
                    stream.close()
                    stream = None
                    
                    play_random_music_snippet()
                    
                    greetings = ["Estou a postos!", "Aqui estou!", "Diz lá, chefe!", "Bumblebee ao teu dispor!"]
                    greeting = random.choice(greetings)
                    play_tts(greeting)
                    
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
        if porcupine is not None:
            porcupine.delete()
            print("Recursos do Porcupine libertados.")
        sys.exit(0)

if __name__ == "__main__":
    
    # Define as threads globais
    if config.OLLAMA_THREADS > 0:
        os.environ['OLLAMA_NUM_THREAD'] = str(config.OLLAMA_THREADS)
        print(f"INFO: A limitar threads do Ollama a {config.OLLAMA_THREADS} (Apenas para o snap service)")
    try:
        if config.WHISPER_THREADS > 0:
            torch.set_num_threads(config.WHISPER_THREADS)
            print(f"INFO: A limitar threads do Torch/Whisper a {config.WHISPER_THREADS}")
        if not torch.cuda.is_available():
            print("INFO: CUDA não disponível. A forçar Whisper a correr em CPU.")
    except Exception as e:
        print(f"AVISO: Falha ao definir threads do Torch: {e}")

    # Inicializa a BD
    setup_database()

    # Carrega as skills dinamicamente
    load_skills()

    # Carrega os modelos pesados
    try:
        print(f"A carregar modelos pesados (Whisper: {config.WHISPER_MODEL}, Ollama: {config.OLLAMA_MODEL_PRIMARY})...")
        whisper_model = whisper.load_model(config.WHISPER_MODEL, device="cpu")
        ollama_client = ollama.Client()
        print("Modelos carregados com sucesso.")
    except Exception as e:
        print(f"ERRO: Falha ao carregar modelos: {e}")
        sys.exit(1)

    # Inicializa o Histórico de Conversa
    print("A inicializar o histórico de conversa (memória de sessão)...")
    conversation_history = [
        {'role': 'system', 'content': config.SYSTEM_PROMPT}
    ]

    # Iniciar API e Loop de Voz
    api_thread = threading.Thread(target=start_api_server, daemon=True)
    api_thread.start()
    main_loop()
