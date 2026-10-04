"""A skill runs either way; only the DESTINATION of its output changes.

The owner's rule, 2026-10-04:

    "como está o tempo no Porto?"            -> weather answers, directly
    "o que achas de como está o tempo no Porto" -> weather runs, and the LLM
                                                  answers from that reading

Measured before this: both returned the same bare telemetry line, because
`respond_to_text` returned whatever a skill produced and never asked the model
anything. The second one is asking for a reading, and a line of numbers is the
bot declining to have an opinion.

The skill is NOT skipped in the opinion case. The reading is the freshest fact
in the prompt and it is the whole point of the question; dropping it and asking
the model to opine from memory would be worse than the bug.
"""

from unittest.mock import patch

import pytest

from assistant import PhantasmaPipeline

WEATHER = "Hoje no Porto: estado incerto, entre 17° e 27°."


@pytest.fixture
def pipeline():
    """A pipeline with only the two collaborators under test replaced."""
    with patch.object(PhantasmaPipeline, "__init__", lambda self: None):
        p = PhantasmaPipeline()
    p._skill_result = WEATHER
    return p


def _skill_runs(pipeline, text):
    """Execute the skill branch, recording what the pipeline did with it."""
    seen = {"skill": None, "llm": None, "skill_data": "NOT-CALLED"}

    def fake_skill(t):
        seen["skill"] = t
        return pipeline._skill_result

    def fake_llm(t, skill_data=None):
        seen["llm"] = t
        seen["skill_data"] = skill_data
        return "resposta do modelo"

    pipeline._execute_with_paused_shared_audio = fake_skill
    pipeline._respond_with_llm = fake_llm
    return pipeline.respond_to_text(text), seen


def test_a_plain_lookup_is_answered_by_the_skill(pipeline):
    """The lookup case is unchanged: skill output IS the answer."""
    answer, seen = _skill_runs(pipeline, "como está o tempo no Porto?")
    assert seen["skill"] == "como está o tempo no Porto?", (
        "a skill tem de correr -- e a responder a pergunta, não aoervative"
    )
    assert answer == WEATHER, (
        "numa pergunta directa o valor medido é a resposta; passá-lo pelo "
        "modelo só tornaria a Casa mais lenta e menos fiável"
    )
    assert seen["llm"] is None, "o modelo não deve ser chamado num lookup directo"


def test_asking_what_we_think_sends_the_reading_to_the_model(pipeline):
    answer, seen = _skill_runs(pipeline, "o que achas de como está o tempo no Porto?")
    assert seen["skill"] is not None, (
        "a skill tem de correr primeiro: a leitura é o facto mais fresco que "
        "existe para esta pergunta"
    )
    assert seen["llm"] == "o que achas de como está o tempo no Porto?"
    assert seen["skill_data"] == WEATHER, (
        "o modelo tem de receber a leitura, não opinar de memória"
    )
    assert answer == "resposta do modelo"
    assert answer != WEATHER, (
        "a leitura crua como resposta final é exactamente o defeito medido"
    )


def test_a_command_still_answers_directly(pipeline):
    """Commands must not be turned into prose. 'acender a luz' is not a question."""
    answer, seen = _skill_runs(pipeline, "acende a luz da sala")
    assert answer == WEATHER
    assert seen["llm"] is None, (
        "um comando tem de ser confirmado, nãoRAINT discutido"
    )


def test_no_skill_output_means_no_skill_context(pipeline):
    """Nothing matched: the model is called with no fabricated reading."""
    pipeline._skill_result = None
    answer, seen = _skill_runs(pipeline, "o que achas do Edgar Allan Poe?")
    assert seen["llm"] == "o que achas do Edgar Allan Poe?"
    assert seen["skill_data"] is None, (
        "sem skill não há leitura para injectar; passar uma seria inventar"
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("o que achas de como está o tempo no Porto?", True),
        ("o que pensas do tempo?", True),
        ("como vês esta previsão?", True),
        ("o que te parece do tempo no Porto?", True),
        ("como está o tempo no Porto?", False),
        ("acende a luz", False),
        ("", False),
    ],
)
def test_the_opinion_test_decides_the_route(text, expected):
    """The discriminator is already `_is_opinion`; it must not drift."""
    assert PhantasmaPipeline._is_opinion(text) is expected


