#!/usr/bin/env bash
# Deploy pHantasma: DEV -> PROD.
#
# Direction is one-way and enforced here. The dev repo
# (/home/seyon/dev/pHantasma) is the source of truth; /opt/phantasma is the
# deployed copy. This script exists because the direction was previously done
# by hand and got inverted: work started in prod and was copied back to dev,
# which quietly made prod the source. A script makes the direction explicit
# and repeatable.
#
# Usage:
#   scripts/deploy.sh --dry-run            show what would change, touch nothing
#   scripts/deploy.sh [DIR...]             sync the given trees (default: all)
#   scripts/deploy.sh --force-host         ALSO sync the host-specific files
#   scripts/deploy.sh src tests            scoped deploy, e.g. while skills/
#                                          is still under review
#
# NEVER synced, and must stay that way:
#   .env          prod credentials, prod DB paths, and every host value that
#                 used to be hardcoded in config.py (block_size, auto_detect,
#                 device_in). This is the ONLY place prod-specific values live.
#   data/         brain.db, memory.db, config.db -- live state
#   venv/ cache/ .coverage coverage.xml *.pid  build artefacts
#
# config.py and audio_utils.py are NOT in SYNC_DIRS, but they are also NOT
# host-specific any more: both are byte-identical across the trees and a
# divergence is treated as a bug, not a host difference.
#
# assistant.py IS synced below (TOP_LEVEL_SYNC) because it is the service entry
# point. It was previously neither synced nor excluded, which is incoherent: the
# tripwire demanded it stay byte-identical while nothing ever copied it, so a
# change to it in dev reached prod only by hand -- and a hand copy is how a
# stale/edited prod file ends up diverging unnoticed. config.py grew
# env-overridable knobs precisely so that the prod tuning (block_size=512,
# auto_detect=False, device_in=0) could move into .env and stop forking this
# file. The loop below trips a WARN if any of them diverges -- see also the
# .env.example entries and `make deploy-verify-env`.
#
# skills/ IS in the default set and all 26 modules are byte-identical. The
# former skill_weather / skill_tuya exclusions are gone: those two were
# production-authoritative, so they were backported INTO dev rather than
# overwritten from dev, and SKILL_REVIEW is empty by design. The rationale is
# recorded in docs/ROADMAP.md -- do not re-add a skill to an exclusion list to
# paper over a divergence; find out which side is being validated, first.


set -euo pipefail

DEV="/home/seyon/dev/pHantasma"
PROD="/opt/phantasma"

# What is code. tests/ is included on purpose: prod can only verify a deploy
# if it can run the same suite dev runs, and prod was missing 16 of the 27
# test modules, including tests/conftest.py.
SYNC_DIRS=(src tests skills prompts public)

# Top-level files that ARE part of the product and must therefore be deployed.
# assistant.py is the service entry point: not syncing it meant a change reached
# production only by hand, which is how it silently diverged.
  TOP_LEVEL_SYNC=(assistant.py)

  # pyproject.toml is not only packaging: it is the pytest configuration prod
  # runs the suite with. Without it in prod, `pytest tests/` found no
  # [tool.pytest.ini_options], so `pythonpath = ["src"]` and testpaths were
  # absent and tests that do `from tests.helpers_ui_auth import ...` failed
  # there while passing in dev -- 15 failures that made the deploy look broken
  # for a reason that had nothing to do with the change under test. The
  # dependency list in the same file is what the prod-venv import gate compares
  # against, so prod needs it for the gate to mean anything either.
  TOP_LEVEL_SYNC=(assistant.py pyproject.toml)

# SKILL_REVIEW was populated on 2026-09-27 with skill_tuya.py and
# skill_weather.py: prod held behaviour dev lacked (a weather cache, and a
# sensor-datapoint map instead of BASE_NOUNS resolution), so a deploy would
# have deleted it. The owner is validating the PRODUCTION behaviour of both,
# so prod was authoritative and those two were backported into dev instead.
# All 26 skills are now byte-identical, so the exclusion is empty by design.
#
# Do not re-add files here to "protect" them from dev without checking which
# side the owner is validating -- an exclusion that outlives its reason makes
# a deploy silently skip a real change.
SKILL_REVIEW=()

