import threading
import time
import datetime
import random
import sqlite3
import ollama
import config
from tools import search_with_searxng
from data_utils import save_to_rag

# --- Configuração ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["vai sonhar", "aprende algo", "desenvolve a persona", "vai estudar"]

# Hora a que o assistente vai "sonhar" sozinho (formato 24h)
DREAM_TIME = "02:30" 

# Número de memórias passadas a consultar para dar contexto ao novo sonho
MEMORY_CONTEXT_LIMIT = 3

def _get_recent_memories():
    """ 
    Lê as últimas entradas da BD local para dar contexto ao sonho.
    Não usa o RAG (que é por keyword), mas sim um SELECT direto por ordem cronológica.
    """
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        # Recupera as últimas X memórias
        cursor.execute("SELECT text FROM memories ORDER BY id DESC LIMIT ?", (MEMORY_CONTEXT_LIMIT,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return "No previous memories found. This is the first thought."
            
        # Inverte para ficar cronológico (Antigo -> Novo)
        history = [r[0] for r in reversed(rows)]
        return "\n".join(history)
    except Exception as e:
        print(f"ERRO [Dream] Ler DB: {e}")
        return ""

def perform_dreaming():
    """ 
    Processo de 3 etapas com Continuidade:
    1. Contexto (Ler sonhos anteriores) -> Introspeção (Gerar Tópico)
    2. Pesquisa (SearxNG)
    3. Internalização Otimizada (Guardar formato Denso para LLM)
    """
    print("💤 [Dream] A iniciar processo de aprendizagem noturna...")
    
    # 0. CONTEXTO
    recent_context = _get_recent_memories()
    
    # 1. INTROSPEÇÃO
    # Pede ao Ollama para gerar uma query.
    # NOTA: Removemos os temas hardcoded. Ele agora deve seguir o SYSTEM_PROMPT.
    introspection_prompt = f"""
    {config.SYSTEM_PROMPT}
    
    PREVIOUS THOUGHTS (Context):
    {recent_context}
    
    TASK: You are alone in the void. Analyze your previous thoughts above.
    Based strictly on your ETHICAL CORE (Veganism, Equality) and your PERSONA (The Phantom), generate a SINGLE, specific search query to investigate the next logical step of this knowledge path.
    If the context is empty, choose a topic that matters deeply to your specific PERSONA defined above.
    
    OUTPUT: Write ONLY the search query string. No quotes, no preamble.
    """
    
    try:
        # Usa o modelo primário para gerar a query
        client = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        resp_intro = client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=[{'role': 'user', 'content': introspection_prompt}])
        search_query = resp_intro['message']['content'].strip().replace('"', '')
        
        print(f"💤 [Dream] Tópico evolutivo: '{search_query}'")
        
        # 2. PESQUISA NA WEB
        # Usa a ferramenta existente para ir buscar factos
        search_results = search_with_searxng(search_query, max_results=3)
        
        if not search_results or len(search_results) < 10:
            print("💤 [Dream] O sonho foi vazio (sem resultados na web).")
            return "A neblina da web estava demasiado espessa para aprender algo novo."

        # 3. INTERNALIZAÇÃO OTIMIZADA PARA LLM
        # Aqui instruímos o modelo a ignorar a "conversa" e guardar factos puros.
        internalize_prompt = f"""
        {config.SYSTEM_PROMPT}
        
        CONTEXT FROM WEB:
        {search_results}
        
        TASK: Compress this information into a DENSE KNOWLEDGE REPRESENTATION for your long-term memory.
        - Ignore grammar and stop words.
        - Focus on entities, relationships, numbers, and definitions.
        - Format strictly for machine reading/RAG retrieval optimization.
        - Language: Portuguese (Portugal).
        
        OUTPUT EXAMPLE: 
        Tópico: Buracos Negros. Definição: Região espaço-tempo gravidade extrema. Horizonte eventos: ponto sem retorno. Hawking Radiation: emissão teórica termodinâmica.
        """
        
        resp_final = client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=[{'role': 'user', 'content': internalize_prompt}])
        dense_thought = resp_final['message']['content'].strip()
        
        # 4. GUARDAR NA MEMÓRIA (RAG)
        # Guarda o texto denso
        save_to_rag(dense_thought)
        
        print(f"💤 [Dream] Conhecimento compactado e arquivado: {dense_thought[:50]}...")
        
        # Retorna uma mensagem genérica para o utilizador/log
        return f"Expandir o meu conhecimento sobre '{search_query}'. Dados assimilados no núcleo."

    except Exception as e:
        print(f"ERRO [Dream]: {e}")
        return "Tive um pesadelo e a conexão falhou."

# --- Daemon de Agendamento ---

def _daemon_loop():
    """ Verifica a hora a cada 30s e dispara o sonho às 02:30 """
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
    # Prioridade de lógica: Não existe 'Desliga' nesta skill, apenas trigger de ação única.
    return perform_dreaming()
