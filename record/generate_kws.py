import os
import sys
from pocketsphinx import get_model_path

# --- Configuração (Deve ser igual à do assistant.py) ---
# Usa a tua hotword preferida, em MAIÚSCULAS
HOTWORD = "FANTASMA" 
# O limiar de sensibilidade mais alto que estamos a usar
KWS_THRESHOLD = 1e-1 
# Caminho de saída (ex: fantasma.kws)
KWS_MODEL_PATH = os.path.join(os.getcwd(), f"{HOTWORD.lower()}.kws") 
# ------------------------------------

print(f"A gerar ficheiro KWS para a hotword: {HOTWORD}...")
print(f"Limiar de Sensibilidade (Threshold): {KWS_THRESHOLD}")

# A sintaxe do ficheiro KWS é: [palavra] / [limiar_de_sensibilidade] / [nome_interno]
try:
    with open(KWS_MODEL_PATH, 'w') as f:
        # Pocketsphinx espera a hotword em minúsculas
        f.write(f"{HOTWORD.lower()} /{KWS_THRESHOLD}/ {HOTWORD.lower()}\n")
    
    print(f"SUCESSO: Ficheiro KWS criado em '{KWS_MODEL_PATH}'")
    print("\nPróximo passo: Correr 'python assistant.py'")

except Exception as e:
    print(f"ERRO: Falha ao criar o ficheiro KWS: {e}")
    print("Verifica se tens permissão de escrita nesta pasta.")
    sys.exit(1)
