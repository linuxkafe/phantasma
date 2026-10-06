# Zigbee local via Zigbee2MQTT

Controlo local dos dispositivos Zigbee da casa, sem cloud de vendedor. A skill é
`skills/skill_zigbee.py`; o coordinator é o Sonoff Zigbee 3.0 USB Dongle Plus
ligado a `/dev/ttyUSB0`.

---

## As três coisas que a versão anterior deste ficheiro dizia, e a máquina desmentiu

Este documento foi reescrito em 2026-10-05 porque a versão anterior descrevia um
plano que **não funciona**. As três correções estão aqui porque cada uma delas
custou uma tentativa falhada, e porque a próxima pessoa a ler o plano antigo vai
repetir as mesmas três.

| A versão anterior dizia | A verdade, medida |
|---|---|
| `serial.adapter: ezsp` | **`zstack`**. `znp` também é recusado. E auto-detecção falha com `No valid USB adapter found` — o adapter tem de ser dito. |
| "pHantasma fala com o Z2M por MQTT, o Z2M tem REST API" | **Não há REST API.** Em 2.x, `/api` é um WebSocket e o resto é a SPA do frontend. `GET /api` devolve a página de erro com HTTP 404. |
| "Basta mapear `ZIGBEE2MQTT_*` e o `.env`" | **O Z2M não arranca sem Mosquitto**, e não lê a configuração no primeiro boot: sobe um servidor de onboarding e espera por uma pessoa. Ver `scripts/setup_zigbee.sh`. |

Duas conclusões que não estavam no plano original:

* **Mosquitto não é opcional.** O Z2M sai com `MQTT failed to connect` antes de
  alguma vez abrir a porta série. É broker ou não é nada.
* **`PRIORITY` escrita ao nível do módulo é ignorada.** O `LegacySkillAdapter`
  constrói-se com `priority=0` e nunca lê o atributo — medido, atribuindo
  `PRIORITY = 70` ao `skill_cloogy` e vendo o loader dar-lhe `0`. Por isso a
  skill é uma subclasse de `Skill`.

---

## O que está a correr

```
Zigbee devices  ──radio──▶  ZBDongle-P (/dev/ttyUSB0)
                                    │  USB serial, adapter: zstack
                                    ▼
                            zigbee2mqtt (docker)
                                    │  MQTT
                                    ▼
                             mosquitto (docker)  127.0.0.1:1883
                                    │
                                    ▼
                    skill_zigbee (systemd, no host) ──▶ voz
```

O broker e o frontend do Z2M estão ligados só a `127.0.0.1`. Não estão
acessíveis pela rede local. Isto é deliberado e é uma correcção: o plano
anterior mapeava `1883` para `0.0.0.0` com `allow_anonymous true` — um broker
sem autenticação em toda a rede, com poder de ligar e desligar o forno.

## Pôr a correr

```bash
scripts/setup_zigbee.sh                # sobe, semeia a config, conclui o onboarding
scripts/setup_zigbee.sh --check        # só relata
scripts/setup_zigbee.sh --logs         # segue o log do Z2M
```

Idempotente. O script:

1. semeia `configuration.yaml` no volume **só se não existir** — o mesmo
   ficheiro passa a conter a `advanced.network_key` gerada, e sobrescrevê-la
   órfã todos os dispositivos emparelhados;
2. espera por Mosquitto com um gate que **publica** (ver abaixo);
3. conclui o onboarding por `POST /submit`, que é a Via não-interactiva do Z2M;
4. espera por `Zigbee2MQTT started` no log.

Sobre o passo 3: `onboarding: false` no ficheiro **não** evita o servidor de
onboarding. Quatro tentativas, mesmo resultado, e `/data` reportava
`onboarding: true` para um ficheiro que dizia `false`. Só o `POST /submit`
persiste o estado, e a partir daí os reinícios passam direto.

### Um probe que só pode falhar

O gate de saúde do Mosquitto **publica**, não subscreve. Isto não é
estilística: `mosquitto_sub -C 1 -W 2` sai não-zero em todas as execuções,
porque nada publica no tópico que ele espera, e assim reporta um broker
perfeitamente saudável como morto. Numa primeira versão deste script era exactamente isso, e
o script falhou com `FAILED: mosquitto is not answering after 30s` com o
broker a aceitar ligações e a logá-las.

---

## Configurar os dispositivos

Em `.env` (não em `config.py` — um valor de host num dataclass é o que
`deploy.sh` trata como bug):

