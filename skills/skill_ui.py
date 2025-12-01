# Esta skill não tem triggers de voz, serve apenas para separar a UI do core.
TRIGGER_TYPE = "none"
TRIGGERS = []

def register_routes(app):
    """ Chamado pelo assistant.py para registar a rota web. """
    app.add_url_rule('/', 'ui', handle_request)

def handle_request():
    """ Serve a página HTML principal do frontend. """
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
                if (n.includes('carro')||n.includes('carrinha')) return '🚗';
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

                    // 1. ATUALIZAÇÃO DE ESTADO ON/OFF
                    const newStateIsOn = data.state === 'on';
                    
                    // --- CORREÇÃO: Forçar leitura de power_w se existir ---
                    let newPowerW = data.power_w;
                    
                    // Normaliza: se vier como string ou null, trata
                    if (newPowerW === undefined || newPowerW === null) newPowerW = 0;
                    else newPowerW = parseFloat(newPowerW);

                    if (element.dataset.state !== data.state) {
                        input.checked = newStateIsOn;
                        if (newStateIsOn) element.classList.add('active'); else element.classList.remove('active');
                        element.dataset.state = data.state;
                    }
                    
                    // 2. OPACIDADE
                    const newOpacity = data.state === 'unreachable' ? 0.3 : 1.0;
                    if (parseFloat(element.style.opacity) !== newOpacity) element.style.opacity = newOpacity;
                    
                    input.disabled = false; element.classList.add('loaded');
                    
                    // 3. ATUALIZAÇÃO DE WATTS (LÓGICA MELHORADA)
                    // Se tiver consumo (> 0.5W para filtrar ruído) OU se for explicitamente um device de energia
                    if (newPowerW > 0.5) {
                        const newText = `${Math.round(newPowerW)} W`;
                        
                        // Só atualiza o DOM se o texto mudou (evita flicker)
                        if (label.innerText !== newText) {
                            label.innerText = newText;
                            label.style.color = "#ffb74d"; // Laranja
                            label.style.fontWeight = "bold";
                            label.title = `Consumo: ${newPowerW}W`;
                        }
                    } else {
                        // REVERT: Se o consumo for 0, volta a mostrar o NOME do dispositivo
                        const originalName = name.split(' ').pop().substring(0,9);
                        
                        if (label.innerText !== originalName && label.innerText.includes('W')) {
                            label.innerText = originalName;
                            label.style.color = "#aaa";
                            label.style.fontWeight = "normal";
                            label.title = name;
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
