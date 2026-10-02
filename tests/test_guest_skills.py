"""O que um convidado pode tocar, e o que isso custa.

Duas decisões do dono, em 2026-10-02:

* um convidado **nao entra**. Nao ha login, nem sessao, nem interface. So
  existe no Discord, e a identidade e o `author.id` que o Discord poe na
  mensagem. Ver o porquê em `discord_access`.
* as skills a que um convidado tem acesso sao uma **allowlist**, definida na
  administracao. Nao uma blocklist: o inventario real sao 24 skills e nomear as
  perigosas e um trabalho que nunca acaba.
* a quota **contabiliza tudo**.

O que estes testes fixam é a parte que falhou antes. `ALLOWED_SKILL_KEYWORDS` era
uma lista de sub-cadeias do prompt que parecia uma allowlist e não restringia
nada: só decidia se o pedido gastava quota, e `+ - * /` estavam na lista, o que
tornava "conta-me uma historia" ilimitado. Um teste que verifica que o acesso
depende do texto do pedido é um teste que não verifica o acesso.

Aqui a decisão é tomada sobre **qual skill o pedido toca**, resolvida antes de
gastar seja o que for.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402
from src.api import discord_access as da  # noqa: E402

GUEST = "111222333444555666"


@pytest.fixture(autouse=True)
def guest_env(monkeypatch):
    """A guest, a allowlist por omissão, e contadores limpos."""
    monkeypatch.setattr(config, "DISCORD_ADMIN_USERS", [], raising=False)
    monkeypatch.setattr(config, "DISCORD_STANDARD_USERS", [GUEST], raising=False)
    monkeypatch.setattr(config, "GUEST_SKILLS_ALLOWED", "weather,calculator", raising=False)
    monkeypatch.setattr(config, "DISCORD_DAILY_LLM_LIMIT", 3, raising=False)
    da.reset_quotas()
    yield
    da.reset_quotas()


# --- acesso ---------------------------------------------------------------


def test_guest_may_reach_an_allowlisted_skill():
    allowed, _ = da.check(GUEST, "que tempo faz", ["skill_weather"])
    assert allowed


def test_guest_is_refused_a_device_skill():
    """A restricao que o dono pediu: nao activar dispositivos.

    `skill_chacon` e `skill_tuya` sao as que mudam a casa. Nenhuma das duas pode
    estar na allowlist por omissao, e o valor por omissao nao as inclui.
    """
    allowed, msg = da.check(GUEST, "acende a luz da sala", ["skill_chacon", "skill_tuya"])
    assert not allowed
    assert "luz" not in msg.lower() or "acesso" in msg.lower()


def test_one_allowed_skill_does_not_vouch_for_another():
    """A lista é de acesso, não de一想 "uma está dentro, a outra está fora".

    `execute_skill` percorre as skills casadas e fica com a primeira que
    responde. Se a checagem fosse feita só pela primeira, um convidado recusado
    pela segunda seria entregue à segunda. Qualquer skill fora da lista recusa,
    seja qual for a sua posição.
    """
    allowed, _ = da.check(GUEST, "x", ["skill_weather", "skill_tuya"])
    assert not allowed

    allowed, _ = da.check(GUEST, "x", ["skill_tuya", "skill_weather"])
    assert not allowed


def test_the_prefixed_name_is_the_same_skill():
    """O owner escreve `weather`; o loader chama-lhe `skill_weather`.

    Nao se obriga o dono a aprender o nome interno das skills para poder
    configurar a allowlist.
    """
    assert "weather" in da.allowed_guest_skills()
    allowed, _ = da.check(GUEST, "que tempo faz", ["skill_weather"])
    assert allowed


def test_allowlist_is_read_every_time_not_cached():
    """Nao ha segunda copia da politica num global de modulo.

    Nao por frescura -- uma mudanca em /admin/config so chega ao processo no
    arranque seguinte, como qualquer valor desta pagina. Mas um global aqui seria
    uma segunda copia da politica com a sua propria vida, e a pergunta "qual
    delas esta em vigor" e a pergunta que vira uma mudanca de permissao numa
    discussao.
    """

    config.GUEST_SKILLS_ALLOWED = "weather"
    original = config.GUEST_SKILLS_ALLOWED
    try:
        assert not da.check(GUEST, "x", ["skill_calculator"])[0]
        config.GUEST_SKILLS_ALLOWED = "weather,calculator"
        assert original != config.GUEST_SKILLS_ALLOWED
        assert da.check(GUEST, "x", ["skill_calculator"])[0]
    finally:
        config.GUEST_SKILLS_ALLOWED = original


# --- a recusa de "nao sei" ------------------------------------------------


def test_unresolvable_request_is_refused_not_allowed():
    """`matching=None` e "nao consegui resolver", e recusa.

    Se None significasse "nenhuma skill casou", seria conversa normal e passava.
    Uma falha de wiring passaria a ser o que abre a porta -- e a unica coisa que
    separa um convidado da casa e precisamente esta lista.
    """
    allowed, _ = da.check(GUEST, "acende a luz", None)
    assert not allowed


def test_an_empty_match_list_is_ordinary_conversation():
    """Distinguir "nenhuma skill casou" de "nao resolvi" e o que torna a recusa acima util."""
    assert da.check(GUEST, "bom dia", [])[0]


# --- quota ----------------------------------------------------------------


def test_everything_costs_the_budget():
    """Decisao do dono: contabiliza tudo.

    Inclusive o que uma skill autorizada responde. Isto e o que fecha o buraco do
    hifen: `skill_calculator` casa com "conta-me uma historia" e devolve None, ou
    seja, quem responde e o LLM. Uma isencao baseada em "casou" nao distingue
    "respondeu" de "recusou e o LLM apanhou", e a diferenca e a quota inteira.
    """
    for i in range(3):
        allowed, msg = da.check(GUEST, "que tempo faz", ["skill_weather"])
        assert allowed, f"pedido {i + 1} recusado cedo demais: {msg}"
    allowed, msg = da.check(GUEST, "que tempo faz", ["skill_weather"])
    assert not allowed
    assert "3" in msg


def test_a_hyphen_is_not_a_free_pass():
    """O bypass exacto que o teste antigo nao via.

    `+`, `-`, `*` e `/` estavam em ALLOWED_SKILL_KEYWORDS, portanto qualquer
    pedido com um hifen nao gastava quota. O request e sempre cobrado; nao ha
    caminho no texto do pedido que o evite.
    """
    for request in (
        "conta-me uma historia",
        "conta me uma historia",
        "conta-me: 10/10",
        "conta-me *rapido*",
    ):
        da.reset_quotas()
        passed = sum(da.check(GUEST, r, ["skill_calculator"])[0] for r in [request] * 8)
        assert passed == 3, f"{request!r} passou {passed}/8 em vez de 3/8"


def test_a_refused_skill_does_not_spend_the_budget():
    """Negar acesso nao e um pedido. Quem e recusado nao paga com a quota dele."""
    for _ in range(10):
        assert not da.check(GUEST, "acende a luz", ["skill_tuya"])[0]
    assert da.check(GUEST, "bom dia", [])[0], "recusar devices esgotou a quota"


def test_quota_is_per_guest():
    """Cada convidado tem o seu contador. Esgotar o de um nao tranca o outro.

    Both ids have to be on the owner's list -- an id that is not there is refused
    before the quota is even consulted, which is the correct order and the
    reason the first version of this test asserted nothing useful.
    """
    other = "999888777666555444"
    config.DISCORD_STANDARD_USERS = [GUEST, other]
    for _ in range(3):
        da.check(GUEST, "x", [])
    assert not da.check(GUEST, "x", [])[0], "o contador do primeiro nao esgostou"
    assert da.check(other, "x", [])[0], "o segundo convidado herdou a quota do primeiro"


# --- o que este ficheiro NAO fixa, e porque -------------------------------


@pytest.fixture(scope="module")
def loader():
    """The real loader, with the two meter skills given a device.

    Not a fake: the whole point of these tests is which skills a Portuguese
    sentence *matches*, and a fake trigger list would only prove that the fake
    behaves as the fake was written to behave. `CLOOGY_DEVICES` / `TUYA_DEVICES`
    are set because both `handle`s return None without a device, so without them
    every meter question would look like it reaches nothing.
    """
    from skills.loader import SkillLoader

    saved = (
        getattr(config, "CLOOGY_DEVICES", None),
        getattr(config, "TUYA_DEVICES", None),
    )
    config.CLOOGY_DEVICES = {"forno": "a", "casa": "b"}
    config.TUYA_DEVICES = {"luz": "x"}
    try:
        loader_ = SkillLoader("skills")
        loader_.load_all()
        yield loader_
    finally:
        config.CLOOGY_DEVICES, config.TUYA_DEVICES = saved


# The owner chose (a): narrow the triggers that collided, rather than loosen the
# rule. So the test is a pair, and both halves matter -- narrowing a trigger to
# fix a collision is the easiest way in this codebase to break the voice.


@pytest.mark.parametrize(
    "prompt",
    [
        "quanto gastou o total",
        "quanto gastou a casa",
        "quanto consome o forno",
        "quanto consumiu a casa",
        "quanto esta no wc",
        "quanto marca o sensor",
        "leitura do forno",
        "lista do cloogy",
    ],
)
def test_the_owner_still_reaches_the_meters(loader, prompt):
    """Nothing the owner says every day may stop reaching the meter skills.

    `quanto` was removed from both skills. Before removing it, "quanto gastou o
    total" matched ONLY on that word -- `total` was never a trigger, though the
    handle has always treated it as an alias for the whole-house reading. So the
    obvious one-word deletion would have sent a daily question to the language
    model, which answers fluently and is wrong. That is the regression this
    half of the pair exists to catch, and `quanto esta` is spelled without the
    accent on purpose: `matches` does not fold accents and the text comes from
    the STT.
    """
    matched = loader.resolve_matching_skills(prompt.lower())
    assert {"skill_cloogy", "skill_tuya"} & set(matched), (
        f"{prompt!r} deixou de chegar aos medidores: casou {matched}"
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "quanto é 2+2",
        "quanto e 2+2",
        "quanto é 10/2",
        "calcula 3 vezes 4",
        "quanto é o geral",
        "quanto e a capital de França",
        "2+2",
    ],
)
def test_arithmetic_reaches_only_the_calculator(loader, prompt):
    """A guest asking an arithmetic question must not be refused.

    The guest rule is "any matched skill outside the allowlist refuses", and
    `calculator` is on the list, so one stray `contains` match on a device skill
    turned a sum into a refusal naming a light bulb. `cloogy` and `tuya` both
    declared the bare trigger `quanto`; it is gone, replaced by verbs that
    cannot appear in a sum.

    `2+2` is here on purpose. That one matches on the operator `+`, which is
    where the old `ALLOWED_SKILL_KEYWORDS` came from: it was a hand-kept copy of
    the calculator's triggers, which is how a keyword list came to be mistaken
    for an allowlist.
    """
    matched = loader.resolve_matching_skills(prompt.lower())
    outside = [m for m in matched if m not in ("skill_calculator",)]
    assert not outside, f"{prompt!r} colide com {outside}"