# public/ was missing from the sync set until 2026-09-27 (T046), which meant
# every edit to the 3D explorer -- memory_3d.html and static/explorer.mjs --
# was written in dev and silently never shipped. The deploy reported "already
# in sync" because it was only ever comparing the directories it knew about.
#
# public/ cannot simply join the set: it holds vendored third-party assets and
# two prod-only modules that dev does not have, and --delete would remove them
# and break the explorer outright. They are listed here so the behaviour is a
# real exclusion rather than a comment, mirroring SKILL_REVIEW above.
#
# As with SKILL_REVIEW: do not add to this list to paper over a divergence.
# vendor/ is a provisioned dependency (three.js, OrbitControls, mermaid) and
# mermaid_graph*.mjs is prod-only behaviour still under validation.
PUBLIC_REVIEW=(vendor static/mermaid_graph.mjs static/mermaid_graph.test.mjs)

DRY_RUN=0
FORCE_HOST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)    DRY_RUN=1; shift ;;
    --force-host) FORCE_HOST=1; shift ;;
    -h|--help)    sed -n '2,32p' "$0"; exit 0 ;;
    -*)           echo "unknown flag: $1" >&2; exit 2 ;;
    *)            SYNC_DIRS+=("$1"); shift ;;
  esac
done

[ -d "$DEV" ] || { echo "FATAL: dev tree missing at $DEV" >&2; exit 1; }
[ -d "$PROD" ] || { echo "FATAL: prod tree missing at $PROD" >&2; exit 1; }

# Prod used to symlink src/ at the dev tree, which meant prod executed dev
# code with no deploy step. Refuse to run while that is still true rather than
# silently syncing into a symlink.
if [ -L "$PROD/src" ]; then
  echo "FATAL: $PROD/src is a symlink ($(readlink "$PROD/src"))." >&2
  echo "       prod would execute dev code directly and this deploy would be a no-op." >&2
  echo "       Replace it with a real copy first: see docs/CLAUDE.md." >&2
  exit 1
fi

