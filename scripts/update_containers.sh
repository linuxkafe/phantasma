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

# Where the compose file the RUNNING containers were actually created from.
#
# It is not $ROOT. The `phantasma` compose project lives in the development tree,
# and `docker inspect` says so:
#
#   docker inspect ollama --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
#   /home/seyon/dev/pHantasma/docker-compose.yml
#
# and its bind mounts point there too -- `./ollama-data` resolves to
# /home/seyon/dev/pHantasma/ollama-data, which is where the models are. So
# copying the compose file into /opt/phantasma would be worse than not deploying
# it: the relative volumes would resolve to a fresh empty directory and
# `docker compose up` would recreate the Ollama container with no models in it.
#
# This was written assuming `$ROOT/docker-compose.yml`, which does not exist in
# production, so the image half of the nightly job exited 2 on every run and the
# rollback code below was never reached. Found by the peer review, which read
# the log: four "compose file not found" lines.
#
# Overridable, and `PHANTASMA_COMPOSE_DIR` is the honest name -- this is a host
# value, and the rule from CLAUDE.md is that a host value does not go in code.
COMPOSE_DIR="${PHANTASMA_COMPOSE_DIR:-$ROOT}"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yml"
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
  fail "  PHANTASMA_COMPOSE_DIR=$COMPOSE_DIR"
  fail "  where do the running containers come from?"
  fail "  docker inspect ollama --format '{{index .Config.Labels \"com.docker.compose.project.config_files\"}}'"
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

# Tag the image the service was running before, under a name compose can
# actually start.
#
# The previous version wrote `${run_image}:rollback`, e.g.
# `ollama/ollama:latest:rollback`. Docker rejects that outright -- a repository
# cannot carry two colons -- so the tag always failed, and it was silenced with
# `2>/dev/null || true`. Both occurrences. The rollback therefore rested on
# nothing and the comment described a step that never ran.
rollback_tag() {  # service, image-id -> prints the tag it created, or fails
  local svc="$1" img_id="$2" run_image
  run_image=$(docker inspect --format '{{.Config.Image}}' "$svc" 2>/dev/null) || return 1
  case "$run_image" in
    *@sha256:*)
      # Digest-pinned: the ref cannot be re-tagged ("refusing to create a tag with
      # a digest reference"), so the rollback target has to be the id itself.
      printf '%s' "$img_id"
      return 0
      ;;
  esac
  # `repo/name:tag` -> `repo/name:rollback-<short id>`
  local base="${run_image%:*}" short="${img_id:0:12}"
  docker image tag "$img_id" "${base}:rollback-${short}" 2>/dev/null || return 1
  printf '%s' "${base}:rollback-${short}"
}

# Put the service back on a specific image, and only take it down once the
# replacement is known to be startable.
#
# The order here is the whole point. It used to be:
#
#   docker update --restart=no "$svc"
#   docker rm -f "$svc"
#   if docker image tag "$before" "$run_image"; then compose up; fi
#
# `rm -f` first, and the retag second, guarded. When the retag failed -- which
# it did, always -- the container was already deleted with its restart policy
# disabled and `compose up` never ran. The service was down AND staying down,
# which is strictly worse than the unverified update the rollback was written to
# undo.
rollback() {  # service, image-id -> 0 if the service is back
  local svc="$1" img_id="$2" target
  target=$(rollback_tag "$svc" "$img_id") || {
    fail "$svc: nao foi possivel preparar a imagem anterior; o servico fica como esta"
    return 1
  }
  say "$svc: rollback para $target (antes de mexer no container)"
  # The tag exists now, so removing the container cannot strand it.
  docker rm -f "$svc" >/dev/null 2>&1 || true
  if [ "$target" = "$img_id" ]; then
    docker run -d --name "$svc" --restart unless-stopped "$img_id" \
      >>"$LOG_FILE" 2>&1 || true
  else
    docker compose -f "$COMPOSE_FILE" up -d --no-deps "$svc" >>"$LOG_FILE" 2>&1 || true
  fi
  return 0
}

verify() {  # service -> 0 if THAT service's dependency actually answers
  # Scoped on purpose. It used to run the whole-project check, so updating
  # `searxng` while the Ollama fallback was slow rolled `searxng` back -- the
  # wrong container, for a reason it did not cause. And the fallback IS slow
  # right now, which means the first night any digest moves would have undone a
  # good update at 03:30.
  say "verifying $1 (up to ${VERIFY_TIMEOUT_S}s)..."
  local only="${1:-}"
  if [ -x "$ROOT/venv/bin/python" ]; then
    PY="$ROOT/venv/bin/python"
  elif [ -x /opt/phantasma/venv/bin/python ]; then
    PY=/opt/phantasma/venv/bin/python
  else
    # No `$PROD_PY`: it was referenced and never assigned, and under
    # `set -u` that is not a fallback that fails, it is a script that DIES --
    # after the pull, after the restart, before the check and before the
    # rollback. An update landing unverified is worse than the outage the
    # rollback exists to prevent.
    echo "no venv python with the ollama SDK; refusing to verify" >&2
    return 2
  fi
  PROBE_TIMEOUT="$VERIFY_TIMEOUT_S" "$PY" "$ROOT/scripts/dependency_check.py" \
    ${only:+--only "$only"}
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
      rollback "$svc" "$before" || fail "$svc: rollback nao restores o servico"
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
      if rollback "$svc" "$before"; then
        if verify "$svc"; then
          fail "$svc: rolled back to $before and verified working again"
        else
          fail "$svc: ROLLBACK DID NOT RESTORE SERVICE. Needs a human now."
        fi
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