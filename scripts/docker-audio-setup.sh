#!/usr/bin/env bash
# scripts/docker-audio-setup.sh
# pHantasma — verify and enable host audio access from the phantasma container.
#
# Why: the voice pipeline depends on the container being able to open /dev/snd
# (capture + playback). This tool diagnoses every link in that chain (host ALSA
# card, device nodes, compose mapping, container group, real capture/playback)
# and — only via `apply` — idempotently fixes the host-side gaps it can.
#
# Modes:
#   check                diagnose (default). Read-only except for a brief
#                        stop/restart of the service during the capture test
#                        (disable with CHECK_SKIP_CAPTURE=1). The probe is
#                        automatically skipped while a model download is in
#                        flight, so an interrupted download is never corrupted.
#   apply [--devices D]  check, then apply fixes:
#                          - add current user to 'audio' group (needs sudo)
#                          - ensure docker-compose.yml maps /dev/snd + group_add audio
#                          - with --devices D (e.g. plughw:0,0), switch the
#                            ALSA_DEVICE_IN/OUT env lines to D in compose
#                        compose edits are backed up to docker-compose.yml.bak
#   --dry-run            show intended mutations of apply without writing
#   -h | --help          this help
#
# Exit: 0 = all checks passed, 1 = at least one FAIL (or usage error).

set -u

# Resolve the project root from the script location, not the cwd, so the tool
# works from any directory (e.g. `cd scripts` first).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SERVICE="${SERVICE:-phantasma}"
COMPOSE_FILE="${COMPOSE_FILE:-$PROJECT_ROOT/docker-compose.yml}"
IMAGE_HINT="phantasma-phantasma"
CHECK_SKIP_CAPTURE="${CHECK_SKIP_CAPTURE:-0}"
DRY_RUN=0
APPLY_MODE=0
DEVICES_OVERRIDE=""

# Every compose invocation shares the same file + project directory so that
# `ps`/`stop`/`start`/`up` agree with the stack started from the repo root.
compose() { docker compose -f "$COMPOSE_FILE" --project-directory "$PROJECT_ROOT" "$@"; }

log_info() { printf '\033[0;32m[INFO]\033[0m %s\n' "$*"; }
log_warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
log_fail() { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
log_ok()   { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }

PASS=0
FAIL=0
maybe_ok()   { log_ok   "$1"; PASS=$((PASS + 1)); }
maybe_fail() { log_fail "$1"; FAIL=$((FAIL + 1)); }

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; }

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      check)        APPLY_MODE=0 ;;
      apply)        APPLY_MODE=1 ;;
      --dry-run)    DRY_RUN=1 ;;
      --devices)    shift; DEVICES_OVERRIDE="${1:-}" ;;
      -h|--help)    usage; exit 0 ;;
      *) log_fail "unknown argument: $1"; usage; exit 1 ;;
    esac
    shift
  done
  if [ "$DRY_RUN" -eq 1 ]; then APPLY_MODE=1; fi
}

resolve_image() {
  # Prefer the image the running/composed service actually uses; fall back to hint.
  local img
  img="$(docker inspect "$SERVICE" --format '{{.Config.Image}}' 2>/dev/null)"
  if [ -n "$img" ] && [ "$img" != "<no value>" ]; then
    IMAGE="$img"
  else
    IMAGE="$(compose config --format json 2>/dev/null \
      | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['services']['$SERVICE']['image']) if 'image' in d.get('services',{}).get('$SERVICE',{}) else print('$IMAGE_HINT')" 2>/dev/null \
      || echo "$IMAGE_HINT")"
  fi
  log_info "Using image: $IMAGE"
}

# ---------------------------------------------------------------------------
# Host-side checks
# ---------------------------------------------------------------------------
check_host_card() {
  # Note: procfs files report st_size=0, so read content (grep) instead of -s.
  if grep -q . /proc/asound/cards 2>/dev/null; then
    maybe_ok "Host ALSA card present:"
    sed -E 's/^/             /' /proc/asound/cards
  else
    maybe_fail "No ALSA card on host (/proc/asound/cards empty)."
    log_warn "Required: a working sound card. For USB devices, replug and run: sudo udevadm trigger"
  fi
}

check_host_snd_nodes() {
  if [ -d /dev/snd ] && ls /dev/snd/ 2>/dev/null | grep -q .; then
    maybe_ok "Host /dev/snd exposed with $(ls /dev/snd | wc -l) node(s)."
  else
    maybe_fail "Host /dev/snd missing/empty — container cannot be granted the device."
    log_warn "Required: access to /dev/snd on the host (kernel sound drivers + udev)."
  fi
}

