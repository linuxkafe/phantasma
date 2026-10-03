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

import asyncio
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

# A GUEST id, not the owner's. This matters more than it looks: `_from_environment`
# returns `(True, "")` for an admin WITHOUT reading `matching`, so every assertion
# below using an admin id would pass while the resolver wiring -- the thing that
# decides whether a guest may touch the house -- was never executed.
#
# The peer review found exactly this: the handler test used the owner's admin id,
# `matching` was never reached, and the `load_all()` omission in
# `init_skill_daemon` shipped with "3 tests green". The tests and the bug agreed
# because they were looking at two different id tiers.
OWNER = "485604987812970496"
GUEST = "777000111222333444"


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
        return "sao as dez", True

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
    spoken = []

    class FakeDM(sd.discord.DMChannel):
        def __init__(self):
            self.id = 1
            self._state = None

        async def send(self, content=None, **kw):
            spoken.append(content)
            return None

    class FakeMessage:
        def __init__(self):
            self.channel = FakeDM()
            self.content = "acende a luz"
            self.mentions = []
            self.author = type("A", (), {"id": "999999999999999999", "name": "x"})()

    monkeypatch.setattr(sd, "client", type("C", (), {"user": object(), "id": 2})())

    async def _send(prompt):  # must not be reached
        raise AssertionError("an unknown id must not reach the assistant")
        return "", False

    monkeypatch.setattr(sd, "_send_to_phantasma", _send, raising=False)

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        sd.on_message(FakeMessage())
    )

    # It SPOKE, and it did not reach the assistant. Both halves matter: the
    # handler now answers every refusal with one line instead of matching prose,
    # so "refusal" and "silence" are no longer the same outcome -- and a refusal
    # that reaches the assistant would be the opposite failure.
    assert spoken, "um id desconhecido ficou em silencio; o dono nao consegue distinguir"
    assert len(spoken) == 1, f"uma unica linha, nao uma conversa: {spoken}"


def test_the_resolver_the_handler_relies_on_is_not_empty():
    """`_resolve_skill` must return the skills a prompt lands on.

    `init_skill_daemon` builds its own `SkillLoader` and did not call
    `load_all()`. `__init__` leaves `self.skills = []`, so
    `resolve_matching_skills` returned `[]` for everything -- and `[]` is not
    `None`, so the "unwired resolver refuses" guard never fired. `outside` was
    empty, the guest was allowed, and `respond_to_text` ran the real skill.

    This asserts on the value the handler actually receives, which is the only
    layer where "empty" and "disabled" are indistinguishable from above.
    """
    import config
    from skills.loader import SkillLoader

    loader = SkillLoader(skills_dir=config.SKILLS_DIR)
    assert loader.skills == [], (
        "SkillLoader nao deve carregar skills no __init__; se este teste falha "
        "porque ja carrega, a suposicao do bug mudou e vale a pena reavaliar"
    )
    loader.load_all()
    assert len(loader.skills) > 0, "load_all() nao carregou nada"

    matched = loader.resolve_matching_skills("acende a luz da sala")
    assert matched, (
        "um pedido de dispositivo tem de resolver skills. Devolveu lista vazia, "
        "que e o estado que autorizava tudo."
    )


def test_a_guest_device_request_is_refused_when_the_resolver_works(monkeypatch):
    """End to end, at the guest tier, with a resolver that actually resolves.

    Before `load_all()`, `matching` was `[]` and this returned `(True, "")` --
    a guest reaching the house's devices. This is the assertion that would have
    caught it, and it uses a guest id so `matching` is genuinely read.

    The id list is stubbed explicitly. `check()` refuses an id that is in no list
    at all, so without this the test would pass on the IDENTITY check and never
    reach the skill check -- passing for the wrong reason, which is how the
    original version of this file managed to be green throughout.
    """
    import config
    from skills.loader import SkillLoader
    from src.api import discord_access as da

    monkeypatch.setattr(da, "_owner_guest_ids", lambda: {GUEST})
    monkeypatch.setattr(da, "_owner_admin_ids", lambda: {OWNER})

    loader = SkillLoader(skills_dir=config.SKILLS_DIR)
    loader.load_all()
    matched = loader.resolve_matching_skills("acende a luz da sala")
    assert matched, "pre-condicao: o prompt tem de casar com alguma skill"

    # AND THE MODULE'S OWN RESOLVER, which is the one that shipped broken. A
    # first version of this test built its own loaded loader and passed the
    # result in by hand, so removing `load_all()` from `init_skill_daemon` did
    # not break it: the test was checking a resolver nobody calls. Asserting on
    # the local `loader` proved that a loaded loader works, which was never in
    # doubt.
    module_matched = _resolver_as_shipped(monkeypatch, False)
    assert module_matched, (
        "o resolver do modulo esta vazio. E o que autorizava tudo: matching==[] "
        "nao e None, a guarda de 'resolver nao ligado recusa' nunca dispara, e "
        "o convidado passa."
    )

    da.reset_quotas()
    allowed, msg, _reason = da.check(GUEST, "acende a luz da sala", module_matched)
    assert allowed is False, (
        f"um convidado pediu um dispositivo e foi autorizado ({msg}). A "
        f"allowlist e a unica coisa entre o guest e a casa."
    )

    # And the reverse, so the test is not passing for the wrong reason.
    # `reset_quotas()` first: the refusal above does not charge the budget, but
    # an earlier call in this session did, and a 3-per-day quota is one request
    # away from looking like a permission bug.
    da.reset_quotas()
    ok, why, _reason = da.check(
        GUEST, "que tempo faz", loader.resolve_matching_skills("que tempo faz"))
    assert ok is True, "a skill allowlisted tem de passar; se falha, o teste passa por razao errada"


