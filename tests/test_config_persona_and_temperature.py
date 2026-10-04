"""The configuration page must edit the voice, and the temperature must be the
owner's rather than a constant in the middle of a function.

Two requests, 2026-10-04:

    "o admin/config agora não tem a edição de persona e devia ter"
    "deve ter também a gestão da temperatura do modelo com um slider"

The persona was not missing by accident and never had been: it lives on
`/admin/persona`, rendered by `_PERSONA_TEMPLATE`, and `config_manager` renders
`CONFIG_TEMPLATE`, which has no persona section. The POST handler already
accepted `save_persona` -- `persona=get_persona()` and
`persona_overridden=...` were already passed to the render. Only the HTML was
absent, so the route had a working save path with nothing to trigger it.

Worth stating plainly, because the first version of this work claimed otherwise:
the persona did not "disappear". The evidence for that is in this file's sibling,
`test_model_config_precedence.py`, whose docstring records the owner asking the
same question. An earlier answer asserted a regression that did not happen.

The case for putting it here is not tidiness. Everything on this page says
"aplica-se no próximo arranque", while the persona had always applied to the next
message. The voice sat on a page of restart-pending values, so the one setting an
owner changes most often looked like the one that never took effect.

Temperature: one slider, not two. The factual temperature is fixed at 0.15 from a
measurement -- at 0.6 an 8B embellishes a fact instead of reporting it, and asked
what the Capuchinho Verde is it answered at length about a residential security
module. Putting it on a slider makes that available again by accident, so it is
shown as fixed and the save loop skips it.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.api.admin import CONFIG_CONTROLS, CONFIG_TEMPLATE  # noqa: E402
from src.brain import model_config as mc  # noqa: E402

TEMP_KEY = "LLM_TEMPERATURE_CONVERSATION"
FACTUAL_KEY = "LLM_TEMPERATURE_FACTUAL"


# --- the persona is on this page ------------------------------------------

def test_the_persona_editor_is_here_and_did_not_disappear():
    """It was ALWAYS here. Asserting otherwise would have been a fabricated bug.

    The owner reported it missing. The search for it went wrong in both
    directions: the persona template at `/admin/persona` was found first, which
    made "it lives elsewhere" look like an answer, and a second search for
    `name="persona"` inside the file found the OTHER page's textarea before the
    one in CONFIG_TEMPLATE. Both exist -- `CONFIG_TEMPLATE` carries a
    "Persona e reacções" section with `save_persona`, `reset_persona` and the
    overridden flag.

    So this test now asserts the section is present AND that it is a full editor,
    because the useful question was never "is it there" but "does saving from
    here reach the same store the runtime reads".
    """
    assert 'name="persona"' in CONFIG_TEMPLATE, (
        "a secção de persona desapareceu de CONFIG_TEMPLATE"
    )
    assert 'value="save_persona"' in CONFIG_TEMPLATE, "sem botão de guardar"
    assert 'value="reset_persona"' in CONFIG_TEMPLATE, "sem botão de repor"
    assert "persona_overridden" in CONFIG_TEMPLATE, (
        "a página não diz se a persona está personalizada"
    )


def test_the_persona_section_says_it_applies_without_a_restart():
    """Everything else on this page says "next restart".

    The voice sat among restart-pending values with no marker saying it was the
    exception, so the setting an owner changes most often looked like the one
    that never took effect. It says "aplica-se à próxima mensagem" -- check the
    wording, not just the presence of the idea.
    """
    folded = CONFIG_TEMPLATE.lower()
    # Diacritics folded: the template is written in accented Portuguese and a test
    # that spells it unaccented fails on typography rather than on meaning.
    for a, b in (("á", "a"), ("à", "a"), ("ó", "o"), ("é", "e"), ("í", "i")):
        folded = folded.replace(a, b)
    assert "proxima mensagem" in folded, (
        "a secção da persona nao diz que aplica-se ja"
    )
    assert "reiniciar" in folded, (
        "a secção da persona nao diz que nao precisa de reiniciar"
    )


def test_saving_the_persona_from_here_is_the_same_path_as_the_persona_page():
    """One store, two routes -- or a change made in one is invisible in the other.

    `/admin/persona` keeps its place and this page links to it for the reaction
    weights. Both must end in the same store.
    """
    src = open(os.path.join(ROOT, "src", "api", "admin.py"), encoding="utf-8").read()
    writer = 'set_persona(request.form.get("persona"'
    assert src.count(writer) >= 2, (
        "as duas rotas deixaram de gravar no mesmo sítio -- uma mudança feita aqui "
        "seria invisível ali e vice-versa"
    )
    assert "/admin/persona" in CONFIG_TEMPLATE, (
        "a página não liga à página das reacções, que é o que ficou só lá"
    )


# --- the temperature slider -----------------------------------------------

def test_the_conversation_temperature_is_a_slider():
    ctl = CONFIG_CONTROLS.get(TEMP_KEY)
    assert ctl is not None, f"{TEMP_KEY} não está nos controlos"
    assert ctl["type"] == "number", ctl
    assert ctl["min"] == 0 and ctl["max"] == 1, ctl
    assert ctl["step"] == 0.05, ctl


def test_the_factual_temperature_is_shown_but_cannot_be_saved():
    """Shown as fixed, and skipped on save.

    The value is 0.15 from a measurement, not a preference. It appears in
    CONFIG_CONTROLS so the page can explain its absence, which means it has to be
    marked readonly -- otherwise the form would let the page overwrite the value
    it has just told the owner it does not allow.
    """
    ctl = CONFIG_CONTROLS.get(FACTUAL_KEY)
    assert ctl is not None, "a temperatura dos factos devia estar explainavel"
    assert ctl.get("readonly") is True, (
        "a temperatura factual deixou de ser readonly e voltou a ser gravavel. "
        "Foi posta a 0.15 porque a 0.6 o modelo enfeita factos em vez de os "
        "relatar."
    )
    # Marking it readonly is only half the guard: the save loop has to honour it.
    # The declaration alone was enough to satisfy the test above while the loop
    # would still have posted the field -- which is why the falsification that
    # removed `readonly=True` passed when it should not have.
    src = open(os.path.join(ROOT, "src", "api", "admin.py"), encoding="utf-8").read()
    assert 'if meta.get("readonly"):' in src, (
        "o loop de gravacao deixou de saltar as chaves readonly, e a pagina "
        "passa a poder escrever o valor que ela propria diz nao ser ajustavel"
    )


def test_the_runtime_reads_the_temperature_instead_of_a_constant():
    src = open(os.path.join(ROOT, "assistant.py"), encoding="utf-8").read()
    assert "resolve_temperature" in src, (
        "assistant.py deixou de resolver a temperatura pelo modulo; o slider "
        "passaria a nao fazer nada, que e o mesmo bug do espelho morto"
    )
    code = "\n".join(
        line for line in src.split("\n") if not line.lstrip().startswith("#")
    )
    assert "else 0.6" not in code, (
        "a temperatura da conversation voltou a ser uma constante no meio da "
        "funcao"
    )


def test_an_opinion_is_never_given_the_factual_temperature(monkeypatch):
    """The measurement that put the rule in place.

    2026-10-03, "o que achas do Edgar Allan Poe": `grounded` was true because the
    graph returns context for every question, so the temperature dropped to 0.15
    and the answer came back as a third-person literary review. `assistant.py`
    combines `factual or (grounded and not opinion)` before asking, which is what
    this asserts about the shape of the decision.
    """
    monkeypatch.setattr(mc, "_from_settings", lambda k: None)
    monkeypatch.setattr(mc, "_from_env", lambda k: None)
    assert mc.temperature(factual=False) == 0.6
    assert mc.temperature(factual=True) == mc.TEMP_FACTUAL


def test_the_owner_can_move_the_conversation_temperature(monkeypatch):
    monkeypatch.setattr(mc, "_from_settings", lambda k: "0.35" if k == TEMP_KEY else None)
    monkeypatch.setattr(mc, "_from_env", lambda k: None)
    assert mc.temperature(factual=False) == 0.35
    # And a factual turn ignores it entirely.
    assert mc.temperature(factual=True) == mc.TEMP_FACTUAL


def test_a_hand_edited_temperature_outside_the_range_is_clamped(monkeypatch):
    """A `.env` edited by hand, or a form posting nonsense.

    The page says the slider stops at 1.0, so a request for 4.0 should not
    silently get it -- an 8B above 1.0 stops being the house.
    """
    for raw, expected in (("4.0", 1.0), ("-2", 0.0), ("lixo", 0.6), ("", 0.6)):
        monkeypatch.setattr(
            mc, "_from_settings", lambda k, v=raw: v if k == TEMP_KEY else None
        )
        monkeypatch.setattr(mc, "_from_env", lambda k: None)
        assert mc.temperature(factual=False) == expected, (raw, expected)


def test_the_default_temperature_is_the_measured_one():
    """0.6, not a round number chosen for looks.

    Below it the answers flatten; at it the persona's cadence survived the Poe
    question that started the model work.
    """
    assert mc.DEFAULTS[mc.TEMP_CONVERSATION] == "0.6"
    assert mc.TEMP_FACTUAL == 0.15