changed=0
for d in "${SYNC_DIRS[@]}"; do
  [ -d "$DEV/$d" ] || { echo "skip $d (absent in dev)"; continue; }

  # SKILL_REVIEW protects the two modules where prod holds behaviour dev does
  # not. Putting them in the exclude list is what makes that protection real
  # rather than a comment -- and --delete below would otherwise remove them.
  RSYNC_EXC=(--exclude=__pycache__ --exclude='*.pyc')
  DIFF_EXC=(--exclude=__pycache__ --exclude='*.pyc')
  if [ "$d" = "public" ]; then
    for s in "${PUBLIC_REVIEW[@]}"; do
      RSYNC_EXC+=(--exclude="$s")
      # rsync matches --exclude against the path relative to the transfer
      # root, so "static/mermaid_graph.mjs" is right there. GNU diff matches
      # --exclude against the BASENAME only, so the same pattern silently
      # matches nothing and the dry-run kept reporting protected prod-only
      # assets as differences -- a false "sync public" that sends whoever is
      # deploying to investigate a problem the exclusion had already solved.
      # The basename form is what makes the report tell the truth.
      DIFF_EXC+=(--exclude="$(basename "$s")" --exclude="$s")
      [ -e "$PROD/public/$s" ] && \
        echo "  keep  public/$s  (prod-only asset, protected from --delete)"
    done
  fi
  if [ "$d" = "skills" ]; then
    for s in "${SKILL_REVIEW[@]}"; do
      RSYNC_EXC+=(--exclude="$s"); DIFF_EXC+=(--exclude="$s")
      [ -f "$PROD/skills/$s" ] && \
        echo "  keep  skills/$s  (prod-only behaviour, needs a real merge -- docs/ROADMAP.md)"
    done
  fi

  # --delete makes prod match dev exactly. Without it, files deleted in dev
  # would linger in prod forever, which is how trees drift.
  if diff -rq "${DIFF_EXC[@]}" "$DEV/$d" "$PROD/$d" >/dev/null 2>&1; then
    echo "  ok    $d (in sync)"
    continue
  fi
  echo "  sync  $d"

  if [ "$DRY_RUN" -eq 1 ]; then
    # `diff -rq` exits 1 when the trees DIFFER, which is the normal case here
    # and not an error. Under `set -euo pipefail` that non-zero status aborted
    # the whole script on the first out-of-sync directory: --dry-run died on
    # `src`, never reached tests/skills/prompts/public, never ran the gates,
    # and exited 1 as though the tree were fine. It reported failure while
    # having looked at almost nothing, which is how the prod-only files that
    # `--delete` would have removed stayed invisible.
    #
    # The count is printed as well as the list because `head -40` truncated
    # silently, so a directory with 60 differences reported the first 40 and
    # said nothing about the rest.
    _diffs=$(diff -rq "${DIFF_EXC[@]}" "$DEV/$d" "$PROD/$d" 2>&1 || true)
    _n=$(printf '%s\n' "$_diffs" | grep -c . || true)
    if [ "$_n" -gt 0 ]; then
      printf '%s\n' "$_diffs" | sed 's/^/          /' | head -40
      [ "$_n" -gt 40 ] && echo "          ... and $((_n - 40)) more"
    fi
    unset _diffs _n
  else
    # -rlt, NOT -a. -a implies -o -g, and this script may run as a user that
    # cannot chgrp (sudo is not granted for chown here): rsync aborted with
    # code 23 "chgrp failed: Operation not permitted" AFTER the file data was
    # already transferred, which under set -e skipped the verification and
    # restart steps. --no-owner/--no-group keep it honest about what it can
    # actually do, so a permission problem fails loudly at the rsync call
    # instead of silently skipping the gates.
    rsync -rlt --no-owner --no-group --no-times --delete "${RSYNC_EXC[@]}" "$DEV/$d/" "$PROD/$d/"
  fi
  changed=1
done

# Top-level files that are part of the product and must be deployed.
for f in "${TOP_LEVEL_SYNC[@]}"; do
  if cmp -s "$DEV/$f" "$PROD/$f"; then echo "  ok    $f (in sync)"; continue; fi
  echo "  sync  $f (service entry point)"
  changed=1
  if [ "$DRY_RUN" -eq 0 ]; then
    # Write-then-rename so a failure cannot leave a half-written entry point
    # in production; assistant.py is what the service executes on start.
    cp "$DEV/$f" "$PROD/$f.tmp-deploy" && mv "$PROD/$f.tmp-deploy" "$PROD/$f"
  fi