check_compose_mapping() {
  [ -f "$COMPOSE_FILE" ] || { maybe_fail "Compose file '$COMPOSE_FILE' not found."; return; }
  local ok=1
  grep -q "/dev/snd:/dev/snd" "$COMPOSE_FILE" || { ok=0; log_fail "docker-compose.yml: missing device mapping /dev/snd:/dev/snd"; }
  grep -q "group_add" "$COMPOSE_FILE" || { ok=0; log_fail "docker-compose.yml: missing group_add"; }
  grep -q '"audio"' "$COMPOSE_FILE" || { ok=0; log_fail "docker-compose.yml: audio group not listed in group_add"; }
  if [ "$ok" -eq 1 ]; then
    maybe_ok "docker-compose.yml maps /dev/snd + audio group (already configured)."
  else
    log_warn "Required service stanza (under phantasma:):"
    log_warn "  devices:"
    log_warn "    - /dev/snd:/dev/snd"
    log_warn "  group_add:"
    log_warn "    - \"audio\""
    maybe_fail "Compose audio mapping incomplete."
  fi
}

check_host_user_group() {
  if id -aG 2>/dev/null | tr ' ' '\n' | grep -qx "audio"; then
    maybe_ok "Current user is in the 'audio' group (host-level CLI access)."
  else
    log_warn "Current user NOT in 'audio' group (informational — container runs as root+gid29)."
    log_warn "To grant host CLI access: sudo usermod -a -G audio \"\$USER\" (re-login after)."
    maybe_ok "Host user group check (informational)."
  fi
}

# ---------------------------------------------------------------------------
# Container-side checks
# ---------------------------------------------------------------------------
container_test_script() {
  # One-shot diagnostics inside a throwaway container with the *required* mapping.
  cat <<'PYEOF'
import json, sys
have_snd = False
try:
    import os
    have_snd = os.path.isdir("/dev/snd") and bool(os.listdir("/dev/snd"))
except Exception:
    pass
if not have_snd:
    json.dump({"nodes": False, "group": False, "query": None,
               "capture": "MISSING_DEV_SND"}, sys.stdout)
    sys.exit(0)
try:
    import grp
    groups = [g.gr_name for g in grp.getgrall() if g.gr_gid in os.getgroups()]
except Exception:
    groups = []
try:
    import sounddevice as sd
    devs = sd.query_devices()
    query = [{"index": i, "name": d["name"], "in": int(d["max_input_channels"]),
              "out": int(d["max_output_channels"])} for i, d in enumerate(devs)]
except Exception as e:
    query = None
    json.dump({"nodes": have_snd, "group": groups, "query": None,
               "capture": "IMPORT_FAIL: %r" % e}, sys.stdout)
    sys.exit(0)
result = {"nodes": have_snd, "group": groups, "query": query, "capture": None,
          "playback": None, "level": None, "attempts": []}
try:
    import numpy as np
    sd.default.device = 0
    for ch in (1, 2):
        try:
            rec = sd.rec(int(16000 * 1.0), samplerate=16000, channels=ch, dtype="int16")
            sd.wait()
            a = rec.astype("float32")
            result["level"] = {"rms": int((a ** 2).mean() ** 0.5), "peak": int(abs(a).max())}
            result["attempts"].append({"ch": ch, "status": "OK"})
            result["capture"] = "OK"
            break
        except Exception as e:
            es = repr(e)
            result["attempts"].append({"ch": ch, "status": "FAIL", "err": es})
            if "Invalid number of channels" in es:
                continue
            if any(k in es for k in ("Unavailable", "busy", "resource busy")):
                result["capture"] = "BUSY (device held by another process?)"
            else:
                result["capture"] = "FAIL: %s" % es
            break
    if result["capture"] is None:
        result["capture"] = "FAIL: no supported channel count"
except Exception as e:
    result["capture"] = "FAIL: %r" % e
try:
    import numpy as np
    sd.play(np.zeros(8000, dtype="int16"), samplerate=16000)
    sd.wait()
    result["playback"] = "OK"
except Exception as e:
    result["playback"] = "FAIL: %r" % e
json.dump(result, sys.stdout)
PYEOF
}

