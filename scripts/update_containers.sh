#!/usr/bin/env bash
# Pull newer container images for the services pHantasma depends on, and prove
# they still work before calling it a success.
#
# NOT models. See "Why models are not in here" below.
#
# The shape of this is borrowed from deploy.sh, and for the same reason. A pull
# that ends in "done" is a claim, not a measurement: on 2026-10-03 the local
# `qwen3:8b` timed out after 240s while /api/health reported "ollama: healthy"
# and deploy.sh saw 200 on all sixty of its polls. A box that looks healthy
# while one of its two brains is dead is worse than one that is visibly down,
# because the green light is what stops anyone looking.
#
# So: pull, restart, then ASK THE THING WHETHER IT WORKS -- and roll back to the
# previous digest if it does not. The rollback is the part that makes unattended
# updates defensible; without it, "automated update" means "automated outage, at
# 4am, discovered in the morning".
#
# Usage:
#   scripts/update_containers.sh              # searxng + ollama, with rollback
#   scripts/update_containers.sh --check      # report only, change nothing
#   scripts/update_containers.sh searxng      # just one
#   DRY_RUN=1 scripts/update_containers.sh   # show the plan, pull nothing
#
# Exit 0 on success, 1 on failure after a rollback, 2 on "cannot tell".
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.yml"
SERVICES_DEFAULT=(searxng ollama)

CHECK_ONLY=0
case "${1:-}" in
  --check) CHECK_ONLY=1; shift ;;
