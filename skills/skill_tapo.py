import os
import time
import ollama
import config
import subprocess

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["vês", "ver", "câmara", "vigia", "olha", "sala", "quarto"]

# Dicionário de "Localização" PT-BR -> PT-PT
TRANSLATION_MAP = {
    "laptop": "portátil",
    "tela": "ecrã",
    "celular": "telemóvel",
    "cachorro": "cão",
    "lapa": "colo",
    "hospitalidade": "convívio", # Frequentemente alucinado pelo Llava
    "mordomo": "suporte",
    "banheiro": "casa de banho",
    "geladeira": "frigorífico"
}

def _get_ffmpeg_snapshot(ip):
    """Captura e redimensiona o frame via RTSP para acelerar a análise."""
    img_path = f"/tmp/phantasma_eye_{int(time.time())}.jpg"
    rtsp_url = f"rtsp://{config.TAPO_USER}:{config.TAPO_PASS}@{ip}:554/stream1"
    
    cmd = [
        'ffmpeg', '-loglevel', 'error',
        '-rtsp_transport', 'tcp',
        '-i', rtsp_url,
        '-vf', 'scale=720:-1',
        '-frames:v', '1',
        '-q:v', '5',
        '-update', '1',
        '-y', img_path
    ]
    
    try:
        subprocess.run(cmd, check=True, timeout=5)
        if os.path.exists(img_path):
            return img_path
    except Exception as e:
        print(f"ERRO [FFmpeg]: {e}")
    return None

def _refine_response(text):
    """Limpa clichês de IA e traduz para PT-PT."""
    # 1. Remover introduções robóticas
    prefixes = ["na imagem,", "vemos na imagem", "a imagem mostra", "observo que", "esta imagem exibe"]
    for prefix in prefixes:
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
    
    # 2. Aplicar mapa de tradução
    words = text.split()
    refined_words = [TRANSLATION_MAP.get(w.lower().strip(",."), w) for w in words]
    
    return " ".join(refined_words).capitalize()

def _vision_cycle(target_ip):
    """Pipeline: Snapshot -> Llava (Prompt Direto) -> Refinação -> Resposta."""
    img_path = _get_ffmpeg_snapshot(target_ip)
    
    if not img_path:
        return "A vigia está inacessível. Verifica a rede."

    try:
        # Prompt de sistema para evitar alucinações técnicas
        prompt = (
            "Age como o sistema central Phantasma. Descreve o que vês de forma "
            "casual e muito direta, como se falasses com um amigo. "
            "Foca apenas no essencial: pessoas, ações e objetos principais. "
            "Não descrevas a imagem, apenas o conteúdo. Sê conciso."
        )
        
        res = ollama.generate(
            model=config.OLLAMA_VISION_MODEL,
            prompt=prompt,
            images=[img_path]
        )
        
        # Limpar o ficheiro imediatamente para segurança
        if os.path.exists(img_path):
            os.remove(img_path)
            
        final_text = _refine_response(res['response'].strip())
        return f"Vigia: {final_text}"
        
    except Exception as e:
        return f"A visão falhou: {str(e)}"

def handle(user_prompt_lower, user_prompt_full):
    # 1. Identificar a câmara pelo nome no prompt
    target_ip = None
    for name, ip in config.TAPO_CAMERAS.items():
        if name in user_prompt_lower:
            target_ip = ip
            break
    
    if not target_ip:
        target_ip = list(config.TAPO_CAMERAS.values())[0]

    # 2. Executar ciclo de visão
    if any(word in user_prompt_lower for word in TRIGGERS):
        return _vision_cycle(target_ip)

    return None
