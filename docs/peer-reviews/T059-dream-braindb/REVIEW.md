# T059 — Peer review: skills de memória/sonho e `brain.db`

**Rubrica:** [`rubric.md`](rubric.md) · sha256 `916831f02d706a8d497f5bc0591c5b75882abf1acd54d76e2ab273dec90ea4ff`
**Modo:** multi-perspectiva (obrigatório — não há revisores de outra família de modelo disponível)
**Personas:** Cínico · Purista · Pragmático · Utilizador, em subagentes frescos, sem partilhar findings
**48 findings brutos → 14 clusters**

> **Nota de infra-estrutura:** a skill `aes-peer-review` não traz
> `docs/PEER_REVIEW.md`, `templates/review-rubric.md` nem
> `docs/review/MULTI_PERSPECTIVE_REVIEW.md` — a pasta tem só o `SKILL.md`. E
> o gate como escrito pede o hash em `aes/peer-reviews/<id>/`, que é
> impossível: `.gitignore:11` tem `/aes/` e `git ls-files aes/` devolve 0. A
> rubrica foi por isso pré-registada em `docs/`, que é rastreada, **antes** de
> uma linha de código alvo ser lida.

---

## Como se lê isto

48 findings, quase todos com evidência verificada em `/opt/phantasma/data/brain.db`
(só-leitura) ou por reprodução. Clustering por causa, não por palavras.

**Divergência de severidade preservada, não resolvida.** Onde duas personas
discordam, ambas as leituras ficam. Um moderador que escolhe um lado está a
fazer o trabalho do autor.

---

## BLOCKER

### B1 — O sonho manda conteúdo das memórias do dono para a `pt.wikipedia.org`
**Personas:** Utilizador (BLOCKER, R4) · Cínico (MAJOR, R4) · **divergência de severidade**

`tools.py:100-101` — quando o SearXNG não devolve resultados:
```python
results = _searxng(prompt, max_results, client)
if not results:
    results = _wikipedia(prompt, max_results, client)   # pt.wikipedia.org
```
`skill_dream.py:427-431` monta a query a partir de `row[0]` — o texto de uma
memória real. `reconcile.py:178` manda rótulos de nós.
`CLAUDE.md` e `README.md:3` prometem *"without third-party cloud dependencies
(except optional web search via self-hosted SearXNG)"*. A dependência existe,
é implícita, e o fallback **não se anuncia em lado nenhum** — o `print` vai
para o stdout do serviço.

**Verificado por mim:** `tools.py:100` contém exactamente esta sequência.

**closure-condition:** `_wikipedia` deixa de existir, ou passa a ser chamado
só com query derivada de tópicos genéricos e nunca de `memories.text`/rótulos;
e o ramo escreve uma linha em `graph_edit_audit` (`op='leak.blocked'`, host no
`after_json`) em vez de só imprimir. Fecha quando um teste com o SearXNG em
falha e `pt.wikipedia.org` bloqueado afirma que `_research_gap` devolve `None`.

---

### B2 — As promoções do GMIF escrevem no grafo sem auditoria, e a docstring afirma o contrário
**Personas:** Utilizador (BLOCKER, R1) · Cínico (BLOCKER, R6) · **divergência de critério: R1 vs R6**

`skill_dream.py:620-621` — *"Every write also lands in `graph_edit_audit`
through `graph_edit.py`, so it is reversible."*
`skill_dream.py:641-684` abre `sqlite3.connect(config.DB_PATH)` e faz
`UPDATE`/`INSERT` directo. `grep -n graph_edit skills/skill_dream.py` devolve
**apenas as duas linhas do docstring**: o módulo nunca é importado.

Produção: `gmif_classified_by` → `dream_gmif_research`: 6, `materialize_memories`: 1278.
`graph_edit_audit` tem **zero** linhas para essas 6 promoções. São
irreversíveis e não são atribuíveis a ninguém.

**closure-condition:** `_apply_research_to_graph` escreve através de
`graph_edit.update_edge`/`create_edge`, ou chama `_audit(conn, 'dream_gmif_research', ...)`
na mesma transacção. Fecha quando um teste falha se, depois de
`_apply_research_to_graph` devolver `True`, `select count(*) from graph_edit_audit
where actor='dream_gmif_research'` for 0.

---

### B3 — `/api/graph/flybrain` serve `{"_raw": <binário>}` ao explorador 3D
**Personas:** Purista (BLOCKER, R1) · Pragmático (MAJOR, R1) · Cínico (MAJOR, R1) · **convergente**