esac
# `"${@:-}"` with no arguments yields ONE empty string, not an empty array, so
# the loop below ran once against a service named "" and reported
# "FAILED : (not running)" for an empty name. A default must be a default.
SERVICES=("$@")
if [ ${#SERVICES[@]} -eq 0 ]; then
  SERVICES=("${SERVICES_DEFAULT[@]}")
fi

# A generation, not a ping. A host that answers /api/tags in 20ms and times out
# mid-generation is fine by every reachability check and useless in practice.
VERIFY_TIMEOUT_S="${VERIFY_TIMEOUT_S:-180}"
ROLLBACK_ON_FAILURE="${ROLLBACK_ON_FAILURE:-1}"
LOG_FILE="${LOG_FILE:-$ROOT/data/update-containers.log}"

cd "$ROOT" || exit 2

say()  { printf '%s %s\n' "$(date -Is)" "$*"; }
fail() { printf '%s %s\n' "$(date -Is)" "$*" >&2; }

if [ ! -f "$COMPOSE_FILE" ]; then
  fail "compose file not found: $COMPOSE_FILE"
  exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
  fail "docker compose unavailable"
  exit 2
fi

# ---------------------------------------------------------------------------
# Why models are not in here
#
# `docker-compose.yml` runs `ollama pull phi3:mini` as a one-shot service. A new
# digest on that tag means new weights, and new weights mean the assistant
# answers differently -- with no commit, no diff and nobody deciding it. That is
# a change in behaviour dressed as a maintenance task.
#
# It is also the thing most likely to break silently. `llama3.1:8b` carrying the
# persona depends on instruction-following at a quantisation somebody chose; a
# different build of the same tag can lose it, and the symptom is a slightly
# duller answer rather than an error. An update that can only be noticed by
# reading every response is not one to schedule unattended.
#
# So images are automatic and reversible, models are proposed:
#
#   for m in llama3.1:8b qwen3:8b; do
#     curl -sI -H 'Authorization: Bearer <token>' \
#       "https://registry.ollama.ai/v2/library/${m%%:*}/manifests/${m##*:}" | grep -i digest
#   done
#
# Compare digests weekly; pull by hand when you decide the change is worth it.
# ---------------------------------------------------------------------------

digest_for() {  # service -> locally running image id, or empty
  docker inspect --format '{{.Image}}' "$1" 2>/dev/null
}

remote_digest_of() {  # service -> the digest `pull` would install, or empty
  # `docker compose config --images` resolves the tag; the registry digest needs
  # an authenticated pull, so this is best-effort and never load-bearing.
  local svc="$1" img
  img=$(docker compose -f "$COMPOSE_FILE" config --images 2>/dev/null \
        | grep -E "$(docker inspect --format '{{.Config.Image}}' "$svc" 2>/dev/null | sed 's|.*/||')" \
        | head -1)
  [ -z "$img" ] && return 1
  docker manifest inspect "$img" --verbose 2>/dev/null \
    | grep -m1 '"digest"' | cut -d'"' -f4
}

verify() {  # service -> 0 if the dependency actually answers
  say "verifying with a real generation request (up to ${VERIFY_TIMEOUT_S}s)..."
  if [ -x "$ROOT/venv/bin/python" ]; then
    PY="$ROOT/venv/bin/python"
  elif [ -x "$PROD_PY" ] || [ -x "/opt/phantasma/venv/bin/python" ]; then
    PY="/opt/phantasma/venv/bin/python"
  else
    PY="$(command -v python3)"
  fi
  PROBE_TIMEOUT="$VERIFY_TIMEOUT_S" "$PY" "$ROOT/scripts/dependency_check.py"
}

mkdir -p "$(dirname "$LOG_FILE")"
{
  say "=== update run: services=${SERVICES[*]} check_only=$CHECK_ONLY ==="
} >>"$LOG_FILE"

overall=0
declare -a UNCHANGED=() UPDATED=() FAILED=()

for svc in "${SERVICES[@]}"; do
  running=$(docker ps --filter "name=^${svc}$" --format '{{.Names}}')
  if [ -z "$running" ]; then
    fail "$svc: not running, skipping (start it first)"
    FAILED+=("$svc (not running)")
    continue
  fi

  before=$(digest_for "$svc")
  current_image=$(docker inspect --format '{{.Config.Image}}' "$svc" 2>/dev/null)
  say "$svc: running $current_image at ${before:-<unknown>}"

  if [ "$CHECK_ONLY" = "1" ]; then
    remote=$(remote_digest_of "$svc" 2>/dev/null || true)
    if [ -n "$remote" ] && [ "$remote" != "$before" ]; then
      say "$svc: UPDATE AVAILABLE ($remote)"
      UPDATED+=("$svc (update available, not pulled)")
    else
      say "$svc: up to date"
      UNCHANGED+=("$svc")
    fi
    continue
  fi

  if [ "${DRY_RUN:-0}" = "1" ]; then
    say "$svc: would run 'docker compose pull $svc' then restart"
    continue
  fi

  say "$svc: pulling..."
  if ! docker compose -f "$COMPOSE_FILE" pull "$svc" >>"$LOG_FILE" 2>&1; then
    fail "$svc: pull failed, leaving the running container alone"
    FAILED+=("$svc (pull failed)")
    overall=1
    continue
  fi

  say "$svc: restarting..."
  if ! docker compose -f "$COMPOSE_FILE" up -d --no-deps "$svc" >>"$LOG_FILE" 2>&1; then
    fail "$svc: restart failed, rolling back to ${before:-unknown}"
    if [ "$ROLLBACK_ON_FAILURE" = "1" ] && [ -n "$before" ]; then
      # Retag the image we were running before, so compose can start it again.
      run_image=$(docker inspect --format '{{.Config.Image}}' "$svc" 2>/dev/null)
      docker image tag "$before" "${run_image}:rollback" 2>/dev/null || true
      docker compose -f "$COMPOSE_FILE" up -d --no-deps "$svc" >>"$LOG_FILE" 2>&1 || true
    fi
    FAILED+=("$svc (restart failed)")
    overall=1
    continue
  fi

  after=$(digest_for "$svc")
  if [ "$after" = "$before" ]; then
    say "$svc: no change (still ${after:-<unknown>})"
    UNCHANGED+=("$svc")
    continue
  fi

  if verify; then
    say "$svc: UPDATED ${before} -> ${after} and verified working"
    UPDATED+=("$svc")
  else
    fail "$svc: updated but verification FAILED -- rolling back"
    if [ "$ROLLBACK_ON_FAILURE" = "1" ] && [ -n "$before" ]; then
      run_image=$(docker inspect --format '{{.Config.Image}}' "$svc" 2>/dev/null)
      docker image tag "$before" "${run_image}:rollback" 2>/dev/null || true
      # compose starts the tagged image it knows about; if it pulled the newer
      # one, point the service back at the old id explicitly.
      docker update --restart=no "$svc" >/dev/null 2>&1 || true
      docker rm -f "$svc" >/dev/null 2>&1 || true
      if docker image tag "$before" "$run_image"; then
        docker compose -f "$COMPOSE_FILE" up -d --no-deps "$svc" >>"$LOG_FILE" 2>&1 || true
      fi
      if verify; then
        fail "$svc: rolled back to $before and verified working again"
      else
        fail "$svc: ROLLBACK DID NOT RESTORE SERVICE. Needs a human now."
      fi
    fi
    FAILED+=("$svc (verification failed)")
    overall=1
  fi
done

{
  say "--- updated: ${UPDATED[*]:-none}"
  say "--- unchanged: ${UNCHANGED[*]:-none}"
  say "--- failed: ${FAILED[*]:-none}"
} >>"$LOG_FILE"

echo
if [ ${#UPDATED[@]} -gt 0 ]; then printf 'updated  : %s\n' "${UPDATED[*]}"; fi
if [ ${#UNCHANGED[@]} -gt 0 ]; then printf 'unchanged: %s\n' "${UNCHANGED[*]}"; fi
if [ ${#FAILED[@]} -gt 0 ]; then printf 'FAILED   : %s\n' "${FAILED[*]}" >&2; fi
echo "log: $LOG_FILE"
exit "$overall"