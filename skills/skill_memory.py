import config
import ollama
import json
from data_utils import save_to_rag

TRIGGER_TYPE = "startswith"
TRIGGERS = ["memoriza", "lembra-te disto", "grava isto", "guarda isto", "anota"]

def handle(user_prompt_lower, user_prompt_full):
    """ 
    Guarda texto na memória RAG, mas primeiro converte-o 
    para o formato JSON estandardizado (Tags PT + Factos EN).
    """
    
    # Encontra o trigger usado
    trigger_found = None
    for trigger in TRIGGERS:
        if user_prompt_lower.startswith(trigger):
            trigger_found = trigger
            break
            
    # Extrai o texto bruto do utilizador
    text_to_save = user_prompt_full[len(trigger_found):].strip()
    
    if not text_to_save:
        return "Não percebi o que era para memorizar. Repete lá isso!"

    print(f"🧠 [Memory] A estruturar memória manual: '{text_to_save}'")

    # --- PROCESSO DE ESTRUTURAÇÃO (Igual ao Dream) ---
    structure_prompt = f"""
    SYSTEM: You are a Data Entry Clerk. You do NOT chat. You output JSON only.
    
    USER INPUT: "{text_to_save}"
    
    TASK: Convert this input into a structured knowledge entry.
    RULES:
    1. "tags": Extract keywords in PORTUGUESE (Portugal) for indexing.
    2. "facts": Extract facts in ENGLISH (Subject -> Predicate -> Object).
    3. JSON ONLY. No markdown.
    
    OUTPUT FORMAT:
    {{ "tags": ["TagPT"], "facts": ["Subject(EN) -> verb -> Object(EN)"] }}
    """

    try:
        client = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        # Usamos o SYSTEM_PROMPT neutro aqui, não a Persona
        resp = client.chat(
            model=config.OLLAMA_MODEL_PRIMARY, 
            messages=[{'role': 'user', 'content': structure_prompt}],
            options={'num_ctx': config.OLLAMA_CONTEXT_SIZE}
        )

        json_output = resp['message']['content'].strip()
        
        # Limpeza de Markdown se necessário
        if "```" in json_output:
            json_output = json_output.split("```json")[-1].split("```")[0].strip()
            
        # Validação simples
        json.loads(json_output)
        
        # Guarda na BD o JSON, não o texto bruto
        save_to_rag(json_output)
        
        return f"Entendido. Guardei isso na minha base de conhecimento."

    except Exception as e:
        print(f"ERRO [Memory Skill]: {e}")
        # Fallback: Se o LLM falhar, guarda o texto bruto para não perder a informação
        save_to_rag(text_to_save)
        return "Guardei a informação, mas tive uma falha ao estruturá-la."
