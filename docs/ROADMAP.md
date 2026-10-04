# Roadmap

## [HIGH] Project Initialisation & AES Setup
- Impact: High
- Effort: Low
- Status: done

## [HIGH] Core Voice Pipeline Integration
- Impact: High
- Effort: High
- Status: done
- Description: Integrate openWakeWord → Whisper → Ollama → Piper pipeline in assistant.py with VAD gating
- Completed: 2026-09-17 (commit eb41968)

## [HIGH] Skills System Implementation
- Impact: High
- Effort: Medium
- Status: todo
- Description: Dynamic skill loader; base skill classes; example skills (calc, weather, music, memory)

## [HIGH] Long-term Memory (SQLite RAG)
- Impact: High
- Effort: Medium
- Status: todo
- Description: Skill for "memoriza isto..." / "o que memorizei?" with SQLite storage and retrieval

## [HIGH] SearxNG Web Search RAG
- Impact: High
- Effort: Medium
- Status: todo
- Description: Skill to enrich Ollama responses with real-time search via local SearxNG

## [HIGH] Tuya Local Control Skill
- Impact: High
- Effort: Medium
- Status: todo
- Description: skill_tuya.py with DPS mapping (1=switch, 20=light); DHCP reservation required

## [HIGH] Xiaomi Local Control Skill
- Impact: High
- Effort: Medium
- Status: todo
- Description: skill_xiaomi.py for Viomi vacuum and Yeelight via python-miio

## [MEDIUM] REST API & CLI
- Impact: Medium
- Effort: Low
- Status: done
- Description: Flask /api/command endpoint; phantasma-cli.sh wrapper
- Completed: 2026-09-17 (src/api/ implemented)

## [MEDIUM] Audio Feedback System
- Impact: Medium
- Effort: Low
- Status: todo
- Description: Random music snippet + greeting on hotword using mpg123

## [MEDIUM] Systemd Service & Deployment
- Impact: Medium
- Effort: Low
- Status: todo
- Description: phantasma.service with venv PATH, Nice=19, auto-restart

## [LOW] Config Management & Validation
- Impact: Low
- Effort: Low
- Status: todo
- Description: config.py schema validation; device discovery helpers

## [LOW] Custom Hotword Training
- Impact: Low
- Effort: High
- Status: backlog
- Description: Train "Ei Fantasma" custom onnx model via Google Colab / local training

## [LOW] Tuya UDP Daemon
- Impact: Low
- Effort: Medium
- Status: backlog
- Description: Background daemon to collect Tuya device state via UDP for sensors

## [LOW] Test Coverage & CI
- Impact: Low
- Effort: Medium
- Status: backlog
- Description: pytest suite; GitHub Actions CI; make check quality gates

## [HIGH] [DISCOVERED 2026-09-30] `/api/command` responde que ejecutou e não executou
- Impact: High
- Effort: Medium
- Status: backlog
- Description: `_handle_device_command` e `_handle_memory_command` (src/api/routes.py) são stubs: devolvem `CommandResponse(success=True, text="Comando de dispositivo executado.")` sem tocar num único dispositivo. A rota de voz `/api/voz` chamava-os e por isso confirmava falsamente que a luz se apagou — corrigido em T054, que passou `/api/voz` a `pipeline.respond_to_text`. **A mentira continua viva em `/api/command`**. Não foi corrigida em T054 porque o cliente Android (o único caller no repo) tinha um contrato próprio; esse cliente foi removido a 2026-09-30, portanto a razão já não existe e a decisão tem de ser revista. Ou `/api/command` passa pelo pipeline, ou passa a dizer que não faz nada. Enquanto lá estiver, um `success=True` vindo de `/api/command` não significa nada. Note-se que a rota continua na gate de comandos (`/comando`, `/device_action`, `/api/command`) e por isso aceita um token de máquina que qualquer programa na rede possa ter.

## [MEDIUM] [DISCOVERED 2026-09-30] `/api/voz` não abre a janela de feedback
- Impact: Medium
- Effort: Low
- Status: backlog
- Description: `_process_speech` (assistant.py) abre uma janela de feedback depois de uma resposta do LLM, para o "obrigado"/"não faz sentido" em linguagem natural (SD-DOMAIN-001). `/api/voz` responde e não abre. A caixa está lá; a rota de voz é que não a usa. Fora do âmbito de T054 por ser ortogonal ao defeito reportado.

## [MEDIUM] Migrate off webrtcvad (drop setuptools<81 pin)
- Impact: Medium
- Effort: Medium
- Status: backlog
- Description: [DISCOVERED mid-task T031] Replace webrtcvad 2.0.10 (imports pkg_resources; setuptools>=81 removed it) with a pkg_resources-free VAD binding, then lift the `setuptools>=61,<81` pin in pyproject.toml and Dockerfile. setuptools<81 is EOL — this is a stopgap.

## [MEDIUM] Compose healthcheck mismatch in assistant mode
- Impact: Medium
- Effort: Low
- Status: backlog
- Description: [DISCOVERED mid-task T031] docker-compose.yml healthcheck curls :5000/health, which is only served in `api` mode; assistant mode container shows `unhealthy` while the pipeline runs fine. Fix: make assistant mode expose health, or change healthcheck.
## [MEDIUM] [DISCOVERED 2026-09-27] GMIF não classifica nós do grafo

- **Impact**: médio. O grafo tem 2 nós + 1 aresta para 61 memórias. Só a
  aresta tem claim GMIF (`logical_form`, `level=M3`, `validation_type`).
  Os 2 nós têm `extraction_confidence=0.0`, `validation_confidence=0.0`,
  `logical_form=''`, `level=NULL`, `validation_type=NULL`. O
  `node_gmif_confidence=0.8` que aparece nos nós é um default fixo, não uma
  classificação — contar colunas não-nulas dá uma leitura enganosa.
- **Effort**: médio. Requer classificar claims nos nós, não só nas arestas.
- **Status**: por fazer — bloqueado por dependência de Ollama.
- **Why blocked**: o único caminho de saída (`_research_gap` →
  `_safe_ollama_chat`) escreve em `http://10.0.0.128:11434`, inacessível.
  O journal mostra o ciclo a correr e a falhar sempre aí, pelo que o grafo
  nunca cresce. Decidir o destino do Ollama primário é pré-requisito.
