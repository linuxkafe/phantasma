# Hostile Insights Registry

Lessons from hostile analysis across this project.
Add entries when hostile analysis reveals assumptions that proved false or critical.

## Format

## [Category] - [Brief Title]
- **Task**: [link or description]
- **Insight**: [what we learned]
- **Origin**: [Plan/Build/Verify/Review phase that revealed it]
- **Impact**: [what happened when ignored / what was mitigated]
- **Applied To**: [how approach changed since]
- **Date**: YYYY-MM-DD

---

## [Epistemics] — "o serviço caiu" vs "a conta morreu": sondar o servidor, não o sintoma
- **Task**: T051 — skill_chacon inoperante
- **Insight**: A mensagem do dono foi "a cloud deixou de estar disponível". A
  cloud estava **no ar**: TLS válido, `101 Switching Protocols`, o protocolo DIO
  a responder — e a rejeitar a *conta*. Duas sondas provaram-no: o
  `POST /api/session/login` devolve HTTP 200 com `Invalid username/password`
  (um serviço em baixo não devolve 200 com JSON estruturado), e credenciais
  inventadas devolvem a **mesma** string byte a byte. Esse segundo teste é o que
  fecha o caso: um oráculo de resposta constante prova que nenhuma sondagem
  adicional distingue "password errada" de "conta apagada".
- **Origin**: Reconhecimento (Phase 0), antes de escrever código
- **Impact**: Sem isto, a resposta would've sido implementar um bypass de LAN
  contra a premissa errada — e a família DIO não responde localmente por
  desenho, portanto o bypass não teria funcionado. O custo de pararmos para
  medir pagou-se.
- **Applied To**: Antes de aceitar "X está em baixo", distinguir o *transporte*
  do *autor*. `TLS ok` + `protocolo responde` + `credencial rejeitada` = conta.
- **Date**: 2026-09-29

## [Process] — `except ImportError` com `return None` transforma uma falha em silêncio
- **Task**: T051 — skill_chacon
- **Insight**: O `pyproject.toml` declarava `dio-chacon-wifi-api` desde T035, mas
  o venv de dev nunca a recebeu. O `except ImportError` punha um flag e
  `handle()` devolvia `None` para sempre — a skill parecia *desligada*, não
  *avariada*. O `T035-learn.md` registou isto como comportamento pretendido
  ("returns None so Tuya can handle the request"), o que transformou uma falha
  de instalação num requisito de design. Um `print` dentro do `except` não
  chega ao journald de forma filtrável nem a nenhum teste.
- **Origin**: Reconhecimento, confirmado por execução (`DioChaconApi = None`)
- **Impact**: A skill nunca funcionou em dev, durante ~10 meses após a
  dependência ser declarada. Nenhum teste, nenhuma linha de log.
- **Applied To**: O motivo do import failure tem de ser um **atributo de módulo**
  (`IMPORT_ERROR`) legível por teste, não um `print`. Acresce-se fallback das
  classes de excepção, porque os `except` do handler as nomeiam e um import
  falhado deixa-as unbound — o `NameError` esconderia a causa real.
- **Date**: 2026-09-29

## [Verification] — Declarar uma dependência não é tê-la; testar o import, não o `pyproject`
- **Task**: T051 — skill_chacon
- **Insight**: `pyproject.toml` é uma *intenção*; o venv é o *facto*. Só
  `import dio_chacon_wifi_api` diz se a dependência existe. O teste
  `test_dependency_is_importable` existe por isso, e a verificação por mutação
  provou que tem dentes: revertida a skill ao comportamento original
  (`print` + `None`), o teste falha.
- **Origin**: Build/Verify
- **Impact**: Um `grep` no `pyproject` daria "verde" para uma skill morta.
- **Applied To**: Testes que importam a dependência real, e sempre confirmar
  que o teste falha quando o bug é reintroduzido.
- **Date**: 2026-09-29

## [Scope] — Parar antes de disparar frames para hardware não identificado
- **Task**: T051 — fallback local do Chacon
- **Insight**: `10.0.0.115` (MAC Espressif) era o único candidato a plug DIO na
  rede, com uma porta que faz SYN-ACK e depois silêncio. Escrever controlo
  local contra ele seria atirar frames de um protocolo de fabricante para um
  dispositivo doméstico não identificado, porque "Espressif" é uma
  *plausibilidade*, não uma *identificação*. O pedido era explícito
  ("entrar no dispositivo directamente") e ainda assim o custo de errar era alto.
