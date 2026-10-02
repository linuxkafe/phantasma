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
    """A guest, a allowlist por omissão, e contadores limpos.

    The two policy values are set on `config` AND on the settings store, because
    production reads the store first. Pinning only the attribute would leave the
    tests exercising a fallback branch that production never takes -- and the
    previous version of this file pinned only the attribute, which is how a test
    suite can be green about a path that does not run on the host.
    """
    monkeypatch.setattr(config, "DISCORD_ADMIN_USERS", [], raising=False)
    monkeypatch.setattr(config, "DISCORD_STANDARD_USERS", [GUEST], raising=False)
    monkeypatch.setattr(config, "DISCORD_DAILY_LLM_LIMIT", 3, raising=False)
    _PINNED.clear()
    _set_setting(monkeypatch, "GUEST_SKILLS_ALLOWED", "weather,calculator")
    _set_setting(monkeypatch, "DISCORD_DAILY_LLM_LIMIT", "3")
    da.reset_quotas()
    yield
    _PINNED.clear()
    da.reset_quotas()


_PINNED: dict[str, str] = {}


def _set_setting(monkeypatch, key: str, value: str) -> None:
    """Make `settings_store.get_setting` return `value` for `key`, keeping the rest.

    It accumulates into a module dict and re-pins on every call. The first
    version closed over a single `key`, so pinning two keys in the fixture meant
    the second call silently unpinned the first -- and the symptom arrived four
    tests later as a quota that refused arithmetic. A helper that forgets what it
    was told two lines ago is worse than no helper.
    """
    from src import settings_store

    _PINNED[key] = value
    monkeypatch.setattr(
        settings_store,
        "get_setting",
        lambda k, default=None: _PINNED.get(k, default),
    )


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


# --- o contrato de onde a politica e lida ---------------------------------
#
# Duas vezes nesta sessao um `str.replace` cujo padrao ja nao existia falhou em
# silencio: o ficheiro ficou por escrever, o teste continuava verde porque
# exercitava o ramo de fallback, e o unico sintoma foi um `allowed_guest_skills`
# a devolver `[]` quando `config.GUEST_SKILLS_ALLOWED` deixou de existir.
#
# Nada num teste de comportamento apanha isso. Estes tres apanham.


def test_the_policy_is_not_read_from_config_py():
    """`config.py` esta congelado pelo deploy e nao pode levar a politica.

    `deploy.sh` deixa `config.py` fora de SYNC_DIRS e de TOP_LEVEL_SYNC e
    obriga a que seja byte-identico nas duas arvores. Um setting novo la nunca
    chegaria a producao pelo script -- e copia-lo a mao e o que o script existe
    para impedir. Se este teste comecar a falhar, a resposta nao e adicionar o
    campo ao config.py: e usar a loja.
    """
    # Nos dois sitios onde um setting novo aparece no config.py: o campo do
    # dataclass e a exportacao de modulo. A primeira versao deste teste so
    # olhava para o segundo -- e reintroduzir o campo passou, porque "campo" e
    # "atributo" nao sao a mesma coisa e o teste so sabia de uma.
    assert not hasattr(config, "GUEST_SKILLS_ALLOWED"), (
        "GUEST_SKILLS_ALLOWED voltou a config.py. O deploy trata esse ficheiro "
        "como congelado: a politica tem de ser lida de settings_store."
    )
    #
    # So `guest*`. `discord_daily_llm_limit` fica de fora de proposito: esse
    # campo ja existe no config.py de producao, e nao mexer num campo que a
    # instalacao ja tem e o que mantem as arvores identicas. A loja e lida
    # primeiro, portanto um owner que o defina em /admin/config ganha a esse
    # valor; o `.env` continua a valer para quem nunca passou pela pagina.
    #
    # A primeira versao deste assert era larga demais e falhava com um campo
    # pre-existente, que e a forma de um teste de arquitectura acabar odiado
    # por coisas que nao e culpa dele.
    leaked = [
        name
        for name in dir(getattr(config, "config", object()))
        if "guest" in name.lower()
    ]
    assert not leaked, f"config.py voltou a declarar a politica: {leaked}"


def test_the_allowlist_survives_a_store_that_says_nothing():
    """Uma loja vazia tem de dar a default, nao uma lista vazia.

    "Sem default" e "ninguem pode usar nada" produzem o mesmo valor e
    comportamentos opostos. Sem a default, uma base de dados nova tirava o
    clima e a calculadora a todos os convidados sem ninguem reparar.
    """
    from src import settings_store

    original = settings_store.get_setting
    settings_store.get_setting = lambda k, default=None: default
    try:
        assert "weather" in da.allowed_guest_skills()
        assert da.check(GUEST, "que tempo faz", ["skill_weather"])[0]
    finally:
        settings_store.get_setting = original


