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
from flask import Flask, request, jsonify
import pvporcupine
import sounddevice as sd 

# --- NOSSOS MÓDULOS ---
import config
from audio_utils import *
from data_utils import *
from tools import search_with_searxng

# --- Carregamento Dinâmico de Skills ---
SKILLS_LIST = []

# assistant.py (Função load_skills)

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

            SKILLS_LIST.append({
                "name": skill_name,
                "module": module, # <-- CRÍTICO: Objeto do módulo para iniciar o daemon
                "trigger_type": getattr(module, 'TRIGGER_TYPE', 'contains'),
                "triggers": raw_triggers,
                "triggers_lower": triggers_lower,
                "handle": module.handle,
                "get_status": getattr(module, 'get_status_for_device', None)
                })
            print(f"  -> Skill '{skill_name}' carregada.")
        except Exception as e:
            print(f"AVISO: Falha ao carregar {f}: {e}")

# --- Globais ---
whisper_model = None
ollama_client = None
conversation_history = []
volatile_cache = {}

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
    rag = retrieve_from_rag(prompt); web = search_with_searxng(prompt)
    final = f"{web}\n{rag}\nPERGUNTA: {prompt}"
    conversation_history.append({'role': 'user', 'content': final})
    try:
        print(f"A pensar ({config.OLLAMA_MODEL_PRIMARY})...")
        cli = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        resp = cli.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=conversation_history)
        content = resp['message']['content']
        conversation_history.append({'role': 'assistant', 'content': content})
        return content
    except: return "Erro no cérebro."

def route_and_respond(user_prompt, speak_response=True):
    """
    Esta é a função "cérebro" central.
    Tenta executar skills; se falhar, envia para o Ollama.
    Recebe 'speak_response' para decidir se deve falar a resposta (Voz=True, API=False).
    """
    try:
        llm_response = None
        user_prompt_lower = user_prompt.lower()

        # --- NOVO ROUTER DE SKILLS (usando triggers_lower) ---
        for skill in SKILLS_LIST:
            triggered = False
            # Usa a nova lista de triggers em lowercase (populada em load_skills)
            triggers_to_check = skill.get("triggers_lower", [])

            if skill["trigger_type"] == "startswith":
                if any(user_prompt_lower.startswith(trigger) for trigger in triggers_to_check):
                    triggered = True
            elif skill["trigger_type"] == "contains":
                if any(trigger in user_prompt_lower for trigger in triggers_to_check):
                    triggered = True

            if triggered:
                print(f"A ativar skill: {skill['name']}")
                # A chamada ao handle é mantida como no original (lower, full)
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
                        llm_response = "O sistema está um pouco ocupado agora. Tenta perguntar-me isso daqui a bocado."

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
    try:
        text = transcribe_audio(record_audio())
        print(f"User: {text}")
        if text: route_and_respond(text)
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
    if not nick: return jsonify({"state": "unknown"}), 400 # Adicionada verificação de nick
    nick_lower = nick.lower()

    for s in SKILLS_LIST:
        # FIX CRÍTICO: Usa .get() para aceder de forma segura. 
        # Se a chave não existir (e o load_skills estiver na versão antiga), 
        # ou se o valor for None, status_func será Falsy.
        status_func = s.get('get_status')

        if status_func:
            # PROTEÇÃO CRÍTICA CONTRA FUGA DE GÁS (mantida)
            if s["name"] == "skill_shellygas" and "gás" not in nick_lower and "gas" not in nick_lower:
                continue 

            try:
                # Se passou o if, a função existe e é chamada
                res = status_func(nick)
                if res and res.get('state') != 'unreachable': return jsonify(res)
            except: 
                # Falha silenciosamente se a skill crachar
                pass

    return jsonify({"state": "unreachable"})

@app.route("/device_action", methods=['POST'])
def api_action():
    d = request.json
    return jsonify({"status":"ok", "response": route_and_respond(f"{d.get('action')} o {d.get('device')}", False)})

