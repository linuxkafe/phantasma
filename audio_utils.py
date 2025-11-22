import subprocess
import glob
import random
import os
import numpy as np
import sounddevice as sd
import webrtcvad
import collections
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
        
        # Revertido: Reintroduzimos o SoX com o efeito 'flanger'
        sox_proc = subprocess.Popen(
            [
                'sox',
                '-t', 'raw', '-r', '22050', '-e', 'signed-integer', '-b', '16', '-c', '1', '-',
                '-t', 'raw', '-',
                'flanger', '1', '1', '5', '50', '1', 'sin', 'tempo', '0.9'
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
    """ 
    Grava áudio dinamicamente usando VAD (Voice Activity Detection).
    Para de gravar automaticamente quando deteta silêncio.
    """
    print("A ouvir...")
    
    # Configurações do VAD
    vad = webrtcvad.Vad(2) # Nível de agressividade (0-3). 2 é equilibrado.
    frame_duration_ms = 30 # Duração do frame em ms (VAD aceita 10, 20 ou 30)
    # O VAD requer 16-bit PCM, mas o Whisper prefere float32. Vamos converter apenas para verificação.
    
    # Cálculos de buffer
    # config.MIC_SAMPLERATE é 16000
    samples_per_frame = int(config.MIC_SAMPLERATE * frame_duration_ms / 1000)
    
    # Limites
    silence_threshold_seconds = 1.5  # Para de gravar após 1.5s de silêncio
    max_duration_seconds = 10.0      # Segurança: para se nunca calares a boca
    
    # Buffers
    frames = []
    silence_counter = 0
    speech_detected = False
    chunks_per_second = 1000 // frame_duration_ms
    silence_limit_chunks = int(silence_threshold_seconds * chunks_per_second)
    max_chunks = int(max_duration_seconds * chunks_per_second)

    try:
        with sd.InputStream(samplerate=config.MIC_SAMPLERATE, channels=1, dtype='int16') as stream:
            for _ in range(max_chunks):
                # Lê um chunk de áudio
                audio_chunk, overflowed = stream.read(samples_per_frame)
                
                if overflowed:
                    print("AVISO: Audio buffer overflow.")

                # Converte para bytes para o VAD
                audio_bytes = audio_chunk.tobytes()
                
                # Verifica se é voz
                is_speech = vad.is_speech(audio_bytes, config.MIC_SAMPLERATE)

                # Lógica de Controlo
                if is_speech:
                    silence_counter = 0
                    speech_detected = True
                else:
                    silence_counter += 1

                # Guarda o frame (convertendo para float32 para o Whisper mais tarde)
                # Normalização de int16 para float32: dividir por 32768.0
                frames.append(audio_chunk.flatten().astype(np.float32) / 32768.0)

                # Condição de paragem: Falou e depois calou-se
                if speech_detected and silence_counter > silence_limit_chunks:
                    print("Fim de fala detetado.")
                    break
        
        print("Gravação terminada.")
        
        # Se não detetou fala nenhuma (apenas ruído de fundo ou silêncio), retorna vazio
        if not speech_detected:
            return np.array([], dtype='float32')

        return np.concatenate(frames)

    except Exception as e:
        print(f"ERRO crítico na gravação VAD: {e}")
        return np.array([], dtype='float32')
