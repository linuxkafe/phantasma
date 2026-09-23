#!/usr/bin/env bash
# scripts/check-wakewords.sh
# pHantasma — verify the PT wake word models BEFORE starting the container.
#
# Why: HotwordDetector fell back to all pretrained ENGLISH openWakeWord models
# when the configured .onnx paths did not exist in the container, so "Hey
# Fantasma"/"Olá Fantasma" never fired and noise-triggered loops produced
# "STT returned empty text". The models are verified and loaded before `docker
# compose up`, per project rule.
#
# Modes:
#   check                verify (default). Checks every configured wake word
#                        path exists on the host and — inside the phantasma
#                        image — loads the ONNX models in openwakeword and runs
#                        one inference on a silence buffer, proving the model
#                        tensors are valid. Read-only, no containers started.
#   apply                check, then write the corrected WAKEWORD_MODELS paths
#                        into .env and .env.example when they point at a stale
#                        /opt/phantasma location. compose/env edits are backed
#                        up to <file>.bak. Does NOT launch the stack.
#   --image IMAGE        use a specific image for the in-container probe
#                        (default: resolved from compose).
#   --dry-run            with apply: print intended edits without writing.
#   -h | --help          this help
#
# Exit: 0 = all checks passed, 1 = at least one FAIL (or usage error).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SERVICE="phantasma"
COMPOSE_FILE="${COMPOSE_FILE:-$PROJECT_ROOT/docker-compose.yml}"
IMAGE=""
APPLY_MODE=0
DRY_RUN=0

# Every compose invocation shares file + project dir so ps/run agree with the stack.
compose() { docker compose -f "$COMPOSE_FILE" --project-directory "$PROJECT_ROOT" "$@"; }

log_info() { printf '\033[0;32m[INFO]\033[0m %s\n' "$*"; }
log_warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
log_fail() { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
log_ok()   { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }

PASS=0
FAIL=0
maybe_ok()   { log_ok   "$1"; PASS=$((PASS + 1)); }
maybe_fail() { log_fail "$1"; FAIL=$((FAIL + 1)); }

usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; }

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      check)        APPLY_MODE=0 ;;
      apply)        APPLY_MODE=1 ;;
      --image)      shift; IMAGE="${1:-}" ;;
      --dry-run)    DRY_RUN=1 ;;
      -h|--help)    usage; exit 0 ;;
      *) log_fail "unknown argument: $1"; usage; exit 1 ;;
    esac
    shift
  done
  if [ "$DRY_RUN" -eq 1 ]; then APPLY_MODE=1; fi
}

# The paths the runtime will use. Order of precedence mirrors config.py/env:
# env line (container path) > compose > defaults baked in config.py.
resolve_wakeword_paths() {
  _paths=""
  # (a) compose < (b) .env < (c) config.py defaults. env_file lines win per
  # docker semantics, but compose overrides just these separately.
  local env_value
  env_value="$(grep -E '^WAKEWORD_MODELS=' "${ENV_FILE:-$PROJECT_ROOT/.env}" 2>/dev/null | tail -1 | cut -d= -f2-)"
  if [ -n "$env_value" ]; then
    _paths="$env_value"
  else
    _paths="/app/models/hey_fantasma.onnx,/app/models/ola_fantasma.onnx"
  fi
}

