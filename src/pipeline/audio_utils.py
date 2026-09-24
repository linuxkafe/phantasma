"""
Audio I/O utilities using sounddevice (PortAudio) for bare-metal deployment.

Ported from production (/opt/phantasma/audio_utils.py) with modernizations:
- Config-driven paths (no hardcoded /opt/phantasma)
- Result type for error handling
- Structured logging
- VAD frame collection compatible with pipeline
"""

import glob
import hashlib
import os
import random
import subprocess
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import webrtcvad

from config import config
from src.pipeline.utils import Result, logger

# TTS cache directory (configurable via env, fallback to project dir)
TTS_CACHE_DIR = os.getenv(
    "TTS_CACHE_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "cache", "tts")
)


def clean_old_cache(days: int = 30) -> None:
    """Remove TTS cache files older than specified days."""
    if not os.path.exists(TTS_CACHE_DIR):
        return
    logger.info(f"Manutenção: A verificar limpeza de cache TTS (> {days} dias)...")
    now = time.time()
    cutoff = days * 86400
    try:
        for f in os.listdir(TTS_CACHE_DIR):
            f_path = os.path.join(TTS_CACHE_DIR, f)
            if os.path.isfile(f_path) and (now - os.stat(f_path).st_mtime > cutoff):
                os.remove(f_path)
    except Exception as e:
        logger.warning(f"Erro ao limpar cache: {e}")


