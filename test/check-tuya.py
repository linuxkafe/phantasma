#!/usr/bin/env python

# --- INÍCIO DO TRUQUE DE IMPORTAÇÃO ---
# (Necessário se este script estiver na pasta /test)
import sys
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# --- FIM DO TRUQUE DE IMPORTAÇÃO ---

import tinytuya
import config # Importamos para ter acesso a config.TUYA_DEVICES
import time
import json
import logging
import subprocess
import re

# --- CONFIGURAÇÃO DO SCRIPT DE TESTE ---
# (Listas locais, como pediste)

# IPs que sabemos que NÃO são Tuya e que queremos ignorar
TUYA_SCAN_EXCLUDE_IPS = [
    "10.0.0.1",   # Router
    "10.0.0.111", # O teu servidor, por exemplo
    "10.0.0.123",
    "10.0.0.120",
    "10.0.0.107",
    "10.0.0.101",
    "10.0.0.102",
    "10.0.0.114",
    "10.0.0.100"
]

# IPs estáticos que queremos verificar SEMPRE, mesmo que o arp-scan falhe
TUYA_SCAN_EXTRA_IPS = [
    "10.0.0.121",
    "10.0.0.118",
    "10.0.0.103",
    "10.0.0.104",
    "10.0.0.105",
    "10.0.0.106",
    "10.0.0.109",
    "10.0.0.111",
    "10.0.0.108",
    "10.0.0.116",
    "10.0.0.112",
    "10.0.0.114",
    "10.0.0.113"
]

# Timeout de ligação para cada tentativa
CONNECTION_TIMEOUT = 2
# ---------------------------------------

# Desliga o logging verboso do tinytuya
logging.getLogger("tinytuya").setLevel(logging.CRITICAL)

def run_arp_scan():
    """
    Usa arp-scan para encontrar TODOS os IPs ativos na rede local.
    """
    active_ips = []
    print("A iniciar scan 'arp-scan --localnet' (requer sudo)...")

    command = ['sudo', 'arp-scan', '--localnet']

    try:
        ip_regex = re.compile(r"^([\d\.]+)\s+")

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(timeout=120)

        if process.returncode != 0:
            print(f"ERRO: arp-scan falhou (código: {process.returncode}).")
            print(f"Stderr: {stderr}")
            if "Can't open interface" in stderr or "Operation not permitted" in stderr:
                 print("\n************************************************************")
                 print("   ERRO: 'arp-scan' precisa de privilégios 'sudo'.")
                 print("   Tenta executar: sudo venv/bin/python test/check_tuya.py")
                 print("************************************************************")
            return None

        for line in stdout.splitlines():
            match = ip_regex.search(line)
            if match:
                ip = match.group(1)
                if ip not in active_ips:
                    active_ips.append(ip)

        active_ips = [ip for ip in active_ips if not (ip.endswith('.255') or ip.endswith('.0'))]

        print(f"arp-scan concluído. Encontrados {len(active_ips)} IPs ativos na rede.")
        if active_ips:
            print(f"Lista (arp-scan): {active_ips}...")
        return active_ips

    except FileNotFoundError:
        print("\nERRO CRÍTICO: Comando 'arp-scan' não encontrado.")
        print("Por favor, instala: sudo apt install arp-scan")
        return None
    except subprocess.TimeoutExpired:
        print("\nERRO: Scan arp-scan demorou demasiado tempo (timeout).")
        return None
    except Exception as e:
        print(f"\nERRO CRÍTICO durante o scan arp-scan: {e}")
        return None