```bash
ZIGBEE_DEVICES_JSON={"forno": {"friendly_name": "forno", "kind": "plug"},
                     "consumo": {"friendly_name": "casa", "kind": "clamp"}}
ZIGBEE2MQTT_URL=http://127.0.0.1:8090
ZIGBEE2MQTT_BASE_TOPIC=zigbee2mqtt
MQTT_HOST=127.0.0.1
MQTT_PORT=1883
```

`friendly_name` tem de bater com o nome que o dispositivo tem no frontend do
Z2M. `kind: "clamp"` é só-leitura: um clamp mede, e perguntar para o desligar é
recusado com uma frase que diz porquê, em vez de publicar um `state` que o
device nunca vai aceitar.

### Dispositivos

Vieram do Cloogy (consultado em 2026-10-05, ambos `PhysicalAddressType:
ZigbeeHA`):

| Cloogy | IEEE | Tipo | Estado no Cloogy | No Zigbee local |
|---|---|---|---|---|
| 58521 "Forno" | `00:12:4B:00:02:37:71:D1` | PLUG | comunica; tag 169809 `Actuator`, `CanActuate: true` | **por emparelhar** |
| 58520 "Consumo Total" | `00:12:4B:00:02:04:EB:88` | CLAMP | `IsCommunicating: false` desde Nov-2025; tag 169806 a 0.0 W | emparelhado, **não mede** |

### O clamp emparelha mas não devolve potência

Juntou-se à primeira tentativa: `Clamp CLP310 HA`, fabricante
`VirtualPowerSolutions`, interviewado, a trocar mensagens (36 no health check).
O Z2M avisa que **não é suportado** e cai numa definição gerada automaticamente.

O que responde: `battery` (62%) e `linkquality` (129).

O que **não** responde, e é precisamente o que um clamp existe para fazer:

```
Publish 'get' 'power' → haElectricalMeasurement.read(["activePower"])
                        failed (Status 'UNSUPPORTED_ATTRIBUTE')
```

O endpoint 8 não implementa electrical measurement. Não é falha de
emparelhamento nem de reporting: o device está na rede e recusa a pergunta.
Consumo via Zigbee local **não está disponível** com este clamp, e a tentativa
não foi feita às cegas — mandei `get` para `power`, `voltage` e `battery` e o
device respondeu a uma e recusou as outras.

Quem continuar a ler consumo é o `skill_cloogy`, por cloud, com as leituras que
já vinham a 0.0 W. Fica como tarefa aberta, e não se disfarça: a skill não
anuncia potência que o device não mandou.

## Emparelhar

Isto não é automático e não pode ser feito por script. Um dispositivo Zigbee
pertence a um coordinator só.

```bash
scripts/setup_zigbee.sh --permit-join on     # abre a janela, 10 minutos
# ... desemparelhar do hub do Cloogy (botão ~10 s),
#     power-cycle o dispositivo perto do dongle, esperar
scripts/setup_zigbee.sh --permit-join on JOIN_MINUTES=20   # se demorares
```

`--permit-join on` **não é um booleano, e a unidade não é a que a documentação
diz.** Duas coisas, ambas medidas:

**É uma duração, e o valor é em SEGUNDOS.** Em `zigbee-herdsman/controller.js:283`:

```js
assert(time <= 254, "Cannot permit join for more than 254 seconds.");
const timeMs = time * 1000;
```

A documentação do Zigbee2MQTT diz "minutes" e está errada. Uma primeira versão
deste script aceitou "25 minutos", converteu o número como quem lê a doc, e
abriu uma janela de **25 segundos** — medido: `permit_join_end` 20,8 s no futuro
depois de pedir 25. Pior: o Z2M registava `{"status":"ok"}` e o script confirmava
com sucesso. É um erro por factor de 60 que nenhum sinal de erro acompanha.

O Argumento `true` também não funciona: `bridge.js:355` faz
`Number.parseInt(message, 10)`, `NaN` significa *desligar*, e a resposta é outra
vez `ok`. Um "on" que desliga é pior do que um erro, porque o operador sai de
cima a achar que tem janela.

**O teto é 254 segundos** (4 min 14 s), porque é um uint8 no fio. O script
converte de minutos e **recusa** o que não cabe, em vez de deixar o container
do Z2M morrer dentro de um assert:

```bash
scripts/setup_zigbee.sh --permit-join on              # 1 min (60 s)
scripts/setup_zigbee.sh --permit-join on JOIN_MINUTES=4   # o máximo
```

