"""Zigbee skill — controlo local de dispositivos Zigbee via Zigbee2MQTT.

Substitui o caminho cloud do Cloogy para os dispositivos Zigbee da casa. ate
`docs/ZIGBEE_INTEGRATION.md` com o que este ficheiro mediu em vez do que o
plano original assumia.

Tres coisas que o plano original dizia e a maquina desmentiu
------------------------------------------------------------
1. ``adapter: ezsp`` esta errado para este dongle. O Sonoff Zigbee 3.0 USB
   Dongle Plus e um ZBDongle-P (CC2652P, stack Z-Stack 3.x.0) e o valor
   correcto no Z2M 2.14.2 e ``zstack``. O ``znp`` tambem e recusado --
   "serial/adapter must be equal to one of the allowed values". E
   auto-detecao falha com "No valid USB adapter found", portanto o adapter
   tem de ser dito.
2. O Z2M nao tem REST API. Em 2.x ``/api`` e um WebSocket e o resto e a SPA;
   ``GET /api`` devolve a pagina de erro do frontend com HTTP 404. MQTT e a
   unica superficie de integracao que existe.
3. O Z2M nao arranca sem broker. Com um ficheiro de configuracao valido e sem
   Mosquitto, ele sobe um servidor de onboarding na porta 8080 e fica la a
   espera de uma pessoa. Ver ``scripts/setup_zigbee.sh``.

Prioridade 70, e class-based de proposito
-----------------------------------------
``PRIORITY`` escrita ao nivel do modulo e **ignorada**: o ``LegacySkillAdapter``
constroi-se com ``priority=0`` por omissao e nunca le o atributo. Medido --
atribui-se ``PRIORITY = 70`` a uma skill legacy e o loader deu-lhe 0.
Por isso esta e uma subclasse de ``Skill``, como ``skill_tasmota`` e
``skill_chacon_udp``.

E 70 e acima de propósito. ``forno``/``tomada``/``ficha`` sao DEVICE_NOUNS de
``skill_tuya``, que e legacy e prioridade 0. O loader parte um empate pela ordem
alfabetica do ficheiro, portanto a 0 quem respondia a "liga o forno" dependia da
ordem de leitura do directorio. A 70, responde a Zigbee, que e o que o dono
quer dizer quando o dispositivo esta emparelhado aqui.

Este skill substituiu a `skill_cloogy`, que lia os mesmos devices por cloud e
foi descontinuada em 2026-10-05: reportava o atuador do forno como 1000 W --
o seu proprio codigo marcava aquilo como `state: unknown`, e o valor no cache
tinha 5 dias. A lesson dela vive aqui: um comando aceite pelo broker nao e um
comando executado, e um sensor que mede nada tem de dizer que nao mede em vez de
devolver 0.

O recuo continua a existir
-------------------------
Quando nao ha dispositivos configurados, ou quando o texto nao nomeia nenhum,
``handle`` devolve ``None`` e o loader segue para a skill seguinte. Com
``ZIGBEE_DEVICES_JSON`` vazio a skill nao casa com nada e desaparece do loader,
que e o que se quer: uma skill que capturasse "liga o forno" para depois dizer
"nao ha dispositivos" tiraria a resposta a quem de facto a tem.
"""

import json
import logging
import os
import threading
import time
import unicodedata
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt

from skills.base import Skill, TriggerType

logger = logging.getLogger(__name__)

PRIORITY = 70
NAME = "zigbee"

