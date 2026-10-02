# T061 — Peer review da mudança T060, e o que a review mudou

**Rubrica:** [`rubric.md`](rubric.md) · sha256 `645f8ef6359930eccdfe63cd4524d530db75cea4fbefc9c1507d98dc1191c090`
**Limitação declarada:** o autor escreveu o código sob revisão. Ver a secção
"Limitação" da rubrica. O que é garantido: rubrica commitada antes de os
revisores a verem, e quatro subagentes frescos sem a avaliação do autor.
**39 findings brutos → 12 clusters. 1 BLOCKER, ambos corrigidos antes de commit.**

---

## O BLOCKER: eu tapei a porta errada

Quatro personas, três independentemente, e **todas têm razão**. Verifiquei
antes de mexer:

```
payload: {"tags": ["pt-BR", "Bom, parece que há um mistério aqui...", "Leite"]}

index_memory(...)        -> nada escrito                     ✓ portão funciona
materialize_memories(...) -> {'nodes_written': 3, 'edges_written': 3}
   node 'pt-BR'
   node 'Bom, parece que há um mistério aqui...'
   node 'Leite'
   edge 'pt-BR + Bom, parece que há um mistério aqui...'
```

`materialize_memories` filtrava só por comprimento
(`MIN_CONCEPT_LEN <= len(c) <= MAX_CONCEPT_LEN`, `memory_graph.py:203`) e
nunca chamava o portão. E é **o escritor que produziu 179 dos 190 nós** de
produção (`gmif_classified_by = 'materialize_memories'`), chamado todas as
noites por `skill_dream.py:698`.

A consequência é pior do que "incompleto". A frase que eu escrevi na mensagem
de commit — *"limpar os dados sem isto seria remendo, o próximo ciclo volta a
promover"* — era **falsa**. O próximo ciclo promovia na mesma, por um caminho que
eu não toquei. O portão fechava a porta que já estava fechada.

**Correcção:** o filtro de `concepts` em `materialize_memories` chama os mesmos
dois predicados. As arestas derivam de `concepts`, por isso um filtro cobre os
dois escritores. Novo teste: `test_materialize_memories_is_gated_too`, que
falha contra `641844d`.

---

## Corrigido

### B1 — `materialize_memories` sem portão → BLOCKER, R1/R2
Acima. Falso: o gate estava no sítio errado.

### M1 — A regra do "olá" comia conceitos → R2, 4/4 personas
```
antes:  is_speech_label('Bom dia a todos')        = True
        is_speech_label('Bom uso da água')        = True
        is_speech_label('well being of society')  = True
        is_speech_label('So it goes')             = True
        is_speech_label('Hello Kitty Merchandise')= True
        is_speech_label('Ok Corral Vermelho')     = True
        is_speech_label('A vida tem sentido?')    = True
```

E o que o resto da review acrescenta, e que é a parte honesta: **isto era uma
regra de contagem de palavras disfarçada.** `bool(GREETING_RE.match(text)) and
len(text.split()) > 2` é uma regra de palavras com um prefilter. E o ticket
T060 tinha rejeitado uma regra de palavras no dia anterior, exactamente por
isto. Escrevi a regra que disse não escrever.

**Correcção:** a clause de `!`/`?` foi **removida** — medição: **zero**
verdadeiros positivos nos 190 rótulos de produção, porque toda a fala de
produção que acaba em `!` acaba também em reticências — e o opener passou a
exigir **vírgula**. A vírgula é o que separa `"Olha, não sou uma pessoa que se
apresenta"` de `"Bom uso da água"`. Oito casos fixados por teste, e um teste que
prova que o estreitamento **não perdeu** nenhuma das cinco amostras de produção.

### M2 — A proveniência de `LANGUAGE_TAGS` era falsa → R1/R6, 2 personas
O comentário dizia *"Language and locale markers the tagger emits"*. **Não há
tagger nenhum**: `grep -rni '\baoe\b|pt-br' src/ skills/` não encontra emissor, e
o único prompt que dita uma tag é `skill_memory.py:43`, que dita `"Tag"` e mais
nada. A lista era extrapolação a partir de dois valores observados, e
`"Português"` passava_right por ela.

**Correcção:** o comentário passa a dizer o que a lista é, com a medição e a
limitação, e remete para o ROADMAP. E **`tag` foi acrescentado** — é o literal
que o prompt emite e é `node:tag`, o tópico corrente em produção. É boilerplate
de prompt, não um conceito. Uma linha para reverter.

### M3 — O ramo da elipse em `_memory_label` era código morto → R1, 4 personas
`_informative_text` truncava a `_MAX_LABEL`, logo o `len(text) > _MAX_LABEL` de
`_memory_label` nunca era verdade. **A mudança introduziu o corte silencioso**:
antes os rótulos vinham do `preview` (não truncado) e a elipse funcionava; ao
mover a fonte para os factos, os factos longos passaram a ser cortados a meio
de uma palavra sem marca. Verificado: fact de 100 chars → rótulo de 60, sem
elipse. **Correcção:** a truncagem passou a viver num sítio só, e há teste a
afirmar que um fact longo termina em `…` (61 chars).

### M4 — `preview` nunca testado → R4, 2 personas
Escolha minha, agora **fixada por teste**: `preview` é `""` para uma memória cuja
única conteúdo é um marcador de idioma. Mostrar o cabeçalho JSON
(`{"tags": ["pt"], "facts": []}`) é pior. O revisor pediu um teste que fixe o
que `preview` deve ser; existe.

