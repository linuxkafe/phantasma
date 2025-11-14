import re
import httpx
import config

# --- Configuração da Skill ---
TRIGGER_TYPE = "startswith"
TRIGGERS = ["pergunta à gemini", "o que diz a gemini", "pede à gemini"]

# --- Constantes da API Gemini ---
# Usamos o modelo gemini-pro, que é o mais comum para texto
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent"

def handle(user_prompt_lower, user_prompt_full):
    """
    Extrai uma pergunta e envia-a para a API do Google Gemini.
    """
    
    # 1. Verificar se a API Key está configurada
    if not config.GEMINI_API_KEY or config.GEMINI_API_KEY == "A_TUA_API_KEY_DO_GEMINI_AQUI":
        print("ERRO (Skill Gemini): A GEMINI_API_KEY não está definida em config.py.")
        # Responde ao utilizador a dizer que falta a chave
        return "Parece que ainda não me deu a chave para ligar aos meus amigos da Google. Pede-lhe para configurar a GEMINI_API_KEY."

    # 2. Extrair a pergunta real
    # Usamos o user_prompt_full (com maiúsculas) para obter a pergunta exata
    try:
        # Encontra o trigger exato que foi usado, ignorando o case
        trigger_pattern = r'|'.join(TRIGGERS)
        # Remove o trigger do início da frase
        question = re.sub(trigger_pattern, '', user_prompt_full, 1, flags=re.IGNORECASE).strip()
    except Exception:
        question = "" # Fallback

    if not question:
        print("AVISO (Skill Gemini): Trigger detetado, mas sem pergunta.")
        return "Sim, seyon? O que é que querias que eu perguntasse aos meus amigos?"

    print(f"A enviar pergunta para o Gemini: '{question}'")

    # 3. Preparar e Enviar o Pedido para a API
    headers = {
        'Content-Type': 'application/json'
    }
    
    payload = {
        "contents": [{
            "parts": [{
                "text": question
            }]
        }]
    }
    
    # Construir o URL com a chave
    url_with_key = f"{GEMINI_API_URL}?key={config.GEMINI_API_KEY}"

    try:
        client = httpx.Client(timeout=30.0) # Timeout generoso
        response = client.post(url_with_key, headers=headers, json=payload)
        response.raise_for_status() # Lança erro se a API retornar 4xx ou 5xx
        
        data = response.json()
        
        # 4. Extrair a resposta do JSON complexo do Gemini
        # Navegação segura para evitar KeyErrors
        gemini_response_text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', None)

        if gemini_response_text:
            # SUCESSO!
            return f"A minha amiga Gemini disse isto: {gemini_response_text.strip()}"
        else:
            print(f"ERRO (Skill Gemini): Resposta da API vazia ou mal formatada. JSON: {data}")
            return "Os meus amigos da Google responderam, mas não consegui perceber o que eles disseram. Tenta outra vez."

    except httpx.TimeoutException:
        print("ERRO (Skill Gemini): Timeout ao contactar a API do Gemini.")
        return "Os meus amigos da Google estão a demorar muito a responder, chefe."
    except httpx.HTTPStatusError as e:
        print(f"ERRO (Skill Gemini): A API do Gemini retornou um erro {e.response.status_code}: {e.response.text}")
        return f"Ups! Tentei ligar à Gemini, mas a chamada falhou com o erro {e.response.status_code}."
    except Exception as e:
        print(f"ERRO CRÍTICO (Skill Gemini): {e}")
        return "Ocorreu um erro inesperado ao tentar falar com os meus amigos da Google."
