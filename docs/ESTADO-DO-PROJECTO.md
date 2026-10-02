# Estado do projecto — 2026-10-02

Substituto do `aes-project-manager`, que não existe como skill. A função pedida
— reconciliar o estado do projecto com a realidade — é a do orquestrador `aes`
em modo project, e é isto que ele produz.

**Não há uma fonte única de verdade, e este documento diz qual é.** `aes/kanban.md`
diz que `current_ticket: T002`. O último ticket é o T061 e a última data é hoje.
O kanban tem 23 linhas `pending`; `aes/tickets/` tem 92 ficheiros.

## Qual é a fonte de verdade, para quê

| Pergunta | Fonte | Estado |
|---|---|---|
| O que está a correr? | `git log` em `testing` | fiável |
| O que está em produção? | `/opt/phantasma/` | ** diverge de `testing` |
| O que está feito? | `aes/tickets/` | fiável (92 ficheiros) |
| O que falta? | `docs/ROADMAP.md` | fiável, e mais recente que o kanban |
| Qual é o ticket actual? | `aes/kanban.md` | ** não fiável |

`main` e `testing` não têm ancestral comum (`CLAUDE.md`). A release é uma tag,
nunca um merge. `testing` está **21 commits à frente** de `origin/testing`? Não:
já foi feito push; está alinhado com `origin/testing` em `5052d8b`.

## Divergência dev ↔ produção, medida

| Ficheiro | Idêntico? |
|---|---|
| `src/api/admin.py` | **não** — `brain-topbar`: 7 ocorrências em dev, 0 em `/opt` |
| `src/api/memory_graph.py` | **não** |
| `src/brain/memory_graph.py` | **não** |
| `config.py`, `audio_utils.py`, `assistant.py` | sim (gate duro do `deploy.sh`) |

**Consequência que o dono sente:** a barra de topo, o portão do grafo e o
`#pt` **não estão em produção**. `scripts/deploy.sh` é o que os leva lá, e
`CLAUDE.md` é explícito: editar `src/` aqui não muda produção.

## Feito desde 2026-10-01

| Ticket | O quê | Onde está |
|---|---|---|
| T058 | `/admin/brain`: uma só linha de topo. Quatro barras empilhadas; a sleep bar estava debaixo de duas — 32 pixels visíveis de 117, e o número que ela trazia era clicado pelo link por cima. 25 testes Playwright, falsificados contra `HEAD` (23 de 25 falham). | `f4da0c4` |
| T059 | Peer review das skills de memória/sonho e da `brain.db`. 48 findings → 14 clusters. Um BLOCKER rebaixado por verificação. | `9a29ecd`, `30bd00b` |
| — | Auditoria de infra-estrutura AES: 1 das 6 skills não mede nada, 2 não correm. | `8921ba8` |
| T060 | Portão de promoteabilidade no grafo: fala e tags de idioma deixam de virar conceitos. | `641844d` |
| T061 | Revisão do T060. **BLOCKER encontrado e corrigido:** o portão estava no escritor errado. | `5052d8b` |

## Em aberto, por ordem de dano

Derivado de T059 e T061. A ordem é por **dano irreversível**, não por
gravidade no relatório.

| | | Onde |
|---|---|---|
| 1 | **Fuga de privacidade.** `tools.py:100` manda o texto das memórias para `pt.wikipedia.org` quando o SearXNG não responde, sem anunciar. A garantia central de `CLAUDE.md` é falsa. | T059/B1 |
| 2 | **Escritas sem auditoria.** As promoções do GMIF não passam por `graph_edit_audit`, e a docstring diz que passam. 6 promoções em produção, 0 no audit. | T059/B2 |
| 3 | **Recuperação morta.** 188 dos 190 nós e 1285 das 1286 arestas inalcançáveis. `/admin/brain` mostra os totais como se fossem contexto activo. | T059/B4 |
| 4 | **Afinidade sem clamp.** `apply_reward` soma sem limite; produção tem 61.0. `graph_edit` documenta e valida [-1, 1] no caminho manual. | T059/B5 |
| 5 | **Deadlock na fila.** `_place_unplaced_reactions` não é atómico; o segundo item trava-se em `database is locked`, para sempre. | T059/B7 |
| 6 | **O portão não regista o que rejeita.** Conceito bom rejeitado é indistinguível de um nunca extraído. | T061 |
| 7 | **`apply_reward` ressuscita o nó apagado** (`ON CONFLICT DO NOTHING` pela chave do tópico) — um 👍 sem texto traz `node:tag` de volta. | T061 |
| 8 | **Desempenho.** `_analyze_graph_gaps` é O(N²·E): 3,65 s a 190 nós, 65 s a 400, não acaba a 800. | T059/B8 |
| 9 | **Limpeza de dados.** 4 nós de fala, 5 pontas, `node:tag`, 15 marcadores órfãos, 3 memórias que mostravam `#pt`. Ticket à parte com `--dry-run`; **precisa do teu OK porque apaga-te coisas.** | T060 |

## Decisões que são tuas, não minhas

1. **`Ah, a chuva...`** — rejeitado por ter reticências, mas lê-se como conclusão
   plausível. Uma linha para reverter.
2. **O que conta como "fala"** — a regra actual só apanha reticências ou
   interjecção+vírgula. `"Não estou muito à vontade hoje."` passa. Declarado
   num teste chamado `test_the_known_gap_is_named_not_hidden`.
3. **O que conta como tag de idioma** — lista fechada. `portugues` passa.
4. **Se a limpeza de dados apaga** — e se o AC inclui
   `UPDATE topic_state SET current_key=NULL` (senão o `node:tag` volta).
5. **Se queres kill switch** no portão. Hoje reverter é editar código + deploy.
6. **Se `main` e `testing` se reconciliam** — divergem em 273 ficheiros e
   `git merge-base` é vazio. `CLAUDE.md` proíbe `--allow-unrelated-histories`
   sem veres o número de conflitos.

## Registo de validação

| | |
|---|---|
| `make check` | 1344 → **1401 passed**, 17 errors |
| os 17 errors | `test_hotword.py`, `melspectrogram.onnx` em falta no pacote instalado — pré-existentes, desde o T057 |
| lint | `ruff check` limpo |
| falsificação | T058: 23 de 25 testes falham contra `HEAD`. T060/T061: `materialize_memories` escreve 3 nós e 3 arestas contra o código anterior, `[]` agora |
| deploy | **não feito**. `/opt/phantasma` diverge de `testing` |