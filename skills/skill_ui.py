import os
import json
from flask import jsonify

TRIGGER_TYPE = "none"
TRIGGERS = []
WEATHER_CACHE_FILE = "/opt/phantasma/cache/weather_cache.json"

def register_routes(app):
    app.add_url_rule('/', 'ui', handle_request)
    app.add_url_rule('/api/weather', 'weather_api', handle_weather_api)

def handle_weather_api():
    if not os.path.exists(WEATHER_CACHE_FILE): return jsonify({"error": "No cache data"})
    try:
        with open(WEATHER_CACHE_FILE, 'r') as f: return jsonify(json.load(f))
    except Exception as e: return jsonify({"error": str(e)})

def handle_request():
    return """
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
                height: 100vh; height: 100dvh; margin: 0; overflow: hidden;
            }

            /* --- SIDEBAR UNIFICADA (DASHBOARD) --- */
            #header-strip {
                display: flex; align-items: flex-start; 
                background: #181818; 
                border-bottom: 1px solid #2a2a2a; 
                box-shadow: 0 4px 15px rgba(0,0,0,0.3);
                height: 240px; flex-shrink: 0; z-index: 50; 
            }
            #brand {
                display: flex; flex-direction: column; align-items: center; justify-content: center;
                width: 210px; height: 100%;
                border-right: 1px solid #333; background: #151515;
                cursor: pointer; user-select: none; z-index: 10;
                padding: 10px; box-sizing: border-box;
                position: relative; overflow: hidden;
            }
            #brand:active { background: #222; }

            /* ZONA DO CÉU (Tempo + Lua) */
            #sky-stage {
                display: flex; align-items: flex-end; justify-content: center;
                gap: 15px; margin-bottom: 5px; width: 100%;
            }
            .sky-element {
                display: flex; flex-direction: column; align-items: center;
                position: relative;
            }
            #main-weather-icon, #main-moon-icon {
                font-size: 1.8rem; 
                filter: drop-shadow(0 0 5px rgba(0,0,0,0.5));
                transition: all 0.5s ease;
            }
            #main-weather-temp {
                font-size: 0.8rem; font-weight: bold; color: #bbb;
                margin-top: -2px; background: rgba(0,0,0,0.4); padding: 1px 6px; border-radius: 10px;
            }
            
            /* ZONA DO FANTASMA + AR */
            #ghost-stage {
                position: relative;
                display: flex; justify-content: center; align-items: center;
                margin-bottom: 5px;
            }
            #brand-logo { 
                font-size: 3.5rem; 
                transition: all 1s ease; z-index: 10;
            }
            
            /* Indicador de Ar (Flutuante ao lado do fantasma) */
            #aqi-indicator {
                position: absolute;
                right: -15px; bottom: 5px;
                font-size: 1.2rem;
                filter: drop-shadow(0 0 5px rgba(0,0,0,0.8));
                animation: floatWeather 6s infinite ease-in-out;
                opacity: 0.8;
            }

            /* Nome e Power */
            #brand-name { 
                font-size: 0.7rem; font-weight: bold; color: #555; 
                letter-spacing: 2px; text-transform: uppercase; margin-bottom: 5px;
            }
            #power-display {
                font-size: 1.3rem; font-weight: bold; color: #ffb74d;
                text-shadow: 0 0 10px rgba(255, 183, 77, 0.2);
                letter-spacing: 0.5px;
            }

            /* ANIMAÇÕES */
            .ghost-normal { animation: floatGhost 3s ease-in-out infinite; }
            .ghost-rain { filter: drop-shadow(0 0 10px #4db6ac) grayscale(0.6); animation: shakeGhost 5s infinite; }
            .ghost-sun  { filter: drop-shadow(0 0 15px #ffb74d) brightness(1.1); animation: floatGhost 3s infinite; }
            .ghost-storm { filter: drop-shadow(0 0 10px #7e57c2) contrast(1.2); animation: shakeGhost 0.5s infinite; }

            /* --- TOPBAR (DEVICES) --- */
            #topbar {
                flex: 1; display: flex; align-items: flex-start; align-content: flex-start;
                flex-wrap: wrap; overflow-y: auto; overflow-x: hidden;
                height: 100%; padding: 20px 0 20px 20px;
            }
            #topbar::-webkit-scrollbar { width: 4px; }
            #topbar::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }

            .device-room {
                display: inline-flex; flex-direction: column;
                margin-right: 15px; margin-bottom: 15px;
                padding-right: 15px; border-right: 1px solid #333;
                vertical-align: top;
            }
            .room-header { font-size: 0.75rem; font-weight: bold; color: #666; margin-bottom: 8px; text-transform: uppercase; }
            .room-content { display: flex; gap: 8px; flex-wrap: wrap; }

            /* WIDGETS */
            .device-toggle, .device-sensor { 
                display: inline-flex; flex-direction: column; align-items: center; justify-content: center;
                background: #222; opacity: 0.5; transition: all 0.3s; 
                min-width: 68px; height: 56px; border-radius: 8px; padding: 3px 4px;
            }
            .device-sensor { background: #252525; border: 1px solid #333; }
            .device-toggle.loaded { opacity: 1; border: 1px solid #333; }
            .device-toggle.active .device-icon { filter: grayscale(0%); }
            
            .device-icon { font-size: 1.3rem; filter: grayscale(100%); transition: filter 0.3s; margin-bottom: 2px; }
            .device-label { 
                font-size: 0.6rem; color: #aaa; width: 100%; text-align: center;
                line-height: 1.05; white-space: normal; overflow: hidden; 
                display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
            }

            .switch { position: relative; display: inline-block; width: 28px; height: 14px; margin-bottom: 2px; }
            .switch input { opacity: 0; width: 0; height: 0; }
            .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #444; transition: .4s; border-radius: 34px; }
            .slider:before { position: absolute; content: ""; height: 10px; width: 10px; left: 3px; bottom: 2px; background-color: white; transition: .4s; border-radius: 50%; }
            input:checked + .slider { background-color: var(--ia-msg); }
            input:checked + .slider:before { transform: translateX(12px); }

            .sensor-data { font-size: 0.7rem; color: #4db6ac; font-weight: bold; }
            .sensor-label { font-size: 0.6rem; color: #888; width: 100%; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

            /* CHAT */
            #main { flex: 1; display: flex; flex-direction: column; overflow: hidden; position: relative; }
            #chat-log { 
                flex: 1; padding: 15px; overflow-y: auto; display: flex; flex-direction: column; gap: 15px;
                padding-top: 25px; 
            }
            .msg-row { display: flex; width: 100%; align-items: flex-end; }
            .msg-row.user { justify-content: flex-end; }
            .msg-row.ia { justify-content: flex-start; }
            .ia-avatar { font-size: 1.5rem; margin-right: 8px; margin-bottom: 5px; animation: floatGhost 4s ease-in-out infinite; }
            .msg { max-width: 80%; padding: 10px 14px; border-radius: 18px; font-size: 1rem; line-height: 1.4; white-space: pre-wrap; }
            .msg-user { background: var(--user-msg); color: #fff; border-bottom-right-radius: 2px; }
            .msg-ia { background: var(--chat-bg); color: #ddd; border-bottom-left-radius: 2px; border: 1px solid #333; }
            
            .typing-indicator { display: inline-flex; align-items: center; padding: 12px 16px; background: var(--chat-bg); border-radius: 18px; border-bottom-left-radius: 2px; border: 1px solid #333; }
            .dot { width: 6px; height: 6px; margin: 0 2px; background: #888; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out both; }
            .dot:nth-child(1) { animation-delay: -0.32s; } .dot:nth-child(2) { animation-delay: -0.16s; }
            @keyframes bounce { 0%, 80%, 100% { transform: scale(0); } 40% { transform: scale(1); } }

            #chat-input-box { padding: 10px; background: #181818; border-top: 1px solid #333; display: flex; gap: 10px; flex-shrink: 0; padding-bottom: max(10px, env(safe-area-inset-bottom)); align-items: flex-end; }
            #chat-input { flex: 1; background: #2a2a2a; color: #fff; border: none; padding: 12px; border-radius: 20px; font-size: 16px; outline: none; resize: none; height: 24px; max-height: 100px; font-family: inherit; overflow-y: hidden; }
            #chat-send { background: var(--ia-msg); color: white; border: none; padding: 0 20px; border-radius: 25px; font-weight: bold; cursor: pointer; height: 48px; }

            @keyframes floatGhost { 0%, 100% { transform: translateY(0px); } 50% { transform: translateY(-5px); } }
            @keyframes floatWeather { 0%, 100% { transform: translateY(0px) scale(1); } 50% { transform: translateY(-3px) scale(1.05); } }
            @keyframes shakeGhost { 0% { transform: translate(1px, 1px) rotate(0deg); } 10% { transform: translate(-1px, -2px) rotate(-1deg); } 20% { transform: translate(-3px, 0px) rotate(1deg); } 30% { transform: translate(3px, 2px) rotate(0deg); } 40% { transform: translate(1px, -1px) rotate(1deg); } 50% { transform: translate(-1px, 2px) rotate(-1deg); } 60% { transform: translate(-3px, 1px) rotate(0deg); } 70% { transform: translate(3px, 1px) rotate(-1deg); } 80% { transform: translate(-1px, -1px) rotate(1deg); } 90% { transform: translate(1px, 2px) rotate(0deg); } 100% { transform: translate(1px, -2px) rotate(-1deg); } }

            #easter-egg-layer { position: fixed; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 9999; display: flex; align-items: center; justify-content: center; visibility: hidden; }
            #big-ghost { font-size: 15rem; opacity: 0; transform: scale(0.5); transition: all 0.3s; }
            .boo #easter-egg-layer { visibility: visible; }
            .boo #big-ghost { opacity: 1; transform: scale(1.2); }
            #cli-help { background: #111; border-top: 1px solid #333; max-height: 0; overflow: hidden; transition: max-height 0.3s; }
            #cli-help.open { max-height: 200px; overflow-y: auto; padding: 10px; }
            #help-toggle { text-align: center; font-size: 0.8rem; color: #666; padding: 5px; cursor: pointer; }
        </style>
    </head>
    <body>
        <div id="easter-egg-layer"><div id="big-ghost">👻</div></div>

        <div id="header-strip">
            <div id="brand" onclick="triggerEasterEgg()">
                <div id="sky-stage">
                    <div class="sky-element" title="Meteorologia">
                        <div id="main-weather-icon">☁️</div>
                        <div id="main-weather-temp">--°</div>
                    </div>
                    <div class="sky-element" title="Fase Lunar">
                        <div id="main-moon-icon">🌑</div>
                    </div>
                </div>
                <div id="ghost-stage">
                    <div id="brand-logo" class="ghost-normal">👻</div>
                    <div id="aqi-indicator" title="Qualidade do Ar"></div>
                </div>
                <div id="brand-name">pHantasma</div>
                <div id="power-display" title="Consumo Geral">-- W</div>
            </div>
            <div id="topbar"></div>
        </div>

        <div id="main">
            <div id="chat-log"></div>
            <div id="help-toggle" onclick="toggleHelp()">Ver Comandos</div>
            <div id="cli-help"><pre id="help-content" style="color:#888; font-size:0.8em; margin:0;">...</pre></div>
            <div id="chat-input-box">
                <textarea id="chat-input" placeholder="Mensagem..." autocomplete="off"></textarea>
                <button id="chat-send">Enviar</button>
            </div>
        </div>

        <script>
            const chatLog = document.getElementById('chat-log');
            const chatInput = document.getElementById('chat-input');
            const chatSend = document.getElementById('chat-send');
            const topBar = document.getElementById('topbar');
            const helpContent = document.getElementById('help-content');
            
            const ALL_DEVICES_ELEMENTS = []; 
            const ROOMS_ORDER = ["Geral", "WC", "Sala", "Quarto", "Entrada"]; 

            function triggerEasterEgg() {
                document.body.classList.add('boo');
                setTimeout(() => { document.body.classList.remove('boo'); }, 1200);
            }

            // --- UI HELPERS ---
            function getDeviceIcon(name) {
                const n = name.toLowerCase();
                if (n.includes('aspirador')||n.includes('robot')) return '🤖';
                if (n.includes('luz')||n.includes('candeeiro')) return '💡';
                if (n.includes('exaustor')||n.includes('ventoinha')) return '💨';
                if (n.includes('desumidificador')) return '💧';
                if (n.includes('gás')||n.includes('fumo')) return '🔥';
                if (n.includes('carro')||n.includes('carrinha')||n.includes('veículo')) return '🚗';
                if (n.includes('forno')) return '♨️';
                if (n.includes('tomada')||n.includes('ficha')) return '⚡';
                return '⚡';
            }
            
            function getRoomName(name) {
                const n = name.toLowerCase();
                if (n.includes("wc") || n.includes("banho")) return "WC";
                if (n.includes("sala")) return "Sala";
                if (n.includes("quarto")) return "Quarto";
                if (n.includes("entrada") || n.includes("corredor")) return "Entrada";
                return "Geral";
            }
            function getOrCreateRoomContainer(room) {
                let roomContainer = document.getElementById(`room-content-${room}`);
                if (roomContainer) return roomContainer;
                const roomWrapper = document.createElement('div'); roomWrapper.className = 'device-room';
                const header = document.createElement('div'); header.className = 'room-header'; header.innerText = room;
                roomContainer = document.createElement('div'); roomContainer.className = 'room-content'; roomContainer.id = `room-content-${room}`; 
                roomWrapper.append(header, roomContainer); topBar.appendChild(roomWrapper);
                return roomContainer;
            }

            function showTypingIndicator() {
                if (document.getElementById('typing-indicator-row')) return;
                const row = document.createElement('div'); row.id = 'typing-indicator-row'; row.className = 'msg-row ia'; 
                const avatar = document.createElement('div'); avatar.className = 'ia-avatar'; avatar.innerText = '👻';
                const bubble = document.createElement('div'); bubble.className = 'typing-indicator'; 
                bubble.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
                row.append(avatar, bubble); chatLog.appendChild(row); chatLog.scrollTop = chatLog.scrollHeight;
            }
            function removeTypingIndicator() { const row = document.getElementById('typing-indicator-row'); if (row) row.remove(); }

            function addToChatLog(text, sender = 'ia') {
                removeTypingIndicator(); 
                const row = document.createElement('div'); row.className = `msg-row ${sender}`;
                if (sender === 'ia') { const avatar = document.createElement('div'); avatar.className = 'ia-avatar'; avatar.innerText = '👻'; row.appendChild(avatar); }
                const msgDiv = document.createElement('div'); msgDiv.className = `msg msg-${sender}`;
                msgDiv.innerText = text; row.appendChild(msgDiv); chatLog.appendChild(row); chatLog.scrollTop = chatLog.scrollHeight;
            }

            async function sendChatCommand() {
                const prompt = chatInput.value.trim(); if (!prompt) return;
                addToChatLog(prompt, 'user'); chatInput.value = ''; chatInput.style.height = '24px'; 
                showTypingIndicator(); 
                try {
                    const res = await fetch('/comando', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt}) });
                    const data = await res.json(); 
                    if (data.response) addToChatLog(data.response, 'ia'); else removeTypingIndicator();
                } catch (e) { removeTypingIndicator(); addToChatLog('Erro rede.', 'ia'); }
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
                const container = getOrCreateRoomContainer(getRoomName(device));
                const div = document.createElement('div'); div.className = 'device-toggle'; div.title = device;
                div.dataset.state = 'unreachable'; div.dataset.type = 'toggle';
                const icon = document.createElement('span'); icon.className = 'device-icon'; icon.innerText = getDeviceIcon(device);
                const switchLabel = document.createElement('label'); switchLabel.className = 'switch';
                const input = document.createElement('input'); input.type = 'checkbox'; input.disabled = true;
                input.onchange = () => {
                    handleDeviceAction(device, input.checked ? 'ligar' : 'desligar');
                    div.dataset.state = input.checked ? 'on' : 'off';
                    if(input.checked) div.classList.add('active'); else div.classList.remove('active');
                };
                const slider = document.createElement('div'); slider.className = 'slider'; switchLabel.append(input, slider);
                const label = document.createElement('span'); label.className = 'device-label'; 
                label.innerText = device.split(' ').pop().substring(0,12);
                div.append(icon, switchLabel, label); container.appendChild(div);
                ALL_DEVICES_ELEMENTS.push({ name: device, type: 'toggle', element: div, input: input, label: label });
            }
            
            function createSensor(device) {
                if(device.toLowerCase().includes('casa') || device.toLowerCase() === 'geral') return;
                const container = getOrCreateRoomContainer(getRoomName(device));
                const div = document.createElement('div'); div.className = 'device-sensor'; div.title = device;
                div.dataset.state = 'unreachable'; div.dataset.type = 'sensor';
                const dataSpan = document.createElement('span'); dataSpan.className = 'sensor-data'; dataSpan.innerText = '...';
                const label = document.createElement('span'); label.className = 'sensor-label'; 
                label.innerText = device.replace(/sensor|alarme/gi, '').trim().substring(0,12);
                div.append(dataSpan, label); container.appendChild(div);
                ALL_DEVICES_ELEMENTS.push({ name: device, type: 'sensor', element: div, dataSpan: dataSpan, label: label });
            }

            async function fetchDeviceStatus(item) {
                const { name, element, input, label } = item;
                try {
                    const res = await fetch(`/device_status?nickname=${encodeURIComponent(name)}`);
                    const data = await res.json();
                    const isOn = data.state === 'on';
                    if (element.dataset.state !== data.state) {
                        input.checked = isOn;
                        if (isOn) element.classList.add('active'); else element.classList.remove('active');
                        element.dataset.state = data.state;
                    }
                    element.style.opacity = data.state === 'unreachable' ? 0.3 : 1;
                    input.disabled = false; element.classList.add('loaded');
                    if (data.power_w > 0.5) {
                         label.innerText = `${Math.round(data.power_w)} W`; label.style.color = "#ffb74d";
                    } else {
                         label.innerText = name.split(' ').pop().substring(0,12); label.style.color = "#aaa";
                    }
                } catch (e) {}
            }
            
            async function fetchSensorStatus(item) {
                const { name, element, dataSpan } = item;
                try {
                    const res = await fetch(`/device_status?nickname=${encodeURIComponent(name)}`);
                    const data = await res.json();
                    element.style.opacity = data.state === 'unreachable' ? 0.5 : 1;
                    if (data.state === 'unreachable') return;

                    // CORREÇÃO: Lógica para Humidade + Temperatura
                    let textParts = [];
                    let color = '#4db6ac';

                    if (data.power_w !== undefined) { 
                        textParts.push(Math.round(data.power_w) + ' W'); 
                        color = "#ffb74d"; 
                    } else {
                        if (data.temperature !== undefined) textParts.push(Math.round(data.temperature) + '°');
                        if (data.humidity !== undefined) textParts.push(data.humidity + '%');
                        if (data.ppm !== undefined) { 
                            textParts.push(data.ppm + ' ppm'); 
                            if (data.status!=='normal') color='#ff5252'; 
                        }
                    }

                    dataSpan.innerText = textParts.length > 0 ? textParts.join(' ') : 'ON';
                    dataSpan.style.color = color;
                } catch (e) {}
            }

            async function updateHomePower() {
                try {
                    const res = await fetch(`/device_status?nickname=casa`);
                    const data = await res.json();
                    const el = document.getElementById('power-display');
                    if (data.power_w !== undefined) {
                        el.innerText = `${Math.round(data.power_w)} W`; el.style.color = "#ffb74d"; 
                    } else { el.innerText = "-- W"; el.style.color = "#444"; }
                } catch(e) {}
            }

            async function updateWeather() {
                try {
                    const res = await fetch('/api/weather'); const data = await res.json();
                    if (!data.forecast) return;
                    const today = data.forecast[0];
                    let wType = today.idWeatherType;
                    let wIcon = '☁️';
                    let ghostClass = 'ghost-normal';
                    if (wType === 1) { wIcon = '☀️'; ghostClass = 'ghost-sun'; }
                    else if (wType <= 5) { wIcon = '⛅'; ghostClass = 'ghost-normal'; }
                    else if (wType <= 15) { wIcon = '🌧️'; ghostClass = 'ghost-rain'; }
                    else if (wType >= 16) { wIcon = '🌫️'; ghostClass = 'ghost-normal'; }
                    document.getElementById('main-weather-icon').innerText = wIcon;
                    document.getElementById('main-weather-temp').innerText = `${Math.round(today.tMax)}°`;
                    const ghost = document.getElementById('brand-logo');
                    ghost.className = ''; ghost.classList.add(ghostClass);
                    let mIcon = '🌑'; const moon = data.moon_phase || "";
                    if (moon.includes("Crescente")) mIcon = '🌓'; else if (moon.includes("Cheia")) mIcon = '🌕'; else if (moon.includes("Minguante")) mIcon = '🌗';
                    document.getElementById('main-moon-icon').innerText = mIcon;
                    document.querySelector('.sky-element[title="Fase Lunar"]').title = moon || "Fase Lunar";
                    const aqi = data.aqi; const aqiEl = document.getElementById('aqi-indicator');
                    if (aqi !== undefined) {
                        if (aqi <= 50) { aqiEl.innerText = '🍃'; aqiEl.title = `AQI ${aqi} (Bom)`; }
                        else if (aqi <= 100) { aqiEl.innerText = '😷'; aqiEl.title = `AQI ${aqi} (Moderado)`; }
                        else { aqiEl.innerText = '☠️'; aqiEl.title = `AQI ${aqi} (Mau)`; }
                    } else { aqiEl.innerText = ''; }
                } catch(e) {}
            }

            async function loadDevicesStructure() {
                try {
                    const res = await fetch('/get_devices'); const data = await res.json();
                    const allDevices = [];
                    if (data.devices?.status) data.devices.status.forEach(d => allDevices.push({name: d, type: 'sensor'}));
                    if (data.devices?.toggles) data.devices.toggles.forEach(d => allDevices.push({name: d, type: 'toggle'}));
                    const grouped = {};
                    ROOMS_ORDER.forEach(r => grouped[r] = []); 
                    allDevices.forEach(d => grouped[getRoomName(d.name)].push(d));
                    for (const room of ROOMS_ORDER) {
                        const devs = grouped[room];
                        if (devs && devs.length > 0) devs.forEach(d => { if (d.type === 'sensor') createSensor(d.name); else createToggle(d.name); });
                    }
                    updateHomePower(); updateWeather();
                    setInterval(() => {
                        ALL_DEVICES_ELEMENTS.forEach(i => i.type==='toggle'?fetchDeviceStatus(i):fetchSensorStatus(i));
                        updateHomePower(); 
                    }, 5000);
                    setInterval(updateWeather, 600000);
                } catch (e) {}
            }

            async function loadHelp() {
                try {
                    const res = await fetch('/help'); const data = await res.json();
                    if (data.commands) { let t = ""; for (const c in data.commands) t += `${c}: ${data.commands[c]}\\n`; helpContent.innerText = t = t.replace(/\\n/g, '\\n'); }
                } catch (e) {}
            }
            function toggleHelp() { document.getElementById('cli-help').classList.toggle('open'); }
            chatSend.onclick = sendChatCommand; 
            chatInput.onkeydown = (e) => { 
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatCommand(); }
                setTimeout(() => { chatInput.style.height = 'auto'; chatInput.style.height = chatInput.scrollHeight + 'px'; }, 0);
            };
            loadDevicesStructure(); loadHelp(); addToChatLog("Nas sombras, aguardo...", "ia");
        </script>
    </body>
    </html>
    """
