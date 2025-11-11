import os

# --- Configuração ---
HOTWORD = "FANTASMA"
KWS_THRESHOLD = 1e-1
# Dicionário de utilizador a ser criado
USER_DICT_PATH = os.path.join(os.getcwd(), f"{HOTWORD.lower()}.dic") 
KWS_FILE_PATH = os.path.join(os.getcwd(), f"{HOTWORD.lower()}.kws")

# A pronúncia que o motor Inglês CMU entende (a que determinámos)
FANTASMA_PRONUNCIATION = "F AE N T AE Z M AH" 
# --------------------

print("--- 1. Criando ficheiro de Pronúncia (.dic) ---")
try:
    # Apenas escrevemos a nossa hotword e pronúncia
    with open(USER_DICT_PATH, 'w') as f:
        f.write(f"{HOTWORD} {FANTASMA_PRONUNCIATION}\n")
    print(f"SUCESSO: Ficheiro de dicionário criado em '{USER_DICT_PATH}'")
except Exception as e:
    print(f"ERRO: Falha ao escrever o ficheiro .dic: {e}")
    exit(1)

print("\n--- 2. Criando ficheiro KWS (.kws) ---")
try:
    # Cria o ficheiro de palavras-chave
    with open(KWS_FILE_PATH, 'w') as f:
         f.write(f"{HOTWORD.lower()} /{KWS_THRESHOLD}/ {HOTWORD.lower()}\n")
    print(f"SUCESSO: Ficheiro KWS criado em '{KWS_FILE_PATH}'")
except Exception as e:
    print(f"ERRO: Falha ao escrever o ficheiro .kws: {e}")
    exit(1)

print("\n=== PRONTO! ===")
print("Pode agora correr 'python assistant.py'")