`persistence.py:105` grava `msgpack.packb`. `graph_edit.py:682-706` só tenta
`zlib.decompress` e depois `json.loads`; em falha devolve `{"_raw": str(raw)[:4000]}`.
O docstring promete *"The FlyBrain ring state, decoded"*.
`routes.py:926` devolve isso em `/api/graph/flybrain`.

**Verificado por três personas em produção:** primeiros bytes
`86 ae 73 63 68 65 6d 61` (msgpack `fixmap`), não `78` (zlib) nem `{` (JSON).
Resultado: chaves `['_raw']`.

**closure-condition:** `flybrain_state` usa `msgpack.unpackb` importado de
`src.brain.persistence` (não reimplementado). Fecha quando um teste grava via
`FlyBrainStore.save()` e afirma `state['steps'] == N` sem a chave `_raw`.

---

### B4 — A recuperação do grafo está morta: 188 dos 190 nós e 1285 das 1286 arestas são inalcançáveis
**Personas:** Cínico (BLOCKER, R1) · Utilizador (MAJOR, R1) · Purista (BLOCKER, R1, sobre a expansão) · **divergência de severidade**

Dois defeitos que se somam, ambos de R1:

**(a) O limiar não deixa passar nada.** `memory_graph.py:618`
`relevant = [it for it in result if affinity >= MIN_AFFINITY]` com
`MIN_AFFINITY = 0.05`. Nenhum escritor produz afinidade > 0: `materialize_memories`
(`:216-260`) e `upsert_node`/`upsert_edge` (`:297-344`) omitem a coluna →
`DEFAULT 0.0`. Só `apply_reward` a sobe. Produção: **2 nós** de 190 acima do
limiar; **1 aresta** de 1286.

**(b) A expansão por arestas compara o campo errado.** `memory_graph.py:556-559`
afirma *"An edge stores node_keys in source/target, never labels"* — e é essa
afirmação que justifica as 37 linhas de `key_by_label`/`label_by_key` (`:561-597`).
Contra a produção: `where node_type='edge' and source not like 'node:%'` →
**1286**. `source like 'node:%'` → **0**. `upsert_edge` (`:327-341`) grava os
labels crus.

`assistant.py:950-953` chama `graph_context_text()` em **cada mensagem**, e o
docstring do módulo (`:9-11`) chama-lhe de "material bridge". Devolve string
vazia em quase todos os turnos, enquanto `/admin/brain` mostra os totais como se
fossem contexto activo.

**closure-condition:** (a) `materialize_memories` escreve afinidade derivável de
`gmif_validation_confidence`, **ou** `retrieve_neighborhood` filtra por peso;
(b) a comparação passa a `LOWER(source) = LOWER(?)` sobre labels. Fecha quando
um teste com `materialize_memories()` + `upsert_edge()` (sem afinidade manual)
faz `graph_context_text("...")` devolver uma linha — hoje devolve `""`.

---

### B5 — `apply_reward` soma sem clamp; a afinidade em produção é 61.0 e `graph_edit` documenta e valida [-1, 1]
**Personas:** Purista (BLOCKER, R2) · Cínico (MAJOR, R2) · **divergência de severidade**

`graph_edit.py:30-33` — *"Weight and affinity are bounded to [-1, 1] … the range
is enforced here rather than trusted from the client"*, e `_clamp` (`graph_edit.py:92`)
é aplicado em `update_edge`/`update_node`/`create_edge`.
`memory_graph.py:373-380` — `SET affinity = affinity + ?`, sem clamp.

Produção: `min/max affinity = (0.0, 61.0)`. O clamp do editor é decorativo
para afinidade, e `retrieve_neighborhood` faz `sort(key=affinity, reverse=True)`
— pelo que `'node:capitalismo tardio'` com 61.0 ganha qualquer lista.
Agravante (Cínico): `_place_unplaced_reactions` soma **duas vezes** —
`upsert_node(abs(reward)*0.5)` em `skill_dream.py:816` seguido de
`apply_reward(reward)` em `:827` → 1.5 num único 👍, reproduzido.

**closure-condition:** clamp em `apply_reward` reutilizando `graph_edit._clamp`
(não uma cópia), mais aviso/migração das linhas já fora de rango. Fecha quando
200 reacts de `+1.0` deixam `affinity <= 1.0`.

---

### B6 — `index_memory` transforma o cabeçalho `graph TD;` numa aresta, e o tópico do cérebro é o literal "Tag"
**Personas:** Cínico só (BLOCKER, R1) — **verificado por mim**

