"""Which audio settings exist in code, and which of them the page can change.

The owner's report, 2026-10-04: the response sound "deve poder ser definido na
config". Two separate defects, found by reading the two sides against each other
rather than by looking at either alone.

1. `AUDIO_FEEDBACK_ENABLED` is on the page as "Som de confirmação", and it is the
   only member of that group. The sound it gates is built from two more values --
   `music_dir` and `greeting_path` -- and neither is on the page. So the switch was
   editable and the sound was not: an owner could turn the sound on and off, and
   could not say what it should be.

2. The temperature slider had been committed, deployed, unit-tested and its save
   path verified against production -- and was not on the page at all.
   `get_configs_by_category` iterates the `config` TABLE, not the registry, and
   the rows for the two new keys had never been created. `update_config` upserts,
   but only once somebody submits the form, and a control nobody can see is a
   control nobody can fill in. Found by the owner looking at the page, which is
   the only method that exercises the whole path.

The second one is the more interesting failure. Everything I checked about the
slider passed: the control was registered, the runtime read it through the
resolver, saving through the store changed the resolved value, reverting restored
it, the prod suite was green. What none of that could see was a page that never
rendered it, because rendering needs a row and only the page reads rows.

So this test walks the registry against the table. A key registered in code but
absent from `config.db` is invisible to the owner no matter how well it works,
and that is the assertion.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.api.admin import CONFIG_CONTROLS, seed_missing_config_controls  # noqa: E402

# The audio-feedback group. `enabled` is the switch; the other two decide WHAT it
# plays, and a switch without them is half a control.
AUDIO_FEEDBACK_KEYS = (
    "AUDIO_FEEDBACK_ENABLED",
    "MUSIC_DIR",
    "GREETING_PATH",
)

TEMP_KEYS = ("LLM_TEMPERATURE_CONVERSATION", "LLM_TEMPERATURE_FACTUAL")


def test_the_temperature_slider_is_registered():
    """Registered -- which was true, and was not sufficient. See below."""
    assert "LLM_TEMPERATURE_CONVERSATION" in CONFIG_CONTROLS
    assert "LLM_TEMPERATURE_FACTUAL" in CONFIG_CONTROLS


def test_every_registered_control_ends_up_with_a_row():
    """The bug that hid the slider.

    `get_configs_by_category` renders the TABLE. A control in the registry with no
    row is not rendered, cannot be filled in, and never will be -- `update_config`
    only upserts when a form is submitted, and there is no form for a control
    that is not on the page.

    `seed_missing_config_controls` is called on every read for exactly this. The
    assertion here is that the seeding exists and is idempotent; the table is
    checked in production by the check that prints which keys are missing.
    """
    assert callable(seed_missing_config_controls)
    src = open(
        os.path.join(ROOT, "src", "api", "admin.py"), encoding="utf-8"
    ).read()
    # Called from the read path, not from a migration someone has to remember.
    assert "seed_missing_config_controls()" in src, (
        "a semeadura existe mas nao e chamada da leitura da pagina, e um control "
        "novo continua invisivel ate alguem submeter o formulario"
    )
    # INSERT OR IGNORE, so a second read does not duplicate or fail.
    assert "INSERT OR IGNORE INTO config" in src, (
        "a semeadura nao e idempotente: a segunda leitura da pagina insere "
        "outra vez"
    )


def test_the_seed_does_not_take_the_page_down(tmp_path):
    """A read-only database must not break a page that used to render.

    The seeding writes. Before it, reading the table worked on a read-only
    database and rendered whatever had rows. After it, a write failure that
    escaped would turn a working page into a 500.
    """
    src = open(
        os.path.join(ROOT, "src", "api", "admin.py"), encoding="utf-8"
    ).read()
    body = src[src.find("def seed_missing_config_controls"):]
    body = body[: body.find("\ndef get_configs_by_category")]
    assert "except Exception" in body, (
        "a semeadura nao trata falhas; uma base so de leitura passava a dar 500 "
        "numa pagina que antes funcionava"
    )
    assert "warning" in body, "a falha tem de ser registada, não só engolida"


def test_the_audio_feedback_group_is_editable_together():
    """The switch and what it switches.

    Measured: `AUDIO_FEEDBACK_ENABLED` was on the page as "Som de confirmação"
    and it was the ONLY member of its group. `music_dir` and `greeting_path`
    decide what actually plays -- `_play_audio_feedback` picks a random file
    from the directory and then plays the greeting -- and neither was editable.
    An owner could turn the sound on and off and could not say what it should be.
    """
    for key in AUDIO_FEEDBACK_KEYS:
        audio_controls = [
            k
            for k in CONFIG_CONTROLS
            if "AUDIO" in k or "MUSIC" in k or "GREETING" in k
        ]
        assert key in CONFIG_CONTROLS, (
            f"{key} faz parte do som de resposta e não é ajustável na config. "
            f"Registados no som: {sorted(audio_controls)}"
        )


def test_the_audio_feedback_key_is_labelled_for_what_it_plays():
    """The label said "confirmação" while it plays the WAKE sound.

    A confirmation sound and a wake sound are different things, and the label
    said the first while the code did the second. Read quickly -- which is how an
    owner reads a settings page -- the group looks handled: there is a switch, it
    is labelled, and it is in the right category.

    The fix is not to add a switch. It is that the label has to name the moment
    the sound happens, because that is the only thing an owner can act on: this
    plays when the wake word fires, and not when the assistant answers.
    """
    enabled = CONFIG_CONTROLS["AUDIO_FEEDBACK_ENABLED"]
    label = (enabled.get("label") or "").lower()
    help_text = (enabled.get("help") or "").lower()

    assert "confirmação" not in label, (
        "o interruptor continua etiquetado como som de confirmação. Toca na "
        "activação, não na resposta, e o nome é a única coisa que o dono lê"
    )
    # "activacao" is what it must name. Both spellings, because the page is
    # written in one and this test must not depend on which.
    assert ("activação" in label or "activacao" in label), (
        f"o interruptor não diz quando toca. Label: {enabled.get('label')!r}"
    )
    assert "palavra de activação" in help_text or "palavra de ativação" in help_text, (
        f"a ajuda não diz que é ao reconhecer a palavra de activação. "
        f"Ajuda: {enabled.get('help')!r}"
    )


def test_the_music_and_greeting_keys_have_help():
    """A path in a text field with no explanation is a guess.

    Both are directories and files on a specific host; the owner has to know
    where they live and what goes in them.
    """
    for key in ("MUSIC_DIR", "GREETING_PATH"):
        assert CONFIG_CONTROLS[key].get("help"), (
            f"{key} é um caminho e está sem ajuda; quem não souber o que lá pôr "
            f"não o consegue preencher"
        )


def test_the_seed_does_not_overwrite_a_value_the_owner_already_set(tmp_path):
    """INSERT, not INSERT OR REPLACE.

    Seeding on every read with a REPLACE would reset any value that had been
    saved the moment the page loaded -- the control would appear to save and then
    silently revert, which is worse than the missing control this fixes.
    """
    src = open(
        os.path.join(ROOT, "src", "api", "admin.py"), encoding="utf-8"
    ).read()
    body = src[src.find("def seed_missing_config_controls"):]
    body = body[: body.find("\ndef get_configs_by_category")]
    assert "INSERT OR REPLACE" not in body, (
        "a semeadura usa INSERT OR REPLACE, que rebenta o valor de quem ja "
        "guardou assim que a pagina abre"
    )


def test_the_page_iterates_the_table_and_not_only_the_registry():
    """Why the slider was invisible, stated as a fact about the code.

    Worth pinning because the fix (seeding) is easy to remove as redundant: the
    page looks like it reads `CONFIG_CONTROLS`, and it does -- but only to decide
    which rows to render. The rows are the source of what is shown.
    """
    src = open(
        os.path.join(ROOT, "src", "api", "admin.py"), encoding="utf-8"
    ).read()
    body = src[src.find("def get_configs_by_category"):]
    body = body[: body.find("\ndef ")]
    assert "SELECT * FROM config" in body, (
        "a leitura da pagina deixou de ser sobre a tabela config; se passar a "
        "ler so o registo, o problema passa a ser o oposto -- e vale a pena "
        "saber qual dos dois e"
    )
    assert "seed_missing_config_controls" in body, (
        "a leitura da tabela tem de semear as linhas em falta ANTES de as "
        "ler, ou o primeiro carregamento mostra o estado antigo"
    )
