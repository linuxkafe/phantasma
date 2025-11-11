import sounddevice as sd
import numpy as np
import time
from scipy.io.wavfile import write

DEVICE_INDEX = 4 # O índice 4 do teu Jabra
SAMPLE_RATE = 16000
DURATION = 3 # segundos
FILENAME = "teste_sounddevice.wav"

print(f"A gravar áudio do dispositivo {DEVICE_INDEX} por {DURATION} segundos...")
print("FALA AGORA!")

# Grava o áudio
myrecording = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, device=DEVICE_INDEX, dtype='float32')

# Espera a gravação terminar
sd.wait() 

print(f"Gravação terminada. A guardar em {FILENAME}...")

# Converte para int16 para o ficheiro WAV
write(FILENAME, SAMPLE_RATE, (myrecording * 32767).astype(np.int16))

print("Ficheiro guardado.")