`_EDGE_RE` (`memory_graph.py:463-468`) não ancora no início de uma linha, por
isso `graph TD;\nBimby-->Bimba` produz `('graph TD', '', ';\nBimby-->Bimba\nbimba')`.
`skill_memory.py:43` pede exactamente essa forma ao LLM.

Produção: `topic_state = (1, 'node:tag', '2026-09-30T00:14:46')` — o primeiro
tag gravado como tópico **ambiente** (`memory_graph.py:507`), que
`reactions.record` e `apply_reward` creditam. E existe a linha
`'edge:;\nbimby-->bimba\nbimba|lola'` em `memory_graph`.

**Eu verifiquei ambas** (`topic_state` e a chave malformada), em `/opt/phantasma/data/brain.db`.

**closure-condition:** `_EDGE_RE` ancora em `^` com `MULTILINE` e consome
`graph\s+\w+\s*;`; `index_memory` recusa endpoints que não casem com um
`node:<key>` existente. Fecha quando um teste com `'mermaid': 'graph TD;\nA-->B'`
afirma que nenhum `node_key` contém `\n` nem `;` e que `get_current_topic()`
devolve `node:a`.

---

### B7 — `_place_unplaced_reactions` não é atómico e trava a fila em "database is locked"
**Personas:** Pragmático (BLOCKER, R7) · Cínico (MAJOR, R7) · **divergência de severidade**

`skill_dream.py:816` (`upsert_node`) e `:827` (`apply_reward`) abrem **as suas
próprias ligações** via `memory_graph._connect()` e fazem `commit`, enquanto
`unplaced.mark()` escreve na `conn` do ciclo e o único `conn.commit()` está em
`:835`, **depois** do loop. `journal_mode` em produção é `delete`.

Primitiva reproduzida pelo Pragmático: conexão A com `INSERT` por fazer +
conexão B a escrever → `OperationalError: database is locked` ao fim de 5.00 s.
O `except Exception` por linha (`:830-834`) deixa a linha `PENDING`, e
`unplaced.pending` é `ORDER BY id LIMIT 3` — as linhas 2 e 3 bloqueiam todas as
 reacções seguintes indefinidamente. Latente: hoje há 1 linha pendente.

**closure-condition:** as três escritas numa só transacção (passar a ligação a
`upsert_node`/`apply_reward`), ou `WHERE outcome IS NULL` no `mark` com
verificação de `rowcount`. Fecha quando um teste enfileira 3 linhas todas com
nós novos e afirma `0` linhas com `outcome IS NULL`.

---

### B8 — `_analyze_graph_gaps` é O(N²·E) em Python: 3,65 s a 190 nós, 65 s a 400, e não acaba a 800
**Personas:** Pragmático só (BLOCKER, R7) — **medido, com extrapolação**

`skill_dream.py:503-512` — loop duplo sobre `labels` com
`any(... for e in edges)` (varredura linear das 1286 arestas) **dentro** do
loop interno.

| nós | arestas | medido |
|---|---|---|
| 190 (produção) | 1 286 | **3,65 s** |
| 200 | 5 000 | 15,63 s |
| 400 | 5 000 | **65,56 s** |
| 800 | — | não terminou em 100 s |

Ajuste N²·E confirmado (previsto 15,8×, medido 18×). Bónus: o `seen` em
`:502-508` é código morto — cada par `(i,j)` é enumerado uma vez só, e 17 955
tuplas são construídas por ciclo sem nunca lidas.

**closure-condition:** construir um `set` de pares undirected `(source,target)`
antes do loop e testar pertença em O(1); apagar o `seen`. Fecha quando um
benchmark com 400 nós afirma `< 2 s` (hoje 65,56 s).

---

## MAJOR

### M1 — `tests/schema.sql` está 4 tabelas atrás da produção, em ambos os ficheiros
**Personas:** as 4 (Purista/Pragmatic MAJOR, Cínico/Utilizador MINOR) · **divergência de severidade, convergência total**

`tests/schema.sql:1-4` — *"Mirrored from the production databases on 2026-09-27.
Regenerate from the real sqlite_master when a table drifts; do not hand-edit."*

Falta em `brain.db`: `app_settings`, `graph_edit_audit`, `memories_purged`,
`unplaced_reactions`. Falta em `config.db`: `auth_codes`, `auth_tokens`,
`trusted_devices`, e a coluna `users.discord_id`.

