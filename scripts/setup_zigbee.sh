#!/usr/bin/env bash
# Bring up the Zigbee coordinator: Mosquitto + Zigbee2MQTT.
#
# Idempotent. Safe to run again after a reboot, after `docker compose pull`,
# or when nothing has changed at all.
#
#   scripts/setup_zigbee.sh            bring the stack up and wait for the coordinator
#   scripts/setup_zigbee.sh --check    report only, start nothing
#   scripts/setup_zigbee.sh --permit-join on            open the join window (10 s)
#   scripts/setup_zigbee.sh --permit-join on JOIN_MINUTES=4
#   scripts/setup_zigbee.sh --permit-join off           close it now
#   scripts/setup_zigbee.sh --logs     follow zigbee2mqtt
#
# JOIN_MINUTES is minutes for YOU and is capped at 4. The coordinator counts in
# SECONDS and refuses anything above 254 (a uint8), which is why the cap is
# there: 4 min 14 s. Zigbee2MQTT's docs call the field "minutes"; the code
# multiplies by 1000 and asserts <= 254. The docs are wrong.
#
# Run from the DEVELOPMENT tree (~/dev/pHantasma), not from /opt/phantasma.
# See ROOT below for why the copy in production is not the one to run.
#
# Why a script and not `docker compose up -d zigbee2mqtt`
# -------------------------------------------------------
# Zigbee2MQTT 2.14.2 will not read `zigbee2mqtt/configuration.yaml` on a first
# boot. It ignores what is there, starts an onboarding web server on port 8080
# and waits there for a human. Setting `onboarding: false` in that file does
# not help: the file is read, the flag is honoured, and the onboarding server
# comes up anyway. Measured, not inferred -- four attempts, same outcome, and
# `/data` reported `onboarding: true` for a file that said `false`.
#
# So the config is seeded into a named volume and then submitted once over the
# onboarding endpoint, which is Z2M's own non-interactive path
# (`POST /submit` -> `settings.apply()`). After that the flag is persisted and
# later restarts boot straight through. That is why this file exists instead
# of three lines in a README: the sequence is not discoverable from the
# compose file, and getting it wrong produces a container that looks healthy
# and serves nothing.
#
# What this does NOT do
# ---------------------
# It cannot pair anything. The two devices that were on the Cloogy hub have to
# be unpaired from it by hand (press and hold) and re-joined here, with
# `--permit-join on` for the duration. A coordinator that has never been asked
# to join anything reports "0 devices are joined", which is the correct and
# only thing it can report.
#
# Run this from the DEVELOPMENT tree, not from /opt/phantasma. See ROOT below.

set -uo pipefail

# Deliberately NOT $(dirname $0)/..: `deploy.sh` copies this file to
# /opt/phantasma/scripts/, where the parent directory has no compose file and
# the containers do not live. Running the copy there fails with the least
# useful message docker has -- "no configuration file provided: not found" --
# which says nothing about the two trees or which one owns the containers.
#
# The compose project that runs zigbee2mqtt is the DEV tree's. That is not an
# accident of history: `update_containers.sh` documents that ollama and searxng
# are also created from /home/seyon/dev/pHantasma/docker-compose.yml, with
# `docker inspect` to prove it. Following that precedent, zigbee2mqtt joins the
# same set rather than growing a second compose file that nothing deploys.
ROOT="${PHANTASMA_DEV_ROOT:-/home/seyon/dev/pHantasma}"
if [ ! -f "$ROOT/docker-compose.yml" ]; then
  echo "FAILED: no docker-compose.yml in $ROOT" >&2
  echo "The Zigbee containers are defined in the development tree. Set" >&2
  echo "PHANTASMA_DEV_ROOT to point somewhere else if that moved." >&2
  exit 1
fi
cd "$ROOT" || exit 1

COMPOSE=(docker compose)
BROKER_URL="http://127.0.0.1:8090"
CONFIG_FILE="zigbee2mqtt-data/configuration.yaml"
ONBOARD_TIMEOUT=180