run_container_check() {
  resolve_image
  local present=1
  [ -d /dev/snd ] && ls /dev/snd 2>/dev/null | grep -q . || present=0
  if [ "$present" -eq 0 ]; then
    log_warn "Skipping container checks — no /dev/snd on host."
    return
  fi
  local out
  out="$(docker run --rm --device /dev/snd --group-add audio --entrypoint bash "$IMAGE" -c "
    python3 - <<'PY'
$(container_test_script)
PY
  " 2>/dev/null)" || { maybe_fail "Container probe could not run (image missing? run: docker compose build phantasma)."; return; }
  echo "$out" | python3 -c "
import json, sys
r = json.load(sys.stdin)
print('  nodes   :', 'OK' if r.get('nodes') else 'MISSING')
print('  groups  :', ','.join(r.get('group') or []) or '(none)')
if r.get('capture') == 'OK':
    lv = r.get('level') or {}
    print('  capture : OK  (rms=%s peak=%s)' % (lv.get('rms'), lv.get('peak')))
elif r.get('capture','').startswith('OK'):
    print('  capture : OK')
else:
    print('  capture :', r.get('capture'))
att = '; '.join(str(a) for a in r.get('attempts') or [])
if att:
    print('  attempts:', att)
print('  playback:', r.get('playback') or 'not attempted')
" || { maybe_fail "Container probe returned unparsable output."; return; }

  # Verdict pass/fail
  if echo "$out" | python3 -c "
import json,sys
r=json.load(sys.stdin)
ok=r.get('nodes',False) and ('audio' in (r.get('group') or [])) and (r.get('capture')== 'OK') and (r.get('playback')=='OK')
sys.exit(0 if ok else 1)
"; then
    maybe_ok "Container can capture + play audio through /dev/snd (verified end-to-end)."
  else
    log_fail "Container audio access NOT fully verified — see details above."
    log_warn "Required (compose): devices /dev/snd:/dev/snd + group_add [audio];"
    log_warn "or a host audio server socket (Pulse/PipeWire) with matching env."
  fi
}

