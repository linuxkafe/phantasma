import re
import httpx
import unicodedata
import config
import json
import os
import time
import threading
import math
import tempfile
from datetime import datetime, timedelta

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["tempo", "clima", "meteorologia", "previsão", "vai chover", "vai estar", "qualidade do ar", "lua"]

# Cache numa subpasta dedicada
CACHE_FILE = "/opt/phantasma/cache/weather_cache.json"
POLL_INTERVAL = 1800  # Atualiza a cada 30 minutos

# Localização Padrão (Porto/Casa) se não estiver no config
DEFAULT_CITY_ID = getattr(config, 'IPMA_GLOBAL_ID', 1131200) 
DEFAULT_CITY_NAME = getattr(config, 'CITY_NAME', "Porto")

# --- Caches em memória (Auxiliares) ---
_IPMA_LOCATIONS_CACHE = {}
_IPMA_WEATHER_TYPES_CACHE = {}

# --- Helpers de Utilitários ---

def _normalize(text):
    try:
        nfkd = unicodedata.normalize('NFKD', text)
        return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()
    except:
        return text.lower().strip()

def _get_moon_phase():
    """ 
    Calcula a fase da lua com base numa data de referência (época).
    Não requer bibliotecas externas.
    """
    try:
        # Época: Lua Nova conhecida em 6 de Janeiro de 2000
        known_new_moon = datetime(2000, 1, 6)
        lunar_cycle = 29.53058867
        now = datetime.now()
        
        diff = now - known_new_moon
        days_passed = diff.total_seconds() / 86400
        
        # Onde estamos no ciclo (0 a 29.53)
        current_cycle_pos = days_passed % lunar_cycle
        
        # Determinar a fase (simplificado para output de voz)
        age = current_cycle_pos
        
        if age < 1.84: return "Lua Nova"
        if age < 5.53: return "Lua Crescente Côncava"
        if age < 9.22: return "Quarto Crescente"
        if age < 12.91: return "Lua Crescente Gibosa"
        if age < 16.61: return "Lua Cheia"
        if age < 20.30: return "Lua Minguante Gibosa"
        if age < 23.99: return "Quarto Minguante"
        if age < 27.68: return "Lua Minguante Côncava"
        return "Lua Nova"
    except:
        return "Fase lunar desconhecida"

# --- Helpers de API IPMA ---

def _get_ipma_locations():
    global _IPMA_LOCATIONS_CACHE
    if _IPMA_LOCATIONS_CACHE: return _IPMA_LOCATIONS_CACHE
    try:
        url = "https://api.ipma.pt/open-data/distrits-islands.json"
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        for entry in data.get('data', []):
            name = entry.get('local')
            global_id = entry.get('globalIdLocal')
            if name and global_id:
                _IPMA_LOCATIONS_CACHE[_normalize(name)] = global_id
        return _IPMA_LOCATIONS_CACHE
    except Exception as e:
        print(f"[Weather] ERRO IPMA Locations: {e}")
        return {}

def _get_weather_type_desc(type_id):
    global _IPMA_WEATHER_TYPES_CACHE
    if not _IPMA_WEATHER_TYPES_CACHE:
        try:
            url = "https://api.ipma.pt/open-data/weather-type-classe.json"
            resp = httpx.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                for entry in data.get('data', []):
                    _IPMA_WEATHER_TYPES_CACHE[entry['idWeatherType']] = entry['descIdWeatherTypePT']
        except: pass

    fallback = {
        1: "céu limpo", 2: "céu pouco nublado", 3: "céu parcialmente nublado",
        4: "céu muito nublado", 5: "céu encoberto", 6: "chuva",
        7: "aguaceiros fracos", 8: "aguaceiros fortes", 9: "chuva",
        10: "chuva fraca", 11: "chuva forte", 16: "nevoeiro"
    }
    return _IPMA_WEATHER_TYPES_CACHE.get(type_id, fallback.get(type_id, "estado incerto"))