def verify_devices(ips_to_check, known_devices_config):
    """
    Tenta ligar-se a cada IP da lista com cada credencial conhecida.
    """
    if not known_devices_config:
        print("\nERRO: 'TUYA_DEVICES' está vazio no config.py.")
        return

    print("\n" + "=" * 50)
    print(f"A verificar {len(known_devices_config)} dispositivos contra {len(ips_to_check)} IPs...")
    print(f"Isto pode demorar (até {len(known_devices_config) * len(ips_to_check)} tentativas)...")
    print("=" * 50)

    devices_to_find = []
    for name, details in known_devices_config.items():
        details['name'] = name
        devices_to_find.append(details)

    found_devices_map = {} # Key: device_id, Value: IP real
    ip_report = {} # Key: ip, Value: string (Found/Failed)

    # Loop principal: M x N (IPs x Credenciais)
    for ip in ips_to_check:
        if ip in ip_report: continue

        print(f"\n--- A testar IP: {ip} ---")

        device_found_on_this_ip = False
        for device in devices_to_find:
            dev_id = device['id']
            dev_key = device['key']
            dev_name = device['name']

            if dev_id in found_devices_map:
                continue

            print(f"  -> A tentar credencial para: '{dev_name}' (id: ...{dev_id[-6:]})", end="", flush=True)

            try:
                d = tinytuya.OutletDevice(dev_id, ip, dev_key)
                d.set_socketTimeout(CONNECTION_TIMEOUT)
                d.set_version(3.3)

                status = d.status()

                if 'dps' in status:
                    print(" ... SUCESSO!")
                    found_devices_map[dev_id] = ip
                    ip_report[ip] = f"SUCESSO ({dev_name})"
                    device_found_on_this_ip = True
                    print_device_details(device, ip, status)
                    break
                else:
                    print(" ... Falhou (Resposta inválida, não-Tuya)")

            except Exception as e:
                print(f" ... Falhou ({type(e).__name__})")
                time.sleep(0.05)

        if not device_found_on_this_ip:
            if ip not in ip_report:
                 ip_report[ip] = "FALHOU (Nenhuma credencial funcionou)"
            print("  -> Nenhuma credencial conhecida funcionou para este IP.")

    # Resumo
    print("\n" + "=" * 50)
    print("Resumo do Diagnóstico:")
    print("=" * 50)

    found_count = 0
    for name, details in known_devices_config.items():
        dev_id = details['id']
        known_ip = details.get('ip', 'N/A')

        if dev_id in found_devices_map:
            found_count += 1
            real_ip = found_devices_map[dev_id]
            print(f"\n[+] Encontrado: '{name}'")
            print(f"    -> IP Real:   {real_ip}")
            if real_ip != known_ip and 'x' not in known_ip.lower():
                print(f"    -> AVISO: Configurado como {known_ip}. Atualiza o config.py!")
        else:
            print(f"\n[-] NÃO Encontrado: '{name}'")
            print(f"    -> ID:    {dev_id}")
            print(f"    -> Verifique se está ligado e se a 'key' está correta.")

    print("\n" + "=" * 50)
    print(f"Scan concluído. {found_count} de {len(known_devices_config)} dispositivos foram encontrados.")

def print_device_details(device_config, found_ip, status):
    """ Imprime os detalhes de um dispositivo acabado de encontrar """
    print("\n      --- Detalhes do Dispositivo Encontrado ---")
    print(f"      Nome:        {device_config['name']}")
    print(f"      IP Real:     {found_ip}")
    print(f"      ID:          {device_config['id']}")

    known_ip_guess = device_config.get('ip', 'N/A')
    if found_ip != known_ip_guess and 'x' not in known_ip_guess.lower():
         print(f"      AVISO:       IP no config.py ({known_ip_guess}) está incorreto.")

    try:
        d = tinytuya.OutletDevice(device_config['id'], found_ip, device_config['key'])
        d.set_socketTimeout(CONNECTION_TIMEOUT)
        d.set_version(3.3)
        info = d.deviceinfo()
        print(f"      MAC Address: {info.get('mac', 'N/A')}")
    except Exception:
        print("      MAC Address: (Falha ao obter)")

    print("\n      Estado Atual (DPS):")
    print(json.dumps(status.get('dps', {}), indent=8))
    print("      ----------------------------------------")


if __name__ == "__main__":
    print("=== Script de Diagnóstico TUYA (arp-scan + Tinytuya) ===")

    # 1. Encontra IPs ativos com arp-scan
    active_ips_set = set(run_arp_scan() or []) # 'or []' evita erro se o scan falhar

    # --- LÓGICA DE FILTRAGEM (Usando as listas LOCAIS) ---

    # 2. Adicionar IPs extra
    if TUYA_SCAN_EXTRA_IPS:
        print(f"A adicionar {len(TUYA_SCAN_EXTRA_IPS)} IPs extra locais: {TUYA_SCAN_EXTRA_IPS}")
        active_ips_set.update(TUYA_SCAN_EXTRA_IPS)

    # 3. Remover IPs excluídos
    if TUYA_SCAN_EXCLUDE_IPS:
        print(f"A excluir {len(TUYA_SCAN_EXCLUDE_IPS)} IPs locais: {TUYA_SCAN_EXCLUDE_IPS}")
        active_ips_set.difference_update(TUYA_SCAN_EXCLUDE_IPS)

    # Converter de volta para lista
    final_ips_to_scan = sorted(list(active_ips_set))

    # --- FIM DA LÓGICA DE FILTRAGEM ---

    if final_ips_to_scan:
        print(f"\nLista final de IPs a verificar ({len(final_ips_to_scan)}): {final_ips_to_scan}")
        # 4. Tenta as credenciais na lista filtrada
        #    (Puxa a lista de dispositivos do config.py)
        verify_devices(final_ips_to_scan, config.TUYA_DEVICES)
    elif active_ips_set is None:
        print("\nScan arp-scan falhou. A abortar.")
    else:
        print("\nNenhum IP ativo encontrado (ou todos foram excluídos). A parar.")

    print("\n=== Diagnóstico Concluído ===")
