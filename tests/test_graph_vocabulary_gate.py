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

import json
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
    # PRODUCTION_SPEECH[0], verbatim. It used to be a hand-trimmed version of
    # it with the trailing ellipsis removed, and it passed for the wrong
    # reason: the "!" clause caught it. Narrowing that clause in T061 exposed
    # that the sample was an invention. The real string is what the gate has to
    # catch, so the real string is the fixture.
    keys = index_memory({"tags": [PRODUCTION_SPEECH[0]]})
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
    mermaid = "graph TD;\n" + PRODUCTION_SPEECH[0] + "-->gato"
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


# --------------------------------------------------------------------------
# T061 -- what the review of T060 found, and the fix.
#
# The blocking finding was that the gate sat at ONE writer. `index_memory` was
# gated; `materialize_memories` was not, and that is the writer that produced
# 179 of the 190 nodes in production. The tests below drive both writers with
# the SAME payload, because the bug was precisely that only one of them
# answered.
# --------------------------------------------------------------------------
from src.brain.memory_graph import materialize_memories  # noqa: E402


def _memories_db(tmp_path, monkeypatch):
    """A brain.db with the `memories` table materialize_memories reads from."""
    path = tmp_path / "brain.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    from src.brain import memory_graph as mg

    mg.init_db()
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, timestamp TEXT, text TEXT)"
    )
    con.commit()
    con.close()
    return path


def _add_memory(path, payload):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO memories (id, timestamp, text) VALUES (1, '2026-01-01', ?)",
        (payload,),
    )
    con.commit()
    con.close()


def _everything(path):
    con = sqlite3.connect(path)
    rows = con.execute(
        "SELECT node_type, label FROM memory_graph WHERE node_type IN ('node','edge')"
    ).fetchall()
    con.close()
    return rows


PAYLOAD = json.dumps(
    {"summary": "x", "tags": ["pt-BR", "Bom, parece que há um mistério aqui...", "Leite"],
     "facts": []}
)


def test_materialize_memories_is_gated_too(tmp_path, monkeypatch):
    """THE BLOCKER. Before the fix this wrote three nodes and three edges --
    the speech and the language marker included -- and it is the writer behind
    179 of 190 production nodes."""
    path = _memories_db(tmp_path, monkeypatch)
    _add_memory(path, PAYLOAD)
    materialize_memories(only_unparsed=True)
    labels = [r[1] for r in _everything(path)]
    assert "Bom, parece que há um mistério aqui..." not in labels, labels
    assert "pt-BR" not in labels, labels
    assert labels == ["Leite"], f"o escritor deixou de escrever o conceito bom: {labels}"


def _bare(path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(path))
    from src.brain import memory_graph as mg

    mg.init_db()
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, timestamp TEXT, text TEXT)"
    )
    con.commit()
    con.close()
    return path


def test_both_writers_answer_the_same_payload(tmp_path, monkeypatch):
    """The invariant behind the fix: the two writers agree.

    A gate at one call site is a gate that the other call site walks past.
    """
    a = _bare(tmp_path / "a.db", monkeypatch)
    b = _bare(tmp_path / "b.db", monkeypatch)
    _add_memory(a, PAYLOAD)
    _add_memory(b, PAYLOAD)
    index_memory(json.loads(PAYLOAD))
    materialize_memories(only_unparsed=True)
    monkeypatch.setattr(config, "DB_PATH", str(a))
    gated = {r[1] for r in _everything(a)}
    monkeypatch.setattr(config, "DB_PATH", str(b))
    gated_b = {r[1] for r in _everything(b)}
    speech = "Bom, parece que há um mistério aqui..."
    assert speech not in gated and speech not in gated_b


# --------------------------------------------------------------------------
# The greeting rule, narrowed. These are the concepts it was eating.
# --------------------------------------------------------------------------
CONCEPTS_THE_GREETING_RULE_ATE = [
    "Bom dia a todos",
    "Bom uso da água",
    "well being of society",
    "So it goes",
    "Hello Kitty Merchandise",
    "Ok Corral Vermelho",
    "A vida tem sentido?",
    "Bom dia",
]


@pytest.mark.parametrize("label", CONCEPTS_THE_GREETING_RULE_ATE)
def test_the_greeting_rule_no_longer_eats_a_concept(label):
    """T061. Each of these was rejected before: the rule fired on an opener
    plus a word count, which is a word-count rule wearing a disguise -- and the
    ticket had rejected a word-count rule the day before for exactly this."""
    assert not is_speech_label(label), f"o portão comeu um conceito: {label!r}"


@pytest.mark.parametrize("label", PRODUCTION_SPEECH)
def test_narrowing_the_rule_did_not_lose_a_production_case(label):
    """Every production sample has to still be caught after the narrowing."""
    assert is_speech_label(label), f"o estreitamento perdeu uma caso real: {label!r}"


def test_the_tag_the_only_prompt_emits_is_rejected():
    """`skills/skill_memory.py:43` dictates `"tags": ["Tag"]` and nothing else,
    and `node:tag` is the current topic in production. Prompt boilerplate, not
    a concept."""
    assert is_language_tag("Tag")


# --------------------------------------------------------------------------
# What the label/preview chain now guarantees.
# --------------------------------------------------------------------------
def test_a_long_fact_is_marked_as_truncated():
    """The ellipsis branch in `_memory_label` was unreachable, because
    `_informative_text` had already truncated. A long fact was cut mid-word
    with no marker -- the worst of both, and it was the new source of labels."""
    label = _memory_label(parse_memory('{"tags": [], "facts": ["%s"]}' % ("x" * 100)), 7)
    assert label.endswith("…"), label
    assert len(label) == 61, len(label)


def test_preview_is_empty_rather_than_raw_json():
    """Pinned because it is a choice, not an accident. A memory whose only
    content is a language marker has no text; showing the JSON head would put
    `{"tags": ["pt"], "facts": []}` in front of the owner."""
    assert parse_memory('{"tags": ["pt"], "facts": []}')["preview"] == ""


def test_preview_is_the_fact_when_there_is_one():
    assert parse_memory('{"tags": ["pt"], "facts": ["um facto"]}')["preview"] == "um facto"


def test_the_known_gap_is_named_not_hidden():
    """What the narrowed gate does NOT catch, stated so it cannot be forgotten.

    Every one of the five production utterances ends in an ellipsis, or opens
    with a greeting and a comma. An utterance with neither passes: the reviewer
    asked for this to be either fixed or declared, and declaring it is the
    honest option while the cost of a false positive -- losing a real concept
    out of the owner's only graph -- is the higher one.
    """
    for lost in ("Não estou muito à vontade hoje.",
                 "Quer dizer, não sei bem",
                 "ele disse que sim e depois foi embora"):
        assert not is_speech_label(lost), (
            f"isto devia ser apanhado e o gate nao apanha: {lost!r} -- se a "
            f"clause foi reintroduzida, apaga este teste"
        )
