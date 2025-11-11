#!/bin/bash

# 1. Obtém o caminho absoluto para a pasta onde o script está
SCRIPT_DIR=$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd)

# 2. Define os caminhos explícitos
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
ASSISTANT_SCRIPT="$SCRIPT_DIR/assistant.py"

# 3. Verifica se o Python do venv existe
if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERRO: Não foi possível encontrar o Python em $VENV_PYTHON"
    echo "Certifique-se que o venv foi criado."
    exit 1
fi

# 4. Executa o script usando o Python explícito do venv
echo "A iniciar o assistente com o Python do venv..."
"$VENV_PYTHON" -u "$ASSISTANT_SCRIPT"
