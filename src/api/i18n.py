"""Bilingual (PT/EN) string catalogue for the Phantasma admin UI.

Design notes
------------
* Strings live in one flat dict keyed ``area.key``. Each entry carries both
  languages, so a missing translation is a *visible* test failure rather than a
  silent fallback to the key name.
* Language resolution order: explicit session choice -> cookie -> browser
  ``Accept-Language`` -> ``DEFAULT_LANGUAGE``. An explicit choice always wins,
  so toggling the switch sticks even if the browser asks for another language.
* ``t()`` never raises. A missing key returns the key itself, which is obvious
  in the UI and covered by ``test_i18n.py``. Swallowing the error and rendering
  an empty string would be worse: a blank label looks intentional.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LANGUAGE = "pt"
LANGUAGES: dict[str, str] = {"pt": "Português", "en": "English"}
LANGUAGE_CODES = tuple(LANGUAGES)

COOKIE_NAME = "phantasma_lang"


STRINGS: dict[str, dict[str, str]] = {
    # --- navigation ---------------------------------------------------------
    "nav.dashboard": {"pt": "Dashboard", "en": "Dashboard"},
    "nav.brain": {"pt": "Cérebro", "en": "Brain"},
    "nav.config": {"pt": "Configuração", "en": "Settings"},
    "nav.users": {"pt": "Utilizadores", "en": "Users"},
    "nav.env": {"pt": ".env", "en": ".env"},
    "nav.logout": {"pt": "Sair", "en": "Sign out"},
    "nav.language": {"pt": "Idioma", "en": "Language"},
    # --- brain section ------------------------------------------------------
    "brain.hub": {"pt": "Tudo", "en": "All"},
    "brain.memory": {"pt": "Memória", "en": "Memory"},
    "brain.rag": {"pt": "RAG", "en": "RAG"},
    "brain.flybrain": {"pt": "FlyBrain", "en": "FlyBrain"},
    "brain.explorer": {"pt": "Explorador 3D", "en": "3D Explorer"},
    "brain.overview": {"pt": "Visão geral", "en": "Overview"},
    "brain.section_label": {"pt": "Cérebro", "en": "Brain"},
    # --- generic ------------------------------------------------------------
    "common.save": {"pt": "Guardar", "en": "Save"},
    "common.cancel": {"pt": "Cancelar", "en": "Cancel"},
    "common.loading": {"pt": "A carregar…", "en": "Loading…"},
    "common.empty": {"pt": "Sem dados", "en": "No data"},
    "common.error": {"pt": "Erro", "en": "Error"},
    "common.back": {"pt": "Voltar", "en": "Back"},
    "common.refresh": {"pt": "Atualizar", "en": "Refresh"},
    "common.close": {"pt": "Fechar", "en": "Close"},
    "common.search": {"pt": "Pesquisar", "en": "Search"},
    "common.filter": {"pt": "Filtro", "en": "Filter"},
    "common.all": {"pt": "Todos", "en": "All"},
    "common.none": {"pt": "Nenhum", "en": "None"},
    "common.yes": {"pt": "Sim", "en": "Yes"},
    "common.no": {"pt": "Não", "en": "No"},
    "common.never": {"pt": "Nunca", "en": "Never"},
    "common.unknown": {"pt": "Desconhecido", "en": "Unknown"},
    # --- dashboard ----------------------------------------------------------
    "dashboard.title": {"pt": "Painel", "en": "Dashboard"},
    "dashboard.memories": {"pt": "Memórias", "en": "Memories"},
    "dashboard.graph_nodes": {"pt": "Nós do grafo", "en": "Graph nodes"},
    "dashboard.skills": {"pt": "Competências", "en": "Skills"},
    "dashboard.devices": {"pt": "Dispositivos", "en": "Devices"},
    "dashboard.flybrain": {"pt": "Estado FlyBrain", "en": "FlyBrain state"},
    "dashboard.no_state": {"pt": "sem estado", "en": "no state"},
    # --- memory / RAG -------------------------------------------------------
    "memory.title": {"pt": "Memória", "en": "Memory"},
    "memory.search": {"pt": "Pesquisar memórias", "en": "Search memories"},
    "memory.results": {"pt": "Resultados", "en": "Results"},
    "memory.empty": {"pt": "Nenhuma memória encontrada", "en": "No memories found"},
    "memory.record": {"pt": "Registo", "en": "Record"},
    "rag.title": {"pt": "RAG — últimas memórias", "en": "RAG — recent memories"},
    "rag.subtitle": {
        "pt": "Fragmentos recuperados pela busca semântica",
        "en": "Chunks retrieved by semantic search",
    },
    "rag.empty": {
        "pt": "Sem memórias indexadas",
        "en": "No indexed memories",
    },
    # --- flybrain -----------------------------------------------------------
    "flybrain.title": {"pt": "FlyBrain", "en": "FlyBrain"},
    "flybrain.reinforcement": {"pt": "Reforço", "en": "Reinforcement"},
    "flybrain.orientation": {"pt": "Orientação", "en": "Orientation"},
    "flybrain.affinity": {"pt": "Afinidade", "en": "Affinity"},
    "flybrain.steps": {"pt": "Passos", "en": "Steps"},
    "flybrain.empty": {
        "pt": "Sem estado de FlyBrain guardado. Oolonamento escreve aqui quando o vosso primeiro ciclo de reforço terminar.",
        "en": "No stored FlyBrain state. It is written here when your first reinforcement cycle finishes.",
    },
    # --- auth ---------------------------------------------------------------
    "auth.email": {"pt": "E-mail", "en": "Email"},
    "auth.password": {"pt": "Palavra-passe", "en": "Password"},
    "auth.signin": {"pt": "Entrar", "en": "Sign in"},
    "auth.email_required": {"pt": "E-mail obrigatório", "en": "Email is required"},
    # --- env ----------------------------------------------------------------
    "env.title": {"pt": "Variáveis de ambiente", "en": "Environment variables"},
    "env.subtitle": {
        "pt": "Edite o ficheiro .env e reinicie o serviço para aplicar",
        "en": "Edit the .env file and restart the service to apply",
    },
    "env.save": {"pt": "Guardar .env", "en": "Save .env"},
    "env.saved": {"pt": "Ficheiro gravado", "en": "File written"},
    "env.restart_warning": {
        "pt": "As alterações só entram em vigor depois de reiniciar o serviço.",
        "en": "Changes only take effect after restarting the service.",
    },
    # --- explorer -----------------------------------------------------------
    "explorer.title": {"pt": "Explorador 3D", "en": "3D Explorer"},
    "explorer.subtitle": {
        "pt": "Grafo de memória em WebGL, construído a partir de dados reais",
        "en": "WebGL memory graph, built from real data",
    },
    # --- config / users -----------------------------------------------------
    "config.title": {"pt": "Configuração", "en": "Settings"},
    "users.title": {"pt": "Utilizadores", "en": "Users"},
    "users.add": {"pt": "Adicionar utilizador", "en": "Add user"},
    "users.role": {"pt": "Função", "en": "Role"},
    "users.admin": {"pt": "Administrador", "en": "Admin"},
    "users.user": {"pt": "Utilizador", "en": "User"},
}


def normalize_language(code: Any) -> str:
    """Map an arbitrary code to a supported one, defaulting to PT."""
    if not isinstance(code, str):
        return DEFAULT_LANGUAGE
    base = code.strip().lower().split("-")[0]
    return base if base in LANGUAGES else DEFAULT_LANGUAGE


def parse_accept_language(header: str | None) -> str | None:
    """Return the best supported language from an Accept-Language header."""
    if not header:
        return None
    ranked: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        part = part.strip()
        if not part:
            continue
        bits = part.split(";")
        tag = bits[0].strip().lower()
        quality = 1.0
        for extra in bits[1:]:
            extra = extra.strip()
            if extra.startswith("q="):
                try:
                    quality = float(extra[2:])
                except ValueError:
                    quality = 0.0
        base = tag.split("-")[0]
        if base in LANGUAGES:
            # higher q first, then original order
            ranked.append((-quality, index, base))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][2]


def t(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Translate ``key``. Returns the key itself when unknown (never blank)."""
    language = normalize_language(lang) if lang else DEFAULT_LANGUAGE
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(language) or entry.get(DEFAULT_LANGUAGE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def missing_translations() -> dict[str, list[str]]:
    """Keys that lack a language. Consumed by tests/test_i18n.py."""
    gaps: dict[str, list[str]] = {}
    for key, entry in STRINGS.items():
        absent = [code for code in LANGUAGE_CODES if not entry.get(code)]
        if absent:
            gaps[key] = absent
    return gaps


def unused_keys(used: set[str]) -> list[str]:
    """Catalogue entries no template references. Informational only."""
    return sorted(set(STRINGS) - used)