A janela fecha-se sozinha, sem ninguém se lembrar de a fechar. O script confirma
pelo estado retido (`bridge/info`) e verifica que o `permit_join_end` está no
futuro — `permit_join: true` com o fim no passado é uma janela já fechada, que
uma verificação só do flag reportaria como aberta.

### Power cycle não é factory reset

Desligar e voltar a ligar o plug **não** o traz para uma rede nova. Ele
reboot no mesmo Zigbee network em que já está emparelhado — o hub do Cloogy — e
para o coordinator novo o ver tem de esquecer a rede antiga: press-and-hold
~10 s até o LED piscar, e só depois power-cycle. Um power cycle sozinho, com a
janela aberta, não produz nenhum `device_joined`.

Um dispositivo Zigbee pertence a um coordinator só. `permit_join` desligado
entre sessões é o estado por omissão: enquanto está aberto, qualquer
dispositivo dentro do alcance de rádio se junta à rede sem ser convidado.

### Correr o script

Da **árvore de desenvolvimento**, não de `/opt/phantasma`:

```bash
cd ~/dev/pHantasma
scripts/setup_zigbee.sh --permit-join on
```

O `deploy.sh` copia este ficheiro para `/opt/phantasma/scripts/`, onde o
directório-pai não tem `docker-compose.yml`. Executado de lá, o `cd` relativo
leva para a árvore errada e o docker responde `no configuration file provided:
not found` — que não diz nada sobre as duas árvores nem sobre qual possui os
containers. Os containers do Zigbee estão definidos na árvore de desenvolvimento,
como `ollama` e `searxng` (`update_containers.sh` documenta-o, com
`docker inspect` a prová-lo). Para apontar para outro sítio:
`PHANTASMA_DEV_ROOT=/caminho scripts/setup_zigbee.sh`.

## O que a skill diz, e o que não diz

O risco desta skill não é escrever código. É **anunciar um estado que não
existe**. Três decisões:

1. **Um comando aceito pelo broker não é um comando executado.** Entre o
   publish e o relé há a rede Zigbee, que falha em silêncio. A skill publica,
   depois espera pelo estado retido, e se o device não responder diz
   *"Pedi para ligar o forno, mas o dispositivo não devolveu o novo estado"* —
   não "já está ligado".
2. **Broker em baixo nunca é lido como "desligado".** Uma tomada que não
   responde e uma tomada desligada são coisas diferentes, e a segunda é uma
   coisa em que se pode confiar. O `zigbee2mqtt/bridge/state` retido é usado
   para distinguir coordinator em baixo de device calado.
3. **`"desliga"` contém `"liga"`.** Testados na ordem contrária, um
   "desliga o forno" publica `ON`. O teste vê o que foi **publicado**, não a
   frase que saiu — a frase pode estar certa por acaso, com o comando errado já
   enviado para a tomada.

E o recuo: com `ZIGBEE_DEVICES_JSON` vazio a skill **não casa com nada** e
desaparece do loader. Enquanto não houver nada emparelhado aqui, quem responde
pelo forno é a `skill_cloogy`, que é onde ele ainda está. Uma skill que
capturasse "liga o forno" para dizer "não há dispositivos" tiraria ao Cloogy a
única resposta que existe.

## Firmware do dongle

Não foi feito flash. O ZBDongle-P vem pré-flashado com firmware de
coordenador, e o log confirma:

```
Coordinator firmware version: ZStack3x0 revision 20210708
```

Sobrevive a reinício (`zigbee-herdsman started (resumed)`). Não tocar sem
necessidade — o `pyproject.toml` tem a cicatriz do `faster_whisper`, e um flash
mal-sucedido deixa o dongle sem coordinator, o que significa ir buscar o
firmware à mão.

## Diagnóstico

```bash
scripts/setup_zigbee.sh --check     # estado AGORA, do broker
```

`--check` pergunta `zigbee2mqtt/bridge/info`, que é **retido**, e não o log. O
log é uma história: `grep` nele responde "o que aconteceu em algum momento
desde que o container arrancou", o que numa máquina em pé há horas significa que
a resposta não se mexe. Dois erros de uma falhança de manhã eram as últimas
linhas correspondentes, e um coordinator perfeitamente saudável reportava-se
partido.

