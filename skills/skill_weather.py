import re
import httpx

TRIGGER_TYPE = "contains"
TRIGGERS = ["tempo", "clima", "meteorologia", "previsão", "vai chover", "vai estar"]

def handle(user_prompt_lower, user_prompt_full):
    """ Tenta obter a previsão do tempo. """
    
    print("A tentar obter meteorologia...")
    
    # <--- MODIFICADO: Dicionário de tradução expandido ---
    weather_translations = {
        "Sunny": "Ensolarado",
        "Clear": "Céu limpo",
        "Partly cloudy": "Parcialmente nublado",
        "Cloudy": "Nublado",
        "Overcast": "Encoberto",
        "Mist": "Névoa",
        "Fog": "Nevoeiro",
        "Patchy rain possible": "Possibilidade de aguaceiros",
        "Patchy rain nearby": "Aguaceiros nas proximidades", # <--- ADICIONADO
        "Patchy light rain": "Aguaceiros fracos",
        "Light rain": "Chuva fraca",
        "Light rain shower": "Aguaceiros fracos",
        "Moderate rain": "Chuva moderada",
        "Heavy rain": "Chuva forte",
        "Heavy rain at times": "Chuva forte por vezes",
        "Moderate or heavy rain shower": "Aguaceiros moderados ou fortes",
        "Thundery outbreaks possible": "Possibilidade de trovoada"
    }
    # <--- FIM DA MODIFICAÇÃO ---

    location = "Porto"
    match = re.search(r'\b(no|na|em|para)\s+(?!(?:hoje|amanhã)\b)([A-Za-zÀ-ú\s]+)', user_prompt_lower)
    
    if match:
        location = match.group(2).strip().replace(" ", "+")
        print(f"Localização explícita encontrada: {location}")
    else:
        print("Nenhuma localização explícita. A assumir 'Porto'.")

    try:
        url = f"https://wttr.in/{location}?format=j1"
        client = httpx.Client(timeout=10.0)
        response = client.get(url)
        response.raise_for_status()
        data = response.json()
        location_name = data['nearest_area'][0]['areaName'][0]['value']
        
        display_location = "no Porto" if location_name == "Oporto" else f"em {location_name}"
        
        if "amanhã" in user_prompt_lower:
            forecast = data['weather'][1]
            cond_en = forecast['hourly'][4]['weatherDesc'][0]['value']
            # Tenta traduzir; se falhar, usa o original em inglês
            cond = weather_translations.get(cond_en, cond_en) 
            max_t = forecast['maxtempC']
            min_t = forecast['mintempC']
            
            # --- MODIFICADO: Lógica de resumo de chuva ---
            response_str = f"A previsão para amanhã {display_location} é: {cond}, com máxima de {max_t} e mínima de {min_t} graus."
            
            cond_en_lower = cond_en.lower()
            if "rain" in cond_en_lower or "shower" in cond_en_lower or "thundery" in cond_en_lower:
                response_str += " Amanhã vai chover."
            else:
                response_str += " Amanhã não chove."
            # <--- FIM DA MODIFICAÇÃO ---
                
            return response_str
        else:
            forecast = data['weather'][0]
            current = data['current_condition'][0]
            cond_atual_en = current['weatherDesc'][0]['value']
            # Tenta traduzir; se falhar, usa o original em inglês
            cond_atual = weather_translations.get(cond_atual_en, cond_atual_en)
            temp_atual = current['temp_C']
            max_t = forecast['maxtempC']
            min_t = forecast['mintempC']
            return f"Atualmente {display_location} estão {temp_atual} graus com {cond_atual}. A máxima para hoje é {max_t} e a mínima {min_t}."
    except Exception as e:
        print(f"ERRO ao obter meteorologia: {e}")
        return None # Deixa o Ollama tratar
