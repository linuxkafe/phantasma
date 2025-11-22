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

# Importação segura do tools
try:
    from tools import search_with_searxng
except ImportError:
    def search_with_searxng(q): return ""

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

            SKILLS_LIST.append({
                "name": skill_name,
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

# --- IA Core ---
def transcribe_audio(audio_data):
    if audio_data.size == 0: return ""
    print(f"A transcrever (Modelo: {config.WHISPER_MODEL})...")
    try:
        # initial_prompt ajuda a manter o contexto em PT
        res = whisper_model.transcribe(
            audio_data, 
            language='pt', 
            fp16=False, 
            initial_prompt=config.WHISPER_INITIAL_PROMPT, 
            no_speech_threshold=0.6
        )
        return res['text'].strip()
    except Exception as e: print(f"Erro transcrição: {e}"); return ""

def process_with_ollama(prompt):
    global conversation_history
    if not prompt: return "Não percebi."
    
    try:
        rag = retrieve_from_rag(prompt)
        web = search_with_searxng(prompt)
        final = f"{web}\n{rag}\nPERGUNTA: {prompt}"
    except: final = prompt

    conversation_history.append({'role': 'user', 'content': final})
    # Mantém histórico curto
    if len(conversation_history) > 10: del conversation_history[1:3]

    try:
        print(f"A pensar ({config.OLLAMA_MODEL_PRIMARY})...")
        cli = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        resp = cli.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=conversation_history)
        content = resp['message']['content']
        conversation_history.append({'role': 'assistant', 'content': content})
        return content
    except: return "Erro no cérebro."

def route_and_respond(user_prompt, speak_response=True):
    try:
        resp = None; prompt_low = user_prompt.lower()
        for s in SKILLS_LIST:
            is_trig = False
            if s["trigger_type"]=="startswith": is_trig=any(prompt_low.startswith(t) for t in s["triggers_lower"])
            else: is_trig=any(t in prompt_low for t in s["triggers_lower"])
            
            if is_trig:
                print(f"Skill '{s['name']}' ativada.")
                # --- PATCH DE COMPATIBILIDADE (1 vs 2 argumentos) ---
                try:
                    resp = s["handle"](s['name'], user_prompt)
                except TypeError:
                    resp = s["handle"](user_prompt)
                # ----------------------------------------------------
                if resp: break
        
        if resp is None:
            if speak_response: play_tts(random.choice(["Deixa ver...", "Um momento..."]))
            resp = process_with_ollama(prompt=user_prompt)
            
        if isinstance(resp, dict) and resp.get("stop_processing"): return resp.get("response", "")
        if speak_response and resp: play_tts(resp)
        return resp
    except Exception as e: return f"Erro crítico: {e}"

def process_user_query():
    try:
        # Usa o record_audio do audio_utils (que já tem VAD)
        audio = record_audio()
        if len(audio) == 0: return
        
        text = transcribe_audio(audio)
        print(f"User: {text}")
        
        # Filtro básico de ruído
        if not text or len(text) < 2 or text in [".", "Obrigado."]: return
        
        route_and_respond(text, speak_response=True)
    except Exception as e: print(f"Erro no ciclo de query: {e}")

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
    if not nick: return jsonify({"state":"unknown"})
    nick_lower = nick.lower()
    
    for s in SKILLS_LIST:
        if s['get_status']:
            if s["name"] == "skill_shellygas" and "gás" not in nick_lower and "gas" not in nick_lower: continue 
            try:
                res = s['get_status'](nick)
                if res and res.get('state') != 'unreachable': return jsonify(res)
            except: pass
            
    return jsonify({"state": "unreachable"})

@app.route("/device_action", methods=['POST'])
def api_action():
    d = request.json
    # Simula comando de voz
    return jsonify({"status":"ok", "response": route_and_respond(f"{d.get('action')} o {d.get('device')}", False)})

@app.route("/get_devices")
def api_devices():
    # --- CORREÇÃO DO CRASH DE DICIONÁRIOS ---
    # Convertemos as chaves dos dicionários em listas antes de somar
    toggles = []
    status = []
    
    # Helper para extrair chaves de forma segura
    def get_keys(conf_attr):
        val = getattr(config, conf_attr, {})
        return list(val.keys()) if isinstance(val, dict) else (val if isinstance(val, list) else [])

    tuya_list = get_keys('TUYA_DEVICES')
    miio_list = get_keys('MIIO_DEVICES')
    cloogy_list = get_keys('CLOOGY_DEVICES')

    # Separação lógica
    for n in tuya_list:
        if any(x in n.lower() for x in ['sensor','temperatura','humidade']): status.append(n)
        else: toggles.append(n)
        
    toggles.extend(miio_list)
    
    for n in cloogy_list:
        if 'casa' in n.lower(): status.append(n)
        else: toggles.append(n)
        
    if getattr(config, 'SHELLY_GAS_URL', None): status.append("Sensor de Gás")
    
    return jsonify({"status":"ok", "devices": {"toggles": toggles, "status": status}})

@app.route("/help", methods=['GET'])
def get_help():
    try:
        commands = {}
        commands["diz"] = "TTS. Ex: diz olá"
        for skill in SKILLS_LIST: commands[skill["name"].replace("skill_", "")] = "Comando ativo"
        return jsonify({"status": "ok", "commands": commands})
    except: return jsonify({"status": "erro"}), 500

@app.route("/")
def ui():
    # UI CORRIGIDA PARA ANDROID: height: 100dvh
    return """<!DOCTYPE html><html lang="pt"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no"><title>Phantasma UI</title><style>
    :root{--bg:#121212;--chat:#1e1e1e;--usr:#2d2d2d;--ia:#005a9e;--txt:#e0e0e0}
    /* FIX ANDROID: 100dvh */
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--txt);display:flex;flex-direction:column;height:100dvh;margin:0;overflow:hidden}
    
    #head{display:flex;align-items:center;background:#181818;border-bottom:1px solid #333;height:85px;flex-shrink:0}
    #brand{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:0 15px;min-width:70px;height:100%;border-right:1px solid #333;background:#151515;cursor:pointer;user-select:none;z-index:10}
    #brand-logo{font-size:1.8rem;animation:float 3s ease-in-out infinite}
    #bar{flex:1;display:flex;align-items:center;overflow-x:auto;white-space:nowrap;height:100%;padding-left:10px;gap:10px}
    
    .dev,.sens{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;background:#222;padding:4px;border-radius:8px;min-width:60px;transition:opacity 0.3s;margin-top:5px;position:relative}
    .sens{background:#252525;border:1px solid #333;height:52px}
    .dev.active .ico{filter:grayscale(0%)}
    .ico{font-size:1.2rem;margin-bottom:2px;filter:grayscale(100%);transition:filter 0.3s}
    .lbl,.slbl{font-size:0.65rem;color:#aaa;max-width:65px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
    .sdat{font-size:0.75rem;color:#4db6ac;font-weight:bold}
    
    .sw{position:relative;display:inline-block;width:36px;height:20px}
    .sw input{opacity:0;width:0;height:0}
    .sl{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background-color:#444;transition:.4s;border-radius:34px}
    .sl:before{position:absolute;content:"";height:14px;width:14px;left:3px;bottom:3px;background-color:white;transition:.4s;border-radius:50%}
    input:checked+.sl{background-color:var(--ia)}
    input:checked+.sl:before{transform:translateX(16px)}
    
    #main{flex:1;display:flex;flex-direction:column;overflow:hidden;position:relative}
    #log{flex:1;padding:15px;overflow-y:auto;display:flex;flex-direction:column;gap:15px;scroll-behavior:smooth}
    .row{display:flex;width:100%;align-items:flex-end}
    .row.usr{justify-content:flex-end}
    .av{font-size:1.5rem;margin-right:8px;margin-bottom:5px;animation:float 4s ease-in-out infinite}
    .msg{max-width:80%;padding:10px 14px;border-radius:18px;line-height:1.4;font-size:1rem;word-wrap:break-word}
    .msg.usr{background:var(--usr);color:#fff;border-bottom-right-radius:2px}
    .msg.ia{background:var(--chat);color:#ddd;border-bottom-left-radius:2px;border:1px solid #333}
    
    .typing-row { display: flex; width: 100%; align-items: flex-end; justify-content: flex-start; }
    .typing{display:inline-flex;align-items:center;padding:12px 16px;background:var(--chat);border-radius:18px;border-bottom-left-radius:2px;border:1px solid #333}
    .dot{width:6px;height:6px;margin:0 2px;background:#888;border-radius:50%;animation:bounce 1.4s infinite ease-in-out both}
    .dot:nth-child(1){animation-delay:-0.32s}.dot:nth-child(2){animation-delay:-0.16s}
    @keyframes bounce{0%,80%,100%{transform:scale(0)}40%{transform:scale(1)}}

    #box{padding:10px;background:#181818;border-top:1px solid #333;display:flex;gap:10px;flex-shrink:0}
    #in{flex:1;background:#2a2a2a;color:#fff;border:none;padding:12px;border-radius:25px;outline:none;font-size:16px}
    #btn{background:var(--ia);color:white;border:none;padding:0 20px;border-radius:25px;font-weight:bold;cursor:pointer}
    
    @keyframes float{0%{transform:translateY(0px)}50%{transform:translateY(-5px)}100%{transform:translateY(0px)}}
    </style></head><body>
    <div id="head"><div id="brand" onclick="location.reload()"><div id="brand-logo">👻</div></div><div id="bar"></div></div>
    <div id="main"><div id="log"></div><div id="box"><input id="in" placeholder="..."><button id="btn">></button></div></div>
    <script>
    const log=document.getElementById('log'),bar=document.getElementById('bar'),devs=new Set();
    
    function showTyping(){if(document.getElementById('typing-row'))return;const r=document.createElement('div');r.id='typing-row';r.className='typing-row';r.innerHTML='<div class="av">👻</div><div class="typing"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>';log.appendChild(r);log.scrollTop=log.scrollHeight}
    function hideTyping(){const t=document.getElementById('typing-row');if(t)t.remove()}
    function typeText(el,text,speed=10){let i=0; function t(){if(i<text.length){el.textContent+=text.charAt(i);i++;log.scrollTop=log.scrollHeight;setTimeout(t,speed)}} t();}

    function add(t,s){
        hideTyping();
        const r=document.createElement('div');r.className=`row ${s}`;
        if(s=='ia')r.innerHTML='<div class="av">👻</div>';
        const m=document.createElement('div');m.className=`msg ${s}`;
        r.appendChild(m);log.appendChild(r);log.scrollTop=log.scrollHeight;
        if(s=='ia') typeText(m,t); else m.innerText=t;
    }
    
    async function cmd(){
        const i=document.getElementById('in'),v=i.value.trim();if(!v)return;
        add(v,'usr');i.value='';showTyping();
        try{
            const r=await fetch('/comando',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:v})});
            const d=await r.json();
            if(d.response) add(d.response,'ia'); else hideTyping();
        }catch{hideTyping();add('Erro','ia')}
    }
    document.getElementById('btn').onclick=cmd;document.getElementById('in').onkeypress=e=>{if(e.key=='Enter')cmd()};
    
    function ico(n){
        n=n.toLowerCase();
        if(n.match(/aspirador|robot/))return'🤖';
        if(n.match(/luz|candeeiro|lâmpada/))return'💡';
        if(n.match(/temp/))return'🌡️';
        if(n.match(/gás|fumo/))return'🔥';
        return'⚡';
    }
    
    function clean(n) { return n.replace(/(sensor|luz|candeeiro|exaustor|desumidificador|alarme|tomada)( de| da| do)?/gi,"").trim().substring(0,12); }

    function w(d,s){
        const e=document.createElement('div');e.id='d-'+d.replace(/[^a-z0-9]/gi,''); e.title=d;
        if(s){e.className='sens';e.innerHTML=`<span class="sdat">...</span><span class="slbl">${clean(d)}</span>`}
        else{e.className='dev';e.innerHTML=`<span class="ico">${ico(d)}</span><label class="sw"><input type="checkbox" disabled><div class="sl"></div></label><span class="lbl">${clean(d)}</span>`;
        e.querySelector('input').onchange=function(){fetch('/device_action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device:d,action:this.checked?'liga':'desliga'})})}}
        bar.appendChild(e);devs.add({n:d,s:s,id:e.id});upd(d,s,e.id)}
    
    async function upd(n,s,id){const el=document.getElementById(id);if(!el)return;try{const r=await fetch(`/device_status?nickname=${encodeURIComponent(n)}`);const d=await r.json();
    if(d.state=='unreachable'){el.style.opacity=0.4;if(s)el.querySelector('.sdat').innerText='?';return}el.style.opacity=1;
    if(s){let t='';const v = el.querySelector('.sdat');
    if(d.power_w!==undefined){t=Math.round(d.power_w)+'W';v.style.color='#ffb74d'}
    else if(d.temperature!==undefined){t=Math.round(d.temperature)+'°';v.style.color='#4db6ac'}
    v.innerText=t||'ON'}
    else{const i=el.querySelector('input');i.disabled=false;i.checked=(d.state=='on');
    if(d.state=='on')el.classList.add('active');else el.classList.remove('active');}}catch{}}
    
    function loop(){devs.forEach(d=>upd(d.n,d.s,d.id))}
    fetch('/get_devices').then(r=>r.json()).then(d=>{bar.innerHTML='';d.devices.status.forEach(x=>w(x,true));d.devices.toggles.forEach(x=>w(x,false));add('Nas sombras, aguardo...','ia')});setInterval(loop,5000);
    </script></body></html>"""

def start_api_server(host='0.0.0.0', port=5000):
    logging.getLogger('werkzeug').setLevel(logging.ERROR); app.run(host=host, port=port)

def main():
    pv=None; pa=os.path.dirname(pvporcupine.__file__)
    # REMOVIDO: vad = webrtcvad.Vad(1) -> Causa "surdez" em início de frases
    
    try:
        # Busca modelo PPN automático
        models_dir = '/opt/phantasma/models/'
        ppn_files = glob.glob(os.path.join(models_dir, '*.ppn'))
        if not ppn_files: 
             # Fallback ou erro
             print("ERRO: Nenhum .ppn encontrado.")
             ppn_path = "" 
        else:
             ppn_path = ppn_files[0]

        pv=pvporcupine.create(
            access_key=config.ACCESS_KEY, 
            keyword_paths=[ppn_path] if ppn_path else [], 
            model_path=f'{pa}/lib/common/porcupine_params_pt.pv', 
            sensitivities=[0.5]
        )
        
        # Inicia Stream
        stream = sd.InputStream(
            device=config.ALSA_DEVICE_IN, channels=1, 
            samplerate=pv.sample_rate, dtype='int16', 
            blocksize=pv.frame_length
        )
        stream.start()
        print(f"--- A OUVIR ({config.ALSA_DEVICE_IN}) ---")

        while True:
            c, overflow = stream.read(pv.frame_length)
            if overflow: pass
            
            # --- HOTWORD LOGIC OTIMIZADA ---
            # Removemos o bloqueio do VAD aqui. O Porcupine processa tudo.
            if pv.process(c.flatten()) == 0:
                print("\n!!! HOTWORD !!!")
                
                # FIX CRÍTICO: Solta o microfone antes de gravar
                stream.stop()
                stream.close()
                stream = None
                
                play_tts(random.choice(["Sim?","Diz."]))
                
                # Grava e Processa
                process_user_query()
                
                # Reabre o microfone
                stream = sd.InputStream(
                    device=config.ALSA_DEVICE_IN, channels=1, 
                    samplerate=pv.sample_rate, dtype='int16', 
                    blocksize=pv.frame_length
                )
                stream.start()
                print("--- A OUVIR ---")

    except KeyboardInterrupt: pass
    except Exception as e: print(f"Erro Loop: {e}"); traceback.print_exc()
    finally: 
        if stream: stream.close()
        if pv: pv.delete()

if __name__=="__main__":
    setup_database(); load_skills(); 
    try: 
        whisper_model=whisper.load_model(config.WHISPER_MODEL,device="cpu")
        ollama_client=ollama.Client()
    except: pass
    conversation_history=[{'role':'system','content':config.SYSTEM_PROMPT}]
    threading.Thread(target=start_api_server, daemon=True).start()
    main()
