import psutil
import re

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["sistema", "cpu", "ram", "memória", "disco", "armazenamento", "ocupação"]

# --- EDITAR AQUI ---
# Adiciona os pontos de montagem exatos dos discos que queres monitorizar.
# Ex: ["/", "/mnt/dados", "/home/media"]
DISCOS_MONITORIZADOS = [
    "/",
    "/mnt/data"  # <-- ADICIONA O TEU SEGUNDO DISCO AQUI
]
# --------------------

def format_bytes(b):
    """ Converte bytes para GB ou TB de forma legível. """
    if b >= 1024**4: # TB
        return f"{b / 1024**4:.1f} TB"
    return f"{b / 1024**3:.1f} GB"

def handle(user_prompt_lower, user_prompt_full):
    """ 
    Fornece estatísticas do sistema (CPU, RAM, e Discos definidos 
    em DISCOS_MONITORIZADOS). 
    """
    
    # --- Lógica de Disco ---
    # Ativa se o prompt incluir "disco", "armazenamento" ou "ocupação"
    if any(trigger in user_prompt_lower for trigger in ["disco", "armazenamento", "ocupação"]):
        print("Skill System: A verificar a ocupação dos discos...")
        try:
            if not DISCOS_MONITORIZADOS:
                return "A skill de sistema não tem discos configurados para monitorizar."

            response_lines = ["Estado dos discos:"]
            
            for path in DISCOS_MONITORIZADOS:
                try:
                    usage = psutil.disk_usage(path)
                    line = (
                        f"Disco {path}: {usage.percent}% usado "
                        f"({format_bytes(usage.used)} de {format_bytes(usage.total)})"
                    )
                    response_lines.append(line)
                except FileNotFoundError:
                    response_lines.append(f"Disco {path}: (Erro: Caminho não encontrado)")
                except Exception as e_disk:
                     response_lines.append(f"Disco {path}: (Erro: {e_disk})")
            
            return "\n".join(response_lines)

        except Exception as e:
            print(f"ERRO (Skill System/Disco): {e}")
            return "Desculpa, chefe, não consegui verificar a ocupação dos discos."

    # --- Lógica de CPU/RAM (Fallback) ---
    print("Skill System: A verificar CPU e RAM...")
    try:
        # psutil.cpu_percent(interval=None) dá o uso desde a última chamada
        # Chamamos uma vez com intervalo para "acordar"
        psutil.cpu_percent(interval=0.1) 
        # A segunda chamada dá um valor mais realista
        cpu = psutil.cpu_percent(interval=None)
        
        ram = psutil.virtual_memory()
        
        response = (
            f"Estado do sistema: "
            f"CPU a {cpu}% e "
            f"RAM a {ram.percent}%."
        )
        return response
        
    except Exception as e:
        print(f"ERRO (Skill System/CPU): {e}")
        return "Não consegui verificar o estado do sistema."
