#!/usr/bin/env python3
import socket
import tinytuya
import json
import time
import os
import sys
import threading

# Adiciona o diretório atual ao path para importar o config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config

# --- Configuração ---
CACHE_FILE = "/opt/phantasma/tuya_cache.json"
PORTS_TO_LISTEN = [6666, 6667] 

# Cache em memória para evitar polling excessivo
LAST_POLL = {} 
POLL_COOLDOWN = 5 

# --- Funções de Dados ---
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_cache(data):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[ERRO IO] Ao guardar cache: {e}")

def get_device_name_by_ip(ip):
    if not hasattr(config, 'TUYA_DEVICES'):
        return None, None
    for name, details in config.TUYA_DEVICES.items():
        if details.get('ip') == ip:
            return name, details
    return None, None

# --- Lógica de Leitura Silenciosa ---
def poll_device_task(name, details, force=False):
    """ Tenta conectar e ler o estado do dispositivo """
    ip = details.get('ip')
    
    if not force:
        if time.time() - LAST_POLL.get(name, 0) < POLL_COOLDOWN:
            return
    
    LAST_POLL[name] = time.time()
    # REMOVIDO O PRINT "A tentar ler..." para não poluir

    # Tenta várias versões
    versions = [3.3, 3.1, 3.4, 3.2]
    success = False
    data_dps = None
    error_msg = ""

    for ver in versions:
        try:
            d = tinytuya.OutletDevice(details['id'], ip, details['key'])
            d.set_socketTimeout(3)
            d.set_version(ver)
            status = d.status()
            
            if 'dps' in status:
                data_dps = status['dps']
                success = True
                break
            elif status.get('Err') == '905':
                error_msg = "Erro 905 (Chave/IP)"
                break
        except Exception as e:
            error_msg = str(e)
            continue

    if success and data_dps:
        try:
            current_cache = load_cache()
            prev_data = current_cache.get(name, {})
            prev_dps = prev_data.get('dps')

            # Atualiza o objeto cache
            current_cache[name] = {
                "dps": data_dps,
                "timestamp": time.time()
            }
            
            # LÓGICA DE LOG INTELIGENTE:
            # Só imprime se os dados mudaram OU se foi um scan forçado (warm-up)
            if prev_dps != data_dps:
                # Detetámos mudança (ex: temperatura subiu)
                print(f"[NOVO] '{name}': {data_dps}")
                save_cache(current_cache)
            elif force:
                # Warm-up: mostra que está vivo
                print(f"[OK] '{name}' (Online)")
                save_cache(current_cache)
            else:
                # Dados iguais (Heartbeat). Atualiza o ficheiro silenciosamente (para o timestamp)
                save_cache(current_cache)
                
        except Exception as e:
            print(f"[ERRO Cache] {e}")
    else:
        # Se falhar silenciosamente no dia-a-dia é normal (udp packet loss),
        # mas se for force (warm-up) ou erro crítico, convém saber.
        if force or "905" in error_msg:
             print(f"[FALHA] '{name}' ({ip}): {error_msg}")

# --- Listener UDP ---
def udp_listener(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('', port))
        print(f"[Daemon] À escuta na porta UDP {port}...")
    except Exception as e:
        print(f"[ERRO] Falha ao bind na porta {port}: {e}")
        return

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            ip = addr[0]
            name, details = get_device_name_by_ip(ip)
            if name:
                # Dispara leitura silenciosa
                threading.Thread(target=poll_device_task, args=(name, details, False)).start()
        except Exception as e:
            print(f"[Listener {port}] Erro: {e}")
            time.sleep(1)

# --- Inicialização ---
if __name__ == "__main__":
    print("--- Phantasma Tuya Listener (Modo Silencioso) ---")
    
    # 1. Warm-up: Scan explícito no arranque (mostra output)
    if hasattr(config, 'TUYA_DEVICES'):
        print("[Daemon] A verificar dispositivos...")
        for name, details in config.TUYA_DEVICES.items():
            threading.Thread(target=poll_device_task, args=(name, details, True)).start()

    # 2. Inicia listeners
    threads = []
    for port in PORTS_TO_LISTEN:
        t = threading.Thread(target=udp_listener, args=(port,))
        t.daemon = True
        t.start()
        threads.append(t)
        
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("A sair...")
