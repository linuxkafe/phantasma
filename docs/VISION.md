# Vision

## Problem

Voice assistants today force a privacy trade-off: convenience requires sending audio to cloud providers (Amazon Alexa, Google Assistant, Apple Siri). Users who value privacy have no viable offline alternative that matches cloud functionality — existing open-source options (Mycroft, Rhasspy) are abandoned, complex to deploy, or lack modular extensibility.

## Solution

pHantasma is a local-first, modular voice assistant that runs entirely on user hardware:
- **Hotword detection**: openWakeWord (zero cloud, zero API keys)
- **Speech-to-text**: Whisper (local, medium model)
- **Language model**: Ollama with Llama3 8K context (local LLM)
- **Text-to-speech**: Piper + sox effects (local, robotic voice)
- **Skills system**: Dynamic Python modules for extensibility
- **RAG**: SQLite long-term memory + SearxNG web search (self-hosted)
- **IoT integration**: Local Tuya (tinytuya) and Xiaomi (python-miio) control

All components are swappable; no component requires internet access.

## Value

- **Privacy by default**: Zero audio leaves the device
- **Offline-first**: Works without internet (except optional web search)
- **Modular**: Add skills without touching core
- **Hackable**: Python throughout, systemd service, CLI + REST API
- **Hardware-agnostic**: Runs on Raspberry Pi, mini PCs, servers

Success criteria:
- Hotword detection < 500ms latency on Raspberry Pi 4
- End-to-end voice command < 3s (hotword → TTS response)
- Zero cloud API keys required for core operation
- Skills load dynamically without restart