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

            # Prepara o registo da skill
            skill_registry_entry = {
                "name": skill_name,
                "trigger_type": module.TRIGGER_TYPE,
                "triggers": module.TRIGGERS,
                "handle": module.handle
            }
            
            # --- MODIFICAÇÃO: Verifica se a skill tem a função de status ---
            if hasattr(module, 'get_status_for_device'):
                print(f"  -> '{skill_name}' tem a função 'get_status_for_device'.")
                skill_registry_entry['get_status'] = module.get_status_for_device
            # ---------------------------------------------------------------
            
            # Regista a skill
            SKILLS_LIST.append(skill_registry_entry)
            print(f"  -> Skill '{skill_name}' carregada.")
            
        except Exception as e:
            print(f"AVISO: Falha ao carregar a skill {f}: {e}")
# -----------------------------------

# --- Declaração de Variáveis Globais ---
whisper_model = None
ollama_client = None
conversation_history = []
# --- Cache volátil (em memória) para respostas do Ollama ---
volatile_cache = {}
# -----------------------------------------------------------------

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

# --- FUNÇÃO NOVA (VAD RMS) ---
def calculate_rms(audio_chunk):
    """ Calcula o Root Mean Square de um chunk de áudio numpy. """
    # Garante que o input é float para evitar overflow no cálculo ao quadrado
    if audio_chunk.dtype != np.float32:
        # Converte int16 (de -32768 a 32767) para float32 (de -1.0 a 1.0)
        audio_chunk = audio_chunk.astype(np.float32) / 32768.0
        
    return np.sqrt(np.mean(audio_chunk**2))
# --- FIM DA FUNÇÃO NOVA ---


