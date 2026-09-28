import httpx

import config

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
}


def _fmt(results, max_results):
    """Turn raw hits into the context block the LLM reads.

    Title and URL were dropped before, so the assistant could not cite where
    a claim came from and a snippet arrived with no idea what it described.
    """
    lines = ["CONTEXTO DA WEB (Usa isto para responder se for relevante):\n"]
    for res in results:
        content = (res.get("content") or "").strip()
        if not content:
            continue
        title = (res.get("title") or "").strip()
        url = (res.get("url") or "").strip()
        head = f"- {title}" if title else "-"
        lines.append(f"{head}\n  {content}\n  fonte: {url}\n" if url else f"{head}\n  {content}\n")
        if len(lines) - 1 >= max_results:
            break
    return "".join(lines) if len(lines) > 1 else ""


def _searxng(prompt, max_results, client):
    """Query the local SearXNG. Returns [] when it is down or returns nothing."""
    if not config.config.searxng_url:
        return []
    try:
        response = client.get(
            f"{config.config.searxng_url}/search",
            params={"q": prompt, "format": "json"},
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except httpx.ConnectError:
        print(f"ERRO (Web RAG): SearXNG inacessível em {config.config.searxng_url}")
        return []
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO (Web RAG): SearXNG falhou: {exc}")
        return []


def _wikipedia(prompt, max_results, client):
    """Fallback: the Wikipedia search API.

    SearXNG's general engines are all blocked from this host's IP, so when the
    metasearch comes back empty -- or the container is restarting -- this keeps
    search working. It is a real search, not a cache, and it answers from the
    host, which is not the address any engine is blocking.
    """
    try:
        response = client.get(
            "https://pt.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": prompt,
                "format": "json",
                "srlimit": max_results,
            },
        )
        response.raise_for_status()
        hits = response.json().get("query", {}).get("search", [])
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO (Web RAG): Wikipedia falhou: {exc}")
        return []

    out = []
    for hit in hits:
        text = (hit.get("snippet") or "").strip()
        if not text:
            continue
        out.append({
            "title": hit.get("title", ""),
            "url": "https://pt.wikipedia.org/wiki/" + str(hit.get("title", "")).replace(" ", "_"),
            "content": text,
        })
    return out


def search_with_searxng(prompt, max_results=3):
    """Pesquisa na web e devolve contexto para o LLM.

    Tenta o SearXNG local primeiro; se não devolver nada, cai na API da
    Wikipedia. A pesquisa é funcional mesmo sem SearXNG -- um único motor
    bloqueado deixa de decidir se o assistente consegue pesquisar.
    """
    print(f"A pesquisar na web: '{prompt}'")
    try:
        with httpx.Client(timeout=10.0, headers=_HEADERS, follow_redirects=True) as client:
            results = _searxng(prompt, max_results, client)
            if not results:
                print("Web RAG: SearXNG sem resultados, a tentar Wikipedia.")
                results = _wikipedia(prompt, max_results, client)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO (Web RAG): {exc}")
        return ""

    if not results:
        print("Web RAG: Nenhum resultado encontrado.")
        return ""

    context_str = _fmt(results, max_results)
    if not context_str:
        print("Web RAG: resultados sem snippet, nada a entregar ao LLM.")
        return ""
    print(f"Web RAG: Contexto encontrado:\n{context_str}")
    return context_str