- **Origin**: Phase 1 Hostile Analysis
- **Impact**: Evitado. O trabalho parou num pedido de identificação (MAC/IP) em
  vez de gerar código não testado contra hardware real.
- **Applied To**: Antes de automação que **actua** em hardware físico, exigir
  identificação positiva do alvo.address, ou autorização explícita para sondar.
- **Date**: 2026-09-29

## [Configuration] — O plano de arquitectura tinha três erros, e nenhum se manifestava como falha
- **Task**: Zigbee local via Zigbee2MQTT (substitui o caminho Cloogy)
- **Insight**: `docs/ZIGBEE_INTEGRATION.md` afirmava três coisas, todas erradas,
  e **nenhuma delas falha de forma legível**:
  (1) `adapter: ezsp` — o Z2M recusa o valor e sai com
  `serial/adapter must be equal to one of the allowed values`, ou, sem ele,
  `No valid USB adapter found`; o correcto no 2.14.2 é `zstack`;
  (2) o plano falava em REST API — em 2.x `/api` é um **WebSocket** e o resto é
  a SPA, com HTTP 404 em `/api`, `/api/bridge/info` e `/api/bridge/devices`;
  (3) o plano tratava o Mosquitto como opcional — o Z2M sai com
  `MQTT failed to connect` **antes de abrir a porta série**.
  A quarta corolário: `onboarding: false` no `configuration.yaml` não evita o
  servidor de onboarding. Quatro tentativas, mesmo resultado, e `/data` reportava
  `onboarding: true` para um ficheiro que dizia `false`.
  Fui eu que recomendei "REST, sem Mosquitto" como a opção mais simples. A
  máquina desmentiu-me, e a opção que eu chamei de mais simples era a única que
  não existia.
- **Origin**: Phase 3 — a recommendation foi testada contra a máquina antes de
  ser adoptada, e o teste é que a refutou
- **Impact**: Sem a medição, teríamos escrito a skill contra uma API que não
  existe, com um broker que não arrancava, e o sintoma teria sido "a skill
  responde mas o coordinator está em baixo" — que parece um problema de
  configuração, não de premissa.
- **Applied To**: Um plano de integração escrito contra documentação de uma
  versão diferente do software instalado não é evidência. O `enum` do schema
  (`GET /data` no servidor de onboarding) e os logs de arranque dizem mais do
  que a página de docs. Quando uma recomendação minha é refutada pela máquina,
  é a recomendação que muda, não a máquina.
- **Date**: 2026-10-05

## [Testing] — Um health-check por subscrição só pode reportar "morto"
- **Task**: `scripts/setup_zigbee.sh` — gate de saúde do Mosquitto
- **Insight**: escrevi o gate com `mosquitto_sub -h localhost -t probe -C 1 -W 2`.
  O `mosquitto_sub` sai **não-zero sempre**, porque nada publica no tópico que
  ele espera e o `-W` expira. O script falhou com `FAILED: mosquitto is not
  answering after 30s` — e o log do broker ao lado mostrava doze
  `New client connected` seguidos de `disconnected`, todos MQTT bem. Um probe de
  subscrição espera por um evento que, num broker normalmente inactivo, nunca
  acontece; um probe de **publicação** só tem sucesso se o broker aceitou.
- **Origin**: Phase 4 — o gate falhou contra um broker saudável
- **Impact**: O script falhava sempre, e a falha apontava para o broker em vez
  de apontar para o probe. É a forma mais cara de estar errado: um teste que
  nunca passa, cuja mensagem culpa a coisa errada, e que por isso levada a sério
  durante mais tempo do que devia.
- **Applied To**: Um health-check tem de gerar o evento que espera. Se nada no
  sistema produz o evento, o probe não mede saúde — mede a ausência de algo que
  não devia existir, e falha sempre. `mosquitto_pub`, `curl`, `pg_isready`: todos
  geram o evento que verificam.
- **Date**: 2026-10-05

