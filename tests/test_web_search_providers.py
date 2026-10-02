"""Pesquisa web: onde pode sair, e o que o modelo faz com o que volta (T063).

Três defeitos, um por ficheiro.

**1. A Wikipédia tinha uma porta de serviço em `tools.py`.** `_wikipedia()` fazia
`GET https://pt.wikipedia.org/w/api.php` com o `prompt` — e o `prompt` é, nos
caminhos `assistant.py:959` e `skill_dream.py:389,431,591`, o texto bruto do
utilizador ou uma etiqueta de um nó do grafo. Era chamada quando o SearXNG
devolvia vazio, sem anunciar. Enquanto existia, a promessa de privacidade do
`CLAUDE.md` era falsa. E o SearXNG **já tinha** `wikipedia` como motor
(`searxng-settings.yml:97`), ou seja a porta era redundante *e*隐蔽.

**2. archive.org e arquivo.pt não são motores do SearXNG.** Medido: 216 motores,
nenhum dos dois. Ambos têm API pública (`arquivo.pt/textsearch` → 200,
`archive.org/advancedsearch.php` → 200). Entraram como prestadores nomeados, e
cada um regista no log que respondeu — que é o oposto de um fallback escondido.

**3. O prompt não mandava usar o contexto web, e não proibia inventar.** Medido
contra o Ollama de produção: com o resultado certo dentro do contexto, a
resposta a "conheces o Chefe Jamon?" foi *"sim, eu sei quem é"* e um e-shop de
presunto checo virar uma pessoa. A regra contra a invenção existia — mas só nos
ramos em que a pesquisa **tinha falhado**.

Estes testes verificam o que é verificável sem LLM: **para onde pode sair uma
consulta** e **o que o prompt diz ao modelo**. A prova de que o prompt muda a
resposta é a medição com o modelo real, registada em
`aes/tickets/T063-*.md` §3, e não cabe num teste de unidade.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import tools  # noqa: E402

WIKI = "pt.wikipedia.org"
# The real configured host, so the fake matches the URL that will actually be
# requested. A placeholder key like "searxng_url/search" does not occur in
# "http://127.0.0.1:8081/search", and the test then failed for a reason that
# had nothing to do with the provider ordering it was checking.
SEARXNG = f"{config.config.searxng_url}/search"
ARCHIVES = ("arquivo.pt", "archive.org")


def _read(path: str) -> str:
    return open(path, encoding="utf-8").read()


# --------------------------------------------------------------------------
# 1. A porta de serviço
# --------------------------------------------------------------------------
def test_tools_makes_no_wikipedia_request():
    """A asserção mais simples e a mais importante.

    Feita sobre o ENDPOINT (`w/api.php`), não sobre o nome do host: a docstring
    da função menciona `pt.wikipedia.org` ao explicar que a porta saiu, e um
    teste que proibisse a menção passaria a proibir a explicação. Isto verifica
    que não há um pedido, que é o que importa.
    """
    src = _read(os.path.join(os.path.dirname(__file__), "..", "tools.py"))
    assert "w/api.php" not in src, (
        f"tools.py ainda tem um pedido directo ao {WIKI}: a consulta do dono "
        f"sai da casa por uma porta que ninguem le"
    )


def test_no_module_level_function_is_named_after_a_hidden_archive():
    assert not hasattr(tools, "_wikipedia"), (
        "_wikipedia ainda existe como fallback; o SearXNG ja tem o motor"
    )


def test_the_provider_list_is_readable_in_one_place():
    """O conjunto de sítios por onde uma consulta pode sair tem de caber numa
    linha de código legível. Foi um `if not results:` dentro de uma função que
    escondia o segundo destino durante meses."""
    names = [n for n, _ in tools.PROVIDERS]
    assert names[0] == "searxng", f"o SearXNG tem de ser o primeiro: {names}"
    for archive in ARCHIVES:
        assert archive in names, f"{archive} não está em PROVIDERS: {names}"


# --------------------------------------------------------------------------
# 2. Comportamento, com a rede trocada
# --------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, answers):
        self.answers = answers
        self.seen: list[str] = []

    def get(self, url, params=None, **kw):
        self.seen.append(url)
        # Matched on host+path, NOT on a bare fragment: "search" is a substring
        # of "textsearch" AND of "advancedsearch", so a fragment match silently
        # served SearXNG's empty payload to arquivo.pt. That is how this test
        # spent its first run failing for a reason that had nothing to do with
        # the code under test.
        for key, payload in self.answers.items():
            if key in url:
                return _FakeResponse(payload)
        raise AssertionError(f"provider desconhecido: {url}")


def _run(monkeypatch, answers):
    client = _FakeClient(answers)

    class _Ctx:
        def __enter__(self_inner):
            return client

        def __exit__(self_inner, *a):
            return False

    monkeypatch.setattr(tools.httpx, "Client", lambda **kw: _Ctx())
    return client


def test_searxng_answering_means_nothing_else_is_asked(monkeypatch):
    searxng = {"results": [{"title": "Chefe Jamon", "content": "Bacalhau com Natas",
                            "url": "https://youtube.example/x"}]}
    client = _run(monkeypatch, {SEARXNG: searxng, "arquivo.pt/textsearch": {},
            "archive.org/advancedsearch": {}})
    out = tools.search_with_searxng("conheces o Chefe Jamon?", 3)
    assert "Bacalhau com Natas" in out
    assert len(client.seen) == 1, f"mais do que um prestador foi consultado: {client.seen}"
    assert client.seen[0].endswith("/search"), client.seen


def test_the_archives_answer_when_searxng_does_not(monkeypatch):
    """A ordem é SearXNG, arquivo.pt, archive.org — e cada um é nomeado."""
    client = _run(
        monkeypatch,
        {
            SEARXNG: {"results": []},
            "arquivo.pt/textsearch": {"response_items": [
                {"title": "Cira", "originalURL": "https://arquivo.pt/wayback/1",
                 "originalSnippet": "chefe de cozinha"}]},
            "archive.org/advancedsearch": {"response": {"docs": []}},
        },
    )
    out = tools.search_with_searxng("chef", 3)
    assert "chefe de cozinha" in out
    assert len(client.seen) == 2, client.seen
    assert "textsearch" in client.seen[1]


def test_archive_org_is_the_last_resort(monkeypatch):
    client = _run(
        monkeypatch,
        {
            SEARXNG: {"results": []},
            "arquivo.pt/textsearch": {"response_items": []},
            "archive.org/advancedsearch": {"response": {"docs": [
                {"identifier": "chef-jamon", "title": "Chefe Jamon",
                 "description": "programa de tv"}]}},
        },
    )
    out = tools.search_with_searxng("chef", 3)
    assert "programa de tv" in out
    assert len(client.seen) == 3, client.seen


def test_when_everything_is_empty_the_answer_is_empty(monkeypatch, capsys):
    """Antes, isto devolvia contexto da Wikipédia. Agora devolve nada — e diz."""
    _run(monkeypatch, {SEARXNG: {"results": []},
                        "arquivo.pt/textsearch": {"response_items": []},
                        "archive.org/advancedsearch": {"response": {"docs": []}}})
    assert tools.search_with_searxng("assunto raro", 3) == ""
    said = capsys.readouterr().out
    assert "Nenhum resultado" in said


def test_every_provider_that_answered_is_named(monkeypatch, capsys):
    """O que se perdeu com o fallback da Wikipédia era o rasto: uma segunda
    tentativa que só existia num `print` que ninguém lia."""
    _run(monkeypatch, {SEARXNG: {"results": []},
                        "arquivo.pt/textsearch": {"response_items": [
                            {"title": "x", "originalURL": "u", "originalSnippet": "y"}]},
                        "archive.org/advancedsearch": {"response": {"docs": []}}})
    tools.search_with_searxng("q", 3)
    said = capsys.readouterr().out
    assert "searxng sem resultados" in said
    assert "respondeu arquivo.pt" in said


# --------------------------------------------------------------------------
# 3. O prompt
# --------------------------------------------------------------------------
def _prompt_block(source: str, anchor: str) -> str:
    start = source.index(anchor)
    return source[start:start + 1400]


@pytest.fixture(scope="module")
def assistant_src():
    return _read(os.path.join(os.path.dirname(__file__), "..", "assistant.py"))


def test_the_branch_with_context_tells_the_model_to_use_it(assistant_src):
    """O defeito: havia contexto web e nenhuma instrução sobre ele."""
    block = _prompt_block(assistant_src, 'parts.append(f"### PESQUISA WEB')
    assert "COMO RESPONDER COM A PESQUISA" in block, (
        "o ramo em que HÁ contexto web continua sem instrução para o usar; a "
        "regra contra a invenção só existe nos ramos em que a pesquisa FALHOU"
    )


def test_the_no_inventing_rule_applies_where_the_search_worked(assistant_src):
    block = _prompt_block(assistant_src, 'parts.append(f"### PESQUISA WEB')
    assert "NAO inventes" in block, (
        "sem a regra de não inventar no ramo em que a pesquisa funcionou, o "
        "modelo responde a partir da memória com a confiança de quem pesquisou"
    )


def test_the_context_header_stops_offering_an_opt_out(tools_src=None):
    """`tools.py:17` dizia 'usa isto ... se for relevante'. Para uma pergunta
    obscura, 'relevante' é um juízo que o modelo falha — e falhou."""
    import tools as t

    src = _read(t.__file__)
    assert "se for relevante" not in src, (
        "o cabeçalho do contexto continua a dar permissão para o ignorar"
    )
    assert "fonte para esta resposta" in src


@pytest.mark.parametrize("archive", ARCHIVES)
def test_the_searxng_config_still_carries_wikipedia(archive):
    """Wikipedia continua a ser procurável — dentro do SearXNG, que é onde o
    dono a queria. Este teste existe para que ninguém 'conserve a
    funcionalidade' reintroduzindo a porta de serviço."""
    cfg = _read(os.path.join(os.path.dirname(__file__), "..", "searxng-settings.yml"))
    assert "engine: wikipedia" in cfg, (
        "wikipedia saiu do SearXNG: a busca perde o que o dono pediu para manter"
    )
