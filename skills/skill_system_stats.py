import psutil
import os

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["como está o sistema", "estado do sistema", "system stats", "monitorização"]
# -----------------------------

def handle(user_prompt_lower, user_prompt_full):
    """
    Fornece estatísticas de uso do sistema (Carga, CPU, RAM, Disco).
    """
    
    print("A obter estatísticas do sistema...")
    
    try:
        # 1. Load Average (Média de 1 minuto) e Cores
        # (Usa psutil.getloadavg() que é standard)
        load_1m, _, _ = psutil.getloadavg()
        cores = psutil.cpu_count()
        load_percent = (load_1m / cores) * 100 # Carga de 1 min como percentagem
        
        # 2. Uso de CPU
        # (interval=0.5 dá uma medição de 0.5s, mais fiável que 0.0)
        cpu_usage = psutil.cpu_percent(interval=0.5)
        
        # 3. Uso de Memória
        mem = psutil.virtual_memory()
        mem_usage = mem.percent
        
        # 4. Uso de Disco (partição /)
        disk = psutil.disk_usage('/')
        disk_usage = disk.percent

        # Formata a resposta
        response = f"O sistema está assim: Carga: {load_percent:.1f}%. CPU: {cpu_usage:.1f}%. Memória: {mem_usage:.1f}%. Disco: {disk_usage:.1f}%."
        
        return response

    except Exception as e:
        print(f"ERRO ao obter stats do sistema: {e}")
        return "Desculpa, não consegui verificar o estado do sistema."