### MINOR — `_LEADING_JUNK_RE` limpa para a decisão e guarda o lixo → R6, 2 personas
`is_speech_label` decide sobre `'capitalismo'` depois de tirar `;\n`, mas o
`upsert_edge` grava o valor **com** o `;\n`. A "defence in depth" é verdadeira
para a detecção e falsa para o armazenamento. **Não corrigido** — é o defeito
B6 e a correcção é de lá. Fica registado.

### MINOR — a tabela de falsificação do commit citava `memória 19/20/21` → R5, 2 personas
**Tem razão, e é uma correcção minha, não do código.** As três memórias de
produção *têm* facts (3, 4 e 4), portanto a cadeia fact→tag→texto nunca chega ao
id: os rótulos reais são os factos truncados a 60 chars. O ramo
`f"memória {mem_id}"` é **inalcançável em produção** — e é exactamente o ramo
que os meus testes exercitam. Um teste com um payload que não existe.

---

## Não corrigido, e porquê

| # | finding | porquê não |
|---|---|---|
| 1 | **A queda de recall**: `is_speech_label` só apanha reticências ou opener+vírgula. `"Não estou muito à vontade hoje."` passa. | Declarado num teste chamado `test_the_known_gap_is_named_not_hidden`. As cinco amostras de produção têm reticências ou vírgula. Endurecer a regra volta a comer conceitos, e o custo de um falso positivo — perder um conceito do único grafo do dono — é maior. **Decisão do dono se quiser o contrário.** |
| 2 | `upsert_node`/`upsert_edge` continuam sem portão; `skill_dream.py:816` escreve por lá | É a terceira porta, e o gate em `materialize_memories` + `index_memory` cobre os dois escritores de produção. Mover o portão para `upsert_node` é mais limpo e é o passo certo, mas é uma mudança de maior âmbito que o que foi aprovado. |
| 3 | `_concept_from_reply` (`skill_dream.py:883`) é um segundo portão, divergente | Idem. |
| 4 | Nada regista o que o portão rejeitou | Verdade, e é o mais importante dos pendentes: um conceito bom rejeitado é indistinguível de um que nunca foi extraído. Não o implementei porque a resposta é do dono (logger? estatística no `/admin`? ambos?), não minha. |
| 5 | Sem kill switch | Verdade. Reverter hoje é editar código + `deploy.sh`, não um toggle. `settings_store.get_setting` é o mecanismo. |
| 6 | `topic_state` continua `node:tag` e `apply_reward` recria o nó apagado | `apply_reward` faz `ON CONFLICT DO NOTHING`, ou seja **recria** pela chave do tópico. Um 👍 sem texto ressuscita `node:tag`. Depende do ticket de limpeza. |
| 7 | A tela muda menos do que a resposta ao dono sugere | `nodes 236→234`: saem `tag:pt` e `tag:dev`; os 4 nós de fala **ficam**, por decisão declarada (esconder também os esconderia da única interface que os apaga). A Justificação desse comentário está errada — a lista de candidatos vem de um `SELECT` directo, não de `build_graph` — e o comentário deve ser corrigido. |
| 8 | A asserção re-especificada em `test_memory_graph.py:52` enfraqueceu? | Verificado por uma persona: ficou **mais forte** (igualdade mais uma asserção negativa). |
| 9 | `it`, `nl`, `portugues` não estão na lista | Verdade, e é a dívida da lista fechada. Registado no ROADMAP. |

---

## O padrão, outra vez

O BLOCKER e o M1 são **a mesma coisa**: eu construí o mecanismo certo e pusem-no
no sítio errado, depois afinei-o com um critério que não era o que dizia ser.

E há uma correcção minha que vale mais que os findings: a **medição de raio de
blast estava viciada**. Eu medi falsos positivos sobre "os oito nós mais curtos
que sobrevivem" — `Tag`, `casa`, `gato` — e nenhum deles começa por um opener, ou
tem reticências, ou acaba em `!`. **A lista não continha nenhum membro da classe
que a regra nova pode rejeitar**, portanto não podia falhar, e portanto não
certificava nada. A review viu-o em três personas ao mesmo tempo.

Isto liga ao que escrevi no `docs/aes-infra-audit.md`: uma verificação que não
pode falhar é uma verificação que não mede. A minha era exactamente isso, e
passava-me porque eu é que a escrevi.

---

## Registo

| | |
|---|---|
| findings brutos | 39 (10 + 10 + 10 + 9) |
| clusters | 12 |
| BLOCKER | 1 — corrigido antes do commit |
| MAJOR corrigidos | 2 (regra do "olá", proveniência da lista) + 2 MINOR (elipse, preview) |
| findings não corrigidos | 9, cada um com o motivo |
| claims de BLOCKER verificados pelo moderador | 1 — confirmado por reprodução própria, não por confiança |
| falsificação | `materialize_memories` escreve 3 nós + 3 arestas em `641844d`; `[]` agora |
| suite | 1401 passed, 17 errors pré-existentes, ruff limpo |
| uma corrida intermédia mostrou 1 falha transitória; duas corridas completas seguintes limpas. Causa-raiz não investigada — registado como não verificado. |