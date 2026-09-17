"""
Main orchestration loop for pHantasma voice pipeline.
"""

import queue
import threading
import time
import signal
import sys
import subprocess
import numpy as np
from typing import Optional

from config import config
from src.pipeline.utils import Result, logger, log_stage
from src.pipeline.audio import AudioCapture, AudioPlayback, VADProcessor, HotwordDetector
from src.pipeline.stt import transcribe as stt_transcribe
from src.pipeline.llm import chat as llm_chat
from src.pipeline.tts import synthesize as tts_synthesize


class PhantasmaPipeline:
    """Main voice pipeline orchestrator."""

    def __init__(self):
        self.audio_capture = AudioCapture(
            device=config.audio.device_in,
            sample_rate=config.audio.sample_rate,
            channels=config.audio.channels,
            block_size=config.audio.block_size,
            dtype=config.audio.dtype,
            queue_maxsize=config.pipeline.queue_maxsize
        )
        self.audio_playback = AudioPlayback(
            device=config.audio.device_out,
            sample_rate=22050,  # Piper default
            channels=1,
            dtype="int16"
        )
        self.vad = VADProcessor(
            aggressiveness=config.vad.aggressiveness,
            sample_rate=config.audio.sample_rate,
            frame_duration_ms=config.vad.frame_duration_ms
        )
        self.hotword = HotwordDetector(
            models=config.hotword.models,
            threshold=config.hotword.threshold,
            sample_rate=config.audio.sample_rate,
            chunk_size=config.audio.block_size
        )

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._audio_buffer: list[np.ndarray] = []
        self._hotword_detected = False
        self._collecting_speech = False
        self._speech_frames: list[np.ndarray] = []
        self._silence_frames = 0
        self._max_silence_frames = int(1.0 * 1000 / config.vad.frame_duration_ms)  # 1s silence

    def _play_audio_feedback(self) -> Result:
        """Play music snippet + greeting on hotword detection."""
        if not config.audio_feedback.enabled:
            return Result.ok(None)

        try:
            import random
            import os

            music_dir = config.audio_feedback.music_dir
            music_files = [f for f in os.listdir(music_dir) if f.endswith((".mp3", ".wav", ".ogg"))]

            if music_files:
                music_file = os.path.join(music_dir, random.choice(music_files))
                # Use mpg123 for mp3, sounddevice for wav
                if music_file.endswith(".mp3"):
                    subprocess.run(["mpg123", "-q", music_file], check=False, timeout=5)
                else:
                    # Play via sounddevice
                    import soundfile as sf
                    data, sr = sf.read(music_file, dtype="int16")
                    self.audio_playback.play(data)

            # Play greeting
            greeting_path = config.audio_feedback.greeting_path
            if os.path.exists(greeting_path):
                import soundfile as sf
                data, sr = sf.read(greeting_path, dtype="int16")
                self.audio_playback.play(data)

            return Result.ok(None)
        except Exception as e:
            logger.warning(f"Audio feedback failed: {e}")
            return Result.ok(None)  # Non-fatal

    def _process_audio_frame(self, frame: np.ndarray):
        """Process single audio frame through VAD and hotword detection."""
        # Run VAD
        vad_frames = self.vad.process_chunk(frame)

        for vad_frame in vad_frames:
            if not self._collecting_speech:
                # Hotword detection mode
                detected, model = self.hotword.process(vad_frame.data)
                if detected:
                    logger.info(f"Hotword detected: {model}")
                    self._hotword_detected = True
                    self._collecting_speech = True
                    self._speech_frames = []
                    self._silence_frames = 0

                    # Play audio feedback
                    self._play_audio_feedback()
            else:
                # Speech collection mode
                self._speech_frames.append(vad_frame.data)

                if vad_frame.is_speech:
                    self._silence_frames = 0
                else:
                    self._silence_frames += 1

                # Check for end of speech (1 second silence)
                if self._silence_frames >= self._max_silence_frames:
                    self._process_speech()
                    self._collecting_speech = False
                    self._hotword_detected = False

    def _process_speech(self):
        """Process collected speech through STT -> LLM -> TTS."""
        if not self._speech_frames:
            return

        # Concatenate speech frames
        speech_audio = np.concatenate(self._speech_frames)

        # Limit max duration
        max_samples = int(config.pipeline.stt_max_audio_seconds * config.audio.sample_rate)
        if len(speech_audio) > max_samples:
            speech_audio = speech_audio[:max_samples]
            logger.warning(f"Speech truncated to {config.pipeline.stt_max_audio_seconds}s")

        logger.info(f"Processing speech: {len(speech_audio)} samples ({len(speech_audio)/config.audio.sample_rate:.1f}s)")

        # STT
        stt_result = stt_transcribe(speech_audio)
        log_stage(logger, "stt", stt_result)
        if not stt_result.success:
            logger.error(f"STT failed: {stt_result.error}")
            return

        text = stt_result.data
        if not text:
            logger.info("STT returned empty text")
            return

        logger.info(f"User said: {text}")

        # LLM
        llm_result = llm_chat(text)
        log_stage(logger, "llm", llm_result)
        if not llm_result.success:
            logger.error(f"LLM failed: {llm_result.error}")
            return

        response = llm_result.data
        if not response:
            logger.info("LLM returned empty response")
            return

        logger.info(f"Phantasma responds: {response}")

        # TTS
        tts_result = tts_synthesize(response)
        log_stage(logger, "tts", tts_result)
        if not tts_result.success:
            logger.error(f"TTS failed: {tts_result.error}")
            return

        audio_data, sample_rate = tts_result.data

        # Playback
        playback_result = self.audio_playback.play(audio_data)
        log_stage(logger, "playback", playback_result)
        if not playback_result.success:
            logger.error(f"Playback failed: {playback_result.error}")

    def _worker_loop(self):
        """Background worker that processes audio queue."""
        logger.info("Pipeline worker started")

        while self._running:
            result = self.audio_capture.get_frame(timeout=0.5)
            if not result.success:
                continue

            frame = result.data
            self._process_audio_frame(frame)

        logger.info("Pipeline worker stopped")

    def start(self) -> Result:
        """Start the pipeline."""
        if self._running:
            return Result.fail("Already running")

        # Start audio capture
        capture_result = self.audio_capture.start()
        if not capture_result.success:
            return capture_result

        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        logger.info("Phantasma pipeline started")
        return Result.ok(None)

    def stop(self) -> Result:
        """Stop the pipeline."""
        if not self._running:
            return Result.ok(None)

        self._running = False

        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)

        self.audio_capture.stop()

        logger.info("Phantasma pipeline stopped")
        return Result.ok(None)


def run():
    """Main entry point for running the assistant."""
    pipeline = PhantasmaPipeline()

    # Signal handling
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        pipeline.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start pipeline
    result = pipeline.start()
    if not result.success:
        logger.error(f"Failed to start pipeline: {result.error}")
        sys.exit(1)

    logger.info("Phantasma is listening... Say 'Hey Jarvis' to activate")
    logger.info("Press Ctrl+C to stop")

    # Keep main thread alive
    try:
        while pipeline._running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()


if __name__ == "__main__":
    run()