@app.route("/get_devices")
def api_devices():
    toggles = []; status = []
    if hasattr(config, 'TUYA_DEVICES'):
        for n in config.TUYA_DEVICES:
            if any(x in n.lower() for x in ['sensor','temperatura','humidade']): status.append(n)
            else: toggles.append(n)
    if hasattr(config, 'MIIO_DEVICES'):
        for n in config.MIIO_DEVICES: toggles.append(n)
    if hasattr(config, 'CLOOGY_DEVICES'):
        for n in config.CLOOGY_DEVICES:
            if 'casa' in n.lower(): status.append(n)
            else: toggles.append(n)
    if hasattr(config, 'SHELLY_GAS_URL') and config.SHELLY_GAS_URL: status.append("Sensor de Gás")
    return jsonify({"status":"ok", "devices": {"toggles": toggles, "status": status}})

# assistant.py (Substituir a função get_help)

@app.route("/help", methods=['GET'])
def get_help():
    try:
        commands = {}
        # 1. Comando 'diz'
        commands["diz"] = "TTS. Ex: diz olá"
        
        # 2. Iterar sobre as skills e extrair triggers
        for skill in SKILLS_LIST:
            skill_name_short = skill["name"].replace("skill_", "")
            
            # Obtém a lista de triggers
            triggers = skill.get("triggers", [])
            
            # Formata os triggers para a listagem (limita a 4 para não sobrecarregar)
            if triggers:
                # Limita a 4 triggers e junta-os
                trigger_summary = ', '.join(triggers[:4])
                
                if len(triggers) > 4:
                    trigger_summary += ', ...'
                
                description = f"Ativado por '{skill.get('trigger_type', 'N/A')}': {trigger_summary}"
            else:
                description = "Comando ativo (sem triggers visíveis)"
                
            commands[skill_name_short] = description
            
        return jsonify({"status": "ok", "commands": commands})
    except Exception as e: 
        # Manter o bloco de exceção para garantir que o serviço não falha
        return jsonify({"status": "erro"}), 500