| Sintoma | Causa provável |
|---|---|
| `no configuration file provided` | Executado de `/opt/phantasma`. Correr de `~/dev/pHantasma`. |
| `No valid USB adapter found` | `serial.adapter` ausente ou errado. Tem de ser `zstack`. |
| `MQTT failed to connect, exiting` | Mosquitto não está a dar answer. |
| Fica na página de onboarding | `POST /submit` não foi feito. `scripts/setup_zigbee.sh` faz. |
| `Cannot POST /submit` | Normal **depois** do arranque. `/submit` só existe durante o onboarding; `permit_join` vai por MQTT. |
| `Entity 'forno' is unknown` | A skill publicou para um device que ainda não se emparelhou. |
| `Currently 0 devices are joined` | Normal até haver emparelhamento. |
| Skill responde "não consegui ler" | Broker em baixo, ou device sem estado retido. |

### Três bugs que já custaram tempo, para não os repetir

**`set -o pipefail` + `grep -q`.** O `grep -q` sai à primeira correspondência, o
`docker compose logs` leva SIGPIPE e sai com 141, e o `pipefail` reporta o
pipeline inteiro como falhado. Medido: o mesmo pipeline dá `exit 0` sem
`pipefail` e `exit 255` com ele. O efeito foi um coordinator em pé há horas a ser
testado como "não arrancou". Onde o script precisa de um booleano, usa
`grep -c` e compara — `grep -c` consome o stream todo e o docker sai com 0.

**A ordem do check de arranque.** `/submit` só responde enquanto o servidor de
onboarding está de pé. A verificação "já arrancou?" tem de vir **primeiro**; se
não, o poll do onboarding corre contra um endpoint que nunca mais vai responder.

**Um probe que espera por um evento que ninguém produz.** O health-check do
Mosquitto **publica**, não subscreve — `mosquitto_sub -C 1 -W 2` sai não-zero em
todas as execuções porque nada publica no tópico que espera, e reporta um broker
saudável como morto.

## Referências

- Zigbee2MQTT: https://www.zigbee2mqtt.io/
- Dongle P (CC2652P): https://sonoff.tech/products/sonoff-zigbee-3-0-usb-dongle-plus-zbdongle-p
- Firmware coordinator Z-Stack: https://github.com/Koenkk/Z-Stack-firmware
---

## Os dispositivos que entraram, e o que cada um faz

Em 2026-10-05, com o owner acyclicamente presente e o `permit_join` aberto:

| | `Consumo Energia` | `0x00124b00023771d1` |
|---|---|---|
| IEEE | `0x00124b000204eb88` | `0x00124b00023771d1` |
| Device | `Clamp CLP310 HA` | `Plug PLG300 HA` |
| Fabricante | VirtualPowerSolutions | VirtualPowerSolutions |
| Fonte | Bateria | Mains, Router |
| Estado | emparelhado | emparelhado |
| **Mede** | `battery`, `linkquality` | `power_8`, `linkquality` |
| **Comuta** | não tem relé | **`state` ON/OFF — confirmado** |

O `PLG300` é o SKU da Cloogy. Todos os resultados de pesquisa para "PLG300" são
o *Appleton PLG-300*, um tampão de condute de ferro fundido — não tem nada a ver.
Marca branca, sem manual público: nada além do próprio device diz como se faz o
reset dele. O que funcionou foi **cortar a alimentação**, e o `DataCollectionStatus`
do Cloogy ter passado de `2` para `0` confirmou que o device largou a rede antiga.

O reset por corte **não é** factory reset. Um power cycle normal não tira um plug
sem botão da rede — leva-o a reiniciar na rede em que já estava, e o coordinator
novo nunca o vê.

### Canal

O canal estava no **11**, que é o que o Zigbee2MQTT desaconselha. Mudou para
**15**. O plug só encontrou a rede depois disso — não porque o canal estivesse
sujo, mas porque **estava à procura no canal errado e desistia depois**. Um
device mains-powered como este faz scan e pára; um device a bateria sonda ao longo
do tempo. O clamp entrou primeiro sem depender do canal, o que explica a
diferença entre os dois.

Mudar o canal obriga a re-emparelhar. O clamp re-emparelhou-se sozinho; o forno
precisou de novo power cycle.

### O `PLG300` não anuncia o seu próprio interruptor

Este é o defeito do device, e é a razão de `power_8` existir e `state` não:

```
model_id 'Plug PLG300 HA', manufacturer 'VirtualPowerSolutions'
ep8 in : genBasic, genIdentify, haElectricalMeasurement, seMetering, 64717
ep14 out: genOta
```

Não há `genOnOff` em nenhum endpoint. O Z2M gera uma definition
automaticamente, e o que sai é `identify, power, voltage, current, energy,
linkquality` — **sem `state`**. E o comando responde:

```
No converter available for 'state' on '0x00124b00023771d1'
```

