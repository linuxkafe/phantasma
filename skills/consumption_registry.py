"""
Registrador de consumo acumulado por skill/dispositivo.

Este módulo mantém um registo persistente do consumo acumulado (em Wh/kWh)
por skill/dispositivo, amostrando a potência a intervalos regulares e
integrando ao longo do tempo (regra do trapézio).

Os dados são guardados em JSON no diretório de cache e sobrevivem a reinícios.
"""

import json
import os
import time
import threading
from pathlib import Path
from typing import Dict, Optional
from collections import defaultdict

import config


class ConsumptionRegistry:
    """Registo de consumo acumulado por skill/dispositivo."""

    def __init__(self):
        self.cache_file = str(Path(config.CACHE_DIR) / "consumption_registry.json")
        self._lock = threading.Lock()
        self._data: Dict[str, Dict] = {}  # skill_name -> {device_name: {energy_wh, last_power_w, last_ts, samples}}
        self._load()

    def _ensure_dir(self):
        directory = os.path.dirname(self.cache_file)
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

    def _load(self):
        """Carrega o registo do disco."""
        import os
        import json
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def _save(self):
        """Guarda o registo no disco (escrita atómica)."""
        import os
        import json
        self._ensure_dir()
        try:
            tmp = self.cache_file + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(self._data, f, separators=(',', ':'))
            os.replace(tmp, self.cache_file)
            os.chmod(self.cache_file, 0o666)
        except Exception as e:
            print(f"[ConsumptionRegistry] Erro ao guardar: {e}")

    def record_power(self, skill_name: str, device_name: str, power_w: float, timestamp: Optional[float] = None):
        """Regista uma leitura de potência e acumula energia.

        Args:
            skill_name: Nome da skill (ex: 'zigbee', 'ewelink', 'tuya')
            device_name: Nome do dispositivo (ex: 'forno', 'carregador do carro')
            power_w: Potência instantânea em Watts
            timestamp: Timestamp Unix (opcional, usa time.time() se omitido)
        """
        if power_w is None or power_w < 0:
            return

        if timestamp is None:
            timestamp = time.time()

        key = f"{skill_name}:{device_name}"

        with self._lock:
            if key not in self._data:
                self._data[key] = {
                    'skill': skill_name,
                    'device': device_name,
                    'energy_wh': 0.0,
                    'last_power_w': 0.0,
                    'last_ts': timestamp,
                    'samples': 0
                }

            entry = self._data[key]
            last_ts = entry.get('last_ts', timestamp)
            last_power = entry.get('last_power_w', 0.0)

            # Integração pelo método do trapézio: energia = (P1 + P2) / 2 * dt
            dt_hours = (timestamp - last_ts) / 3600.0  # horas
            if dt_hours > 0:
                avg_power = (last_power + power_w) / 2.0
                energy_wh = avg_power * dt_hours
                entry['energy_wh'] += energy_wh

            entry['last_power_w'] = power_w
            entry['last_ts'] = timestamp
            entry['samples'] = entry.get('samples', 0) + 1

            # Guarda periodicamente (a cada 10 amostras)
            if entry['samples'] % 10 == 0:
                self._save()

    def get_energy_kwh(self, skill_name: str, device_name: str) -> float:
        """Retorna a energia acumulada em kWh."""
        key = f"{skill_name}:{device_name}"
        with self._lock:
            entry = self._data.get(key, {})
            return round(entry.get('energy_wh', 0.0) / 1000.0, 3)

    def get_all(self) -> Dict[str, Dict]:
        """Retorna todo o registo (cópia)."""
        with self._lock:
            return {k: dict(v) for k, v in self._data.items()}

    def get_summary(self) -> Dict[str, float]:
        """Retorna resumo de energia por skill em kWh."""
        with self._lock:
            summary = {}
            for key, entry in self._data.items():
                skill = entry.get('skill', 'unknown')
                energy_kwh = entry.get('energy_wh', 0.0) / 1000.0
                summary[skill] = summary.get(skill, 0.0) + energy_kwh
            return {k: round(v, 3) for k, v in summary.items()}

    def reset_device(self, skill_name: str, device_name: str):
        """Zera o contador de um dispositivo específico."""
        key = f"{skill_name}:{device_name}"
        with self._lock:
            if key in self._data:
                self._data[key]['energy_wh'] = 0.0
                self._save()

    def force_save(self):
        """Força gravação imediata."""
        with self._lock:
            self._save()


# Instância global
_consumption_registry: Optional['ConsumptionRegistry'] = None


def get_consumption_registry() -> 'ConsumptionRegistry':
    """Retorna a instância global do registo (singleton)."""
    global _consumption_registry
    if _consumption_registry is None:
        _consumption_registry = ConsumptionRegistry()
    return _consumption_registry