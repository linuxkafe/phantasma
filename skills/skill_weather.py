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

def handle(p_low, prompt):
    global LAST_SEARCHED_CITY
    norm_prompt = _normalize(p_low)
    
    for name, cid in CITY_MAP.items():
        if name in norm_prompt:
            LAST_SEARCHED_CITY = {"id": cid, "name": name.capitalize()}
            break
    
    target_id, target_name = LAST_SEARCHED_CITY["id"], LAST_SEARCHED_CITY["name"]

    if target_id == DEFAULT_CITY_ID and os.path.exists(CACHE_FILE) and "amanha" not in norm_prompt:
        with open(CACHE_FILE, 'r') as f: data = json.load(f)
    else:
        data = _fetch_city_data(target_id)

    if not data or not data.get('forecast'): return "Não consegui aceder ao IPMA agora."

    idx = 1 if "amanha" in norm_prompt else 0
    day = data['forecast'][idx]
    is_daylight = (7 <= datetime.now().hour <= 19)
    asked_air = re.search(r'\b(ar|qualidade|uv)\b', norm_prompt)
    
    avisos_list = data.get('warnings', [])
    avisos_str = f"Atenção, temos aviso {' e '.join(avisos_list)}. " if avisos_list else ""

    if asked_air and idx == 0:
        aqi, uv = data.get('aqi', 0), int(data.get('uv', 0))
        qualidade_ar = "boa" if aqi < 50 else "moderada"
        qualidade_uv = _get_uv_qualitative(uv)
        return f"{avisos_str}Em {target_name}, a qualidade do ar está {qualidade_ar} e o índice UV está {qualidade_uv}, com valor {uv}."

    t_max, t_min = int(float(day['tMax'])), int(float(day['tMin']))
    condicao = day['idWeatherType']
    intro = "Vais apanhar uma molha se não te cuidares." if condicao in [6, 9, 11] else "Olha, o cenário é este:"
    quando = "amanhã" if idx == 1 else "hoje"
    
    resp = f"{avisos_str}{intro} Para {quando} em {target_name}, conta com {t_min} a {t_max} graus."

    extras = []
    if idx == 0:
        if data.get('aqi', 0) < 50: extras.append("a qualidade do ar está boa")
        if is_daylight: extras.append(f"o UV está {_get_uv_qualitative(data['uv'])}")
        if not is_daylight and data.get('moon'): extras.append(f"estamos em fase de {data['moon'].lower()}")
    
    if extras: resp += " Além disso, " + " e ".join(extras) + "."

    return resp
