# skill_tts.py

# Definimos o trigger para intercetar o início da frase
TRIGGERS = ["diz"]
TRIGGER_TYPE = "startswith"

def handle(p_low, prompt):
    """
    Lógica para repetir o que o utilizador pede.
    """
    # O prompt original preserva a capitalização e pontuação,
    # o que resulta numa entoação de TTS muito mais natural.
    # Cortamos os primeiros 4 caracteres ("diz ")
    if len(prompt) > 4:
        message = prompt[4:].strip()
        if message:
            return message
            
    return "Não me disseste o que é para dizer."

def get_status_for_device(nickname):
    # Esta skill não controla hardware, logo não precisa de status
    return None
