import sounddevice as sd
import numpy as np
from scipy.io.wavfile import write
import os
import time

# --- Configuração ---
HOTWORD = "phantasma"          # O nome da tua hotword (vai ser o nome da pasta)
DEVICE_INDEX = 4             # O índice 4 do teu Jabra (que descobrimos)
SAMPLE_RATE = 16000          # OBRIGATÓRIO: openwakeword treina a 16kHz
DURATION = 2                 # 2 segundos por amostra
NUM_SAMPLES = 20             # Vamos gravar 20 amostras
# --------------------

# Criar a pasta para guardar as amostras
output_folder = os.path.join("train_samples", HOTWORD)
os.makedirs(output_folder, exist_ok=True)

print(f"=== Preparar para gravar {NUM_SAMPLES} amostras de '{HOTWORD}' ===")
print(f"As amostras serão guardadas em: {output_folder}")
print("Dica: Tenta variar a tua entoação (pergunta, afirmação, sussurro).")
print("Dica: Deixa 1 segundo de silêncio antes e depois de falares.")
print("\nPrime 'Enter' quando estiveres pronto para começar a primeira amostra...")
input() # Espera que o utilizador prima Enter

for i in range(NUM_SAMPLES):
    print(f"\n--- A preparar amostra {i + 1}/{NUM_SAMPLES} ---")

    # Contagem decrescente
    print("3...", end='', flush=True); time.sleep(1)
    print("2...", end='', flush=True); time.sleep(1)
    print("1...", end='', flush=True); time.sleep(1)
    print(" GRAVAR! (Diz a hotword agora)")

    # Gravar áudio (usando float32, como provámos que funciona)
    recording = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, device=DEVICE_INDEX, dtype='float32')
    sd.wait() # Espera a gravação de 2s terminar

    print("Gravação terminada.")

    # Definir o nome do ficheiro
    filename = os.path.join(output_folder, f"sample_{i + 1}.wav")

    # Converter de float32 para int16 (o formato WAV padrão para treino)
    audio_int16 = (recording * 32767).astype(np.int16)

    # Guardar o ficheiro .wav
    write(filename, SAMPLE_RATE, audio_int16)

    print(f"Amostra guardada: {filename}")

    if i < NUM_SAMPLES - 1:
        print("Prime 'Enter' para a próxima amostra...")
        input() # Espera pelo utilizador

print(f"\n=== Concluído! ===\nGravaste {NUM_SAMPLES} amostras.")
print(f"Verifica a pasta '{output_folder}'.")
