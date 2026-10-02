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

    The header no longer says "use this if relevant" (T063). That clause gave
    the model permission to ignore the block, and it did: asked "conheces o
    Chefe Jamon?" with the right result sitting in the SearXNG response, the
    model answered "sim, eu sei quem e" and built a person out of a Czech
    ham e-shop. The instruction that makes it use the context, and the one that
    stops it inventing when the context does not cover the question, live in
    the prompt (assistant.py) -- not here, where they were being asked for and
    then contradicted on the next line.
    """
    lines = ["CONTEXTO DA WEB (fonte para esta resposta):\n"]
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


# ---------------------------------------------------------------------------
# Web archives (T063). NOT SearXNG engines -- measured 2026-10-02: SearXNG ships
# 216 engines and none of them is archive.org or arquivo.pt. Both expose a public
# JSON API, so these are providers here, in the open, rather than a fallback
# buried in a branch nobody reads.
#
# The trade, said out loud: this ADDS egress while a privacy finding is open.
# The query leaving for either archive is the raw user text, or a graph node
# label. What changes versus the removed Wikipedia fallback is that these are
# named, listed, and logged -- `Web RAG: respondeu <provider>` -- instead of a
# silent second attempt. If that trade is wrong, the right home is a
# `searx_engine` pointing at a SearXNG instance that already indexes them, and
# there is not one for archive.org.
# ---------------------------------------------------------------------------
def _arquivo_pt(prompt, max_results, client):
    """arquivo.pt's own text search. A web archive of the Portuguese web."""
    try:
        response = client.get(
            "https://arquivo.pt/textsearch",
            params={"q": prompt, "maxItems": max_results, "versionHistory": 0},
        )
        response.raise_for_status()
        hits = response.json().get("response_items", [])
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO (Web RAG): arquivo.pt falhou: {exc}")
        return []
    out = []
    for hit in hits[:max_results]:
        text = " ".join((hit.get("originalSnippet") or hit.get("linkToArchive") or "").split())
        if not text:
            continue
        out.append({
            "title": hit.get("title", ""),
            "url": hit.get("originalURL") or hit.get("linkToArchive", ""),
            "content": text,
        })
    return out


def _archive_org(prompt, max_results, client):
    """The Internet Archive's item/metadata search."""
    try:
        response = client.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": prompt,
                "rows": max_results,
                "output": "json",
                "fl[]": ["identifier", "title", "description"],
            },
        )
        response.raise_for_status()
        docs = response.json().get("response", {}).get("docs", [])
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO (Web RAG): archive.org falhou: {exc}")
        return []
    out = []
    for doc in docs[:max_results]:
        ident = doc.get("identifier") or ""
        text = " ".join(str(doc.get("description") or "").split())
        if not text:
            continue
        out.append({
            "title": doc.get("title") or ident,
            "url": f"https://archive.org/details/{ident}" if ident else "",
            "content": text,
        })
    return out


# (name, callable). Read in order, first answer wins. Listed here so the set of
# places a query can leave the house is one screen, not one per branch.
PROVIDERS = (
    ("searxng", _searxng),
    ("arquivo.pt", _arquivo_pt),
    ("archive.org", _archive_org),
)


def search_with_searxng(prompt, max_results=3):
    """Pesquisa na web e devolve contexto para o LLM.

    SearXNG first, and when it returns nothing the web archives are tried in
    the order in ``PROVIDERS``. Every provider that answers is named in the log.

    There used to be a second, hard-coded path to pt.wikipedia.org here. It is
    gone (T063): the SearXNG config already carries wikipedia as an engine
    (``searxng-settings.yml``), so the archive was reachable without the app
    holding a private door to it -- and that door was the finding. It sent the
    owner's memory text to a third party on a schedule nobody could see, with
    one `print` nobody read, and the privacy promise in CLAUDE.md was false
    while it existed.
    """
    print(f"A pesquisar na web: '{prompt}'")
    results = []
    try:
        with httpx.Client(timeout=10.0, headers=_HEADERS, follow_redirects=True) as client:
            for name, provider in PROVIDERS:
                results = provider(prompt, max_results, client)
                if results:
                    print(f"Web RAG: respondeu {name}.")
                    break
                print(f"Web RAG: {name} sem resultados.")
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
