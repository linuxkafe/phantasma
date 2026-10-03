"""A brace inside a string is not a brace.

Production, 2026-10-03. After folding memory.db into brain.db, 9 of the 53
memories made `_render_memory` recurse until the stack ran out:

    RecursionError: maximum recursion depth exceeded

Reproduced minimally:

    _render_memory('{"tags": ["a {b} c"], "facts": ["d"]}')

The chunker split a joined blob by counting `{` and `}` characters, and counted
the ones inside string literals too. A `{` in a value pushed the depth up, `buf`
never emptied, and the chunk handed back still started with `{` and still held
more than one -- so it went back into the same function with the same argument,
forever.

This is the conversation path, not a batch job. `retrieve_from_rag` returns these
memories, `_render_memory` renders what the prompt shows, and one RAG hit was
enough to crash the response.

The ninth failure is a different shape -- `{"facts": [{"S": ...}]}`, a list of
objects rather than a list of strings -- and it is in here because a fix for the
first shape is not a fix for the second.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import assistant as A  # noqa: E402

render = A.PhantasmaPipeline._render_memory

CASES = {
    "brace inside a string value": '{"tags": ["a {b} c"], "facts": ["d"]}',
    "brace pair inside a string": '{"tags": ["x {y} z"], "facts": ["d"]}',
    "unclosed brace inside a string": '{"tags": ["a {b"], "facts": ["d"]}',
    "escaped quote then brace": '{"tags": ["a \\" {x}"], "facts": ["d"]}',
    "brace in a mermaid label": (
        '{"tags": ["m"], "mermaid": "graph TD;\\na [label=\\"a {b} c\\"] --> d"}'
    ),
    "list of objects": (
        '{"facts": [{"S": "Your", "P": "trusted source", "O": "for breakfast"}]}'
    ),
    "plain json": '{"tags": ["a","b"], "facts": ["c"]}',
    "not json at all": "texto simples sobreVF",
}


def test_no_case_recurse_forever():
    """The crash, stated as a property over every shape seen in production.

    A single regression limit is what makes this a failure rather than a hang:
    the bug was unbounded recursion, so without one the test suite itself would
    have needed the timeout to notice.
    """
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(300)
    try:
        for name, blob in CASES.items():
            try:
                render(blob)
            except RecursionError:
                raise AssertionError(
                    f"{name!r} entra em recursão infinita. Uma chave dentro de "
                    f"uma string não é uma chave: {blob!r}"
                ) from None
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(
                    f"{name!r} rebentou com {type(exc).__name__}: {exc}"
                ) from None
    finally:
        sys.setrecursionlimit(old)


def test_a_brace_inside_a_string_is_kept_in_the_output():
    """Not merely "does not crash" -- the content survives.

    The obvious wrong fix is to strip or escape braces, which renders the memory
    but loses the fact. A memory that says a node label contains `{` has to come
    out of the renderer with it.
    """
    out = render('{"tags": ["a {b} c"], "facts": ["d"]}')
    assert "{b}" in out, f"o conteúdo entre chaves desapareceu: {out!r}"
    assert "Conceitos:" in out, out


def test_two_documents_still_split():
    """The reason the chunker exists must keep working.

    `retrieve_from_rag` joins several memories with newlines, so the input is
    routinely several JSON documents. Fixing the brace count must not stop the
    split, or every multi-memory RAG hit collapses into one parsed blob and the
    rest is dropped as raw.
    """
    blob = '{"tags":["p {q}"],"f":["r"]}\n{"tags":["s {t}"],"f":["u"]}'
    out = render(blob)
    assert "p {q}" in out and "s {t}" in out, out
    assert out.startswith("- "), f"os documentos deixaram de ser separados: {out!r}"


def test_plain_prose_is_untouched():
    assert render("nem sempre a culpa é do sistema") == "nem sempre a culpa é do sistema"


def test_every_production_memory_renders():
    """Run against the real store when it is reachable, skip when it is not.

    Nine concrete memories crashed. Naming the shapes above stops the class of
    bug; this stops the next one that only exists in the data. A test that
    quietly passes because the database is missing is worse than no test, so the
    skip says which path it looked for.
    """
    import sqlite3

    db = "/opt/phantasma/data/brain.db"
    if not os.path.exists(db):
        import pytest

        pytest.skip(f"{db} is not readable from here")

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = [r[0] for r in con.execute("SELECT text FROM memories")]
    finally:
        con.close()

    old = sys.getrecursionlimit()
    sys.setrecursionlimit(300)
    try:
        broken = []
        for text in rows:
            try:
                render(text)
            except Exception as exc:  # noqa: BLE001
                broken.append((type(exc).__name__, text[:70]))
        assert not broken, f"{len(broken)} memórias de produção não renderizam: {broken}"
    finally:
        sys.setrecursionlimit(old)

def test_two_documents_survive_an_unbalanced_brace_in_a_string():
    """The case that separates a correct brace count from a lucky fallback.

    `_render_memory` falls back to `json.loads` when the chunker cannot split,
    and for a SINGLE well-formed document that fallback produces exactly the
    right answer -- which is why a wrong brace counter, plus a guard against
    re-entering on the same bytes, passes every test above.

    Two documents do not recover: the blob is not valid JSON as a whole, so the
    fallback returns it verbatim and the second memory is never rendered. The
    owner sees raw JSON in the middle of a reply.

    The unbalanced brace is inside a string value, where it is content rather
    than structure -- which is the whole point of counting braces outside
    strings.
    """
    blob = '{"tags":["a {b"],"f":["r"]}\n{"tags":["s"],"f":["u"]}'
    out = render(blob)

    assert "Conceitos:" in out, (
        f"o blob não foi dividido e caiu no fallback -- JSON cru no meio da "
        f"resposta:\n{out!r}"
    )
    assert out.startswith("- "), f"os dois documentos não foram separados: {out!r}"
    assert "a {b" in out, out
