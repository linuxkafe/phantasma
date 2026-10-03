#!/usr/bin/env python3
"""Measure candidate models on what actually broke, not on a benchmark.

Why this exists. Measured 2026-10-03, production, asked "o que achas do Edgar
Allan Poe", `llama3.1:8b` answered in Brazilian Portuguese:

    "O escritor Edgar Allan Poe. Uma mente sombria e fascinante... A sua obra e
     uma denuncia da hipocrisia..."

Three prompts did not move it. Bare question: still "Sua obra". With the Czech
and Spanish search results attached: still "Seu legado". With an explicit
substitution table ("sua obra -> a tua obra"): "Sua obra", "Seus poemas" --
worse. So the deciding question is not "which model is best in general", it is
"which of these writes European Portuguese and stays in voice", measured on the
same prompt the owner actually used.

Design choices that matter for the numbers being comparable:

- The real production prompt. The harness calls `_respond_with_llm_body` with a
  stub client and captures the messages, so what each model receives is byte for
  byte what the service sends. A simplified prompt measures a different system.

- The same search payload for every model, canned. Measured reality is garbage
  ("ChatGPT" results for a question about Poe), but letting each candidate
  search for itself would make the comparison depend on a live engine.

- No cache. `_respond_with_llm_body` checks the response cache before anything
  else, and a hit returns a stored answer instead of the model's -- which would
  have every candidate "passing" on someone else's prose.

- A recursion limit while scoring. The scoring is not the interesting part and
  a hung candidate should fail the run rather than stop it.

Usage:
    scripts/eval_models.py --host http://10.0.0.128:11434 --models llama3.1:8b
    scripts/eval_models.py --host http://localhost:11434 --models qwen3:8b --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# The canned search payload. Taken verbatim from what SearXNG returned on
# 2026-10-03 for "o que achas do Edgar Allan Poe" -- three results about ChatGPT,
# in Czech and Spanish. If a candidate can stay in voice with this attached,
# it can stay in voice with a real result.
CANNED_WEB = (
    "- ChatGPT: Chat, Work, Create & Code with AI\n"
    "  Use ChatGPT to answer questions, write, create images, complete work, and\n"
    "  code—all in one place. Get started for free or download …\n"
    "- ChatGPT\n"
    "  ChatGPT helps you get answers, find inspiration, and be more productive.\n"
    "- Introducing ChatGPT - OpenAI\n"
    "  30 de nov. de 2022 · We've trained a model called ChatGPT which interacts in\n"
    "  a conversational way."
)

# Each prompt carries what a CORRECT answer looks like, because the first run
# of this harness ranked a model that says nothing first.
#
# Measured 2026-10-03, qwen2.5:7b, score 84 -- above llama3.1:8b's 67 -- with
# these answers:
#
#     "o que é o aspire?"    -> "Não sei, Phantasma não guardou informações
#                                sobre o termo 'aspire' neste contexto."
#     "obrigado"             -> "Não sei, Phantasma não recebeu qualquer
#                                pergunta específica para responder."
#
# Short, no Brazilian markers, nothing to cite: 84 out of 100. The scoring had
# no notion of whether the answer was any use, so refusing everything was the
# highest-scoring strategy available. Brevity was rewarded and emptiness was
# invisible.
#
# So every prompt declares its own expectations. Abstention is CORRECT for one of
# them and WRONG for the others, which is exactly the distinction a length-based
# score cannot make.
PROMPTS: list[dict[str, Any]] = [
    {
        "id": "opinion_poe",
        "text": "o que achas do Edgar Allan Poe",
        "why": "the measured failure: Brazilian Portuguese, and a literary review "
        "in the third person instead of the house speaking",
        "mentions": ["poe"],
        "must_abstain": False,
        "forbids": ["dicionário", "dicionario", "wikipedia", "apartamento",
                    "significa", "significar", "definição"],
        "max_words": 110,
    },
    {
        "id": "greeting",
        "text": "Olá",
        "why": "answered with a dictionary definition of the word 'Olá' -- "
        "Infopedia, the Wikipedia disambiguation page, an apartment in Svinoústí",
        "mentions": [],
        "must_abstain": False,
        "forbids": ["dicionário", "dicionario", "wikipedia", "apartamento",
                    "significa", "significar", "desambiguação", "significado"],
        "max_words": 45,
    },
    {
        "id": "self",
        "text": "quem sou eu",
        "why": "must come back with the dominant graph nodes, which is what "
        "defines the register of the house. NO abstention expectation here, and "
        "that is deliberate: the harness runs against the dev checkout, whose "
        "graph has zero nodes with positive affinity, so there is nothing to "
        "answer from and abstaining is the honest reply. Judging it would "
        "measure the harness, not the model",
        "mentions": [],
        "must_abstain": None,
        "forbids": ["chatgpt"],
        "max_words": 90,
    },
    {
        "id": "factual",
        "text": "o que é o aspire?",
        "why": "a fact the model can answer, and abstention here is a failure, "
        "not honesty",
        "mentions": ["aspire"],
        "must_abstain": False,
        "forbids": [],
        "max_words": 90,
    },
    {
        "id": "unknown_person",
        "text": "conheces o Chefe Jamon?",
        "why": "invented a person out of a Czech ham e-shop when the search "
        "returned him. Abstaining is the RIGHT answer here -- and the only "
        "prompt where saying 'I don't know' scores as success",
        "mentions": [],
        "must_abstain": True,
        "forbids": ["ham", "presunto", "charcutaria"],
        "max_words": 60,
    },
    {
        "id": "thanks",
        "text": "obrigado",
        "why": "should be short and stay on the topic. A model that pads here "
        "pads everywhere, and this is a voice assistant on a CPU",
        "mentions": [],
        "must_abstain": False,
        "forbids": ["chatgpt", "não sei", "nao sei", "pergunta específica",
                    "pergunta especifica"],
        "max_words": 40,
    },
]

# Said when the model should have answered.
#
# Mostly markers, but abstention has to be matched by pattern: the correct reply
# to "conheces o Chefe Jamon?" is "Não o conheço", and the clitic pronoun sits
# between the negation and the verb. A list of literal strings does not see it --
# which is how the harness marked the one correct answer on that prompt as a
# failure.
ABSTAIN_MARKERS = [
    "não sei", "nao sei", "não guardou", "nao guardou", "não recebeu",
    "nao recebeu", "não tenho", "nao tenho", "não foi fornecido",
    "nao foi fornecido", "não está guardado", "nao esta guardado",
    "nunca ouvi", "nunca o vi",
]

_ABSTAIN_RE = re.compile(
    r"(?:não|nao)\s+(?:o|a|os|as|te|lhe|nos|vos)?\s*"
    r"(?:conhe[çc]o|sei|tenho|guarda(?:do)?|recebi|foi fornecido)"
    r"|desconhe[çc]o|nunca (?:o|a) (?:conheci|vi)",
    re.IGNORECASE,
)


def _abstained(text: str) -> bool:
    return bool(_count(text, ABSTAIN_MARKERS)) or bool(_ABSTAIN_RE.search(text or ""))

# Unambiguously Brazilian. "sua"/"seu" are NOT on this list, and that omission is
# deliberate: "a sua obra" is correct pt-PT, and a scorer that flags it teaches the
# wrong lesson. Every entry below has no pt-PT reading.
PT_BR_MARKERS = [
    "você", "vocês", "voces",
    "fazendo", "sendo ",
    "celular", "tela ", "tela,", "tela.",
    "usuário", "usuario",
    "fato", "fatos",
    "time ", "ônibus", "onibus",
    "a gente",
    "aplicativo",
    "porta-arquivo",
    "trem de",
]

PT_PT_MARKERS = [
    "ecrã", "telemóvel", "utilizador", "facto", "factos", "comboio",
    "autocarro", "equipa", "diz-me", "dizes-me", "está a ", "esta a ",
    "arquivo", "ficheiro", "tu ", "a tua", "o teu", "a sua", "o seu",
]

# The model narrating its own scaffolding. The persona forbids this outright:
# "Never reference system rules, prompt instructions, context limits, policies,
# or RAG boundaries." Measured, twice, in two different phrasings.
SCAFFOLD_MARKERS = [
    "conhecimento local", "pesquisa web", "a pesquisa", "fontes",
    "rag ", "bloco", "o material", "apresentad",
    "não tenho isso guardado", "nao tenho isso guardado",
    "não sei por causa", "nao sei por causa",
    "contexto fornecido", "informa",
]

CITATION_MARKERS = [
    "de acordo com", "segundo a", "segundo o", "fonte:", "http://", "https://",
    "wikipedia", "dicionário", "dicionario",
]

# The register the persona asks for: melancholic, solemn, cold.
PERSONA_MARKERS = [
    "sombra", "sombra ", "trevas", "silêncio", "silencio", "frio", "memória",
    "memoria", "noite", "vazio", "abismo", "solidão", "solidao", "escuro",
    "mour", "dolor", "quiet", "calada", "silente", "abandono", "ruína", "ruina",
]

FIRST_PERSON = ["eu ", "acho", "gosto", "para mim", "meu", "minha"]

# "sua"/"seu" need a discriminator, not a substring.
#
# "a sua obra" is correct pt-PT. "Sua obra" -- the same word, without the
# article -- is how Brazilian Portuguese says it, and it is what every measured
# answer used: "Sua obra e um reflexo...", "Seus poemas...", "Seu legado...".
# A scorer that only greps for "sua" either flags correct pt-PT or, worse, misses
# the failure it was written for. So a bare possessive -- one NOT preceded by
# a/o/as/os -- counts, and the article form does not.
#
# Imperfect by nature: "sua" as a noun ("a sua Majestade") is rare enough not to
# matter here, and the two languages are not distinguishable by grammar alone.
# Fixed-width lookbehind: `[ao]s?` is 1 OR 2 characters and Python's re rejects
# variable-width lookbehind, so the optional plural is spelled out as two
# alternatives. "as suas" -> the `as ` branch matches; "a sua" -> `a `; a bare
# "Sua" at the start matches nothing before it and is therefore bare.
_BARE_POSSESSIVE = re.compile(
    r"(?<!a\s)(?<!o\s)(?<!as\s)(?<!os\s)\b(sua|seu|suas|seus)\b", re.IGNORECASE
)


def _count(text: str, markers: list[str]) -> list[str]:
    low = text.lower()
    return [m for m in markers if m in low]


def score(text: str, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per-answer scores. Higher is better except where the key says lower.

    `spec` is the prompt's own expectations. Without it the caller gets style
    scores only, which is how the first run ranked a model that refused every
    question first -- so any scoring path used for a decision passes the spec.
    """
    if spec is None:
        raise TypeError("score() needs the prompt's expectations")
    words = len((text or "").split())
    br = _count(text, PT_BR_MARKERS)
    bare = sorted({m.lower() for m in _BARE_POSSESSIVE.findall(text or "")})
    scaffolding = _count(text, SCAFFOLD_MARKERS)
    citations = _count(text, CITATION_MARKERS)
    persona = _count(text, PERSONA_MARKERS)
    ptpt = _count(text, PT_PT_MARKERS)
    first = _count(text, FIRST_PERSON)
    abstained = _abstained(text)

    # Per-prompt checks. Each failure is worse than any style problem, because a
    # style problem is a matter of taste and a failed check is a wrong answer.
    failures: list[str] = []
    low = (text or "").lower()

    for word in spec.get("mentions") or []:
        if word.lower() not in low:
            failures.append(f"never mentions {word!r}")
    for banned in spec.get("forbids") or []:
        if banned.lower() in low:
            failures.append(f"mentions {banned!r}")
    limit = spec.get("max_words")
    if limit and words > limit:
        failures.append(f"{words} words, limit {limit}")
    if spec.get("must_abstain") and not abstained:
        failures.append("invented an answer instead of saying it does not know")
    if spec.get("must_abstain") is False and abstained:
        failures.append("abstained where it could have answered")

    return {
        "failures": failures,
        "chars": len(text or ""),
        "words": words,
        "pt_br": len(br) + len(bare),
        "pt_br_found": br + [f"bare:{m}" for m in bare],
        "pt_pt": len(ptpt),
        "scaffolding": len(scaffolding),
        "scaffolding_found": scaffolding,
        "citations": len(citations),
        "citations_found": citations,
        "persona": len(persona),
        "first_person": len(first),
        "abstained": abstained,
        "text": text,
    }


