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
    if not uv: return "desconhecido", ""
    val = int(round(uv))
    if val < 3: return "baixo", "" 
    if val < 6: return "moderado", "usa óculos de sol"
    if val < 8: return "alto", "usa protetor solar"
    if val < 11: return "muito alto", "evita o sol direto"
    return "extremo", "é perigoso sair sem proteção"

def _get_aqi_advice(aqi):
    if not aqi: return "desconhecido", ""
    val = int(aqi)
    if val <= 50: return "boa", "" 
    if val <= 100: return "moderada", "se fores sensível tem cuidado"
    if val <= 150: return "insalubre", "evita exercício na rua"
    return "perigosa", "usa máscara ou fica em casa"

# --- Fetch de Dados e Localizações ---

def _get_city_id(city_name):
    """ Tenta encontrar o ID de uma cidade pelo nome usando a API do IPMA """
    global _LOCATIONS_CACHE
    
    if not _LOCATIONS_CACHE:
        try:
            url = "https://api.ipma.pt/open-data/distrits-islands.json"
            resp = httpx.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                for entry in data.get('data', []):
                    norm_name = _normalize(entry['local'])
                    _LOCATIONS_CACHE[norm_name] = entry['globalIdLocal']
        except Exception as e:
            print(f"[Weather] Falha ao obter lista de cidades: {e}")
            return None

    return _LOCATIONS_CACHE.get(_normalize(city_name))

def _analyze_hourly_rain(lat, lon):
    """ 
    Usa Open-Meteo para verificar se a chuva é só de noite ou no horário laboral (08-20).
    Retorna um dicionário com contexto.
    """
    try:
        # Pede probabilidade de precipitação horária
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=precipitation_probability&timezone=auto&forecast_days=2"
        resp = httpx.get(url, timeout=5.0)
        data = resp.json()
        
        hourly = data.get('hourly', {})
        probs = hourly.get('precipitation_probability', [])
        # 'time' vem em ISO, mas a lista 'probs' começa na hora 00:00 do dia atual.
        
        # Índices para HOJE
        # Laboral: 08:00 (idx 8) a 20:00 (idx 20)
        # Noite/Manhã Cedo: 00-08 e 20-24
        
        today_probs = probs[0:24]
        
        work_hours = today_probs[8:21] # 08h às 20h (inclusive)
        off_hours = today_probs[0:8] + today_probs[21:24]
        
        max_work = max(work_hours) if work_hours else 0
        max_off = max(off_hours) if off_hours else 0
        
        # Amanhã (índices 24 a 48)
        tomorrow_probs = probs[24:48]
        max_tomorrow = max(tomorrow_probs) if tomorrow_probs else 0
        
        return {
            "max_rain_work": max_work,
            "max_rain_off": max_off,
            "max_rain_tomorrow": max_tomorrow
        }
    except Exception as e:
        print(f"[Weather] Erro na análise horária: {e}")
        return None

def _fetch_wunderground(lat, lon):
    """
    Tenta obter dados do Wunderground se a chave estiver configurada.
    """
    key = getattr(config, 'WUNDERGROUND_API_KEY', None)
    if not key: return None
    
    try:
        # Endpoint de previsão diária 5 dias
        url = f"https://api.weather.com/v3/wx/forecast/daily/5day?geocode={lat},{lon}&format=json&units=m&language=pt-PT&apiKey={key}"
        resp = httpx.get(url, timeout=5.0)
        
        if resp.status_code == 200:
            data = resp.json()
            # Retorna o texto narrativo para o dia e para a noite
            daypart = data.get('daypart', [{}])[0]
            narratives = daypart.get('narrative', [])
            # O array daypart geralmente tem [dia, noite, dia, noite...] ou [noite, dia...] se já for tarde
            # Vamos simplificar e devolver a primeira narrativa disponível
            if narratives:
                return narratives[0]
    except Exception as e:
        print(f"[Weather] Erro Wunderground: {e}")
    
    return None

