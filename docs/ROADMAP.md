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
