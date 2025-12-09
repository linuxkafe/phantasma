import subprocess
import glob
import random
import os
import time
import numpy as np
import sounddevice as sd
import webrtcvad
import collections
import config
import hashlib 
import traceback

# Diretório para guardar os ficheiros de áudio gerados
TTS_CACHE_DIR = "/opt/phantasma/cache/tts"

def clean_old_cache(days=30):
    """
    Remove ficheiros da cache que sejam mais antigos que 'days'.
    """
    if not os.path.exists(TTS_CACHE_DIR):
        return

    print(f"Manutenção: A verificar limpeza de cache TTS (> {days} dias)...")
    now = time.time()
    cutoff = days * 86400
    count = 0

    try:
        for f in os.listdir(TTS_CACHE_DIR):
            f_path = os.path.join(TTS_CACHE_DIR, f)
            if os.path.isfile(f_path):
                t_mod = os.stat(f_path).st_mtime
                if now - t_mod > cutoff:
                    os.remove(f_path)
                    count += 1
        if count > 0:
            print(f"Manutenção: {count} ficheiros de áudio antigos removidos.")
    except Exception as e:
        print(f"ERRO ao limpar cache: {e}")

def play_tts(text, use_cache=True):
    """ 
    Converte texto em voz.
    - use_cache=True: Verifica/Gera ficheiro no disco (Ideal para frases fixas).
    - use_cache=False: Pipeline direto em memória (Ideal para respostas do LLM).
    """
    if not text: return

    text_cleaned = text.replace('**', '').replace('*', '').replace('#', '').replace('`', '').strip()
    print(f"IA: {text_cleaned}")

    # --- LÓGICA 1: COM CACHE (Disco) ---
    if use_cache:
        # 1. Preparar Cache
        if not os.path.exists(TTS_CACHE_DIR):
            try:
                os.makedirs(TTS_CACHE_DIR, exist_ok=True)
                os.chmod(TTS_CACHE_DIR, 0o777)
            except: pass

        # Cria um nome de ficheiro único baseado no texto (MD5 hash)
        file_hash = hashlib.md5(text_cleaned.encode('utf-8')).hexdigest()
        cache_path = os.path.join(TTS_CACHE_DIR, f"{file_hash}.wav")

        # 2. Verificar se já existe (CACHE HIT)
        if os.path.exists(cache_path):
            try:
                subprocess.run(
                    ['aplay', '-D', config.ALSA_DEVICE_OUT, '-q', cache_path],
                    check=False
                )
                return 
            except Exception as e:
                print(f"Erro ao tocar cache: {e}")

        # 3. Cache MISS (Gerar para disco e tocar)
        try:
            piper_proc = subprocess.Popen(
                ['piper', '--model', config.TTS_MODEL_PATH, '--output-raw'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE
            )
            
            sox_cmd = [
                'sox',
                '-t', 'raw', '-r', '22050', '-e', 'signed-integer', '-b', '16', '-c', '1', '-',
                cache_path, # Grava aqui
                'flanger', '1', '1', '5', '50', '1', 'sin', 'tempo', '0.9'
            ]
            
            sox_proc = subprocess.Popen(
                sox_cmd,
                stdin=piper_proc.stdout, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE
            )
            
            piper_proc.stdin.write(text_cleaned.encode('utf-8'))
            piper_proc.stdin.close()
            sox_proc.wait()
            
            if os.path.exists(cache_path):
                subprocess.run(
                    ['aplay', '-D', config.ALSA_DEVICE_OUT, '-q', cache_path],
                    check=False
                )

        except FileNotFoundError:
            print("ERRO: 'piper', 'sox' ou 'aplay' não encontrados.")
        except Exception as e:
            print(f"Erro no pipeline TTS: {e}")

    # --- LÓGICA 2: SEM CACHE (Streaming/Pipes) ---
    else:
        try:
            piper_proc = subprocess.Popen(
                ['piper', '--model', config.TTS_MODEL_PATH, '--output-raw'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE
            )

            sox_proc = subprocess.Popen(
                [
                    'sox',
                    '-t', 'raw', '-r', '22050', '-e', 'signed-integer', '-b', '16', '-c', '1', '-',
                    '-t', 'wav', '-',
                    'flanger', '1', '1', '5', '50', '1', 'sin', 'tempo', '0.9'
                ],
                stdin=piper_proc.stdout,
                stdout=subprocess.PIPE
            )

            aplay_proc = subprocess.Popen(
                ['aplay', '-D', config.ALSA_DEVICE_OUT, '-q'],
                stdin=sox_proc.stdout
            )

            piper_proc.stdin.write(text_cleaned.encode('utf-8'))
            piper_proc.stdin.close()
            
            aplay_proc.wait()
            sox_proc.wait()

        except Exception as e:
            print(f"Erro no pipeline TTS (Stream): {e}")

def play_random_music_snippet():
    """ Encontra um MP3 aleatório e toca um snippet de 1 segundo (e espera). """
    try:
        music_dir = '/home/media/music'
        mp3_files = glob.glob(os.path.join(music_dir, '**/*.mp3'), recursive=True)
        if not mp3_files: return
        random_song = random.choice(mp3_files)
        print(f"A tocar snippet de: {random_song}")
        mp3_proc = subprocess.Popen(
            ['mpg123', '-q', '-n', '45', random_song],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        mp3_proc.wait()
    except: pass

def play_random_song_full():
    """ Encontra um MP3 aleatório e toca a música inteira (em background). """
    try:
        music_dir = '/home/media/music'
        mp3_files = glob.glob(os.path.join(music_dir, '**/*.mp3'), recursive=True)
        if not mp3_files:
            print("AVISO (Música): Nenhum ficheiro MP3 encontrado.")
            return False
        random_song = random.choice(mp3_files)
        print(f"A tocar música: {random_song}")
        subprocess.Popen(
            ['mpg123', '-q', random_song],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except: return False

def record_audio():
    """ Grava áudio dinamicamente usando VAD. """
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
        with sd.InputStream(samplerate=config.MIC_SAMPLERATE, channels=1, dtype='int16') as stream:
            for _ in range(max_chunks):
                audio_chunk, overflowed = stream.read(samples_per_frame)
                if overflowed: pass

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
            return np.array([], dtype='float32')

        return np.concatenate(frames)

    except Exception as e:
        print(f"ERRO crítico na gravação VAD: {e}")
        return np.array([], dtype='float32')