Mas o relé é real: `bridge/request/on_off` com `toggle` foi aceite e o plug
**acendeu e apagou fisicamente**. O hardware tem o cluster; o device não o
anuncia.

A correcção é `zigbee2mqtt/converters/plg300.js`, um external converter que
declara o `onOff` que faltava. Verificado por ti nos dois sentidos: `state: ON`
acendeu, `state: OFF` apagou.

**Cinq premissas minhas estavam erradas** até carregar. Cada uma delas falhou de
uma forma que parecia progresso:

1. `model: "PLG300"` — o device diz `Plug PLG300 HA`. **Carregou com
   `Loaded external converter 'plg300.js'` e não mudou nada.** Esta é a
   perigosa: sucesso logged, efeito zero.
2. Sem `fingerprint` nem `zigbeeModel`, o definition **nunca entra no índice de
   procura**. Fica na lista e nunca é candidato.
3. `m` não é um global — o ficheiro é carregado por `import()` ES simples.
4. `new m.Switch()` é uma classe de *exposes*, não um *extend*: o extend exige
   `isModernExtend`, e a asserção diz `"has legacy extend in modern extend"`.
5. Os *extends* (`onOff`, `light`) **não** estão no índice do pacote, que
   exporta as classes de exposes. Vivem em `dist/lib/modernExtend.js`, e o
   subpath exportado é `./lib/*` — **sem** o `dist/`.

O que torna isto registável: em quatro das cinco o Z2M respondeu `status: ok` ou
uma mensagem de erro plausível, e em duas loading foi um sucesso declarado. Um
conversor que carrega não é um conversor que funciona.

### O clamp não mede, e isso é do device

`Consumo Energia` entra e sai, responde a `battery` e `linkquality`, e recusa:

```
haElectricalMeasurement.read(["activePower"])  → UNSUPPORTED_ATTRIBUTE
```

Não é falha de emparelhamento — está na rede, a trocar mensagens, e recusa a
pergunta. O endpoint 8 não implementa electrical measurement, que é o que um
clamp existe para fazer. **Consumo via Zigbee local não está disponível**, e o
Z2M continua a prometer `power_8` numa definition gerada que o device recusa.

Quem continua a ler consumo é o `skill_cloogy`, por cloud.

### Os nomes não são o que se escolhe

Nenhum dos dois devices aceitou rename no frontend do Z2M, e ambos publicam sob
o **IEEE**. `ZIGBEE_DEVICES_JSON` tem de usar o IEEE como `friendly_name`:

```bash
ZIGBEE_DEVICES_JSON={"forno":   {"friendly_name": "0x00124b00023771d1", "kind": "plug"},
                     "consumo": {"friendly_name": "0x00124b000204eb88", "kind": "clamp"}}
```

Com um nome que não bate certo, cada comando é publicado num tópico que ninguém
escuta e a skill diz "não consegui falar com o Zigbee2MQTT" para um device
perfeitamente alcançável.

### Comandos úteis

```bash
# mangueira de eventos, sem as megabytes do cluster definition
docker compose logs -f zigbee2mqtt 2>&1 | sed 's/\x1b\[[0-9;]*m//g' \
  | grep --line-buffered -E "permit_join|device_joined|Interview|0x00124b"

# devices e clusters de cada um
docker compose exec -T mosquitto mosquitto_sub -h localhost -t 'zigbee2mqtt/bridge/devices' -C 1 -W 5

# canal, sem reiniciar
docker compose exec -T mosquitto mosquitto_pub -h localhost \
  -t 'zigbee2mqtt/bridge/request/options' -m '{"options":{"advanced":{"channel":15}}}'
# devolve {"restart_required":true} — só muda com restart
```

Tópico do conversor: é `bridge/request/converter/save` — **singular**, e sem
underscore. `external_converter/save` não responde a nada, e o Z2M não dá erro
por isso: é silêncio.

---

## O clamp CLP310 HA: porque não há leitura de consumo (medido 2026-10-06)

O dono: *"é estranho pois ele funcionava quando ligado na cloud"*. Isto foi
investigado a sério, com o device real, e a resposta é **não é um bug do
pHantasma nem do Zigbee2MQTT**.

### O que o device anuncia

`0x00124b000204eb88`, `Consumo Energia`, `Clamp CLP310 HA`, `VirtualPowerSolutions`,
`EndDevice` a bateria. Endpoint 8, input clusters:

```
genBasic, genIdentify, haElectricalMeasurement, genPowerCfg, 64592, 64717
```

### O que acontece quando se pede a potência