CHECK_ONLY=0
FOLLOW_LOGS=0
PERMIT_JOIN=""
REPORT_INPUT="$(mktemp)"
STATE_INPUT="$(mktemp)"
cleanup() { rm -f "$REPORT_INPUT" "$STATE_INPUT"; }
trap cleanup EXIT

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --logs) FOLLOW_LOGS=1 ;;
    --permit-join)
      shift
      case "${1:-}" in
        on | true | yes) PERMIT_JOIN=on ;;
        off | false | no) PERMIT_JOIN=off ;;
        *)
          echo "--permit-join takes 'on' or 'off', got '${1:-}'" >&2
          usage 2
          ;;
      esac
      ;;
    -h | --help) usage 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage 2
      ;;
  esac
  shift
done

if [ "$FOLLOW_LOGS" = 1 ]; then
  exec "${COMPOSE[@]}" logs -f zigbee2mqtt
fi

if [ "$CHECK_ONLY" = 1 ]; then
  echo "== containers =="
  "${COMPOSE[@]}" ps mosquitto zigbee2mqtt

  # From the broker's retained state, not from the log. The log is a history:
  # grepping it answered "what happened at some point since the container
  # started", which on a box up for hours means the answer does not move. Two
  # errors from a failed run this morning were the last matching lines, so a
  # perfectly healthy coordinator reported as broken. `--check` exists to say
  # what is true now, so it asks the thing that publishes retained state.
  echo
  echo "== coordinator (live, from the broker) =="
  info="$(docker compose exec -T mosquitto mosquitto_sub -h localhost \
    -t 'zigbee2mqtt/bridge/info' -C 1 -W 5 2>/dev/null)"
  if [ -z "$info" ]; then
    echo "  no answer on zigbee2mqtt/bridge/info -- is zigbee2mqtt running?"
    "${COMPOSE[@]}" logs --tail 15 zigbee2mqtt 2>&1 | sed 's/^/  /'
    exit 1
  fi
  printf '%s\n' "$info" > "$REPORT_INPUT"
  python3 - "$REPORT_INPUT" <<'PY'
import json
import sys

with open(sys.argv[1]) as fh:
    d = json.load(fh)

herdsman = d.get("zigbee_herdsman", {})
coord = d.get("coordinator", {})
fw = coord.get("meta", {})
# `config.devices`, NOT `devices`. In 2.14.2 the joined-device map lives under
# `config`; the top level has no `devices` key at all, so reading it yields 0
# joined even when a device is paired and reporting. `health` does carry a
# `devices` map, which is where the confusion comes from -- same name, different
# thing, and one of them is empty when nothing is paired.
devices = (d.get("config") or {}).get("devices") or {}

print(f"  version:            {d.get('version')}")
print(f"  zigbee-herdsman:    {herdsman.get('version')}")
print(f"  coordinator fw:     {coord.get('type')} rev {fw.get('revision')}")
print(f"  ieee:               {coord.get('ieee_address')}")
print(f"  permit_join:        {d.get('permit_join')}")
print(f"  devices joined:     {len(devices)}")
for ieee, spec in devices.items():
    print(f"    - {spec.get('friendly_name')} ({ieee})")
PY
  rm -f "$REPORT_INPUT"
  exit 0
fi

# --- seed the config into the volume -------------------------------------
# Only when it is not already there. The generated `advanced.network_key`
# lives in this same file, so overwriting it orphans every paired device.
#
# Seeded into the volume, not into the working tree on purpose: after the
# first boot this file holds the Zigbee network key, and the working tree is
# a git repository.
seed_config() {
  "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh zigbee2mqtt -c \
    'mkdir -p /app/data && cat > /app/data/configuration.yaml' <<'YAML'
homeassistant:
  enabled: false
permit_join: false
onboarding: false
mqtt:
  base_topic: zigbee2mqtt
  server: mqtt://mosquitto:1883
serial:
  port: /dev/ttyUSB0
  adapter: zstack
frontend:
  port: 8080
  enabled: true
YAML
  echo "seeded zigbee2mqtt configuration.yaml"
}