def _fetch_city_data(global_id):
    """
    Função pura de fetch. Retorna dict com dados ou None se erro.
    Agrega IPMA, Open-Meteo (UV) e IQAir (AQI).
    """
    result = {"timestamp": time.time(), "city_id": global_id}
    client = httpx.Client(timeout=10.0)
    
    try:
        # 1. IPMA (Meteorologia)
        url_ipma = f"https://api.ipma.pt/open-data/forecast/meteorology/cities/daily/{global_id}.json"
        resp = client.get(url_ipma)
        resp.raise_for_status()
        data_ipma = resp.json()
        
        result['forecast'] = data_ipma.get('data', [])
        
        # Coordenadas para UV e AQI (usamos as do dia 0)
        if result['forecast']:
            lat = result['forecast'][0].get('latitude')
            lon = result['forecast'][0].get('longitude')
            
            # 2. Open-Meteo (UV)
            try:
                url_uv = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=uv_index"
                r_uv = client.get(url_uv, timeout=3.0)
                if r_uv.status_code == 200:
                    result['uv'] = r_uv.json().get('current', {}).get('uv_index')
            except: pass
            
            # 3. IQAir (AQI) - Requer chave no config
            if hasattr(config, 'IQAIR_KEY') and config.IQAIR_KEY:
                try:
                    url_iq = f"http://api.airvisual.com/v2/nearest_city?lat={lat}&lon={lon}&key={config.IQAIR_KEY}"
                    r_iq = client.get(url_iq, timeout=5.0)
                    if r_iq.status_code == 200:
                        iq_data = r_iq.json().get('data', {})
                        result['aqi'] = iq_data.get('current', {}).get('pollution', {}).get('aqius')
                except: pass

        # 4. Fase Lunar (Calculada localmente)
        result['moon_phase'] = _get_moon_phase()
        
        return result

    except Exception as e:
        print(f"[Weather] Falha no fetch para ID {global_id}: {e}")
        return None
    finally:
        client.close()

# --- Gestão de Cache e Daemon ---

def _save_cache(data):
    dir_name = os.path.dirname(CACHE_FILE)
    
    # Garante que a pasta existe antes de tentar escrever
    try:
        os.makedirs(dir_name, exist_ok=True)
    except Exception as e:
        print(f"[Weather] Erro ao criar pasta de cache: {e}")
        return

    try:
        with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False) as tf:
            json.dump(data, tf)
            tf.flush()
            os.fsync(tf.fileno())
            temp_name = tf.name
        os.replace(temp_name, CACHE_FILE)
        os.chmod(CACHE_FILE, 0o666)
    except Exception as e:
        print(f"[Weather] Erro ao gravar cache: {e}")
        if 'temp_name' in locals() and os.path.exists(temp_name):
            os.remove(temp_name)

def _load_cache():
    if not os.path.exists(CACHE_FILE): return None
    try:
        with open(CACHE_FILE, 'r') as f: return json.load(f)
    except: return None

def _daemon_loop():
    """ Loop principal que atualiza a 'casa' periodicamente """
    print("[Weather Daemon] Iniciado.")
    while True:
        try:
            data = _fetch_city_data(DEFAULT_CITY_ID)
            if data:
                _save_cache(data)
                print(f"[Weather Daemon] Dados atualizados. Lua: {data.get('moon_phase')}")
        except Exception as e:
            print(f"[Weather Daemon] Crash no loop: {e}")
        
        time.sleep(POLL_INTERVAL)

def init_skill_daemon():
    """ Iniciado pelo assistant.py """
    threading.Thread(target=_daemon_loop, daemon=True).start()


# --- Conselhos (Lógica de Resposta) ---

def _get_uv_advice(uv):
    if uv is None: return "", ""
    val = round(uv)
    if val < 3: return "baixo", "Não precisas de proteção solar."
    if val < 6: return "moderado", "Usa óculos de sol."
    if val < 8: return "alto", "Usa protetor solar."
    if val < 11: return "muito alto", "Cuidado com o sol direto!"
    return "extremo", "Perigoso sair sem proteção."

def _get_iqair_advice(aqi_us):
    if aqi_us is None: return "", ""
    if aqi_us <= 50: return "boa", ""
    if aqi_us <= 100: return "moderada", "Cuidado se tiveres alergias."
    if aqi_us <= 150: return "insalubre para sensíveis", "Evita exercício na rua."
    return "má", "O ar lá fora está perigoso."

# --- Função Principal ---