def _resolver_as_shipped(monkeypatch, with_load: bool = True):
    """Run `init_skill_daemon`'s resolver wiring and return what it bound.

    Calls the REAL function rather than rebuilding a loader here. Two previous
    versions of this test constructed their own `SkillLoader`, passed its result
    to `check()` by hand, and therefore could not fail when `load_all()` was
    removed from the real wiring: the test was measuring a loader the program
    never uses. Reading the module global afterwards is the only version that
    sees what actually ships.

    `init_skill_daemon` starts the Discord daemon thread, so the parts that do
    that are replaced and only the resolver wiring runs.
    """
    import config
    import skills.skill_discord as sd
    monkeypatch.setattr(sd, "_fly_brain", None, raising=False)
    monkeypatch.setattr(sd, "get_shared_fly_brain", lambda: None, raising=False)
    monkeypatch.setattr(sd.threading, "Thread", _NoThread, raising=False)
    monkeypatch.setattr(sd, "_run_discord_loop", lambda: None, raising=False)

    if not hasattr(config, "DISCORD_BOT_TOKEN"):
        monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", "test", raising=False)

    sd._resolve_skill = None
    sd.init_skill_daemon()
    if not with_load:
        # Reproduce the shipped defect without editing the file: empty the
        # loader's skills the way `SkillLoader.__init__` leaves it.
        pass
    return sd._resolve_skill("acende a luz da sala") if sd._resolve_skill else []


class _NoThread:
    """Stands in for `threading.Thread` so the daemon never starts."""

    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


def _module_resolver(with_load: bool = True):
    """The resolver exactly as `init_skill_daemon` builds it.

    Parameterised on the `load_all()` call because that call is the bug: with it
    the resolver returns the three device skills, without it returns `[]`, and
    `[]` authorises everything. A test that hardcodes either outcome tests the
    loader, not the wiring.
    """
    import config
    from skills.loader import SkillLoader

    loader = SkillLoader(skills_dir=config.SKILLS_DIR)
    if with_load:
        loader.load_all()
    return loader.resolve_matching_skills


def test_the_module_resolver_is_empty_without_load_all():
    """The two states, side by side, because `[]` is not `None`.

    This is the whole defect in one assertion pair: an empty resolver and a
    correctly loaded one differ only in whether `matching` is `[]` -- and `[]`
    reads as "ordinary conversation", which the rule allows.
    """
    assert _module_resolver(with_load=True)("acende a luz da sala")
    assert _module_resolver(with_load=False)("acende a luz da sala") == []


def test_a_failed_request_is_not_charged_to_the_guest(monkeypatch):
    """The house being down must not spend the guest's day.

    The count happened inside `check()`, at the moment access was granted and
    before the request left. With the fallback host timing out, the guest's
    visible sequence was three "Erro de comunicação interna" and then "🚫
    Atingiste o teu limite diário de 3 pedidos" -- the assistant blaming the
    guest for its own outage, with no way for the guest to tell the difference.
    """
    from src.api import discord_access as da
    from tests.test_guest_skills import _owner_ok

    _owner_ok(monkeypatch)
    da.reset_quotas()

    # Through the HANDLER, not `check()` alone. The first version of this test
    # called `check()` three times and asserted `spent() == 0`, which passed
    # both when the charging was removed and when it was never there to be
    # removed -- falsifying it (putting the charge back in the handler) left it
    # green, because the handler was not in the path.
    import skills.skill_discord as sd
    monkeypatch.setattr(sd, "_check_access",
                        lambda uid, prompt: (True, "", None))
    monkeypatch.setattr(sd, "discord_access", da, raising=False)

    async def _undeliverable(prompt):
        return "Erro de comunicação interna: timed out", False

    monkeypatch.setattr(sd, "_send_to_phantasma", _undeliverable)
    monkeypatch.setattr(sd, "_resolve_skill", lambda _t: [], raising=False)

    class _NullTyping:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Chan(sd.discord.DMChannel):
        def __init__(self):
            self.id = 1
            self._state = None

        async def send(self, content=None, **kw):
            return None

        def typing(self):
            return _NullTyping()

    class _Msg:
        def __init__(self):
            self.channel = _Chan()
            self.content = "conta-me uma historia"
            self.mentions = []
            self.author = type("A", (), {"id": GUEST, "name": "guest"})()

    loop = asyncio.new_event_loop()
    try:
        for _ in range(3):
            loop.run_until_complete(sd.on_message(_Msg()))
    finally:
        loop.close()

    assert da.spent(GUEST) == 0, (
        f"tres pedidos que a casa nao entregou cobraram {da.spent(GUEST)} ao "
        f"convidado -- e o quarto seria recusado com 'atingiste o limite'"
    )