Isto não é cosmético: as três tabelas em falta são **a via de recuperação**
(`memories_purged`, 51 linhas vivas), **a trilha de auditoria**
(`graph_edit_audit`, 48 linhas) e **a fila de reacções**
(`unplaced_reactions`). Pior: 20+ ficheiros de teste escrevem o seu próprio
`CREATE TABLE` (`test_memory_graph.py:198,207,218`; `test_dream_graph.py:199` +7;
`test_discord_identity.py:56`; `helpers_ui_auth.py:66,122`), e
`test_graph_schema.py:39` admite *"hand-written CREATE TABLE here drifted from
the reader"*. O espelho é decorativo para o código do grafo, e
`test_memory_graph.py:218` cria `flybrain_state(... data TEXT ...)` contra o
`data BLOB NOT NULL` real.

**closure-condition:** `tests/schema.sql` gerado de `sqlite_master` real, e um
teste que compare o conjunto de `CREATE TABLE`/colunas do fixture com o de
produção e falhe com a lista do que falta. Os `CREATE TABLE` inline saem.

---

### M2 — Três esquemas incompatíveis de `node_key` de aresta na mesma coluna UNIQUE
**Personas:** Cínico · Purista · Utilizador (os três, MAJOR, R2) · **convergente**

| escrita | chave | Who |
|---|---|---|
| `concept_edge_key` `memory_graph.py:140` | `edge:{a}\|{b}`, minúsculas, **ordenado** | materialiser |
| `upsert_edge` `memory_graph.py:327` | `edge:{src}\|{tgt}`, **não ordenado** | Upserts |
| `create_edge` `graph_edit.py:234` | `edge:{source} -> {target}`, como escrito | edição manual |

`skill_dream.py:654` promove só por `concept_edge_key(...)`, logo nunca encontra
as linhas dos outros dois esquemas. Reproduzido: `upsert_edge('Bovinos','Leite')`
seguido de `upsert_edge('Leite','Bovinos')` deixa **duas** linhas — exactamente
o bug que o docstring de `concept_edge_key` diz ter corrigido em produção.
O mesmo par via `create_edge` dá **três**.

E `resolve_dangling` (`graph_edit.py:607,657`) reescreve `source`/`target` mas
**nunca `node_key`** (Purista), deixando chaves não-canónicas que a promoção
não consegue corrigir.

**closure-condition:** um único construtor `edge_key(a, b)` usado pelos três
escritores, e `resolve_dangling` a reescrever `node_key` na mesma transacção.
Fecha quando
`group by lower(min(source,target)), lower(max(source,target)) having count(*)>1`
devolve **zero** linhas.

---

### M3 — `_dedupe_memories` engole toda a excepção e o `/admin/brain/sleep/status` reporta `ok: True`
**Personas:** Cínico · Purista (ambos MAJOR, R1) · **convergente**

`skill_dream.py:369-370` — `except Exception as e: print(f"❌ Erro Deduplicação: {e}")`.
`admin.py:3338` — `_step()` só marca `ok: False` se a função **levantar**.

Reproduzido com a tabela `memories` removida:
```
❌ Erro Deduplicação: no such table: memories
→ o estado que o admin publicaria: {'dedupe_memories': {'ok': True}}
```

