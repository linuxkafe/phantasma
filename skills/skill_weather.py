# skill_weather.py
import re, httpx, unicodedata, config, json, os, time, threading
from datetime import datetime

TRIGGER_TYPE = "contains"
TRIGGERS = ["tempo", "clima", "meteorologia", "previsão", "vai chover", "vai estar", "frio", "calor"]

CACHE_FILE = "/opt/phantasma/cache/weather_cache.json"
POLL_INTERVAL = 1800 

DEFAULT_CITY_ID = getattr(config, 'IPMA_GLOBAL_ID', 1131200)
DEFAULT_CITY_NAME = getattr(config, 'CITY_NAME', "Porto")

LAST_SEARCHED_CITY = {"id": DEFAULT_CITY_ID, "name": DEFAULT_CITY_NAME}

DIST_TO_AREA = {
    "101": "AVR", "102": "BJA", "103": "BRG", "104": "BGC", "105": "CBO",
    "106": "CBR", "107": "EVR", "108": "FAR", "109": "GDA", "110": "LRA",
    "111": "LSB", "112": "PTG", "113": "PTO", "114": "STM", "115": "STB",
    "116": "VCT", "117": "VRL", "118": "VIS"
}

CITY_MAP = {
    "porto": 1131200, "lisboa": 1110600, "coimbra": 1060300,
    "braga": 1030300, "faro": 1080500, "faro": 1080500,
    "aveiro": 1010500, "viana do castelo": 1160900
}

def _normalize(text):
    try: return "".join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).lower()
    except: return text.lower()

def _get_uv_qualitative(uv):
    """ Escala qualitativa do IPMA """
    if uv <= 2: return "baixo"
    if uv <= 5: return "moderado"
    if uv <= 7: return "elevado"
    if uv <= 10: return "muito elevado"
    return "extremo"

def _get_moon_phase():
    try:
        known_new_moon = datetime(2000, 1, 6)
        lunar_cycle = 29.53058867
        now = datetime.now()
        days_passed = (now - known_new_moon).total_seconds() / 86400
        current_pos = days_passed % lunar_cycle
        phases = [
            (1.84, "Lua Nova"), (5.53, "Crescente"), (9.22, "Quarto Crescente"),
            (12.91, "Crescente Gibosa"), (16.61, "Lua Cheia"), (20.30, "Minguante Gibosa"),
            (23.99, "Quarto Minguante"), (27.68, "Minguante")
        ]
        for limit, name in phases:
            if current_pos < limit: return name
        return "Lua Nova"
    except: return ""

def _get_ipma_warnings(city_id):
    try:
        dist_key = str(city_id)[:3]
        area_code = DIST_TO_AREA.get(dist_key)
        url = "https://api.ipma.pt/open-data/forecast/warnings/warnings_www.json"
        resp = httpx.get(url, timeout=5.0)
        if resp.status_code == 200:
            active = []
            now = datetime.now().isoformat()
            for w in resp.json():
                # Apenas avisos relevantes para a zona e acima de 'green'
                if w.get('idAreaAviso') == area_code and w.get('awarenessLevelID') != 'green':
                    if w['startTime'] <= now <= w['endTime']:
                        lvl = w['awarenessLevelID'].replace('yellow','amarelo').replace('orange','laranja').replace('red','vermelho')
                        active.append(f"{lvl} de {w['awarenessTypeName'].lower()}")
            return sorted(list(set(active)), reverse=True)[:2]
    except: return []
    return []