def force_volume_down(card_index: int) -> Result:
    """
    Apply volume from config and DISABLE AGC (Auto Gain Control).

    Critical for Jabra SPEAK devices — AGC causes gain fluctuations
    that break hotword detection stability.
    """
    target = config.audio.volume_percent
    logger.info(f"A configurar áudio no Card {card_index} (Alvo: {target}%)...")

    try:
        cmd = ["amixer", "-c", str(card_index), "scontrols"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        import re

        controls = re.findall(r"Simple mixer control '([^']+)'", result.stdout)

        if not controls:
            return Result.fail(f"No mixer controls found for card {card_index}")

        for ctrl in controls:
            # Skip playback controls
            if any(
                x in ctrl for x in ["PCM", "Master", "Speaker", "Headphone", "Playback"]
            ):
                continue

            # 1. Adjust Capture/Mic volume
            if "Capture" in ctrl or "Mic" in ctrl:
                logger.debug(f"Ajustando ganho: '{ctrl}' -> {target}%")
                subprocess.run(
                    [
                        "amixer",
                        "-c",
                        str(card_index),
                        "sset",
                        ctrl,
                        f"{target}%",
                        "unmute",
                        "cap",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

            # 2. Disable AGC (critical for hotword stability)
            if "AGC" in ctrl or "Auto Gain" in ctrl:
                logger.info(f"Desativar AGC: '{ctrl}'")
                subprocess.run(
                    ["amixer", "-c", str(card_index), "sset", ctrl, "off"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

        return Result.ok(None)

    except Exception as e:
        logger.error(f"Erro ao ajustar volumes: {e}")
        return Result.fail(str(e))


def find_working_samplerate(
    device_index: Optional[int], preferred_rates: list[int] = None
) -> int:
    """
    Negotiate a working sample rate with the audio device.

    Jabra SPEAK 410 may not support 16000Hz directly; tries fallback rates.
    """
    if preferred_rates is None:
        preferred_rates = [16000, 48000, 44100, 32000]

    logger.info(f"A negociar Sample Rate para o device {device_index}...")
    for rate in preferred_rates:
        try:
            with sd.InputStream(
                device=device_index, channels=1, samplerate=rate, dtype="int16"
            ):
                pass
            logger.info(f"Hardware aceitou: {rate} Hz")
            return rate
        except Exception:
            continue
    logger.warning("Nenhuma taxa funcionou, a usar default 16000 Hz")
    return 16000


def play_tts(text: str, use_cache: bool = True) -> Result:
    """
    Convert text to speech using Piper + SoX effects, play via aplay.

    Args:
        text: Text to synthesize
        use_cache: Whether to use/write WAV cache

    Returns:
        Result.ok(None) on success, Result.fail(error) on failure
    """
    if not text:
        return Result.ok(None)

    text_cleaned = (
        text.replace("**", "")
        .replace("*", "")
        .replace("#", "")
        .replace("`", "")
        .strip()
    )
    logger.info(f"IA: {text_cleaned}")

    try:
        if use_cache:
            os.makedirs(TTS_CACHE_DIR, exist_ok=True)
            file_hash = hashlib.md5(text_cleaned.encode("utf-8")).hexdigest()
            cache_path = os.path.join(TTS_CACHE_DIR, f"{file_hash}.wav")

            if os.path.exists(cache_path):
                try:
                    subprocess.run(
                        ["aplay", "-D", config.audio.device_out, "-q", cache_path],
                        check=False,
                    )
                    return Result.ok(None)
                except Exception:
                    pass

            # Generate via Piper + SoX
            p1 = subprocess.Popen(
                ["piper", "--model", config.tts.voice_model_path, "--output-raw"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
            p2 = subprocess.Popen(
                [
                    "sox",
                    "-t",
                    "raw",
                    "-r",
                    "22050",
                    "-e",
                    "signed-integer",
                    "-b",
                    "16",
                    "-c",
                    "1",
                    "-",
                    cache_path,
                    "flanger",
                    "1",
                    "1",
                    "5",
                    "50",
                    "1",
                    "sin",
                    "tempo",
                    "1.1",
                ],
                stdin=p1.stdout,
            )
            p1.stdin.write(text_cleaned.encode("utf-8"))
            p1.stdin.close()
            p2.wait()

            if os.path.exists(cache_path):
                subprocess.run(
                    ["aplay", "-D", config.audio.device_out, "-q", cache_path],
                    check=False,
                )
            return Result.ok(None)
        else:
            # Stream directly without cache
            p1 = subprocess.Popen(
                ["piper", "--model", config.tts.voice_model_path, "--output-raw"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
            p2 = subprocess.Popen(
                [
                    "sox",
                    "-t",
                    "raw",
                    "-r",
                    "22050",
                    "-e",
                    "signed-integer",
                    "-b",
                    "16",
                    "-c",
                    "1",
                    "-",
                    "-t",
                    "wav",
                    "-",
                    "flanger",
                    "1",
                    "1",
                    "5",
                    "50",
                    "1",
                    "sin",
                    "tempo",
                    "1.1",
                ],
                stdin=p1.stdout,
                stdout=subprocess.PIPE,
            )
            p3 = subprocess.Popen(
                ["aplay", "-D", config.audio.device_out, "-q"],
                stdin=p2.stdout,
            )
            p1.stdin.write(text_cleaned.encode("utf-8"))
            p1.stdin.close()
            p3.wait()
            return Result.ok(None)

    except Exception as e:
        logger.error(f"TTS playback error: {e}")
        return Result.fail(str(e))


def play_random_music_snippet(
    music_dir: str = None, duration_seconds: int = 45
) -> Result:
    """Play a random music snippet from directory."""
    if music_dir is None:
        music_dir = config.audio_feedback.music_dir

    try:
        mp3s = glob.glob(os.path.join(music_dir, "**/*.mp3"), recursive=True)
        if not mp3s:
            mp3s = glob.glob(os.path.join(music_dir, "**/*.wav"), recursive=True)
        if not mp3s:
            mp3s = glob.glob(os.path.join(music_dir, "**/*.ogg"), recursive=True)

        if mp3s:
            chosen = random.choice(mp3s)
            if chosen.endswith(".mp3"):
                subprocess.run(
                    ["mpg123", "-q", "-n", str(duration_seconds), chosen], check=False
                )
            else:
                import soundfile as sf

                data, sr = sf.read(chosen, dtype="int16")
                # Play via sounddevice
                with sd.OutputStream(
                    device=config.audio.device_out,
                    samplerate=sr,
                    channels=1 if len(data.shape) == 1 else data.shape[1],
                    dtype="int16",
                ) as stream:
                    stream.write(data)
            return Result.ok(None)
    except Exception as e:
        logger.warning(f"Music playback failed: {e}")
    return Result.fail("No music files or playback error")


def play_greeting(greeting_path: str = None) -> Result:
    """Play greeting audio file."""
    if greeting_path is None:
        greeting_path = config.audio_feedback.greeting_path

    if not os.path.exists(greeting_path):
        return Result.fail(f"Greeting file not found: {greeting_path}")

    try:
        import soundfile as sf

        data, sr = sf.read(greeting_path, dtype="int16")
        with sd.OutputStream(
            device=config.audio.device_out,
            samplerate=sr,
            channels=1 if len(data.shape) == 1 else data.shape[1],
            dtype="int16",
        ) as stream:
            stream.write(data)
        return Result.ok(None)
    except Exception as e:
        logger.warning(f"Greeting playback failed: {e}")
        return Result.fail(str(e))


def record_audio_vad(
    device_index: Optional[int] = None,
    sample_rate: int = None,
    vad_aggressiveness: int = 2,
    frame_duration_ms: int = 30,
    silence_threshold_seconds: float = 1.5,
    max_duration_seconds: float = 30.0,
) -> Result:
    """
    Record audio with VAD (Voice Activity Detection) using sounddevice.

    Returns concatenated float32 audio [-1, 1] or empty array on failure/silence.
    """
    if sample_rate is None:
        sample_rate = config.audio.sample_rate

    logger.info("A ouvir...")

    vad = webrtcvad.Vad(vad_aggressiveness)
    samples_per_frame = int(sample_rate * frame_duration_ms / 1000)

    chunks_per_second = 1000 // frame_duration_ms
    silence_limit_chunks = int(silence_threshold_seconds * chunks_per_second)
    max_chunks = int(max_duration_seconds * chunks_per_second)

    frames = []
    silence_counter = 0
    speech_detected = False

    try:
        # Resolve device index if string
        device = device_index
        if isinstance(device_index, str):
            # Try to parse "hw:X,Y" or use as-is for sounddevice
            device = device_index

        # Find working sample rate if device specified
        actual_rate = sample_rate
        if device is not None:
            actual_rate = find_working_samplerate(
                device, [sample_rate, 48000, 44100, 32000]
            )
            if actual_rate != sample_rate:
                logger.info(f"Sample rate ajustado: {sample_rate} -> {actual_rate} Hz")
                samples_per_frame = int(actual_rate * frame_duration_ms / 1000)
                chunks_per_second = 1000 // frame_duration_ms
                silence_limit_chunks = int(
                    silence_threshold_seconds * chunks_per_second
                )
                max_chunks = int(max_duration_seconds * chunks_per_second)

        with sd.InputStream(
            device=device,
            samplerate=actual_rate,
            channels=1,
            dtype="int16",
            blocksize=samples_per_frame,
        ) as stream:
            for _ in range(max_chunks):
                audio_chunk, overflowed = stream.read(samples_per_frame)
                if overflowed:
                    logger.debug("Input stream overflow")

                audio_bytes = audio_chunk.tobytes()
                is_speech = vad.is_speech(audio_bytes, actual_rate)

                if is_speech:
                    silence_counter = 0
                    speech_detected = True
                else:
                    silence_counter += 1

                # Convert to float32 [-1, 1] for pipeline
                frames.append(audio_chunk.flatten().astype(np.float32) / 32768.0)

                if speech_detected and silence_counter > silence_limit_chunks:
                    logger.info("Fim de fala detetado.")
                    break

        logger.info("Gravação terminada.")
        if not speech_detected:
            return Result.ok(np.array([], dtype=np.float32))

        audio_data = np.concatenate(frames)
        # Resample to 16000 if needed (Whisper expects 16kHz)
        if actual_rate != 16000:
            ratio = 16000 / actual_rate
            new_length = int(len(audio_data) * ratio)
            indices = np.linspace(0, len(audio_data) - 1, new_length)
            audio_data = np.interp(
                indices, np.arange(len(audio_data)), audio_data
            ).astype(np.float32)

        return Result.ok(audio_data)

    except Exception as e:
        logger.error(f"Erro gravação VAD: {e}")
        return Result.fail(str(e))


def play_audio_file(file_path: str, device: str = None) -> Result:
    """Play a WAV/OGG/MP3 file via sounddevice or mpg123."""
    if not os.path.exists(file_path):
        return Result.fail(f"Audio file not found: {file_path}")

    try:
        if file_path.endswith(".mp3"):
            subprocess.run(["mpg123", "-q", file_path], check=False)
            return Result.ok(None)

        import soundfile as sf

        data, sr = sf.read(file_path, dtype="int16")
        with sd.OutputStream(
            device=device or config.audio.device_out,
            samplerate=sr,
            channels=1 if len(data.shape) == 1 else data.shape[1],
            dtype="int16",
        ) as stream:
            stream.write(data)
        return Result.ok(None)
    except Exception as e:
        logger.error(f"Audio playback error: {e}")
        return Result.fail(str(e))