def test_a_limit_the_owner_never_set_keeps_the_configured_one():
    """O `.env` ainda serve, so nao manda.

    `DISCORD_DAILY_LLM_LIMIT` ja existia em `config.py` e pode estar no `.env` de
    uma instalacao. Um valor posto a mao la nao pode ser apagado por uma loja
    vazia -- senao mudar de instalacao passa a apagar um limite.
    """
    from src import settings_store

    original = settings_store.get_setting
    settings_store.get_setting = lambda k, default=None: default
    try:
        assert da._daily_limit() == 3
    finally:
        settings_store.get_setting = original


# --- a pagina: ids e caixas -----------------------------------------------


def test_unticking_every_box_turns_access_off(monkeypatch):
    """A lista vazia e uma RESPOSTA do dono, nao um campo em falta.

    So as caixas marcadas sao submetidas. Desmarcar a ultima produz uma lista
    vazia, e lista vazia e o dono a dizer "nao pode usar nada". Se a leitura a
    tratar como "nunca definido", o_owner recebe a allowlist por omissao no
    instante exacto em que a tirou -- e a porta fecha-se abrindo.

    E o inverso de um erro subtil: nenhum pedido e recusado, nada avisa, e a
    unica evidencia e um convidado a perguntar o tempo numa casa onde o dono
    acabou de fechar a porta.
    """
    from src import settings_store

    monkeypatch.setattr(settings_store, "get_setting", lambda k, default=None: "")
    assert da.allowed_guest_skills() == [], "uma lista vazia voltou a dar a default"
    assert not da.check(GUEST, "que tempo faz", ["skill_weather"])[0]

    monkeypatch.setattr(settings_store, "get_setting", lambda k, default=None: None)
    assert "weather" in da.allowed_guest_skills(), "a default desapareceu"


def test_guest_ids_come_from_the_admin_page(monkeypatch):
    """Os ids sao do dono e vivem na pagina, nao num ficheiro so ele abre.

    `config.py` esta congelado e o `.env` e um ficheiro que o dono abre a mao --
    nenhuma das duas e um sitio para "quem deixo entrar". A lista que o dono
    edita na pagina conta, e o `.env` continua a valer para quem nunca passou
    por ela.
    """
    from src import settings_store

    monkeypatch.setattr(config, "DISCORD_STANDARD_USERS", [111], raising=False)
    monkeypatch.setattr(config, "DISCORD_ADMIN_USERS", [], raising=False)
    values = {"DISCORD_STANDARD_USERS": "222,333"}
    monkeypatch.setattr(
        settings_store, "get_setting", lambda k, default=None: values.get(k, default)
    )
    assert "222" in da._owner_guest_ids()
    # The two lists are a UNION. The page was going to shadow the `.env`, which
    # means the first time the owner saved it every id he had in `.env` and did
    # not retype became disallowed at once -- with no message, because a refused
    # guest is silent by design. An admin page that locks people out as a side
    # effect of being used is worse than one that forgets.
    assert da.check(111, "bom dia", [])[0], "o .env deixou de valer ao guardar na pagina"
    assert da.check(222, "bom dia", [])[0], "o id posto na pagina nao chegou"
    # An id on neither list is refused before the quota is consulted.
    assert not da.check(999, "bom dia", [])[0]


def test_a_claimed_profile_id_cannot_outrank_the_owner_list(monkeypatch):
    """`/perfil` deixa qualquer conta registada reclamar um id.

    Um id nos Dispositivos e in the owner's list is allowed whatever a profile
    says. A list is the owner's decision and a claim is not; the reverse -- a
    claim handing out access -- would make the page a way in.
    """
    from src import settings_store

    monkeypatch.setattr(config, "DISCORD_STANDARD_USERS", [], raising=False)
    monkeypatch.setattr(config, "DISCORD_ADMIN_USERS", [], raising=False)
    monkeypatch.setattr(
        settings_store,
        "get_setting",
        lambda k, default=None: None if k.endswith("_ALLOWED") else "111",
    )
    assert da.check("111", "que tempo faz", ["skill_weather"])[0]


# --- a pergunta que o dono fez --------------------------------------------