def _fetch_city_data(global_id):
    result = {"timestamp": time.time(), "city_id": global_id}
    client = httpx.Client(timeout=10.0)
    
    try:
        # 1. IPMA (Base Oficial)
        url = f"https://api.ipma.pt/open-data/forecast/meteorology/cities/daily/{global_id}.json"
        data = client.get(url).json()
        result['forecast'] = data.get('data', [])
        
        if result['forecast']:
            lat = result['forecast'][0].get('latitude')
            lon = result['forecast'][0].get('longitude')
            
            # 2. Open-Meteo (UV, AQI e Análise Horária)
            try:
                # UV e AQI
                url_metrics = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=uv_index,us_aqi"
                metrics_data = client.get(url_metrics, timeout=3.0).json()
                
                result['uv'] = metrics_data.get('current', {}).get('uv_index')
                result['aqi'] = metrics_data.get('current', {}).get('us_aqi')
                
                # Análise Horária (Chuva Laboral vs Noturna)
                result['hourly_analysis'] = _analyze_hourly_rain(lat, lon)
                
            except Exception as e:
                print(f"[Weather] Erro Open-Meteo: {e}")

            # 3. Fallback IQAir (se Open-Meteo falhar AQI e houver chave)
            if 'aqi' not in result and hasattr(config, 'IQAIR_KEY') and config.IQAIR_KEY:
                try:
                    url_iq = f"http://api.airvisual.com/v2/nearest_city?lat={lat}&lon={lon}&key={config.IQAIR_KEY}"
                    iq_data = client.get(url_iq, timeout=5.0).json()
                    result['aqi'] = iq_data['data']['current']['pollution']['aqius']
                except: pass

            # 4. Comparativo Wunderground (Opcional)
            wg_narrative = _fetch_wunderground(lat, lon)
            if wg_narrative:
                result['wunderground_narrative'] = wg_narrative

        result['moon_phase'] = _get_moon_phase()
        return result

    except Exception as e:
        print(f"[Weather] Erro fetch principal: {e}")
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

        # 1. Tentar detetar localização
        location_match = re.search(r'\b(em|no|na)\s+(?!(?:hoje|amanhã)\b)([a-zà-ú\s]+)', user_prompt_lower)
        if location_match:
            city_searched = location_match.group(2).strip()
            city_id = _get_city_id(city_searched)
            if city_id:
                print(f"[Weather] Fetch on-demand para: {city_searched}")
                target_data = _fetch_city_data(city_id)
                target_city_name = city_searched.title()
            else:
                print(f"[Weather] Cidade '{city_searched}' desconhecida.")

        # 2. Fallback Cache
        if not target_data:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r') as f: target_data = json.load(f)
            else: return "Ainda estou a recolher dados meteorológicos."
            
        if not target_data or not target_data.get('forecast'):
            return "Não tenho dados de previsão disponíveis."

        # Dados Básicos IPMA
        is_tomorrow = "amanhã" in user_prompt_lower
        idx = 1 if is_tomorrow else 0
        if len(target_data['forecast']) <= idx: return "Previsão indisponível."
        
        day_forecast = target_data['forecast'][idx]
        t_max = int(round(float(day_forecast.get('tMax', 0))))
        t_min = int(round(float(day_forecast.get('tMin', 0))))
        ipma_prob = int(float(day_forecast.get('precipitaProb', 0)))
        w_desc = _get_weather_desc(day_forecast.get('idWeatherType'))
        
        current_hour = datetime.now().hour
        is_night = (current_hour >= 19 or current_hour < 7) and not is_tomorrow

        # Análise Horária (08-20h vs Noite)
        hourly = target_data.get('hourly_analysis')
        
        # --- Construção da Resposta ---

        # Se for pergunta sobre CHUVA
        if any(x in user_prompt_lower for x in ["chover", "chuva", "molhar", "água"]):
            prefix = f"Em {target_city_name}, " if target_city_name != DEFAULT_CITY_NAME else ""
            
            # Se for AMANHÃ
            if is_tomorrow:
                if ipma_prob > 50: return f"{prefix}Sim, prevê-se chuva ({ipma_prob}%) para amanhã."
                return f"{prefix}Para amanhã a probabilidade é de {ipma_prob}%."
            
            # Se for HOJE (Lógica Inteligente)
            if hourly and not is_night:
                max_work = hourly.get('max_rain_work', 0)
                max_off = hourly.get('max_rain_off', 0)
                
                # Caso 1: Chuva Laboral (A que chateia)
                if max_work >= 40:
                    return f"{prefix}Sim, conta com chuva durante o dia (probabilidade de {max_work}% no horário laboral)."
                
                # Caso 2: Chuva só "fora de horas"
                elif max_off >= 40:
                    return f"{prefix}Durante o dia de trabalho deve estar tranquilo, mas há previsão de chuva ({max_off}%) para o início da manhã ou noite."
                
                # Caso 3: IPMA diz chuva, mas horário diz pouco
                elif ipma_prob > 50:
                    return f"{prefix}O IPMA indica chuva, mas a análise horária mostra probabilidades baixas. Leva guarda-chuva só por precaução."
                    
                else:
                    return f"{prefix}Não, não deve chover hoje."
            
            # Fallback se não houver dados horários
            if ipma_prob >= 50: return f"{prefix}Sim, vai chover ({ipma_prob}%)."
            return f"{prefix}Não, não vai chover."

        # Resposta GERAL
        day_str = "amanhã" if is_tomorrow else "hoje"
        resp = f"Previsão para {day_str} em {target_city_name}: {w_desc}, máxima {t_max}°, mínima {t_min}°."

        # Adiciona nuance sobre a chuva na resposta geral de HOJE
        if not is_tomorrow and hourly and not is_night:
            max_work = hourly.get('max_rain_work', 0)
            if max_work > 30:
                resp += f" Atenção à chuva durante o dia ({max_work}%)."
            elif hourly.get('max_rain_off', 0) > 30:
                resp += " Possibilidade de chuva apenas de manhã cedo ou à noite."
        elif ipma_prob > 0:
             resp += f" Probabilidade de chuva: {ipma_prob}%."

        # Wunderground (Opcional - "Segunda Opinião")
        wg_text = target_data.get('wunderground_narrative')
        if wg_text:
            # Só adicionamos se o utilizador não perguntou especificamente "vai chover",
            # para não ficar uma resposta gigante, ou se a previsão divergir muito.
            resp += f" O Wunderground diz: \"{wg_text}\""

        # Extras (UV, AQI, Lua)
        extras = []
        if 'aqi' in target_data:
            aqi = target_data['aqi']
            d, a = _get_aqi_advice(aqi)
            extras.append(f"Ar {d} ({aqi})")
            
        if 'uv' in target_data and not is_night:
            uv = target_data['uv']
            d, a = _get_uv_advice(uv)
            extras.append(f"UV {int(round(uv))}")
            
        if is_night and 'moon_phase' in target_data:
            extras.append(f"Lua: {target_data['moon_phase']}")

        if extras:
            resp += " [" + ", ".join(extras) + "]"

        return resp

    except Exception as e:
        print(f"[Weather] Erro handle: {e}")
        return "Erro na meteorologia."
