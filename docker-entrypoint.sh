#!/bin/bash
# pHantasma Docker Entrypoint
# Handles initialization and starts the appropriate service

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Ensure data directories exist
mkdir -p /app/data /app/models/tts /app/audio/music

# Check for required model files
if [ ! -f "/app/models/tts/pt_PT-dii-high.onnx" ]; then
    log_warn "TTS model not found at /app/models/tts/pt_PT-dii-high.onnx"
    log_warn "TTS will not work without the model file"
fi

# Check for hotword models
if [ ! -f "/app/models/hey_fantasma.onnx" ] && [ ! -f "/opt/phantasma/models/hey_fantasma.onnx" ]; then
    log_warn "Hotword model not found. Hotword detection will not work."
fi

# Environment variables are injected by docker-compose via env_file
# No need to source .env manually

# Validate configuration
log_info "Validating configuration..."
cd /app
python3 -c "
from config import config
errors = config.validate()
if errors:
    for e in errors:
        print(f'  CONFIG ERROR: {e}')
    exit(1)
else:
    print('  Config validation: OK')
"

# Determine which service to run
SERVICE="${1:-assistant}"

case "$SERVICE" in
    assistant)
        log_info "Starting pHantasma voice assistant..."
        exec python3 -m src.main
        ;;
    api)
        log_info "Starting Flask REST API only..."
        export PHANTASMA_API_ONLY=1
        exec python3 -c "
from src.api.routes import create_app
app = create_app()
app.run(host='0.0.0.0', port=5000)
"
        ;;
    test)
        log_info "Running tests..."
        exec python3 -m pytest tests/ -v
        ;;
    shell)
        log_info "Starting interactive shell..."
        exec /bin/bash
        ;;
    *)
        log_error "Unknown service: $SERVICE"
        echo "Usage: $0 {assistant|api|test|shell}"
        exit 1
        ;;
esac