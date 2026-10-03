#!/usr/bin/env bash
# The nightly run: check, update images, update models, check again.
#
# Scheduled by crontab at 03:30, on purpose and not by accident:
#
#   30 3 * * * /opt/phantasma/scripts/nightly_update.sh
#
# 03:30 is inside the quiet period and before the household wakes. The assistant
# restarts as part of a container update, so doing it at 03:30 means nobody is
# mid-conversation when the containers bounce.
#
# Silent means no alert is SENT. It does not mean nothing is recorded. Everything
# lands in $LOG, and the last outcome is left in $STATUS, because an update job
# with no record is indistinguishable from an update job that never ran -- and
# both look the same at 08:00 when the assistant is answering oddly.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || exit 2

LOG="$ROOT/data/nightly-update.log"
STATUS="$ROOT/data/nightly-update.status"

# A generation can legitimately take minutes on a cold model, and a 5GB pull
# takes much longer. The defaults are deliberately generous: this job runs
# unattended, so a timeout that fires while the work is still progressing turns
# a slow update into a rollback.
export MODEL_PULL_TIMEOUT="${MODEL_PULL_TIMEOUT:-3600}"
export MODEL_VERIFY_TIMEOUT="${MODEL_VERIFY_TIMEOUT:-180}"
export VERIFY_TIMEOUT_S="${VERIFY_TIMEOUT_S:-180}"

# Cron gives a minimal PATH and the shebang resolves `python3` to
# `/usr/bin/python3`, which has no `ollama`. So the interpreter is chosen HERE and
# passed explicitly -- the scripts are invoked as `$PY script.py`, never as
# `./script.py`. Running the shebang from cron reported all three dependencies as
# "inalcançavel" for a whole deployment before this was found.
if [ -x "$ROOT/venv/bin/python" ]; then
  PY="$ROOT/venv/bin/python"
elif [ -x /opt/phantasma/venv/bin/python ]; then
  PY=/opt/phantasma/venv/bin/python
else
  echo "NIGHTLY UPDATE ABORTED: no venv python with the ollama SDK" >&2
  exit 2
fi
export PATH="$(dirname "$PY"):$PATH"

say()  { printf '%s %s\n' "$(date -Is)" "$*" >>"$LOG"; }
note() { printf '%s\n' "$*" >>"$LOG"; }

mkdir -p "$ROOT/data"

# Keep the log from growing without bound. Ten runs is three months of nights,
# which is long enough to see a pattern and short enough to read.
find "$LOG" -mtime +90 -delete 2>/dev/null || true

note "=============================================================="
say "nightly update starting (python: $PY)"

# --------------------------------------------------------------------------
# 1. BEFORE. If something is already broken, updating on top of it makes the
#    rollback ambiguous: if the run fails you cannot tell whether the update
#    caused it or the pre-existing fault did. Recorded and continued, because
#    the alternative is that one bad night stops the house from ever updating
#    again.
# --------------------------------------------------------------------------
say "pre-check:"
BEFORE="$("$PY" "$SCRIPT_DIR/dependency_check.py" 2>&1)"
pre_rc=$?
echo "$BEFORE" >>"$LOG"
if [ $pre_rc -ne 0 ]; then
  say "pre-check FAILED (exit $pre_rc) -- a dependencia ja estava avariada antes de tocar em nada"
  PRE_BROKEN=1
else
  say "pre-check ok"
  PRE_BROKEN=0
fi

# --------------------------------------------------------------------------
# 2. Images, then models. Images first because a model pull is the long leg and
#    the container that serves it should already be the new one.
# --------------------------------------------------------------------------
say "updating images:"
"$SCRIPT_DIR/update_containers.sh" >>"$LOG" 2>&1
img_rc=$?
say "images exit=$img_rc"

say "updating models:"
"$PY" "$SCRIPT_DIR/update_models.py" >>"$LOG" 2>&1
mod_rc=$?
say "models exit=$mod_rc"

# --------------------------------------------------------------------------
# 3. AFTER. The only verdict that counts. Anything that changed must now be
#    proven working, or rolled back inside update_containers.sh.
# --------------------------------------------------------------------------
say "post-check:"
AFTER="$("$PY" "$SCRIPT_DIR/dependency_check.py" 2>&1)"
post_rc=$?
echo "$AFTER" >>"$LOG"

# --------------------------------------------------------------------------
# 4. A visible marker, because there are no alerts. The one case that needs a
#    human -- a rollback that did not restore the service -- writes a file the
#    admin page shows. Silence is fine for the normal path; it is not fine for
#    the path where the house is broken and nobody was told.
# --------------------------------------------------------------------------
if [ $post_rc -ne 0 ] && [ $PRE_BROKEN -eq 0 ]; then
  {
    echo "NIGHTLY UPDATE LEFT SOMETHING BROKEN"
    echo "when: $(date -Is)"
    echo "images exit: $img_rc   models exit: $mod_rc"
    echo
    echo "$AFTER"
  } >"$STATUS"
  say "RESULT: BROKEN AFTER AN UPDATE THAT STARTED HEALTHY -- wrote $STATUS"
elif [ $post_rc -ne 0 ]; then
  say "RESULT: still broken, but it was already broken before the run"
  printf 'NIGHTLY UPDATE: dependency broken (pre-existing)\nwhen: %s\n' "$(date -Is)" >"$STATUS"
else
  say "RESULT: all dependencies healthy"
  printf 'NIGHTLY UPDATE: ok\nwhen: %s\n' "$(date -Is)" >"$STATUS"
fi

say "nightly update finished"
exit 0