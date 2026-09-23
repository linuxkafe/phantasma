import psutil
import time
import threading
import json
import os
import config

TRIGGER_TYPE = "contains"
# Gatilhos de sistema
TRIGGERS = ["estado do sistema", "cpu", "ram", "memória", "disco", "armazenamento", "status do servidor"]

CACHE_FILE = "/opt/phantasma/cache/system_stats.json"
POLL_INTERVAL = 60

FSTYPE_IGNORADOS = ["squashfs", "tmpfs", "devtmpfs", "loop", "overlay", "iso9660", "autofs"]
MOUNTPOINT_IGNORADOS = ["/boot/efi"]

def _save_cache(data):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, 'w') as f: json.dump(data, f)
    except: pass

def _load_cache():
    if not os.path.exists(CACHE_FILE): return None
    try:
        with open(CACHE_FILE, 'r') as f: return json.load(f)
    except: return None

def _collect_stats():
    stats = {"timestamp": time.time()}
    try:
        stats["cpu_percent"] = psutil.cpu_percent(interval=1)
        stats["ram_percent"] = psutil.virtual_memory().percent
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                stats["temperature"] = max(s.current for l in temps.values() for s in l if s.current)
    except: pass
    return stats

def init_skill_daemon():
    def loop():
        while True:
            _save_cache(_collect_stats())
            time.sleep(POLL_INTERVAL)
    threading.Thread(target=loop, daemon=True).start()

def handle(user_prompt_lower, user_prompt_full):
    # CRÍTICO: Só responde a temperatura se for da CPU ou Sistema.
    # Se houver uma localização (WC, Sala, etc), ignora e deixa para a skill_tuya.
    locations = ["sala", "quarto", "wc", "cozinha", "entrada"]
    if any(loc in user_prompt_lower for loc in locations):
        return None

    is_explicit = any(x in user_prompt_lower for x in ["cpu", "sistema", "servidor", "memória", "disco"])
    if not is_explicit and "temperatura" not in user_prompt_lower:
        return None

    data = _load_cache()
    if not data: return "Ainda a ler as entranhas da máquina..."

    cpu = data.get("cpu_percent", 0)
    ram = data.get("ram_percent", 0)
    resp = f"O sistema respira a {cpu:.0f}% de CPU e {ram:.0f}% de RAM."
    if "temperature" in data:
        resp += f" Temperatura interna: {data['temperature']:.0f} graus."
    return resp
