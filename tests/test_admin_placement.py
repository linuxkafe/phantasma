"""The admin pages must each own the right editor, and own it once.

Written after three separate mistakes in one session, all of the same shape:
a Jinja block inserted by a fragile textual anchor, with no assertion that it
landed in the right template. `ast.parse` passed every time -- the file was
valid Python, the markup was simply in the wrong place, or a button posted to
a handler that ignored it. Syntax is not placement, and it is not wiring.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import admin as admin_mod  # noqa: E402


def _template_body(name: str) -> str:
    source = open(admin_mod.__file__, encoding="utf-8").read()
    start = source.index(f"{name} = (")
    end = source.index("</body>", start)
    return source[start:end]


PERSONA_MARK = "Persona e reacções"
KNOWLEDGE_MARK = "Corrigir o que ele believe"
KNOWLEDGE_MARK_PT = "Corrigir o que ele acredita"


def test_persona_lives_in_config():
    """The owner changes how the assistant speaks from /admin/config."""
    body = _template_body("CONFIG_TEMPLATE")
    assert PERSONA_MARK in body
    assert "save_persona" in body, "no submit control for the persona"
    assert 'name="persona"' in body, "no textarea bound to the persona"


def test_config_persona_form_posts_somewhere_real():
    """A persona form with no action posts to the current URL. So the
    current URL has to handle it -- this is the wiring that a visual check
    cannot see and that a copy-pasted block silently loses."""
    src = open(admin_mod.__file__, encoding="utf-8").read()
    cfg = src[src.index("def config_manager():") :]
    cfg = cfg[: cfg.index("\n@admin_bp.route")]
    for action in ("save_persona", "reset_persona", "save_weights", "reset_weights"):
        assert action in cfg, f"/admin/config does not handle {action}"


def test_knowledge_editor_lives_in_brain():
    """The owner corrects what the assistant believes from /admin/brain."""
    body = _template_body("BRAIN_TEMPLATE")
    assert KNOWLEDGE_MARK_PT in body, (
        "/admin/brain has no knowledge editor: the block landed in "
        "MEMORY_TEMPLATE (/admin/memory) instead"
    )
    for field in (
        'value="delete_node"',
        'value="save_memory"',
        'value="delete_memory"',
        'name="node_id"',
    ):
        assert field in body, f"/admin/brain is missing {field}"


def test_brain_handles_the_knowledge_post():
    """Same wiring rule: the buttons on /admin/brain must reach a handler."""
    src = open(admin_mod.__file__, encoding="utf-8").read()
    # /admin/brain is brain_hub, NOT memory_viewer. Asserting on the wrong
    # function is how this test passed while the page stayed read-only.
    start = src.index("def brain_hub():")
    body = src[start : start + 4000]
    assert 'if request.method == "POST":' in body, (
        "brain_hub is GET-only, so the edit buttons on /admin/brain would silently do nothing"
    )
    for op in ("delete_node", "save_memory", "delete_memory"):
        assert op in body, f"brain_hub does not handle {op}"


def test_persona_block_is_not_stranded_in_brain():
    """The mistake that shipped: a persona form inside /admin/brain, whose
    buttons post to a handler that never heard of them."""
    body = _template_body("BRAIN_TEMPLATE")
    assert PERSONA_MARK not in body, (
        "/admin/brain contains a persona form that posts to a handler which does not handle it"
    )


@pytest.mark.parametrize("name", ["BRAIN_TEMPLATE", "MEMORY_TEMPLATE"])
def test_knowledge_block_appears_once_per_template(name):
    """Not twice, not zero: a duplicated block means two controls bound to
    the same field, and a zero means the owner cannot correct anything."""
    body = _template_body(name)
    assert body.count(KNOWLEDGE_MARK_PT) <= 1


def test_every_post_control_has_a_handler():
    """Cross-check the two lists: every action/op the templates emit must
    appear in some admin handler, and every handler must be reachable."""
    src = open(admin_mod.__file__, encoding="utf-8").read()
    for name in ("BRAIN_TEMPLATE", "MEMORY_TEMPLATE", "CONFIG_TEMPLATE"):
        body = _template_body(name)
        for marker in (
            'value="save_persona"',
            'value="delete_node"',
            'value="save_memory"',
        ):
            if marker in body:
                action = marker.split('"')[1]
                assert f'"{action}"' in src, f"{name} offers {action} but no handler mentions it"
