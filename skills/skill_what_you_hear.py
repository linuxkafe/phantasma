"""
Skill: what_you_hear
Responde a "o que ouves?" capturando um trecho curto de áudio do microfone,
transcrevendo via Whisper e devolvendo a transcrição.
Se invocado via voz, o assistente fará o TTS da resposta automaticamente.
"""

import sounddevice as sd

from config import config
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

    try:
        sample_rate = config.audio.sample_rate
        device = config.audio.device_in
        duration = CAPTURE_SECONDS

        logger.info(f"Capturando {duration}s de áudio...")
        audio_data = sd.rec(
            int(duration * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            device=device,
        )
        sd.wait()

        if audio_data.size == 0:
            return "Não consegui capturar áudio."

        audio_data = audio_data.flatten()

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

    except Exception as e:
        logger.error(f"Erro na captura/ transcrição: {e}")
        return "Não consegui processar o áudio."