Isto é a **mesma classe** do defeito que o T057 registou ("apagava 20 linhas sem
gravar nada e dizia que tinha sucesso"). `_consolidate_memories` foi reescrita
para levantar (`skill_dream.py:281-287`); `_dedupe_memories`, `_reduce_to_concepts`
(`:709-711`) e `_perform_web_dream` (`:441`) mantêm o padrão.

**closure-condition:** `_dedupe_memories` levanta (ou devolve `error`) em vez
de imprimir. Fecha quando um teste apaga a tabela `memories` e afirma
`steps.dedupe_memories.ok is False` no estado do ciclo real.

---

### M4 — "Desliga o sonho" não desliga o botão que apaga memórias
**Personas:** Utilizador só (BLOCKER, R1) — **promovido a MAJOR por mim**, e o motivo está abaixo

`skill_dream.py:1006-1008` responde *"Os sonhos foram suprimidos"* e só muda
`globals()['DREAM_ENABLED']`. `admin.py:3402-3428` só condiciona
`reduce_to_concepts` a `GMIF_DREAM_ENABLED` — `_dedupe_memories`,
`_consolidate_memories` (que faz `DELETE FROM memories`, `skill_dream.py:305`) e
`_research_half` correm **incondicionalmente**. A flag é um global em memória,
perdida ao reiniciar, e `/tmp/no_dream` é apagado a cada reboot. Não há sítio
nenhum na UI que a mostre.

**Por que baixei de BLOCKER:** a consequência não é um evento catastrophico
único — `DELETE FROM memories` tem `_archive_purged_memories` atrás
(`:222-229`, 51 linhas em produção), e a resposta ao dono é queixa, não perda.
Continua a ser uma mentira numa resposta directa a uma acção do dono.
Divergência preservada.

**closure-condition:** `brain_sleep` devolve 409 com
`{"ok":false,"error":"dream disabled"}` quando `DREAM_ENABLED` é False, e
`_run_sleep_cycle` salta todas as fases nesse caso; o valor passa a ser lido e
escrito em `app_settings` e renderizado em `/admin/brain`.

---

### M5 — `FlyBrainStore.load()` engole todo o erro: um estado corrompido arranca um cérebro novo em silêncio
**Personas:** Pragmático só (MAJOR, R2)

`persistence.py:169-172` — `except Exception: logger.error(...); return None`.
`fly_brain.py:228-230` — `if loaded: self.restore(loaded)`: um no-op silencioso.
Todos os `wkc`, `valence`, `affinity`, actividade do anel e `steps` aprendidos
são descartados e o assistente responde como se nunca tivesse treinado. A guarda
de versão (`:160-163`) também não bloqueia: regista o mismatch e desserializa na
mesma. Simétrico: `save()` engole falhas e `FlyBrain.persist()` devolve `True`
incondicionalmente, com um docstring que promete *"True if a write happened"*.

**closure-condition:** `load()` distingue "sem linha" (None) de "linha ilegível"
(levanta, ou devolve um sentinel que o chamador tem de reconhecer
explicitamente). Fecha quando uma linha cujo blob não é msgpack produz erro
explícito, não um cérebro novo.

---

### M6 — `neuro_to_ollama` é uma ponte morta, e `FORBIDDEN` declara uma supressão que não existe
**Personas:** Pragmático · Purista (MAJOR, R8) · Cínico (MINOR, R1) · **divergência de critério: R8 vs R1**

`grep -rn 'ollama_params\|build_request' --include=*.py` fora do próprio módulo e
dos testes → **zero**. Só `src/brain/__init__.11-14` re-exporta e os 15 testes
passam. `assistant.py:935` paga `ring.step()` (n_solve=20 em 64×64) **por turno**
para um estado que não muda prompt, temperatura nem `num_predict`.

E `neuro_to_ollama.py:29-31` comenta *"Forbidden topics (always suppressed
regardless of temperature)"* sobre `FORBIDDEN = {"instruções para fabricar explosivos"}`
que **não é lido em lado nenhum**. Um comentário que afirma uma garantia de
segurança sem implementação é pior que a constante morta: o próximo leitor
assume que a filtragem existe.

**closure-condition:** ou `build_request` é ligado ao pedido de Ollama do
assistente, com teste de integração que afirme a temperatura alterada; ou o
módulo é removido. `FORBIDDEN` passa a existir ou desaparece.

---

### M7 — O ciclo de sono não tem trinco, e o segundo toque apaga o relatório do primeiro
**Personas:** Pragmático · Utilizador (ambos MAJOR, R7) · **convergente**

`grep -n Lock skills/skill_dream.py` → só um comentário. Três entradas
independentes: `_daemon_loop` numa thread (`:978`), `handle()` noutra (`:1002,1022`),
`/admin/brain/sleep` noutra (`admin.py:3441`). Dois `_consolidate_memories`
concorrentes leem as mesmas `LIMIT 20` linhas (`:262`) e ambos inserem um
resumo. `_LAST_SLEEP_CYCLE` é um dict único, sobreposto em `admin.py:3443` — o
relatório do primeiro ciclo, que é o artefacto pelo qual o endpoint existe
(`:3457-3466`), fica inalcançável.

**closure-condition:** `threading.Lock` de módulo adquirido por
`perform_dreaming` e por `_run_sleep_cycle`; `/admin/brain/sleep` devolve 409
com o estado corrente quando já está a correr; `_LAST_SLEEP_CYCLE` guarda uma
lista por id de ciclo. Fecha quando dois `perform_dreaming()` concorrenciais com
`_consolidate_memories` instrumentado mostram `calls.count == 1`.

---

### M8 — `reconcile_refs` re-litiga as mesmas 30 referências todas as noites, sem tecto e sem memória
**Personas:** Pragmático · Utilizador (ambos MAJOR, R7) · **convergente**