## [Epistemics] — `PRIORITY` ao nível do módulo é um atributo morto
- **Task**: `skills/skill_zigbee.py` — desempate com `skill_cloogy` e `skill_tuya`
- **Insight**: `LegacySkillAdapter.__init__` tem `priority: int = 0` e o loader
  constrói-o sem passar esse argumento: **nunca lê `PRIORITY` do módulo**. Medi —
  atribui `PRIORITY = 70` ao `skill_cloogy` e o loader devolveu `0`. O plano
  original escrevia `PRIORITY = 70` num módulo legacy e ficava com a ilusão de
  ter ganho o desempate.
  Com `forno` a ser trigger de `skill_cloogy` (vem de `CLOOGY_DEVICES`) e
  DEVICE_NOUN de `skill_tuya`, e ambas legacy a 0, quem respondia a "liga o
  forno" dependia da **ordem alfabética do `glob`** — `skill_cloogy` antes de
  `skill_tuya` antes de `skill_zigbee`. Um desempate que resolve por ordem de
  leitura de directório não é um desempate; é uma coincidência estável até
  alguém renomear um ficheiro.
- **Origin**: Phase 1 — leitura do `LegacySkillAdapter` antes de escolher a forma
  da skill
- **Impact**: Evitado por uma medição de uma linha. A skill nova é
  class-based como `skill_tasmota` e `skill_chacon_udp`, e há um teste que
  falha se deixar de ser.
- **Applied To**: Antes de poner `PRIORITY` num módulo de skill, confirmar que o
  loader a lê. `LegacySkillAdapter` e `Skill` são contratos diferentes e só uma
  das duas formas honra o atributo.
- **Date**: 2026-10-05

## [Testing] — `set -o pipefail` transforma um `grep -q` num teste que mente
- **Task**: `scripts/setup_zigbee.sh` — "o coordinator já arrancou?"
- **Insight**: `set -uo pipefail` (linha 3 do script) combinado com
  `grep -q`: o `grep -q` sai assim que encontra a primeira correspondência, o
  `docker compose logs` do outro lado do pipe leva SIGPIPE e sai com **141**, e
  o `pipefail` promove isso a falha do pipeline inteiro. Medido: o mesmo
  pipeline dá `exit 0` sem `pipefail` e **`exit 255`** com ele. O `if` via a
  falsidade e o script decide que o coordinator nunca arrancou — quando estava
  em pé há horas. `grep -c` consome o stream todo, o docker sai com 0, e a
  comparação numérica é a resposta.
- **Origin**: Phase 5 — o script "funcionava" mas demorava 1m55s em vez de 7s
- **Impact**: Com a queda do check, o script caía no ramo do onboarding e
  fazia polling de `POST /submit` durante os 180 s de timeout. `/submit` dá 404
  para sempre depois do arranque, portanto o polling nunca podia ter sucesso. A
  falha apresentar-se como "demora", não como "erro", é o que a torna difícil de
  ver: parece o coordinator a arrancar devagar, e `--permit-join on` era inútil.
- **Applied To**: Num script com `pipefail`, `grep -q` num pipeline é um teste
  que reporta o *pipeline*, não a *correspondência*. Usar `grep -c` e comparar,
  ou `grep -q` sem `pipefail`. Vale para `docker compose logs`, `kubectl`, curl
  com progress-bar — tudo o que não consome o stream todo.
- **Date**: 2026-10-05

## [Epistemics] — "on" que desliga, e a resposta que dizia "ok"
- **Task**: `--permit-join on` — abrir a janela de emparelhamento
- **Insight**: `permitJoin` não é booleano. Em `bridge.js:355` o payload passa
  por `Number.parseInt(message, 10)`: o comando é uma **duração em minutos**.
  `{"value": true}` → `Invalid payload`. `true` → `{"status":"ok"}` e **depois
  desliga**, com `Number.parseInt("true")` a dar `NaN` e o log a dizer
  *"disabling joining new devices"*. Só `10` produziu `permit_join: true` com um
  `permit_join_end` no futuro.
  E o caminho nem sequer era o que eu tinha escrito: `POST /submit` só existe
  enquanto o servidor de onboarding está de pé — depois do arranque dá 404 para
  sempre. `permit_join` tem de ir por `zigbee2mqtt/bridge/request/permit_join`.
- **Origin**: Phase 3 — o primeiro `--permit-join on` do dono falhou com
  `FAILED: could not set permit_join=true`
- **Impact**: O pior formato possível de falha: **sucesso falso**. A resposta
  dizia `ok`, o operador via `ok` e saía de lá a emparelhar dispositivos com a
  janela fechada. Nenhum dispositivo se emparelhava e nada indicava porquê.
  Agravado por ter tentado o endpoint HTTP duas vezes e ninguém ter visto que
  o valor — não o transporte — é que estava errado.
