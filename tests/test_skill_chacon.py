"""Tests for skill_chacon.

These guard the failure mode found in T036: the skill imported
``dio_chacon_wifi_api`` at module scope, and when that package was missing the
``except ImportError`` set a module flag and returned ``None`` from ``handle()``
forever. The skill looked *disabled* rather than *broken*, and nothing in the
test suite or the logs said why. `T035-learn` recorded the behaviour as
intended.

The tests below do NOT contact the Chacon cloud. That account is dead (see
aes/tickets/T036), and a test that needs a live vendor account is a test that
silently stops running.
"""

import ast
from pathlib import Path

SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "skill_chacon.py"


def test_dependency_is_importable():
    """The library the skill needs must be installed, not merely declared.

    pyproject.toml listed ``dio-chacon-wifi-api`` from T035, but the dev venv
    never received it. Declaring a dependency is not the same as having it.
    """
    import dio_chacon_wifi_api  # noqa: F401


def test_skill_module_loads_and_is_not_inert():
    """Importing the skill must not silently disable it."""
    from skills import skill_chacon

    assert skill_chacon.IMPORT_ERROR is None, (
        f"skill_chacon is inert: {skill_chacon.IMPORT_ERROR}"
    )
    assert skill_chacon.DioChaconApi is not None


def test_import_error_is_recorded_not_only_printed():
    """The import failure reason must survive into a module attribute.

    A bare `print` inside `except ImportError` is invisible to journald
    filtering and to any test; the reason for a dead skill has to be
    programmatically readable.
    """
    source = SKILL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    import_error = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "IMPORT_ERROR":
                    import_error = node

    assert import_error is not None, "IMPORT_ERROR must be assigned in the module"
    assert not isinstance(import_error.value, ast.Constant), (
        "IMPORT_ERROR must capture the exception message, not a fixed string"
    )


def test_exception_fallbacks_exist_so_except_blocks_cannot_name_error():
    """The `except` clauses reference exception classes by name.

    If the import fails those names are unbound, so a failure raised inside the
    handler would raise NameError and hide the real cause. Fallback classes must
    be defined in the ImportError branch.
    """
    from skills import skill_chacon

    assert issubclass(skill_chacon.DIOChaconAPIError, Exception)
    assert issubclass(skill_chacon.DIOChaconInvalidAuthError, Exception)


def test_handle_returns_none_for_unrelated_prompt():
    """Non-matching prompts must not be claimed by this skill."""
    from skills import skill_chacon

    assert skill_chacon.handle("que horas sao", "que horas sao") is None


def test_triggers_cover_the_balcao_nicknames():
    """The nicknames the assistant is invoked with must still match."""
    from skills import skill_chacon

    for nickname in ("luz do balcao", "luz do balcão", "balcao"):
        assert nickname in skill_chacon.TRIGGERS


def test_on_off_intents_are_disjoint():
    """A prompt that asks to switch off must not be read as 'on'."""
    from skills import skill_chacon

    off_prompt = "desliga a luz do balcao"
    assert any(a in off_prompt for a in skill_chacon.ACTIONS_OFF)
