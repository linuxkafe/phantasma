#!/usr/bin/env python3
# `python3` is NOT resolved to the venv. This runs from cron, where PATH is
# whatever cron was given -- `/usr/bin/env python3` lands on `/usr/bin/python3`,
# which has no `ollama` installed, and the `import` fails inside the check's
# `try`, where it reported "inalcançavel: AttributeError" for every host. The
# first version looked like a network problem on all three dependencies.
#
# So: use the venv interpreter explicitly when there is one.

"""Does every host the assistant depends on actually answer?

`/api/health` is not this. It reports `"ollama": "healthy"` after a successful
`list()` against the PRIMARY host, and says nothing about the fallback or about
the search. Measured 2026-10-03: the local `qwen3:8b` -- the fallback, the thing
that answers when the primary is down -- timed out after 240s, while
`/api/health` reported `ollama: healthy` and `llm: healthy` throughout, and the
`deploy.sh` probe saw 200 on every one of its sixty polls.

So the box was green, the deploys were green, and the assistant had one working
brain out of two. Nobody noticed until an answer came back wrong, and the wrong
answer could have come from the broken half.

That makes this a health check rather than a monitor: each host gets a real
generation request with the timeout it would actually face, because a host that
answers `/api/tags` in 20ms and then times out mid-generation is fine by every
existing check and useless in practice.

Exit 0 when every dependency answers, 1 otherwise, and it names the one that
did not. Exit 2 for "cannot tell" -- a missing config, an unreachable docker --
which is deliberately NOT the same as "healthy", because the failure mode being
guarded against is a check that cannot run and reports nothing.

Usage:
    scripts/dependency_check.py            # human output
    scripts/dependency_check.py --json     # for a monitor
    scripts/dependency_check.py --quick    # skip generation, connectivity only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# A generation, not a ping. This is the whole point: the failure that motivated
# this file passed every reachability check in the repository.
PROBE_PROMPT = "di: ok"
PROBE_TIMEOUT_S = 45.0
CONNECT_TIMEOUT_S = 5.0

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _import_config():
    sys.path.insert(0, ROOT)
    import config  # noqa: PLC0415

    inner = getattr(config, "config", None)
    return inner if inner is not None else config


def _pairs(cfg):
    """(name, host, model) for every LLM target, primary first.

    Duplicates are dropped but ORDER IS NOT: the failover chain has a priority,
    and a check that reports them as a set cannot say which one is the safety
    net.
    """
    llm = getattr(cfg, "llm", None)
    out = []
    if llm is not None:
        out.append(("primary", getattr(llm, "host", None), getattr(llm, "model", None)))
        out.append(
            (
                "fallback",
                getattr(llm, "host_fallback", None),
                getattr(llm, "model_fallback", None),
            )
        )
    if not any(h for _, h, _ in out):
        out.append(
            (
                "primary",
                getattr(cfg, "OLLAMA_HOST_PRIMARY", None),
                getattr(cfg, "OLLAMA_MODEL_PRIMARY", "llama3.1:8b"),
            )
        )
        out.append(
            (
                "fallback",
                getattr(cfg, "OLLAMA_HOST_FALLBACK", None),
                getattr(cfg, "OLLAMA_MODEL_FALLBACK", "llama3"),
            )
        )
    seen, uniq = set(), []
    for name, host, model in out:
        if host and host not in seen:
            seen.add(host)
            uniq.append((name, host, model))
    return uniq


def check_llm(host: str, model: str, quick: bool) -> dict:
    res = {"host": host, "model": model, "ok": False, "detail": ""}
    started = time.monotonic()
    try:
        import ollama
    except ImportError as exc:
        res["detail"] = f"ollama SDK ausente: {exc}"
        return res
    try:
        client = ollama.Client(host=host, timeout=CONNECT_TIMEOUT_S)
        client.list()
    except Exception as exc:  # noqa: BLE001
        res["detail"] = f"inalcançavel: {type(exc).__name__}"
        return res
    if quick:
        res["ok"] = True
        res["detail"] = "responde (--quick: sem geração)"
        res["seconds"] = round(time.monotonic() - started, 2)
        return res
    try:
        client = ollama.Client(host=host, timeout=PROBE_TIMEOUT_S)
        resp = client.chat(
            model=model,
            messages=[{"role": "user", "content": PROBE_PROMPT}],
            options={"num_predict": 8},
        )
        text = (resp.get("message", {}) or {}).get("content", "")
        res["ok"] = bool(text.strip())
        res["detail"] = "respondeu" if res["ok"] else "resposta vazia"
    except Exception as exc:  # noqa: BLE001
        res["detail"] = f"{type(exc).__name__} apos {PROBE_TIMEOUT_S:.0f}s"
    res["seconds"] = round(time.monotonic() - started, 2)
    return res


def check_searxng(url: str) -> dict:
    """One real search, because a reachable SearXNG that answers nothing is the
    state this project hit on 2026-09-28: every engine refused, the search
    returned empty, and the assistant answered as if it had consulted the web.
    """
    res = {"url": url, "ok": False, "detail": ""}
    started = time.monotonic()
    try:
        sys.path.insert(0, ROOT)
        from tools import search_with_searxng  # noqa: PLC0415

        out = search_with_searxng("teste de dependencias")
        res["ok"] = bool(out and str(out).strip())
        res["detail"] = "respondeu com resultados" if res["ok"] else "vazio (pesquisa sem resultados)"
    except Exception as exc:  # noqa: BLE001
        res["detail"] = f"{type(exc).__name__}: {str(exc)[:60]}"
    res["seconds"] = round(time.monotonic() - started, 2)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quick", action="store_true", help="connectivity only, no generation")
    args = ap.parse_args()

    try:
        cfg = _import_config()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot read config: {exc}", file=sys.stderr)
        return 2

    targets = _pairs(cfg)
    if not targets:
        print("no LLM hosts configured", file=sys.stderr)
        return 2

    results = {
        "llm": [dict(name=n, **check_llm(h, m, args.quick)) for n, h, m in targets],
        "searxng": None,
    }
    # `config` is a namedtuple with no `web` field; `searxng_url` is the
    # documented one (config.py:347) and `SEARXNG_URL` is the module export that
    # reads it back off the instance (config.py:843). Probing the wrong one is
    # how a check ends up silently skipping SearXNG -- reported as `"searxng":
    # null`, which reads as "not configured" rather than "I looked in the wrong
    # place".
    web = getattr(cfg, "web", None)
    url = (
        getattr(cfg, "SEARXNG_URL", None)
        or getattr(web, "searxng_url", None)
        or os.getenv("SEARXNG_URL")
    )
    if url:
        results["searxng"] = check_searxng(url)
    else:
        # Not silent. A search dependency that cannot be located is a gap in the
        # check, and the caller is told it is a gap rather than a pass.
        results["searxng"] = {
            "url": None, "ok": False,
            "detail": "SEARXNG_URL nao encontrado na config",
        }

    # Computed before the JSON return, not after: a monitor reading `--json`
    # must get the same verdict as a human reading the table, and computing it
    # afterwards would mean the machine-readable output reports `ok: true` for a
    # broken dependency.
    failures = [r for r in results["llm"] if not r["ok"]]
    if results["searxng"] and not results["searxng"]["ok"]:
        failures.append(results["searxng"])

    if args.json:
        print(json.dumps({"ok": not failures, "results": results}, indent=2))
        return 1 if failures else 0

    print("LLM hosts")
    for r in results["llm"]:
        mark = "ok  " if r["ok"] else "FALHA"
        print(f"  {mark} {r['name']:9} {r['host']}  {r['model']}")
        print(f"        {r['detail']}  ({r.get('seconds', '?')}s)")
    if results["searxng"]:
        r = results["searxng"]
        mark = "ok  " if r["ok"] else "FALHA"
        print(f"  {mark} searxng   {r['url']}")
        print(f"        {r['detail']}  ({r.get('seconds', '?')}s)")

    if failures:
        print()
        for r in failures:
            where = r.get("host") or r.get("url")
            print(f"  FALHA: {where} -- {r['detail']}", file=sys.stderr)
        print(
            f"\n{len(failures)} dependencia(s) em falha. /api/healthContinua a dizer "
            "\"ollama: healthy\": so prova o host primario, e nao prova geracao.",
            file=sys.stderr,
        )
        return 1
    print("\ntodas as dependencias respondem")
    return 0


if __name__ == "__main__":
    sys.exit(main())