- **Related**: `skills/skill_gmif_dream.py` também usa
  `datetime.now().strftime("%H:%M") == "03:00"` — igualdade de string num
  loop de 30s. Funciona por margem, mas perde o dia se a máquina suspender
  ou o loop atrasar >60s. Redesign, não bug.

## [RESOLVIDO] [2026-09-27] `config.py` / `audio_utils.py` — bifurcação eliminada

- **Causa**: valores de host viviam no código, não no ambiente. `block_size`
  (512) e `auto_detect` (False) estavam fixos no dataclass de prod sem override
  possível, e `TTS_CACHE_DIR` estava hardcoded em `audio_utils.py`. Como não
  havia env var, a única forma de afinar a prod era bifurcar o ficheiro.
- **Correcção**: `AUDIO_BLOCK_SIZE` e `AUDIO_DEVICE_AUTO_DETECT` passaram a ser
  lidos do `.env`; `TTS_CACHE_DIR` foi promovido a campo de config (o
  `getattr(config, "TTS_CACHE_DIR", ...)` de `audio_utils.py` existia mas o
  atributo não, logo a indirecção era **inerte** e caía sempre no default
  hardcoded). Os três ficheiros passaram a ser byte-idênticos.
- **Bug adicional encontrado**: o `.env` de prod declarava `AUDIO_AUTO_DETECT`
  mas o código lê `AUDIO_DEVICE_AUTO_DETECT`. A linha estava morta — editá-la
  não fazia nada, e o default ("true") ganhava. Neutralizada com comentário.
- **Verificação (reformulada após peer review)**: os valores efectivos em prod
  **depois** são `block_size=512`, `auto_detect=False`, `device_in=0`,
  `sample_rate=16000`, `volume_percent=85`, `threshold=0.7`, `persistence=2` —
  reproduzível por qualquer revisor com
  `cd /opt/phantasma && ./venv/bin/python3 -c "...;import config;print(...)"`.
  A alegação original de "idênticos antes e depois" foi **retirada**: `/opt/phantasma`
  não tem commits e o `config.py` pré-refactor não é recuperável, portanto a
  metade "antes" era NÃO-VERIFICÁVEL e dependia apenas da palavra do autor.
  Isto é uma lacuna real de proveniência, não um，示意 de que houve regressão:
  o `block_size=512` de prod é um valor que já vinha do dataclass *antes* do
  refactor e que o `validate.sh` fixa agora como invariante verificada.
- **Achado do peer review (BLOCKER, corrigido)**: mover o valor para o `.env`
  criou um *fail-open*. Sem `.env`, ou com o nome escrito errado, `block_size`
  ia a 1600 e `auto_detect` a True em silêncio — pior que a bifurcação
  anterior, que pelo menos era visível num `cmp`. Corrigido com chaves em
  `.env.example` e um gate no `deploy.sh` que falha com exit≠0.
- **Prevenção**: `deploy.sh` imprime `WARN <ficheiro> diverges` se algum destes
  divergir — é um tripwire para detetar um valor de host a vazar para o código.


## [BACKLOG] [2026-09-27] TLS / reverse proxy à frente do phantasma

**Estado:** adiado pelo utilizador — "deixa o proxy em backlog, trato disso depois".

### O que é
O serviço escuta em `0.0.0.0:5000` **em claro**, e a máquina está em
`10.0.0.111` (RFC1918). Não há reverse proxy: o `node` que ocupa :80/:443 é o
`copilot-api` em `127.0.0.1:4141`, não um proxy. O acesso externo chega ao 5000
por **port-forward do router**, e é por isso que o bind `0.0.0.0` é necessário.

### Porquê o bind NÃO pode ser mudado para 127.0.0.1
Foi-me proposto, e está errado. Sem proxy, mudar para `127.0.0.1` **tira o
acesso ao admin**. O `0.0.0.0` não é descuido: é o que torna o port-forward
funcional.

### A correcção, quando for feita
1. Reverse proxy com TLS (caddy/nginx) em `127.0.0.1:443` e `127.0.0.1:80`.
2. `proxy_pass` para `127.0.0.1:5000`.
3. **Só depois** mudar o port-forward do router para o proxy (443), não para o 5000.
4. **Só depois** mudar o bind da app para `127.0.0.1`.
5. A linha de `client_key()` em `src/api/ratelimit.py` passa a poder confiar no
   proxy — mas apenas o **último salto**, nunca o header cru. Ver o aviso no
   docstring dessa função.

### Evidência de que é urgente (mas não activo)
30 dias de log: **296 sondagens** a `.env`, `.git/config`, `.aws/credentials`,
`.codex/auth.json`, `actuator/env`, `phpinfo.php` — e 2 a `/.git-credentials`.
**Todas devolveram 404**, sem fuga de segredos, e o `/admin` OTP não foi
followers. Mas o vector continua aberto enquanto o 5000 estiver em claro.

Ver `audit-uso-rotas.md` §5.

## `--force-host` em config.py — 2026-09-28

O gate de deploy exige `config.py` byte-idêntico entre dev e prod, para
impedir que um valor de host vaze do `.env` para o código. Disparou ao
publicar a correcção do grafo/RAG.

Verificado antes de forçar:
- ASTs idênticos (mesmo código, outra formatação).
- Diff por palavras: apenas parênteses e quebras de linha. Nenhum token,
  string ou endereço diferente.
- O ficheiro já continha `10.0.0.128:11434` e `127.0.0.1:8081` antes
  desta alteração; nada novo foi introduzido.

Causa: `ruff format` reformatou `config.py` no dev e o gate compara
bytes. O código não mudou. Reformatado com `--force-host`.

## `--force-host` em config.py — 2026-10-01

O gate byte-idêntico voltou a disparar, agora por uma mudança de código
deliberada: `memory_db_path` e `brain_db_path` passaram a resolver ambos
`data/brain.db` por omissão (antes `memory.db` e `flybrain.db`). Não é um
valor de host — é a correcção de que a memória e o flybrain são UM store.
O prod já apontava ambos para `data/brain.db` via `.env`, portanto o
comportamento em produção não muda: o `.env` continua a sobrepor. O que muda
é que um checkout sem `.env` deixa de se dividir em dois ficheiros e de
mostrar um grafo vazio no `/admin`.

Verificado antes de forçar:
- A mudança está nos defaults e no comentário; nenhuma credencial, IP ou
  chave foi introduzida.
