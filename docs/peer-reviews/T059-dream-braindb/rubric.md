# Rubrica pré-registada — T059

Peer review das *skills de memória/sonho* e da `brain.db`.

**Pré-registada antes de ler o código alvo.** Nenhuma linha de
`skills/skill_dream.py`, `skills/skill_memory.py`, `src/brain/*` ou do esquema
de `brain.db` foi lida antes de este ficheiro existir. O hash em
`rubric.sha256` é a prova.

## Âmbito

| Alvo | Ficheiros |
|------|-----------|
| Sonho | `skills/skill_dream.py` (1023 linhas) |
| Memória curta | `skills/skill_memory.py` (78 linhas) |
| Cérebro | `src/brain/*.py` — 8 módulos, 2805 linhas |
| Base de dados | esquema e uso de `brain.db` (`memories`, `memory_graph`, `flybrain_state`, `topic_state`, `graph_edit_audit`, `memories_purged`, `unplaced_reactions`) |

**Fora do âmbito:** conteúdo das memórias de produção (é a vida do dono — a
revisão é sobre esquema, consultas e escrita, não sobre o texto guardado).
`src/api/admin.py`, `src/api/routes.py` e o ciclo de deploy.

## Dimensões

Cada *finding* tem de citar exactamente uma destas. Uma citação que não
exista aqui é rejeitada.

### R1 — Honestidade da comportamento
O código faz o que a sua docstring, nome ou mensagem de UI diz? Uma
afirmação que é falsa quando lida ao lado do código é BLOCKER.

### R2 — Integridade dos dados
`brain.db` é o activo que o projecto não pode perder (é o `_apply_research_to_graph`
a promoting arestas, e o `memories_purged` a registar o que se apagou).
Transacções, chaves estrangeiras, migrações, `PRAGMA`s, e o que acontece a meio
de uma escrita falhada.

### R3 — Falsabilidade dos testes
Um teste que passaria se o comportamento se invertesse. `"x" in body`,
`assertTrue(result)` sem valor, mocks que replicam a implementação.
*"Se eu inverter a cadeia de producao, o que fica vermelho?"*

### R4 — Offline-first
Nenhuma chamada de rede obrigatória no caminho de sono/memória. O projecto
declara isto como não-negociável; SearXNG é opcional e auto-hospedado.

### R5 — Segredos e configuração de host
Credenciais em código ou na base de dados; valores de host em dataclass em vez
de `.env`. O `CLAUDE.md` é explícito sobre isto.

### R6 — A documentação diz a verdade
`docs/`, `README.md`, `tests/schema.sql` e os docstrings face ao código.
Um espelho de esquema que não espelha é R6, não R1.

### R7 — Concorrência e falha
Threads, timeouts, escrita parcial, reentrância. "O que acontece se a segunda
conexão abre enquanto a primeira escreve?"

### R8 — Simplicidade e dívida
Abstração especulativa, código morto, camadas que não compram nada.
*Obrigação de dívida:* cada `TODO`, `FIXME`, `# HACK`, `except: pass`.

## Formato obrigatório

```yaml
type: BLOCKER | MAJOR | MINOR
title: <uma linha>
evidence: <caminho:linha, ou saída de comando que prova>
closure-condition: <o que, mecanicamente, fecha isto>
rubric-criterion: <R1..R8>
```

Rejeitados: "deveria melhorar", "considere refatorizar X", "a qualidade do
código é boa". Um finding sem `closure-condition` verificável é rejeitado.

## Modo multi-perspectiva

Não há revisores independentes de outra família de modelo disponível, por isso
a skill obriga ao modo multi-perspectiva: quatro personas (**Cínico,
Purista, Pragmático, Utilizador**) a produzir findings em subagentes frescos,
sem partilhar findings nem a avaliação do autor, e um moderador que preserve
divergências em vez de as fundir.

**Divergência preservada, não resolvida:** quando duas personas
discordam, o finding mantém as duas leituras. Um moderador que escolhe um lado
está a fazer o trabalho do autor.