# ---------------------------------------------------------------------------
# Capture test with service stop/start (avoid device-busy false negatives)
# ---------------------------------------------------------------------------
# If the service is mid-way through a model download (Whisper medium is 1.4 GB),
# stopping it kills the download, leaves a corrupt partial file and forces a full
# re-download next time. Detect that and refuse to pause instead of destroying
# hours of a download. Detection is deterministic: a still-growing medium.pt
# inside the container's whisper cache (log-scraping is unreliable — tqdm bars
# are \r-rendered and checkpoints only in stderr).
service_download_in_flight() {
  compose ps "$SERVICE" 2>/dev/null | grep -q "Up" || return 1
  docker exec "$SERVICE" sh -c '
    for f in /root/.cache/whisper/*.pt; do
      [ -e "$f" ] || continue
      s0=$(wc -c < "$f")
      sleep 3
      s1=$(wc -c < "$f")
      [ "$s1" -gt "$s0" ] && { echo "$f is growing ($s0->$s1)"; exit 0; }
    done
    exit 1
  ' 2>/dev/null && return 0
  # The checksum warning marks the corrupt-redownload trap explicitly.
  docker logs --since 10m "$SERVICE" 2>/dev/null | grep -q 're-downloading' && return 0
  return 1
}

run_capture_test() {
  if [ "$CHECK_SKIP_CAPTURE" = "1" ]; then
    log_warn "CHECK_SKIP_CAPTURE=1 — skipping live capture test (nodes+group only)."
    return
  fi
  if service_download_in_flight; then
    log_warn "Model download in flight inside '$SERVICE' — skipping live probe"
    log_warn "to avoid aborting it (a partial file would force a full re-download)."
    log_warn "Re-run the check after it finishes, or use CHECK_SKIP_CAPTURE=1."
    return
  fi
  local was_running=0
  if compose ps "$SERVICE" 2>/dev/null | grep -q "Up"; then
    was_running=1
    log_info "Pausing '$SERVICE' for a clean capture test..."
    compose stop "$SERVICE" >/dev/null 2>&1 || { log_warn "Could not stop service."; }
  fi
  run_container_check
  if [ "$was_running" -eq 1 ]; then
    log_info "Restarting '$SERVICE'..."
    compose start "$SERVICE" >/dev/null 2>&1 || log_warn "manual restart may be needed"
  fi
}

# ---------------------------------------------------------------------------
# apply: host + compose fixes (idempotent)
# ---------------------------------------------------------------------------
apply_fixes() {
  log_info "Apply mode ($([ "$DRY_RUN" -eq 1 ] && echo dry-run || echo live))"

  # 1) audio group membership (host CLI convenience; harmless if already member)
  if ! id -aG 2>/dev/null | tr ' ' '\n' | grep -qx "audio"; then
    log_info "Adding current user to 'audio' group..."
    if [ "$DRY_RUN" -eq 1 ]; then log_warn "  [dry-run] sudo usermod -a -G audio \"\$USER\""; 
    elif sudo -n usermod -a -G audio "$USER" 2>/dev/null; then
      log_ok "Users added to audio group (effective after re-login)."
    else
      log_warn "  Could not add group (needs sudo). Run manually: sudo usermod -a -G audio \"\$USER\""
    fi
  fi

  # 2) compose file: ensure devices + group_add (or --devices ALSA names)
  apply_compose
}

apply_compose() {
  [ -f "$COMPOSE_FILE" ] || { log_fail "Compose file '$COMPOSE_FILE' missing — cannot patch."; return; }
  local py_script
  py_script=$(cat <<PY
import sys, shutil

path = "$COMPOSE_FILE"
device_override = "$DEVICES_OVERRIDE"
dry = int("$DRY_RUN")
text = open(path).read()
changed = []

def has(line):
    return line in text

# --- devices mapping ---
if not has("- /dev/snd:/dev/snd"):
    marker = "  phantasma:"
    if marker in text:
        idx = text.index(marker) + len(marker)
        insert = "\n    devices:\n      - /dev/snd:/dev/snd"
        text = text[:idx] + insert + text[idx:]
        changed.append("devices: /dev/snd:/dev/snd")
else:
    print("  compose: /dev/snd mapping present (skip)")

# --- group_add audio ---
if '"audio"' not in text:
    marker = "    devices:"
    if marker in text:
        idx = text.index(marker) + len(marker)
        insert = "\n    group_add:\n      - \"audio\""
        text = text[:idx] + insert + text[idx:]
        changed.append("group_add: [audio]")
else:
    print("  compose: group_add audio present (skip)")

# --- ALSA device names ---
if device_override:
    for key in ("ALSA_DEVICE_IN", "ALSA_DEVICE_OUT"):
        if f"{key}={device_override}" in text:
            print(f"  compose: {key} already {device_override} (skip)")
            continue
        import re
        new_text = re.sub(rf"^\s*-\s*{key}=.*$",
                          f"      - {key}={device_override}",
                          text, count=1, flags=re.M)
        if new_text != text:
            text = new_text
            changed.append(f"{key}={device_override}")

if not changed:
    print("  compose: no changes needed")
    sys.exit(0)

print("  compose: pending:", ", ".join(changed))
if dry:
    print("  [dry-run] won't write. Run without --dry-run to apply.")
    sys.exit(0)

shutil.copy(path, path + ".bak")
open(path, "w").write(text)
print(f"  wrote {path} (backup: {path}.bak)")
PY
)
  python3 -c "$py_script"
  if [ "$DRY_RUN" -eq 0 ] && grep -q "/dev/snd:/dev/snd" "$COMPOSE_FILE"; then
    log_info "Recreating container so device mapping takes effect..."
    compose up -d --force-recreate "$SERVICE" >/dev/null 2>&1 \
      && log_ok "Container recreated." || log_fail "docker compose up failed — check output above."
  fi
}

# ---------------------------------------------------------------------------
main() {
  parse_args "$@"
  log_info "pHantasma container audio setup — mode: $([ "$APPLY_MODE" -eq 1 ] && echo apply || echo check)"
  check_host_card
  check_host_snd_nodes
  check_compose_mapping
  check_host_user_group
  run_capture_test

  if [ "$APPLY_MODE" -eq 1 ]; then
    apply_fixes
    # re-check after apply so the summary reflects real state
    if [ "$DRY_RUN" -eq 0 ]; then
      log_info "Re-checking after apply (capture skipped)..."
      CHECK_SKIP_CAPTURE=1 run_container_check
    fi
  fi

  echo
  log_info "Result: $PASS passed, $FAIL failed."
  if [ "$FAIL" -gt 0 ]; then
    log_fail "Audio access not fully satisfied. Review the required config lines above."
    exit 1
  fi
  log_ok "Container audio access verified."
  exit 0
}

main "$@"