- **Applied To**: Uma resposta de sucesso que não é verificável não é sucesso.
  O script agora confirma pelo **estado retido** (`bridge/info` ->
  `permit_join`), nunca pelo "ok" da resposta, e `--check` lê o mesmo sítio.
  E onde a API não tem um booleano, não se oferece um booleano ao utilizador:
  `--permit-join on` abre 10 minutos e fecha-se sozinho, porque nada se
  esquece de fechar o que não precisa de ser fechado.
- **Date**: 2026-10-05

## [Epistemics] — A documentação dizia "minutes"; o código dizia "seconds", e ambos respondiam "ok"
- **Task**: `--permit-join` — abrir a janela de emparelhamento
- **Insight**: A documentação do Zigbee2MQTT descreve `permit_join` como uma
  duração em **minutos**. Não é. Em `zigbee-herdsman/controller.js:283`:
  `assert(time <= 254, "Cannot permit join for more than 254 seconds.")` e logo
  a seguir `const timeMs = time * 1000`. **Segundos**, com um teto de 254 (uint8
  no fio, 4 min 14 s).
  Escrevi "25 minutos" num script porque a doc dizia minutos, e a janela que se
  abriu foi de **25 segundos** — medido, `permit_join_end` 20,8 s no futuro. O
  Z2M registou `{"data":{"time":25},"status":"ok"}` e o meu script imprimiu
  "JOIN WINDOW OPEN for 25 minutes". Nenhum sinal de erro em lado nenhum.
- **Origin**: Phase 3→5 — só apareceu quando a janela "de 25 minutos" expira em
  segundos e o dono não conseguiu emparelhar nada
- **Impact**: Um erro por um factor de 60, invisível do lado do servidor, que se
  apresenta como "o dispositivo não se junta". O diagnóstico aponta para o
  hardware, para o alcance, para o factory reset — e a causa é que a janela foi
  um sexto do que se pediu. Pior: `--permit-join on` com um valor inventado é
  exatamente o comando que alguém usaria para "tentar de novo", e Volta a dar
  `ok`.
- **Applied To**: Um valor com asserção no código vale mais do que a
  documentação desse valor. A diferença entre "minutes" e `time * 1000` com
  `<= 254` foi mais informativa que a página de docs inteira. E quando um
  CLI aceita um número, a conversão para a unidade do sistema de baixo tem de
  acontecer **no** CLI, com o teto verificado lá — para que o teto seja um
  `exit 2` com explicação e não um assert dentro de um container.
- **Date**: 2026-10-05

## [Scope] — Um clamp emparelhado que não mede: o sucesso do emparelhamento não é o objetivo
- **Task**: `0x00124b000204eb88` — o "Consumo Total" que vinha do Cloogy
- **Insight**: Juntou-se à primeira: `Clamp CLP310 HA` / `VirtualPowerSolutions`,
  interviewado, 36 mensagens trocadas. E **não mede**. `power` e `voltage`
  devolvem `UNSUPPORTED_ATTRIBUTE` no endpoint 8; `battery` (62%) e
  `linkquality` (129) respondem. O Z2M avisa que o modelo não é suportado e cai
  numa definição gerada automaticamente, que *promete* `power_8` — uma
  propriedade que o device recusa quando alguém a pede.
  Um device que junta, responde e mede zero é um resultado que só é visível
  só se se perguntar a coisa que se queria. "Emparelhou com sucesso" e "dá a
  leitura que veio buscar" são duas propriedades diferentes, e o relatório só
  diz a primeira.
- **Origin**: Phase 3 — medição depois do emparelhamento, não antes
- **Impact**: A primeira versão da skill lia só `power` (não `power_8`, que é
  onde o Z2M publica medição por endpoint) e mandava `/get` sem payload, que o
  Z2M tratava como falha total. Resultado: **"Nao consegui ler"** para um device
  perfeitamente saudável, e a culpa apontada ao broker. Passou a pedir os campos
  que cada `kind` suporta — `/get {"battery":"","linkquality":""}` responde, e
  a frase passou a ser "O consumo comunica (62% de bateria, sinal 126), mas não
  devolveu potência nem estado". Um sensor que mede nada tem de dizer que não
  mede, não devolver 0 W.
- **Applied To**: Depois de um emparelhamento bem-sucedido, **perguntar ao
  device a leitura que motivou a acção**. Um `device_joined` no log prova que
  o device se juntou, e nada mais. Se a leitura pedida não vier, isso é um
  resultado, e é um resultado que se regista — não um device que se arquiva
  como "emparelhado".
- **Date**: 2026-10-05
