import re
import httpx

TRIGGER_TYPE = "contains"
TRIGGERS = ["tempo", "clima", "meteorologia", "previsão", "vai chover", "vai estar", "qualidade do ar"]

def _get_aqi_advice(aqi_value):
    """ Retorna (descrição, conselho) baseado no índice CAQI. """
    if aqi_value is None: 
        return "sem dados", ""
    
    # Escala CAQI (Common Air Quality Index - Europe)
    if aqi_value < 25: 
        return "excelente", "Aproveita para abrir as janelas."
    if aqi_value < 50: 
        return "boa", "Podes fazer atividades ao ar livre à vontade."
    if aqi_value < 75: 
        return "moderada", "Se fores sensível, evita esforços prolongados na rua."
    if aqi_value < 100: 
        return "fraca", "É melhor reduzir as atividades intensas ao ar livre."
    return "má", "Evita sair de casa ou fazer exercício na rua."

def _get_uv_advice(uv_index):
    """ Retorna (descrição, conselho) baseado no índice UV. """
    try:
        uv = float(uv_index)
    except (ValueError, TypeError):
        return "desconhecido", ""

    if uv <= 2:
        return "baixo", "Não precisas de proteção extra."
    if uv <= 5:
        return "moderado", "Usa óculos de sol se saíres."
    if uv <= 7:
        return "alto", "Usa protetor solar e chapéu."
    if uv <= 10:
        return "muito alto", "Evita o sol direto, é perigoso."
    return "extremo", "Não saias à rua sem proteção máxima."

def handle(user_prompt_lower, user_prompt_full):
    """ Obtém previsão do tempo, UV e Qualidade do Ar com recomendações. """
    
    print("A tentar obter meteorologia completa...")
    
    weather_translations = {
        "Sunny": "sol", # Ajustado para fluir com "e está sol"
        "Clear": "céu limpo",
        "Partly cloudy": "parcialmente nublado",
        "Cloudy": "nublado",
        "Overcast": "encoberto",
        "Mist": "nevoeiro",
        "Fog": "nevoeiro",
        "Patchy rain possible": "possibilidade de aguaceiros",
        "Patchy rain nearby": "aguaceiros por perto",
        "Shower in vicinity": "aguaceiros por perto",
        "Patchy light rain": "aguaceiros fracos",
        "Light rain": "chuva fraca",
        "Light rain shower": "aguaceiros fracos",
        "Moderate rain": "chuva moderada",
        "Heavy rain": "chuva forte",
        "Heavy rain at times": "chuva forte por vezes",
        "Moderate or heavy rain shower": "aguaceiros fortes",
        "Thundery outbreaks possible": "possibilidade de trovoada"
    }

    location = "Porto"
    match = re.search(r'\b(no|na|em|para)\s+(?!(?:hoje|amanhã)\b)([A-Za-zÀ-ú\s]+)', user_prompt_lower)
    
    if match:
        location = match.group(2).strip().replace(" ", "+")
        print(f"Localização explícita encontrada: {location}")
    else:
        print("Nenhuma localização explícita. A assumir 'Porto'.")

    try:
        # 1. Obter Meteorologia (WTTR.IN)
        url_wttr = f"https://wttr.in/{location}?format=j1"
        client = httpx.Client(timeout=10.0)
        response_wttr = client.get(url_wttr)
        response_wttr.raise_for_status()
        data = response_wttr.json()
        
        nearest = data['nearest_area'][0]
        location_name = nearest['areaName'][0]['value']
        lat = nearest['latitude']
        lon = nearest['longitude']
        
        display_location = "no Porto" if location_name == "Oporto" else f"em {location_name}"

        # 2. Obter Qualidade do Ar (OPEN-METEO)
        url_aqi = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=european_aqi"
        aqi_desc, aqi_advice = "desconhecida", ""
        
        try:
            response_aqi = client.get(url_aqi, timeout=4.0)
            if response_aqi.status_code == 200:
                data_aqi = response_aqi.json()
                aqi_val = data_aqi.get('current', {}).get('european_aqi')
                aqi_desc, aqi_advice = _get_aqi_advice(aqi_val)
        except Exception:
            pass # Falha silenciosa no AQI
        
        # 3. Construir a Resposta
        if "amanhã" in user_prompt_lower:
            forecast = data['weather'][1]
            cond_en = forecast['hourly'][4]['weatherDesc'][0]['value']
            cond = weather_translations.get(cond_en, cond_en) 
            max_t = forecast['maxtempC']
            min_t = forecast['mintempC']
            
            uv_val = forecast.get('uvIndex', '0')
            uv_desc, uv_advice = _get_uv_advice(uv_val)
            
            response_str = (
                f"A previsão para amanhã {display_location} aponta para {cond}, "
                f"com máxima de {max_t} e mínima de {min_t} graus. "
                f"O índice UV será {uv_desc} ({uv_val}), portanto {uv_advice}"
            )
            
            # Limpeza final se o conselho vier vazio
            response_str = response_str.replace(" ,", ",").replace(" .", ".")
            return response_str
            
        else:
            forecast = data['weather'][0]
            current = data['current_condition'][0]
            
            cond_key = current['weatherDesc'][0]['value']
            cond = weather_translations.get(cond_key, cond_key).lower()
            
            temp = current['temp_C']
            max_t = forecast['maxtempC']
            min_t = forecast['mintempC']
            
            uv_val = current.get('uvIndex', '0')
            uv_desc, uv_advice = _get_uv_advice(uv_val)

            # Construção da frase natural
            # Ex: "Atualmente no Porto estão 12 graus e céu limpo."
            msg = (
                f"Atualmente {display_location} estão {temp} graus e está {cond}. "
                f"A qualidade do ar é {aqi_desc}. {aqi_advice} "
                f"O índice UV está {uv_desc} ({uv_val}); {uv_advice} "
                f"Hoje a máxima chega aos {max_t} e a mínima desce aos {min_t}."
            )
            
            return msg

    except Exception as e:
        print(f"ERRO skill_weather: {e}")
        return None
