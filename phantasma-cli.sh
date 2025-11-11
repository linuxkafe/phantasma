#!/bin/bash

# O URL da API do assistente
API_URL="http://localhost:5000/comando"
# <--- ADICIONADO: URL para a ajuda ---
HELP_URL="http://localhost:5000/help"

# --- Função de Ajuda (MODIFICADA) ---
show_help() {
    echo "Uso: $0 [comando] [texto...]"
    echo ""
    echo "Envia comandos para o serviço phantasma."
    echo ""
    echo "Comandos disponíveis (obtidos do serviço):"

    # --- LÓGICA DE AJUDA DINÂMICA ---
    # Tenta obter os comandos via cURL
    COMMANDS_JSON=$(curl -s -X GET "$HELP_URL")

    if [ $? -ne 0 ]; then
        echo "  ERRO: Não foi possível ligar ao serviço em $HELP_URL."
        echo "  O assistente 'phantasma.service' está a correr?"
        exit 1
    fi

    # Se o 'jq' estiver instalado, formata a resposta
    if [ $JQ_INSTALLED -eq 1 ]; then
        # Extrai o objeto 'commands', converte em entradas [key, value]
        # e imprime formatado.
        echo "$COMMANDS_JSON" | jq -r '.commands | to_entries[] | "  - \(.key):\n      \(.value)\n"'
    else
        # Se não houver 'jq', imprime o JSON "feio"
        echo "$COMMANDS_JSON"
    fi
    # --- FIM DA LÓGICA DINÂMICA ---
    
    echo ""
    echo "Exemplos:"
    echo "  $0 diz olá, isto é um teste"
    echo "  $0 como vai estar o tempo amanhã em Lisboa"
    echo "  $0 quanto é 5 mais 5"
}

# --- Verificar se 'jq' está instalado ---
if ! command -v jq &> /dev/null; then
    echo "AVISO: 'jq' não encontrado. A resposta JSON não será formatada." >&2
    JQ_INSTALLED=0
else
    JQ_INSTALLED=1
fi

# --- Verificar ajuda ou falta de argumentos ---
if [ -z "$1" ] || [ "$1" == "-h" ] || [ "$1" == "--help" ]; then
    show_help
    exit 0
fi

# --- Lógica Principal ---
PROMPT=""

# Se o primeiro argumento é "diz", o prompt é "diz [resto]"
# (Isto corresponde à lógica do 'diz' na tua API)
if [ "$1" == "diz" ]; then
    # Remove o "diz" ($1) e junta o resto
    shift
    PROMPT="diz $@"
else
    # Todos os argumentos são o prompt
    PROMPT="$@"
fi

# Construir o JSON
# Usamos 'jq' para criar o JSON de forma segura
JSON_PAYLOAD=$(jq -n --arg prompt "$PROMPT" '{"prompt": $prompt}')

echo "A enviar comando: $PROMPT"
echo "------------------------------"

# Enviar o comando via cURL e processar a resposta
RESPONSE=$(curl -s -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d "$JSON_PAYLOAD")

# Verificar se o curl falhou
if [ $? -ne 0 ]; then
    echo "ERRO: Falha ao ligar a $API_URL. O serviço phantasma está a correr?" >&2
    exit 1
fi

# Processar e mostrar a resposta JSON
echo "Resposta da API:"
if [ $JQ_INSTALLED -eq 1 ]; then
    echo "$RESPONSE" | jq .
else
    echo "$RESPONSE"
fi