done

  # Declared-dependency gate.
  #
  # This script copies code and then runs the test suite. It never checked that
  # the code can actually run in the prod venv, so a package the code imports
  # but nobody declared shipped cleanly and failed only at runtime, in the one
  # place it matters. That happened twice with faster-whisper: the swap from
  # openai-whisper was correct in dev, "openai-whisper" stayed in the
  # dependency list, "faster-whisper" was never added, the deploy passed, and
  # /opt/phantasma/venv logged "No module named 'faster_whisper'" on every
  # utterance while dev was fine.
  #
  # The direction that matters is the CODE's, not the manifest's. Declaring a
  # dependency does not install it and does not prove the venv has it, so this
  # walks the actual import statements -- including the ones inside functions,
  # which is the whole point: src/pipeline/stt.py imports faster_whisper lazily
  # inside the loader, so merely importing the entry point would pass and the
  # failure would still be a runtime surprise on the first utterance.
  #
  # Fail-closed, and it does not install: a deploy that silently mutates the
  # production venv is a deploy nobody can reason about afterwards. It reports
  # what is missing and stops, so the fix is deliberate.
  if ( cd "$PROD" && ./venv/bin/python3 - <<'PY'
import ast
import pathlib
import sys

# Scope is explicit, and it is the code this service runs. An earlier version of
# this gate used rglob(".") and swept up the CPython test suite that ships inside
# the venv's own site-packages -- test_grp, pgen2, urllib2, org, java -- and
# failed on all of them in a perfectly healthy environment. A gate that fails
# when everything is fine is worse than no gate: it teaches the reader to ignore
# it. See SD-phantasma-OPS-025.
LOCAL = {"assistant", "config", "skills", "src", "main", "aes", "setup"}
SKIP_DIR_PARTS = {"tests", "test", "venv", "build", ".git", "node_modules", "vendor"}

files = []
for pattern_dir in ("src", "skills"):
    base = pathlib.Path(pattern_dir)
    if base.is_dir():
        files.extend(p for p in base.rglob("*.py")
                     if not (SKIP_DIR_PARTS & set(p.parts)))
# Top-level modules are the service entry points (assistant.py, config.py).
files.extend(p for p in pathlib.Path(".").glob("*.py")
             if p.name not in ("setup.py", "conftest.py"))

roots = set()
for path in sorted(set(files)):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        continue
    for node in ast.walk(tree):
        # Both forms, at any nesting depth: `import x`, `from x import y`, and
        # the deferred ones inside a function body. The deferred ones are the
        # point: stt.py imports faster_whisper inside the loader, so importing
        # the entry point would pass and the failure would still only surface
        # on the first utterance.
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: always project-local
                continue
            if node.module:
                roots.add(node.module.split(".")[0])

stdlib = set(getattr(sys, "stdlib_module_names", ()))
candidates = sorted(
    r for r in roots
    if r not in stdlib and r not in LOCAL and not r.startswith("_")
)

missing = []
for mod in candidates:
    try:
        __import__(mod)
    except ImportError as exc:
        missing.append((mod, str(exc)))
    except Exception:
        # Imported far enough to hit runtime: a hardware or network library that
        # fails for another reason is not a packaging fault and must not be
        # reported as one.
        pass

if missing:
    print("  the prod venv cannot import modules the code uses:", file=sys.stderr)
    for mod, err in missing:
        print(f"    {mod}: {err}", file=sys.stderr)
    print(
        "  install them in $PROD/venv, or fix the import. Declared in\n"
        "  pyproject.toml and actually imported are not the same set, and\n"
        "  this gate checks the second one.",
        file=sys.stderr,
    )
    sys.exit(1)
print(f"  ok    {len(candidates)} imported modules resolvable in prod venv "
      f"(scanned {len(set(files))} files)")
PY
  ); then
    :
  else
    echo "  FAILED: prod-venv import check (see above)" >&2
    if [ "$DRY_RUN" -eq 1 ]; then exit 3; else exit 1; fi
  fi

  # Audio env-var verification. config.py fails OPEN for these two: with the
  # .env absent, renamed, unreadable, or containing a name typo, the effective
  # values become block_size=1600 / auto_detect=True with no error anywhere.
  # That regresses the wake-word framing, and prod's .env is gitignored so a
  # reviewer cannot see it either. Assert the validated prod values explicitly.
  if [ -f "$PROD/.env" ]; then
    if ( cd "$PROD" && ./venv/bin/python3 - <<'PY'
import os, sys
from dotenv import load_dotenv
load_dotenv()
import config
a = config.config.audio
bad = []
if a.block_size != 512:
    bad.append(f"block_size={a.block_size} (expected 512; AUDIO_BLOCK_SIZE in .env)")
if a.auto_detect is not False:
    bad.append(f"auto_detect={a.auto_detect} (expected False; AUDIO_DEVICE_AUTO_DETECT in .env)")
if bad:
    print("  audio config did not load from .env:", file=sys.stderr)
    for b in bad:
        print(f"    {b}", file=sys.stderr)
    sys.exit(1)
print("  ok    audio env (block_size=512 auto_detect=False)")
PY
    ); then
      :
    else
      echo "  FAILED: audio .env check (see above)" >&2
      if [ "$DRY_RUN" -eq 1 ]; then exit 3; else exit 1; fi
    fi
  else
    echo "  FAILED: $PROD/.env is missing -- audio tuning cannot be verified" >&2
    if [ "$DRY_RUN" -eq 1 ]; then exit 3; else exit 1; fi
  fi

  if [ "$FORCE_HOST" -eq 1 ]; then
  for f in config.py audio_utils.py; do
    if cmp -s "$DEV/$f" "$PROD/$f"; then echo "  ok    $f"; continue; fi
    echo "  sync  $f (--force-host: HOST-SPECIFIC, verify before shipping)"
    [ "$DRY_RUN" -eq 1 ] || cp "$DEV/$f" "$PROD/$f"
    changed=1
  done
