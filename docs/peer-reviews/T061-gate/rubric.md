# Rubrica pré-registada — T061 (revisão da mudança T060)

Peer review da mudança que implementa o portão de promoteabilidade:
`src/brain/memory_graph.py`, `src/api/memory_graph.py`,
`tests/test_graph_vocabulary_gate.py`, `tests/test_memory_graph.py`.

## Limitação declarada, antes de tudo

**Esta revisão não é cega ao autor.** Eu escrevi o código. A rubrica de T059
foi pré-registada *antes* de o autor ver o alvo, e isso é o que dá força ao
gate. Aqui isso não é possível, e fingir que é seria exactamente o defeito que
esta rubrica existe para apanhar.

O que **é** garantido, e por isso a revisão ainda vale:

* A rubrica está written e commitada **antes** de qualquer revisor a ver.
* Os quatro revisores são subagentes frescos, sem a minha avaliação, sem o meu
  raciocínio e sem a história da conversa.
* As perguntas da rubrica estão escritas de forma a poderem **condemnar** a
  mudança — em particular R2 e R6, que existem precisamente porque o autor
  tende a subestimar o raio de blast e a alargar o âmbito.
* Qualquer finding que eu não consiga responder é reportada como não resolvida,
  não como aceite.

## Âmbito

| Ficheiro | O que mudou |
|---|---|
| `src/brain/memory_graph.py` | `is_speech_label`, `is_language_tag`, `LANGUAGE_TAGS`, portão em `index_memory` (duas portas + tópico corrente) |
| `src/api/memory_graph.py` | `_informative_text`, `_memory_label(parsed, mem_id)`, `preview` deixa de ser a tag, filtro de tag de idioma em `build_graph` |
| `tests/test_graph_vocabulary_gate.py` | 37 testes, novo |
| `tests/test_memory_graph.py` | uma asserção re-especificada |

**Fora do âmbito:** a limpeza dos dados já gravados (ticket à parte), e o
defeito B6 (`_EDGE_RE` não ancorado), que o autor Endureceu mas não corrigiu.

## Dimensões

### R1 — O código faz o que a docstring diz?
 Particularmente para `is_speech_label`: os três sinais estão descritos como
introduzidos, e a descrição bate com o comportamento? O docstring menciona
"defence in depth" para `_LEADING_JUNK_RE` — e é?

### R2 — Raio de blast e falsos positivos
A regra é **estreita de propósito**. A pergunta: rejeita ela algum conceito
legítimo? O autor mediu 4 nós de 190 e 5 pontas de 197, e é isso que basta?
Procurar o caso que ainda não foi considerado — multi-língua, símbolos,
acentos, maiúsculas, rótulos muito longos mas legítimos.

### R3 — Falsabilidade dos testes
Cada `test_` falha sem a mudança? A alteração de assinatura em `_memory_label`
e a re-especificação de `test_memory_graph.py:52` enfraqueceram alguma
asserção, ou strengthening? Há mock, ou tudo vai à base real?

### R4 — Risco de regressão
`preview` deixou de ser a tag — quem mais o consome? `index_memory` passou a
poder devolver `[]` — os chamadores assumem lista não vazia?
`skills/skill_memory.py:71` e `apply_reward` sobre o tópico corrente.

### R5 — Honestidade da documentação
Os comentários e a rubrica em `aes/tickets/T060-*.md` batem com o que o código
faz? Alguma afirmação sobreviveu de quando o código era outro?

### R6 — Disciplina de âmbito
O que foi aprovado foi "1+2+3". Houve mais? `_LEADING_JUNK_RE` é 4º item
introduzido sem aprovação — justificado ou não?

### R7 — Custo no caminho quente
`is_speech_label` corre por etiqueta e por ponta de aresta, em cada
`index_memory`. `import` de `src.brain.memory_graph` dentro de
`src.api.memory_graph.py` — ciclo, custo, ou ambos?

### R8 — Reversibilidade
É prevenção, não migração? Dá para desligar o portão numa linha? A limpeza de
dados ficou dependente deste código?

## Formato

```
type: BLOCKER | MAJOR | MINOR
title: <uma linha>
evidence: <caminho:linha, ou saída de comando>
closure-condition: <o que mecanicamente fecha isto>
rubric-criterion: <R1..R8>
```

Máximo 10 findings por persona, ordenados por gravidade. `TOTAL: <n>` no fim.
Um finding sem `closure-condition` verificável é rejeitado.