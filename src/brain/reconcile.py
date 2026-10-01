"""Reconcile dangling edge endpoints. The sleep cycle's T047 step.

Why this module exists
----------------------
`unresolved_edges` is not a stored fact. `memory_graph.build_graph` fabricates
a "dangling" concept at read time for every stored edge whose endpoint label
matches no concept, and counts it. The number is therefore recomputed from the
same data on every read, and none of the cycle's four original steps
(classify_edges, classify_nodes, consolidate_memories, gmif_dream) writes a
node, relinks an edge or prunes anything -- they only write `gmif_*` columns
and merge duplicate `memories` rows. The number could not move, however many
times the cycle ran. This module is the step that can move it.

The hard part is not the write, it is the judgement
----------------------------------------------------
For a pending label X and the stored nodes as candidates there are three
answers, not two:

* duplicate  -- X is the same concept as an existing node Y  -> relink to Y
* new        -- X is a real concept that simply had no node -> promote X
* ambiguous   -- the evidence does not settle it

`ambiguous` is a first-class outcome and it does NOT write. An LLM asked to
"fix a dangling reference" will always produce an answer; the only way to tell
a grounded answer from a confident guess is to require quoted evidence and
refuse to act without it. A wrong relink silently deletes a relationship, and
a wrong promote silently inflates the graph. An unresolved ref is visible and
recoverable; a hallucinated one is neither.

Writes go through `graph_edit.resolve_dangling`, so the guard rails (edge
exists, side is valid, target is a stored node, refuse no-op, audit in the same
transaction) live in exactly one place.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Optional

from src.api.memory_graph import normalise
from src.brain.graph_edit import EditError, resolve_dangling

ACTOR = "sleep-cycle"
RESEARCH_TIMEOUT_HINT = "oLLAMA/SearxNG"


# ---------------------------------------------------------------------------
# Extracting the pending refs without importing the reader's internals
# ---------------------------------------------------------------------------


def find_dangling(db_path) -> list[dict[str, Any]]:
    """Every stored edge endpoint whose label matches no stored node.

    Mirrors the reader's rule (compare `normalise(label)` against the node
    labels) rather than trusting the reader's `unresolved_edges` count, because
    the count is a single integer and the cycle needs to know WHICH endpoint of
    WHICH edge is pending in order to act on it.
    """
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        nodes = con.execute(
            "SELECT node_key, label FROM memory_graph WHERE node_type = 'node'"
        ).fetchall()
        edges = con.execute(
            "SELECT node_key, source, target, label FROM memory_graph WHERE node_type = 'edge'"
        ).fetchall()
    finally:
        con.close()

    by_label: dict[str, dict[str, str]] = {}
    for n in nodes:
        by_label.setdefault(normalise(n["label"]), {"node_key": n["node_key"], "label": n["label"]})

    pending: list[dict[str, Any]] = []
    for e in edges:
        for side in ("source", "target"):
            label = e[side]
            if not label:
                continue
            if normalise(label) in by_label:
                continue
            pending.append(
                {
                    "edge_key": e["node_key"],
                    "side": side,
                    "label": label,
                    "other_side": "target" if side == "source" else "source",
                    "other_label": e["target"] if side == "source" else e["source"],
                    "edge_label": e["label"],
                    "candidates": [{"node_key": n["node_key"], "label": n["label"]} for n in nodes],
                }
            )
    return pending


# ---------------------------------------------------------------------------
# Evidence. Without it, nothing is written.
# ---------------------------------------------------------------------------


def _search(prompt: str) -> list[dict[str, Any]]:
    """Online search, same mechanism gmif_dream already uses.

    Imported lazily and defensively: a missing SearxNG must degrade the step,
    never abort the cycle that called it.
    """
    try:
        from tools import search_with_searxng
    except Exception:  # noqa: BLE001
        return []
    try:
        return search_with_searxng(prompt, max_results=5) or []
    except Exception:  # noqa: BLE001
        return []


def _ask(prompt: str, system: str) -> Optional[str]:
    try:
        from skills.skill_dream import _safe_ollama_chat
    except Exception:  # noqa: BLE001
        return None
    try:
        return _safe_ollama_chat(prompt, system)
    except Exception:  # noqa: BLE001
        return None


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def judge_pending(item: dict[str, Any]) -> dict[str, Any]:
    """Decide what to do about one pending endpoint.

    Returns a dict with at least `decision` in {relink, promote, ambiguous, skip}
    and, for a decision that will be written, `evidence`: quoted text. A
    decision without evidence is downgraded to `ambiguous` by the caller, which
    is the whole point of this function existing.
    """
    label = item["label"]
    candidates = item["candidates"]
    # A shortlist, not all 180 nodes: a prompt with 180 labels invites the
    # model to pick whichever sounds closest, which is the failure we are here
    # to prevent.
    shortlist = sorted(
        candidates,
        key=lambda c: -_token_overlap(label, c["label"]),
    )[:8]

    if not shortlist:
        return {
            "decision": "promote",
            "target_label": label,
            "evidence": [],
            "reason": "no stored nodes exist yet; the label is its own node",
        }

    names = [c["label"] for c in shortlist]
    query = _ask(
        f"Para decidir se um rótulo é um conceito novo ou uma variation de "
        f"nome de um conceito existente, gera UMA query de pesquisa web. "
        f"Rótulo pendente: '{label}'. Conceitos candidatos: {names}. "
        f"Responde apenas com a query, sem aspas.",
        "Especialista em Taxonomia de Conceitos.",
    )
    results = _search((query or label).strip()) if query else []

    context = json.dumps(results, ensure_ascii=False)[:4000] if results else ""
    prompt = (
        f"Um grafo de conhecimento tem uma referência guardada cujo alvo não "
        f"existe.\n"
        f"Rótulo pendente: '{label}'\n"
        f"É ligado a partir de: '{item.get('other_label')}'\n"
        f"Conceitos já guardados (candidatos): {names}\n\n"
        + (
            f"Resultados de pesquisa web:\n{context}\n\n"
            if context
            else "NÃO HÁ RESULTADOS DE PESQUISA DISPONÍVEIS.\n\n"
        )
        + "Responde em JSON com:\n"
        '{"decision": "relink|promote|ambiguous",\n'
        ' "target_label": "rótulo existente para relink, ou o próprio rótulo para promote",\n'
        ' "evidence": ["trecho literal que sustenta a decisão"],\n'
        ' "confidence": 0.0-1.0,\n'
        ' "reason": "uma frase"}\n\n'
        "Regras, sem excepção:\n"
        f"- 'relink' SÓ se '{label}' e o conceito escolhido forem o MESMO "
        "conceito com outro nome, E houver um trecho de pesquisa que o diga.\n"
        "- 'promote' SÓ se for um conceito genuinamente distinto de TODOS os "
        "candidatos.\n"
        "- Se não houver evidência nos resultados, ou se houver dúvida real, "
        "responde 'ambiguous'. Inventar evidência é pior que responder "
        "'ambiguous'.\n"
        "- Se a pesquisa não está disponível, responde 'ambiguous'."
    )
    ans = _ask(prompt, "Curador de Grafo de Conhecimento Cético.")
    parsed = _extract_json(ans or "")

    if not parsed:
        return {
            "decision": "ambiguous",
            "evidence": [],
            "reason": "no parseable answer",
        }
    decision = str(parsed.get("decision", "ambiguous")).lower()
    if decision not in ("relink", "promote"):
        return {
            "decision": "ambiguous",
            "evidence": [],
            "reason": f"decision '{decision}' not actionable",
        }
    try:
        conf = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    evidence = [str(x) for x in (parsed.get("evidence") or []) if str(x).strip()]

    if not results or not evidence:
        # The guard that makes this safe. No research, or a decision with no
        # quoted support, is not a decision.
        return {
            "decision": "ambiguous",
            "evidence": evidence,
            "reason": "no online evidence to justify a write",
        }
    if conf < 0.6:
        return {
            "decision": "ambiguous",
            "evidence": evidence,
            "reason": f"confidence {conf:.2f} below the 0.6 floor",
        }

    target = str(parsed.get("target_label") or "").strip() or label
    if decision == "relink" and normalise(target) == normalise(label):
        return {
            "decision": "ambiguous",
            "evidence": evidence,
            "reason": "relink target is the pending label itself",
        }
    return {
        "decision": decision,
        "target_label": target,
        "evidence": evidence,
        "confidence": conf,
        "reason": str(parsed.get("reason", ""))[:200],
    }


def _token_overlap(a: str, b: str) -> int:
    ta = set(normalise(a).split())
    tb = set(normalise(b).split())
    return len(ta & tb)


# ---------------------------------------------------------------------------
# The step the cycle calls
# ---------------------------------------------------------------------------


def reconcile_refs(db_path, dry_run: bool = False) -> dict[str, Any]:
    """Resolve what can be justified, leave the rest, and say which is which.

    Never raises for a single bad ref: one unresolvable endpoint must not stop
    the other endpoints, nor abort the cycle. A hard failure here would leave
    `sleep/status` reporting "failed" for work that is 90% fine.
    """
    report: dict[str, Any] = {
        "pending": 0,
        "relinked": 0,
        "promoted": 0,
        "ambiguous": 0,
        "failed": 0,
        "skipped": 0,
        "details": [],
        "dry_run": dry_run,
    }
    try:
        pending = find_dangling(db_path)
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"could not enumerate pending refs: {exc}"
        return report
    report["pending"] = len(pending)

    for item in pending:
        entry: dict[str, Any] = {
            "edge_key": item["edge_key"],
            "side": item["side"],
            "label": item["label"],
        }
        try:
            verdict = judge_pending(item)
        except Exception as exc:  # noqa: BLE001
            report["failed"] += 1
            entry.update({"outcome": "failed", "reason": f"{type(exc).__name__}: {exc}"})
            report["details"].append(entry)
            continue

        decision = verdict["decision"]
        rationale = (
            f"{verdict.get('reason', '')} | evidence: {' | '.join(verdict.get('evidence', [])[:2])}"
        ).strip()
        entry.update(
            {
                "decision": decision,
                "reason": verdict.get("reason", ""),
                "evidence": verdict.get("evidence", []),
                "confidence": verdict.get("confidence"),
            }
        )

        if decision == "ambiguous":
            report["ambiguous"] += 1
            entry["outcome"] = "ambiguous"
            report["details"].append(entry)
            continue

        if dry_run:
            entry["outcome"] = f"would {decision}"
            report["details"].append(entry)
            continue

        try:
            out = resolve_dangling(
                db_path,
                item["edge_key"],
                item["side"],
                decision,
                target_label=verdict.get("target_label"),
                actor=ACTOR,
                rationale=rationale[:500],
            )
        except EditError as exc:
            # Includes "already this endpoint": a human resolved it while the
            # cycle was deciding. Not a failure.
            report["skipped"] += 1
            entry.update({"outcome": "skipped", "reason": str(exc)})
            report["details"].append(entry)
            continue
        except Exception as exc:  # noqa: BLE001
            report["failed"] += 1
            entry.update({"outcome": "failed", "reason": f"{type(exc).__name__}: {exc}"})
            report["details"].append(entry)
            continue

        if decision == "relink":
            report["relinked"] += 1
        else:
            report["promoted"] += 1
        entry["outcome"] = decision
        entry["result"] = out
        report["details"].append(entry)

    report["resolved"] = report["relinked"] + report["promoted"]
    return report