else
  # config.py and audio_utils.py were forked for years because they held
  # host-specific values as dataclass defaults -- config.py pinned
  # block_size=512 and auto_detect=False for the Jabra, which no env var could
  # override. Fixed 2026-09-27: AUDIO_BLOCK_SIZE and AUDIO_DEVICE_AUTO_DETECT
  # are now read from .env, and audio_utils.py uses a config-driven
  # TTS_CACHE_DIR. Both files are byte-identical across the trees, so this
  # warning is a tripwire: if it fires, a host value leaked back into code and
  # belongs in .env instead. Use --force-host to override deliberately.
  # config.py, audio_utils.py and assistant.py must stay byte-identical. A
  # divergence is a BUG, not a host difference: host values live in .env. This
  # is a hard gate, not a warning -- it runs BEFORE the rsync and exits non-zero,
  # because a WARN that lets the deploy continue and print "deploy OK" is exactly
  # the yes-man failure AES forbids. Use --force-host only with a stated reason.
  for f in config.py audio_utils.py; do
    cmp -s "$DEV/$f" "$PROD/$f" && continue
    cat >&2 <<EOF
  FAILED: $f diverges between dev and prod.
  These three are supposed to be byte-identical; host tuning belongs in .env.
  If prod is right, fix dev first, then redeploy. If dev is right, re-run with
  --force-host and record why in docs/ROADMAP.md.
EOF
    if [ "$DRY_RUN" -eq 1 ]; then exit 3; else exit 1; fi
  done
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo
  echo "DRY RUN -- nothing was written. Re-run without --dry-run to apply."
  exit 0
fi

# `changed` only knows whether THIS run wrote anything. A previous run that
# aborted after rsync had already copied the data (observed 2026-09-27: rsync
# exited 23 on chgrp after transferring) leaves the tree in sync but the
# service still running the old code in memory -- and this script would have
# reported "nothing to restart" and exited 0. So compare the service start
# time against the newest code mtime and restart whenever the code is newer,
# regardless of who wrote it.
# Epoch form, because the comparison below is numeric.
#
# Uses the human-readable ActiveEnterTimestamp, NOT ActiveEnterTimestampMonotonic.
# The monotonic value is in MICROSECONDS from boot, and mixing that unit with a
# wall-clock epoch silently produced a number ~1000x too large, so the
# comparison always concluded the service was newer than the code and never
# restarted. `date -d` handles the human-readable form and its timezone.
service_started_epoch=$(systemctl show phantasma -p ActiveEnterTimestamp --value 2>/dev/null \
  | xargs -r -I{} date -d "{}" +%s 2>/dev/null || echo "")
if [ "$changed" -eq 1 ]; then
  reason="this run wrote files"
