---
ticket: T001
title: Core voice pipeline integration (openWakeWord → Whisper → Ollama → Piper)
sprint: sprint-01
priority: high
status: pending
created: 2026-09-17
---

# T001 — Core Voice Pipeline Integration

## Context
The heart of pHantasma is the voice pipeline: hotword detection → speech-to-text → language model → text-to-speech. This ticket implements the core orchestration in `assistant.py` that chains these components together with VAD gating.

## Acceptance Criteria
- [ ] Hotword detection works with openWakeWord (multiple models: Hey Jarvis, Alexa, Hey Mycroft, Hey Rhasspy)
- [ ] WebRTC VAD pre-filters audio to reduce false positives
- [ ] Whisper (medium model) transcribes Portuguese/English audio to text
- [ ] Ollama (llama3:8b-instruct-8k) processes transcribed text with Phantasma system prompt
- [ ] Piper TTS + sox generates robotic voice response
- [ ] End-to-end latency < 3s on target hardware (HP Mini G4, 16GB RAM)
- [ ] Audio device configuration via config.py (ALSA_DEVICE_IN, ALSA_DEVICE_OUT)
- [ ] Graceful error handling: each stage failure logs and continues loop

## Scope
**In scope:**
- assistant.py main loop
- Audio input/output handling (sounddevice)
- openWakeWord integration with VAD gate
- Whisper transcription
- Ollama chat completion
- Piper TTS synthesis with sox effects
- Music snippet playback on hotword (mpg123)

**Out of scope:**
- Skill system (T002)
- Memory skills (T003, T004)
- IoT skills (T005, T006)
- REST API/CLI (T007)
- Systemd service (T009)

## Dependencies
- config.py must exist with audio device IDs, model paths
- Ollama running with llama3:8b-instruct-8k model
- Piper voice model file available
- Music snippets directory populated

## Rollback
Revert assistant.py to previous version; pipeline runs as before.

## Known Risks
- Whisper medium model memory usage (~2GB VRAM/RAM) — may need tiny/base on constrained hardware
- openWakeWord model loading time on startup (~5-10s)
- Audio device IDs change on reboot — need robust detection or udev rules
- Piper voice model path must be correct in config

## Notes
- Use sounddevice for audio I/O (cross-platform, low latency)
- VAD threshold tuning critical: too sensitive = false triggers, too insensitive = missed hotwords
- sox effects chain: pitch -100, tempo 1.1, reverb for "robotic" voice
- Music snippets: random selection from configured directory on hotword detection