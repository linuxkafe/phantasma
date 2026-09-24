"""Tests for Discord reaction rewards (T042).

The Discord skill module cannot be imported directly in the dev venv
(discord package is not installed), so we import the pure reward logic by
loading the module source against a stub `discord` module injected into
sys.modules. This tests the decision the FlyBrain reward depends on without
requiring network or the discord.py runtime.
"""

import ast
import sys
import types
from pathlib import Path

import pytest

SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "skill_discord.py"


class _DummyIntents:
    message_content = True
    reactions = True

    @staticmethod
    def default():
        intents = _DummyIntents()
        intents.message_content = True
        intents.reactions = True
        return intents


class _DummyClient:
    """Minimal stand-in so `import discord` + `discord.Client()` succeed."""

    Intents = _DummyIntents

    class Client:
        user = None

        def __init__(self, **kwargs):
            pass


@pytest.fixture(scope="module")
def skill_module():
    """Import skills/skill_discord.py with a stubbed discord package."""

    disco = types.ModuleType("discord")
    disco.Client = _DummyClient.Client
    disco.Intents = _DummyIntents
    sys.modules["discord"] = disco

    mod = types.ModuleType("skill_discord")
    mod.__file__ = str(SKILL_PATH)
    source = SKILL_PATH.read_text(encoding="utf-8")
    # Remove discord.py decorators/coro wrappers that assume a live event loop:
    # keep module-level defs; @client.event decorators reference the stub client
    # and would run wrappers. Instead parse AST and strip decorators.
    tree = ast.parse(source)
    # Drop decorators on async defs so the module body does not invoke the
    # stub event-registration machinery at import time.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
    cleaned = ast.unparse(tree)

    exec(compile(cleaned, str(SKILL_PATH), "exec"), mod.__dict__)
    return mod


def test_thumbs_down_maps_to_negative_reward(skill_module):
    """T042: 👎 must now produce a negative reward (was silently dropped)."""
    assert skill_module._reward_for_emoji("👎") == -1.0


def test_thumbs_up_maps_to_positive_reward(skill_module):
    assert skill_module._reward_for_emoji("👍") == +1.0


def test_unmapped_emoji_returns_none(skill_module):
    """Unknown emoji yields None so the handler can log + skip cleanly."""
    assert skill_module._reward_for_emoji("🤖") is None


def test_all_mapped_emojis_preserved(skill_module):
    """Regression: previously-working emojis must keep their rewards."""
    expected = {
        "👍": +1.0,
        "👎": -1.0,
        "❤️": +1.0,
        "🔥": +1.0,
        "😡": -1.0,
        "😢": -0.5,
    }
    for emoji, reward in expected.items():
        assert skill_module._reward_for_emoji(emoji) == reward