`reconcile.py:296` — `for item in pending:` sem `[:N]`. `MAX_RESEARCH_PER_CYCLE = 3`
(`skill_dream.py:40`) só governa o **outro** caminho. Produção tem 30 pendentes
(`find_dangling` → 30, 0,026 s). Cada uma custa 1 query-gen + 1 SearXNG + 1
juízo: até 2×90 s ≈ **90 min por ciclo**. O ramo `ambiguous` (`:323-327`) não
escreve nada e nenhuma coluna regista que já foi julgado.

Contraste com `unplaced.mark` (`skill_dream.py:776-778`), que existe
precisamente para *"a queue whose rejected rows stay pending forever would
re-ask the same question every night"*.

**closure-condition:** `reconcile_refs` aceita `max_refs` (≤3) e persiste os
veredictos ambíguos. Fecha quando dois `reconcile_refs` seguidos não geram nova
pesquisa para o mesmo `edge_key`/`side`.

---

### M9 — `data_utils._distil_for_memory` cria um cliente Ollama **sem timeout**, no caminho de escrita do sonho
**Personas:** Purista só (MAJOR, R7)

`data_utils.py:62` — `client = ollama.Client(host=cfg.llm.host)`, sem
`timeout=`. Em `venv/.../ollama/_client.py:86,94`, `timeout: Any = None`.
`save_to_rag` chama-o sempre que `len(text) > 400` (`data_utils.py:55,100`), e
`save_to_rag` está em todas as escritas do sonho (`:325, :412, :439, :751`).

`skill_dream.py:65-76` documenta precisamente a disciplina que esta chamada
contorna: *"A per-call timeout, not the global `OLLAMA_TIMEOUT` … At the global
timeout one step can hold the cycle for ten minutes."*

**closure-condition:** `_distil_for_memory` passa `timeout=` a partir do mesmo
valor que `_safe_ollama_chat` usa. Fecha quando um teste que faz o cliente
pendurar afirma que o passo termina em < `2 × DREAM_OLLAMA_TIMEOUT`.

---

### M10 — O classificador GMIF é inerte nos dois sentidos
**Personas:** Pragmático (MAJOR, R1) · Utilizador (BLOCKER, R1) · **divergência de severidade**

`gmif_classifier.py:270` — `classify_all_edges` selecciona só
`WHERE gmif_level IS NULL`, mas todos os escritores carimbam um nível
(`memory_graph.py:252` escreve `'M1'`). Produção: `gmif_classified_by =
'gmif_classifier_v1'` aparece **1 vez** em 1286, e
`gmif_source_chunks LIKE '%edge_label_pattern%'` = **0**. A tabela
`EDGE_PATTERNS` de 90 linhas nunca disparou.

No sentido inverso, `classify_node` (`:203-209`) devolve `CONCEPT`,
`confidence=0.80`, `basis: default` sem examinar o texto — produção:
`node_gmif_confidence` → `[(0.8, 190)]`, todos. 188/190 com `basis: default`.
`missing_requirements` mede 0 permanentemente, e uma das quatro entradas de
`GMIF_DESIRED_LEVEL` (`:46-51`) é inalcançável — enquanto os testes
(`test_dream_graph.py:186-191`) continuam a afirmar que o mapeamento existe.

**closure-condition:** `classify_all_edges` passa a seleccionar
`WHERE gmif_classified_by != 'gmif_classifier_v1'`, e `classify_node` deixa
`node_gmif_type` NULL quando nenhuma regra casa. Fecha quando
`gaps['missing_requirements']` é não-vazio numa fixture cujos nós não casam com
os quatro padrões.

---

### M11 — `save_to_rag` grava no RAG uma síntese que a porta de evidência recusou
**Personas:** Pragmático só (MAJOR, R2) — **reproduzido**

`skill_dream.py:751` corre `save_to_rag(...)` **fora** do
`if _apply_research_to_graph(...)` de `:749`. Com `evidencia: []` a porta
imprime *"⚠️ [Dream-GMIF] Recusado: síntese sem evidência."* e devolve `False`,
mas `memories` vai de 0 para 1 linhas.

`save_to_rag` escreve a tabela `memories` (`data_utils.py:106-108`), que
`retrieve_from_rag` serve ao LLM na conversa normal e que
`_consolidate_memories` funde **como se fosse memória do dono** (`:262`).