def test_the_reading_reaches_the_prompt_text_and_the_llm_is_actually_called(monkeypatch):
    """End to end through prompt assembly: the reading must be IN the prompt.

    The routing tests above prove the value is passed between two functions.
    This one proves it survives prompt assembly and reaches Ollama, because
    that is the step where a parameter can be accepted, threaded along, and
    quietly dropped.
    """
    captured = {}

    def fake_search(_q):
        return ""

    class _Resp:
        @staticmethod
        def chat(*args, **kwargs):
            # **kwargs on purpose: a double that only accepts the keywords this
            # one test happens to use raises TypeError on any other call site
            # and the pipeline reports it as "all Ollama hosts failed", which
            # looks like an outage and is really a test that was too strict.
            messages = kwargs.get("messages") or []
            # messages[0] is the PERSONA system turn; the assembled prompt with
            # every context block is the USER turn. Reading index 0 here made
            # the test assert against the persona and conclude that the reading
            # had been dropped, when it had arrived perfectly well.
            captured["persona"] = messages[0]["content"]
            captured["prompt"] = messages[1]["content"]
            captured["model"] = kwargs.get("model")
            return {
                "message": {
                    "content": "Está instável, se queres a minha leitura."
                }
            }

    class _Client(_Resp):
        """The pipeline calls `chat` on THIS object, not on a separate response.

        Splitting them made the double raise AttributeError, which the pipeline
        reports as "Ollama <host> failed" and then as "all hosts failed" -- the
        same shape as a real outage, caused by a test.
        """

        def __init__(self, host=None, timeout=None):
            captured["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *_e):
            return False

    monkeypatch.setattr("assistant.search_with_searxng", fake_search)
    monkeypatch.setattr("assistant.retrieve_from_rag", lambda _t: None)
    monkeypatch.setattr("assistant.get_cached_response", lambda _t: None)
    # `ollama` is imported inside the function, so the module object itself
    # is the only thing that can be patched.
    monkeypatch.setattr("ollama.Client", _Client)

    class _Brain:
        def step(self, **kwargs):
            captured["step"] = kwargs

    p = PhantasmaPipeline.__new__(PhantasmaPipeline)
    p._fly_brain = _Brain()
    p._web_derived = False
    p._skill_loader = None

    answer = p._respond_with_llm_body(
        "o que achas de como está o tempo no Porto?", skill_data=WEATHER
    )

    assert WEATHER in captured["prompt"], (
        "a leitura não chegou ao prompt -- o parâmetro foi aceite e perdido"
    )
    assert "LEITURA DOS DISPOSITIVOS" in captured["prompt"]
    assert answer == "Está instável, se queres a minha leitura."
    assert captured["model"], "a inferência tem de usar o modelo resolvido"
    assert "LEITURA DOS DISPOSITIVOS" in captured["prompt"]
    # The reading must not displace the persona: persona leads, per the order
    # the owner set.
    #
    # Asserted as a shape, not as a string. This used to check for "ETHICAL
    # CORE", a header of config.SYSTEM_PROMPT -- and it passed in dev and failed
    # in prod, because prod's persona is not config.SYSTEM_PROMPT. It is the
    # 2748-character "### IDENTITY & TONE" text the owner curated on
    # /admin/config and which lives in app_settings, i.e. host data this deploy
    # does not carry. The test was asserting on the host's persona, which the
    # host is allowed to change, and it only looked green because dev had never
    # been asked.
    #
    # What the code actually owes is: the persona is the system turn, it is not
    # empty, it is not the assembled prompt, and the reading did not leak into
    # it. Those four hold for any persona.
    assert captured["persona"].strip(), "o turno de sistema chegou vazio"
    assert captured["persona"] != captured["prompt"], (
        "a persona e o prompt são o mesmo texto -- a montagem duplicou um turno"
    )
    assert WEATHER not in captured["persona"], (
        "a leitura foi parar à persona em vez do turno de utilizador"
    )


def test_a_lookup_does_not_invent_a_reading_block(monkeypatch):
    """Without a skill there is no reading block: no empty scaffolding."""
    captured = {}

    def fake_search(_q):
        return ""

    class _Resp:
        @staticmethod
        def chat(*args, **kwargs):
            messages = kwargs.get("messages") or []
            captured["prompt"] = messages[1]["content"]
            return {"message": {"content": "ok"}}

    class _Client(_Resp):
        def __init__(self, host=None, timeout=None):
            pass

    monkeypatch.setattr("assistant.search_with_searxng", fake_search)
    monkeypatch.setattr("assistant.retrieve_from_rag", lambda _t: None)
    monkeypatch.setattr("assistant.get_cached_response", lambda _t: None)
    # `ollama` is imported inside the function, so the module object itself
    # is the only thing that can be patched.
    monkeypatch.setattr("ollama.Client", _Client)

    class _Brain:
        def step(self, **kwargs):
            captured["step"] = kwargs

    p = PhantasmaPipeline.__new__(PhantasmaPipeline)
    p._fly_brain = _Brain()
    p._web_derived = False
    p._skill_loader = None

    p._respond_with_llm_body("o que achas do Edgar Allan Poe?")
    assert "LEITURA DOS DISPOSITIVOS" not in captured["prompt"], (
        "um cabeçalho de leitura vazio é o mesmo defeito que o bloco de "
        "conhecimento local vazio causava: o modelo passa a narrar o andaime"
    )


# --- the search subject, added 2026-10-04 after the first live run -----------
#
# "o que achas de como está o tempo em Lisboa?" was sent to SearxNG whole and
# came back with a Spanish thread about the phrase "tal y como esta" and
# Infopedia's dictionary entry for the word "achas". The engine searched for the
# wrapper because the wrapper was in the query. The reading already answers the
# question; the web is there for what the sensors cannot know.

SEARCH_SUBJECT = [
    ("o que achas de como está o tempo em Lisboa?", "como está o tempo em Lisboa?"),
    ("qual achas do Edgar Allan Poe?", "Edgar Allan Poe?"),
    ("como vês a sala?", "a sala?"),
    ("o que te parece disto?", "disto?"),
    # nothing to strip: the question IS the subject
    ("como está o tempo em Lisboa?", "como está o tempo em Lisboa?"),
    # stripping would empty the query, so the original is searched
    ("o que achas", "o que achas"),
]


@pytest.mark.parametrize("asked,expected", SEARCH_SUBJECT)
def test_the_search_gets_the_subject_not_the_opinion_wrapper(asked, expected):
    assert PhantasmaPipeline._search_subject(asked) == expected


def test_every_strippable_prefix_is_an_opinion_marker():
    """The two lists have to agree, or a routed opinion still reaches the engine.

    If a phrase routes to the LLM as an opinion but is not in the prefixes, the
    wrapper survives into the query -- the exact 2026-10-04 failure. Asserted as
    a set relationship rather than by copying the list twice.
    """
    prefixes = set(PhantasmaPipeline.OPINION_PREFIXES)
    markers = set(PhantasmaPipeline.OPINION_MARKERS)
    assert prefixes <= markers, f"prefixos que não são marcadores: {prefixes - markers}"


@pytest.mark.parametrize("marker", ["gostas de", "gostas do", "gosto de"])
def test_a_marker_that_is_not_a_prefix_leaves_the_query_intact(marker):
    """Not stripping is the safe failure.

    "gostas do Porto" is about liking Porto, not about the city forecast, and
    these markers are deliberately NOT prefixes: stripping "do" would search for
    "Porto" and hand the engine a question nobody asked. They route to the LLM
    as opinions, and the whole phrase goes with them.
    """
    q = f"{marker} Porto"
    assert PhantasmaPipeline._search_subject(q) == q
    assert PhantasmaPipeline._is_opinion(q), "mesmo assim continua a ser opinião"


@pytest.mark.parametrize(
    ("asked", "expected"),
    [
        ("concordas com isso?", "isso?"),
        ("concordas que está frio?", "que está frio?"),
    ],
)
def test_concordas_is_a_prefix_and_is_stripped(asked, expected):
    """"concordas" IS a prefix, unlike "gostas do".

    It was in both lists while this test asserted it was in neither, which is
    the assertion being wrong rather than the code: "concordas com isso" has
    nothing to search for except "isso".
    """
    assert PhantasmaPipeline._search_subject(asked) == expected


def test_the_search_actually_receives_the_subject(monkeypatch):
    """The call site, not just the helper.

    Added because falsifying the fix -- putting `text` back where
    `_search_subject(text)` was -- left this file GREEN. Every test above called
    the helper directly, so they passed while the production path searched the
    whole wrapper again and SearxNG went back to returning the dictionary entry
    for "achas". A helper test is not a wiring test.
    """
    asked = []

    def fake_search(q, *a, **k):
        asked.append(q)
        return ""

    monkeypatch.setattr("assistant.search_with_searxng", fake_search)
    monkeypatch.setattr("assistant.retrieve_from_rag", lambda _t: None)
    monkeypatch.setattr("assistant.get_cached_response", lambda _t: None)

    class _Resp:
        @staticmethod
        def chat(*a, **k):
            return {"message": {"content": "resposta"}}

    class _Client(_Resp):
        def __init__(self, host=None, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_e):
            return False

    monkeypatch.setattr("ollama.Client", _Client)

    p = PhantasmaPipeline.__new__(PhantasmaPipeline)
    p._fly_brain = type("FB", (), {"step": lambda self, **k: None})()
    p._web_derived = False

    p._respond_with_llm_body("o que achas de como está o tempo em Lisboa?")

    assert asked, "a pesquisa não foi chamada"
    assert "achas" not in asked[0], (
        f"o enquadramento da opinião chegou ao motor de pesquisa: {asked[0]!r}"
    )
    assert "tempo em Lisboa" in asked[0], (
        f"o assunto não chegou ao motor de pesquisa: {asked[0]!r}"
    )