# Host-side model file check. Container paths (/app/models/...) are mapped back
# to the host tree (./models/...) so the files are verified against real disk.
check_host_models() {
  resolve_wakeword_paths
  local IFS=','
  local p host_path ok=1
  for p in $_paths; do
    p="$(echo "$p" | tr -d '[:space:]')"
    [ -n "$p" ] || continue
    case "$p" in
      /app/models/*) host_path="$PROJECT_ROOT/models/${p#/app/models/}" ;;
      *)             host_path="${p#file://}" ;;
    esac
    if [ -f "$host_path" ]; then
      maybe_ok "Model file present: $p ($(du -h "$host_path" | cut -f1))"
    else
      maybe_fail "Model file missing: $p (host lookup: $host_path)"
      ok=0
    fi
  done
  return "$ok"
}

# One-shot in-image probe: load every configured .onnx with openwakeword and
# run one inference, proving weights/tensors are sound. Same pattern as the
# audio probe in docker-audio-setup.sh. Paths arrive via WAKEWORD_PROBE_PATHS
# (space-separated) so bash word-splitting never mangles them.
in_image_probe() {
  cat <<'PYEOF'
import json, os, sys
import warnings
warnings.filterwarnings("ignore", message="Specified provider")
paths = os.environ.get("WAKEWORD_PROBE_PATHS", "").split()
missing = [p for p in paths if not os.path.isfile(p)]
if missing:
    json.dump({"ok": False, "error": "missing files: " + ", ".join(missing)}, sys.stdout)
    sys.exit(0)
try:
    import numpy as np
    from openwakeword.model import Model
    model = Model(wakeword_model_paths=paths)
    # one inference over silence (16kHz, ~1s) proves the graph executes
    x = np.zeros(16000, dtype="float32")
    pred = model.predict(x)
    json.dump({"ok": True, "loaded": sorted(pred),
               "sample_1s": {k: float(pred[k]) for k in sorted(pred)[:3]}}, sys.stdout)
except Exception as e:  # noqa: BLE001 - report any probe failure to the gate
    json.dump({"ok": False, "error": "%r" % e}, sys.stdout)
PYEOF
}

run_in_image_probe() {
  resolve_wakeword_paths
  local env_host=""
  # Translate container paths back to host paths so we can mount them read-only.
  # NOTE: never mutate IFS in this function — unquoted $mounts must word-split
  # on spaces when docker run is composed below.
  local p mounts="" inner_paths=""
  for p in ${_paths//,/ }; do
    [ -n "$p" ] || continue
    case "$p" in
      /app/models/*) host_path="$PROJECT_ROOT/models/${p#/app/models/}" ;;
      *)             host_path="$p" ;;
    esac
    if [ ! -f "$host_path" ]; then
      maybe_fail "Cannot probe in image: model missing on host ($host_path)."
      return
    fi
    inner_paths="${inner_paths:+$inner_paths }$p"
    mounts="$mounts -v $host_path:$p:ro"
  done
  if [ -z "$inner_paths" ]; then
    maybe_fail "No wake word paths configured to probe."
    return
  fi
  local out
  out="$(docker run --rm --entrypoint python3 -e WAKEWORD_PROBE_PATHS="$inner_paths" $mounts "$IMAGE" -c "$(in_image_probe)" 2>&1)" || { maybe_fail "Container probe could not run (image missing? run: docker compose build phantasma)."; return; }
  if echo "$out" | python3 -c "
import json,sys
try: r=json.load(sys.stdin); sys.exit(0 if r.get('ok') else 1)
except Exception: sys.exit(1)
"; then
    echo "$out" | python3 -c "
import json,sys
r=json.load(sys.stdin)
print('  loaded    :', ', '.join(r.get('loaded') or []))
print('  sample    :', json.dumps(r.get('sample') or {}, ensure_ascii=False))
"
    maybe_ok "Wake word models load and infer inside the phantasma image."
  else
    echo "$out"
    maybe_fail "Wake word models FAILED to load inside the image — see output."
  fi
}

resolve_image() {
  if [ -n "$IMAGE" ]; then return; fi
  local img
  img="$(compose config --format json 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['services']['$SERVICE'].get('image',''))" 2>/dev/null)"
  if [ -z "$img" ] || [ "$img" = "<no value>" ]; then
    IMAGE="phantasma-phantasma"
  else
    IMAGE="$img"
  fi
  log_info "Using image: $IMAGE"
}

apply_fix() {
  # Correct stale /opt/phantasma wake word paths in .env and .env.example so a
  # fresh `docker compose up` reads container-true paths.
  for f in "$PROJECT_ROOT/.env" "$PROJECT_ROOT/.env.example"; do
    [ -f "$f" ] || continue
    local patched patched_text
    patched_text="$(sed 's#^\(WAKEWORD_MODELS=\)/opt/phantasma/models/hey_fantasma\.onnx,/opt/phantasma/models/ola_fantasma\.onnx#\1/app/models/hey_fantasma.onnx,/app/models/ola_fantasma.onnx#' "$f")"
    if [ "$patched_text" != "$(cat "$f")" ]; then
      patched=1
      log_info "  $f: stale /opt path -> /app/models"
      if [ "$DRY_RUN" -eq 0 ]; then
        cp "$f" "$f.bak"
        printf '%s\n' "$patched_text" > "$f"
      fi
    else
      log_ok "  $f: wake word paths already correct."
    fi
  done
  [ "${patched:-0}" -eq 1 ] && log_warn "Run the check again, then: docker compose up -d --force-recreate phantasma"
}

main() {
  parse_args "$@"
  if [ "$APPLY_MODE" -eq 1 ]; then
    log_info "pHantasma wake word pre-flight — mode: apply"
    check_host_models
    apply_fix
  else
    log_info "pHantasma wake word pre-flight — mode: check"
    check_host_models
    resolve_image
    run_in_image_probe
  fi

  echo
  log_info "Result: $PASS passed, $FAIL failed."
  if [ "$FAIL" -gt 0 ]; then
    log_fail "Wake word verification failed. Fix the model paths, then re-run before compose up."
    exit 1
  fi
  log_ok "Wake words ready. Safe to: docker compose up -d phantasma"
  exit 0
}

main "$@"