É a porta que o T057 já-opentava — *"o portão de evidência aceitou-os"* — a ser
contornada pelo caminho de escrita em vez do de leitura.

**closure-condition:** mover o `save_to_rag` para dentro do `if`. Fecha quando
um teste afirma que uma síntese recusada deixa `SELECT COUNT(*) FROM memories`
inalterado.

---

### M12 — O botão de apagar nó do `/admin` apaga por `id` sem guarda referencial, com o mesmo `op` da via que a tem
**Personas:** Cínico só (MAJOR, R2)

`graph_edit.py:440-450` — `delete_node` recusa se alguma aresta ainda aponta
para o nó e grava `op='node.delete'`, `target=<node_key>`.
`admin.py:3018-3033` — `op == "delete_node"` faz
`DELETE FROM memory_graph WHERE id = ?` **sem verificação**, e grava
`op='node.delete'`, `target=f"node:{nid}"`.

Produção: 8 linhas `node.delete` em `graph_edit_audit`, com `target` em dois
formatos (`node:sinto-me-como-se-...` vs `node:75`, `node:118`, …) —
**indistinguíveis no audit**. E `reconcile.find_dangling(prod)` devolve 30 refs
penduradas, que é o que a reconciliação da noite tenta resolver em vez de apagar.

**closure-condition:** `admin._apply_knowledge_edit('delete_node')` delega em
`graph_edit.delete_node(db, node_key, actor=...)` e deixa de aceitar `id` cru;
`op` distingue os dois caminhos enquanto o antigo existir. Fecha quando um POST
sobre um nó com aresta devolve o erro de referência pendurada e a linha fica.

---

## MINOR

| # | Finding | Personas | Critério |
|---|---------|----------|----------|
| m1 | `_load_flybrain` pede `total_rewards`, `total_punishments`, `avg_reward`, `orientation_deg` de topo — nenhum existe no estado que o serializador emite. Produção emite `['mushroom_body','neuromodulatory_pool','ring','saved_at','schema_version','steps']`; `orientation_deg` vive dentro de `ring`. O painel mostra sempre só `steps`. Sem teste nenhum. | Purista | R1 |
| m2 | `reactions.record()` nunca chama `persist()`, ao contrário do docstring de `fly_brain.py:260-268` que diz que *"each one is a deliberate correction … must call this instead"*. `maybe_auto_save` faz batching 1-em-10 — a reacção 👎 é o único feedback que se perde num restart. | Pragmático | R1 |
| m3 | `"Aprendi N coisas novas"` conta `len(data['noticias'])`, o que o LLM afirmou, não o que foi gravado — mesmo quando `_as_memory` devolve `None` para todos os itens. E `save_to_rag` tem `except Exception: print(...)` e devolve `None`. Mesma classe do `success=True` de um stub vazio. | Utilizador | R1 |
| m4 | `_analyze_graph_gaps` é O(N²·E) (BLOCKER B8) e com SearXNG em baixo `_optimize_graph` faz **6** `_research_gap` contra `MAX_RESEARCH_PER_CYCLE = 3`, porque `researched` só incrementa no sucesso e o `break` nunca dispara. 6,2 min de Ollama por noite por 0 escritas, reportado como "Ciclo completo". | Pragmático | R7 |
| m5 | O relatório do ciclo é destruído no sucesso: `admin.py:2404` faz `location.reload()`. E `SLEEP_STEP_LABELS` (`:2339-2345`) lista 5 dos 8 passos — faltam `reconcile_refs`, `dedupe_memories` e `reduce_to_concepts`, os três que alteram o grafo ou destroem dados. | Utilizador | R1 |
| m6 | `memories_purged` não tem **nenhum** caminho de leitura: só o escritor, uma migração e testes que contam linhas. A docstring (`skill_dream.py:209-219`) promete *"so a bad merge is recoverable"*. E `_archive_purged_memories` devolve `len(rows)` em vez de `cur.rowcount` — "N memórias arquivadas" é o tamanho da entrada. Os marcadores `memory:` crescem sem limite (53 em produção para 38 memórias). | Utilizador | R6 |
| m7 | `update_rag` documenta *"summary, tags, facts or mermaid"* mas `RAG_FIELDS = {"summary","tags","facts"}` (`graph_edit.py:62`), e `routes.py:898` envia `mermaid` → qualquer POST com `mermaid` recebe 422 e perde também os campos válidos. | Utilizador | R6 |

---

## Verificações do moderador — e uma que **não** passou

