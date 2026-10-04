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


class _DMChannel:
    """`isinstance(message.channel, discord.DMChannel)` is the DM test."""


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
    disco.DMChannel = _DMChannel
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


# The owner's own words from #linuxkafe on 2026-10-04, read back from the
# Discord REST API. The first one was answered with silence; the second one
# worked. The only difference is the mention spelling, which is why this test
# exists and why the numbers are hardcoded rather than invented.
BOT_ID = "1442501057170243614"
BOT_ROLE_ID = "1442506598726766659"  # the bot's role in LINUXKAFE
OWNER_ID = "485604987812970496"


class _FakeRole:
    def __init__(self, role_id):
        self.id = role_id


class _FakeMember:
    """`guild.me` -- the bot as a member, which is where its roles live."""

    def __init__(self, role_ids):
        self.roles = [_FakeRole(r) for r in role_ids]


class _FakeGuild:
    def __init__(self, role_ids):
        self.me = _FakeMember(role_ids)


class _FakeChannel:
    """A guild text channel.

    Deliberately NOT a subclass of the stubbed `discord.DMChannel`. Making one
    class serve both roles made `isinstance(channel, DMChannel)` true for every
    message, so all of them took the DM branch and the mention-stripping tests
    failed on a harness bug while the code under test was fine.
    """

    def __init__(self):
        self.sent = []

    async def send(self, content):
        self.sent.append(content)

    def typing(self):
        """`async with message.channel.typing()` wraps the whole reply."""
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _FakeDMChannel(_FakeChannel, _DMChannel):
    """A DM: same surface, but it passes the isinstance test for DMs."""


def _message(content, *, user_mentions=(), role_mentions=(), role_ids=(), is_dm=False):
    """A stand-in with just the attributes on_message actually reads.

    `mentions` and `role_mentions` are separate because that separation IS the
    bug: Discord fills only `role_mentions` for `<@&id>`, and code that checks
    only `mentions` sees an ordinary message from a bot that never heard it.
    """
    import types as _t

    msg = _t.SimpleNamespace()
    msg.content = content
    msg.author = _t.SimpleNamespace(id=OWNER_ID, name="linuxkafe")
    msg.guild = _FakeGuild(role_ids) if role_ids is not None else None
    msg.channel = _FakeDMChannel() if is_dm else _FakeChannel()

    class _Bot:
        def __init__(self, bot_id):
            self.id = bot_id

        def __eq__(self, other):
            return getattr(other, "id", None) == self.id

        __hash__ = None

    msg.mentions = [_Bot(u) for u in user_mentions]
    msg.role_mentions = [_Bot(r) for r in role_mentions]
    return msg


def _extracted_prompt(skill_module, message):
    """Run the real on_message with everything past the prompt cut off.

    Returns the prompt the handler would send. Anything that returns None never
    reached the assistant, which is the failure being pinned down.
    """
    import asyncio

    sent = {}

    async def fake_send(prompt):
        sent["prompt"] = prompt
        return "resposta", True

    # The authorisation decision is covered by its own tests against the real
    # store. Here it is stubbed so that a failure means "the trigger did not
    # fire", and cannot be mistaken for "the guest was refused".
    originals = (skill_module._send_to_phantasma, skill_module._check_access)
    skill_module._send_to_phantasma = fake_send
    skill_module._check_access = lambda *_a, **_k: (True, "", None)
    try:
        asyncio.run(skill_module.on_message(message))
    finally:
        (skill_module._send_to_phantasma, skill_module._check_access) = originals
    # Only reaching `_send_to_phantasma` counts as "the trigger fired". The
    # successful reply ALSO goes out through `channel.send`, so treating any
    # send as a refusal made every working test report None.
    return sent.get("prompt")


def test_a_role_mention_reaches_the_assistant(skill_module):
    """`<@&role>` is how the owner actually summons the bot. It must work.

    This is the exact message that got no reply. Before the fix,
    `message.mentions` was empty and the handler returned without a word.
    """
    skill_module.client.user = type("U", (), {"id": BOT_ID})()
    msg = _message(
        f"<@&{BOT_ROLE_ID}> como está o tempo em Aveiro?",
        role_mentions=[BOT_ROLE_ID],
        role_ids=[BOT_ROLE_ID],
    )
    assert skill_module.client.user not in msg.mentions, (
        "premissa do teste quebrada: uma mencao de role NAO pode aparecer "
        "em message.mentions -- e e por isso que o bug existia"
    )
    assert _extracted_prompt(skill_module, msg) == "como está o tempo em Aveiro?"


def test_a_user_mention_still_reaches_the_assistant(skill_module):
    """The spelling that already worked must keep working."""
    skill_module.client.user = type("U", (), {"id": BOT_ID})()
    msg = _message(
        f"<@{BOT_ID}> diz olá", user_mentions=[BOT_ID], role_ids=[BOT_ROLE_ID]
    )
    assert _extracted_prompt(skill_module, msg) == "diz olá"


def test_the_bang_user_spelling_is_stripped(skill_module):
    """`<@!id>` is what some clients send; the token must not survive."""
    skill_module.client.user = type("U", (), {"id": BOT_ID})()
    msg = _message(f"<@!{BOT_ID}> diz olá", user_mentions=[BOT_ID])
    assert _extracted_prompt(skill_module, msg) == "diz olá"


def test_a_role_the_bot_does_not_hold_is_not_a_summons(skill_module):
    """Mentioning someone else's role must stay silent, not answer everything."""
    skill_module.client.user = type("U", (), {"id": BOT_ID})()
    msg = _message(
        "<@&999> texto", role_mentions=["999"], role_ids=[BOT_ROLE_ID]
    )
    assert _extracted_prompt(skill_module, msg) is None


def test_a_plain_channel_message_is_still_ignored(skill_module):
    """No mention, no answer. Unchanged on purpose: the bot is not ambient."""
    skill_module.client.user = type("U", (), {"id": BOT_ID})()
    msg = _message("wee :p", role_ids=[BOT_ROLE_ID])
    assert _extracted_prompt(skill_module, msg) is None


def test_dm_needs_no_mention(skill_module):
    skill_module.client.user = type("U", (), {"id": BOT_ID})()
    msg = _message("oi", is_dm=True)
    assert _extracted_prompt(skill_module, msg) == "oi"
