import threading
import time
import datetime
import random
import ollama
import config
from tools import search_with_searxng
from data_utils import save_to_rag

# --- Configuração ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["vai sonhar", "aprende algo", "desenvolve a persona", "vai estudar"]

# Hora a que o assistente vai "sonhar" sozinho (formato 24h)
DREAM_TIME = "02:30" 

def perform_dreaming():
    """ 
    Processo de 3 etapas: 
    1. Introspeção (Gerar Tópico)
    2. Pesquisa (SearxNG)
    3. Internalização (Guardar no RAG com a persona)
    """
    print("💤 [Dream] A iniciar processo de aprendizagem noturna...")
    
    # 1. INTROSPEÇÃO
    # Pede ao Ollama para inventar uma curiosidade que a persona gostaria de saber
    introspection_prompt = f"""
    {config.SYSTEM_PROMPT}
    
    TASK: You are alone and thinking. Based on your dark/vegan/philosophical persona, generate a single, specific search query to learn something new today.
    It could be about gothic history, ethical veganism, space, or melancholic poetry.
    OUTPUT: Write ONLY the search query string. No quotes, no preamble.
    """
    
    try:
        # Usa o modelo primário para gerar a query
        client = ollama.Client(timeout=60)
        resp_intro = client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=[{'role': 'user', 'content': introspection_prompt}])
        search_query = resp_intro['message']['content'].strip().replace('"', '')
        
        print(f"💤 [Dream] Tópico escolhido: '{search_query}'")
        
        # 2. PESQUISA NA WEB
        # Usa a ferramenta existente para ir buscar factos
        search_results = search_with_searxng(search_query, max_results=3)
        
        if not search_results or len(search_results) < 10:
            print("💤 [Dream] O sonho foi vazio (sem resultados na web).")
            return "Tentei aprender algo novo, mas a neblina da web estava demasiado espessa."

        # 3. INTERNALIZAÇÃO
        # Pede ao Ollama para reescrever os factos como se fosse uma memória ou reflexão pessoal
        internalize_prompt = f"""
        {config.SYSTEM_PROMPT}
        
        CONTEXT FROM WEB:
        {search_results}
        
        TASK: Internalize this information. Write a short, first-person thought or memory based on these facts.
        It MUST sound like YOU (The Phantom). Dark, concise, and profound.
        Start with phrases like "Nas minhas deambulações descobri...", "A noite ensinou-me...", "Refleti que...".
        OUTPUT: The thought in Portuguese (Portugal). Max 2 sentences.
        """
        
        resp_final = client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=[{'role': 'user', 'content': internalize_prompt}])
        thought = resp_final['message']['content'].strip()
        
        # 4. GUARDAR NA MEMÓRIA (RAG)
        # Ao usar save_to_rag, isto fica disponível para o retrieve_from_rag no futuro
        save_to_rag(thought)
        
        print(f"💤 [Dream] Memória guardada: {thought}")
        return f"A minha mente expandiu-se nas sombras. {thought}"

    except Exception as e:
        print(f"ERRO [Dream]: {e}")
        return "Tive um pesadelo e não consegui aprender nada."

# --- Daemon de Agendamento ---

def _daemon_loop():
    """ Verifica a hora a cada 30s e dispara o sonho às 04:00 """
    print(f"[Dream] Daemon agendado para as {DREAM_TIME}...")
    while True:
        now = datetime.datetime.now()
        current_time = now.strftime("%H:%M")
        
        if current_time == DREAM_TIME:
            try:
                # Executa o sonho
                perform_dreaming()
                # Espera 65 segundos para garantir que não repete no mesmo minuto
                time.sleep(65)
            except Exception as e:
                print(f"ERRO CRÍTICO [Dream Daemon]: {e}")
                time.sleep(60)
            
        time.sleep(30)

def init_skill_daemon():
    """ Iniciado automaticamente pelo assistant.py """
    t = threading.Thread(target=_daemon_loop, daemon=True)
    t.start()

# --- Gatilho Manual (Voz) ---

def handle(user_prompt_lower, user_prompt_full):
    """ Permite forçar o processo via comando de voz """
    # Não precisa de lógica complexa, o router já validou o trigger
    return perform_dreaming()