@app.route("/")
def ui():
    """ Serve a página HTML principal do frontend (Mobile Fix + Sensores + Watts + Agrupamento por Divisão) """

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
                /* FIX CRÍTICO MOBILE: 100dvh respeita a altura do teclado/barras de navegação */
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

            /* --- DEVICE SCROLL & AGRUPAMENTO --- */
            #topbar {
                flex: 1; display: flex; align-items: flex-start; /* Alinhamento superior para cabeçalhos */
                overflow-x: auto; 
                white-space: nowrap; 
                -webkit-overflow-scrolling: touch;
                height: 100%; padding-left: 10px; 
                scrollbar-width: none;
                padding-top: 5px;
            }
            #topbar::-webkit-scrollbar { display: none; }

            .device-room {
                display: inline-flex;
                flex-direction: column;
                margin-right: 20px; 
                padding-right: 15px;
                border-right: 1px solid #333; /* Separador Visual */
                vertical-align: top;
            }
            .room-header {
                font-size: 0.75rem;
                font-weight: bold;
                color: #999;
                padding-bottom: 5px;
                margin-left: 5px;
                text-transform: uppercase;
                user-select: none;
            }
            .room-content {
                display: flex;
                gap: 10px;
                white-space: nowrap; /* Garante que os dispositivos ficam em linha */
            }

            /* --- WIDGETS (SWITCHES/SENSORES) --- */
            .device-toggle, .device-sensor { 
                display: inline-flex; flex-direction: column; align-items: center; justify-content: center;
                opacity: 0.5; transition: all 0.3s; min-width: 60px; height: 52px; box-sizing: border-box;
                padding: 4px; border-radius: 8px; margin-top: 5px;
            }
            .device-toggle { background: #222; }
            .device-sensor { background: #252525; border: 1px solid #333; padding: 5px 8px; }

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

            /* Sensores */
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
            @keyframes floatGhost { 0%, 100% { transform: translateY(0px); } 50% { transform: translateY(-5px); } }
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
            
            // Variável global para guardar a lista de dispositivos (nome, tipo, e elemento DOM)
            const ALL_DEVICES_ELEMENTS = []; 

            const ROOMS_ORDER = ["WC", "Sala", "Quarto", "Entrada", "Geral"];

            function triggerEasterEgg() {
                document.body.classList.add('boo');
                setTimeout(() => { document.body.classList.remove('boo'); }, 1200);
            }

            function getDeviceIcon(name) {
                const n = name.toLowerCase();
                if (n.includes('aspirador')||n.includes('robot')) return '🤖';
                if (n.includes('luz')||n.includes('candeeiro')||n.includes('abajur')||n.includes('lâmpada')) return '💡';
                if (n.includes('exaustor')||n.includes('ventoinha')) return '💨';
                if (n.includes('desumidificador')||n.includes('humidade')) return '💧';
                if (n.includes('gás')||n.includes('incêndio')||n.includes('fumo')) return '🔥';
                if (n.includes('tomada')||n.includes('ficha')||n.includes('forno')) return '⚡';
                return '⚡';
            }

            function getRoomName(name) {
                const n = name.toLowerCase();
                if (n.includes("wc") || n.includes("casa de banho")) return "WC";
                if (n.includes("sala")) return "Sala";
                if (n.includes("quarto")) return "Quarto";
                if (n.includes("entrada") || n.includes("corredor")) return "Entrada";
                return "Geral";
            }

            // MANTIDO: Cria ou obtém o contentor da divisão
            function getOrCreateRoomContainer(room) {
                let roomContainer = document.getElementById(`room-content-${room}`);
                if (roomContainer) return roomContainer;

                const roomWrapper = document.createElement('div');
                roomWrapper.className = 'device-room';

                const header = document.createElement('div');
                header.className = 'room-header';
                header.innerText = room;

                roomContainer = document.createElement('div');
                roomContainer.className = 'room-content';
                roomContainer.id = `room-content-${room}`; 

                roomWrapper.append(header, roomContainer);
                topBar.appendChild(roomWrapper);
                return roomContainer;
            }

            function showTypingIndicator() {
                if (document.getElementById('typing-indicator-row')) return;
                const row = document.createElement('div'); row.id = 'typing-indicator-row'; row.className = 'msg-row ia'; row.style.cssText = "align-items:flex-end;";
                const avatar = document.createElement('div'); avatar.className = 'ia-avatar'; avatar.innerText = '👻';
                const bubble = document.createElement('div'); bubble.className = 'typing-indicator'; bubble.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
                row.append(avatar, bubble); chatLog.appendChild(row); chatLog.scrollTop = chatLog.scrollHeight;
            }
            function removeTypingIndicator() { const row = document.getElementById('typing-indicator-row'); if (row) row.remove(); }

            function typeText(element, text, speed = 10) {
                element.textContent = text; 
            }

            function addToChatLog(text, sender = 'ia') {
                removeTypingIndicator();
                const row = document.createElement('div'); row.className = `msg-row ${sender}`;
                if (sender === 'ia') { const avatar = document.createElement('div'); avatar.className = 'ia-avatar'; avatar.innerText = '👻'; row.appendChild(avatar); }
                const msgDiv = document.createElement('div'); msgDiv.className = `msg msg-${sender}`;
                row.appendChild(msgDiv); chatLog.appendChild(row);

                if (sender === 'ia') { 
                    typeText(msgDiv, text); 
                } else { 
                    msgDiv.textContent = text; 
                }
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
                } catch (e) { removeTypingIndicator(); addToChatLog('Erro: Falha de rede.', 'ia'); }
            }

            async function handleDeviceAction(device, action) {
                showTypingIndicator();
                try {
                    const res = await fetch('/device_action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({device, action}) });
                    const data = await res.json();
                    if (data.response) addToChatLog(data.response, 'ia'); else removeTypineIndicator();
                } catch (e) { removeTypingIndicator(); }
            }

            // CRIAÇÃO: Adiciona o elemento ao DOM e à lista global
            function createToggle(device) {
                const room = getRoomName(device);
                const container = getOrCreateRoomContainer(room);

                const toggleDiv = document.createElement('div'); toggleDiv.className = 'device-toggle'; toggleDiv.title = device;
                const icon = document.createElement('span'); icon.className = 'device-icon'; icon.innerText = getDeviceIcon(device);
                const switchLabel = document.createElement('label'); switchLabel.className = 'switch';
                const input = document.createElement('input'); input.type = 'checkbox'; input.disabled = true;
                
                // Variáveis para estabilizar o estado
                toggleDiv.dataset.state = 'unreachable'; 
                toggleDiv.dataset.type = 'toggle';

                input.onchange = () => {
                    handleDeviceAction(device, input.checked ? 'ligar' : 'desligar');
                    // Atualiza o estado visual imediatamente
                    toggleDiv.dataset.state = input.checked ? 'on' : 'off';
                    if(input.checked) toggleDiv.classList.add('active'); else toggleDiv.classList.remove('active');
                };
                const slider = document.createElement('div'); slider.className = 'slider'; switchLabel.append(input, slider);
                const label = document.createElement('span'); label.className = 'device-label'; label.innerText = device.split(' ').pop().substring(0,9);
                toggleDiv.append(icon, switchLabel, label); container.appendChild(toggleDiv);
                
                // ARMAZENA O ELEMENTO PARA ATUALIZAÇÃO NO LOOP
                ALL_DEVICES_ELEMENTS.push({ name: device, type: 'toggle', element: toggleDiv, input: input, label: label });
            }
            
            // CRIAÇÃO: Adiciona o elemento ao DOM e à lista global
            function createSensor(device) {
                const room = getRoomName(device);
                const container = getOrCreateRoomContainer(room);

                const div = document.createElement('div'); div.className = 'device-sensor'; div.title = device;
                div.dataset.state = 'unreachable';
                div.dataset.type = 'sensor';

                const dataSpan = document.createElement('span'); dataSpan.className = 'sensor-data'; dataSpan.innerText = '...';
                const label = document.createElement('span'); label.className = 'sensor-label'; 
                let shortName = device.replace(/sensor|alarme/gi, '').replace(/ do | da | de /gi, ' ').trim().substring(0,10);
                label.innerText = shortName;
                div.append(dataSpan, label); container.appendChild(div);
                
                // ARMAZENA O ELEMENTO PARA ATUALIZAÇÃO NO LOOP
                ALL_DEVICES_ELEMENTS.push({ name: device, type: 'sensor', element: div, dataSpan: dataSpan, label: label });
            }
            
            // ====================================================================
            // === FUNÇÕES DE ATUALIZAÇÃO (RODAM NO LOOP DE INTERVALO) ===
            // ====================================================================

            async function fetchDeviceStatus(item) {
                const { name, element, input, label } = item;
                try {
                    const res = await fetch(`/device_status?nickname=${encodeURIComponent(name)}`);
                    const data = await res.json();

                    // 1. ATUALIZAÇÃO DE ESTADO ON/OFF (Com cheque de flicker)
                    const newStateIsOn = data.state === 'on';
                    const newPowerW = data.power_w;

                    if (element.dataset.state !== data.state) {
                         // Só atualiza o DOM se o estado lógico mudou
                        input.checked = newStateIsOn;
                        if (newStateIsOn) element.classList.add('active'); else element.classList.remove('active');
                        element.dataset.state = data.state;
                    }
                    
                    // 2. ATUALIZAÇÃO DE OPACIDADE (Com cheque de flicker)
                    const newOpacity = data.state === 'unreachable' ? 0.3 : 1.0;
                    if (parseFloat(element.style.opacity) !== newOpacity) element.style.opacity = newOpacity;
                    
                    input.disabled = false; element.classList.add('loaded');
                    
                    // 3. ATUALIZAÇÃO DE WATTS (Com cheque de flicker no texto)
                    if (newPowerW !== undefined) {
                        const newText = `${Math.round(newPowerW)}W`;
                        if (label.innerText !== newText) {
                            label.innerText = newText;
                            label.style.color = "#ffb74d"; 
                            label.style.fontWeight = "bold";
                            label.title = `Consumo: ${newPowerW}W`;
                        }
                    } else {
                        // Restaura para o texto original se estava em modo Watts e agora não está
                        if (label.style.color === "rgb(255, 183, 77)") { 
                            label.innerText = name.split(' ').pop().substring(0,9);
                            label.style.color = "#aaa";
                            label.style.fontWeight = "normal";
                        }
                    }
                } catch (e) { 
                    if (element.style.opacity !== '0.3') element.style.opacity = 0.3;
                }
            }
            
            async function fetchSensorStatus(item) {
                const { name, element, dataSpan } = item;
                try {
                    const res = await fetch(`/device_status?nickname=${encodeURIComponent(name)}`);
                    const data = await res.json();
                    
                    // 1. ATUALIZAÇÃO DE ESTADO/OPACIDADE (Com cheque de flicker)
                    const newOpacity = data.state === 'unreachable' ? 0.5 : 1.0;
                    if (parseFloat(element.style.opacity) !== newOpacity) element.style.opacity = newOpacity;
                    
                    if (data.state === 'unreachable') { 
                         if (dataSpan.innerText !== '?') dataSpan.innerText = '?';
                        return; 
                    }

                    let text = '';
                    let tempColor = '#4db6ac';

                    // --- FIX CLOOGY: Lógica de Consumo (Watts) para Sensores ---
                    if (data.power_w !== undefined) {
                        text = Math.round(data.power_w) + ' W';
                        tempColor = "#ffb74d"; // Laranja para consumo
                    } 
                    // --- Lógica de Sensores (Temp/Hum/Gás) ---
                    else {
                        if (data.temperature !== undefined) text += Math.round(data.temperature) + '° ';
                        if (data.humidity !== undefined) text += data.humidity + '%';

                        if (data.ppm !== undefined) {
                            text = data.ppm + ' ppm';
                            if (data.status !== 'normal' && data.status !== 'unknown') {
                                 tempColor = '#ff5252'; 
                                 text += ' ⚠️';
                            }
                        }
                    }
                    
                    if (!text) text = 'ON';

                    // 3. ATUALIZAÇÃO CONDICIONAL (Só se o conteúdo mudou)
                    if (dataSpan.innerText !== text) dataSpan.innerText = text;
                    if (dataSpan.style.color !== tempColor) dataSpan.style.color = tempColor;

                } catch (e) { 
                    if (dataSpan.innerText !== 'Err') dataSpan.innerText = 'Err';
                    if (element.style.opacity !== '0.5') element.style.opacity = 0.5;
                }
            }
            
            // ====================================================================
            // === CONTROLADORES DE LOOP ===
            // ====================================================================

            // Roda a cada 5 segundos para atualizar os elementos existentes
            function deviceUpdateLoop() {
                ALL_DEVICES_ELEMENTS.forEach(item => {
                    if (item.type === 'toggle') fetchDeviceStatus(item);
                    else fetchSensorStatus(item);
                });
            }

            // Roda apenas UMA VEZ para construir a estrutura
            async function loadDevicesStructure() {
                try {
                    const res = await fetch('/get_devices'); 
                    const data = await res.json();
                    
                    // Se já existem elementos, a estrutura foi criada. Não faz nada e evita a re-renderização.
                    if (ALL_DEVICES_ELEMENTS.length > 0) return; 
                    
                    // Prepara a lista de dispositivos para criação
                    const allDevices = [];
                    if (data.devices?.status) data.devices.status.forEach(d => allDevices.push({name: d, type: 'sensor'}));
                    if (data.devices?.toggles) data.devices.toggles.forEach(d => allDevices.push({name: d, type: 'toggle'}));

                    // 1. Agrupar Dispositivos
                    const groupedDevices = {};
                    ROOMS_ORDER.forEach(room => groupedDevices[room] = []); 
                    allDevices.forEach(d => groupedDevices[getRoomName(d.name)].push(d));

                    // 2. Criar Divisões e Widgets
                    for (const room of ROOMS_ORDER) {
                        const devicesInRoom = groupedDevices[room];
                        if (devicesInRoom.length > 0) {
                            const container = getOrCreateRoomContainer(room); 
                            devicesInRoom.forEach(d => {
                                // A função de criação também popula ALL_DEVICES_ELEMENTS
                                if (d.type === 'sensor') createSensor(d.name);
                                else createToggle(d.name);
                            });
                        }
                    }
                    
                    // Inicia o primeiro ciclo de atualização logo após a construção
                    deviceUpdateLoop();

                } catch (e) {
                    console.error("Falha ao carregar estrutura de dispositivos:", e);
                }
            }

            async function loadHelp() {
                try {
                    const res = await fetch('/help'); const data = await res.json();
                    if (data.commands) { let t = ""; for (const c in data.commands) t += `${c}: ${data.commands[c]}\\n`; helpContent.innerText = t = t.replace(/\\n/g, '\\n'); }
                } catch (e) {}
            }
            function toggleHelp() { document.getElementById('cli-help').classList.toggle('open'); }

            chatSend.onclick = sendChatCommand;
            chatInput.onkeypress = (e) => { if (e.key === 'Enter') sendChatCommand(); };

            addToChatLog("Nas sombras, aguardo...", "ia");
            
            // 1. CRIA A ESTRUTURA E INICIA O PRIMEIRO UPDATE
            loadDevicesStructure(); 
            loadHelp();
            
            // 2. CORRE O LOOP DE ATUALIZAÇÃO (SÓ DE DADOS)
            setInterval(deviceUpdateLoop, 5000); 
            
        </script>
    </body>
    </html>
    """
    return html_content

def start_api_server(host='0.0.0.0', port=5000):
    logging.getLogger('werkzeug').setLevel(logging.ERROR); app.run(host=host, port=port)

def main():
    """ O loop principal: ESCUTAR com Porcupine, PROCESSAR, REPETIR """

    # pv = porcupine
    pv = None
    # pa = porcupine path
    pa = os.path.dirname(pvporcupine.__file__)
    stream = None

    # Lógica de pesquisa da Hotword
    # Lógica de pesquisa da Hotword
    try:
        models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
        ppn_files = glob.glob(os.path.join(models_dir, '*.ppn'))

        if not ppn_files:
            HOTWORD_CUSTOM_PATH = '/opt/phantasma/models/ei-fantasma_pt_linux_v3_0_0.ppn'
            ppn_path = HOTWORD_CUSTOM_PATH
        else:
            ppn_path = ppn_files[0]

        HOTWORD_NAME = os.path.basename(ppn_path).replace('.ppn', '')

        # Cria a instância do Porcupine
        pv = pvporcupine.create(
                access_key=config.ACCESS_KEY,
                keyword_paths=[ppn_path],   
                model_path=os.path.join(pa, 'lib/common/porcupine_params_pt.pv'),
                sensitivities=[0.4] 
                )

        chunk_size = pv.frame_length

        while True:
            # --- Inicia Stream (dentro do loop para ser reaberto) ---
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
                if overflowed:
                    pass 

                # --- NOVO: Apenas processamento do Porcupine (sem VAD) ---
                chunk_flat = chunk.flatten()
                keyword_index = pv.process(chunk_flat)

                if keyword_index == 0: 
                    print(f"\n\n**** HOTWORD '{HOTWORD_NAME}' DETETADA! ****\n")

                    # --- FIX CRÍTICO: Solta o microfone antes de gravar ---
                    stream.stop()
                    stream.close()
                    stream = None
                    # ----------------------------------------------------

                    # --- RESPOSTA ---
                    greetings = ["Diz coisas!", "Aqui estou!", "Diz lá.", "Ei!", "Sim?"]
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
        if pv is not None:
            pv.delete()
            print("Recursos do Porcupine libertados.")
        sys.exit(0)

if __name__ == "__main__":

    # Define as threads globais (mantido do ficheiro original)
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
    threading.Thread(target=start_api_server, daemon=True).start()

    # --- NOVO: Executa inicialização de daemons genéricos nas skills ---
    print("\n--- A procurar e iniciar daemons de skills em background ---")
    for skill in SKILLS_LIST:
        module = skill["module"] # <-- NOVO: Obtém o objeto do módulo

        # Verifica se o módulo tem a função 'init_skill_daemon'
        if hasattr(module, 'init_skill_daemon'):
            try:
                print(f"-> A iniciar daemon para '{skill['name']}'...")
                # Chama a função de inicialização no objeto do módulo
                module.init_skill_daemon()
            except Exception as e:
                print(f"ERRO CRÍTICO: Falha ao iniciar daemon de '{skill['name']}': {e}")
    # ----------------------------------------------------------------------

    main()
