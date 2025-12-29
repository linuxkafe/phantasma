import re
import httpx
import unicodedata
import config
import json
import os
import time
import threading
import tempfile
from datetime import datetime

# --- Configuração ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["tempo", "clima", "meteorologia", "previsão", "vai chover", "vai estar", "está frio", "está calor", "qualidade do ar", "lua"]

CACHE_FILE = "/opt/phantasma/cache/weather_cache.json"
POLL_INTERVAL = 1800  # 30 minutos

# ID Padrão (Porto) se não houver config
DEFAULT_CITY_ID = getattr(config, 'IPMA_GLOBAL_ID', 1131200)
DEFAULT_CITY_NAME = getattr(config, 'CITY_NAME', "Porto")

# Cache em memória para os IDs das cidades (para não bater na API do IPMA sempre)
_LOCATIONS_CACHE = {}

# --- Helpers ---

def _normalize(text):
    try:
        return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8').lower()
    except:
        return text.lower()

def _get_moon_phase():
    try:
        known_new_moon = datetime(2000, 1, 6)
        lunar_cycle = 29.53058867
        now = datetime.now()
        days_passed = (now - known_new_moon).total_seconds() / 86400
        current_pos = days_passed % lunar_cycle
        
        if current_pos < 1.84: return "Lua Nova"
        if current_pos < 5.53: return "Crescente"
        if current_pos < 9.22: return "Quarto Crescente"
        if current_pos < 12.91: return "Crescente Gibosa"
        if current_pos < 16.61: return "Lua Cheia"
        if current_pos < 20.30: return "Minguante Gibosa"
        if current_pos < 23.99: return "Quarto Minguante"
        if current_pos < 27.68: return "Minguante"
        return "Lua Nova"
    except: return ""

def _get_weather_desc(type_id):
    types = {
        1: "céu limpo", 2: "céu pouco nublado", 3: "céu nublado",
        4: "céu muito nublado", 5: "céu encoberto", 6: "chuva",
        7: "aguaceiros fracos", 8: "aguaceiros", 9: "chuva",
        10: "chuva fraca", 11: "chuva forte", 16: "nevoeiro"
    }
    return types.get(type_id, "céu nublado")

def _get_uv_advice(uv):
    val = int(round(uv))
    if val < 3: return "baixo", "" 
    if val < 6: return "moderado", "usa óculos de sol"
    if val < 8: return "alto", "usa protetor solar"
    if val < 11: return "muito alto", "evita o sol direto"
    return "extremo", "é perigoso sair sem proteção"

def _get_aqi_advice(aqi):
    val = int(aqi)
    if val <= 50: return "boa", "" 
    if val <= 100: return "moderada", "se fores sensível tem cuidado"
    if val <= 150: return "insalubre", "evita exercício na rua"
    return "perigosa", "usa máscara ou fica em casa"

# --- Fetch de Dados e Localizações ---

def _get_city_id(city_name):
    """ Tenta encontrar o ID de uma cidade pelo nome usando a API do IPMA """
    global _LOCATIONS_CACHE
    
    # Se a cache estiver vazia, popula-a
    if not _LOCATIONS_CACHE:
        try:
            url = "https://api.ipma.pt/open-data/distrits-islands.json"
            resp = httpx.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                for entry in data.get('data', []):
                    # Guarda normalizado: "lisboa" -> 1110600
                    norm_name = _normalize(entry['local'])
                    _LOCATIONS_CACHE[norm_name] = entry['globalIdLocal']
        except Exception as e:
            print(f"[Weather] Falha ao obter lista de cidades: {e}")
            return None

    # Tenta encontrar a cidade
    return _LOCATIONS_CACHE.get(_normalize(city_name))

def _fetch_city_data(global_id):
    result = {"timestamp": time.time(), "city_id": global_id}
    client = httpx.Client(timeout=10.0)
    
    try:
        # 1. IPMA
        url = f"https://api.ipma.pt/open-data/forecast/meteorology/cities/daily/{global_id}.json"
        data = client.get(url).json()
        result['forecast'] = data.get('data', [])
        
        if result['forecast']:
            lat = result['forecast'][0].get('latitude')
            lon = result['forecast'][0].get('longitude')
            
            # 2. UV (Open-Meteo)
            try:
                url_uv = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=uv_index"
                uv_data = client.get(url_uv, timeout=3.0).json()
                result['uv'] = uv_data['current']['uv_index']
            except: pass

            # 3. AQI
            try:
                url_oma = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=us_aqi"
                oma_res = client.get(url_oma, timeout=5.0).json()
                result['aqi'] = oma_res['current']['us_aqi']
            except: 
                # Fallback IQAir se configurado
                if hasattr(config, 'IQAIR_KEY') and config.IQAIR_KEY:
                    try:
                        url_iq = f"http://api.airvisual.com/v2/nearest_city?lat={lat}&lon={lon}&key={config.IQAIR_KEY}"
                        iq_data = client.get(url_iq, timeout=5.0).json()
                        result['aqi'] = iq_data['data']['current']['pollution']['aqius']
                    except: pass

        result['moon_phase'] = _get_moon_phase()
        return result

    except Exception as e:
        print(f"[Weather] Erro fetch: {e}")
        return None
    finally:
        client.close()

