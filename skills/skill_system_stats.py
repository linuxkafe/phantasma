import psutil
import time
import threading
import json
import os
import config

# --- Configuração ---
TRIGGER_TYPE = "contains"
# REMOVIDO "ocupação" para evitar o falso positivo com o poema.
TRIGGERS = ["sistema", "cpu", "ram", "memória", "disco", "armazenamento", "temperatura", "status do servidor"]

CACHE_FILE = "/opt/phantasma/cache/system_stats.json"
POLL_INTERVAL = 60  # Atualiza a cada 60 segundos

# Filtros de Disco
FSTYPE_IGNORADOS = ["squashfs", "tmpfs", "devtmpfs", "loop", "overlay", "iso9660", "autofs"]
MOUNTPOINT_IGNORADOS = ["/boot/efi"]

# --- Helpers de Cache ---
def _ensure_permissions():
    if os.path.exists(CACHE_FILE):
        try: os.chmod(CACHE_FILE, 0o666)
        except: pass

def _save_cache(data):
    try:
        # Garante diretório
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, 'w') as f: json.dump(data, f)
        os.replace(tmp, CACHE_FILE)
        _ensure_permissions()
    except Exception as e:
        print(f"[SystemStats] Erro ao gravar cache: {e}")

def _load_cache():
    if not os.path.exists(CACHE_FILE): return None
    try:
        with open(CACHE_FILE, 'r') as f: return json.load(f)
    except: return None

# --- Coleta de Dados (Lógica do Daemon) ---

def _format_bytes(b):
    if b >= 1024**4: return f"{b / 1024**4:.1f} TB"
    if b >= 1024**3: return f"{b / 1024**3:.1f} GB"
    return f"{b / 1024**2:.1f} MB"

def _get_temperature():
    try:
        if not hasattr(psutil, "sensors_temperatures"): return None
        temps = psutil.sensors_temperatures()
        if not temps: return None
        
        # Procura a temperatura mais alta (normalmente o CPU Package)
        max_temp = 0
        for sensor_list in temps.values():
            for sensor in sensor_list:
                if sensor.current > max_temp:
                    max_temp = sensor.current
        return max_temp if max_temp > 0 else None
    except: return None

def _collect_stats():
    """ Recolhe todos os dados do sistema num dicionário estruturado. """
    stats = {"timestamp": time.time()}
    
    # 1. CPU & RAM
    try:
        stats["cpu_percent"] = psutil.cpu_percent(interval=1)
        stats["ram_percent"] = psutil.virtual_memory().percent
        
        temp = _get_temperature()
        if temp: stats["temperature"] = temp
    except: pass

    # 2. Discos
    disks = []
    try:
        partitions = psutil.disk_partitions()
        disk_count = 0
        for part in partitions:
            if part.fstype in FSTYPE_IGNORADOS or part.mountpoint in MOUNTPOINT_IGNORADOS:
                continue
            
            try:
                usage = psutil.disk_usage(part.mountpoint)
                if usage.total > 0:
                    disk_count += 1
                    disks.append({
                        "id": disk_count,
                        "mount": part.mountpoint,
                        "free_gb": usage.free / (1024**3),
                        "free_human": _format_bytes(usage.free),
                        "percent": usage.percent
                    })
            except: pass
    except: pass
    
    stats["disks"] = disks
    return stats

# --- Loop do Daemon ---
def _poll_loop():
    # Executa uma vez no arranque
    first_run = _collect_stats()
    _save_cache(first_run)
    
    while True:
        time.sleep(POLL_INTERVAL)
        try:
            data = _collect_stats()
            _save_cache(data)
        except Exception as e:
            print(f"[SystemStats] Erro no loop: {e}")

def init_skill_daemon():
    print("[SystemStats] A iniciar monitorização de recursos...")
    threading.Thread(target=_poll_loop, daemon=True).start()

# --- Handler de Voz ---
def handle(user_prompt_lower, user_prompt_full):
    """ Lê a cache e responde. """
    
    # Ignorar falsos positivos se a frase for muito longa e não for explícita
    # (Proteção extra para poemas ou conversas longas)
    if len(user_prompt_full.split()) > 10 and "sistema" not in user_prompt_lower:
        return None

    data = _load_cache()
    if not data:
        return "Ainda estou a recolher os dados do sistema. Tenta daqui a pouco."

    # 1. Pedido específico de DISCO
    if any(x in user_prompt_lower for x in ["disco", "armazenamento"]):
        if not data.get("disks"): return "Não detetei discos monitorizáveis."
        
        res = []
        for d in data["disks"]:
            res.append(f"Disco {d['id']} tem {d['free_human']} livres")
        return ", ".join(res) + "."

    # 2. Pedido específico de CPU/RAM
    if any(x in user_prompt_lower for x in ["cpu", "ram", "memória", "temperatura"]):
        cpu = data.get("cpu_percent", 0)
        ram = data.get("ram_percent", 0)
        resp = f"Processador a {cpu:.1f}%, memória a {ram}%."
        
        if "temperature" in data:
            resp += f" Temperatura {data['temperature']:.0f} graus."
        return resp

    # 3. Pedido Geral ("Como está o sistema?")
    cpu = data.get("cpu_percent", 0)
    ram = data.get("ram_percent", 0)
    msg = f"Sistema estável. CPU a {cpu:.0f}%, RAM a {ram}%."
    
    if "temperature" in data:
        msg += f" Temp {data['temperature']:.0f}°C."
        
    if data.get("disks"):
        # Resumo breve dos discos para não ser chato
        free_space = data["disks"][0]["free_human"]
        msg += f" Disco principal com {free_space} livres."
        
    return msg