Pedido explícito a `power_8` (que a definição gerada do Z2M expoe), com o log em
`debug`:

```
zh:controller:endpoint: ZCL command 0x00124b000204eb88/8
  haElectricalMeasurement.read(["activePower"], {...})
zh:controller:endpoint: Error: ... failed (Status 'UNSUPPORTED_ATTRIBUTE')
z2m: error: Publish 'get' 'power' to 'Consumo Energia' failed
```

**O device declara o cluster e recusa o atributo.** Isto é a contradição central,
e é do firmware, não da configuração: o cluster existe (o Z2M constrói a
definição a partir dele) mas o atributo lá dentro não.

O que responde é a bateria:

```
genPowerCfg.read -> batteryPercentageRemaining: 126   (= 2 x 63%)
```

### `power_clamp()` do Zigbee2MQTT não resolve isto

Existe `power_clamp()` em `zigbee-herdsman-converters`, feita exactamente para
clamps, e é a primeira coisa que se deve tentar. **Rejeitada sem ser experimentada
por uma razão medida:** ela converte o que o cluster `haElectricalMeasurement`
publica. O atributo que o cluster recusou é precisamente o que essa função
lê. Aplicá-la aqui produziria o mesmo `UNSUPPORTED_ATTRIBUTE`, e o gyro de
"já tenho a solução pronta" foi exactamente o que me levou a concluir ontem, sem
medir, que este device "não media potência". [ERRO CORRIGIDO 2026-10-05 — a
conclusão certa, a razão errada.]

### Os clusters 64592 / 64717 — a hipótese honesta, e onde ela morre

64592 = `0xFC30` e 64717 = `0xFCCD`. Nenhum dos dois é um cluster ZCL definido:

- Em `zigbee-herdsman/dist/zspec/zcl/definition/cluster.js`, os clusters
  manufacturer-specific (>= `0xFC00`) são **um só**: `manuSpecificAmazonWWAH`
  (`0xFC57`). Não existe `0xFC30` nem `0xFCCD`.
- Em 1592 ficheiros de `zigbee-herdsman-converters/dist/devices/`, **nenhum**
  menciona `VirtualPowerSolutions`.
- Estes dois IDs aparecem em `terncy.js` e `kelvinToXy.js` como **coincidências
  numéricas**, não como clusters.

São, portanto, clusters proprietários da Cloogy sem definição pública. A
hipótese é que a app Cloogy os lia por protocolo proprietário — o que é
consistente com "funcionava na cloud".

**Não foi escrito converter para eles, deliberadamente.** Um converter que
adivinha attributeIDs produz números inventados com forma de medição. Isso é
exatamente o bug da `skill_cloogy` (o atuador 0/1 lido como 1000 W), uma
geração mais abaixo. Inventar um attributeID é pior do que não ler nada: o
primeiro mente em silêncio, o segundo não mente.

### Experimento ativo: o owner carregou no botão (2026-10-06)

O teste decisivo, feito com o owner a carregar fisicamente no clamp enquanto o
log corria em `debug`, e uma escuta de ~12 minutos.

**Resultado: o clamp não emitiu um único `attributeReport`. Zero.** Nem ao
carregar. De tudo o que apareceu assinado por `0x00124b000204eb88`:

| frame | quem mandou | o que é |
|---|---|---|
| `genPowerCfg.read(["batteryPercentageRemaining"])` | nós | pedido nosso |
| `genPowerCfg.read(["batteryVoltage"])` | nós | pedido nosso |
| `haElectricalMeasurement.read(["acPowerDivisor","acPowerMultiplier"])` → `UNSUPPORTED_ATTRIBUTE` | nós | recusa |
| `genOta.queryNextImageResponse({"status":152})` | **o clamp** | o device a falar por initiative dele |

Duas conclusoes, ambas SUPORTADA:

1. **O device acorda e fala quando quer.** O `queryNextImageResponse` prova que
   ficou online o suficiente para responder a uma OTA — logo o botão acordou o
   device, e a escuta não o perdeu por estar a dormir.
2. **Não tem leitura espontânea nenhuma.** Não há `attributeReport` do
   `0xFC30`/`0xFCCD` nem de outro cluster. O clamp é um `EndDevice` que só fala
   quando perguntado, e quando perguntado só responde sobre bateria.

O `attributeReport` de `{"activePower":0}` que aparece na mesma janela é **do
`Forno`**, não do clamp — verificado no log: `Received Zigbee message from
'Forno', type 'attributeReport', cluster 'haElectricalMeasurement'`. Atribuí-lo ao
clamp seria exactamente a leitura por contexto errada que o `skill_cloogy`
fazia.