def test_the_language_model_is_still_there_for_a_guest(monkeypatch):
    """Resposta a "o LLM fica disponivel?". Sim, e ate ao limite.

    Nenhuma skill casa com "conta-me uma historia", portanto o pedido passa, e
    e o LLM que responde. E o que um convidado faz a maior parte do tempo, e a
    restricao nao lhe tira a conversa -- corta-lhe as ferramentas.

    O outro lado da moeda, que e o que vale saber: um pedido que case com uma
    skill nao autorizada e recusado MESMO que a conversa seja inocente. "como esta
    a luz da sala" e recusado, porque toca em `tuya`. A regra e "qualquer skill
    tocada fora da lista recusa", e nao "o convite foi recusado" -- e por isso
    vale a pena a allowlist ser feita com os gatilhos na mao, e nao so com o
    nome das skills.
    """
    from src import settings_store

    monkeypatch.setattr(settings_store, "get_setting", lambda k, default=None: {
        "GUEST_SKILLS_ALLOWED": "weather,calculator",
        "DISCORD_DAILY_LLM_LIMIT": "3",
        "DISCORD_STANDARD_USERS": GUEST,
    }.get(k, default))

    # No skill matches: conversation, and it costs the budget.
    da.reset_quotas()
    allowed, _ = da.check(GUEST, "conta-me uma historia", [])
    assert allowed, "o convidado perdeu a conversa"

    # Four in a row, and the fifth is over the line.
    for _ in range(3):
        da.check(GUEST, "conta-me outra", [])
    allowed, msg = da.check(GUEST, "conta-me mais uma", [])
    assert not allowed and "limite" in msg

    # And an innocent question that happens to touch a device skill is refused.
    allowed, msg = da.check(GUEST, "como esta a luz da sala", ["skill_tuya"])
    assert not allowed, "uma conversa inocente passou por cima da restricao"


def test_ids_from_config_are_not_stringified():
    """`config.py` guarda os ids como `list[int]`, e `str()` disso e lixo.

    `str([111, 222])` -> `"[111, 222]"`, e partido por virgulas da os ids
    `"[111"` e `"222]"`: dois ids que nao existem em conta nenhuma, numa lista
    cuja funcao e nomear ids reais. O sintoma e o pior possivel -- nao ha
    mensagem nenhuma, so ninguem e nunca autorizado, e a culpa parece ser da
    pagina de convidados.

    A suite nao apanhou isto; um probe de uma linha apanhou. Vale a pena dizer
    que o teste acima nao o apanhou, porque e o que torna o probe parte do
    trabalho e nao um extra.
    """
    from src.api import discord_access

    original = getattr(config, "DISCORD_STANDARD_USERS", None)
    config.DISCORD_STANDARD_USERS = [111, 222]
    try:
        assert discord_access._ids_in([111, 222]) == {"111", "222"}
        ids = discord_access._owner_guest_ids()
        assert "111" in ids and "222" in ids
        assert not [i for i in ids if i.startswith("[") or i.endswith("]")], (
            f"ids corrompidos na lista: {sorted(ids)}"
        )
    finally:
        config.DISCORD_STANDARD_USERS = original


def test_the_checkboxes_are_named_the_way_the_rule_compares():
    """Cada checkbox tem de se chamar exactamente como o que a regra compara.

    O valor gravado pela pagina e o nome com que a regra compara, e o nome da
    pagina vinha de `name[5:]` -- `skill_` tem SEIS caracteres, portanto
    `skill_calculator` dava "_calculator". Passou pela pagina em producao com 23
    caixas, e o efeito e silencioso: marcar uma skill guardava um nome que nunca
    casa, logo recusava tudo; e as duas skills que estavam permitidas apareciam
    por marcar enquanto estavam a funcionar.

    Um underscore inicial ao lado de uma checkbox ve-se pouco, e le-se como "a
    lista esta vazia" em vez de "a lista esta errada".

    Este teste existe porque o bug passou no deploy, na suite, e no "deploy OK".
    O que o apanhou foi ler os valores que a pagina real emitia.
    """
    from src.api import admin

    # The names the loader produces...
    assert admin._skill_short_name("skill_calculator") == "calculator"
    assert admin._skill_short_name("skill_bareos") == "bareos"
    # ...and one that does not, which must be left alone rather than mangled.
    assert admin._skill_short_name("calculator") == "calculator"

    # And the round trip the page performs on the way in.
    assert "calculator".removeprefix("skill_").strip() in "calculator"
    assert "_calculator".removeprefix("skill_").strip() == "_calculator"

    # So a ticked box cannot produce a name the rule will not match.
    for name in ("calculator", "weather"):
        short = admin._skill_short_name(f"skill_{name}")
        assert short in da.allowed_guest_skills(), (
            f"a caixa para {name} gravaria um nome que a regra nao reconhece"
        )