# --- Daemon ---

def _save_cache(data):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(CACHE_FILE), delete=False) as tf:
            json.dump(data, tf)
            temp_name = tf.name
        os.replace(temp_name, CACHE_FILE)
        os.chmod(CACHE_FILE, 0o666)
    except Exception as e:
        print(f"[Weather] Erro cache: {e}")

def _daemon_loop():
    while True:
        # O Daemon guarda apenas a cidade PADRÃO
        data = _fetch_city_data(DEFAULT_CITY_ID)
        if data: _save_cache(data)
        time.sleep(POLL_INTERVAL)

def init_skill_daemon():
    print("[Weather] Daemon iniciado.")
    threading.Thread(target=_daemon_loop, daemon=True).start()

# --- Handler ---

def handle(user_prompt_lower, user_prompt_full):
    try:
        target_data = None
        target_city_name = DEFAULT_CITY_NAME

        # 1. Tentar detetar localização no prompt (em/no/na)
        # Regex procura por "em x", "no x", "na x", ignorando "hoje" ou "amanhã" como locais
        location_match = re.search(r'\b(em|no|na)\s+(?!(?:hoje|amanhã)\b)([a-zà-ú\s]+)', user_prompt_lower)
        
        if location_match:
            city_searched = location_match.group(2).strip()
            city_id = _get_city_id(city_searched)
            
            if city_id:
                print(f"[Weather] Localização detetada: {city_searched} (ID: {city_id}) - A obter dados em tempo real...")
                # Fetch síncrono para a cidade pedida
                target_data = _fetch_city_data(city_id)
                target_city_name = city_searched.title()
            else:
                print(f"[Weather] Cidade '{city_searched}' não encontrada na lista do IPMA.")

        # 2. Se não houver dados específicos (ou cidade não encontrada), usa a cache padrão
        if not target_data:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r') as f:
                    target_data = json.load(f)
            else:
                return "Ainda estou a recolher dados meteorológicos."
            
        if not target_data or not target_data.get('forecast'):
            return "Não tenho dados de previsão disponíveis."

        is_tomorrow = "amanhã" in user_prompt_lower
        idx = 1 if is_tomorrow else 0
        if len(target_data['forecast']) <= idx: return "Previsão indisponível."
        
        day = target_data['forecast'][idx]
        
        # Valores Inteiros
        t_max = int(round(float(day.get('tMax', 0))))
        t_min = int(round(float(day.get('tMin', 0))))
        precip = int(float(day.get('precipitaProb', 0)))
        w_desc = _get_weather_desc(day.get('idWeatherType'))
        
        current_hour = datetime.now().hour
        is_night = (current_hour >= 19 or current_hour < 7) and not is_tomorrow

        # 3. Construir a Resposta

        # Pergunta CHUVA
        if any(x in user_prompt_lower for x in ["chover", "chuva", "molhar", "água"]):
            prefix = ""
            if target_city_name != DEFAULT_CITY_NAME:
                prefix = f"Em {target_city_name}, "

            if precip >= 50: return f"{prefix}Sim, vai chover ({precip}%)."
            elif precip > 0: return f"{prefix}Talvez, há {precip}% de hipóteses."
            else: return f"{prefix}Não, não vai chover."

        # Resposta GERAL
        day_str = "amanhã" if is_tomorrow else "hoje"
        resp = f"Previsão para {day_str} em {target_city_name}: {w_desc}, máxima de {t_max} e mínima de {t_min} graus."
        
        extras = []
        
        # Qualidade do Ar (Com conselhos)
        if 'aqi' in target_data:
            aqi = int(target_data['aqi'])
            desc, advice = _get_aqi_advice(aqi)
            air_str = f"qualidade do ar {desc} ({aqi})"
            if advice: air_str += f", {advice}"
            extras.append(air_str)
            
        # UV (Com conselhos, só de dia)
        if 'uv' in target_data and not is_night:
            uv = float(target_data['uv'])
            desc, advice = _get_uv_advice(uv)
            uv_str = f"índice UV {int(round(uv))}"
            if advice: uv_str += f" ({advice})"
            extras.append(uv_str)
            
        # Lua (Só à noite e se for a cidade padrão ou se os dados tiverem lua)
        # Nota: O fetch dinâmico também calcula a lua, por isso é seguro.
        if is_night and 'moon_phase' in target_data:
            extras.append(f"fase lunar {target_data['moon_phase']}")
            
        if extras:
            resp += " " + ", ".join(extras).capitalize() + "."
            
        return resp

    except Exception as e:
        print(f"[Weather] Erro handle: {e}")
        return "Erro na meteorologia."
