"""The Discord event handler must not raise.

`on_message` is registered as a module-level `client.event`, so its only
parameter is `message`. It has no `self`.

A revision added `self` to the `_check_access` call inside it, to pass the skill
instance whose context holds the resolver. That raises `NameError` on **every
message the bot receives**. `discord.py` catches exceptions raised inside an
event handler and logs them at DEBUG, so:

- the bot received the message,
- it failed before reaching any authorization code,
- it said nothing, and
- the service stayed `active` with `NRestarts=0` and a healthy `/api/health`.

From Discord that is indistinguishable from an offline bot, which is how a bot
went silent for hours while the whole test suite stayed green.

Why the suite did not catch it: every other test in this repository calls
`check()` or `_check_access()` directly, so it never executes the event
handler's body. The bug lived in the wiring between them, and no test crossed
that boundary. This one does.

The handler is called with a message object whose channel passes
`isinstance(..., discord.DMChannel)` -- a subclass defined here, so this runs
without importing `discord.py` and without a gateway.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _discord_available() -> bool:
    """`discord.py` is installed in production's venv only.

    It is a 15 MB dependency, so it is not in the development venv, and these
    tests skip rather than fake it. That split is also part of why the bug they
    cover lasted: in the environment where they would normally run, the import
    does not resolve at all, so the handler was never executed here.

    `deploy.sh` runs the suite inside `/opt/phantasma`, so the gate that
    matters for this file does run them.
    """
    try:
        import discord  # noqa: F401

        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _discord_available(),
    reason=(
        "discord.py is production-only; these run in the suite deploy.sh "
        "executes, not in the development venv"
    ),
)

OWNER = "485604987812970496"


class _NullTyping:
    """Stand-in for `channel.typing()`, which needs a live gateway."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_the_handler_body_has_no_free_names():
    """`on_message` must not reference any name it does not bind itself.

    A closure analysis over the compiled code object, rather than a search for
    the word `self`. A textual search for "self" would also flag a legitimate
    local named `self`, and would miss a typo'd global -- which is the same
    failure: a name that resolves to nothing at call time.
    """
    import ast
    import inspect

    import skills.skill_discord as sd

    tree = ast.parse(inspect.getsource(sd.on_message))

    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            bound.add(node.name)
            bound.update(a.arg for a in node.args.args)
            bound.update(a.arg for a in node.args.kwonlyargs)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    bound.add(n.id)

    import builtins

    module_names = {n for n in dir(sd) if not n.startswith("__")} | set(
        dir(builtins)
    )

    free = {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name)
        and isinstance(n.ctx, ast.Load)
        and n.id not in bound
        and n.id not in module_names
    }
    # `self` is the name that bit, and it will not be in `free` because it is
    # not a module global -- so assert on it directly rather than trusting the
    # closure walk to classify it.
    assert "self" not in free, (
        "on_message usa `self`. E um handler de modulo: o unico parametro e "
        "`message` e nao existe instancia. O NameError e engolido pelo "
        "discord.py, o bot fica mudo sem registo, e o servico continua active "
        "e saudavel."
    )
    assert not free, (
        f"on_message usa nomes que nao existem em lado nenhum: {sorted(free)}. "
        f"O mesmo modo de falha do `self`: uma excepcao engolida que se le como "
        f"um bot offline."
    )


def test_a_message_reaches_the_authorization_code_without_raising(monkeypatch):
    """Drive `on_message` with a DM and prove it does not raise.

    The point is not what it decides -- it is that it decides at all. Before the
    fix, this raised `NameError` and the caller swallowed it.
    """
    import skills.skill_discord as sd

    class FakeDM(sd.discord.DMChannel):
        """Just enough DMChannel for the handler and for `send`.

        `send` is stubbed because proving it is NOT called for a refused id is
        one of the two things below; the real one needs a gateway.
        """

        def __init__(self, sent):
            self.sent = sent
            self.id = 1
            self._state = None

        async def send(self, content=None, **kw):
            self.sent.append(content)
            return None

        def typing(self):
            # The real one is an async context manager that needs a gateway to
            # send a typing indicator. None of that is under test.
            return _NullTyping()

    class FakeMessage:
        def __init__(self, channel):
            self.channel = channel
            self.content = "que horas sao"
            self.mentions = []
            self.author = type("A", (), {"id": OWNER, "name": "owner"})()

    to_assistant: list[str] = []
    sent: list[str] = []
    channel = FakeDM(sent)
    monkeypatch.setattr(sd, "client", type("C", (), {"user": object(), "id": 2})())

    async def _send(prompt):
        to_assistant.append(prompt)
        return "sao as dez"

    monkeypatch.setattr(sd, "_send_to_phantasma", _send, raising=False)

    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        sd.on_message(FakeMessage(channel))
    )

    # No NameError, and the handler got all the way to answering: authorized,
    # typed, handed the prompt to the assistant, and replied in the channel.
    assert "que horas sao" in to_assistant, (
        f"o handler nao passou o prompt ao assistente: {to_assistant}. Antes da "
        f"correccao levantava NameError e nao chegava aqui."
    )
    assert channel.sent == ["sao as dez"], (
        f"o handler nao respondeu no canal: {channel.sent}"
    )


def test_the_handler_does_not_raise_for_an_unknown_id(monkeypatch):
    """A refused request must also be silent, but NOT by raising.

    The distinction that matters: refusal is a decision (`return` after
    printing), a NameError is a crash. Both look the same from Discord.
    """
    import asyncio

    import skills.skill_discord as sd

    class FakeDM(sd.discord.DMChannel):
        def __init__(self):
            self.id = 1
            self._state = None

    class FakeMessage:
        def __init__(self):
            self.channel = FakeDM()
            self.content = "acende a luz"
            self.mentions = []
            self.author = type("A", (), {"id": "999999999999999999", "name": "x"})()

    monkeypatch.setattr(sd, "client", type("C", (), {"user": object(), "id": 2})())

    async def _send(prompt):  # must not be reached
        raise AssertionError("an unknown id must not reach the assistant")

    monkeypatch.setattr(sd, "_send_to_phantasma", _send, raising=False)

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        sd.on_message(FakeMessage())
    )
