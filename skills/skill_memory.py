# vim skill_memory.py
import config
import ollama
import json
from datetime import datetime
from data_utils import save_to_rag

TRIGGER_TYPE = "startswith"
TRIGGERS = ["memoriza", "lembra-te disto", "grava isto", "guarda isto", "anota"]

def handle(user_prompt_lower, user_prompt_full):
    trigger_found = next((t for t in TRIGGERS if user_prompt_lower.startswith(t)), None)
    text_to_save = user_prompt_full[len(trigger_found):].strip()
    
    if not text_to_save:
        return "O vazio não pode ser memorizado. Diz-me o que queres que eu guarde."

    # Injetamos a data atual para o LLM entender a precedência histórica
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    structure_prompt = f"""
    SYSTEM: You are a Knowledge Architect. Output JSON only.
    CURRENT DATE: {now_str}
    
    USER INPUT: "{text_to_save}"
    
    TASK: Convert this into a Mermaid.js graph. 
    RULES:
    1. If the input updates an existing fact, the graph must reflect the NEW state.
    2. Tags in PT-PT. Graph in English.
    3. JSON ONLY.
    
    FORMAT: {{ "tags": ["Tag"], "mermaid": "graph TD; ..." }}
    """

    try:
        client = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        resp = client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=[{'role': 'user', 'content': structure_prompt}])
        
        # Limpeza e parsing (preservando a tua lógica original)
        json_output = resp['message']['content'].strip()
        if "```" in json_output:
            json_output = json_output.split("```json")[-1].split("```")[0].strip()
        
        save_to_rag(json_output) # Guardamos o JSON estruturado
        return "As sombras foram mapeadas e datadas no meu grafo."
    except Exception as e:
        save_to_rag(text_to_save)
        return "A estrutura falhou, mas a essência foi gravada."
