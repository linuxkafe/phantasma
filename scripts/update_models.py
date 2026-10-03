#!/usr/bin/env python3
"""Pull a newer digest for every installed model, on every reachable Ollama host.

The owner asked for this unconditionally, and it runs unconditionally. What this
file is for is making that safe rather than arguing with the decision.

Why it needed care, recorded once so nobody re-derives it:

`llama3.1:8b` is the model that carries the persona. Instruction-following at a
chosen quantisation is the property the whole character depends on, and a
different build of the same tag can lose it. The symptom is not an error -- it
is a slightly duller answer, which is the worst kind of regression to notice.

So this file does three things that `ollama pull` does not:

1. **Records the digest of every installed model before touching it.** That is
   the only rollback handle that exists. Ollama has no "previous version", but
   the old blob is still in the registry and still pullable by digest, and
   without the digest written down before the pull there is nothing to pull
   back to. A file that does not exist yet is not a rollback.

2. **Pulls every model on every reachable host**, not just the one in use. The
   failover chain has two Ollama hosts (a remote primary at 10.0.0.128 and the
   local container); updating only the primary leaves the fallback on weights
   nobody has looked at, and the fallback is what answers when the primary is
   down -- which is exactly when nobody is watching.

3. **Generates afterwards.** `ollama pull` exits 0 on a download, which says
   nothing about whether the result loads. A model that pulls and then produces
   empty text is a successful pull and a broken assistant.

On failure it re-pulls the recorded digests. That is best-effort and says so.

Usage:
    scripts/update_models.py --check      # list, pull nothing (default action)
    scripts/update_models.py              # pull, verify, roll back on failure
    scripts/update_models.py --host URL   # restrict to one host
    scripts/update_models.py --no-verify  # skip the generation check
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Generous, because these are 4-5GB downloads on a home connection and a
# timeout that kills a half-finished pull helps nobody.
PULL_TIMEOUT_S = float(os.getenv("MODEL_PULL_TIMEOUT", "3600"))
VERIFY_TIMEOUT_S = float(os.getenv("MODEL_VERIFY_TIMEOUT", "120"))
PROBE = "di: ok"

# Written before the first pull. If this file is empty the rollback has no
# handle, so it is opened and flushed before anything is downloaded.
LEDGER = os.path.join(ROOT, "data", "model-digests.json")


def _import_config():
    sys.path.insert(0, ROOT)
    import config  # noqa: PLC0415

    return getattr(config, "config", None) or config


def hosts_from_config(cfg) -> list[tuple[str, str]]:
    """(label, host) for each Ollama host, primary first, deduplicated."""
    out: list[tuple[str, str]] = []
    llm = getattr(cfg, "llm", None)
    if llm is not None:
        for label, attr in (("primary", "host"), ("fallback", "host_fallback")):
            h = getattr(llm, attr, None)
            if h:
                out.append((label, h))
    if not any(h for _, h in out):
        for label, attr in (
            ("primary", "OLLAMA_HOST_PRIMARY"),
            ("fallback", "OLLAMA_HOST_FALLBACK"),
        ):
            h = getattr(cfg, attr, None)
            if h:
                out.append((label, h))
    seen, uniq = set(), []
    for label, host in out:
        if host not in seen:
            seen.add(host)
            uniq.append((label, host))
    return uniq


def installed(client) -> dict[str, str]:
    """{model_name: digest} as this host reports it.

    The digest column is what makes rollback possible. A host that does not
    report one gets an empty string, and the caller treats that as "no handle"
    rather than "nothing to roll back to".
    """
    out: dict[str, str] = {}
    try:
        listed = client.list()
    except Exception as exc:  # noqa: BLE001
        return out
    # `ollama.list()` returns a `ListResponse`, NOT a dict, and `.models` is a
    # list of `Model`. The first version did `isinstance(listed, dict)` and
    # silently got zero models on every host -- which reported itself as
    # "nao foi possivel listar modelos" and looked like an unreachable host
    # rather than a type mistake in the check. Attribute access, then dict, so
    # both SDK shapes work and neither is a silent zero.
    models = getattr(listed, "models", None)
    if models is None and isinstance(listed, dict):
        models = listed.get("models", [])
    for m in models or []:
        name = getattr(m, "model", None) or getattr(m, "name", None)
        if name is None and isinstance(m, dict):
            name = m.get("model") or m.get("name")
        if name:
            dig = getattr(m, "digest", None)
            if dig is None and isinstance(m, dict):
                dig = m.get("digest")
            out[name] = dig or ""
    return out


def digest_of(client, model: str) -> str:
    return installed(client).get(model, "")


def pull(client, model: str) -> tuple[bool, str]:
    started = time.monotonic()
    try:
        # Streaming; the final chunk carries "success".
        last = ""
        for chunk in client.pull(model, stream=True):
            if isinstance(chunk, dict):
                last = chunk.get("status", "") or last
                if chunk.get("error"):
                    return False, str(chunk["error"])[:120]
        return True, f"puxado em {time.monotonic() - started:.0f}s"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def verify(client, model: str) -> tuple[bool, str]:
    try:
        resp = client.chat(
            model=model,
            messages=[{"role": "user", "content": PROBE}],
            options={"num_predict": 8},
        )
        # Same shape issue: `chat` returns a ChatResponse whose `.message` is an
        # object. A `.get()` on it would raise or return nothing, and a verify
        # that cannot read the answer reports the model as broken when it is not.
        message = getattr(resp, "message", None)
        if message is None and isinstance(resp, dict):
            message = resp.get("message") or {}
        text = (
            getattr(message, "content", None)
            if not isinstance(message, dict)
            else message.get("content", "")
        ) or ""
        return (bool(text.strip()), "respondeu" if text.strip() else "resposta vazia")
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}"


def pull_digest(client, model: str, digest: str) -> tuple[bool, str]:
    """Re-pull a specific digest, the only rollback available.

    Ollama accepts `<model>@sha256:...`. If the registry has pruned the old
    manifest this fails, and the honest answer is that the model cannot be
    restored automatically.
    """
    target = f"{model}@{digest}" if digest else None
    if not target:
        return False, "sem digest guardado -- nao ha rollback possivel"
    started = time.monotonic()
    try:
        for _chunk in client.pull(target, stream=True):
            pass
        return True, f"restaurado em {time.monotonic() - started:.0f}s"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:100]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="list only, pull nothing")
    ap.add_argument("--host", help="restrict to one host URL")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()

    try:
        import ollama
    except ImportError as exc:
        print(f"ollama SDK ausente: {exc}", file=sys.stderr)
        return 2
    try:
        cfg = _import_config()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot read config: {exc}", file=sys.stderr)
        return 2

    targets = hosts_from_config(cfg)
    if args.host:
        targets = [(l, h) for l, h in targets if h == args.host] or [("given", args.host)]
    if not targets:
        print("no ollama hosts configured", file=sys.stderr)
        return 2

    ledger: dict = {}
    if os.path.exists(LEDGER):
        try:
            ledger = json.load(open(LEDGER, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            ledger = {}

    overall = 0
    for label, host in targets:
        print(f"\n=== {label} {host} ===")
        client = ollama.Client(host=host, timeout=60)
        models = installed(client)
        if not models:
            print(f"  FALHA: nao foi possivel listar modelos em {host}")
            overall = 1
            continue
        print(f"  {len(models)} modelo(s) instalados")

        for model, old_digest in sorted(models.items()):
            if args.check:
                handle = "digest " + (old_digest[:19] if old_digest else "NAO REPORTADO")
                print(f"    {model:24} {handle}")
                continue

            # The handle first, always, before the download.
            ledger.setdefault(host, {})[model] = old_digest
            os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
            with open(LEDGER, "w", encoding="utf-8") as fh:
                json.dump(ledger, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())

            ok, detail = pull(client, model)
            if not ok:
                print(f"    FALHA {model}: {detail}")
                overall = 1
                continue
            new_digest = digest_of(client, model)
            if new_digest and old_digest and new_digest == old_digest:
                print(f"    igual    {model} (sem digest novo)")
                continue

            print(f"    puxado   {model}  {old_digest[:19]} -> {new_digest[:19]}")

            if args.no_verify:
                continue
            vok, vdetail = verify(client, model)
            if vok:
                print(f"    verificado {model}: {vdetail}")
                continue

            print(f"    FALHA na verificacao de {model}: {vdetail} -- a repor")
            rok, rdetail = pull_digest(client, model, old_digest)
            if rok:
                print(f"    reposto  {model}: {rdetail}")
            else:
                print(f"    NAO REPIDO {model}: {rdetail}")
                print(
                    f"    >> {model} em {host} precisa de intervencao manual. "
                    f"O digest anterior e {old_digest or '(nao reportado)'}.",
                    file=sys.stderr,
                )
            overall = 1

    if args.check:
        print("\n(check: nada foi puxado)")
    else:
        print(f"\nledger de digests: {LEDGER}")
    return overall


if __name__ == "__main__":
    sys.exit(main())