"""The web search is an intake, not an output.

Measured on production, 2026-10-03. Asked "Olá", the assistant answered:

    "O termo "Olá" tem várias definições no contexto da língua portuguesa. De
     acordo com o Dicionário Infopédia da Língua Portuguesa, um dos significados
     de "olá" é uma expressão utilizada para saudar alguém... Por exemplo, no site
     booking.com encontra-se um apartamento chamado "Apartament Ola" localizado em
     Svinoústí. No entanto, é importante notar que a origem do termo não está
     claramente definida nas fontes consultadas."

A search engine's answer, handed to the owner verbatim. The context that
produced it, measured on the same turn:

    rag=0   graph=80   web=517

517 characters of dictionary against 80 of personality. Two causes, and only
one of them was the search.

1. The prompt told the model to cite. The block said "responde a partir dele e
   diz de onde veio", and four lines later "não nomeies o mecanismo". Two
   opposite instructions; the model followed the first, because "diz de onde
   veio" is an instruction and the other is a prohibition. Hence "de acordo com
   o Dicionário Infopédia".

2. There was also a version that skipped the search for a greeting. The owner
   rejected it -- "deve pesquisar mesmo sem pergunta, tem é de digerir com a
   persona e o rag no brain". The search always runs. These tests pin that too,
   because the gate looked obviously right and was wrong.
"""

from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ASSISTANT = os.path.join(ROOT, "assistant.py")


def _source() -> str:
    with open(ASSISTANT, encoding="utf-8") as fh:
        return fh.read()


def _prompt_text() -> str:
    """Every string literal inside the prompt-building branch, joined.

    Read from the AST rather than from the source text, because the guarantee
    is about what the MODEL RECEIVES. Grepping the source cannot tell a prompt
    from the comment explaining why the prompt changed -- and the comment that
    documents this exact fix quotes the offending phrase, so a source-level
    grep fails on the fix's own explanation.
    """
    src = _source()
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
    raise AssertionError("_respond_with_llm_body with the web block was not found")


def _search_block() -> str:
    """The one literal that instructs the model how to answer with results."""
    text = _prompt_text()
    marker = "### COMO RESPONDER COM A PESQUISA"
    idx = text.find(marker)
    assert idx > 0, "the branch that had web context has no instruction block"
    end = text.find("###", idx + len(marker))
    return text[idx:end] if end > 0 else text[idx:]


def _fold(text: str) -> str:
    return (
        text.lower()
        .replace("ã", "a")
        .replace("á", "a")
        .replace("é", "e")
        .replace("ó", "o")
        .replace("õ", "o")
        .replace("í", "i")
        .replace("ú", "u")
        .replace("ç", "c")
    )


# --- the search always runs ------------------------------------------------

def _web_assignment() -> ast.AST:
    """The AST node whose value holds the search call.

    Returned so the gate can be checked structurally. Grepping for identifiers
    like `_is_smalltalk` proves nothing: a gate can be written
    `... if "olá" not in text else ""` and use no name this test knows about.
    That is not hypothetical -- it is how the first falsification of this test
    slipped through, and it is why the check is on the shape of the expression.
    """
    tree = ast.parse(_source())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_respond_with_llm_body"):
            continue
        for sub in ast.walk(node):
            targets = []
            if isinstance(sub, ast.Assign):
                targets = [t.id for t in sub.targets if isinstance(t, ast.Name)]
            elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                targets = [sub.target.id]
            if "web" in targets and any(
                isinstance(n, ast.Call)
                and getattr(n.func, "attr", getattr(n.func, "id", "")) == "search_with_searxng"
                for n in ast.walk(sub.value)
            ):
                return sub.value
    raise AssertionError("no assignment to `web` holds the search call")


