import subprocess
import glob
import random
import os
import numpy as np
import sounddevice as sd
import config

def play_tts(text):
    """ Converte texto em voz e reproduz imediatamente (USANDO PIPER + SOX + APLAY) """
    text_cleaned = text.replace('**', '').replace('*', '').replace('#', '').replace('`', '')
    print(f"IA: {text_cleaned}")
    try:
        piper_proc = subprocess.Popen(
            ['piper', '--model', config.TTS_MODEL_PATH, '--output-raw'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        sox_proc = subprocess.Popen(
            [
                'sox',
                '-t', 'raw', '-r', '22050', '-e', 'signed-integer', '-b', '16', '-c', '1', '-',
                '-t', 'raw', '-',
                'flanger', '1', '1', '5', '50', '1', 'sin'
            ],
            stdin=piper_proc.stdout, stdout=subprocess.PIPE
        )
        aplay_proc = subprocess.Popen(
            ['aplay', '-D', config.ALSA_DEVICE_OUT, '-r', '22050', '-f', 'S16_LE', '-t', 'raw'],
            stdin=sox_proc.stdout
        )
        piper_proc.stdin.write(text_cleaned.encode('utf-8'))
        piper_proc.stdin.close()
        aplay_proc.wait()
        sox_proc.wait()
    except FileNotFoundError:
        print("ERRO: 'piper', 'sox' ou 'aplay' não encontrados. (Instale com: sudo apt install sox)")
    except Exception as e:
        print(f"Erro no pipeline de áudio TTS (piper/sox): {e}")

def play_random_music_snippet():
    """ Encontra um MP3 aleatório e toca um snippet de 1 segundo (e espera). """
    try:
        music_dir = '/home/media/music'
        mp3_files = glob.glob(os.path.join(music_dir, '**/*.mp3'), recursive=True)
        if not mp3_files:
            print("AVISO (Música): Nenhum ficheiro MP3 encontrado em /home/media/music")
            return
        random_song = random.choice(mp3_files)
        print(f"A tocar snippet de: {random_song}")
        mp3_proc = subprocess.Popen(
            ['mpg123', '-q', '-n', '45', random_song],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        mp3_proc.wait()
    except FileNotFoundError:
        print("ERRO: 'mpg123' não encontrado. (Instale com: sudo apt install mpg123)")
    except Exception as e:
        print(f"ERRO ao tocar snippet de música: {e}")

def play_random_song_full():
    """ Encontra um MP3 aleatório e toca a música inteira (em background). """
    try:
        music_dir = '/home/media/music'
        mp3_files = glob.glob(os.path.join(music_dir, '**/*.mp3'), recursive=True)
        if not mp3_files:
            print("AVISO (Música): Nenhum ficheiro MP3 encontrado em /home/media/music")
            return False
        random_song = random.choice(mp3_files)
        print(f"A tocar música: {random_song}")
        mp3_proc = subprocess.Popen(
            ['mpg123', '-q', random_song],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except FileNotFoundError:
        print("ERRO: 'mpg123' não encontrado. (Instale com: sudo apt install mpg123)")
        return False
    except Exception as e:
        print(f"ERRO ao tocar música: {e}")
        return False

def record_audio():
    """ Grava áudio com as configurações corretas para o Whisper """
    print("A gravar...")
    try:
        recording = sd.rec(
            int(config.RECORD_SECONDS * config.MIC_SAMPLERATE), 
            samplerate=config.MIC_SAMPLERATE, 
            channels=1, 
            dtype='float32', 
            device=config.ALSA_DEVICE_IN
        )
        sd.wait()
        print("Gravação terminada.")
        return recording.flatten()
    except Exception as e:
        print(f"ERRO ao gravar áudio (verifique o device {config.ALSA_DEVICE_IN}): {e}")
        return np.array([], dtype='float32')
