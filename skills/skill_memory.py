# vim skill_memory.py
import config
import ollama
import json
import re
import ast  # Para lidar com dicionários mal formatados
from datetime import datetime
from data_utils import save_to_rag

TRIGGER_TYPE = "startswith"
TRIGGERS = ["memoriza", "lembra-te disto", "grava isto", "guarda isto", "anota"]

def _safe_ollama_chat(prompt):
    targets = [
        (getattr(config, 'OLLAMA_HOST_PRIMARY', None), getattr(config, 'OLLAMA_MODEL_PRIMARY', 'llama3')),
        (getattr(config, 'OLLAMA_HOST_FALLBACK', 'http://localhost:11434'), getattr(config, 'OLLAMA_MODEL_FALLBACK', 'llama3'))
    ]
    for host, model in targets:
        if not host: continue
        try:
            client = ollama.Client(host=host, timeout=config.OLLAMA_TIMEOUT)
            resp = client.chat(model=model, messages=[{'role': 'user', 'content': prompt}])
            return resp['message']['content']
        except: continue
    return None

def handle(user_prompt_lower, user_prompt_full):
    trigger_found = next((t for t in TRIGGERS if user_prompt_lower.startswith(t)), None)
    text_to_save = user_prompt_full[len(trigger_found):].strip()
    
    if not text_to_save:
        return "O vazio não pode ser memorizado."

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    structure_prompt = f"""
    SYSTEM: Knowledge Architect. Output JSON only. 
    TASK: Convert to Mermaid graph. Use ONLY double quotes for keys and strings.
    DATE: {now_str}
    INPUT: "{text_to_save}"
    FORMAT: {{ "tags": ["Tag"], "mermaid": "graph TD;\\nA-->B" }}
    """

    ans = _safe_ollama_chat(structure_prompt)
    
    if ans:
        try:
            # 1. Extração cirúrgica do bloco entre chavetas
            match = re.search(r'(\{.*\})', ans, re.DOTALL)
            if match:
                json_str = match.group(1)
                
                # Debug: Descomenta a linha abaixo para ver o que o Ollama envia nos logs
                # print(f"🔍 DEBUG RAW: {repr(json_str)}")

                try:
                    # Tentativa 1: JSON Padrão (Rígido)
                    data = json.loads(json_str, strict=False)
                except:
                    # Tentativa 2: Fallback para ast (aceita aspas simples e lixo técnico)
                    # O ast.literal_eval é mais seguro que o eval()
                    data = ast.literal_eval(json_str)
                
                if isinstance(data, dict):
                    save_to_rag(json.dumps(data, ensure_ascii=False))
                    return "As sombras foram mapeadas e datadas no meu grafo."
        except Exception as e:
            print(f"⚠️ [Memory Skill] Falha terminal no parse: {e}")

    # Fallback final: Grava o texto bruto para não perder a ideia
    save_to_rag(text_to_save)
    return "A estrutura falhou, mas a essência foi gravada."
