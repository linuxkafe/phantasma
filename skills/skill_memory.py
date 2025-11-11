# Esta skill precisa de importar uma função de outro módulo
from data_utils import save_to_rag

TRIGGER_TYPE = "startswith"
TRIGGERS = ["memoriza", "lembra-te disto", "grava isto", "guarda isto", "anota"]

def handle(user_prompt_lower, user_prompt_full):
    """ Guarda texto na memória RAG. """
    
    # Encontra o trigger usado
    trigger_found = None
    for trigger in TRIGGERS:
        if user_prompt_lower.startswith(trigger):
            trigger_found = trigger
            break
            
    text_to_save = user_prompt_full[len(trigger_found):].strip() # Usa o prompt original
    
    if text_to_save:
        save_to_rag(text_to_save) 
        return f"Entendido, chefe! Memorizado: {text_to_save}" 
    else:
        return "Não percebi o que era para memorizar. Repete lá isso!"
