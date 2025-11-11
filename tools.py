import httpx
import config

def search_with_searxng(prompt, max_results=3):
    """
    Pesquisa na web usando SearxNG e retorna snippets de contexto.
    """
    if not config.SEARXNG_URL:
        return "" # Ignora se a URL não estiver definida

    print(f"A pesquisar na web (SearxNG): '{prompt}'")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
        }
        
        client = httpx.Client(timeout=10.0, headers=headers)
        response = client.get(
            f"{config.SEARXNG_URL}/search",
            params={'q': prompt, 'format': 'json'}
        )
        response.raise_for_status()
        data = response.json()
        
        results = data.get('results', [])
        if not results:
            print("Web RAG: Nenhum resultado encontrado.")
            return ""

        context_str = "CONTEXTO DA WEB (Usa isto para responder se for relevante):\n"
        count = 0
        for res in results:
            if res.get('content') and count < max_results:
                context_str += f"- {res['content']}\n"
                count += 1
        
        print(f"Web RAG: Contexto encontrado:\n{context_str}")
        return context_str

    except httpx.ConnectError:
        print(f"ERRO (Web RAG): Não foi possível ligar ao SearxNG em {config.SEARXNG_URL}")
        return ""
    except Exception as e:
        print(f"ERRO (Web RAG): Falha ao pesquisar no SearxNG: {e}")
        return ""
