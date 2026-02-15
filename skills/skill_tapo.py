import os
import time
import ollama
import config
import subprocess

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["vês", "ver", "câmara", "vigia", "olha", "sala", "quarto"]

def _get_ffmpeg_snapshot(ip):
    """
    Captura e redimensiona o frame via RTSP.
    Ajustado para 720px de largura para acelerar a análise do Ollama.
    """
    img_path = f"/tmp/phantasma_eye_{int(time.time())}.jpg"
    rtsp_url = f"rtsp://{config.TAPO_USER}:{config.TAPO_PASS}@{ip}:554/stream1"
    
    cmd = [
        'ffmpeg', '-loglevel', 'error',
        '-rtsp_transport', 'tcp',
        '-i', rtsp_url,
        '-vf', 'scale=720:-1', # Redimensiona para 720px mantendo o aspect ratio
        '-frames:v', '1',
        '-q:v', '5',           # Ligeiro aumento na compressão para reduzir o tamanho
        '-update', '1',
        '-y', img_path
    ]
    
    try:
        # Timeout de 5s para evitar processos zombie em rede instável
        subprocess.run(cmd, check=True, timeout=5)
        if os.path.exists(img_path):
            return img_path
    except Exception as e:
        print(f"ERRO [FFmpeg]: {e}")
    return None

def _vision_cycle(target_ip):
    """Pipeline otimizado: Snapshot Leve -> Llava -> Resposta."""
    img_path = _get_ffmpeg_snapshot(target_ip)
    
    if not img_path:
        return "A vigia está obscurecida ou a câmara está inacessível."

    try:
        # Log minimalista para o terminal
        print(f"Phantasma: Analisando frame com {config.OLLAMA_VISION_MODEL}...")
        
        res = ollama.generate(
            model=config.OLLAMA_VISION_MODEL,
            prompt="Descreve o que vês nesta imagem de forma muito breve, focando em pessoas ou objetos.",
            images=[img_path]
        )
        
        if os.path.exists(img_path):
            os.remove(img_path)
            
        return f"Através da vigia, observo o seguinte: {res['response']}"
    except Exception as e:
        return f"A visão falhou ao processar a imagem: {str(e)}"

def handle(user_prompt_lower, user_prompt_full):
    # 1. Identificar a câmara pelo nome no prompt
    target_ip = None
    for name, ip in config.TAPO_CAMERAS.items():
        if name in user_prompt_lower:
            target_ip = ip
            break
    
    if not target_ip:
        target_ip = list(config.TAPO_CAMERAS.values())[0]

    # 2. Resposta se o prompt for um pedido de visualização
    if any(word in user_prompt_lower for word in TRIGGERS):
        return _vision_cycle(target_ip)

    return None
