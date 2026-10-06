"""O que a skill Zigbee faz quando o broker, o coordinator e o device falham.

O caminho feliz e um exercicio: liga o broker, o device responde. O que se
testa aqui e o contrario -- os tres estados em que a skill esta errada sem
dizer que esta errada:

* o broker nao responde, e a skill **nao** diz "esta desligado";
* o comando foi aceite pelo broker mas o device nao confirmou, e a skill
  **nao** diz "ja desliguei" -- o broker aceitar um pedido nao e o device o
  ter feito, entre os dois ha a rede Zigbee, que falha em silencio;
* "desliga" contem "liga", e um pedido de desligar que acaba por ligar e o
  pior defeito possivel numa skill que comuta um oven.

Mais um, que e o motivo da skill ser class-based: `PRIORITY` escrita ao nivel
do modulo e ignorada. Sem isso, `forno` -- que e DEVICE_NOUN de
DEVICE_NOUN de `skill_tuya`, as duas legacy a prioridade 0 -- Resolve-se pela
ordem alfabetica do ficheiro.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from skills import skill_zigbee as sz  # noqa: E402
from skills.base import SkillContext  # noqa: E402

DEVICES = {"forno": {"friendly_name": "forno", "kind": "plug"}}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """Um device configurado, broker unreachable, e sem cliente herdado.

    O broker e deixado em porta fechada de proposito: quase todos os testes
    precisam que a ligacao falhe, e um broker de verdade a transformar o
    "nao configured" num "funciona" consoante a ordem de execucao seria um
    teste que mente sobre o sistema.
    """
    monkeypatch.setenv("ZIGBEE_DEVICES_JSON", json.dumps(DEVICES))
    monkeypatch.setenv("MQTT_HOST", "127.0.0.1")
    monkeypatch.setenv("MQTT_PORT", "1")  # nada escuta aqui
    monkeypatch.setattr(sz, "_client", None)
    sz._states.clear()
    yield
    sz._client = None
    sz._states.clear()


def skill():
    return sz.ZigbeeSkill(SkillContext())


# --- a alcunha e a configuracao -------------------------------------------


def test_matches_the_nickname():
    assert skill().matches("liga o forno")


def test_folds_accents_both_ways():
    """O STT nao acenta e o dono escreve acentuado. Os dois tem de chegar."""
    monkey = skill()
    assert monkey.matches("liga o forno")
    assert monkey.matches("estado do FORNO")
    # uma alcunha acentuada, dita sem acento
    import os

    old = os.environ.get("ZIGBEE_DEVICES_JSON")
    os.environ["ZIGBEE_DEVICES_JSON"] = json.dumps({"lâmpada": {"kind": "plug"}})
    try:
        assert skill().matches("liga a lampada")
    finally:
        if old is None:
            os.environ.pop("ZIGBEE_DEVICES_JSON", None)
        else:
            os.environ["ZIGBEE_DEVICES_JSON"] = old


def test_declines_when_no_device_is_named(monkeypatch):
    """`None`, nao uma frase.

    O loader percorre a lista toda e fica com a primeira resposta nao vazia.
    Uma frase inventada aqui significa que a skill seguinte -- `skill_tuya` tem
    `forno` nos seus DEVICE_NOUNS -- nunca e consultada.
    """
    monkeypatch.setenv("ZIGBEE_DEVICES_JSON", json.dumps({"frigorifico": {}}))
    assert skill().handle("liga o forno") is None


def test_unconfigured_skill_is_invisible(monkeypatch):
    """Sem dispositivos, a skill nao casa com nada -- e de proposito.

    Uma skill que capturasse "liga o forno" para dizer "nao ha dispositivos"
    tiraria a quem de facto responde a unica resposta que existe, para a trocar
    por "nao ha dispositivos" quando a resposta verdadeira e "ainda nao foi
    emparelhado".
    """
    monkeypatch.setenv("ZIGBEE_DEVICES_JSON", "")
    s = skill()
    assert s.matches("liga o forno") is False
    assert s.handle("liga o forno") is None
    assert s.get_status_for_device("forno") == {"state": "unreachable"}


def test_bare_string_is_shorthand_for_friendly_name():
    import os

    os.environ["ZIGBEE_DEVICES_JSON"] = json.dumps({"forno": "forno"})
    try:
        assert sz._devices() == {"forno": {"friendly_name": "forno", "kind": "plug"}}
    finally:
        os.environ["ZIGBEE_DEVICES_JSON"] = json.dumps(DEVICES)


def test_broken_json_does_not_take_the_skill_down(monkeypatch):
    monkeypatch.setenv("ZIGBEE_DEVICES_JSON", "{not json")
    assert sz._devices() == {}


def test_clamp_is_refused_a_command(monkeypatch):
    """Um clamp mede. Perguntar para o desligar tem de ser recusado, nao publicado.

    Nao ha `state` num clamp: publicar um seria um comando que o device nunca
    aceita, e a skill a responder "ok" a algo que nao aconteceu.
    """
    monkeypatch.setenv(
        "ZIGBEE_DEVICES_JSON", json.dumps({"consumo": {"friendly_name": "casa", "kind": "clamp"}})
    )
    published = []
    monkeypatch.setattr(sz, "_publish", lambda *a: published.append(a) or True)
    out = skill().handle("desliga o consumo")
    assert out is not None and "sensor" in out.lower()
    assert published == []


# --- o broker ------------------------------------------------------------


def test_broker_down_is_never_reported_as_a_state():
    """O defeito que mais caro seria: um broker em baixo lido como "desligado".

    Uma tomada que nao responde e uma tomada desligada sao coisas diferentes, e
    a segunda e uma coisa em que se pode confiar. Em voz, a diferenca e ficar
    sem informacao ou com uma informacao errada; a segunda faz-se agir sobre
    ela.
    """
    out = skill().handle("estado do forno")
    assert out is not None
    assert "desligado" not in out.lower()
    assert "nao" in out.lower() or "configurados" in out.lower()


def test_publish_returns_false_when_there_is_no_client():
    assert sz._publish("forno", "ON") is False


# --- o device ------------------------------------------------------------


def test_reports_the_state_the_device_gave_not_the_one_requested(monkeypatch):
    """O device manda OFF quando foi pedido ON. O device ganha.

    Um comando aceito pelo broker nao e um comando executado. Anunciar o
    pedido em vez do estado e a mesma mentira que a skill_cloogy cometia, ao
    reportar o atuador do forno como 1000 W.
    """
    monkeypatch.setattr(sz, "_publish", lambda *a: True)
    monkeypatch.setattr(
        sz,
        "_state_for",
        lambda *a, **k: {"state": "OFF", "_seen_at": __import__("time").time()},
    )
    out = skill().handle("liga o forno")
    assert out is not None
    assert "desligado" in out.lower()


def test_command_without_a_reply_says_so(monkeypatch):
    """Aceitou, publicou, e o device nao disse nada. Nao se anuncia sucesso."""
    monkeypatch.setattr(sz, "_publish", lambda *a: True)
    monkeypatch.setattr(sz, "_state_for", lambda *a, **k: None)
    out = skill().handle("liga o forno")
    assert out is not None
    assert "desligado" not in out.lower() and "ligado" not in out.lower()


def test_desligar_is_not_ligar(monkeypatch):
    """"desliga" contem "liga". Um pedido de desligar que chegue a ligar e o
    pior defeito possivel aqui: nao e uma resposta errada, e um oven ligado.

    O teste ve o que foi PUBLICADO, nao a frase que saiu. A frase pode estar
    certa por acaso -- o device devolveu OFF e a descricao diz "desligado" --
    enquanto o comando errado ja tinha sido enviado para a tomada.
    """
    published = []
    monkeypatch.setattr(sz, "_publish", lambda friendly, state: published.append(state) or True)
    monkeypatch.setattr(sz, "_state_for", lambda *a, **k: {"state": "OFF"})

    skill().handle("desliga o forno")

    assert published == ["OFF"], f"published {published} for 'desliga'"


def test_ligar_publishes_on(monkeypatch):
    published = []
    monkeypatch.setattr(sz, "_publish", lambda friendly, state: published.append(state) or True)
    monkeypatch.setattr(sz, "_state_for", lambda *a, **k: {"state": "ON"})

    skill().handle("liga o forno")

    assert published == ["ON"], f"published {published} for 'liga'"


def test_describe_only_names_fields_that_exist():
    """Um plug não tem `energy`. Inventar um kWh e pior que nao dizer nada."""
    out = sz._describe("forno", {"state": "ON", "power": 1234.6})
    assert "1235 W" in out
    assert "kWh" not in out


def test_describe_reads_power_8_from_a_clamp():
    """O Z2M publica medicao electrica POR ENDPOINT.

    Um clamp no endpoint 8 manda `power_8`, nao `power`. Uma skill que so le
    `power` ve um device suportado a nao reportar nada -- indistinguivel de um
    device avariado, e e assim que um sensor que funciona parece morto.
    """
    out = sz._describe("consumo", {"power_8": 812.4}, kind="clamp")
    assert "812 W" in out, out


def test_clamp_is_not_told_it_has_unknown_state():
    """Um clamp nao tem `state` porque nao e uma coisa que se liga.

    "estado desconhecido" a um sensor que mede e uma resposta sobre a pergunta
    errada, nao sobre o device.
    """
    out = sz._describe("consumo", {"power_8": 500.0}, kind="clamp")
    assert "estado desconhecido" not in out.lower(), out
    assert not out.lower().startswith("o consumo esta estado"), out


def test_clamp_that_measured_nothing_does_not_invent_watts():
    """O CLP310 HA real: recusa haElectricalMeasurement.read com
    UNSUPPORTED_ATTRIBUTE. Manda battery e linkquality, e nao manda potencia.

    Um 0 W inventado seria pior que o Cloogy, que pelo menos era honesto sobre
    o que nao sabia.

    A segunda metade disto mudou em 2026-10-05, por pedido do dono: a frase
    tambem deixou de dizer que o device nao devolveu potencia. Nao e que o
    clamp passou a medir -- continua a recusar o atributo. E que repetir esse
    "nao mede" a cada pergunta e o sistema a anunciar as proprias limitacoes a
    alguem que perguntou pela bateria. O que o device mede e dito; o que ele
    nao mede fica em silencio.
    """
    out = sz._describe("consumo", {"battery": 62, "linkquality": 129}, kind="clamp")
    assert "W" not in out.replace("bateria", ""), out
    assert "62" in out
    assert "potencia" not in out.lower(), out
    assert "nao devolveu" not in out.lower(), out


def test_plug_still_reports_state():
    """A mudanca nao pode ter comido o caminho do plug."""
    out = sz._describe("forno", {"state": "ON"}, kind="plug")
    assert "ligado" in out.lower()
    assert "potencia" not in out.lower()


def test_describe_says_nothing_when_the_payload_is_empty():
    assert "nao respondeu" in sz._describe("forno", {})


# --- prioridade ----------------------------------------------------------


def test_priority_survives_the_loader():
    """`PRIORITY` de modulo e ignorado, e a skill que disputa a palavra e outra.

    Se esta skill deixar de ser class-based, cai a 0 e `forno` passa a
    resolver-se pela ordem alfabetica do ficheiro. Com a `skill_cloogy` removida
    resta `skill_tuya`, que tem `forno` nos DEVICE_NOUNS e e legacy a 0 -- por
    isso a comparacao agora e com ela e nao apenas "maior que zero".
    """
    from skills.loader import SkillLoader

    loader = SkillLoader("skills", SkillContext())
    loader.load_all()
    mine = [s for s in loader.skills if s.NAME == sz.NAME]
    assert len(mine) == 1, f"expected exactly one zigbee skill, got {len(mine)}"
    assert mine[0].PRIORITY == 70

    # Every legacy skill that can claim "forno" must lose. Not a fixed list:
    # a new one added later would silently take the tie-break.
    for theirs in loader.skills:
        if theirs is mine[0]:
            continue
        if "forno" in getattr(theirs, "TRIGGERS", []):
            assert theirs.PRIORITY < 70, (
                f"{theirs.NAME} can match 'forno' and outranks zigbee"
            )
    assert mine[0].matches("liga o forno")


# --- dashboard -----------------------------------------------------------


def test_dashboard_says_unreachable_rather_than_inventing_a_state():
    assert skill().get_status_for_device("forno") == {"state": "unreachable"}


def test_dashboard_reports_on_off(monkeypatch):
    monkeypatch.setattr(sz, "_state_for", lambda *a, **k: {"state": "ON", "power": 42.0})
    out = skill().get_status_for_device("forno")
    assert out["state"] == "on"
    assert out["power_w"] == 42.0


def test_dashboard_unknown_state_is_not_on_or_off(monkeypatch):
    """`unavailable` nao e `off`. Traduzir um para o outro faz o painel
    prometer um estado que o device nao deu."""
    monkeypatch.setattr(sz, "_state_for", lambda *a, **k: {"state": "unavailable"})
    assert skill().get_status_for_device("forno")["state"] == "unknown"


# --- topicos -------------------------------------------------------------


def test_state_topic_is_parsed_and_command_topics_are_not():
    class Msg:
        def __init__(self, topic, payload):
            self.topic = topic
            self.payload = payload

    sz._on_message(None, None, Msg("zigbee2mqtt/forno", b'{"state":"ON"}'))
    assert sz._states["forno"]["state"] == "ON"

    sz._on_message(None, None, Msg("zigbee2mqtt/forno/set", b'{"state":"OFF"}'))
    assert sz._states["forno"]["state"] == "ON", "a /set echo overwrote real state"


def test_bridge_state_is_not_a_device():
    class Msg:
        topic = "zigbee2mqtt/bridge/state"
        payload = b'{"state":"online"}'

    sz._on_message(None, None, Msg())
    assert sz._states == {}


def test_dashboard_reports_a_clamp_as_a_sensor_not_as_off(monkeypatch):
    """Traduzir um clamp que mede para "off" porque nao mandou `state`.

    E a mesma mudanca de grandeza que a skill_cloogy cometia ao ler o atuador
    do forno como 1000 W: fabricar um valor a partir de uma ausencia, e depois
    mostrar um interruptor no painel.
    """
    monkeypatch.setenv(
        "ZIGBEE_DEVICES_JSON",
        json.dumps({"consumo": {"friendly_name": "casa", "kind": "clamp"}}),
    )
    monkeypatch.setattr(sz, "_state_for", lambda *a, **k: {"power_8": 812.4, "battery": 62})
    out = skill().get_status_for_device("consumo")
    assert out["state"] == "sensor", out
    assert out["power_w"] == 812.4, out
    assert out["battery_pct"] == 62, out


def test_dashboard_clamp_that_measured_nothing_is_still_a_sensor(monkeypatch):
    monkeypatch.setenv(
        "ZIGBEE_DEVICES_JSON",
        json.dumps({"consumo": {"friendly_name": "casa", "kind": "clamp"}}),
    )
    monkeypatch.setattr(sz, "_state_for", lambda *a, **k: {"battery": 62})
    out = skill().get_status_for_device("consumo")
    assert out["state"] == "sensor"
    assert "power_w" not in out, "invented a power reading that never arrived"


def test_clamp_read_verb_asks_for_power_and_the_device_refuses(monkeypatch):
    """O caminho real: pede `power`, o device recusa, a skill nao inventa nada.

    Nao pode traduzir a recusa em 0 W -- e, desde 2026-10-05, tambem nao a
    anuncia. O que resta e a bateria, que e o que o clamp de facto mediu.
    """
    monkeypatch.setenv(
        "ZIGBEE_DEVICES_JSON",
        json.dumps({"consumo": {"friendly_name": "casa", "kind": "clamp"}}),
    )
    monkeypatch.setattr(sz, "_state_for", lambda *a, **k: {"battery": 62, "linkquality": 129})
    out = skill().handle("quanto esta o consumo")
    assert out is not None
    assert " W" not in out, out
    # E nao se anuncia a recusa: ver test_clamp_that_measured_nothing_does_not
    # _invent_watts, que explica a mudanca de 2026-10-05.
    assert "potencia" not in out.lower(), out


# --- aliases da casa -----------------------------------------------------
# "quanto gastou o total" reaches the clamp even though the prompt names no
# device. These aliases were `skill_cloogy._find_id_by_name`'s, and removing
# that skill without carrying them over sent a daily question to the language
# model -- which answers fluently and has no meter to read.


def test_house_alias_reaches_the_clamp_with_no_nickname_in_the_prompt(monkeypatch):
    monkeypatch.setenv(
        "ZIGBEE_DEVICES_JSON",
        json.dumps({"consumo": {"friendly_name": "casa", "kind": "clamp"}}),
    )
    s = skill()
    for prompt in ("quanto gastou o total", "quanto gastou a casa", "quanto consumiu a casa"):
        assert s.matches(prompt), prompt
    # And it resolves to the sensor, not to nothing.
    assert s._house_alias_target("quanto gastou o total") == "consumo"


def test_house_alias_never_lands_on_a_plug(monkeypatch):
    """`total` must not switch a relay.

    "quanto gastou o total" resolving onto the oven would publish an ON for a
    question about spending.
    """
    monkeypatch.setenv("ZIGBEE_DEVICES_JSON", json.dumps(DEVICES))  # plug only
    s = skill()
    assert s._house_alias_target("quanto gastou o total") is None


def test_geral_belongs_to_the_calculator(monkeypatch):
    """`geral` is ambiguous and the calculator wins it.

    "quanto e o geral" is a division. Putting `geral` in the aliases put this
    skill ahead of the calculator on that phrase. The sentence nobody says
    ("quanto gastou o geral") loses to the one everybody says.
    """
    monkeypatch.setenv(
        "ZIGBEE_DEVICES_JSON",
        json.dumps({"consumo": {"friendly_name": "casa", "kind": "clamp"}}),
    )
    s = skill()
    assert s._house_alias_target("quanto e o geral") is None
    assert s._house_alias_target("quanto gastou o total") == "consumo"


def test_aliases_do_not_revive_the_skill_when_unconfigured(monkeypatch):
    """No devices means no aliases either, or an empty install starts
    answering "quanto gastou o total"."""
    monkeypatch.setenv("ZIGBEE_DEVICES_JSON", "")
    s = skill()
    assert s.matches("quanto gastou o total") is False


# --- o comando nao pode responder com o estado de antes --------------------
# O dono ouviu, com o relé fisico desligado: "O forno: ligado e 0 W". E antes
# disso o inverso: mandava ligar, respondia desligado. A causa nao e o Zigbee --
# e a skill a responder com o estado que ja tinha em cache.


PLG300 = {"forno": {"friendly_name": "0x00124b00023771d1", "kind": "plug"}}


class _Published:
    """O que o paho devolve de um `publish` -- o comando foi aceite."""

    def wait_for_publish(self, timeout=None):
        return None

    def is_published(self):
        return True


def test_a_command_does_not_answer_with_the_state_it_cached_before(monkeypatch):
    """Pedir para ligar tem de responder com o estado novo, ou com nada.

    Reproduz a inversao que o dono ouviu: o payload antigo, com o estado
    contrario, estava em cache e era devolvido antes de a rede responder.
    """
    monkeypatch.setenv("ZIGBEE_DEVICES_JSON", json.dumps(PLG300))
    sz._states["0x00124b00023771d1"] = {"state": "OFF", "power": 0, "energy": 3353665,
                           "_seen_at": time.time() - 1}

    class _Net:
        """O broker aceita e o Z2M responde -- mas so depois do comando."""

        def subscribe(self, *_a, **_k):
            pass

        def publish(self, topic, *_a, **_k):
            if not topic.endswith("/set"):
                return _Published()
            sz._states["0x00124b00023771d1"] = {"state": "ON", "power": 0,
                                   "energy": 3353665, "_seen_at": time.time()}
            return _Published()

    monkeypatch.setattr(sz, "_client_instance", lambda: _Net())
    monkeypatch.setattr(sz, "_coordinator_down", lambda: False)
    out = skill().handle("liga o forno")
    assert "desligado" not in (out or ""), out
    assert "posicao nao verificavel" in (out or ""), out


def test_no_new_state_says_so_instead_of_quoting_the_old_one(monkeypatch):
    """Sem resposta da rede, a frase diz que nao houve resposta."""
    monkeypatch.setenv("ZIGBEE_DEVICES_JSON", json.dumps(PLG300))
    sz._states["0x00124b00023771d1"] = {"state": "OFF", "power": 0, "_seen_at": time.time() - 1}

    class _Silent:
        def subscribe(self, *_a, **_k):
            pass

        def publish(self, *_a, **_k):
            return _Published()  # aceite, mas a rede nao responde

    monkeypatch.setattr(sz, "_client_instance", lambda: _Silent())
    monkeypatch.setattr(sz, "_coordinator_down", lambda: False)
    out = skill().handle("desliga o forno")
    assert "desligado" not in (out or ""), out
    assert "nao devolveu" in (out or ""), out


# --- o device conta em W*h, nao em kWh -------------------------------------
# 3353618 -> 3353665 em ~12 s com a carga a puxar: 14 kW em W*h, ou 14 MW em
# kWh. O total certo e ~3353.7 kWh.


def test_energy_is_converted_from_wh_to_kwh():
    assert sz._energy_kwh(3353665) == 3353.665
    assert sz._energy_kwh(0) == 0.0
    assert sz._energy_kwh(None) is None


def test_the_total_is_reported_in_kwh_not_in_wh():
    out = sz._describe("forno", {"state": "ON", "power": 0, "energy": 3353665})
    assert "3353.7 kWh" in out, out
    assert "3353665" not in out, out


# --- a posicao deste relé nao e legivel ------------------------------------
# Nenhuma leitura no sistema prova onde esta o relé: `state` e o ultimo comando
# (medido: relé fisico off, state "ON") e o medidor ve a carga, nao o relé
# (medido: relé comandado ON com a carga off, energia e potencia ambas paradas).


def test_the_relay_position_is_not_asserted(monkeypatch):
    monkeypatch.setenv("ZIGBEE_DEVICES_JSON", json.dumps(PLG300))
    out = sz._describe("forno", {"state": "ON", "power": 0, "energy": 3353665})
    assert "ligado" not in out, out
    assert "posicao nao verificavel" in out, out


def test_a_readable_relay_still_says_on_or_off(monkeypatch):
    """Nao se generalizou a duvida: um relé legivel continua a dizer on/off."""
    monkeypatch.setattr(sz, "POSITION_UNREADABLE", set())
    assert "ligado" in sz._describe("forno", {"state": "ON", "power": 5})
    assert "desligado" in sz._describe("forno", {"state": "OFF"})


def test_zero_watts_is_no_longer_called_a_broken_reading():
    """Retirada a hipotese errada de que o device nao media potencia.

    Com a carga desligada, `power: 0` e a leitura certa. Dizer "nao mede potencia
    instantanea" seria trocar um zero verdadeiro por uma duvida falsa -- e foi
    exactamente isso que a versao anterior fez.
    """
    out = sz._describe("forno", {"state": "OFF", "power": 0, "energy": 3353665})
    assert "0 W" in out, out
    assert "nao mede" not in out, out


# --- o que um device NAO mandou nao se anuncia ------------------------------
# O dono, 2026-10-05, sobre o header do Geral: o "nao mede" e o "sem leitura"
# nao precisam de aparecer caso nao existam dados. A mesma regra vale por voz:
# uma frase que gasta metade do seu comprimento a descrever uma ausencia que
# ninguem pediu e o sistema a narrar as proprias limitacoes.


def test_the_clamp_does_not_narrate_what_it_failed_to_report():
    out = sz._describe("consumo", {"battery": 62.5, "linkquality": 87}, kind="clamp")
    assert "62% de bateria" in out, out
    assert "sinal 87" in out, out
    assert "nao devolveu" not in out, out
    assert "potencia" not in out, out


def test_a_clamp_with_nothing_to_say_says_so_in_one_line():
    out = sz._describe("consumo", {"linkquality": 87}, kind="clamp")
    assert out == "O consumo comunica (sinal 87).", out


# --- um device que nao responde nao leva a skill abaixo ---------------------
# Bug pre-existente encontrado em 2026-10-05 ao mexer no texto acima: o guard
# `if not payload` estava DEPOIS de todos os `payload.get(...)`, e `_describe` e
# chamada com None sempre que um device nao responde. Uma tomada inacessivel
# levantava AttributeError em vez de dizer que nao respondeu.


def test_an_unreadable_device_is_reported_not_raised():
    for payload in (None, {}):
        out = sz._describe("forno", payload, kind="plug")
        assert "nao respondeu" in out, (payload, out)


def test_the_dashboard_survives_an_unreachable_device(monkeypatch):
    monkeypatch.setenv("ZIGBEE_DEVICES_JSON", json.dumps(PLG300))
    monkeypatch.setattr(sz, "_client_instance", lambda: None)
    sk = skill()
    assert sk.get_status_for_device("forno") == {"state": "unreachable"}
    assert sk.handle("estado do forno") is not None


# --- o clamp mede a corrente da casa ---------------------------------------
# Medido 2026-10-06: `rmsCurrent` foi o ÚNICO atributo que respondeu sem
# UNSUPPORTED_ATTRIBUTE, e passou de 0 para 218-279 quando o cabo da pinça foi
# ligado à porta certa. O device recusa rmsVoltage e activePower, logo a
# potência tem de vir de P = V * I.


def test_the_clamp_reads_current_and_the_skill_asks_for_it():
    """A corrente e' o unico atributo que este device implementa.

    Sem este `get`, o clamp so volta com o que o broker tenha retido e a
    leitura depende do humor do Z2M.
    """
    assert "current_8" in sz._GET_FIELDS["clamp"], (
        "a skill deixou de pedir a corrente ao clamp"
    )


def test_the_house_draw_comes_from_the_clamp_not_the_oven():
    """A potenca da casa e' a do clamp. A do forno e' a do forno."""
    watts = sz._clamp_watts({"current_8": 233})
    assert watts is not None
    # divisor 10 x 230 V: o unico dos tres que produz uma casa (ver a nota em
    # skill_zigbee.CLAMP_CURRENT_DIVISOR)
    assert watts == round(233 / 100 * 230, 1), watts


def test_the_clamp_dashboard_carries_the_house_watts():
    payload = {"current_8": 233, "battery": 63, "linkquality": 87}
    watts = sz._clamp_watts(payload)
    assert round(watts) == 536, watts


def test_the_clamp_voice_reports_the_house_draw():
    out = sz._describe("consumo", {"current_8": 233, "battery": 63}, kind="clamp")
    assert "536 W" in out, out


def test_the_divisor_is_a_named_constant_not_a_magic_number():
    """Se a calibracao mudar o divisor, muda-se uma linha nomeada."""
    assert sz.CLAMP_CURRENT_DIVISOR == 100
    assert sz.MAINS_VOLTS == 230
