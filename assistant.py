"""
Production-style voice pipeline using sounddevice (PortAudio) for bare-metal.

This is a simplified rewrite of PhantasmaPipeline that uses sounddevice
directly for audio I/O, matching the proven production approach at
/opt/phantasma/assistant.py

Hotword detection → VAD recording → STT → LLM → TTS → Playback
"""

import os
import re
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import webrtcvad

import config as config_module
from config import config
from data_utils import get_cached_response, retrieve_from_rag
from skills import SkillContext, SkillLoader
from src.brain.fly_brain import FlyBrain
from src.brain.persistence import FlyBrainStore
from src.pipeline.audio_utils import (
    find_working_samplerate,
    force_volume_down,
    play_greeting,
    play_random_music_snippet,
    play_tts,
    record_audio_vad,
)
from src.pipeline.utils import Result, logger
from tools import search_with_searxng


def sanitize_llm_context(context: str) -> str:
    """Sanitize RAG/web context for LLM injection."""
    if not context or not isinstance(context, str):
        return ""
    context = re.sub(
        r"MEMÓRIAS PESSOAIS.*?\n\n", "", context, flags=re.DOTALL | re.IGNORECASE
    )
    context = re.sub(
        r"NOTA: Se houver contradições.*?\n", "", context, flags=re.IGNORECASE
    )
    context = re.sub(r"\[\d{4}-\d{2}-\d{2}.*?\]", "", context)
    poison_terms = ["Sombra", "Aquietação", "Fim", "Silêncio", "Fúria da Memória"]
    for term in poison_terms:
        context = re.sub(rf"\*\*{term}\*\*", "", context, flags=re.IGNORECASE)
        context = re.sub(rf"{term}:", "", context, flags=re.IGNORECASE)
    return context.strip()