def handle(user_prompt_lower, user_prompt_full):
    """ 
    Skill Meteorologia (Daemon + Cache + Lua).
    Usa cache para a cidade padrão, fetch direto para outras.
    """
    
    # 1. Determinar Localização Alvo
    target_id = DEFAULT_CITY_ID
    target_name = DEFAULT_CITY_NAME
    use_cache = True

    match = re.search(r'\b(no|na|em|para)\s+(?!(?:hoje|amanhã)\b)([A-Za-zÀ-ú\s]+)', user_prompt_lower)
    if match:
        city_input = _normalize(match.group(2))
        locations = _get_ipma_locations()
        
        if city_input in locations:
            target_id = locations[city_input]
            target_name = city_input.title()
            # Se a cidade pedida não for a default, não usamos a cache do daemon
            if target_id != DEFAULT_CITY_ID:
                use_cache = False
    
    # 2. Obter Dados (Cache vs Live)
    data = None
    
    if use_cache:
        # Tenta ler da cache
        cached = _load_cache()
        # Valida se a cache existe e se não é demasiado antiga (ex: 2 horas)
        if cached and (time.time() - cached.get('timestamp', 0) < 7200):
            data = cached
        else:
            # Cache vazia ou velha, força fetch
            data = _fetch_city_data(target_id)
            if data: _save_cache(data)
    else:
        # Fetch on-demand para outra cidade
        data = _fetch_city_data(target_id)

    if not data or 'forecast' not in data:
        return f"Não consegui obter a meteorologia para {target_name}."

    # 3. Determinar Dia (Hoje/Amanhã)
    day_index = 0
    day_label = "hoje"
    if "amanhã" in user_prompt_lower:
        day_index = 1
        day_label = "amanhã"

    if len(data['forecast']) <= day_index:
        return "Ainda não tenho previsão para esse dia."

    forecast = data['forecast'][day_index]

    # 4. Contexto Temporal (Dia/Noite)
    current_hour = datetime.now().hour
    is_night = current_hour >= 19 or current_hour < 7
    
    # 5. Construir Resposta
    t_min = round(float(forecast.get('tMin')))
    t_max = round(float(forecast.get('tMax')))
    precip = int(float(forecast.get('precipitaProb', '0')))
    w_desc = _get_weather_type_desc(forecast.get('idWeatherType')).lower()
    
    uv_val = data.get('uv')
    aqi_val = data.get('aqi')
    moon_phase = data.get('moon_phase', 'desconhecida')

    response = ""

    # Lógica Específica: Pergunta sobre CHUVA
    if any(x in user_prompt_lower for x in ["chover", "chuva", "molhar", "água"]):
        prob_txt = ""
        if precip >= 70: prob_txt = "Sim, é muito provável."
        elif precip >= 30: prob_txt = "Talvez chova."
        elif precip > 0: prob_txt = "Pouco provável."
        else: prob_txt = "Não se prevê chuva."
        
        response = f"{prob_txt} Probabilidade de {precip}% em {target_name}."

    # Lógica Geral
    else:
        prefix = f"Previsão para {day_label}"
        
        # Ajuste Noturno para "Hoje"
        if day_index == 0 and is_night:
            prefix = f"Nesta noite em {target_name}"
            response = f"{prefix}, espera-se {w_desc}. "
            if precip > 20: response += f"Chuva {precip}%. "
            response += f"Mínima de {t_min} graus. "
            
            # --- FEATURE NOVA: Fase Lunar ---
            # Só falamos da lua se for de noite e a previsão for para "hoje"
            if moon_phase:
                response += f"A lua está em fase de {moon_phase}. "
                
        else:
            # Resposta Diurna / Amanhã
            response = f"{prefix} em {target_name}: {w_desc}. Máxima {t_max}, mínima {t_min}. "
            if precip > 0: response += f"Chuva {precip}%. "
            
            # UV só relevante de dia
            if uv_val is not None and (not is_night or day_index == 1):
                _, uv_msg = _get_uv_advice(uv_val)
                response += f"UV {int(uv_val)}. {uv_msg} "

        # AQI é relevante sempre
        if aqi_val is not None:
            aqi_desc, aqi_msg = _get_iqair_advice(aqi_val)
            # REMOVIDO: Filtro que escondia a qualidade do ar se fosse boa
            response += f"Qualidade do ar {aqi_desc} ({aqi_val}). {aqi_msg}"

    return response.strip()
