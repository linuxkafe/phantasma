# Esta skill precisa de funções de áudio
from audio_utils import play_tts, play_random_song_full

TRIGGER_TYPE = "contains"
TRIGGERS = ["música", "som"]

# Gatilhos de Ação (para evitar falsos positivos)
ACTIONS = ["toca", "mete", "coloca", "põe", "quando"]

def handle(user_prompt_lower, user_prompt_full):
    """ Toca uma música aleatória. """
    
    has_action = any(action in user_prompt_lower for action in ACTIONS)
    has_object = any(obj in user_prompt_lower for obj in TRIGGERS)
    is_short_command = len(user_prompt_lower.split()) <= 2

    if has_object and (has_action or is_short_command):
        print("Intenção: Tocar música.")
        
        # Esta skill é especial: toca o TTS primeiro, DEPOIS a música.
        llm_response = "A postos! A tocar música."
        play_tts(llm_response) 
        
        success = play_random_song_full()
        if not success:
            llm_response = "Desculpa, chefe, não encontrei nenhuma música em /home/media/music."
            play_tts(llm_response)
        
        # Retorna um dicionário especial para dizer ao router para PARAR
        return {"response": llm_response, "stop_processing": True}
        
    return None # Não é um comando de música