### Leitura final (medida, não inferida)

Este CLP310 HA, **tal como está emparelhado neste adaptador**, expõe apenas a
bateria. O cluster `haElectricalMeasurement` é anunciado — o que faz o Z2M
construir uma definição que promete `power_8`/`voltage_8`/`current_8` — mas o
atributo lá dentro responde `UNSUPPORTED_ATTRIBUTE`. E os clusters `64592`/
`64717`, onde a hipótese era a cloud ir buscar o consumo, **não emitem nada**.

Isso é consistente com a hipótese de que **o consumo só é lido por um protocolo
proprietário que este firmware expõe pela app, e não pela Zigbee**. Também é
consistente com uma variante de firmware: o nome do modelo acaba em `HA`
(Home Assistant), o que sugere unidades com firmware variando, e este pode não
ser o que publica a medição.

O que **não** se conclui: que o clamp é incapaz de medir. Só que, por esta via,
não mede para nós.

### Diagnóstico final: `current_8` não é uma medição (2026-10-06)

O owner estabelecceu que **o clamp está na entrada geral da casa**, e que
**há carga real** — "até o esquentador elétrico tem ligado de vez em quando".

Com isso, o teste fecha:

| condição | `current_8` |
|---|---|
| clamp desligado do quadro | 0 |
| clamp na entrada geral, casa com carga (esquentador a ligar) | **0 — 40 de 40 leituras** |
| report espontâneo do clamp em 200 s | **zero** |

Um transformador de corrente na entrada da casa, com um esquentador a ligar
sozinho, **nunca** devolve 0 A. Logo `rmsCurrent` **não está a medir** — é um
atributo que o device implementa, responde ao `read` sem `UNSUPPORTED_ATTRIBUTE`
(ao contrário de `rmsVoltage` e `activePower`, que recusam), e devolve o valor
armazenado, que é zero.

Esta é a diferença face ao PLG300 e é que torna o caso closed:
`UNSUPPORTED_ATTRIBUTE` = o atributo não existe. `0` = o atributo existe e
está a zero. Un clamp na entrada geral com o esquentador ligado com o esquentador
ligado não está a zero. Portanto o que chega ao Zigbee **não vem do
transformador**.

### A hipótese que sobra, e que o owner tornou plausível

O owner disse que o clamp **é o transmitter CLP300** — e que é um sistema de
duas peças. O manual oficial do Cloogy descreve exactamente isso:

> **Sensor** (a pinça, ligada ao Transmissor **por cabo**) → **Transmissor**
> (pilhas, botão) → **Concentrador** → cloud

Se o CLP310 HA for o **transmissor** e a medição vier de um **sensor ligado
por cabo** a ele, então `current_8` a zero é o comportamento **correcto** de
um receptor que **não está a receber do sensor**. E o botão é, quase de
certeza, o emparelhamento com esse sensor.

Isto explicaria tudo o que medimos, sem contradição:

- `battery` responde → é o transmissor, que tem pilhas.
- `current_8` responde e é 0 → o slot existe mas não recebe do sensor.
- `rmsVoltage` / `activePower` recusam → não implementados num receptor.
- **zero** reports espontâneos → nada para reportar sem sensor.

**Não foi verificado.** É a próxima acção física: emparelhar o sensor com o
transmissor pelo botão, e depois repetir a leitura de `current_8` com carga
ligada. Se sair de 0, está resolvido.

### O CLP310 HA lê POR FASE, e só líamos a fase errada (2026-10-06)

**A pista que o owner deu e que valeu mais que tudo o resto:** *"o clamp tem 3
entradas para os trifásicos, só tem uma fase ligada, no leitor do meio"* — a
**fase B**.

O cluster `haElectricalMeasurement` tem **129 atributos**, e a leitura de fase
única (0x05xx) é só uma das três:

```
0x05xx   fase única      0x08xx   PhA        0x09xx   PhB        0x0axx   PhC
0x050b   activePower     0x0803   ...        0x090b   activePowerPhB
0x0508   rmsCurrent      0x0809   ...        0x0908   rmsCurrentPhB
```

O Zigbee2MQTT gera a definição a partir do cluster e expõe **só os de fase
única**. Numa unidade com o transformador ligado à fase B, isso devolve 0 — e
`current_8: 0` era uma leitura certa de uma fase que ninguém está a medir.

Isto **corrige a conclusão errada** registada acima. Não é que o clamp recuse
medir: recusa o atributo da fase errada.