# --- Funções "Cérebro" (Lógica Principal) ---
def route_and_respond(user_prompt, speak_response=True):
    """
    Esta é a função "cérebro" central.
    Tenta executar skills; se falhar, envia para o Ollama.
    Recebe 'speak_response' para decidir se deve falar a resposta (Voz=True, API=False).
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

            # --- Verificação do Cache Volátil ---
            if user_prompt in volatile_cache:
                print("CACHE: Resposta encontrada no cache volátil.")
                llm_response = volatile_cache[user_prompt]

            # Se NÃO estava no cache (llm_response ainda é None)
            if llm_response is None:
                # --- Verificação de Carga do Sistema ---
                try:
                    cpu_cores = os.cpu_count() or 1 # Obtém o nº de núcleos (fallback para 1)
                    load_threshold = cpu_cores * 0.75 # Define o limite em 75% da capacidade
                    load_1min, _, _ = os.getloadavg() # Pega no 'load average' de 1 min

                    if load_1min > load_threshold:
                        print(f"AVISO: Carga do sistema alta ({load_1min:.2f} > {load_threshold:.2f}). A chamada ao Ollama foi ignorada.")
                        llm_response = "O sistema está um pouco ocupado agora. Tenta perguntar-me isso daqui a um bocado."

                except Exception as e:
                    print(f"AVISO: Não foi possível verificar a carga do sistema: {e}")
                # --------------------------------------------------

                if llm_response is None:

                    # --- MODIFICADO: Só diz "a pensar" se for para falar ---
                    if speak_response:
                        thinking_phrases = [
                                "Deixa-me pensar sobre esse assunto e já te digo algo...",
                                "Ok, deixa lá ver...",
                                "Estou a ver... espera um segundo.",
                                "Boa pergunta! Vou verificar os meus circuitos."
                                ]
                        play_tts(random.choice(thinking_phrases))
                    # ----------------------------------------------------

                    llm_response = process_with_ollama(prompt=user_prompt)

                    # --- Guardar no Cache Volátil ---
                    if llm_response and "Ocorreu um erro" not in llm_response:
                        print(f"CACHE: A guardar resposta para o prompt: '{user_prompt}'")
                        volatile_cache[user_prompt] = llm_response

        # --- Processamento da Resposta ---

        if isinstance(llm_response, dict):
            if llm_response.get("stop_processing"):
                # Skills como a de música já trataram do seu próprio TTS.
                # Apenas retornamos o texto para o log/API.
                return llm_response.get("response", "") 

        # --- MODIFICADO: Só fala a resposta final se a flag estiver ativa ---
        if speak_response:
            play_tts(llm_response)
        
        # Retorna sempre o texto (para a API poder usá-lo)
        return llm_response

    except Exception as e:
        print(f"ERRO CRÍTICO no router de intenções: {e}")
        error_msg = f"Ocorreu um erro ao processar: {e}"
        # Só fala o erro se a chamada original quisesse falar
        if speak_response:
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
        
        # A exceção: "diz" DEVE falar
        if prompt.lower().startswith("diz "):
            text_to_say = prompt[len("diz "):].strip()
            print(f"API: A executar TTS direto.")
            play_tts(text_to_say)
            return jsonify({"status": "ok", "action": "tts_directo", "text": text_to_say})
        else:
            print("API: A enviar prompt para o router (sem voz)...")
            # --- MODIFICADO: Passa a flag speak_response=False ---
            response_text = route_and_respond(prompt, speak_response=False)
            # ----------------------------------------------------
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

# Interface Web

@app.route("/")
def get_frontend_ui():
    """ Serve a página HTML principal do frontend (Mobile Fix + Sensores + Watts) """

    html_content = """
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>Phantasma UI</title>
        <style>
            :root { --bg-color: #121212; --chat-bg: #1e1e1e; --user-msg: #2d2d2d; --ia-msg: #005a9e; --text: #e0e0e0; }
            
            body { 
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; 
                background: var(--bg-color); color: var(--text); 
                display: flex; flex-direction: column; 
                height: 100vh; height: 100dvh; 
                margin: 0; overflow: hidden;
            }
            
            /* --- HEADER --- */
            #header-strip {
                display: flex; align-items: center; background: #181818; border-bottom: 1px solid #333; height: 85px; flex-shrink: 0;
            }
            #brand {
                display: flex; flex-direction: column; align-items: center; justify-content: center;
                padding: 0 15px; min-width: 70px; height: 100%;
                border-right: 1px solid #333; background: #151515;
                cursor: pointer; user-select: none; z-index: 10;
            }
            #brand:active { background: #222; }
            #brand-logo { font-size: 1.8rem; animation: floatGhost 3s ease-in-out infinite; }
            #brand-name { font-size: 0.7rem; font-weight: bold; color: #666; margin-top: 2px; letter-spacing: 1px; }

            /* --- DEVICE SCROLL --- */
            #topbar {
                flex: 1; display: flex; align-items: center; overflow-x: auto; 
                white-space: nowrap; -webkit-overflow-scrolling: touch;
                height: 100%; padding-left: 10px; gap: 10px; scrollbar-width: none;
            }
            #topbar::-webkit-scrollbar { display: none; }

            /* --- SWITCHES (Luzes/Tomadas) --- */
            .device-toggle { 
                display: inline-flex; flex-direction: column; align-items: center; justify-content: center;
                opacity: 0.5; transition: all 0.3s; min-width: 60px; 
                background: #222; padding: 4px; border-radius: 8px; margin-top: 5px;
            }
            .device-toggle.loaded { opacity: 1; border: 1px solid #333; }
            .device-toggle.active .device-icon { filter: grayscale(0%); }
            .device-icon { font-size: 1.2rem; margin-bottom: 2px; filter: grayscale(100%); transition: filter 0.3s; }
            .device-label { font-size: 0.65rem; color: #aaa; max-width: 65px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
            
            .switch { position: relative; display: inline-block; width: 36px; height: 20px; }
            .switch input { opacity: 0; width: 0; height: 0; }
            .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #444; transition: .4s; border-radius: 34px; }
            .slider:before { position: absolute; content: ""; height: 14px; width: 14px; left: 3px; bottom: 3px; background-color: white; transition: .4s; border-radius: 50%; }
            input:checked + .slider { background-color: var(--ia-msg); }
            input:checked + .slider:before { transform: translateX(16px); }

            /* --- SENSORES (Temperatura/Humidade) --- */
            .device-sensor {
                display: inline-flex; flex-direction: column; align-items: center; justify-content: center;
                background: #252525; padding: 5px 8px; border-radius: 8px; margin-top: 5px;
                border: 1px solid #333; min-width: 60px; height: 52px; box-sizing: border-box;
            }
            .sensor-data { font-size: 0.75rem; color: #4db6ac; font-weight: bold; display: flex; gap: 4px; }
            .sensor-label { font-size: 0.55rem; color: #888; margin-top: 3px; max-width: 65px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

            /* --- CHAT AREA --- */
            #main { flex: 1; display: flex; flex-direction: column; overflow: hidden; position: relative; }
            #chat-log { 
                flex: 1; padding: 15px; overflow-y: auto; scroll-behavior: smooth;
                display: flex; flex-direction: column; gap: 15px;
            }
            .msg-row { display: flex; width: 100%; align-items: flex-end; }
            .msg-row.user { justify-content: flex-end; }
            .msg-row.ia { justify-content: flex-start; }
            .ia-avatar { font-size: 1.5rem; margin-right: 8px; margin-bottom: 5px; animation: floatGhost 4s ease-in-out infinite; }
            .msg { max-width: 80%; padding: 10px 14px; border-radius: 18px; line-height: 1.4; font-size: 1rem; word-wrap: break-word; }
            .msg-user { background: var(--user-msg); color: #fff; border-bottom-right-radius: 2px; }
            .msg-ia { background: var(--chat-bg); color: #ddd; border-bottom-left-radius: 2px; border: 1px solid #333; }
            
            #easter-egg-layer { position: fixed; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 9999; display: flex; align-items: center; justify-content: center; visibility: hidden; }
            #big-ghost { font-size: 15rem; opacity: 0; transform: scale(0.5); transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); }
            .boo #easter-egg-layer { visibility: visible; }
            .boo #big-ghost { opacity: 1; transform: scale(1.2); }

            #chat-input-box { padding: 10px; background: #181818; border-top: 1px solid #333; display: flex; gap: 10px; flex-shrink: 0; padding-bottom: max(10px, env(safe-area-inset-bottom)); }
            #chat-input { flex: 1; background: #2a2a2a; color: #fff; border: none; padding: 12px; border-radius: 25px; font-size: 16px; outline: none; }
            #chat-send { background: var(--ia-msg); color: white; border: none; padding: 0 20px; border-radius: 25px; font-weight: bold; cursor: pointer; }

            #cli-help { background: #111; border-top: 1px solid #333; max-height: 0; overflow: hidden; transition: max-height 0.3s; flex-shrink: 0; }
            #cli-help.open { max-height: 200px; overflow-y: auto; padding: 10px; }
            #help-toggle { text-align: center; font-size: 0.8rem; color: #666; padding: 5px; cursor: pointer; flex-shrink: 0; }

            .typing-indicator { display: inline-flex; align-items: center; padding: 12px 16px; background: var(--chat-bg); border-radius: 18px; border-bottom-left-radius: 2px; }
            .dot { width: 6px; height: 6px; margin: 0 2px; background: #888; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out both; }
            .dot:nth-child(1) { animation-delay: -0.32s; } .dot:nth-child(2) { animation-delay: -0.16s; }
            @keyframes bounce { 0%, 80%, 100% { transform: scale(0); } 40% { transform: scale(1); } }
        </style>
    </head>
    <body>
        <div id="easter-egg-layer"><div id="big-ghost">👻</div></div>

        <div id="header-strip">
            <div id="brand" onclick="triggerEasterEgg()">
                <div id="brand-logo">👻</div>
                <div id="brand-name">pHantasma</div>
            </div>
            <div id="topbar"></div>
        </div>
        
        <div id="main">
            <div id="chat-log"></div>
            <div id="help-toggle" onclick="toggleHelp()">Ver Comandos</div>
            <div id="cli-help"><pre id="help-content" style="color:#888; font-size:0.8em; margin:0;">A carregar...</pre></div>
            
            <div id="chat-input-box">
                <input type="text" id="chat-input" placeholder="Mensagem..." autocomplete="off">
                <button id="chat-send">Enviar</button>
            </div>
        </div>

        <script>
            const chatLog = document.getElementById('chat-log');
            const chatInput = document.getElementById('chat-input');
            const chatSend = document.getElementById('chat-send');
            const topBar = document.getElementById('topbar');
            const helpContent = document.getElementById('help-content');

            function triggerEasterEgg() {
                document.body.classList.add('boo');
                setTimeout(() => { document.body.classList.remove('boo'); }, 1200);
            }

            function getDeviceIcon(name) {
                const n = name.toLowerCase();
                if (n.includes('luz') || n.includes('lâmpada') || n.includes('candeeiro')) return '💡';
                if (n.includes('exaustor') || n.includes('ventoinha')) return '💨';
                if (n.includes('desumidificador') || n.includes('humidade')) return '💧';
                if (n.includes('tv') || n.includes('televisão')) return '📺';
                if (n.includes('robot') || n.includes('aspirador')) return '🤖';
                if (n.includes('tomada')) return '🔌';
                return '⚡';
            }

            function showTypingIndicator() {
                if (document.getElementById('typing-indicator')) return;
                const row = document.createElement('div'); row.id = 'typing-indicator-row'; row.className = 'typing-container'; row.style.cssText = "display:flex;align-items:flex-end;margin-bottom:10px;";
                const avatar = document.createElement('div'); avatar.className = 'ia-avatar'; avatar.innerText = '👻';
                const bubble = document.createElement('div'); bubble.className = 'typing-indicator'; bubble.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
                row.append(avatar, bubble); chatLog.appendChild(row); chatLog.scrollTop = chatLog.scrollHeight;
            }
            function removeTypingIndicator() { const row = document.getElementById('typing-indicator-row'); if (row) row.remove(); }

            function typeText(element, text, speed = 10) {
                let i = 0;
                function type() { if (i < text.length) { element.textContent += text.charAt(i); i++; chatLog.scrollTop = chatLog.scrollHeight; setTimeout(type, speed); } } type();
            }

            function addToChatLog(text, sender = 'ia') {
                removeTypingIndicator();
                const row = document.createElement('div'); row.className = `msg-row ${sender}`;
                if (sender === 'ia') { const avatar = document.createElement('div'); avatar.className = 'ia-avatar'; avatar.innerText = '👻'; row.appendChild(avatar); }
                const msgDiv = document.createElement('div'); msgDiv.className = `msg msg-${sender}`;
                row.appendChild(msgDiv); chatLog.appendChild(row);
                if (sender === 'ia') typeText(msgDiv, text); else msgDiv.textContent = text;
                chatLog.scrollTop = chatLog.scrollHeight;
            }

            async function sendChatCommand() {
                const prompt = chatInput.value.trim(); if (!prompt) return;
                addToChatLog(prompt, 'user'); chatInput.value = ''; chatInput.blur();
                showTypingIndicator();
                try {
                    const res = await fetch('/comando', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt}) });
                    const data = await res.json();
                    if (data.response) addToChatLog(data.response, 'ia'); else removeTypingIndicator();
                } catch (e) { removeTypingIndicator(); addToChatLog('Erro: ' + e, 'ia'); }
            }

            async function handleDeviceAction(device, action) {
                showTypingIndicator();
                try {
                    const res = await fetch('/device_action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({device, action}) });
                    const data = await res.json();
                    if (data.response) addToChatLog(data.response, 'ia'); else removeTypingIndicator();
                } catch (e) { removeTypingIndicator(); }
            }

            function createToggle(device) {
                const toggleDiv = document.createElement('div'); toggleDiv.className = 'device-toggle'; toggleDiv.title = device;
                const icon = document.createElement('span'); icon.className = 'device-icon'; icon.innerText = getDeviceIcon(device);
                const switchLabel = document.createElement('label'); switchLabel.className = 'switch';
                const input = document.createElement('input'); input.type = 'checkbox'; input.disabled = true;
                input.onchange = () => {
                    handleDeviceAction(device, input.checked ? 'ligar' : 'desligar');
                    if(input.checked) toggleDiv.classList.add('active'); else toggleDiv.classList.remove('active');
                };
                const slider = document.createElement('div'); slider.className = 'slider'; switchLabel.append(input, slider);
                const label = document.createElement('span'); label.className = 'device-label'; label.innerText = device.split(' ').pop().substring(0,9);
                toggleDiv.append(icon, switchLabel, label); topBar.appendChild(toggleDiv);
                fetchDeviceStatus(device, input, toggleDiv);
            }

            async function fetchDeviceStatus(device, input, div) {
                try {
                    const res = await fetch(`/device_status?nickname=${encodeURIComponent(device)}`);
                    const data = await res.json();
                    
                    if (data.state === 'on') { input.checked = true; div.classList.add('active'); }
                    else { input.checked = false; div.classList.remove('active'); }
                    
                    const label = div.querySelector('.device-label');
                    
                    // --- EXIBIÇÃO DE WATTS ---
                    if (data.power_w !== undefined) {
                        // Mostra Watts (ex: 194W) a cor-de-laranja
                        label.innerText = `${Math.round(data.power_w)}W`;
                        label.style.color = "#ffb74d"; // Laranja
                        label.style.fontWeight = "bold";
                        label.title = `Consumo: ${data.power_w}W`;
                    } else {
                        // Reset se não houver watts (para não ficar valor antigo)
                        // Verifica se é o texto original ou não
                        if (label.innerText.endsWith('W')) {
                            label.innerText = device.split(' ').pop().substring(0,9);
                            label.style.color = "#aaa";
                            label.style.fontWeight = "normal";
                        }
                    }

                    input.disabled = false; div.classList.add('loaded');
                    if(data.state === 'unreachable') div.style.opacity = 0.3;
                } catch (e) { div.style.opacity = 0.3; }
            }

            function createSensor(device) {
                const div = document.createElement('div'); div.className = 'device-sensor'; div.title = device;
                const dataSpan = document.createElement('span'); dataSpan.className = 'sensor-data'; dataSpan.innerText = '...';
                const label = document.createElement('span'); label.className = 'sensor-label'; 
                let shortName = device.replace(/sensor/gi, '').replace(/ do | da /gi, ' ').trim().substring(0,10);
                label.innerText = shortName;
                div.append(dataSpan, label); topBar.appendChild(div);
                fetchSensorStatus(device, dataSpan, div);
            }

            async function fetchSensorStatus(device, element, div) {
                try {
                    const res = await fetch(`/device_status?nickname=${encodeURIComponent(device)}`);
                    const data = await res.json();
                    if (data.state === 'unreachable') { div.style.opacity = 0.5; element.innerText = '?'; return; }

                    let text = '';
                    if (data.temperature !== undefined) text += Math.round(data.temperature) + '° ';
                    if (data.humidity !== undefined) text += data.humidity + '%';
                    if (!text) text = 'ON';

                    element.innerText = text;
                } catch (e) { element.innerText = 'Err'; }
            }

            async function loadDevices() {
                try {
                    const res = await fetch('/get_devices'); const data = await res.json();
                    topBar.innerHTML = ''; 
                    if (data.devices?.status) data.devices.status.forEach(createSensor);
                    if (data.devices?.toggles) data.devices.toggles.forEach(createToggle);
                } catch (e) {}
            }
            
            async function loadHelp() {
                try {
                    const res = await fetch('/help'); const data = await res.json();
                    if (data.commands) { let t = ""; for (const c in data.commands) t += `${c}: ${data.commands[c]}\\n`; helpContent.innerText = t; }
                } catch (e) {}
            }
            function toggleHelp() { document.getElementById('cli-help').classList.toggle('open'); }

            chatSend.onclick = sendChatCommand;
            chatInput.onkeypress = (e) => { if (e.key === 'Enter') sendChatCommand(); };

            addToChatLog("Nas sombras, aguardo...", "ia");
            loadDevices(); loadHelp();
        </script>
    </body>
    </html>
    """
    return html_content

@app.route("/get_devices")
def get_devices_list():
    """
    Endpoint da API para o frontend saber que botões desenhar.
    Filtra os 'triggers' das skills para encontrar apenas nomes de dispositivos.
    """
    global SKILLS_LIST
    
    # Lista de 'lixo' a remover dos triggers
    BLACKLIST_TRIGGERS = [
        # skill_tuya
        "liga", "ligar", "acende", "acender", "desliga", "desligar", "apaga", "apagar",
        "como está", "estado", "temperatura", "humidade", "nível", "diagnostico", "dps",
        "sensor", "luz", "lâmpada", "desumidificador", "exaustor", "tomada", "ficha", 
        "quarto", "sala", "wc", "leitura"
        
        # skill_xiaomi
        "candeeiro", "luz da mesinha", "abajur", # Estes são os 'objects', não os 'nicknames'
        "aspirador", "robot", "viomi",
        "aspira", "limpa", "começa", "inicia",
        "para", "pára", "pausa",
        "base", "casa", "volta", "carrega", "recolhe"
    ]
    
    device_toggles = []
    device_status_only = [] # Para sensores, etc.

    DEVICE_SKILL_NAMES = ["skill_cloogy", "skill_tuya", "skill_xiaomi"]

    for skill in SKILLS_LIST:
        skill_name = skill.get("name")
        if skill_name in DEVICE_SKILL_NAMES:
            
            all_triggers = skill.get("triggers", [])
            
            # Filtra a lista de triggers para obter apenas os nicknames
            device_nicknames = [
                trigger for trigger in all_triggers 
                if trigger not in BLACKLIST_TRIGGERS
            ]
            
            for nickname in device_nicknames:
                if "sensor" in nickname.lower():
                    device_status_only.append(nickname)
                else:
                    # Só adiciona a toggle se a skill tiver a função get_status
                    if 'get_status' in skill:
                        device_toggles.append(nickname)
                    else:
                        print(f"AVISO (UI): Dispositivo '{nickname}' ignorado (skill '{skill_name}' não tem 'get_status_for_device')")

    return jsonify({"status": "ok", "devices": {
        "toggles": device_toggles,
        "status": device_status_only
    }})

@app.route("/device_action", methods=['POST'])
def handle_device_action():
    """
    Endpoint da API para os botões (Ligar/Desligar).
    Isto constrói um prompt e envia-o para o router principal.
    """
    try:
        data = request.json
        device = data.get('device')
        action = data.get('action') # "ligar" ou "desligar"

        if not device or not action:
            return jsonify({"status": "erro", "message": "Ação ou Dispositivo em falta"}), 400

        # Construímos um prompt de voz simulado
        prompt = f"{action} o {device}" 

        print(f"\n[Comando WebUI Recebido]: {prompt} (sem voz)")

        # --- MODIFICADO: Passa a flag speak_response=False ---
        response_text = route_and_respond(prompt, speak_response=False)
        # ----------------------------------------------------

        return jsonify({"status": "ok", "action": "comando_processado", "response": response_text})

    except Exception as e:
        print(f"ERRO no endpoint /device_action: {e}")
        return jsonify({"status": "erro", "message": str(e)}), 500

@app.route("/device_status")
def handle_device_status():
    """
    Endpoint da API para o frontend obter o estado de um único dispositivo.
    Ex: /device_status?nickname=luz%20da%20sala
    """
    global SKILLS_LIST
    nickname = request.args.get('nickname')
    if not nickname:
        return jsonify({"state": "unreachable", "error": "Nickname em falta"}), 400

    print(f"API: A obter estado para '{nickname}'...")

    for skill in SKILLS_LIST:
        # Verificamos se o nickname pertence a esta skill E se a skill tem a função
        if nickname in skill.get("triggers", []) and 'get_status' in skill:
            try:
                status = skill['get_status'](nickname)
                print(f"API: Estado de '{nickname}' é {status}")
                return jsonify(status)
            except Exception as e:
                print(f"ERRO: A função get_status da skill '{skill['name']}' falhou: {e}")
                return jsonify({"state": "unreachable", "error": str(e)}), 500

    # Se o loop terminar, não encontrámos uma skill para este nickname
    print(f"API: Nenhum 'get_status' encontrado para '{nickname}'")
    return jsonify({"state": "unreachable", "error": "Dispositivo não encontrado ou não suporta status"}), 404

def start_api_server(host='0.0.0.0', port=5000):
    """ Inicia o servidor Flask (sem os logs normais). """
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    print(f"\n--- Servidor API a escutar em http://{host}:{port} ---")
    app.run(host=host, port=port)

# --- BLOCO 3: Loop Principal (Ouvinte de Hotword) ---
def main_loop():
    """ O loop principal: ESCUTAR com Porcupine + VAD (WebRTC), PROCESSAR, REPETIR """
    porcupine = None
    stream = None
    
    # --- CONFIGURAÇÃO VAD (WebRTC) ---
    # Nível de agressividade do VAD (0 a 3). 
    # 3 é o mais agressivo a filtrar ruído (menos falsos positivos, mas deves falar claro).
    # 2 é um bom equilíbrio.
    vad = webrtcvad.Vad(3) 
    
    try:
        # --- CORREÇÕES DA HOTWORD "PHANTASMA" ---
        HOTWORD_CUSTOM_PATH = '/opt/phantasma/models/olá-fantasma_pt_linux_v3_0_0.ppn' 
        HOTWORD_NAME = "olá fantasma" 
        
        # 1. Encontra o caminho da biblioteca 'pvporcupine' instalada
        porcupine_lib_dir = os.path.dirname(pvporcupine.__file__)
        
        # 2. Constrói o caminho para o ficheiro de modelo 'pt'
        pt_model_path = os.path.join(
            porcupine_lib_dir, 
            'lib/common/porcupine_params_pt.pv' 
        )
        
        if not os.path.exists(pt_model_path):
            print(f"ERRO CRÍTICO: Não foi possível encontrar o modelo 'pt' do Porcupine em {pt_model_path}")
            sys.exit(1)

        print(f"A carregar o modelo de hotword: '{HOTWORD_NAME}'...")
        
        # --- AJUSTE DE SENSIBILIDADE ---
        # Baixámos de 0.65 para 0.4 para reduzir ativações com a TV.
        porcupine = pvporcupine.create(
            access_key=config.ACCESS_KEY,
            keyword_paths=[HOTWORD_CUSTOM_PATH],   
            model_path=pt_model_path,
            sensitivities=[0.4] 
        )
        
        chunk_size = porcupine.frame_length # Normalmente 512

        while True:
            print(f"\n--- A escutar pela hotword '{HOTWORD_NAME}' (VAD Ativo) ---")
            
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
                    # Ignorar overflows silenciosamente para não sujar o log, ou imprimir se crítico
                    pass 
                
                # --- LÓGICA AVANÇADA DE VAD (WebRTC) ---
                
                # O Porcupine pede 512 amostras (32ms @ 16kHz).
                # O WebRTC VAD só aceita 10, 20 ou 30ms. 
                # 30ms @ 16kHz = 480 amostras.
                # Truque: Verificamos se as primeiras 480 amostras são voz.
                
                try:
                    # Converter numpy array (int16) para raw bytes
                    # Pegamos apenas nas primeiras 480 amostras para o VAD
                    vad_chunk = chunk[:480].tobytes()
                    
                    # 16000 é a sample rate
                    is_speech = vad.is_speech(vad_chunk, 16000)
                except Exception:
                    # Se houver erro no buffer (tamanho incorreto), assumimos False
                    is_speech = False

                # Se NÃO for voz humana, ignoramos e poupamos CPU/Falsos Positivos
                if not is_speech:
                    continue 
                
                # ---------------------------------------
                
                # Se passou no VAD, verificamos a Hotword
                chunk_flat = chunk.flatten()
                keyword_index = porcupine.process(chunk_flat)
                
                if keyword_index == 0: 
                    print(f"\n\n**** HOTWORD '{HOTWORD_NAME}' DETETADA! ****\n")
                    stream.stop()
                    stream.close()
                    stream = None
                    
                    # --- RESPOSTA ---
                    greetings = ["Diz coisas!", "Aqui estou!", "Diz lá.", "Ao dispor!", "Sim?"]
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