- Prod resolve ambos os nomes pelo `.env`; o default de prod não é lido.
- O gate de áudio (`block_size=512`, `auto_detect=False`) passou antes e
  depois.

Forçado com `scripts/deploy.sh --force-host`. A partir daqui os dois
ficheiros voltam a ser byte-idênticos.

---

## Pendências em aberto depois da sessão de 2026-10-01

Registo do que ficou por decidir ou por fazer depois de
`2b81839`, `5a94b35`, `9651a0b`, `bcf3937`, `7a4fea4` e `573a523`.
Nada disto está a falhar em produção neste momento; são três decisões e um
pedido de âmbito. (Ticket AES: T057.)

### 1. Edição simplificada de `.env`, talvez no grafo 3D

**Pedido do dono:** "Variáveis devia permitir edição simplificada
diretamente, se possível até diretamente no gráfico 3D."

**Estado:** não iniciado. `/admin/env` continua a ser um único `<textarea>` com
o ficheiro `.env` inteiro, uma variável por linha.

**Porque é mais do que parece:**

- Um `POST` reescreve **todas** as variáveis de `.env`, não a que se editou.
  Portanto "edição simplificada" não é um textarea mais bonito: é escrita por
  chave, com validação por variável, e um caminho que não reescreve o ficheiro
  inteiro a cada guarda.
- `.env` é onde vivem as credenciais (`PHANTASMA_COMMAND_TOKEN`, chaves de API,
  Discord). `CLAUDE.md` diz que é "o único sítio onde vivem valores de host".
  Trazê-lo para o grafo 3D punha gestão de segredos a um clique de uma
  superfície de visualização.
- A armadilha já vista duas vezes nesta sessão: uma variável de ambiente que o
  código não lê. `DREAM_OLLAMA_TIMEOUT` estava no `.env` de produção e era
  morta. Qualquer variável exposta aqui precisa de um teste que prove que
  editar aquilo muda mesmo o comportamento.

**Decisões que faltam:**

1. Que variáveis são seguras de expor? (as de dispositivo e visualização
   talvez; segredos talvez não)
2. "No grafo 3D" significa um painel nessa página, ou editar um nó do grafo?
3. Escrita por chave com validação, ou aceitar o risco do ficheiro inteiro?

### 2. `/api/graph/resolve` devolve 401 sob o bypass de loopback

**Estado:** conhecido, por corrigir, **não foi mexido** por ser uma decisão de
segurança.

O botão "Aplicar" faz `POST /api/graph/resolve`. Esse path está em
`_TOKEN_PATHS` (`src/api/routes.py:428`), e o `before_request` chama
`_command_authorized()`, que aceita o token de comando ou uma sessão de browser
— **mas não** o bypass de loopback. O bypass só abre `/admin/*`.

Medido em produção: com o bypass activo (como entram a suite e o
`deploy.sh`), o "Aplicar" responde sempre `401 Authorization required`. Com
login real funciona.

O botão esteve partido duas vezes — primeiro pelo `data-ref-mode` errado
(`2b81839`), agora pela auth. Corrigi o primeiro e deixei o segundo de fora de
propósito: alargar o bypass a `/api/graph/*` é alargar o que qualquer processo
local pode escrever, e `CLAUDE.md` proíbe mexer nos testes de segurança por
conta própria.

**Opções:** (a) nada — com login real já funciona, e o bypass é para a suite;
(b) o bypass valida também `/api/graph/*`, mantendo o log de WARNING;
(c) o browser envia o token de comando, o que resolve sem mexer na auth.

### 3. O GMIF promovou fragmentos de conversa a relações

**Estado:** observado depois do ciclo de 2026-10-01, por corrigir.

`gmif_dream` passou a correr (já não morre em `KeyError: 'label'`) e aplicou 3
arestas novas, M1 → M2, como `dream_gmif_research`. Duas ligam lixo:

```
Olá! O nome "Bimby" parece ser associado a gatos que têm nomes próprios... -> Ah, a chuva...
Olá! O nome "Bimby" parece...                                            -> Bom, parece que há um mistério aqui...
```

São fragmentos de conversa do LLM promovidos a nós do grafo. O portão de
evidência aceitou-os, o que quer dizer que o teste não é forte o suficiente
para isto. E algumas queries de pesquisa são degeneradas — uma citação de
memória e até um `SELECT * FROM tabela WHERE` foram enviados ao SearXNG.

**Risco:** cada ciclo pode acrescentar mais destas — `MAX_RESEARCH_PER_CYCLE`
é 3.

**Por abrir:** a decisão é sobre o que conta como evidência. Um nó cujo label
é uma frase de conversa não deve ser promovível, e isso é um filtro no
`_apply_research_to_graph` ou um teste no que o classificador aceita.

### Registo de validação da sessão

| | |
|---|---|
| produção | 1281 passed, 14 skipped; `/api/health` 200; seis componentes saudáveis |
| dev | 1278 passed (os 17 erros de hotword são pré-existentes, falta o modelo .onnx) |
| lint | limpo |
| backups da `brain.db` | `/tmp/opencode/predeploy/`, um por deploy |
| `.env` | `/tmp/opencode/env.bak.20261001-150556` |

---

## [HIGH] [DISCOVERED 2026-10-02] O grafo 3D não renderiza num clone

O `/admin/brain` embute `/memory/3d`, que carrega
`public/vendor/three.module.js`, `public/vendor/OrbitControls.js`,
`public/vendor/mermaid.min.js` e `public/static/mermaid_graph.mjs`.

**Nenhum dos quatro está no git.** `git ls-files public` devolve só
`explorer.mjs`. Todos os quatro existem em `/opt/phantasma/public/`, e só lá.

**Efeito:** num clone limpo, `/memory/3d` fica em "a carregar o grafo..." para
sempre e o canvas nunca aparece. Não dá 404 no `/admin/brain`, não dá erro de
consola útil — o `importmap` resolve e o `mermaid_graph.mjs` é `type=module`,
portanto a falha aparece só como um módulo em falta. A mesma classe de
problema que o T057 registou com o `ADMIN_TEMPLATE`: **devolve 200 e não
funciona.**

