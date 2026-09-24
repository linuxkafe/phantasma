"""
Skill: what_you_hear
Responde a "o que ouves?" capturando um trecho curto de áudio do microfone,
transcrevendo via Whisper e devolvendo a transcrição.
Se invocado via voz, o assistente fará o TTS da resposta automaticamente.
"""

import numpy as np

from config import config
from src.pipeline.audio import AudioCapture
from src.pipeline.stt import transcribe as stt_transcribe
from src.pipeline.utils import logger

# --- Configuração da Skill ---
TRIGGER_TYPE = "contains"
TRIGGERS = [
    "o que ouves",
    "o que você ouve",
    "o que escutas",
    "o que estás a ouvir",
]

# Duração da captura em segundos
CAPTURE_SECONDS = 3


def handle(user_prompt_lower: str, user_prompt_full: str) -> str:
    """
    Captura áudio do microfone por CAPTURE_SECONDS, transcreve e devolve o texto.
    """
    logger.info("Skill 'what_you_hear' ativada: a capturar áudio...")

    # Configuração de captura a partir do config
    capture = AudioCapture(
        device=config.audio.device_in,
        sample_rate=config.audio.sample_rate,
        channels=config.audio.channels,
        block_size=config.audio.block_size,
        dtype=config.audio.dtype,
        queue_maxsize=config.pipeline.queue_maxsize,
    )

    start_res = capture.start()
    if not start_res.success:
        logger.error(f"Falha ao iniciar captura de áudio: {start_res.error}")
        return "Não consegui iniciar a captura de áudio."

    try:
        # Calcula número de blocos necessários
        blocks_needed = int(
            CAPTURE_SECONDS * config.audio.sample_rate / config.audio.block_size
        )
        frames = []
        for _ in range(blocks_needed):
            res = capture.get_frame(timeout=1.0)
            if not res.success:
                continue
            frames.append(res.data)
            if len(frames) >= blocks_needed:
                break

        if not frames:
            return "Não consegui capturar áudio."

        audio_data = np.concatenate(frames)

        # Limita duração máxima
        max_samples = int(CAPTURE_SECONDS * config.audio.sample_rate)
        if len(audio_data) > max_samples:
            audio_data = audio_data[:max_samples]

        # Transcrição
        stt_result = stt_transcribe(audio_data)
        if not stt_result.success:
            logger.error(f"STT falhou: {stt_result.error}")
            return "Não consegui transcrever o áudio."

        text = stt_result.data.strip()
        if not text:
            return "Não detectei fala no trecho capturado."

        logger.info(f"Transcrição: {text}")
        return f"Ouço: {text}"

    finally:
        capture.stop()