class PhantasmaPipeline:
    """Production-style voice pipeline using sounddevice."""

    def __init__(self):
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._device_index = None
        self._sample_rate = config.audio.sample_rate
        self._setup_audio_hardware()
        self._setup_hotword()

        self._fly_brain_store = FlyBrainStore(config_module.BRAIN_DB_PATH)
        self._fly_brain = FlyBrain(store=self._fly_brain_store)
        self._skill_loader = SkillLoader(
            skills_dir=config_module.SKILLS_DIR,
            context=SkillContext(fly_brain=self._fly_brain),
        )
        self._skill_loader.load_all()
        self._skill_loader.start_daemons()

        self._collecting_feedback = False
        self._feedback_start_time: Optional[float] = None
        self._feedback_window_seconds = getattr(
            config_module, "FEEDBACK_WINDOW_SECONDS", 5
        )
        self._positive_keywords = getattr(
            config_module, "FEEDBACK_POSITIVE_KEYWORDS", ["obrigado", "obrigada"]
        )
        self._negative_keywords = getattr(
            config_module, "FEEDBACK_NEGATIVE_KEYWORDS", ["não entendi", "não percebi"]
        )

    def _setup_audio_hardware(self):
        """Setup audio hardware: detect device, disable AGC, negotiate sample rate."""
        try:
            # Find Jabra device
            devices = sd.query_devices()
            for idx, dev in enumerate(devices):
                if dev["max_input_channels"] > 0:
                    name = dev["name"].lower()
                    if "jabra" in name or "speak" in name:
                        self._device_index = idx
                        logger.info(f"Jabra device found: {dev['name']} at index {idx}")
                        break

            if self._device_index is None:
                logger.info("No Jabra found, using default device")
                self._device_index = None

            # Negotiate sample rate
            self._sample_rate = find_working_samplerate(
                self._device_index, [16000, 48000, 44100, 32000]
            )
            logger.info(f"Using sample rate: {self._sample_rate} Hz")

            # Setup ALSA controls
            if self._device_index is not None:
                card_index = self._device_index  # Approximate mapping
                force_volume_down(card_index)

        except Exception as e:
            logger.warning(f"Audio hardware setup warning: {e}")

    def _setup_hotword(self):
        """Setup openWakeWord hotword detector."""
        try:
            from openwakeword.model import Model

            # Resolve model paths (may be container paths or local)
            model_paths = config.hotword.models
            custom_paths = [p for p in model_paths if os.path.isfile(p)]
            if custom_paths:
                self._hotword_model = Model(
                    wakeword_model_paths=custom_paths, inference_framework="onnx"
                )
            else:
                # Fallback to pretrained
                try:
                    from openwakeword import get_pretrained_model_paths

                    all_paths = get_pretrained_model_paths()
                    filtered = [
                        p
                        for p in all_paths
                        if any(m in p for m in config.hotword.models)
                    ]
                    self._hotword_model = Model(
                        wakeword_model_paths=filtered if filtered else all_paths,
                        inference_framework="onnx",
                    )
                except Exception as e:
                    logger.error(f"Failed to load hotword models: {e}")
                    self._hotword_model = None
        except Exception as e:
            logger.error(f"Hotword setup failed: {e}")
            self._hotword_model = None

    def _detect_hotword(self, audio_chunk: np.ndarray) -> bool:
        """Detect hotword in audio chunk."""
        if self._hotword_model is None:
            return False

        try:
            preds = self._hotword_model.predict(audio_chunk)
            if preds:
                score = max(preds.values())
                if score >= config.hotword.threshold:
                    logger.info(f"Hotword detected: {preds}")
                    return True
        except Exception as e:
            logger.debug(f"Hotword detection error: {e}")
        return False

    def _play_audio_feedback(self):
        """Play music snippet + greeting on hotword."""
        if not getattr(config, "AUDIO_FEEDBACK_ENABLED", True):
            return
        try:
            if play_random_music_snippet():
                time.sleep(0.5)
            play_greeting()
        except Exception as e:
            logger.warning(f"Audio feedback failed: {e}")

    def _process_voice_interaction(self):
        """Process a single voice interaction (hotword detected)."""
        # Play feedback
        self._play_audio_feedback()

        # Record with VAD
        record_result = record_audio_vad(
            device=self._device_index, sample_rate=self._sample_rate
        )
        if not record_result.success:
            logger.error(f"Recording failed: {record_result.error}")
            return

        audio_data = record_result.data
        if len(audio_data) == 0:
            logger.info("No speech detected")
            return

        # STT
        import whisper

        whisper_model = None
        try:
            whisper_model = whisper.load_model(config.stt.model_size)
            result = whisper_model.transcribe(
                audio_data, language=config.stt.language, fp16=False
            )
            text = result["text"].strip()
        except Exception as e:
            logger.error(f"STT failed: {e}")
            return

        if not text:
            logger.info("STT returned empty text")
            return

        logger.info(f"User said: {text}")

        # Process text
        response = self.respond_to_text(text)
        if response:
            play_tts(response)

    def _worker_loop(self):
        """Hotword detection loop (runs in thread)."""
        logger.info("Pipeline worker started")

        # VAD parameters (from config)
        frame_duration_ms = config.vad.frame_duration_ms
        samples_per_frame = int(self._sample_rate * frame_duration_ms / 1000)

        while self._running:
            try:
                with sd.InputStream(
                    device=self._device_index,
                    samplerate=self._sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=samples_per_frame,
                ) as stream:
                    while self._running:
                        audio_chunk, _ = stream.read(samples_per_frame)
                        if self._hotword_model:
                            # Convert to float for openwakeword
                            audio_float = audio_chunk.astype(np.float32) / 32768.0
                            if self._detect_hotword(audio_float):
                                # Process in separate thread to avoid blocking capture
                                threading.Thread(
                                    target=self._process_voice_interaction, daemon=True
                                ).start()
                                # Cooldown
                                time.sleep(config.hotword.cooldown_seconds)
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                time.sleep(1)

        logger.info("Pipeline worker stopped")

    def start(self) -> Result:
        if self._running:
            return Result.fail("Already running")

        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info("Phantasma pipeline started")
        return Result.ok(None)

    def stop(self) -> Result:
        if not self._running:
            return Result.ok(None)
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
        logger.info("Phantasma pipeline stopped")
        return Result.ok(None)

    def respond_to_text(self, text: str) -> Optional[str]:
        """Route text through skills then FlyBrain + LLM."""
        self._apply_feedback_reward(text)

        skill_response = self._execute_with_shared_audio_paused(text)
        if skill_response is not None:
            logger.info(f"Skill handled: {skill_response}")
            return skill_response

        return self._respond_with_llm(text)

    def _apply_feedback_reward(self, text: str):
        text_lower = text.lower()
        reward = None
        for kw in self._positive_keywords:
            if kw in text_lower:
                reward = +1.0
                break
        if reward is None:
            for kw in self._negative_keywords:
                if kw in text_lower:
                    reward = -1.0
                    break

        if reward is not None:
            self._fly_brain.step(
                topic_angle_deg=self._fly_brain.ring.orientation_deg,
                novelty=0.1,
                reward=reward,
            )

    def _execute_with_shared_audio_paused(self, text: str) -> Optional[str]:
        # In sounddevice-based approach, we can't easily pause the shared stream
        # Skills that need audio capture should manage it themselves
        return self._skill_loader.execute_skill(text)

    def _respond_with_llm(self, text: str) -> Optional[str]:
        # Check cache
        cached = get_cached_response(text)
        if cached:
            return cached

        # FlyBrain step
        topic_angle = (hash(text) % 3600) / 10.0
        self._fly_brain.step(topic_angle_deg=topic_angle, novelty=0.5, reward=0.0)

        # RAG + Web
        rag = sanitize_llm_context(retrieve_from_rag(text))
        web = sanitize_llm_context(search_with_searxng(text))

        sys_prompt = (
            getattr(config, "llm", None)
            and getattr(config.llm, "system_prompt", "")
            or ""
        )
        full_prompt = (
            f"{sys_prompt}\n\n"
            "### CONHECIMENTO DISPONÍVEL:\n"
            f"{rag}\n{web}\n\n"
            f"User: {text}"
        )

        # Ollama
        try:
            import ollama

            client = ollama.Client(host=config.llm.host, timeout=config.llm.timeout)
            response = client.chat(
                model=config.llm.model,
                messages=[{"role": "user", "content": full_prompt}],
                options={
                    "repeat_penalty": 1.4,
                    "temperature": 0.6,
                    "num_ctx": config.llm.context_size,
                },
            )
            response_text = response.get("message", {}).get("content", "").strip()
            if response_text:
                return response_text
        except Exception as e:
            logger.error(f"Ollama failed: {e}")

        return None