**Decisão que falta:** `public/vendor/` é biblioteca de terceiros e deve ser
ignorada com um `make fetch-assets` (ou um `.gitignore` + nota de instalação);
`public/static/mermaid_graph.mjs` é código nosso e devia estar no git. Os dois
não podem ser tratados da mesma maneira.

**Nota:** esta descoberta foi feita porque T058 precisava de screenshots. Não
foi corrigida — é um ficheiro que não escrevi e não revi.

## [MEDIUM] [DISCOVERED 2026-10-02] `#corrigir` é markup morto em `/admin/brain`

`<section id="corrigir">` está **fora** do `</main>` do `BRAIN_TEMPLATE`
(`src/api/admin.py:1762`), e por isso é filho directo de `<body>`. Medido em
produção a 1440×900: `y=985`, altura **12821px**, inteiramente fora do
viewport, e `document.elementFromPoint` sobre os seus `<input>` devolve `null`.
Isto é, inalcançável, em todos os tamanhos.

Duplica o editor que existe de facto em `/admin/memory`. A barra de topo
fullscreen escondia-o por acidente (`body{overflow:hidden}`); com a página em
fluxo passaria a roubar ~549px de uma janela de 900px ao grafo, por isso o T058
o esconde **de propósito** (`body.brain-fullscreen > #corrigir{display:none}`)
em vez de o apagar.

**Decisão que falta:** apagar, ou mover para dentro do `main` com um sítio onde
seja alcançável. Apagar é a hipótese óbvia — mas há um link
`<a href="#corrigir">` no mesmo template que também morre com ele, e isso é
informação que o dono tem de ter antes de decidirmos.

## [MEDIUM] [DISCOVERED 2026-10-02] `test_brain_layout_rendered.py` aponta para produção