# O teu nome para o dispositivo, o nome que o Z2M conhece. Os dois tem de
# bater: o Z2M publica em `zigbee2mqtt/<friendly_name>` e o `friendly_name` e
# o que o dono renomeou no frontend do Zigbee2MQTT.
#
#   {"forno": {"friendly_name": "forno", "kind": "plug"},
#    "consumo": {"friendly_name": "casa", "kind": "clamp"}}
#
# `kind: "clamp"` e so leitura -- um clamp mede, nao comuta. Pedir para
# "desligar" o consumo e recusado com uma frase que diz porquê, em vez de
# publicar um `state` que o device nunca vai aceitar.
def _devices() -> Dict[str, Dict[str, Any]]:
    raw = os.getenv("ZIGBEE_DEVICES_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.error("ZIGBEE_DEVICES_JSON is not valid JSON; ignoring it")
        return {}
    if not isinstance(parsed, dict):
        logger.error("ZIGBEE_DEVICES_JSON must be an object; ignoring it")
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for nickname, spec in parsed.items():
        if isinstance(spec, str):
            spec = {"friendly_name": spec}
        if not isinstance(spec, dict):
            continue
        friendly = str(spec.get("friendly_name") or nickname)
        out[str(nickname)] = {
            "friendly_name": friendly,
            "kind": str(spec.get("kind") or "plug"),
        }
    return out


def _base_url() -> str:
    return os.getenv("ZIGBEE2MQTT_URL", "http://127.0.0.1:8090").rstrip("/")


def _broker() -> tuple[str, int]:
    host = os.getenv("MQTT_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("MQTT_PORT", "1883"))
    except (TypeError, ValueError):
        port = 1883
    return host, port


# OFF antes de ON, e por uma razao lexica e nao de estilo: "desliga" contem
# "liga". Testados na ordem contraria, "desliga o forno" publica ON. Nao e uma
# resposta errada, e um oven ligado. Mesmo aviso e mesma razao na skill_cloogy.
OFF_WORDS = ("desliga", "desligar", "apaga", "apagar", "desliga-lo")
ON_WORDS = ("liga", "ligar", "acende", "acender", "ativa", "ligue")
READ_WORDS = (
    "estado",
    "como esta",
    "esta ligado",
    "esta desligado",
    "potencia",
    "quanto",
)

# Aliases para o sensor de consumo da casa. "quanto gastou o total" e "quanto
# consumiu a casa" reach the same clamp, and both are things the owner says
# every day -- a test pinned them once and the pinning outlasted the skill it
# was written for.
#
# These came from `skill_cloogy._find_id_by_name`, which mapped casa/geral/
# total/main onto the whole-house clamp. Dropping them with the skill would
# have sent a daily question to the language model, which answers fluently and
# has no meter to read.
HOUSE_ALIASES = ("casa", "total", "main")
# `geral` is deliberately NOT here. It is ambiguous in a way the other three are
# not: "quanto e o geral" is a division, and the calculator owns it. Including it
# put this skill ahead of the calculator on that phrase. The cost is that
# "quanto gastou o geral" no longer reaches the clamp -- and that is the correct
# trade, because the sentence nobody says loses to the one everybody says.
#
# Only a sensor answers to an alias. Resolving "total" onto a plug would make
# "quanto gastou o total" switch a relay, which is not what anyone means.
ALIAS_KIND = "clamp"


def _fold(text: str) -> str:
    """Sem acentos e em minusculas, para comparar alcunha com pedido.

    O texto vem do STT, que nao acenta, e o dono escreve as alcunhas acentuadas.
    Sem isto, uma alcunha acentuada nunca casa com o que se diz.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# --- Estado ---------------------------------------------------------------
# Ultimo estado publicado por cada device, retained pelo Z2M. Quando o
# cliente subscreve recebe logo o ultimo valor retido, por isso isto
# preenche-se no arranque sem precisar de esperar por uma mudanca.
_states: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()

# `zigbee2mqtt/bridge/state` e retido e vale "online"/"offline". Distingue duas
# coisas que sem ele sao a mesma frase: o coordinator em baixo, e um device que
# simplesmente calou. "O Zigbee2MQTT esta em baixo" diz ao dono onde olhar;
# "nao consegui ler" faz-o suspectar do forno.
_bridge_state: Optional[str] = None

_client: Optional[mqtt.Client] = None
_client_lock = threading.Lock()


def _on_message(_client, _userdata, _msg) -> None:
    global _bridge_state
    try:
        topic = _msg.topic
        prefix = _base_topic()
        if not topic.startswith(f"{prefix}/"):
            return
        rest = topic[len(prefix) + 1 :]
        payload = json.loads(_msg.payload.decode("utf-8", "replace"))
        if rest == "bridge/state":
            if isinstance(payload, dict):
                _bridge_state = str(payload.get("state") or "").lower() or None
            return
        if rest.startswith("bridge/") or "/" in rest:
            # `.../set`, `.../get`, `bridge/...` -- topicos de comando e de
            # infraestrutura, nunca estado de um device.
            return
        if not isinstance(payload, dict):
            return
        payload["_seen_at"] = time.time()
        with _lock:
            _states[rest] = payload
    except (ValueError, UnicodeDecodeError, AttributeError):
        logger.warning("Z2M state message was not JSON; ignored", exc_info=True)


def _coordinator_down() -> bool:
    """True so quando se sabe que o coordinator foiShutdown.

    `None` -- nunca heard from -- nao conta. Uma skill que acabou de arrancar
    nao sabe de nada, e tratar essa ignorancia como uma falha faria a primeira
    pergunta depois de um reboot dizer "esta em baixo" sobre um coordinator
    que acabou de subir.
    """
    return _bridge_state == "offline"


def _base_topic() -> str:
    return os.getenv("ZIGBEE2MQTT_BASE_TOPIC", "zigbee2mqtt")


def _on_connect(_client, _userdata, _flags, reason_code=None, _props=None) -> None:
    """Subscribe from here, not straight after `connect()`.

    This was a race, and it decided whether the skill worked at all.

    `connect()` returns as soon as the TCP connection is up and the CONNECT
    packet is written. The CONNACK comes back asynchronously, and `loop_start()`
    is what processes it. Calling `subscribe()` before that point sends a
    SUBSCRIBE on a connection paho does not yet consider established; paho
    only queues outgoing messages that carry a QoS greater than 0, and
    `subscribe()` uses QoS 0 -- so the packet is dropped without an error.
    Nothing raises. The skill then never receives a single message: no retained
    states, no command confirmations, no bridge state. Every device looks
    `unreachable` and every query fails.

    Which is not even the worst of it, because it only happened *sometimes*.
    The race is decided by how fast the broker answers, so the same code passed
    the tests, passed a real run, and then lost -- and the loss looks exactly
    like "the Zigbee devices are offline". Measured on 2026-10-05: `_states`
    was empty three seconds after connecting, while `mosquitto_sub` on the
    same broker received the clamp's retained `{"battery":63}` immediately.

    `on_connect` is the only place the subscription is guaranteed to be sent on
    an established session. A dropped SUBSCRIBE is now impossible rather than
    merely unlikely.
    """
    if getattr(reason_code, "is_failure", False) or reason_code not in (
        None,
        0,
        getattr(reason_code, "Success", 0),
    ):
        logger.warning("MQTT refused the connection: %s", reason_code)
        return
    try:
        _client.subscribe(f"{_base_topic()}/+")
    except (ValueError, RuntimeError):
        logger.warning("MQTT subscribe to %s/+ failed", _base_topic())


def _client_instance() -> Optional[mqtt.Client]:
    """Um unico cliente MQTT, ligado uma vez.

    `None` quando o broker nao esta a dar answer -- e `None` e o que a skill
    diz ao dono. Fingir que ha ligacao e o que produz um "o forno esta
    desligado" que ninguem verificou.
    """
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        host, port = _broker()
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2, client_id="", protocol=mqtt.MQTTv311
            )
            client.on_message = _on_message
            client.on_connect = _on_connect
            client.connect(host, port, keepalive=60)
            client.loop_start()
        except (OSError, ValueError):
            logger.warning("MQTT broker at %s:%s is unreachable", host, port)
            return None
        _client = client
        return _client


def _publish(friendly_name: str, state: str) -> bool:
    client = _client_instance()
    if client is None:
        return False
    topic = f"{_base_topic()}/{friendly_name}/set"
    result = client.publish(topic, json.dumps({"state": state}), qos=1)
    try:
        result.wait_for_publish(timeout=5)
    except (RuntimeError, ValueError):
        return False
    return result.is_published()


# O que pedir a cada tipo de device, e porque nao se pede `state` ao forno.
#
# Um `/get` sem payload pede TUDO, e o Z2M trata a recusa de um atributo como
# falha do pedido inteiro. Pior: pedir um atributo que o device nao tem nao
# devolve NADA, e o Z2M nao responde -- nao ha erro, nao ha evento, so silencio.
#
# O PLG300 e o caso: `/get {"state": ""}` nao devolve absolutamente nada, porque
# o device nao tem cluster genOnOff. `/get {"power": ""}` responde de imediato.
# O `state` que aparece no payload e o eco OTIMISTA do ultimo `/set` -- o Z2M
# publica o valor que espera, e o device nunca o confirma a leitura. Por isso o
# `state` e lido do eco (util, e a melhor coisa disponivel) mas `power` e lido de
# uma leitura que o device respondeu de facto.
#
# Consequencia para quem lee: um device que responde `power` e cala-se em
# `state` nao e um device avariado. E pedir `state` ao forno fazia a skill
# esperar por um evento que nunca chega, e responder "Nao consegui ler" a um
# device que estava a publicar.
_GET_FIELDS: Dict[str, Dict[str, str]] = {
    "plug": {"power": ""},
    # `current_8` e' o que o CLP310 HA mede de facto. Medido 2026-10-06: foi o
    # unico atributo que respondeu sem `UNSUPPORTED_ATTRIBUTE` (ao contrario de
    # rmsVoltage e activePower, que recusam), e passou de 0 para 218-279 quando
    # o cabo da pinza foi ligado a porta correcta.
    "clamp": {"battery": "", "linkquality": "", "current_8": ""},
}


def _state_for(
    friendly_name: str,
    wait_s: float = 2.5,
    kind: str = "plug",
    after: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Ultimo estado conhecido deste device, esperando pelo tema retido.

    Pede `/get` e espera que o Z2M publique. Sem este passo, uma skill que
    arrancou depois do dispositivo responderia "estado desconhecido" para uma
    leitura que o Z2M tem retida e que portanto conhece.
    """
    client = _client_instance()
    if client is None:
        return None
    with _lock:
        known = dict(_states.get(friendly_name) or {})
    fresh_enough = time.time() - known.get("_seen_at", 0) < 30
    # `after` is how a caller says "I only want to hear about what happens next".
    # Without it, a command handler reads the state that was already cached, and
    # since a retained payload is younger than 30 s that old state is returned
    # instantly -- so asking to turn the oven ON answered "off", and asking to
    # turn it OFF answered "on". That inversion was the owner's actual complaint.
    if known and fresh_enough and (
        after is None or known.get("_seen_at", 0) > after
    ):
        return known

    client.publish(
        f"{_base_topic()}/{friendly_name}/get",
        json.dumps(_GET_FIELDS.get(kind, _GET_FIELDS["plug"])),
        qos=1,
    )
    deadline = time.time() + wait_s
    while time.time() < deadline:
        with _lock:
            current = _states.get(friendly_name)
            if not current or current.get("_seen_at", 0) <= known.get("_seen_at", 0):
                continue
            if after is not None and current.get("_seen_at", 0) <= after:
                continue
            return dict(current)
        time.sleep(0.1)
    # Falling back to `known` is right for a plain read -- a retained state is
    # better than nothing. It is exactly wrong after a command: `known` is the
    # state from *before* the command, so returning it answers "off" to a
    # request to turn on. After a command, older-than-the-command is silence,
    # and silence has to be reported as silence.
    if after is not None and known.get("_seen_at", 0) <= after:
        return None
    return known or None


# Relays whose position this device cannot report. Keyed by friendly name.
#
# The PLG300 announces no genOnOff cluster, so Zigbee2MQTT's `state` is not a
# readback: it is the last command the bridge was given. Measured 2026-10-05,
# both directions, and the two measurements together are what make this a fact
# rather than a suspicion:
#
#   * relay physically OFF, `state` still "ON"  -> the echo does not follow the
#     physical button, so it is a memory and not a position.
#   * relay commanded ON with the load off, `energy` and `power` both flat ->
#     the meter reports the load, not the relay. So consumption cannot stand in
#     for position either.
#
# Consequently there is no reading anywhere in this system that proves where the
# relay is. The honest answer says which order was last given and what the meter
# actually saw. Anything that says "ligado" here is a guess.
POSITION_UNREADABLE = {"0x00124b00023771d1"}


def _position_is_unreadable(name: str) -> bool:
    """Is this one of the relays whose position nobody can read?

    Accepts the nickname or the friendly name, because the vocabulary says
    "forno" and the topic says "0x00124b00023771d1".
    """
    if name in POSITION_UNREADABLE:
        return True
    for nickname, spec in _devices().items():
        if spec["friendly_name"] not in POSITION_UNREADABLE:
            continue
        if _fold(nickname) == _fold(name) or _fold(spec["friendly_name"]) == _fold(name):
            return True
    return False


def _energy_kwh(raw: Any) -> Optional[float]:
    """Accumulated energy in kWh, from a device that counts in W*h.

    The PLG300 stores Wh in an attribute the ZCL defines as kWh. Measured: the
    value read 3353618 and then 3353665 roughly twelve seconds later with the
    load drawing -- 47 units in twelve seconds is 14 kW as W*h, or 14 MW as kWh.
    So the total is ~3353.7 kWh, and publishing 3353665 "kWh" is wrong by 1000x.

    This was first corrected in the Zigbee2MQTT converter, twice, and both
    attempts failed in ways the converter file records. Doing it here means it is
    unit-tested instead of discovered in a log.
    """
    if raw is None:
        return None
    try:
        return float(raw) / 1000.0
    except (TypeError, ValueError):
        return None


# Escala do valor bruto de corrente para ampere.
#
# O CLP310 HA publica `rmsCurrent` em unidades CRUAS: medido 233 com a casa a
# consumir (com o esquentador a ligar). A 230 V, 233 A seriam 53 kW, o que nao
# existe numa casa. O ZCL guarda a escala em `acCurrentDivisor` (0x0502) e
# `acCurrentMultiplier` (0x0501) -- tentado, e o device NAO os implementa
# (ver `docs/ZIGBEE_INTEGRATION.md`). Portanto a escala vem de uma restricao
# fisica, nao de um palpite:
#
#     divisor  1  ->  233.0 A  ->  53.59 kW   impossivel
#     divisor 10  ->   23.3 A  ->   5.36 kW   casa com esquentador ligado
#     divisor 100 ->    2.3 A  ->   0.54 kW   abaixo de qualquer casa
#
# O divisor 10 e' o unico dos tres que produz uma casa. ESTE E' O PONTO A
# CALIBRAR: se o valor vier a descer de 20 A com a casa aceso, o divisor esta
# errado e a potencia tambem. O owner adiou a calibracao (2026-10-06) e pediu
# para seguir sem ela; o numero fica numa constante nomeada precisamente para
# que a correcao seja uma linha, e nao uma volta pelo codigo.
#
# A tensao tambem e' assumida: o device recusa `rmsVoltage`, logo P = V * I usa
# os 230 V nominais da rede PT. Numa instalacao a 230 +- 6 V o erro e' de ~3% --
# dentro do que o divisor, sem calibrar, ja comporta.
# 0  # calibrado 2026-10-06: chaleira 2000 W -> delta ~800 raw;
# forno 100º -> 714 raw -> divisor 100 é o único que fecha0
MAINS_VOLTS = 230

CLAMP_CURRENT_DIVISOR = 100  # calibrado 2026-10-06
# delta ~800 raw (chaleira 2000 W)
# forno 100º -> 714 raw
# divisor 100 é o único que fecha

def _clamp_amps(payload: Dict[str, Any]) -> Optional[float]:
    """Corrente em ampere, do valor bruto do clamp. None se nao vier nada."""
    raw = payload.get("current_8")
    if raw is None:
        return None
    try:
        return float(raw) / CLAMP_CURRENT_DIVISOR
    except (TypeError, ValueError):
        return None


def _clamp_watts(payload: Dict[str, Any]) -> Optional[float]:
    """Potencia da casa, de P = V * I com os 230 V nominais.

    O device recusa `activePower`, entao a potencia e' derivada da corrente -- e
    isso e' fisica, nao estimativa: um transformador de corrente mede corrente,
    e numa rede a 230 V nominais a potencia activa e' V * I para uma carga
    predominantemente resistiva. Numa carga reativa o factor de potencia
    desloca um pouco; o clamp nao reporta `powerFactor` para corrigir.
    """
    amps = _clamp_amps(payload)
    if amps is None:
        return None
    return amps * MAINS_VOLTS


def _describe(name: str, payload: Dict[str, Any], kind: str = "plug") -> str:
    """Frase montada so com os campos que o device realmente reportou.

    Um plug tem `state`; um clamp tem `power`. Anunciar um campo que o
    dispositivo nao mandou e inventar um numero com aspecto de medicao.

    O `kind` decide a forma, e importa mais do que parece. Um clamp nao tem
    `state` porque nao e uma coisa que se liga, e a versao anterior deste texto
    dizia-lhe "estado desconhecido" -- que e uma frase sobre uma coisa que o
    dono perguntou errado, e nao sobre o device. Pedia "quanto esta o
    consumo", o clamp respondia: "O consumo esta estado desconhecido." Um
    sensor que mede responde com o que mede.

    Quando nao ha `power` mas ha `battery` e `linkquality` -- o que e
    exactamente o caso do CLP310 HA emparelhado aqui, que recusa
    `haElectricalMeasurement.read` com UNSUPPORTED_ATTRIBUTE -- a frase diz o
    que o device de facto manda e **diz que a potencia nao veio**. Um 0 W
    inventado seria pior que o Cloogy, que pelo menos era honesto sobre o que
    nao sabia.
    """
    # Ahead of every read below, not after them: `_describe` is called with
    # None whenever a device does not answer, and each `payload.get(...)` before
    # this guard used to raise AttributeError on exactly that path -- an
    # unreachable plug took the whole skill down instead of saying so.
    if not payload:
        return f"O {name} nao respondeu."

    bits: list[str] = []

    if kind != "clamp":
        state = str(payload.get("state", "")).upper()
        if _position_is_unreadable(name) or state not in ("ON", "OFF"):
            # Nothing here proves where the relay is, so do not say.
            bits.append("posicao nao verificavel")
        elif state == "ON":
            bits.append("ligado")
        else:
            bits.append("desligado")

    # `power` before `power_8`, and the order is not cosmetic. With the PLG300
    # converter declaring `electricityMeter` on endpoint 8, the device reported
    # BOTH `power` (live) and `power_8` (a leftover in the persisted state.json,
    # measured at 93 W from before). Preferring the stale one made the oven
    # report "off and drawing 93 W" -- the skill_cloogy defect one layer deeper.
    power = payload.get("power")
    if power is None:
        power = payload.get("power_8")
    if power is not None:
        bits.append(f"{round(float(power))} W")
    kwh = _energy_kwh(payload.get("energy"))
    if kwh is not None:
        bits.append(f"{kwh:.1f} kWh acumulados")
    if payload.get("voltage") is not None:
        bits.append(f"{round(float(payload['voltage']))} V")

    if not bits:
        # A payload arrived, and none of it is a field a person asked about.
        # What it did report is still worth saying -- on a clamp, the battery
        # and the signal are the whole answer to "is it alive".
        #
        # What is not said is what it did NOT report. The owner removed that on
        # 2026-10-05, and the same reasoning as the Geral header applies here:
        # "comunica (sinal 87), mas nao devolveu potencia nem estado" spends half
        # the sentence on an absence the owner did not ask about. A CLP310 HA
        # refuses activePower by design; repeating that back every time is the
        # system narrating its own limitations to someone who wants to know the
        # battery. Report the reading, leave the silence alone.
        #
        # What must NOT happen here is inventing a number out of `linkquality`
        # and calling it consumption -- that was the skill_cloogy bug.
        extras = []
        watts = _clamp_watts(payload)
        if watts is not None:
            extras.append(f"{round(watts)} W")
        if payload.get("battery") is not None:
            extras.append(f"{round(float(payload['battery']))}% de bateria")
        if payload.get("linkquality") is not None:
            extras.append(f"sinal {round(float(payload['linkquality']))}")
        if extras:
            return f"O {name} comunica ({', '.join(extras)})."
        # Alive enough to answer, with nothing in the answer worth repeating.
        return f"O {name} respondeu, sem nada a reportar."

    return f"O {name}: {' e '.join(bits)}."


class ZigbeeSkill(Skill):
    """Estado e ON/OFF de dispositivos Zigbee emparelhados no coordinator local."""

    NAME = NAME
    PRIORITY = PRIORITY
    TRIGGER_TYPE = TriggerType.CONTAINS
    # Recalculado em cada `matches`: o conjunto de alcunhas depende do
    # ambiente, e uma lista de triggers classificada no import guarda para
    # sempre o que o `.env` tinha nesse instante.
    TRIGGERS = ["zigbee"]

    def matches(self, text: str) -> bool:
        """So quando ha dispositivos E o texto nomeia um, ou diz "zigbee".

        A consequencia de nao listar as alcunhas em `TRIGGERS` e do que este
        `matches` e a sua unica porta: com `ZIGBEE_DEVICES_JSON` vazio a skill
        nao casa com nada e desaparece do `find_matching_skill`. E o que se
        quer. Uma skill que capturasse "liga o forno" para depois dizer "nao ha
        dispositivos" tiraria a quem de facto responde -- hoje o `skill_tuya`,
        que tem `forno` nos seus DEVICE_NOUNS -- a unica resposta que existe,
        para a trocar por "nao ha dispositivos" quando a resposta verdadeira e
        "nao sei, ainda nao foi emparelhado".

        Este comportamento foi escrito quando a `skill_cloogy` era o recuo, e
        sobreviveu a ela de proposito: a raza nao depende de qual skill
        responde, so de haver alguem a responder.
        """
        if "zigbee" in _fold(text):
            return True
        ntext = _fold(text)
        if any(_fold(name) in ntext for name in _devices()):
            return True
        # The house aliases only match when a sensor is configured to answer
        # them, so an empty configuration still disappears from the loader.
        return self._house_alias_target(ntext) is not None

    def _house_alias_target(self, folded_text: str) -> Optional[str]:
        """The sensor that `casa`/`geral`/`total`/`main` mean, if there is one.

        The alias IS the reference: "quanto gastou o total" names no device, and
        the whole house is what `total` means. Requiring the nickname to appear
        too would make the alias dead on arrival -- which is what happened the
        first time this was written, and what `skill_cloogy._find_id_by_name`
        already knew: it mapped the alias onto the clamp and looked for the
        nickname among casa/geral/total/main, not in the prompt.

        So an alias picks the sensor even when the prompt contains no nickname,
        but it only picks a *sensor* -- never a plug, because "quanto gastou o
        total" must not switch a relay.
        """
        if not any(_fold(a) in folded_text for a in HOUSE_ALIASES):
            return None
        return next(
            (n for n, spec in _devices().items() if spec["kind"] == ALIAS_KIND),
            None,
        )

    def handle(self, text: str) -> Optional[str]:
        if not self.matches(text):
            return None

        devices = _devices()
        ntext = _fold(text)
        target = next((n for n in devices if _fold(n) in ntext), None)
        if target is None:
            target = self._house_alias_target(ntext)
        if target is None:
            # "zigbee" sem alcunha nenhuma, ou uma alcunha que e de outro
            # dispositivo. O loader segue para a skill seguinte.
            return None

        spec = devices[target]
        friendly = spec["friendly_name"]

        wants_off = any(w in ntext for w in OFF_WORDS)
        wants_on = any(w in ntext for w in ON_WORDS)
        wants_read = any(w in ntext for w in READ_WORDS) or ntext.rstrip().endswith("?")

        if wants_off or wants_on:
            if spec["kind"] == "clamp":
                return (
                    f"O {target} e um sensor de consumo: mede, nao comuta. "
                    f"Nao ha nada para ligar ou desligar."
                )
            wanted = "OFF" if wants_off else "ON"
            # Marked before the command, used after it: everything the reply
            # may quote has to have been published later than this instant.
            issued_at = time.time()
            if not _publish(friendly, wanted):
                if _coordinator_down():
                    return (
                        "O Zigbee2MQTT esta em baixo, por isso nao consegui "
                        f"mudar o {target}."
                    )
                return f"Nao consegui falar com o Zigbee2MQTT para mudar o {target}."
            # Confirmar pelo estado retido, e nao pelo pedido. Um comando
            # aceito pelo broker nao e um comando que o device fez: entre os
            # dois ha a rede Zigbee, e ela falha em silencio.
            payload = _state_for(
                friendly, wait_s=6.0, kind=spec["kind"], after=issued_at
            )
            if not payload:
                return (
                    f"Pedi para {'desligar' if wants_off else 'ligar'} o {target}, "
                    f"mas o dispositivo nao devolveu o novo estado."
                )
            return _describe(target, payload, spec["kind"])

        if wants_read:
            if _coordinator_down():
                return "O Zigbee2MQTT esta em baixo, por isso nao ha estado para ler."
            payload = _state_for(friendly, kind=spec["kind"])
            if payload is None:
                return f"Nao consegui ler o {target}. O Zigbee2MQTT esta a responder?"
            return _describe(target, payload, spec["kind"])

        return None

    def get_status_for_device(self, nickname: str) -> Dict[str, Any]:
        """Para o dashboard. on/off, ou unreachable -- nunca um terceiro valor.

        Um estado inventado e pior do que nenhum: o painel mostra um interruptor
        numa tomada que nao esta a responder, e alguem acredita nele.

        Para um clamp, `on`/`off` nao sao categorias: nao ha relé. Reporta
        `sensor` e o que ele mediu. Traduzir um clamp que mede para "off"
        porque nao mandou `state` seria o mesmo erro da `skill_cloogy` a ler um
        atuador como wattagem -- uma mudanca de grandeza feita de uma
        ausencia.
        """
        devices = _devices()
        target = next((n for n in devices if _fold(n) == _fold(nickname)), None)
        if target is None:
            return {"state": "unreachable"}
        spec = devices[target]
        payload = _state_for(spec["friendly_name"], wait_s=1.5, kind=spec["kind"])
        if not payload:
            return {"state": "unreachable"}

        out: Dict[str, Any] = {}
        # `power` first for the reason given in _describe: with the PLG300
        # converter the device publishes `power` as the live reading and
        # `power_8` as a stale leftover, and preferring the latter showed the
        # dashboard 93 W on an oven that was off and drawing zero.
        power = payload.get("power")
        if power is None:
            power = payload.get("power_8")
        if power is not None:
            out["power_w"] = round(float(power), 1)

        # Accumulated energy, in kWh. The PLG300 reports `energy` from its
        # seMetering cluster and it responds to a direct read (measured 3353618).
        # This is what the "Geral" header shows as total consumption, because
        # instantaneous watts are not a total -- the same distinction the
        # cloogy skill made between `Read` and `Consumption` and got right.
        kwh = _energy_kwh(payload.get("energy"))
        if kwh is not None:
            out["energy_kwh"] = round(kwh, 2)

        if payload.get("voltage") is not None:
            out["voltage_v"] = round(float(payload["voltage"]), 1)
        if payload.get("current") is not None:
            out["current_a"] = round(float(payload["current"]), 2)

        if spec["kind"] == "clamp":
            out["state"] = "sensor"
            if payload.get("battery") is not None:
                out["battery_pct"] = round(float(payload["battery"]))
            # The house's own draw. This is the number the Geral header wanted
            # all along and did not have: the Forno's energy is the oven's, not
            # the house's (the owner confirmed: "só o forno").
            watts = _clamp_watts(payload)
            if watts is not None:
                out["power_w"] = round(watts, 1)
            return out

        raw = str(payload.get("state", "")).lower()
        out["state"] = raw if raw in ("on", "off") else "unknown"
        return out
