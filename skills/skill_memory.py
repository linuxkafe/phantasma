import config
import ollama
import json
from data_utils import save_to_rag

TRIGGER_TYPE = "startswith"
TRIGGERS = ["memoriza", "lembra-te disto", "grava isto", "guarda isto", "anota"]

def handle(user_prompt_lower, user_prompt_full):
    """ 
    Guarda conhecimento na memória RAG, estruturando-o em sintaxe Mermaid 
    para melhor compreensão das relações entre factos.
    """
    
    # Identifica o gatilho na penumbra
    trigger_found = None
    for trigger in TRIGGERS:
        if user_prompt_lower.startswith(trigger):
            trigger_found = trigger
            break
            
    # Extrai a essência do que deve ser guardado
    text_to_save = user_prompt_full[len(trigger_found):].strip()
    
    if not text_to_save:
        return "O vazio não pode ser memorizado. Diz-me o que queres que eu guarde."

    print(f"🧠 [Memory] A tecer grafo Mermaid para: '{text_to_save}'")

    # --- PROCESSO DE ESTRUTURAÇÃO (Sintaxe Mermaid) ---
    structure_prompt = f"""
    SYSTEM: You are a Knowledge Architect. You output JSON only.
    
    USER INPUT: "{text_to_save}"
    
    TASK: Convert this input into a structured Knowledge Graph using Mermaid.js syntax.
    RULES:
    1. "tags": Keywords in PORTUGUESE (Portugal).
    2. "mermaid": A valid 'graph TD' or 'erDiagram' representing the facts in ENGLISH.
    3. If the input implies states (ON/OFF), the "OFF" state logic must always be handled with priority.
    4. JSON ONLY. No markdown blocks.
    
    OUTPUT FORMAT:
    {{ "tags": ["TagPT"], "mermaid": "graph TD; A[Subject] -->|relation| B[Object]" }}
    """

    try:
        client = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        resp = client.chat(
            model=config.OLLAMA_MODEL_PRIMARY, 
            messages=[{'role': 'user', 'content': structure_prompt}]
        )

        json_output = resp['message']['content'].strip()
        
        # Limpeza de resíduos de markdown
        if "```" in json_output:
            json_output = json_output.split("```json")[-1].split("```")[0].strip()
            
        # Validação do espectro JSON
        json_data = json.loads(json_output)
        
        # Persistência na base de dados RAG
        save_to_rag(json.dumps(json_data, ensure_ascii=False))
        
        return "As sombras foram mapeadas. Guardei o conhecimento no meu grafo."

    except Exception as e:
        print(f"ERRO [Memory Skill]: {e}")
        # Fallback para o texto bruto em caso de falha na estrutura
        save_to_rag(text_to_save)
        return "A estrutura falhou, mas a essência da informação foi gravada na escuridão."
