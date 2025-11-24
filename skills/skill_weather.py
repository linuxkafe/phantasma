import re
import httpx
import unicodedata
import config
from datetime import datetime

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["tempo", "clima", "meteorologia", "previsão", "vai chover", "vai estar", "qualidade do ar"]

# --- Caches em memória ---
_IPMA_LOCATIONS_CACHE = {}
_IPMA_WEATHER_TYPES_CACHE = {}

def _normalize(text):
    try:
        nfkd = unicodedata.normalize('NFKD', text)
        return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()
    except:
        return text.lower().strip()

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
        print(f"ERRO IPMA (Locations): {e}")
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

# --- CONSELHOS ---

def _get_uv_advice(uv):
    if uv is None: return "", ""
    val = round(uv)
    if val < 3: return "baixo", "Não precisas de proteção solar."
    if val < 6: return "moderado", "Se fores sensível, usa óculos de sol."
    if val < 8: return "alto", "Usa protetor solar e chapéu."
    if val < 11: return "muito alto", "Cuidado, evita o sol direto!"
    return "extremo", "É perigoso sair sem proteção máxima."

def _get_iqair_advice(aqi_us):
    if aqi_us is None: return "", ""
    if aqi_us <= 50: return "boa", "Aproveita para arejar a casa."
    if aqi_us <= 100: return "moderada", "Se tiveres alergias, tem algum cuidado."
    if aqi_us <= 150: return "insalubre para sensíveis", "Evita exercício ao ar livre."
    if aqi_us <= 200: return "insalubre", "Usa máscara ou fica em casa."
    if aqi_us <= 300: return "muito insalubre", "Fecha as janelas, o ar está perigoso."
    return "perigosa", "Alerta máximo, evita respirar o ar exterior!"

def handle(user_prompt_lower, user_prompt_full):
    """ Skill Meteorologia com Consciência Temporal. """
    
    target_city_norm = "porto"
    target_id = 1131200 
    
    # Detetar cidade
    match = re.search(r'\b(no|na|em|para)\s+(?!(?:hoje|amanhã)\b)([A-Za-zÀ-ú\s]+)', user_prompt_lower)
    if match:
        city_extracted = _normalize(match.group(2))
        locations = _get_ipma_locations()
        if city_extracted in locations:
            target_city_norm = city_extracted
            target_id = locations[city_extracted]

    # Determinar dia
    day_index = 0
    day_name = "hoje"
    if "amanhã" in user_prompt_lower:
        day_index = 1
        day_name = "amanhã"

    # --- LÓGICA DE TEMPO REAL (HORAS) ---
    current_hour = datetime.now().hour
    is_night = current_hour >= 19
    
    # Se for noite e perguntarem por "hoje", mudamos a lógica
    if day_index == 0 and is_night:
        # Se for muito tarde, o "hoje" já não interessa tanto como o "amanhã"
        pass

    try:
        client = httpx.Client(timeout=10.0)

        # 1. IPMA (Previsão Diária)
        url_ipma = f"https://api.ipma.pt/open-data/forecast/meteorology/cities/daily/{target_id}.json"
        resp_ipma = client.get(url_ipma)
        resp_ipma.raise_for_status()
        data_ipma = resp_ipma.json()
        
        forecast = data_ipma['data'][day_index]
        
        t_min = round(float(forecast.get('tMin')))
        t_max = round(float(forecast.get('tMax')))
        precip = int(float(forecast.get('precipitaProb', '0')))
        w_desc = _get_weather_type_desc(forecast.get('idWeatherType')).lower()
        lat_ipma = forecast.get('latitude')
        lon_ipma = forecast.get('longitude')

        # 2. Dados Extra (UV / AQI)
        uv_val = None
        aqi_val = None
        
        try:
            url_om = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat_ipma}&longitude={lon_ipma}&current=uv_index"
            resp_om = client.get(url_om, timeout=2.0)
            if resp_om.status_code == 200:
                uv_val = resp_om.json().get('current', {}).get('uv_index')
        except: pass

        if hasattr(config, 'IQAIR_KEY') and config.IQAIR_KEY:
            try:
                if hasattr(config, 'HOME_COORDS') and config.HOME_COORDS:
                    lat_iq, lon_iq = config.HOME_COORDS
                else:
                    lat_iq, lon_iq = lat_ipma, lon_ipma
                url_iq = f"http://api.airvisual.com/v2/nearest_city?lat={lat_iq}&lon={lon_iq}&key={config.IQAIR_KEY}"
                resp_iq = client.get(url_iq, timeout=4.0)
                if resp_iq.status_code == 200:
                    iq_data = resp_iq.json().get('data', {})
                    aqi_val = iq_data.get('current', {}).get('pollution', {}).get('aqius')
            except: pass

        # 3. Conselhos e Descrições
        uv_desc, uv_msg = _get_uv_advice(uv_val)
        aqi_desc, aqi_msg = _get_iqair_advice(aqi_val)

        # --- CONSTRUÇÃO DA RESPOSTA INTELIGENTE ---

        # Caso A: Pergunta sobre CHUVA
        wants_rain = any(x in user_prompt_lower for x in ["chover", "chuva", "molhar", "água"])
        
        if wants_rain:
            # Se for noite (>19h) e perguntarem "hoje", ajustamos a resposta
            if day_index == 0 and is_night:
                if precip > 50:
                    return f"A previsão geral para hoje indicava chuva ({precip}%), mas como já é noite, o melhor é espreitares lá fora. Amanhã a probabilidade é de {data_ipma['data'][1].get('precipitaProb')}%."
                else:
                    return f"Hoje a probabilidade de chuva era baixa ({precip}%). Para o resto da noite deve manter-se assim."

            # Resposta normal (Dia ou Amanhã)
            if precip >= 70: txt = f"Sim, é muito provável ({precip}%). "
            elif precip >= 30: txt = f"Talvez, há {precip}% de hipóteses. "
            elif precip > 0: txt = f"Pouco provável, apenas {precip}%. "
            else: txt = "Não, não se prevê chuva. "
            return txt + f"Em {target_city_norm.title()} espera-se {w_desc}."

        # Caso B: Resposta GERAL
        
        # Ajuste noturno para a resposta geral
        prefix = f"Previsão para {day_name}"
        if day_index == 0 and is_night:
            prefix = f"Nesta noite em {target_city_norm.title()}"
            # À noite não faz sentido falar de UV nem de Máxima do dia
            response = f"{prefix}, o céu está {w_desc}. A mínima prevista foi de {t_min}°."
        else:
            response = (
                f"{prefix} em {target_city_norm.title()}: "
                f"{w_desc}, máxima {t_max}° e mínima {t_min}°."
            )

        if precip > 0:
            response += f" Probabilidade de chuva: {precip}%."

        # Só mostra UV se for de dia (ou previsão de amanhã)
        if uv_val is not None and (day_index == 1 or not is_night):
            uv_int = round(uv_val)
            response += f" Índice UV {uv_int} ({uv_desc}). {uv_msg}"
            
        # AQI é relevante dia e noite
        if aqi_val is not None:
            response += f" Qualidade do ar {aqi_desc} ({aqi_val}). {aqi_msg}"

        return response.replace("  ", " ").strip()

    except Exception as e:
        print(f"ERRO skill_weather: {e}")
        return "Não consegui aceder à meteorologia."