def verdict(answers: list[dict[str, Any]]) -> dict[str, Any]:
    """One verdict per model, from the answers.

    Weighted because the failures are not equal. Brazilian Portuguese is the
    reason the model is being replaced at all -- it is disqualifying, and it is
    weighted as such rather than averaged away against a good persona score.
    Narration of the scaffolding is next: the owner sees it and it is forbidden
    outright. Then length, because this runs on a CPU at ~5-10 tok/s and a
    240-word answer is a minute of silence.
    """
    n = len(answers) or 1
    pt_br = sum(a["pt_br"] for a in answers)
    scaffold = sum(a["scaffolding"] for a in answers)
    citations = sum(a["citations"] for a in answers)
    persona = sum(a["persona"] for a in answers)
    words = sum(a["words"] for a in answers)
    fp = sum(1 for a in answers if a["first_person"])
    failed_checks = sum(len(a.get("failures") or []) for a in answers)
    wrong = sum(1 for a in answers if a.get("failures"))

    # 100 is the ceiling. Brazilian Portuguese and scaffolding are penalties, not
    # bonuses: a model that writes beautifully in pt-BR still loses.
    # Bonuses are capped, because the sum of them was not: six answers with
    # three persona markers each added 108 on top of the ceiling, so a
    # well-written model scored 280 out of a stated 100 and the scale meant
    # nothing.
    score_ = 100.0
    score_ -= 25.0 * pt_br
    score_ -= 20.0 * scaffold
    score_ -= 10.0 * citations
    score_ += min(6.0 * persona, 30.0)
    score_ += min(3.0 * fp, 12.0)
    # A failed check is a wrong answer, not a style difference. Heavier than
    # every style penalty combined, so no amount of good prose buys it out.
    # 30 per failed check saturated the scale: two models with three failures
    # each both read 0.0, and a score that cannot separate two candidates cannot
    # choose between them.
    score_ -= 18.0 * failed_checks
    score_ -= 6.0 * wrong
    # Length: over 90 words per answer starts costing. The persona asks for
    # brevity and the machine is slow.
    avg_words = words / n
    if avg_words > 90:
        score_ -= (avg_words - 90) * 0.15
    return {
        "score": round(min(max(score_, 0.0), 100.0), 1),
        "pt_br_total": pt_br,
        "scaffolding_total": scaffold,
        "citations_total": citations,
        "persona_total": persona,
        "avg_words": round(avg_words, 1),
        "first_person_answers": fp,
        "failed_checks": failed_checks,
        "answers_with_failures": wrong,
        "n_answers": n,
    }