A skill exige que um finding sem `closure-condition` verificável seja rejeitado.
Apliquei o mesmo a mim próprio: verifiquei as três alegações BLOCKER de
persona única mais perigosas. Duas confirmaram-se; **uma não**.

**Alegação (Purista, BLOCKER, R2):** *"A suíte de testes escreve na base de
dados real: `config.DB_PATH` não é isolado e
`test_perform_dreaming_extracts_concepts_before_the_purge` executa o
`_dedupe_memories()` real, que faz DELETE."*

Metade certa, conclusão errada. O que verifiquei:

```
DB_PATH       = /home/seyon/dev/pHantasma/data/brain.db
BRAIN_DB_PATH = /home/seyon/dev/pHantasma/data/brain.db
conftest.py:106  _cfg.CONFIG_DB_PATH = str(_CFG_DB)
conftest.py:107  _cfg.BRAIN_DB_PATH = str(_BRAIN_DB)     ← DB_PATH não é isolado em conftest
```

E `skill_dream.py:350-367` confirma o `DELETE FROM memories` + `commit()` sobre
`config.DB_PATH`.

**Mas** `tests/test_dream_graph.py:30` faz
`monkeypatch.setattr(config, "DB_PATH", str(path))` — a fixture `db` isola-o,
e é a única chamada directa a `_dedupe_memories` em toda a suite
(`test_dream_graph.py:302`, com a fixture). As outras vinte substituem-na
inteiramente. Por isso a suite **não** toca na base real: `data/brain.db` mantém
as 51 memórias depois de duas execuções completas.

**O defeito real é mais estreito e mais lento do que o alegado:** o isolamento
de `config.DB_PATH` é *opt-in por teste* e não é assegurado por nenhum lado.
Um teste novo que chame `_dedupe_memories` sem a fixture `db` apaga memórias da
base de desenvolvimento do dono, em silêncio, e nenhum teste fica vermelho.
Isto é um **MAJOR de R3**, não um BLOCKER de R2 — e escrevo-o assim porque o
BLOCKER não seulhava.

Isto é o ponto do protocolo multi-perspectiva: uma única persona, a mais
alarmada, encontrou algo real **e** exagerou. Aceitar o BLOCKER era aceitar o
medo em vez da evidência.

---

## O padrão por trás

Isto não são 14 defeitos independentes. **São um defeito, visto de quatro ângulos.**

Quatro de cinco clusters de BLOCKER partilham a mesma forma: *uma coisa
declarada que nada garante.*

- A docstring diz que as escritas são auditadas (B2) — não são.
- A docstring diz que as arestas guardam `node_keys` (B4) — guardam labels.
- O comentário diz que a afinidade vai de -1 a 1 (B5) — vai a 61.
- O comentário diz que `FORBIDDEN` é sempre suprimido (M6) — não é lido.
- O comentário diz que `schema.sql` espelha produção (M1) — faltam 4 tabelas.
- A resposta ao dono diz "os sonhos foram suprimidos" (M4) — não foram.

Isto é o registo do T057 outra vez, noutro registo: um caminho automático
—o GMIF, o sono, o `apply_reward`— escreve no activo sem passar pelo código que
fala dele. O editor à mão tem guardas (`_clamp`, `delete_node` recusa,
`graph_edit_audit`); o caminho que corre sozinho à noite não tem nenhuma.

**A regra que daí sai, para a lição:** *quando duas partes do sistema discordam
sobre um facto — o que o comentário promete e o que a linha faz, o que o README
promete e o que o `tools.py` chama — o comentário não é a evidência e o README
não é a evidência. A única evidência é correr a coisa e olhar.* Foi o que fez
esta revisão encontrar a B1, a B6, a B8 e o BLOCKER que rebaixei, e foi também
o que impediu o BLOCKER de subir sem provas.

## Registo

| | |
|---|---|
| findings brutos | 48 (12 × 4 personas) |
| clusters | 14 (6 BLOCKER, 12 MAJOR, 7 MINOR) |
| convergência | M1: 4/4 · M2, B3: 3/4 · B4, B5, M3, M7, M8: 2/4 · resto: 1/4 |
| divergências de severidade preservadas | 8 |
| findings rebaixados pelo moderador | 1 (BLOCKER → MAJOR, M4, com o motivo escrito) |
| findings rebaixados por verificação | 1 (BLOCKER → Major de R3, ver acima) |
| claims de persona única verificados pelo moderador | 3 (B1, B6, B8 confirmados) |
| base de produção | só-leitura; o texto das memórias não foi lido em nenhum momento |