#### `zigbee2mqtt/converters/clp310.js`

Declara os seis atributos por fase (PhA/PhB/PhC, potência e corrente). Todos os
IDs saem da tabela oficial do cluster, lida da library — a diferença face aos
`0xFC30`/`0xFCCD` é que estes têm ID, tipo e semântica oficiais.

**Estado: o converter é carregado e adoptado** (`source: external`, os seis
`exposes` presentes). **A leitura ainda não devolveu valor.** Um `read` é
emitido e o device não responde em tempo útil.

Erro próprio, registado porque custou uma iteração: a primeira versão usava
`{key, read: true, convertSet}` e o Z2M respondia
`No converter available for 'get' 'power_phase_b'` — apesar de a definição estar
correta. O caminho de leitura da library é **`convertGet`**, não `read:true` +
`convertSet` (verificado em `on.electricityMeter({endpoint:8}).toZigbee[0]` ->
`keys: key, convertGet`). `read:true` + `convertSet` é o caminho de escrita.

Também fica registado: substituir a definição gerada removeu `battery` e
`linkquality` do clamp (`No converter available for 'get' 'battery'`). Se esta
via avançar, o converter tem de os declarar também — o estado retido ainda os
mostra porque são valores em cache, não definições.

**Não escrito:** nada disto toca a produção. O clamp continua como estava.

### Última via tentada: a consola de developer do Zigbee2MQTT (2026-10-06)

A única forma de ler os clusters proprietários sem saber os attributeIDs é
executar código **dentro** do processo do Z2M, onde o `zigbee-herdsman` tem a
ligação viva ao device. Isso é a extensão `dev`, via WebSocket `ws://.../api`.

**Não existe nesta build.** Medido, não assumido:

- `ws://localhost:8080/api` liga e responde a `bridge/*` — logo o WebSocket
  funciona e o caminho está certo.
- `{type:'dev', message:{type:'execute', code:'return typeof zigbeeHerdsman'}}`
  **não recebe resposta nenhuma**, e não é por sintaxe: um comando `dev`
  válido devolve `devResponse`.
- Não existe `dist/extensions/dev.js` em `zigbee-herdsman-converters`, nem
  registo de extensão `dev` em lado nenhum do pacote.

Não é uma flag a ligar. É código que esta build não traz. (Nota para quem
voltar a isto: `docs/ZIGBEE_INTEGRATION.md` diz que "o Z2M tem REST API" —
está na tabela de falsificações, mas a leitura porcima segue errada: `/api` é
WebSocket, e a extensão `dev` é necessária.)

**Consequência:** ler `0xFC30`/`0xFCCD` exigiria ou uma build do Z2M com `dev`,
ou escrever uma extensão externa. Ambas são trabalho sobre a ferramenta, não
sobre o pHantasma, e nenhuma delas devolve dados sem alguém primeiro **observar
uma leitura** para saber que attributes existem.

### O fornecedor fechou (confirmação independente)

O owner disse que o serviço Cloogy já não existe. **Corroborado de fora:**
o Tracxn classifica a Cloogy como *exited* (perfil da empresa, entrada de
2021). E uma pesquisa dirigida por `CLP300` / `CLP310` não devolve **nenhuma**
documentação do produto — só material genérico de clamps OWON, que são outra
empresa.

Ou seja: **não há quirk para copiar, nem manual, nem fórum.** Não há caminho
externo conhecido para o firmware deste device. O que resta é o caminho local,
e ele exige a acção física do owner.

### O que tornaria isto resolvível

1. **O owner a carregar no botão do clamp** enquanto se escuta o log. Todos os
   testes feitos aqui foram passivos (pedidos `read`); o clamp é `EndDevice` e
   pode não publicar leitura espontânea. Se ao carregar aparecer um
   `attributeReport` de `0xFC30`/`0xFCCD`, os attributeIDs ficam observados e
   um converter passa a ser escrito sobre factos.
2. **Captura de rede** (ou o dump do firmware da app Cloogy) para o protocolo
   proprietário.

Nenhuma das duas é fazer sem o owner. E **esta é uma decisão dele, não minha**:
o clamp está emparelhado e mede bateria; falta-lhe só o consumo.

### O que o sistema diz entretanto

Nada de inventado. `skill_zigbee` devolve `{'state': 'sensor', 'battery_pct': 63}`
para o clamp, e a voz diz `O consumo comunica (62% de bateria, sinal 87).` — o
que o device mede, sem anunciar o que não mede (decisão do dono, 2026-10-05).