`BASE = os.environ.get("PHANTASMA_URL", "http://127.0.0.1:5000")` e `:5000` é
`/opt/phantasma/assistant.py` — a cópia de produção, separada da árvore de
desenvolvimento. Um teste de layout que corre contra o deploy de ontem passa
com o código de hoje quebrado, e é exactamente o modo de falha que o comentário
`OWNER_VIEWPORT` do `test_brain_hub_clickables.py` descreve ("passa em CI e falha
no ecrã do dono").

`tests/test_brain_top_line.py` resolve isto para o `/admin/brain`: levanta o seu
próprio servidor efémero, como o `test_brain_hub_clickables.py`. O ficheiro
antigo devia fazer o mesmo.

### Registo de validação (T058, 2026-10-02)

| | |
|---|---|
| suite completa (dev) | 1344 passed, 17 errors |
| os 17 errors | `test_hotword.py`, `melspectrogram.onnx` em falta no pacote instalado — pré-existentes, os mesmos do T057 |
| `tests/test_brain_top_line.py` | 25 passed; **23 falham contra o `HEAD` antigo** (falsificação verificada) |
| falsificação | corrida com `admin.py` em `HEAD`: 23 failed, 2 passed |
| lint | `ruff check` limpo |
| screenshots | de um servidor de **dev** com BD isolado, nunca de `:5000` |

## [MEDIUM] [DISCOVERED 2026-10-02] `/admin/flybrain` tem um erro de sintaxe no JS

`FLYBRAIN_TEMPLATE` carrega um script inline que o browser recusa com
`Invalid or unexpected token`. A página responde **200**, o HTML parece
completo, e nada no teste suite o apanha porque nenhum teste abre esta página
num browser.

Verificado como **pré-existente**, não introduzido pelo T058: a mesma mensagem
aparece em `:5000`, que corre o código de `HEAD` (`/admin/flybrain 200
nav=1200px err=['Invalid or unexpected token']`).

Mesma classe do que o T057 registou duas vezes: **devolve 200 e não funciona.**
A lição de T058 aplica-se — só um browser sabe; um `assert "gestaoSleep" in
body` passa e não prova nada.

Onde: `src/api/admin.py`, `FLYBRAIN_TEMPLATE` (a partir da linha 2186). O
selector `sleepStatusLine()` e `SLEEP_STEP_LABELS` vivem lá dentro, portanto
o script está inteiro a não correr, e não é um token solto.

---

## [HIGH] [DISCOVERED 2026-10-02] O pipeline AES não funciona em projectos

`generate-index.py:15`, `conflict-detection.py:13` e `narrative-analysis.py:18`
fazem os três `AES_ROOT = Path(__file__).resolve().parent.parent` — a pasta da
**skill**, não a do projecto. Sem override por env nem por argv. Correr estas
scripts num projecto analisa o repositório AES e reporta-o como se fosse o
projecto.

Os Makefile targets que as skills invocam (`make conflict-check`,
`make narrative-analysis`, `make index`) **não existem** em lado nenhum.

`aes/INDEX.md` diz *"Auto-generated by `make index`"* — gerado por um comando
inexistente, num formato (`## SD-… — Título`) que o gerador não lê: procura
`**SD-…**` em bold, e o ficheiro tem 5 headings e 0 bold.

**Workaround aplicado** em `docs/aes-infra-audit.md`: partir os scripts por
módulo e religar `AES_ROOT`/`SHADOW_DIR`/`INDEX_FILE`/`ACCESS_LOG`. A lógica
distribuída fica intacta; só os caminhos mudam.

**Correcção que falta, e é na distribuição AES:** `AES_ROOT` deveria aceitar
override (`AES_ROOT` env, ou `--root`). Enquanto não aceitar, qualquer skill
epistémica dá resultados sobre o AES, não sobre o projecto. Detalhe em
`docs/aes-infra-audit.md`.

## [HIGH] [DISCOVERED 2026-10-02] Um score sem dados é pior que ausência de score

`aes-narrative` reportou **3/8 MODERATE** neste projecto. **0 das 5 dimensões
têm dados válidos**:

* **Omission 100%** — falso positivo. O parser procura `\*\*SD-…\*\*`; o INDEX
  usa `## SD-… — Título`. Devolve 0 indexados, e 0 de 13 é 100%.
* **Pinning 0,0% "equilibrado"** — razão 0/0.
* **Access concentration Gini 0,000 "uniforme"** — `aes/shadow/access.log`
  **não existe**. Verde por vacuidade.
* **Synthesis coverage 100%** — 0 docs acedidos, 0/0.
* **Score clustering** — "No scores available", mas os 13 docs **têm** score.

**A regra que disto sai, e que devia ser invariante de todos os detectors:**
razão `0/0`, ficheiro ausente, ou parser que não reconhece o formato → o
resultado é `NÃO-VERIFICÁVEL`, **nunca** `0,000` nem `100%`. Uma tool que não
percebe o ficheiro está a mentir com precisão, e um relatório que ocupa o lugar
de uma decisão é pior do que um relatório inexistente.

O `tests/conftest.py` já descreve isto para os testes ("a green suite that
proved nothing because the thing under test was disabled"). Falta o mesmo
invariante do lado dos detectors.

## [MEDIUM] [DISCOVERED 2026-10-02] `aes/shadow/access.log` não existe

Não há registo de que shadow docs tenham sido consultadas. Consequência
directa: **nenhuma decisão futura pode ser reconstituída** a partir de memória
anterior. `aes-conflict` passou sem avisos na mesma — as verificações de órfãos,
causalidade e contradição não tinham entradas para scrutinizar.

Criar o ficheiro vazio seria pior: fingiria um histórico que não existe.

## [MEDIUM] [DISCOVERED 2026-10-02] `aes-debt` e `aes-epistemics` não correm

`aes-debt` precisa de `scripts/epistemic-debt.sh` e
`aes/metrics/epistemic-debt.log` — nenhum existe. `aes-epistemics` precisa de
`aes/graph/` e do solver Z3 (não instalado; `apt`, ou seja sudo).

**Não foram Metro.** Escrever o script de dívida seria inventar a definição da
métrica e depois reportar o número — a forma exacta de produzir uma
quantidade que parece evidência e não é. Criar `aes/graph/` com premissas
extraídas por um LLM do código é a circularidade que a própria epistemics
existe para evitar.

## [HIGH] [DISCOVERED 2026-10-02] O portão do grafo só cobre dois dos três escritores

Revisão T061 à mudança T060: **BLOCKER encontrado e corrigido**.
`materialize_memories` filtrava só por comprimento e nunca chamava o portão, e
é o escritor que produziu **179 dos 190 nós** de produção, chamado todas as
noites por `skill_dream.py:698`.

Reproduzido contra `641844d`: payload `["pt-BR", "Bom, parece que há um
mistério aqui...", "Leite"]` escrevia 3 nós e 3 arestas pelo
`materialize_memories`, com a fala e o marcador de idioma lá dentro. Corrigido.

**Pendentes que a revisão deixou, por ordem:**

1. **Nada regista o que o portão rejeitou.** Um conceito bom rejeitado é
   indistinguível de um que nunca foi extraído. `logger.info`? estatística no
   `/admin`? Decisão do dono.
2. **`upsert_node`/`upsert_edge` continuam sem portão.** `skill_dream.py:816`
   escreve por lá, e `_concept_from_reply` (`skill_dream.py:883`) é um segundo
   portão divergente. Mover o portão para `upsert_node` é o passo certo e é de
   maior âmbito do que o aprovado.
3. **`apply_reward` ressuscita o nó apagado.** Faz `ON CONFLICT DO NOTHING`, ou
   seja recria pela chave do tópico. Um 👍 sem texto traz `node:tag` de volta —
   e é `node:tag` que é o tópico corrente em produção. Depende do ticket de
   limpeza, e o AC desse ticket tem de incluir
   `UPDATE topic_state SET current_key=NULL`.
4. **A lista de idiomas é fechada e já tem um buraco.** `it`, `nl`, `portugues`
   passam. Ou deriva de uma fonte única, ou passa a ser por campo do payload.
   E `LANGUAGE_TAGS` não é do "tagger": não há tagger. O único prompt que dita
   uma tag (`skill_memory.py:43`) dita `Tag`, e `Tag` foi acrescentado à lista.
5. **Recall conhecido do portão, declarado num teste.** Uma fala sem reticências
   e sem opener+vírgula passa. Endurecer a regra volta a comer conceitos, e o
   custo de perder um conceito é maior. Decisão do dono se quiser o contrário.
6. **Sem kill switch.** Reverter hoje é editar código + `deploy.sh`.
   `settings_store.get_setting` é o mecanismo que já existe.
7. **`_LEADING_JUNK_RE` limpa para a decisão e guarda o lixo** — é o defeito
   B6 e a correcção é de lá.

**E uma correcção ao meu próprio processo:** a medição de raio de blast do T060
estava viciada. Medi falsos positivos sobre "os oito nós mais curtos que
sobrevivem" (`Tag`, `casa`, `gato`), e nenhum começa por um opener nem tem
reticências — a lista não continha nenhum membro da classe que a regra nova pode
rejeitar, portanto não podia falhar, portanto não certificava nada. Três personas
viram isso ao mesmo tempo. Uma verificação que não pode falhar não é uma
verificação.

## [HIGH] [DISCOVERED 2026-10-02] O motor wikipedia do SearXNG estava morto e parecia saudável

`searxng-settings.yml` tinha `wikipedia` como motor, `disabled: false`, desde
sempre. **Devolvia zero resultados a todas as consultas** e **não aparecia em
`unresponsive_engines`** — um motor morto que se apresenta como vivo.

Causa: `wikipedia.py:77` declara `display_type = ["infobox"]`. Um artigo
"standard" só é emitido como infobox, e o serializador JSON não coloca infboxes
em `results`. A aplicação pede `format=json` via `tools._searxng` e via um bloco
inteiro vazio — enquanto a interface web do SearXNG mostrava um painel e
fazia parecer que funcionava.

**Esta é a razão pela qual `tools.py` tinha uma segunda porta para a
Wikipédia.** O motor estava configurado e nunca funcionou, e a porta era a única
coisa que respondia. T063 removeu a porta; sem `display_type: [list, infobox]`
isso teria removido a Wikipédia da pesquisa de todo.

Corrigido e verificado em produção: `q=portugal` → `Portugal – Wikipédia`.

### A pista errada, registada porque errar é o método

O primeiro diagnóstico foi **User-Agent**. Do contentor, sem UA, a Wikipédia
devolve 403. Escrevi o bloco `outgoing:` por causa disso — e estava errado:
`searx/1.0.0` devolve 200 tal e qual. O 403 vinha do `Python-urllib` do meu
próprio script de sondagem, não do SearXNG. O bloco ficou, com o motivo
corrigido no comentário, mas a causa era o `display_type`.

### E o que estava a acontecer no log, ao abrir isto

Ao procurar a causa, o log do SearXNG mostrou o motor `wikipedia` a ser
consultado com **payloads de injecção de SQL** como se fossem títulos de
artigos:

```
https://pt.wikipedia.org/api/rest_v1/page/summary/SELECT%20%2A%20FROM%20(
  SELECT 'Mecanismo de Concentração de Capital' AS mecanismo ... 
  UNION SELECT ... FROM information_schema.tables ...)
```

Datas no log: **2026-09-28 e 2026-09-30**. Isto é o item 3 do T057, ainda por
corrigir: o ciclo de sonho manda consultas degeneradas para o SearXNG. Não é
um ataque externo — é o LLM local a gerar SQL a partir de conteúdo, e o
SearXNG a obedecer e a tentar ir buscar um artigo com esse nome.

**Risco real:** `information_schema.tables` é reconhecimento de base de dados.
Não funcionou porque a Wikipédia rejeitou com 400, e não porque houvesse uma
defesa. Se um dia um motor aceitasse a query, o pedido saía com Reconhecimento
de esquema. E o custo é o mesmo mesmo sem ataque: cada chamada é uma ida ao
SearXNG a perguntar por um artigo chamado `SELECT * FROM information_schema`.

**Correcção pendente (T057 §3):** o gate de promoteabilidade do T060 é a
ferramenta certa — `_research_gap` e `_concept_from_reply` devem rejeitar
labels que não são linguagem natural. E o gate **não** foi aplicado ao caminho
`reconcile.py:116`, que é por onde esta consulta saiu.

## [HIGH] [DISCOVERED 2026-10-02] A allowlist dos convidados nao existia; a quota era contornavel

**A regra parecia uma allowlist e nao restringia nada.** `ALLOWED_SKILL_KEYWORDS`
era uma lista de sub-cadeias do prompt que so decidia se o pedido gastava quota.
Verificado:

```
convidado, acesso isolado:
  PERMITE  'toca uma musica'
  PERMITE  'inicia o sonho'
  PERMITE  'acende aTv do quarto'
  PERMITE  'abre as persianas'
```

`music`, `dream` e os dispositivos: todosreachable. Nao havia allowlist nenhuma --
`skill_chacon`, `skill_tuya` e `skill_xiaomi` nunca foram postas numa lista.

**E a quota nao era uma quota.** `+`, `-`, `*` e `/` estavam na lista, e isso casa
com qualquer pedido que contenha o caractere:

```
'conta-me uma historia'   8 pedidos -> 8/8 passaram
'conta me uma historia'   8 pedidos -> 3/8 passaram
```

Um hifen tornava o limite inexistente. A lista original era, afinal, uma copia a
mao dos TRIGGERS do `skill_calculator` -- e por isso divergiu do que as skills
fazem.

**Corrido.** A decisao passa a ser tomada sobre **qual skill o pedido toca**,
resolvida antes de gastar seja o que for (`resolve_matching_skills`), e a regra e
"qualquer skill casada fora da allowlist recusa". Allowlist e nao blocklist, por
decisao do dono. A quota passou a contar **tudo** -- inclusive o que uma skill
autorizada responde -- porque e isso que fecha o buraco do hifen: `calculator`
casa com "conta-me uma historia" e devolve `None`, ou seja, quem responde e o LLM,
e uma isencao baseada em "casou" nao distingue "respondeu" de "recusou e o LLM
apanhou".

`tests/test_guest_skills.py`, 12 testes; 11 falham contra o codigo antigo.

**Duas arestas que ficam abertas, por decisao do dono:**

1. `matching=None` recusa. Um resolver nao ligado e uma falha de wiring, e
   falha de wiring nao pode ser o que abre a porta.
2. **"quanto e 2+2" e recusado.** Casa com TRES skills: `calculator`, `cloogy` e
   `tuya` -- as duas ultimas porque ambas declaram o gatilho `"quanto"`, que e
   substring de "quanto e". A regra e fail-closed, portanto recusa. O owner tem de
   escolher: estreitar os gatilhos, decidir pela skill que responderia (exige
   executar antes de autorizar), ou allowlistar e aceitar o que vem com ela.
   Nenhuma e uma decisao de leitura de codigo.

## [HIGH] [DISCOVERED 2026-10-02] `POST /api/devices/<name>/control` nao tinha gate — e eu reportei duas

**Corrigido, e o primeiro relatório estava errado.** Escrevi que `/comando`,
`/device_action` e `/api/devices/<name>/control` estavam todas sem gate. Verificado
depois, contra a app isolada e sem credencial nenhuma:

```
/comando                  -> 401   gated
/device_action            -> 401   gated
/api/command              -> 401   gated
/api/devices/luz/control  -> 404   {"error":"Device not found"}   <- NAO gated
```

**Uma rota, não três.** `/device_action` está em `_TOKEN_PATHS` e foi sempre
gated. Reportei-o como não-gated porque li os decoradores (`@app.route`) em vez
de a gate — que é um `before_request` a comparar `request.path` com um conjunto.

Causa real de a uma ter escapado: **`_TOKEN_PATHS` é comparação exacta, e um
path com um segmento variável (`<name>`) não se escreve num conjunto exacto.**
As outras três são paths fixos; esta não é, e por isso nunca entrou.

O 404 não era "não existe": é o handler a correr. "Device not found" é a resposta
dele para um nome que não conhece — com um nome real no config, teria mudado o
dispositivo.

Corrido com um conjunto de prefixos (`_TOKEN_PREFIXES`) e um teste novo,
`tests/test_house_gate.py`, parametrizado por **toda** a rota que toca a casa:
acrescentar uma rota de acção nova sem a gate tem de partir um teste.

**A lição, que é do registo e não do código:** reportei um achado de segurança a
partir de leitura de markup. Bastava correr quatro pedidos sem credencial, como
`tests/test_house_gate.py` faz, para saber exactamente qual era. Escrevi no
docstring da review T058 que um teste de markup não distingue "presente" de
"funciona", e reportei um bug de *presença* de gate por leitura de markup.

Nota: com D4 do T068 decidido pelo dono — o convidado **pode** ler RAG e memórias
— esta gate passa a ser a única separação entre um convidado e a casa. Deixou de
ser uma melhoria e passou a ser pré-requisito.


Nota relacionada: `routes.py:344` documenta que com `PHANTASMA_COMMAND_TOKEN`
por definir o gate devolvia `True` — "EITHER credential is enough, and NEITHER
is refused". Se essa frase ainda for verdade, o problema e maior e o teste
primeiro tem de ser esse.

---

## 2026-10-03 — gemma3:4b substitui llama3.1:8b, nos dois hosts

**Motivo.** O `llama3.1:8b` não escreve português europeu. Medido em produção,
três tentativas, nenhuma resultado:

| experiência | resultado |
|---|---|
| pergunta nua, só a persona | "Sua obra é um reflexo..." |
| com a pesquisa em checo/castelhano | "Seus contos, como 'O Corvo'..." |
| com tabela de substituições explícita | "Sua obra", "Seus poemas" (pior) |

A tabela de substituições piorou. Não é bug de prompt: é o modelo.

**O bake-off.** Seis perguntas pelo prompt real de produção
(`scripts/eval_models.py`), com e sem o ruído da pesquisa:

| modelo | score (web) | falhou | pt-BR | scaffolding | palavras |
|---|---|---|---|---|---|
| `gemma3:4b` | **100.0** | 0/6 | 1 | 0 | 36.5 |
| `qwen2.5:7b` | 36.0 | 3/6 | 1 | 0 | 56.7 |
| `llama3.1:8b` | 0.0 | 2/6 | 5 | 1 | 53.0 |
| `aya-expanse:8b` | 0.0 | 4/6 | 6 | 4 | 111.0 |

`aya-expanse:8b` foi o pior e era a hipótese antes de medir — multilingue com forte
cobertura europeia, escolhido por convicção. Um 4B ganhar a um 8B também não era
esperado.

**Visão verificada antes de apagar o `llava:7b`:** vermelho → "Vermelho", verde →
"Verde.". Nos dois hosts. Um modelo de visão que não funciona deixa o assistente a
ouvir e cego, e a remoção é que causaria isso.

**Só `gemma3:4b` nos dois hosts**, por decisão do dono. Os restantes foram
removidos depois de o serviço estar em cima e o gate novo passar.

**Onde o nome do modelo vivia — e porque `deploy.sh` ganhou uma verificação:**

| local | papel |
|---|---|
| `.env` | `OLLAMA_MODEL_PRIMARY`, `_FALLBACK`, `OLLAMA_VISION_MODEL` |
| `config.py` `LLMConfig` | `model`, `model_fallback` |
| `config.py` `Config` | `ollama_vision_model` |
| `assistant.py` | fallbacks literais |

O `.env` ganha em runtime. Os outros existem para o caso de o `.env` faltar ou o
nome estar errado — e nesse caso **não há erro**: o Ollama responde 404 por
pedido, o serviço arranca, e o `/api/health` diz healthy porque pergunta se o
host está de pé e não se o modelo que nomeou existe. É a mesma classe do
`AUDIO_AUTO_DETECT` morto, pelo mesmo motivo: um valor de host num sítio que
falha em aberto. `deploy.sh` passou a ler `/api/tags` nos três papéis.

**O `--force-host` foi usado de propósito.** `config.py` deixou de estar
congelado nesta tarefa: os defaults passaram a `gemma3:4b` porque o dono decided
que é o modelo medido, e um default que aponta para um modelo apagado é o modo de
falha acima. Registado aqui porque o gate exige.

**Verificado depois da remoção:** visão nos dois hosts, os dois hosts a responder,
`dependency_check.py` verde, `/api/health` 200, e as cinco perguntas de conversa
sem português brasileiro, sem scaffolding e sem citação.

---

## 2026-10-04 — `/admin/config` passou a ser a fonte dos modelos

Pergunta do dono: *"tens a certeza que está a usar a config em admin/config?"*
Não. A página mostrava, para as três chaves de modelo:

```
OLLAMA_MODEL_PRIMARY    llama3.1:8b
OLLAMA_MODEL_FALLBACK   qwen3:8b
OLLAMA_VISION_MODEL     llava:7b
```

Os três tinham sido removidos dos hosts. O `gemma3:4b` é que respondia.

Três fontes, uma verdadeira: `.env` lida, `config.db` **não lida**,
`app_settings` **não lida**. E não por falta de ligação — por nunca ter havido
ligação. Não existe boot overlay: nenhuma escrita em `os.environ`, nenhum
`putenv`, nenhum import do `admin` no runtime. O comentário no `CONFIG_CONTROLS`
afirmava que o overlay honra a tabela "para os tokens secrets ficarem no `.env`".
Esse overlay nunca foi construído.

Consequência real: guardar um valor, ver "guardado", e nada acontecer. Um valor
errado no `.env` falha alto assim que o host não o serve; **um valor numa página
que não faz nada é indistinguível de sucesso.**

**Precedência agora igual à dos guests**, onde a página é autoritativa e o código
diz:

```
store  >  .env  >  dataclass
```

A store porque é a escolha mais recente e explícita. O `.env` porque é a verdade
do host e sobrevive à base perdida. O dataclass como último recurso, com
`source == "default"` para a página poder dizer que não há valor guardado — um
default que ganha em silêncio é um valor de host a falhar em aberto.

**A câmara lia `config.OLLAMA_VISION_MODEL`**, congelado no import.
`skills/skill_tapo.py` descreveria um visitante com o modelo apagado, e a câmara
é o caminho que um convidado nunca toca — só que nada mais o revelaria.

**Antes de gravar, verifica-se se o modelo existe no host.** Com a página
autoritativa, gravar um nome inexistente passaria a ser uma queda total em vez de
um no-op: a diferença entre uma gralha e uma indisponibilidade.

**Gravação dupla**, e a página diz qual das duas funcionou — a store vale já na
próxima mensagem, o `.env` é o que o host exige. Um `.env` que deixe de acompanhar
a página é como as duas se separam outra vez, e isso só o dono resolve.

**Verificado em produção:** tabela `config` mostra `gemma3:4b`, o valor efectivo
resolve para `gemma3:4b`, gravar pela página muda o valor resolvido
(`env` → `settings`), e repor repõe. `MainPID 736861 → 1411965`.

---

## 2026-10-04 — Persona e temperatura na página de configuração

Duas coisas do dono:

1. *"o admin/config agora não tem a edição de persona e devia ter"*
2. *"deve ter também a gestão da temperatura do modelo com um slider"*

**Correcção ao que eu disse antes, e é minha.** Afirmei duas vezes que não
existia boot overlay. Existe: `config.py:269`, `_overlay_owner_settings`, chamado
em `config.py:528`. A página **não** era um espelho morto para os modelos —
escrevia na store, o overlay aplicava-a ao `os.environ` antes de o `Config` ser
construído, e o `config.py` lia-a. A minha busca por `putenv` e `os.environ[...]`
não encontrou nada porque procurei o **consumidor** (`assistant.py`) e não o
**produtor** (`_overlay_owner_settings`).

O que era verdade e continua a ser: a `config.db` mostrava `llava:7b`, um modelo
removido; e o `assistant.py` lia `config.OLLAMA_MODEL_*`, o que exigia reinício
para uma mudança pela página.

**A persona nunca faltou.** Havia uma secção "Persona e reacções" no fim do
`CONFIG_TEMPLATE`, com `save_persona`, `reset_persona` e o aviso de personalizada.
Procurei primeiro no template de `/admin/persona`, o que fez "vive noutro sítio"
parecer resposta, e a segunda busca por `name="persona"` encontrou o `textarea` da
outra página antes do que eu queria. Acabei por **duplicar** a secção, e o teste
que escrevi afirmava um bug que não existia — corrigido, e o teste passa a fixar
o que é útil (que há um editor completo e que as duas rotas gravam no mesmo sítio).

**A secção ficou mais honesta:** diz que aplica-se à próxima mensagem sem
reiniciar, ao contrário do resto da página, que precisa de arranque. A voz estava
entre valores pendentes de reinício sem nenhum sinal de que era a excepção — e é
o ajuste que mais se muda.

**Temperatura: um slider, não dois.** O de conversa (0 a 1, passo 0.05, default
0.6 medido) é do dono. O dos factos aparece mas é `readonly` e o loop de gravação
salta-o. A 0.6 um 8B enfeita factos em vez de os relatar: perguntado o que é o
Capuchinho Verde, com "a pastelaria vegan" no contexto, respondeu durante
páginas sobre um módulo de segurança residencial. Deixá-lo num slider seria
reintroduzir isso sem querer.

Verificado em produção: slider `0.6 → 0.25` pela página, conversa a `0.25`, factos
a `0.15`, e repor volta a `0.6`. `MainPID 1411965 → 1822882`.

---

## 2026-10-04 — O que `/opt/phantasma/.env` tem de ter

A `.env` **não está no git**, por desenho: é onde vivem os valores de host
(tokens, IPs, caminhos, calibragens) e versioná-la seria versionar segredos.
Consequência prática: **um clone novo não sabe que `gemma3:4b` é o modelo**, e
quem montar a máquina nova tem de saber o que preencher. Não deve depender de
memória. Registado aqui.

67 variáveis na `.env` actual. Não são todas igualmente obrigatórias.

### Bloqueia o arranque

| chave | porque |
|---|---|
| `OLLAMA_MODEL_PRIMARY` | modelo do primário. O `deploy.sh` falha se não estiver instalado no host |
| `OLLAMA_MODEL_FALLBACK` | idem para o secundário |
| `OLLAMA_VISION_MODEL` | câmara e interpretação de imagens. A sua ausência **não se nota logo**: o assistente continua a ouvir e a responder |
| `OLLAMA_HOST_PRIMARY` | `http://10.0.0.128:11434` |
| `OLLAMA_HOST_FALLBACK` | `http://localhost:11434` |
| `MEMORY_DB_PATH`, `BRAIN_DB_PATH` | ambos apontam para `/opt/phantasma/data/brain.db` — é uma base só, apesar dos dois nomes |
| `WHISPER_MODEL` | `medium`. Modelo diferente = transcrição pior, sem aviso |
| `WAKEWORD_MODELS` | `.onnx` da palavra de activação. Falta = o assistente nunca acorda |
| `TTS_MODEL_PATH` | voz. Falta = não fala |
| `ALSA_DEVICE_IN`, `ALSA_DEVICE_OUT` | dispositivos de áudio |
| `OLLAMA_TIMEOUT` | `600`. É um orçamento de **leitura**, não de ligação |

### Sintonia local — há default no código

`ALSA_VOLUME_PERCENT`, `MIC_SAMPLERATE`, `VAD_AGGRESSIVENESS`,
`VAD_FRAME_DURATION_MS`, `WAKEWORD_CONFIDENCE`, `WAKEWORD_PERSISTENCE`,
`WAKEWORD_COOLDOWN_SECONDS`, `DEBUG_MODE`, `QUIET_START`, `QUIET_END`,
`TTS_GHOST_EFFECTS`, `AUDIO_FEEDBACK_ENABLED`, `USE_SOX_EFFECTS`.

### Tokens de terceiros — vazios significam "feature desligada"

`GEMINI_API_KEY`, `CLOOGY_USERNAME`, `SHELLY_GAS_URL`, `TUYA_DEVICES_JSON`,
`MIIO_DEVICES_JSON`, `DISCORD_BOT_TOKEN`, `PHANTASMA_COMMAND_TOKEN`.

### A armadilha que já mordeu

`AUDIO_AUTO_DETECT` está na `.env` **comentada**, e é uma armadilha viva:

    # AUDIO_AUTO_DETECT was dead: config.py reads AUDIO_DEVICE_AUTO_DETECT.
    # AUDIO_AUTO_DETECT=false

O nome está errado, a linha nunca fez nada, e editá-la não mudou o
comportamento — parece funcionar e não muda. `AUDIO_DEVICE_AUTO_DETECT` é a que
o código lê. A linha morta fica comentada **de propósito**, como aviso para quem
achar que o problema é o valor e não é o nome.

Antes de mudar qualquer definição de áudio, confirmar que o nome na `.env`
**coincide** com o nome em `config.py`. Uma divergência falha para o default e
parece que a definição não funciona.

### O que verifica esta lista

`scripts/deploy.sh` pergunta a `/api/tags` de cada host e falha se o modelo que
a `.env` pede não estiver instalado, nos três papéis (primário, secundário e
visão). É a diferença entre o `.env` estar errado e isso ser descoberto numa
conversa. O que **não** verifica é se os caminhos de áudio e aos `.onnx`
existem — continuam a ser responsabilidade de quem monta a máquina.