def _capture_prompts(with_web: bool = True) -> dict[str, list[dict[str, str]]]:
    """The exact messages production would send, per test prompt.

    Built through the real code path with a stub client, so the harness cannot
    drift from what the service sends. The cache is bypassed by asking each
    prompt once, before capturing: a stored answer would otherwise be returned
    instead of the model's, and every candidate would be scored on prose it did
    not write.
    """
    import ollama as ollama_mod

    import assistant as A

    # A separate holder for the stub's last messages. It used to share the dict
    # with the per-prompt results, and the `clear()` that starts each iteration
    # wiped the prompts already captured -- so six prompts produced one and a
    # half, and the run scored "[stub]" six times.
    holder: dict[str, Any] = {}
    captured: dict[str, list[dict[str, str]]] = {}
    real_client = ollama_mod.Client

    class Stub:
        def __init__(self, host=None, **kw):
            self.host = host

        def chat(self, model=None, messages=None, options=None, **kw):
            holder["last"] = messages
            return {"message": {"content": "[stub]"}}

        def list(self):
            return {"models": []}

    ollama_mod.Client = Stub
    # Two variants, because with the junk payload attached and without it the
    # candidates separate in a way that a single run hides.
    #
    # The prompt says "se o bloco não responder à pergunta, diz numa frase que
    # não sabes e para". With the measured junk attached -- three ChatGPT pages
    # for a question about Poe -- a model that follows that rule correctly says
    # it does not know. qwen2.5:7b abstained on three of six prompts, and the
    # harness scored that as three failures against a prompt of mine that
    # produced them.
    #
    # So each model is measured twice: with the search noise, and with none.
    # The second number is the model's own knowledge and voice, which is what the
    # owner is actually asking for. Neither number alone is the answer, and
    # picking a model on the first one is picking the most willing to refuse.
    if with_web:
        A.search_with_searxng = lambda q, *a, **k: CANNED_WEB
    else:
        A.search_with_searxng = lambda q, *a, **k: ""

    original_cache = A.get_cached_response
    A.get_cached_response = lambda *a, **k: None
    try:
        for item in PROMPTS:
            holder.clear()
            pipe = A.PhantasmaPipeline.__new__(A.PhantasmaPipeline)
            pipe._fly_brain = type("FB", (), {"step": lambda self, **k: None})()
            try:
                pipe._respond_with_llm_body(item["text"])
            except Exception:
                pass
            captured[item["id"]] = holder.get("last") or []
    finally:
        A.get_cached_response = original_cache
        # Leaving the stub installed meant every subsequent real call in this
        # process answered "[stub]", which is how the first run scored 100.0 for
        # both models on one word each.
        ollama_mod.Client = real_client

    missing = [i["id"] for i in PROMPTS if not captured.get(i["id"])]
    if missing:
        raise RuntimeError(f"prompts not captured: {missing}")
    return captured