else
  # Compare the newest deployed source against the SERVICE START TIME, not
  # against a sibling file. The previous heuristic compared against
  # $PROD/assistant.py, which is not a reference point for anything: after a
  # deploy that changes only src/ or skills/, assistant.py keeps its old mtime
  # and the script concluded "nothing to restart" while the running service
  # still had the old code loaded. Verified: waitress/serve.py shipped and the
  # service kept answering with Server: Werkzeug.
  reason=""
  newest=$(find "$PROD/src" "$PROD/skills" "$PROD/prompts" -type f -printf '%T@ %p\n' 2>/dev/null \
           | sort -rn | head -1 | cut -d' ' -f2-)
  newest_mtime=0
  [ -n "$newest" ] && newest_mtime=$(stat -c %Y "$newest" 2>/dev/null || echo 0)
  if [ -z "${service_started_epoch:-}" ]; then
    # No reliable start time: restarting is the safe direction. A needless
    # restart costs seconds; a skipped one leaves stale code live.
    reason="service start time unreadable; restarting to be safe"
  elif [ "${newest_mtime:-0}" -gt "${service_started_epoch:-0}" ]; then
    reason="newest file ($newest) is newer than the service start"
  fi
fi

if [ "$changed" -eq 0 ] && [ -z "$reason" ]; then
  echo
  echo "already in sync and service is newer than the code; nothing to restart."
  exit 0
fi

echo
echo "verifying prod suite ($reason)..."
( cd "$PROD" && ./venv/bin/python3 -m pytest tests/ -q ) || {
  echo; echo "DEPLOY FAILED: prod tests did not pass. Prod has been overwritten" >&2
  echo "and the old code is not restored. Roll back from git, or re-run" >&2
  echo "this script after fixing dev." >&2
  exit 1
}

  echo "restarting phantasma.service..."
  sudo -n service phantasma restart
  # Probe the functional endpoint, not the listening socket. A cold start loads
  # models and has been observed to exceed 150s, during which the socket is
  # absent and the previous `ss`-based check reported a false failure on a
  # service that came up fine moments later.
  #
  # Probe /api/health, NOT "/". The voice UI's root page is behind a session and
  # answers 302 to /login for an unauthenticated caller -- correctly, that is
  # the product working. Demanding a 200 from it made this check unsatisfiable
  # the day the login was added, and the symptom was a deploy that reported
  # "no HTTP 200 on 5000 after 300s" on a service that was up, healthy and
  # listening the whole time. A health check that cannot pass is not a health
  # check. See SD-phantasma-OPS-027.
  #
  # /api/health is the right probe because it is public by design and it
  # reports the components individually -- "stt":"healthy" is the difference
  # between "the process is up" and "the thing the owner complained about is
  # up".
  ok=0
  for _ in $(seq 1 60); do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:5000/api/health 2>/dev/null)" = "200" ]; then
      ok=1; break
    fi
    sleep 5
  done
  # Second assertion: the protected root must redirect to the login, not serve
  # the device page to an anonymous caller. A 200 here would mean the session
  # gate had been lost, which is a worse failure than the service being down and
  # exactly the kind of thing a "is it up?" probe is not looking for.
  root_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:5000/ 2>/dev/null)"
  if [ "$root_code" != "302" ]; then
    echo "  FAILED: / answered $root_code to an unauthenticated request; expected 302 to the login" >&2
    ok=0
  fi
  if [ "$ok" -eq 1 ]; then
    echo "deploy OK -- /api/health 200, / redirects anonymous callers to the login"
    # Surface the component report: a deploy that leaves stt unhealthy has
    # still broken the product, and the owner should not have to go read the
    # journal to find out.
    echo -n "  components: "
    curl -s --max-time 5 http://127.0.0.1:5000/api/health 2>/dev/null \
      | sed -n 's/.*"components":{\([^}]*\)}.*/\1/p' || true
  else
  echo "DEPLOY FAILED: no HTTP 200 on 5000 after 300s" >&2
  systemctl is-active phantasma >&2 2>/dev/null || true
  ss -tln 2>/dev/null | grep ':5000' >&2 || echo "  (nothing listening on 5000)" >&2
  exit 1
fi
