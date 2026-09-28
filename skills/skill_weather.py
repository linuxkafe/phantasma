# skill_weather.py
import re, httpx, unicodedata, config, json, os, time, threading
from datetime import datetime
from pathlib import Path

TRIGGER_TYPE = "contains"
TRIGGERS = ["tempo", "clima", "meteorologia", "previsão", "vai chover", "vai estar", "frio", "calor", "qualidade do ar"]

# Resolved through config.CACHE_DIR instead of a literal /opt/phantasma path.
# A host path in code cannot be overridden, so these caches had to be forked
# per host. In production the resolved path is byte-identical to the old one.
CACHE_FILE = str(Path(config.CACHE_DIR) / "weather_cache.json")
POLL_INTERVAL = 1800 

DEFAULT_CITY_ID = getattr(config, 'IPMA_GLOBAL_ID', 1131200)
DEFAULT_CITY_NAME = getattr(config, 'CITY_NAME', "Porto")

# --- Mapeamentos e Helpers ---

DIST_TO_AREA = {
    "101": "AVR", "102": "BJA", "103": "BRG", "104": "BGC", "105": "CBO",
    "106": "CBR", "107": "EVR", "108": "FAR", "109": "GDA", "110": "LRA",
    "111": "LSB", "112": "PTG", "113": "PTO", "114": "STM", "115": "STB",
    "116": "VCT", "117": "VRL", "118": "VIS"
}

def _normalize(text):
    try:
        nfkd = unicodedata.normalize('NFKD', text)
        return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()
    except:
        return text.lower().strip()

def _get_ipma_locations():
    try:
        url = "https://api.ipma.pt/open-data/distrits-islands.json"
        resp = httpx.get(url, timeout=5.0)
        return { _normalize(loc['local']): loc['globalIdLocal'] for loc in resp.json()['data'] }
    except:
        return {"porto": 1131200, "lisboa": 1110600, "coimbra": 1060300}

def _get_weather_type_desc(type_id):
    # Mapeamento essencial do IPMA
    types = {1: "céu limpo", 2: "céu pouco nublado", 3: "céu parcialmente nublado", 4: "céu muito nublado", 6: "chuva", 9: "chuva"}
    return types.get(int(type_id), "estado incerto")

def _get_uv_advice(uv):
    if uv is None: return "desconhecido", ""
    val = round(uv)
    if val < 3: return "baixo", ""
    if val < 6: return "moderado", ""
    return "elevado", ""

def _get_iqair_advice(aqi):
    if aqi is None: return "desconhecida", ""
    if aqi <= 50: return "boa", ""
    if aqi <= 100: return "moderada", ""
    return "fraca", ""

def _get_moon_phase():
    phases = ["Nova", "Crescente", "Cheia", "Minguante"]
    day = datetime.now().day
    return phases[day % 4]

def _get_ipma_warnings(city_id):
    """
    Recolhe avisos ativos, aplicando a lógica de prioridade ao 'desliga'.
    Ignora avisos de nível 'green' (situação normal).
    """
    try:
        dist_key = str(city_id)[:3]
        area_code = DIST_TO_AREA.get(dist_key)
        url = "https://api.ipma.pt/open-data/forecast/warnings/warnings_www.json"
        resp = httpx.get(url, timeout=5.0)
        now = datetime.now().isoformat()
        
        # FIX: Filtramos apenas avisos que NÃO sejam 'green'
        active = [
            w['awarenessTypeName'].lower() 
            for w in resp.json() 
            if w.get('idAreaAviso') == area_code 
            and w['startTime'] <= now <= w['endTime']
            and w.get('awarenessLevelID') != 'green'
        ]
        
        return sorted(list(set(active)))[:2]
    except: 
        return []

# --- Core da Skill ---