def test_the_search_is_not_gated():
    """A greeting is still searched. Do not "fix" this by skipping it.

    An earlier version skipped the search when every word of the message was
    small talk -- "Olá", "bom dia", "obrigado" -- on the reasoning that a
    greeting has no referent and so nothing to look up. Measured: "Olá" returned
    Infopedia's definition and the model recited it, so the gate looked right.

    The owner rejected it: "deve pesquisar mesmo sem pergunta, tem é de digerir
    com a persona e o rag no brain". The search is how the brain is fed, on every
    turn, including the ones with no question in them. The fix is to stop
    reciting the result, not to stop fetching it.

    Checked on the shape of the expression, not on names. A gate needs a
    condition, and a condition cannot hide inside a plain call.
    """
    value = _web_assignment()

    assert not isinstance(value, ast.IfExp), (
        "a pesquisa voltou a ser condicional (`... if <cond> else ...`). Ela é "
        "incondicional por decisão: é a entrada que a persona e o RAG digerem."
    )
    # And no `if` anywhere in the branch may skip it.
    fn = next(
        n
        for n in ast.walk(ast.parse(_source()))
        if isinstance(n, ast.FunctionDef) and n.name == "_respond_with_llm_body"
    )
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            names = {
                getattr(n, "attr", getattr(n, "id", ""))
                for n in ast.walk(node.test)
                if isinstance(n, (ast.Attribute, ast.Name))
            }
            assert not (names & {"_is_smalltalk", "_SMALLTALK_WORDS"}), (
                f"a pesquisa está dentro de um if que depende de {names}. "
                f"Não deve haver condição."
            )

    for pattern in ("_is_smalltalk", "_SMALLTALK", "searched="):
        assert pattern not in _source(), (
            f"{pattern!r} is back in assistant.py. The search is unconditional by "
            f"decision: it is the intake the persona and the RAG digest."
        )


# --- and what comes out is not the search engine's answer ------------------

def test_the_prompt_does_not_ask_for_sources():
    """"diz de onde veio" is the line that produced the dictionary entry."""
    body = _fold(_prompt_text())
    assert "diz de onde veio" not in body, (
        "a instrução para citar fontes voltou. Ela mandou o modelo responder "
        "como um motor de busca: 'de acordo com o Dicionário Infopédia', com a "
        "lista de páginas e o apartamento em Svinoústí.\n"
        "Comparado sem distinção de maiúsculas: a frase voltou escrita como "
        "\"Diz de onde veio\" no fim de uma linha, e uma comparação "
        "case-sensitive não a via."
    )


def test_the_prompt_forbids_reciting_the_results():
    """Not merely "don't cite" -- forbid the shape of the answer we got.

    Asserted on the prohibitions being present, so a rewrite that drops them
    fails. The owner's words: the answer the search gave "deve ser lida apenas
    por ele nunca atirada para o utilizador".
    """
    block = _search_block()
    folded = _fold(block)
    # The citation vocabulary, named so the model cannot reach for it.
    for forbidden in ("de acordo com", "segundo", "fontes", "urls"):
        assert forbidden in folded, (
            f"a proibição de {forbidden!r} saiu do bloco de pesquisa. Sem ela o "
            f"modelo volta a responder como um motor de busca.\n{block}"
        )
    # And the shape of the answer, which is what actually reached the owner:
    # a dictionary entry, plus a list of sites.
    assert "dicion" in folded, (
        f"o bloco já não proíbe a definição de dicionário, que foi o que "
        f"chegou ao dono.\n{block}"
    )
    assert "nunca" in folded or "jamais" in folded, (
        f"o bloco perdeu a proibição explícita de reproduzir.\n{block}"
    )


def test_digestion_is_stated_as_the_instruction():
    """The replacement for "diz de onde veio" has to actually say what to do."""
    block = _search_block()
    folded = _fold(block)
    assert "diger" in folded, (
        f"o bloco não manda digerir. Sem isso a instrução é só proibições e o "
        f"modelo fica sem saber o que fazer com 517 chars de resultados.\n{block}"
    )
    assert "persona" in folded, (
        f"a digestão não menciona a persona, que é o que deve processar o "
        f"material.\n{block}"
    )


def test_the_priority_block_says_intake_not_output():
    folded = _fold(_prompt_text())
    assert "entrada" in folded and "saida" in folded, (
        "a ordem de prioridades tem de dizer que a pesquisa entra e não sai. "
        "Era a diferença entre um brain que digere e um browser que transcreve."
    )
