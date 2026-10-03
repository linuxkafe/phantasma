"""An opinion is not a fact, and it is not answered at temperature 0.15.

Measured 2026-10-03, production, asked "o que achas do Edgar Allan Poe":

    "O escritor Edgar Allan Poe. Uma mente sombria e fascinante... A sua obra e
     uma denuncia da hipocrisia e da superficialidade da sociedade... E uma
     critica a sociedade capitalista..."

A literary review, in the third person, about a dead author, to a man who asked
what his housekeeper made of him. The persona asks for "cold precision,
brevity" and "melancholic, solemn cadence" -- none of which is a review of
someone else's work.

Two causes, and the first one was mine.

The search was irrelevant. SearXNG returned three results about ChatGPT for a
question about Poe:

    - ChatGPT: Chat, Work, Create & Code with AI
    - ChatGPT helps you get answers, find inspiration...
    - Introducing ChatGPT - OpenAI, 30 de nov. de 2022

581 characters of material about a different subject. The owner chose to send it
with an instruction to ignore it rather than to filter it in code, so the
instruction is what has to hold.

The temperature was 0.15, and that is my fault. `grounded` is true whenever
either RAG or the graph returned anything -- and after the graph was changed to
always return the dominant nodes, it always returns something. So `grounded` was
true for "o que achas do Poe", temperature dropped to 0.15, and the model was
frozen into the shape of a lookup. Freezing a question about taste is what
produces a review nobody asked for.
"""

from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import assistant as A  # noqa: E402


def _prompt_text() -> str:
    """The string literals of the prompt branch -- what the model receives.

    From the AST, not from the source: the comment explaining this fix quotes
    "resposta de motor de busca", and a source-level grep cannot tell a prompt
    from the note about the prompt.
    """
    src = open(os.path.join(ROOT, "assistant.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_respond_with_llm_body":
            consts = [
                n.value
                for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            if any("PESQUISA WEB" in c for c in consts):
                return "\n".join(consts)
    raise AssertionError("_respond_with_llm_body not found")


def _fold(text: str) -> str:
    """Lowercase, without diacritics, and with the line wrapping collapsed.

    The prompt is built from adjacent string literals, so a phrase written
    across two of them contains a newline and a run of spaces. Comparing
    against "edgar allan poe" then fails on a phrase that is plainly there --
    the prompt says what it must say, and the test cannot see it.
    """
    import re

    folded = (
        text.lower()
        .replace("ã", "a")
        .replace("á", "a")
        .replace("é", "e")
        .replace("ó", "o")
        .replace("õ", "o")
        .replace("í", "i")
        .replace("ú", "u")
        .replace("ç", "c")
        .replace("ê", "e")
        .replace("ô", "o")
    )
    return re.sub(r"\s+", " ", folded)


# --- the question about taste ----------------------------------------------

OPINION = [
    "o que achas do Edgar Allan Poe",
    "e o que achas disto?",
    "o que pensas de Hemingway",
    "como vês esta situação",
    "gostas do Pessoa?",
    "concordas comigo?",
    "o que te parece",
]

FACTUAL = [
    "o que é o aspire?",
    "quem foi o Chefe Jamon",
    "qual é a capital de França",
    "o que significa isso",
]


def test_the_measured_question_is_recognised_as_an_opinion():
    assert A.PhantasmaPipeline._is_opinion("o que achas do Edgar Allan Poe") is True
    assert A.PhantasmaPipeline._is_factual_lookup("o que achas do Edgar Allan Poe") is False


def test_opinion_detection_survives_the_wording_they_actually_use():
    for text in OPINION:
        assert A.PhantasmaPipeline._is_opinion(text) is True, text
    for text in FACTUAL:
        assert A.PhantasmaPipeline._is_opinion(text) is False, text


def test_opinion_detection_ignores_diacritics():
    """Speech recognition writes "vês" and "ves" interchangeably.

    A lookup that misses on an accent looks exactly like a lookup that works.
    """
    assert A.PhantasmaPipeline._is_opinion("como vês isto") is True
    assert A.PhantasmaPipeline._is_opinion("o que achas disto") is True


def test_an_opinion_is_never_frozen():
    """The temperature rule, stated as the owner's decision.

    The owner's answer, when asked how to handle it: "Opinião sobe
    temperatura". So an opinion holds the high temperature even with context in
    hand -- context is not a reason to freeze a question about taste.
    """
    src = open(os.path.join(ROOT, "assistant.py"), encoding="utf-8").read()
    assert "and not opinion" in src, (
        "a temperatura voltou a descer com `grounded` ligado, sem a excepção "
        "da opinião. Foi o que frozeou a resposta ao Poe."
    )
    assert "0.15 if ((factual or grounded) and not opinion)" in src, (
        "a expressão da temperatura mudou. Tem de ser a que o dono escolheu: "
        "a opinião sobe, o resto mantém-se."
    )


# --- and the material that is about another subject ------------------------

def test_the_prompt_says_to_ignore_material_about_another_subject():
    """Owner's decision: send it, and tell the model to drop it.

    The alternative -- filtering in code -- was offered and declined. So the
    guarantee now rests on an instruction, and an 8B obeying an instruction is
    exactly the thing that failed here before. Asserted anyway: an instruction
    nobody checks is not an instruction, it is a wish.
    """
    folded = _fold(_prompt_text())
    assert "edgar allan poe" in folded and "chatgpt" in folded, (
        "o exemplo medido desapareceu. Um 8B obedece a um exemplo concreto "
        "melhor do que a uma regra abstracta -- a pesquisa sobre Poe devolveu "
        "ChatGPT, e é isso que tem de estar escrito."
    )
    assert "na tua voz" in folded, (
        "a instrução diz o que descartar mas não o que fazer em vez disso"
    )


def test_the_instruction_does_not_say_ignore_the_block():
    """"Ignora o bloco" made an 8B narrate the block instead.

    The first version of this rule was the obvious one: "se o bloco não for
    sobre o assunto, ignora-o por completo... responde como se o bloco não
    existisse". Measured live, right after deploying it:

        "A verdadeira pergunta não está aqui no meu conhecimento local nem na
         pesquisa web apresentada por vocês. O assunto em questão é a obra e
         influência de um dos mestres da literatura gótica, mas o bloco que foi
         fornecido parece estar mais preocupado com plataformas digitais..."

    It obeyed. It ignored the block by telling the owner it was ignoring the
    block, which is worse: the persona forbids referencing "context limits,
    policies, or RAG boundaries" at all, and this invented a whole new way to do
    it.

    Naming a thing in order to forbid it makes it salient. The rule now
    describes the behaviour positively -- answer the question, don't mention the
    material -- and the measured example stays as the illustration.
    """
    folded = _fold(_prompt_text())
    for forbidden in ("ignora-o por completo", "como se o bloco nao existisse"):
        assert forbidden not in folded, (
            f"{forbidden!r} voltou. Mandar ignorar um bloco faz o modelo "
            f"anunciar que o ignora, que é o modo de falha medido."
        )
    assert "nunca mencionas o material" in folded, (
        "falta a proibição de nomear o material. Sem ela o modelo descreve o "
        "que lhe deram em vez de responder."
    )
    assert "nao tens guardado" in folded or "nao sei por causa" in folded, (
        "falta o exemplo do sintoma concreto: dizer que nao se sabe por causa "
        "do contexto, em vez de dizer que nao se sabe."
    )