def handle(user_prompt_lower, user_prompt_full):
    target_city_norm = DEFAULT_CITY_NAME.lower()
    target_id = DEFAULT_CITY_ID
    
    match = re.search(r'\b(no|na|em|para)\s+(?!(?:hoje|amanhã)\b)([A-Za-zÀ-ú\s]+)', user_prompt_lower)
    if match:
        city_extracted = _normalize(match.group(2))
        locations = _get_ipma_locations()
        if city_extracted in locations:
            target_city_norm = city_extracted
            target_id = locations[city_extracted]

    day_index = 1 if "amanhã" in user_prompt_lower else 0
    day_name = "amanhã" if day_index == 1 else "hoje"
    is_night = datetime.now().hour >= 19

    wants_rain = any(x in user_prompt_lower for x in ["chover", "chuva", "molhar", "água"])

    try:
        with httpx.Client(timeout=10.0) as client:
            url_ipma = f"https://api.ipma.pt/open-data/forecast/meteorology/cities/daily/{target_id}.json"
            data_ipma = client.get(url_ipma).json()
            forecast = data_ipma['data'][day_index]
            
            t_min, t_max = round(float(forecast['tMin'])), round(float(forecast['tMax']))
            precip = int(float(forecast.get('precipitaProb', '0')))
            w_desc = _get_weather_type_desc(forecast.get('idWeatherType')).lower()
            
            # Dados de Ar e UV (Soluções abertas/Open-Meteo)
            lat, lon = forecast['latitude'], forecast['longitude']
            url_om = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=uv_index,us_aqi"
            m = client.get(url_om).json().get('current', {})
            uv_desc, _ = _get_uv_advice(m.get('uv_index'))
            aqi_desc, _ = _get_iqair_advice(m.get('us_aqi'))
            
            # Obtenção de avisos com o novo filtro de gravidade
            avisos = _get_ipma_warnings(target_id)
            resp_prefix = f"Atenção, temos aviso de { ' e '.join(avisos) }. " if avisos else ""

            # 1. Caso de Chuva (Resposta Direta)
            if wants_rain:
                if precip >= 50:
                    msg = f"Sim. A chuva está prevista para {target_city_norm.title()} ({precip}%)."
                elif precip >= 20:
                    msg = f"Talvez. Há uma probabilidade de {precip}% em {target_city_norm.title()}."
                else:
                    msg = f"Não. O céu em {target_city_norm.title()} permanecerá seco."
                return f"{resp_prefix}{msg} Espera-se {w_desc}."

            # 2. Resposta Geral Unificada
            if day_index == 0 and is_night:
                main = f"Nesta noite em {target_city_norm.title()}: {w_desc}, {t_min}°."
            else:
                main = f"{day_name.capitalize()} em {target_city_norm.title()}: {w_desc}, entre {t_min}° e {t_max}°."

            # Unificação de Ar e UV na resposta geral
            ar_uv = f" A qualidade do ar está {aqi_desc} e o UV está {uv_desc} ({m.get('uv_index')})."
            
            res = f"{resp_prefix}{main}{ar_uv}"
            if is_night: 
                res += f" A lua está {_get_moon_phase()}."
            
            return res

    except Exception as e:
        print(f"ERRO skill_weather: {e}")
        return "As nuvens estão mudas. Não consegui aceder ao IPMA."

def _refresh_weather_cache() -> bool:
    """Fetch IPMA + Open-Meteo and atomically write CACHE_FILE.

    On failure the previous snapshot (if any) is preserved and marked
    stale, so the UI never shows an empty widget. Returns True on success.
    """
    city_id = DEFAULT_CITY_ID
    try:
        with httpx.Client(timeout=10.0) as client:
            ipma = client.get(
                f"https://api.ipma.pt/open-data/forecast/meteorology/cities/daily/{city_id}.json"
            ).json()
            days = ipma.get('data', [])
            if not days:
                raise ValueError("IPMA returned no daily data")
            today = days[0]
            lat, lon = today.get('latitude'), today.get('longitude')
            aqi = None
            try:
                om = client.get(
                    "https://air-quality-api.open-meteo.com/v1/air-quality",
                    params={"latitude": lat, "longitude": lon, "current": "uv_index,us_aqi"},
                    timeout=10.0
                ).json()
                aqi = om.get('current', {}).get('us_aqi')
            except Exception:
                # AQI is optional; forecast is the hard requirement
                pass
            snap = {
                "city": DEFAULT_CITY_NAME,
                "forecast": days,
                "moon_phase": _get_moon_phase(),
                "aqi": aqi,
                "fetched_at": datetime.now().isoformat(),
                "stale": False,
            }
            tmp = CACHE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(snap, f)
            os.replace(tmp, CACHE_FILE)
            return True
    except Exception:
        # Keep the last good snapshot, mark it stale. Never delete it.
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE) as f:
                    last = json.load(f)
                last["stale"] = True
                tmp = CACHE_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(last, f)
                os.replace(tmp, CACHE_FILE)
        except Exception:
            pass
        return False


def init_skill_daemon():
    """Populate weather_cache.json for the web UI.

    The UI (skill_ui) reads this file but nothing ever wrote it, so the
    weather widget showed '--°'. A single background thread now keeps the
    snapshot current. On failure the last good snapshot is kept and marked
    stale rather than removed, avoiding an empty UI.
    """
    def _loop():
        # Immediate first fetch so the UI has data as soon as the service starts.
        _refresh_weather_cache()
        while True:
            time.sleep(POLL_INTERVAL)
            _refresh_weather_cache()

    threading.Thread(target=_loop, daemon=True, name="weather-cache").start()