config_exists() {
  "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh zigbee2mqtt -c \
    '[ -s /app/data/configuration.yaml ]'
}

# --- external converters -------------------------------------------------
# Seeded into the volume, and tracked in the repo, because a converter is the
# only thing that makes an unsupported device work at all: the PLG300 does not
# announce its On/Off cluster, so without `plg300.js` the plug reports power and
# refuses every command. See zigbee2mqtt/converters/plg300.js.
#
# Skipped when the file is already in the volume, so a converter edited inside
# the container is not overwritten by a re-run.
seed_converters() {
  local src="$ROOT/zigbee2mqtt/converters"
  [ -d "$src" ] || return 0
  local shipped
  shipped="$(ls -1 "$src"/*.js 2>/dev/null | wc -l)"
  [ "$shipped" -gt 0 ] || return 0

  "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh zigbee2mqtt -c \
    'mkdir -p /app/data/external_converters'
  local f
  for f in "$src"/*.js; do
    "${COMPOSE[@]}" run --rm --no-deps -T \
      -v "$f:/tmp/$(basename "$f"):ro" --entrypoint sh zigbee2mqtt -c \
      'if [ -e /app/data/external_converters/'"$(basename "$f")"' ]; then
         echo "keep  $(basename "'"$(basename "$f")"'")  (already in the volume)"
       else
         cp /tmp/'"$(basename "$f")"' /app/data/external_converters/ && echo "seed  '"$(basename "$f")"'"
       fi'
  done
}

echo "== pulling images =="
"${COMPOSE[@]}" pull --quiet mosquitto zigbee2mqtt || exit 1

# The broker is the one thing Z2M cannot start without -- it exits with
# "MQTT failed to connect" -- so it gets a gate rather than a sleep.
#
# The gate publishes. It does not subscribe: `mosquitto_sub -C 1 -W 2` exits
# non-zero on every run, because nothing publishes to the topic it waits for,
# so a subscribe-based probe here reports a perfectly healthy broker as dead.
# A publish only succeeds if the broker took it.
broker_ready() {
  docker compose exec -T mosquitto mosquitto_pub -h localhost -t health/probe -m ok >/dev/null 2>&1
}

echo
echo "== starting mosquitto =="
"${COMPOSE[@]}" up -d mosquitto || exit 1

for _ in $(seq 1 30); do
  broker_ready && break
  sleep 1
done
if ! broker_ready; then
  echo "FAILED: mosquitto is not accepting publishes after 30s" >&2
  "${COMPOSE[@]}" logs --tail 20 mosquitto >&2
  exit 1
fi
echo "mosquitto accepting on 1883 (loopback)"

echo
echo "== seeding zigbee2mqtt config =="
if config_exists; then
  echo "configuration.yaml already present, leaving it alone"
else
  seed_config
fi

echo
echo "== seeding external converters =="
seed_converters

echo
# --- retained state, per device -------------------------------------------
# Without `retain: true` Zigbee2MQTT publishes device state only to whoever is
# listening at the moment a device happens to report. For a mains-powered plug
# that is most of the time; for a battery-powered clamp it is nearly never,
# because the device is asleep between readings.
#
# Measured 2026-10-05, with no retain: `device_status?nickname=consumo`
# answered `unreachable` on every single attempt, and the Geral header had no
# clamp battery to show -- not a wrong number, no number. With retain, the last
# reading is delivered to every subscriber the moment it connects, and a
# sleeping clamp's battery is still there to be read.
#
# Written into configuration.yaml rather than pushed over the WebSocket because
# this is the one change that must survive a Z2M restart we do not control (a
# reboot, a crash, `docker compose up -d`). Idempotent, and keyed on the IEEE
# address rather than the friendly name -- the owner renames devices in the
# frontend, and a name-keyed script would quietly edit nothing next time.
set_device_retain() {
  # Node, not Python: measured 2026-10-05, the zigbee2mqtt image has no python
  # at all (`exec: "python3": executable file not found in $PATH`), so a Python
  # implementation of this would have skipped itself forever while looking
  # correct. js-yaml ships with Zigbee2MQTT inside the pnpm store, so the path is
  # resolved by globbing rather than hardcoded -- pnpm's layout changes between
  # releases and a hardcoded hash is a bug waiting for an image bump.
  "${COMPOSE[@]}" exec -T zigbee2mqtt sh -c \
    'YAML_MOD=$(ls -d /app/node_modules/.pnpm/js-yaml@*/node_modules/js-yaml 2>/dev/null | head -1)
