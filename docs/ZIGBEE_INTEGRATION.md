# Zigbee2MQTT Integration for pHantasma

## Overview

This document describes how to add **local Zigbee device control** to pHantasma using Zigbee2MQTT. This enables control of Zigbee devices (like the oven plug currently on Cloogy Cloud) without vendor cloud dependencies.

## Why Zigbee2MQTT?

| Current State | With Zigbee2MQTT |
|---------------|------------------|
| Oven plug on Cloogy Cloud → 401 on control | Direct local control via MQTT |
| No state feedback (encrypted reply) | Real-time state + power monitoring |
| Vendor account required | Fully offline, no accounts |
| Single coordinator lock-in | Own coordinator, multi-vendor support |

## Hardware Requirements

### Recommended Coordinator (2024)

| Device | Chip | Price | Notes |
|--------|------|-------|-------|
| **Sonoff ZBDongle-E** | CC2652P | ~€25 | Best range, external antenna, easy flash |
| **Sonoff ZBDongle-P** | CC2532 | ~€18 | Good, internal antenna |
| **Texas Instruments CC2531** | CC2531 | ~€12 | Needs CC Debugger + cable to flash |

**Buy:** Sonoff ZBDongle-E (CC2652P) — best balance of range, price, and ease of use.

### Network Topology

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Zigbee Devices │────▶│  ZBDongle-E      │────▶│  Zigbee2MQTT    │
│  (oven plug,    │     │  (USB /dev/      │     │  (Docker/       │
│   sensors,      │     │   ttyUSB0)       │     │   bare metal)   │
│   switches)     │     │                  │     │  Port 1883      │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  pHantasma      │◀───│  Mosquitto MQTT  │◀───│  skill_zigbee2mqtt│
│  (voice/UI)     │     │  Broker          │     │  (NEW skill)    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## Installation

### 1. Mosquitto MQTT Broker

```bash
# Docker (recommended)
docker run -d \
  --name mosquitto \
  --restart unless-stopped \
  -p 1883:1883 \
  -p 9001:9001 \
  -v /opt/phantasma/mosquitto/config:/mosquitto/config \
  -v /opt/phantasma/mosquitto/data:/mosquitto/data \
  -v /opt/phantasma/mosquitto/log:/mosquitto/log \
  eclipse-mosquitto:2

# Config: /opt/phantasma/mosquitto/config/mosquitto.conf
cat > /opt/phantasma/mosquitto/config/mosquitto.conf <<'EOF'
listener 1883
allow_anonymous true
persistence true
persistence_location /mosquitto/data
log_dest file /mosquitto/log/mosquitto.log
log_type all
EOF
```

### 2. Zigbee2MQTT

```bash
# Find coordinator device
ls -la /dev/serial/by-id/
# Example: usb-ITEAD_SONOFF_Zigbee_3.0_USB_Dongle_Plus_... -> ../../ttyUSB0

# Docker (recommended)
docker run -d \
  --name zigbee2mqtt \
  --restart unless-stopped \
  --network=host \
  -v /opt/phantasma/zigbee2mqtt:/app/data \
  --device=/dev/ttyUSB0:/dev/ttyUSB0 \
  koenkk/zigbee2mqtt:latest

# Config: /opt/phantasma/zigbee2mqtt/configuration.yaml
cat > /opt/phantasma/zigbee2mqtt/configuration.yaml <<'EOF'
homeassistant: false
permit_join: true
mqtt:
  base_topic: zigbee2mqtt
  server: 'mqtt://localhost:1883'
serial:
  port: /dev/ttyUSB0
  adapter: ezsp
advanced:
  network_key: GENERATE  # First run generates, then copy to .env
  pan_id: 6754
  channel: 11
frontend:
  port: 8080
devices:
  '0x00124b00023771d1':  # Oven plug IEEE address (from Cloogy device list)
    friendly_name: forno
    retain: true
EOF
```

### 3. Pair the Oven Plug

1. **Unpair from Cloogy hub**: Hold plug button 10s until LED blinks rapidly
2. **Enable pairing**: Zigbee2MQTT frontend → "Permit join" or `permit_join: true` in config
3. **Pair**: Plug in oven plug near dongle, it should appear in Zigbee2MQTT
4. **Verify**: Check `zigbee2mqtt/forno` topic in MQTT Explorer

### 4. pHantasma Skill

Create `skills/skill_zigbee2mqtt.py`:

```python
"""Zigbee2MQTT skill — local Zigbee device control via MQTT."""
import json
import os
import paho.mqtt.client as mqtt
from skills.base import Skill, SkillContext, TriggerType

NAME = "zigbee2mqtt"
TRIGGERS = ["forno", "ficha", "tomada", "zigbee"]
TRIGGER_TYPE = TriggerType.CONTAINS
PRIORITY = 70  # Wins over Tasmota (50), Chacon (60)

MQTT_HOST = os.getenv("ZIGBEE2MQTT_MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("ZIGBEE2MQTT_MQTT_PORT", "1883"))
BASE_TOPIC = os.getenv("ZIGBEE2MQTT_BASE_TOPIC", "zigbee2mqtt")

_client = None
_states = {}

def _mqtt_client():
    global _client
    if _client is None:
        _client = mqtt.Client()
        _client.on_message = _on_message
        _client.connect(MQTT_HOST, MQTT_PORT, 60)
        _client.subscribe(f"{BASE_TOPIC}/+/get")
        _client.subscribe(f"{BASE_TOPIC}/bridge/devices")
        _client.loop_start()
    return _client

def _on_message(client, userdata, msg):
    try:
        topic = msg.topic
        payload = json.loads(msg.payload.decode())
        if topic.endswith("/get"):
            device = topic.split("/")[-2]
            _states[device] = payload
    except Exception:
        pass

def _publish(device, payload):
    _mqtt_client().publish(f"{BASE_TOPIC}/{device}/set", json.dumps(payload))

def handle(text, context):
    text = text.lower()
    if "forno" not in text and "ficha" not in text and "tomada" not in text:
        return None

    is_on = any(w in text for w in ["liga", "acende", "on"])
    is_off = any(w in text for w in ["desliga", "apaga", "off"])

    if is_off:
        _publish("forno", {"state": "OFF"})
        return "Forno desligado."
    if is_on:
        _publish("forno", {"state": "ON"})
        return "Forno ligado."

    # Status
    state = _states.get("forno", {}).get("state", "unknown")
    power = _states.get("forno", {}).get("power", "?")
    return f"Forno: {state}, {power}W"

def get_status_for_device(nickname):
    if nickname.lower() != "forno":
        return {"state": "unreachable"}
    s = _states.get("forno", {})
    return {"state": s.get("state", "unknown").lower(), "power_w": s.get("power")}
```

### 5. Environment Variables

Add to `/opt/phantasma/.env`:

```bash
# Zigbee2MQTT
ZIGBEE2MQTT_MQTT_HOST=localhost
ZIGBEE2MQTT_MQTT_PORT=1883
ZIGBEE2MQTT_BASE_TOPIC=zigbee2mqtt
```

### 6. Deploy & Restart

```bash
cd /home/seyon/dev/pHantasma
./scripts/deploy.sh
sudo systemctl restart phantasma
```

## Device Mapping

| Cloogy Device | IEEE Address | Zigbee2MQTT Friendly Name | Capabilities |
|---------------|--------------|---------------------------|--------------|
| Forno (plug) | 0x00124b00023771d1 | `forno` | ON/OFF, power (W), energy (kWh), voltage, current |
| Casa (clamp) | 0x00124b000204eb88 | `casa` | Power (W) — read only |

## Testing

```bash
# MQTT test
mosquitto_pub -h localhost -t 'zigbee2mqtt/forno/set' -m '{"state": "ON"}'
mosquitto_sub -h localhost -t 'zigbee2mqtt/forno' -C 1

# Skill test
python3 -c "
import sys; sys.path.insert(0, '.')
from skills.loader import SkillLoader
from skills import SkillContext
loader = SkillLoader('skills', SkillContext())
loader.load_all()
print(loader.execute_skill('liga o forno'))
print(loader.execute_skill('estado do forno'))
"
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Device won't pair | Reset plug (10s hold), bring closer to dongle, check `permit_join: true` |
| No state updates | Check Zigbee2MQTT logs: `docker logs zigbee2mqtt -f` |
| MQTT connection refused | Verify Mosquitto running: `docker ps`, check port 1883 |
| Skill not loading | Check `pip install paho-mqtt`, verify skill priority |

## Security

- **Local only**: MQTT bound to localhost (127.0.0.1) by default
- **No auth needed**: Zigbee2MQTT runs on trusted LAN
- **External access**: If exposing MQTT, enable auth in Mosquitto config

## References

- Zigbee2MQTT docs: https://www.zigbee2mqtt.io/
- Supported devices: https://www.zigbee2mqtt.io/supported-devices/
- Sonoff ZBDongle-E flashing: https://www.zigbee2mqtt.io/guide/adapters/sonoff_zbdongle_e.html