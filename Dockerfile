# pHantasma Docker Image
# Local-first, offline voice assistant

FROM python:3.12-slim

# System dependencies for audio, Whisper, Piper, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Audio I/O
    libasound2-dev \
    portaudio19-dev \
    libportaudio2 \
    # Piper TTS dependencies
    espeak-ng \
    libespeak-ng-dev \
    # Whisper dependencies
    ffmpeg \
    # General build tools
    build-essential \
    git \
    # Cleanup
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy Python requirements first for better caching
COPY pyproject.toml ./
COPY README.md ./

# Install Python dependencies
# setuptools <81 required: webrtcvad (PyPI 2.0.10) imports pkg_resources at module top,
# and pkg_resources was removed from setuptools >= 81. Pin to keep the boot path alive.
RUN pip install --no-cache-dir --upgrade pip "setuptools>=61,<81" wheel && \
    pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -e . && \
    pip install --no-cache-dir "setuptools>=61,<81"

# Copy application code
COPY src/ ./src/
COPY skills/ ./skills/
COPY config.py ./
COPY assistant.py ./
COPY audio_utils.py ./
COPY data_utils.py ./
COPY tools.py ./
COPY .env.example ./

# Create data directory for persistent storage
RUN mkdir -p /app/data

# Copy entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Expose Flask API port
EXPOSE 5000

# Environment variables
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Use entrypoint script
ENTRYPOINT ["docker-entrypoint.sh"]

# Default command runs the assistant
CMD ["assistant"]