if [ -z "$YAML_MOD" ]; then
  echo "SKIP: js-yaml not found in the zigbee2mqtt image; retain left alone"
  exit 0
fi
YAML_MOD="$YAML_MOD" node -e "
const yaml = require(process.env.YAML_MOD);
const fs = require(\"fs\");
const p = \"/app/data/configuration.yaml\";
let cfg;
try { cfg = yaml.load(fs.readFileSync(p, \"utf8\")) || {}; }
catch (e) { console.log(\"SKIP: configuration.yaml unreadable: \" + e.message); process.exit(0); }
const devs = cfg.devices || {};
let changed = 0;
for (const [ieee, spec] of Object.entries(devs)) {
  if (!spec || typeof spec !== \"object\") continue;
  if (spec.retain === true) continue;
  spec.retain = true;
  changed++;
}
if (!changed) { console.log(\"retain already set on every device\"); process.exit(0); }
fs.writeFileSync(p, yaml.dump(cfg, {lineWidth: -1}));
console.log(\"retain: true set on \" + changed + \" device(s)\");
"' 2>/dev/null || echo "SKIP: could not set retain (see above)"
}

echo
echo "== retained device state =="
set_device_retain

echo "== starting zigbee2mqtt =="
"${COMPOSE[@]}" up -d zigbee2mqtt || exit 1

# --- complete the onboarding, once --------------------------------------
# A successful submit is what persists `onboarding: false`; until it happens
# the container sits on the onboarding page and never opens the serial port.
complete_onboarding() {
  local deadline=$((SECONDS + ONBOARD_TIMEOUT))
  while [ $SECONDS -lt $deadline ]; do
    if curl -fsS --max-time 10 -X POST "$BROKER_URL/submit" \
      -H 'Content-Type: application/json' \
      -d '{
            "mqtt": {"base_topic": "zigbee2mqtt", "server": "mqtt://mosquitto:1883"},
            "serial": {"port": "/dev/ttyUSB0", "adapter": "zstack"},
            "frontend": {"port": 8080, "enabled": true},
            "homeassistant": {"enabled": false}
          }' 2>/dev/null | grep -c '"success":true' | grep -qv '^0$'; then
      return 0
    fi
    sleep 3
  done
  return 1
}

coordinator_started() {
  # --tail, always. Z2M logs its whole Zigbee cluster definition on start --
  # megabytes of JSON -- so an unbounded `docker compose logs` here turns a
  # two-second grep into a multi-minute one and the script appears to hang.
  #
  # `grep -c`, NOT `grep -q`. With `set -o pipefail` (line 3) and `grep -q`, the
  # moment grep finds its first match it exits, docker compose logs is killed by
  # SIGPIPE and exits 141, and pipefail reports the whole pipeline as failed.
  # Measured: the same pipeline is exit 0 without pipefail and exit 255 with it.
  # So a coordinator that had been up for hours tested as "not started", the
  # script fell through to the onboarding branch, and /submit -- which is 404
  # forever once Z2M is running -- was polled for the full 180s timeout. This
  # is the second time this function produced a wrong answer for a healthy
  # system; the first was the `/submit` ordering bug above.
  #
  # `grep -c` consumes the whole stream, so docker exits 0 and the count is
  # the answer: >0 means started.
  [ "$("${COMPOSE[@]}" logs --tail 200 zigbee2mqtt 2>&1 |
    grep -c "Zigbee2MQTT started")" -gt 0 ]
}

wait_for_coordinator() {
  local deadline=$((SECONDS + ONBOARD_TIMEOUT))
  while [ $SECONDS -lt $deadline ]; do
    if coordinator_started; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# Order matters, and getting it wrong costs three minutes of apparent hang.
# `/submit` only answers while the onboarding server is up; once Z2M has
# started it is 404 forever. So the "is it already running?" check has to come
# FIRST -- otherwise the onboarding poll spins for the full timeout against an
# endpoint that will never answer again, and the script looks wedged rather
# than wrong. This exact ordering bug is why the first `--permit-join on` run
# printed "coordinator already started" and then sat there for 180s.
if coordinator_started; then
  echo "coordinator already started"
elif complete_onboarding; then
  echo "onboarding submitted"
  if ! wait_for_coordinator; then
    echo "FAILED: zigbee2mqtt did not report 'Zigbee2MQTT started'" >&2
    "${COMPOSE[@]}" logs --tail 30 zigbee2mqtt >&2
    exit 1
  fi
else
  echo "FAILED: could not reach the onboarding endpoint at $BROKER_URL/submit" >&2
  echo "Is zigbee2mqtt running? docker compose ps zigbee2mqtt" >&2
  "${COMPOSE[@]}" logs --tail 30 zigbee2mqtt >&2
  exit 1
fi

# --- permit_join --------------------------------------------------------
# Over MQTT, NOT over POST /submit. /submit only exists while the onboarding
# server is up -- it answers 404 once Z2M has started, which is the case
# whenever this is actually useful. Measured: "Cannot POST /submit".
#
# And the payload is a DURATION, not a boolean. Read out of
  # `bridge.js:355`: `Number.parseInt(message, 10)`, so `true` parses to NaN and
  # NaN means "disable" -- the response said `{"status":"ok"}` while the log said
  # "disabling joining new devices". An `on` that silently turns joining OFF is
  # worse than an error, because the operator walks away believing the window is
  # open. Hence a duration and no boolean at all: the window closes itself, and
  # nothing is left open by a forgotten flag.
#
# THE UNIT IS SECONDS, not minutes. Zigbee2MQTT's own documentation says
# "minutes" and so did this script's first version -- and it was wrong by a
# factor of 60. The code decides, at zigbee-herdsman `controller.js:283`:
#
#     assert(time <= 254, "Cannot permit join for more than 254 seconds.");
#     const timeMs = time * 1000;
#
# 254 is a uint8 on the wire, and the code that multiplies by 1000 is right
# next to the message that says "seconds". A request for 25 opened a window of
# 25 SECONDS: measured `permit_join_end` 20.8 s in the future after asking for
# 25, and the script helpfully printed "~0 min left" for it. Maximum is 254.
#
# So the operator-facing unit stays in minutes (pairing a device takes minutes,
# not seconds) and the conversion to seconds happens here, once, where the
# 254 ceiling can be enforced instead of discovered as a node assert.
permit_join() {
  local payload="$1"
  docker compose exec -T mosquitto mosquitto_pub -h localhost \
    -t "zigbee2mqtt/bridge/request/permit_join" -m "$payload"
}

if [ -n "$PERMIT_JOIN" ]; then
  echo
  case "$PERMIT_JOIN" in
    on) JOIN_MINUTES="${JOIN_MINUTES:-1}" ;;
    off) JOIN_MINUTES=0 ;;
    *)
      echo "FAILED: --permit-join takes 'on' or 'off'" >&2
      exit 2
      ;;
  esac

  # 254 s is a hard ceiling on the wire (uint8, and zigbee-herdsman asserts it).
  # Refused here with an explanation rather than crashing the Z2M container with
  # a node assertion from inside a request handler.
  JOIN_SECONDS=$(( JOIN_MINUTES * 60 ))
  if [ "$JOIN_SECONDS" -gt 254 ]; then
    echo "FAILED: ${JOIN_MINUTES} min = ${JOIN_SECONDS}s, and the Zigbee" >&2
    echo "coordinator accepts at most 254 SECONDS (4 min 14 s). The number is a" >&2
    echo "uint8 on the wire, so it cannot be larger." >&2
    echo "For a longer window, pair one device at a time and re-run." >&2
    exit 2
  fi

  echo "== permit_join: $JOIN_MINUTES min ($JOIN_SECONDS s) =="
  permit_join "$JOIN_SECONDS" || {
    echo "FAILED: could not reach the broker to set permit_join" >&2
    exit 1
  }

  # Confirm from the retained state, NOT from the response. The response said
  # "ok" while the coordinator did the opposite, so "ok" proves nothing here.
  #
  # And confirm the WINDOW, not just the flag: `permit_join: true` with a
  # `permit_join_end` already in the past means joining is over, which is what a
  # flag-only check would report as open. Measured: a run asking for 25 minutes
  # reported "~0 min left" because the retained `bridge/info` was still the copy
  # from before the request -- Z2M publishes it on change, not on demand, so
  # reading it immediately after a publish can return the previous value. Hence
  # retry until the end timestamp is in the future, rather than reading once and
  # believing it.
  flag=""
  end=""
  now_ms() { echo $(( $(date +%s) * 1000 )); }

  for _ in $(seq 1 20); do
    # Two fields, one line: the flag and the window end in epoch millis.
    # Written to a file rather than piped into `python3 -c` because the quotes
    # needed for the dict keys do not survive being nested inside the single
    # quotes of a shell string, and the failure is silent -- the extractor just
    # prints nothing and the check reports "unreachable" instead of a value.
    info="$(docker compose exec -T mosquitto mosquitto_sub -h localhost \
      -t 'zigbee2mqtt/bridge/info' -C 1 -W 3 2>/dev/null)"
    if [ -n "$info" ]; then
      printf '%s\n' "$info" > "$STATE_INPUT"
      state="$(python3 - "$STATE_INPUT" <<'PY'
import json
import sys

with open(sys.argv[1]) as fh:
    d = json.load(fh)
end = d.get("permit_join_end")
print("%s|%s" % (d.get("permit_join"), end if end is not None else "None"))
PY
)"
    else
      state=""
    fi

    if [ -z "$state" ]; then
      sleep 1
      continue
    fi

    flag="${state%%|*}"
    end="${state##*|}"

    # "False|None" is a legitimate answer, not a missing one: joining is off and
    # there is no window end because there is no window. It has to be able to
    # satisfy `--permit-join off`. Treating every `|None` as "not known yet" --
    # which is what the first version did -- meant the close path could never
    # match, looped to the timeout, and reported an empty flag.
    if [ "$JOIN_MINUTES" -eq 0 ]; then
      if [ "$flag" = "False" ]; then
        break
      fi
    elif [ "$flag" = "True" ] && [ "$end" != "None" ] && [ "$end" -gt "$(now_ms)" ]; then
      # An open request needs an unexpired window, not just the flag: true with
      # an end in the past is a window that has already closed.
      break
    fi
    sleep 1
  done

  if [ "$JOIN_MINUTES" -gt 0 ]; then
    if [ "$flag" = "True" ] && [ "${end:-0}" -gt "$(now_ms)" ]; then
      remaining=$(( (end - $(now_ms)) / 60000 ))
      echo "JOIN WINDOW OPEN for $JOIN_MINUTES minutes (~$remaining min left)."
      echo "Pair the devices now. It closes by itself -- no cleanup to forget."
    else
      echo "FAILED: asked for $JOIN_MINUTES minutes, coordinator reports" \
        "permit_join=$flag permit_join_end=$end" >&2
      echo "If that is False, joining is OFF and no device will pair." >&2
      exit 1
    fi
  else
    if [ "$flag" = "False" ]; then
      echo "join window closed"
    else
      echo "WARNING: asked to close the window, coordinator reports" \
        "permit_join=$flag permit_join_end=$end" >&2
    fi
  fi
fi

echo
echo "== state =="
"${COMPOSE[@]}" logs --tail 200 zigbee2mqtt 2>&1 |
  grep -E "Coordinator firmware version|devices are joined" || true
echo
echo "frontend: http://127.0.0.1:8090  (loopback only)"