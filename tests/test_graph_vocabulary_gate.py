"""O portão: o que entra no cérebro, e o que nunca devia ter entrado (T060).

Três defeitos, uma raiz. Não havia nenhuma verificação entre "o LLM devolveu
uma string" e "essa string passa a ser um conceito", e o 3D desenhava o
resultado. O dono viu-o em 2026-10-02: uma série de entradas com texto de
conversa, e nós com o rótulo `#pt`.

Estes testes são verificáveis contra o código antigo, e essa é a razão de
serem ficheiros separados e não mais linhas em test_memory_materialize.py: cada
``test_`` abaixo falha em ``HEAD`` e passa agora. Verificado com
``git stash`` do código de produção.

O que estes testes NÃO fazem, deliberadamente:

* Não limpam nada. A reparação dos dados já gravados é um ticket à parte com
  ``--dry-run``; este ficheiro só fecha a porta.
* Não medem o grafo inteiro. A medição do raio de blast está em
  ``aes/tickets/T060-portao-promoteabilidade-grafo.md`` e foi feita antes da
  regra existir, não depois.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from src.api.memory_graph import (  # noqa: E402
    _memory_label,
    build_graph,
    parse_memory,
)
from src.brain.memory_graph import (  # noqa: E402
    index_memory,
    is_language_tag,
    is_speech_label,
)

# --------------------------------------------------------------------------
# As frases que estão REALMENTE no grafo de produção. Cortadas do que o dono
# viu no ecrã, não inventadas: um teste com fala inventada passaria mesmo que a
# regra não apanhasse a fala verdadeira.
# --------------------------------------------------------------------------
PRODUCTION_SPEECH = [
    'Olá! O nome "Bimby" parece ser associado a gatos que têm nomes próprios. '
    "Se eu souber mais sobre isso... Ah sim!, lem...",
    "Olha, não estou muito à vontade hoje...",
    "Bom, parece que há um mistério aqui...",
    "Ah, a chuva...",
    "Olha, não sou uma pessoa que se apresenta com sorrisos nem gestões amigáveis.",
]

# Conceptos que o gate NÃO pode tocar. O último é o caso que matou a regra
# "mais de N palavras" durante a análise: é uma conclusão de pesquisa com 8
# palavras, e um gate que a apanhasse estava a apagar trabalho do dono.
PRODUCTION_CONCEPTS = [
    "brechas e fissuras na lógica das plataformas digitais",
    "composição nutricional",
    "Capitalismo",
    "gato",
    "Bom dia",          # greeting alone is not speech
    "A saúde pública",  # a bare article opener is not a greeting
    "The Body",
    "Deus",
    "precision agriculture",
]


# --------------------------------------------------------------------------
# A regra
# --------------------------------------------------------------------------
@pytest.mark.parametrize("label", PRODUCTION_SPEECH)
def test_speech_is_not_a_concept(label):
    assert is_speech_label(label), f"a fala entrou como conceito: {label!r}"


@pytest.mark.parametrize("label", PRODUCTION_CONCEPTS)
def test_a_concept_is_not_speech(label):
    assert not is_speech_label(label), (
        f"o gate apagaria um conceito real: {label!r}"
    )


@pytest.mark.parametrize("tag", ["pt", "Dev", "aoe", "EN", "pt-BR"])
def test_language_markers_are_metadata_not_concepts(tag):
    assert is_language_tag(tag), f"{tag!r} é um marcador de idioma, não um conceito"


@pytest.mark.parametrize("tag", ["gato", "Bimby", "veganismo", "Portugal", "casa"])
def test_a_real_tag_is_not_treated_as_metadata(tag):
    assert not is_language_tag(tag)


# --------------------------------------------------------------------------
# A porta das tags
# --------------------------------------------------------------------------
@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "brain.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    from src.brain import memory_graph as mg

    mg.init_db()
    return path


def _labels(path):
    con = sqlite3.connect(path)
    out = [r[0] for r in con.execute(
        "SELECT label FROM memory_graph WHERE node_type='node'"
    )]
    con.close()
    return out


def test_a_speech_tag_writes_no_node(db):
    keys = index_memory({"tags": ['Olá! O nome "Bimby" parece ser associado a gatos']})
    assert keys == [], f"a fala escreveu nós: {keys}"
    assert _labels(db) == [], f"a fala chegou à base: {_labels(db)}"


def test_a_language_tag_writes_no_node(db):
    keys = index_memory({"tags": ["pt", "Dev"]})
    assert keys == [], f"um marcador de idioma escreveu nós: {keys}"
    assert _labels(db) == []


def test_a_real_tag_still_writes(db):
    """The gate is narrow on purpose; this is the test that says so."""
    keys = index_memory({"tags": ["veganismo"]})
    assert keys == ["node:veganismo"], keys
    assert _labels(db) == ["veganismo"]


def test_a_rejected_tag_never_becomes_the_current_topic(db):
    """The one that made `topic_state` read `node:tag` in production.

    `apply_reward` credits the current topic on a reaction that carries no
    text, so a rejected tag landing here means a 👍 reinforces nothing at all --
    and the FlyBrain's ambient orientation points at whatever was rejected last.
    """
    from src.brain.memory_graph import get_current_topic

    index_memory({"tags": ["pt"]})
    assert get_current_topic() is None, (
        f"a tag rejeitada tornou-se o topico corrente: {get_current_topic()!r}"
    )


# --------------------------------------------------------------------------
# A porta do mermaid
# --------------------------------------------------------------------------
def test_a_speech_edge_writes_nothing(db):
    mermaid = 'graph TD;\nOlá! O nome "Bimby" parece ser associado a gatos-->gato'
    keys = index_memory({"mermaid": mermaid})
    assert keys == [], f"a aresta de fala escreveu: {keys}"


def test_a_speech_edge_does_not_drag_its_neighbour_along(db):
    """One endpoint is speech, the other is a real concept. Neither is written.

    Half-writing the pair is worse than writing nothing: it leaves an edge with
    one end missing, which is exactly what the reconciler spends every night
    trying to repair.
    """
    mermaid = 'graph TD;\nOlha, não estou muito à vontade hoje-->gato'
    assert index_memory({"mermaid": mermaid}) == []
    assert _labels(db) == []


def test_a_real_edge_still_writes(db):
    keys = index_memory({"mermaid": "graph TD;\nLeite-->Pecuária"})
    assert len(keys) == 1, keys
    con = sqlite3.connect(db)
    edges = con.execute(
        "SELECT node_type, source, target FROM memory_graph WHERE node_type='edge'"
    ).fetchall()
    con.close()
    assert edges, "a aresta real deixou de ser escrita"


# --------------------------------------------------------------------------
# O ecrã: `#pt` e `tag:pt`
# --------------------------------------------------------------------------
def test_a_memory_label_is_never_just_a_hash_tag():
    """Three memories tagged `pt` drew three nodes reading `#pt`.

    Production has them at mem:19, mem:20 and mem:21. A label has to identify
    the memory; a tag is a category, and the category is already rendered next
    to the node in the explorer.
    """
    for payload in (
        '{"tags": ["pt"], "facts": ["uma tese e uma promessa de contribuicao"]}',
        '{"tags": ["pt"], "facts": []}',
        '{"tags": ["Dev"], "facts": []}',
    ):
        label = _memory_label(parse_memory(payload), 19)
        assert not label.startswith("#"), f"o rotulo voltou a ser uma tag: {label!r}"


def test_three_memories_tagged_pt_no_longer_look_identical():
    labels = {
        _memory_label(parse_memory('{"tags": ["pt"], "facts": []}'), mem_id)
        for mem_id in (19, 20, 21)
    }
    assert len(labels) == 3, f"três memórias continuam idênticas no ecrã: {labels}"


def test_a_fact_wins_over_a_tag():
    p = parse_memory('{"tags": ["pt"], "facts": ["uma tese e uma promessa"]}')
    assert _memory_label(p, 19) == "uma tese e uma promessa"


def test_a_label_is_never_raw_json():
    """The bug this change introduced and then had to fix.

    Making the label fall back to `preview` looked right and was not: for a
    parsed memory `preview` IS the raw JSON head, so a memory whose only content
    is `{"tags": ["pt"], "facts": []}` was labelled with that string.
    """
    p = parse_memory('{"tags": ["pt"], "facts": []}')
    label = _memory_label(p, 21)
    assert not label.startswith("{"), label
    assert label == "memória 21", label


def test_a_plain_text_memory_still_shows_its_text():
    p = parse_memory("nao e json nenhum, texto simples")
    assert _memory_label(p, 35) == "nao e json nenhum, texto simples"


def test_build_graph_draws_no_language_node():
    rows = [(1, "2026-01-01", '{"tags": ["pt", "veganismo"], "facts": []}')]
    payload = build_graph(rows)
    ids = {node["id"] for node in payload["nodes"]}
    assert "tag:pt" not in ids, f"a tag de idioma virou no no 3D: {sorted(ids)}"
    assert "tag:veganismo" in ids, "a tag real deixou de aparecer"
    links = {(edge["source"], edge["target"]) for edge in payload["links"]}
    assert ("mem:1", "tag:pt") not in links, "a ligação para a tag de idioma ficou"
    assert ("mem:1", "tag:veganismo") in links
