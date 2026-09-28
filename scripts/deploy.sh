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
      RSYNC_EXC+=(--exclude="$s"); DIFF_EXC+=(--exclude="$s")
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
    diff -rq "${DIFF_EXC[@]}" "$DEV/$d" "$PROD/$d" 2>&1 \
      | sed 's/^/          /' | head -40
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
ok=0
for _ in $(seq 1 60); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:5000/ 2>/dev/null)" = "200" ]; then
    ok=1; break
  fi
  sleep 5
done
if [ "$ok" -eq 1 ]; then
  echo "deploy OK -- service answering 200 on 5000"
else
  echo "DEPLOY FAILED: no HTTP 200 on 5000 after 300s" >&2
  systemctl is-active phantasma >&2 2>/dev/null || true
  ss -tln 2>/dev/null | grep ':5000' >&2 || echo "  (nothing listening on 5000)" >&2
  exit 1
fi
