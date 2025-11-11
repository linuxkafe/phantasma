#!/bin/bash

# --- Configuração ---
# (Certifique-se que este caminho está correto!)
TTS_MODEL="/root/scripts/phantasma/models/pt_PT-dii-high.onnx"
ALSA_DEVICE="plughw:0,0"

# --- Efeitos SoX (Altere aqui para testar) ---
# pitch -350: Tom grave
# flanger 2 2 20 70 1.5 sin: Efeito metálico
# overdrive 5 5: Distorção robótica
SOX_EFFECTS="pitch -300 : highpass 300 : lowpass 3500 : overdrive 5 5 : flanger 2 2 20 70 1.5 sin : tempo 0.5"
# --- Lógica do Script ---

# 1. Verifica se foi passado texto
if [ -z "$*" ]; then
    echo "Uso: $0 <texto para modular>"
    echo "Exemplo: $0 Olá seyon. Eu sou o pHantasma."
    exit 1
fi

echo "A modular: '$*'..."
echo "Efeitos: $SOX_EFFECTS"

# 2. O Pipeline de Áudio (Echo -> Piper -> SoX -> Aplay)
echo "$*" | \
piper --model "$TTS_MODEL" --output-raw | \
sox -t raw -r 22050 -e signed-integer -b 16 -c 1 - -t raw - $SOX_EFFECTS | \
aplay -D "$ALSA_DEVICE" -r 22050 -f S16_LE -t raw

echo "Teste concluído."