def run_model(host: str, model: str, prompts: dict[str, list[dict[str, str]]],
              timeout: float, label: str = "web") -> dict[str, Any]:
    import ollama

    client = ollama.Client(host=host, timeout=timeout)
    answers = []
    for item in PROMPTS:
        messages = prompts.get(item["id"]) or []
        t0 = time.time()
        try:
            resp = client.chat(
                model=model,
                messages=messages,
                options={"temperature": 0.6, "num_predict": 400},
            )
            text = resp["message"]["content"]
            toks = resp.get("eval_count", 0)
            dur = (resp.get("eval_duration", 0) or 1) / 1e9
        except Exception as exc:  # noqa: BLE001
            answers.append({
                "id": item["id"], "error": f"{type(exc).__name__}: {exc}",
                **{k: 0 for k in ("pt_br", "scaffolding", "citations", "persona",
                                  "first_person", "pt_pt")},
                "words": 0, "chars": 0, "text": "", "seconds": 0.0, "tok_s": 0.0,
                "failures": [f"error: {type(exc).__name__}"], "abstained": False,
            })
            continue
        s = score(text, item)
        s.update(
            id=item["id"],
            question=item["text"],
            why=item["why"],
            seconds=round(time.time() - t0, 1),
            tok_s=round(toks / dur, 1) if dur else 0.0,
        )
        answers.append(s)
    out = verdict(answers)
    out["model"] = model
    out["variant"] = label
    out["host"] = host
    out["answers"] = answers
    out["n_answers"] = out.pop("n_answers")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://10.0.0.128:11434")
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--json", help="write the full result here")
    ap.add_argument("--show", action="store_true", help="print the answers too")
    args = ap.parse_args()

    variants = {}
    for label, with_web in (("web", True), ("noweb", False)):
        print(f"capturing production prompts ({len(PROMPTS)} prompts, "
              f"search={label})...", file=sys.stderr)
        variants[label] = _capture_prompts(with_web)

    results = []
    for model in args.models:
        for label in ("web", "noweb"):
            print(f"\n=== {model} @ {args.host} [{label}] ===", file=sys.stderr)
            res = run_model(args.host, model, variants[label], args.timeout, label)
            results.append(res)
            v = res["answers"]
        print(f"  score {res['score']:6.1f} | falhou {res['answers_with_failures']}"
              f"/{res['answers']} ({res['failed_checks']} checks) | "
              f"pt-BR {res['pt_br_total']:2} | "
              f"scaffolding {res['scaffolding_total']:2} | "
              f"citations {res['citations_total']:2} | "
              f"persona {res['persona_total']:2} | "
              f"avg {res['avg_words']:5.1f} words", file=sys.stderr)
        if args.show:
            for a in v:
                if a.get("error"):
                    print(f"    [{a['id']}] ERROR {a['error']}", file=sys.stderr)
                    continue
                print(f"    [{a['id']}] {a['tok_s']}tok/s {a['seconds']}s "
                      f"pt-BR={a['pt_br']}{a['pt_br_found']} "
                      f"scaf={a['scaffolding']}{a['scaffolding_found']}", file=sys.stderr)
                if a.get("failures"):
                    print(f"       FALHOS: {a['failures']}", file=sys.stderr)
                print(f"       {a['text'][:200]!r}", file=sys.stderr)

    print("\n" + "=" * 78)
    print(f"{'model':28} {'score':>7} {'falhou':>8} {'checks':>7} {'pt-BR':>6} "
          f"{'scaf':>5} {'cit':>4} {'pers':>5} {'words':>6}")
    print("-" * 92)
    for r in sorted(results, key=lambda r: -r["score"]):
        print(f"{r['model']:28} {r['score']:7.1f} "
              f"{r['answers_with_failures']:4}/{r['n_answers']:<3} "
              f"{r['failed_checks']:7} {r['pt_br_total']:6} "
              f"{r['scaffolding_total']:5} {r['citations_total']:4} "
              f"{r['persona_total']:5} {r['avg_words']:6.1f}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
        print(f"\nwritten: {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
