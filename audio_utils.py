import glob
import hashlib
import os
import random
import subprocess
import time

import numpy as np
import sounddevice as sd
import webrtcvad

import config

# Diretório para guardar os ficheiros de áudio gerados (config-driven,
# fallback para o caminho histórico do deployment de host).
TTS_CACHE_DIR = config.TTS_CACHE_DIR


def clean_old_cache(days=30):
    """Remove ficheiros da cache que sejam mais antigos que 'days'."""
    if not os.path.exists(TTS_CACHE_DIR):
        return
    print(f"Manutenção: A verificar limpeza de cache TTS (> {days} dias)...")
    now = time.time()
    cutoff = days * 86400
    try:
        for f in os.listdir(TTS_CACHE_DIR):
            f_path = os.path.join(TTS_CACHE_DIR, f)
            if os.path.isfile(f_path) and (now - os.stat(f_path).st_mtime > cutoff):
                os.remove(f_path)
    except Exception as e:
        print(f"ERRO ao limpar cache: {e}")


def _ghost_effects():
    """Audio effects applied to the synthesised voice, or none.

    The flanger and the tempo drag were there to make the voice sound eerie.
    They are a comb filter and a resampler, so they smear the fine spectral
    detail that separates an open vowel from a closed one -- which is exactly
    what a listener is judging when they say the accentuation is wrong. They
    are now opt-out via TTS_GHOST_EFFECTS, so the voice can be heard on its
    own terms before deciding whether the character is worth the clarity.
    """
    if os.getenv("TTS_GHOST_EFFECTS", "1").strip().lower() in ("0", "false", "no", "off"):
        return []
    return ["flanger", "1", "1", "5", "50", "1", "sin", "tempo", "0.9"]


def play_tts(text, use_cache=True):
    """Converte texto em voz (Lógica restaurada com Cache e SoX)."""
    if not text:
        return
    text_cleaned = (
        text.replace("**", "")
        .replace("*", "")
        .replace("#", "")
        .replace("`", "")
        .strip()
    )
    print(f"IA: {text_cleaned}")

    if use_cache:
        os.makedirs(TTS_CACHE_DIR, exist_ok=True)
        # The key covers the voice and the effects, not just the words. It did
        # not, and that is how a 44-byte failure survived: a synthesis that
        # produced nothing wrote a file, and the next run found the file and
        # served it as though it were the answer. The same trap waits for a
        # voice change -- a cache built by one voice would be replayed under
        # another, forever, with nothing in the filename to say so.
        recipe = "|".join([str(config.TTS_MODEL_PATH), "|".join(_ghost_effects())])
        file_hash = hashlib.md5((text_cleaned + recipe).encode("utf-8")).hexdigest()
        cache_path = os.path.join(TTS_CACHE_DIR, f"{file_hash}.wav")

        if os.path.exists(cache_path):
            try:
                subprocess.run(
                    ["aplay", "-D", config.ALSA_DEVICE_OUT, "-q", cache_path],
                    check=False,
                )
                return
            except Exception:
                pass

        try:
            p1 = subprocess.Popen(
                ["piper", "--model", config.TTS_MODEL_PATH, "--output-raw"],
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
                    *_ghost_effects(),
                ],
                stdin=p1.stdout,
            )
            p1.stdin.write(text_cleaned.encode("utf-8"))
            p1.stdin.close()
            p2.wait()
            if os.path.exists(cache_path):
                subprocess.run(
                    ["aplay", "-D", config.ALSA_DEVICE_OUT, "-q", cache_path],
                    check=False,
                )
        except Exception:
            pass
    else:
        try:
            p1 = subprocess.Popen(
                ["piper", "--model", config.TTS_MODEL_PATH, "--output-raw"],
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
                    "0.9",
                ],
                stdin=p1.stdout,
                stdout=subprocess.PIPE,
            )
            p3 = subprocess.Popen(
                ["aplay", "-D", config.ALSA_DEVICE_OUT, "-q"],
                stdin=p2.stdout,
            )
            p1.stdin.write(text_cleaned.encode("utf-8"))
            p1.stdin.close()
            p3.wait()
        except Exception:
            pass


def play_random_music_snippet():
    try:
        mp3s = glob.glob(os.path.join("/home/media/music", "**/*.mp3"), recursive=True)
        if mp3s:
            subprocess.Popen(
                ["mpg123", "-q", "-n", "45", random.choice(mp3s)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).wait()
    except Exception:
        pass


def play_random_song_full():
    try:
        mp3s = glob.glob(os.path.join("/home/media/music", "**/*.mp3"), recursive=True)
        if not mp3s:
            return False
        subprocess.Popen(
            ["mpg123", "-q", random.choice(mp3s)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def record_audio():
    """
    A Lógica VAD Original (Afinada).
    Usa Vad(2) e espera 1.5s de silêncio.
    """
    print("A ouvir...")

    vad = webrtcvad.Vad(2)
    frame_duration_ms = 30
    samples_per_frame = int(config.MIC_SAMPLERATE * frame_duration_ms / 1000)

    silence_threshold_seconds = 1.5
    max_duration_seconds = 10.0

    frames = []
    silence_counter = 0
    speech_detected = False

    chunks_per_second = 1000 // frame_duration_ms
    silence_limit_chunks = int(silence_threshold_seconds * chunks_per_second)
    max_chunks = int(max_duration_seconds * chunks_per_second)

    try:
        with sd.InputStream(
            samplerate=config.MIC_SAMPLERATE, channels=1, dtype="int16"
        ) as stream:
            for _ in range(max_chunks):
                audio_chunk, overflowed = stream.read(samples_per_frame)
                if overflowed:
                    pass

                audio_bytes = audio_chunk.tobytes()
                is_speech = vad.is_speech(audio_bytes, config.MIC_SAMPLERATE)

                if is_speech:
                    silence_counter = 0
                    speech_detected = True
                else:
                    silence_counter += 1

                frames.append(audio_chunk.flatten().astype(np.float32) / 32768.0)

                if speech_detected and silence_counter > silence_limit_chunks:
                    print("Fim de fala detetado.")
                    break

        print("Gravação terminada.")
        if not speech_detected:
            return np.array([], dtype="float32")

        return np.concatenate(frames)

    except Exception as e:
        print(f"ERRO Gravação VAD: {e}")
        return np.array([], dtype="float32")
