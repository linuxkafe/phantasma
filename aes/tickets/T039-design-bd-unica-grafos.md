# T039 — Design: Base de dados única com grafos (memória + FlyBrain + claims)

**Estado**: aberto (design-only, sem código)

## Contexto / problema

Hoje os dados estão fragmentados em múltiplos stores com esquemas e locais diferentes:

- `data/memory.db` — memória de conversas (SQLite RAG, skills)
- `data/flybrain.db` — neuromodulação + persistência de anéis (SQLite, T021)
- `aes/kanban.md`, `aes/tickets/*.md` — conhecimento project-strutura (markdown, claims)
- (T037/T038) áudio/models em bind mounts separados

Isto cria inconsistências: afinidade/feedback vive em `flybrain.db`, factos em `memory.db`, e
"quem o utilizador é / o que aprendeu" não tem um grafo consultável integrado.

## Objectivo (design)

Propõe-se desenhar **uma única BD para memória factual + neuromodulação + claims**, com grafos
pesquisáveis, decidindo entre opções (SQLite com extensão grafo; base de grafos dedicada;
SQLite + tabelas edges ad-hoc).

## Fora de âmbito (nesta fase, design)

- Sem alteração de código (não passou à build — carência de ACs + prioridade)
- Não altera `config.py`, `assistant.py`, `docker-compose.yml` (ficheiros críticos) sem
  aprovação humana explícita

## Perguntas a responder no design

1. Grafo: node types (`user`,`fact`,`claim`,`topic`,`feedback_event`), edge types (`asserts`,
   `rewards`,`relates_to`), schema de persistência
2. Migração: como unificar `memory.db` + `flybrain.db` sem perder afinidade/estado aprendido
3. Consulta: "o que o utilizador sabe/quer?" e "como recompensar" num mesmo grafo
4. Localização: compatível com `PHANTASMA_DATA_DIR` (T041)
5. Backup/rollback e verificação `make test` + `make lint`

## Critérios de aceitação (a definir na plan)

- [ ] Grafo único pesquisável com 3+ relações entre factos/feedback
- [ ] `flybrain.db` migrado sem perda de afinidade (teste de golden state)
- [ ] `memory.db` RAG continua a funcionar
- [ ] `make test` (150) + `make lint` verdes