def _fetch_city_data(global_id):
    try:
        with httpx.Client(timeout=10.0) as client:
            f = client.get(f"https://api.ipma.pt/open-data/forecast/meteorology/cities/daily/{global_id}.json").json()
            forecast = f.get('data', [])
            if forecast:
                lat, lon = forecast[0]['latitude'], forecast[0]['longitude']
                m = client.get(f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=uv_index,us_aqi").json()
                return {
                    "forecast": forecast,
                    "warnings": _get_ipma_warnings(global_id),
                    "uv": m['current']['uv_index'],
                    "aqi": m['current']['us_aqi'],
                    "moon": _get_moon_phase()
                }
    except: return None

def init_skill_daemon():
    def loop():
        while True:
            d = _fetch_city_data(DEFAULT_CITY_ID)
            if d:
                os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
                with open(CACHE_FILE, 'w') as f: json.dump(d, f)
            time.sleep(POLL_INTERVAL)
    threading.Thread(target=loop, daemon=True).start()

def handle(user_prompt_lower, user_prompt_full):
    # 1. Configuração e Detecção de Localidade
    target_city_norm = "porto"
    target_id = 1131200 # Porto por defeito
    
    # Detecção dinâmica de cidades via IPMA
    match = re.search(r'\b(no|na|em|para)\s+(?!(?:hoje|amanhã)\b)([A-Za-zÀ-ú\s]+)', user_prompt_lower)
    if match:
        city_extracted = _normalize(match.group(2))
        locations = _get_ipma_locations()
        if city_extracted in locations:
            target_city_norm = city_extracted
            target_id = locations[city_extracted]

    # 2. Consciência Temporal
    day_index = 0
    day_name = "hoje"
    if "amanhã" in user_prompt_lower:
        day_index = 1
        day_name = "amanhã"

    current_hour = datetime.now().hour
    is_night = current_hour >= 19
    
    # Verificação de intenção específica
    asked_air = any(x in user_prompt_lower for x in ["ar", "qualidade", "uv", "poluição"])
    wants_rain = any(x in user_prompt_lower for x in ["chover", "chuva", "molhar", "água"])

    try:
        client = httpx.Client(timeout=10.0)

        # 3. Recolha de Dados (IPMA + Extras)
        url_ipma = f"https://api.ipma.pt/open-data/forecast/meteorology/cities/daily/{target_id}.json"
        data_ipma = client.get(url_ipma).json()
        forecast = data_ipma['data'][day_index]
        
        t_min = round(float(forecast.get('tMin')))
        t_max = round(float(forecast.get('tMax')))
        precip = int(float(forecast.get('precipitaProb', '0')))
        cond_id = forecast.get('idWeatherType')
        w_desc = _get_weather_type_desc(cond_id).lower()
        
        # Avisos do IPMA e Fase da Lua
        avisos = _get_ipma_warnings(target_id) # Restaurado
        moon = _get_moon_phase() if is_night else "" # Restaurado
        
        # Qualidade do Ar e UV
        uv_val = None
        aqi_val = None
        try:
            lat, lon = forecast.get('latitude'), forecast.get('longitude')
            url_om = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=uv_index"
            uv_val = client.get(url_om).json().get('current', {}).get('uv_index')
            
            if hasattr(config, 'IQAIR_KEY') and config.IQAIR_KEY:
                url_iq = f"http://api.airvisual.com/v2/nearest_city?lat={lat}&lon={lon}&key={config.IQAIR_KEY}"
                aqi_val = client.get(url_iq).json().get('data', {}).get('current', {}).get('pollution', {}).get('aqius')
        except: pass

        # 4. Construção da Resposta
        
        # Prefácio de Avisos e Intros
        resp_prefix = f"Atenção, temos aviso { ' e '.join(avisos) }. " if avisos else ""
        if wants_rain and cond_id in [6, 9, 11]:
            resp_prefix += "Vais apanhar uma molha se não te cuidares. " # Intro clássica

        # CASO A: Resposta Directa para Chuva
        if wants_rain:
            if precip >= 50:
                res = f"Sim. A chuva vai chover em {target_city_norm.title()} com {precip}% de probabilidade."
            elif precip >= 20:
                res = f"Talvez. Há uma probabilidade de {precip}% de chuva em {target_city_norm.title()}."
            else:
                res = f"Não. O céu em {target_city_norm.title()} permanecerá seco."
            return f"{resp_prefix}{res} Espera-se {w_desc}."

        # CASO B: Pergunta Específica de Qualidade do Ar/UV
        if asked_air:
            uv_desc, _ = _get_uv_advice(uv_val)
            aqi_desc, _ = _get_iqair_advice(aqi_val)
            return f"Em {target_city_norm.title()}, a qualidade do ar está {aqi_desc} ({aqi_val}) e o índice UV está {uv_desc} ({uv_val})."

        # CASO C: Resposta Geral
        if day_index == 0 and is_night:
            main_resp = f"Nesta noite em {target_city_norm.title()}, conta com {w_desc} e {t_min} graus."
        else:
            main_resp = f"Para {day_name} em {target_city_norm.title()}, espera-se {w_desc}, entre {t_min} a {t_max} graus."

        # Adição de Extras (Lua, UV, AQI)
        extras = []
        if moon: extras.append(f"estamos em fase de {moon.lower()}")
        if uv_val and uv_val >= 3 and not is_night: 
            u_d, _ = _get_uv_advice(uv_val)
            extras.append(f"o UV está {u_d}")
        
        final_resp = f"{resp_prefix}{main_resp}"
        if extras: final_resp += " Além disso, " + " e ".join(extras) + "."
        
        return final_resp.strip()

    except Exception as e:
        print(f"ERRO skill_weather: {e}")
        return "As nuvens estão mudas. Não consegui aceder ao IPMA."
