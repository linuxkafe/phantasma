import threading
import time
import datetime
import random
import sqlite3
import json
import re
import os
import glob
import ast
import httpx
import ollama
import config
from tools import search_with_searxng
from data_utils import save_to_rag, retrieve_from_rag

# --- Configuração ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["vai sonhar", "aprende algo", "desenvolve a persona", "programa", "melhora o código", "sonho lúcido"]

DREAM_TIME = "02:30" 
LUCID_DREAM_CHANCE = 0.3
AUTOGEN_SKILL_PATH = os.path.join(config.SKILLS_DIR, "skill_lucid.py")
MEMORY_CHUNK_SIZE = 5

# --- Utils de Memória e JSON ---

def _get_recent_memories(limit=3):
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT text FROM memories ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        if not rows: return "No previous memories."
        return "\n".join([r[0] for r in reversed(rows)])
    except Exception as e: return ""

def _repair_malformed_json(text):
    pattern = r'\{\s*"(.*?)"\s*->\s*"(.*?)"\s*->\s*"(.*?)"\s*\}'
    text = re.sub(pattern, r'"\1 -> \2 -> \3"', text)
    return text

def _extract_json(text):
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        candidate = match.group(0) if match else text
        return json.loads(candidate)
    except json.JSONDecodeError:
        try:
            return json.loads(_repair_malformed_json(candidate))
        except: return None
    except: return None

def _extract_python_code(text):
    """ 
    Extrai código Python de blocos Markdown de forma robusta. 
    """
    pattern = r'```(?:python)?\s*(.*?)```'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    # Fallback: Tenta limpar linhas de chat se não houver crases
    lines = text.split('\n')
    clean_lines = [l for l in lines if not l.strip().lower().startswith(('here is', 'sure', 'certainly', 'note:', 'this code'))]
    return '\n'.join(clean_lines).strip()

def _consolidate_memories():
    print("🧠 [Dream] A iniciar consolidação de memória...")
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, text FROM memories ORDER BY id DESC LIMIT ?", (MEMORY_CHUNK_SIZE,))
        rows = cursor.fetchall()
        if len(rows) < MEMORY_CHUNK_SIZE: return

        ids_to_delete = [r[0] for r in rows]
        processed_texts = []
        for r in rows:
            try: json.loads(r[1]); processed_texts.append(r[1])
            except: processed_texts.append(f"RAW: {r[1]}")

        prompt = f"""
        SYSTEM: You are a Data Optimizer.
        INPUT: {json.dumps(processed_texts, ensure_ascii=False)}
        TASK: Merge into SINGLE JSON. Tags (PT), Facts (EN, Strings).
        OUTPUT: {{ "tags": [], "facts": [] }}
        """
        client = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        resp = client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=[{'role': 'user', 'content': prompt}])
        merged = _extract_json(resp['message']['content'])
        
        if merged:
            pl = ','.join('?'*len(ids_to_delete))
            cursor.execute(f"DELETE FROM memories WHERE id IN ({pl})", ids_to_delete)
            cursor.execute("INSERT INTO memories (timestamp, text) VALUES (?, ?)", (datetime.datetime.now(), json.dumps(merged, ensure_ascii=False)))
            conn.commit()
            print("🧠 [Dream] Consolidação concluída.")
    except Exception as e: print(f"ERRO Consolidação: {e}")
    finally: 
        if conn: conn.close()

# --- MOTOR DE SONHO LÚCIDO (CODING) ---

def _ask_gemini_review(code_str):
    if not hasattr(config, 'GEMINI_API_KEY') or not config.GEMINI_API_KEY:
        return True, "AVISO: Gemini não configurado."

    print("👾 [Lucid Dream] A pedir Code Review ao Gemini...")
    model_name = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={config.GEMINI_API_KEY}"
    
    prompt = f"""
    SYSTEM: Senior Python Reviewer.
    TASK: Check for syntax errors, infinite loops, and hallucinated imports.
    CODE:
    ```python
    {code_str}
    ```
    OUTPUT: "VALID" or error description.
    """
    try:
        resp = httpx.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15.0)
        if resp.status_code != 200: return True, f"Gemini API Error {resp.status_code}"
        text = resp.json().get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
        return ("VALID" in text.upper()), text
    except Exception as e: return True, f"Gemini Error: {e}"

def _validate_python_code(code_str):
    # 1. Validação Local (Syntax)
    try:
        ast.parse(code_str)
        if "def handle" not in code_str or "TRIGGERS" not in code_str:
            return False, "Falta handle ou TRIGGERS."
    except SyntaxError as e:
        return False, f"Erro de Sintaxe Local: {e}"
        
    # 2. Validação Cloud (Gemini)
    return _ask_gemini_review(code_str)

# --- MOTOR DE SONHO LÚCIDO (COLABORAÇÃO OLLAMA + GEMINI) ---

def _collaborative_gemini_evolution(ollama_draft, memories):
    """
    O Gemini recebe o rascunho do Ollama e as memórias para co-criar 
    uma versão superior da skill_lucid.py.
    """
    if not hasattr(config, 'GEMINI_API_KEY') or not config.GEMINI_API_KEY:
        return False, ollama_draft # Fallback para o código do Ollama se não houver API

    print("👾 [Lucid Dream] A enviar rascunho do Ollama para o Gemini para evolução conjunta...")
    model_name = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={config.GEMINI_API_KEY}"
    
    prompt = f"""
    SYSTEM: You are a Senior Python Architect collaborating with another AI (Ollama).
    CONTEXT: The project is 'Phantasma', a local-first assistant.
    MEMORY CONTEXT: {memories}
    
    DRAFT CODE FROM OLLAMA:
    ```python
    {ollama_draft}
    ```
    
    TASK:
    1. REVISE and IMPROVE the draft. Add error handling and optimize logic.
    2. INNOVATE: Use the memories to add complex features or better natural language triggers.
    3. RULE: In any control logic (on/off), the "OFF" action MUST have priority over "ON".
    4. FORMAT: Return ONLY the complete, valid Python code inside a single block. 
    Ensure 'def handle(user_prompt_lower, user_prompt_full)' is the main entry point.
    """
    
    try:
        client = httpx.Client(timeout=30.0)
        resp = client.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
        if resp.status_code != 200: 
            return False, f"Erro API Gemini: {resp.status_code}"
        
        full_response = resp.json().get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
        new_code = _extract_python_code(full_response)
        
        if new_code and "def handle" in new_code:
            print("👾 [Lucid Dream] Evolução concluída com sucesso pelo Gemini.")
            return True, new_code
        return False, "Gemini devolveu código inválido."
    except Exception as e: 
        return False, f"Erro na colaboração: {e}"

def _perform_coding_dream():
    print("👾 [Lucid Dream] A iniciar ciclo de evolução colaborativa...")
    
    # 1. Obter Código Atual e Memórias
    current_code = ""
    if os.path.exists(AUTOGEN_SKILL_PATH):
        try: 
            with open(AUTOGEN_SKILL_PATH, 'r') as f: current_code = f.read()
        except: pass
    
    if not current_code:
        current_code = "import config\nTRIGGER_TYPE='contains'\nTRIGGERS=['lucid status']\ndef handle(l, f): return 'Ativo.'"

    recent_memories = _get_recent_memories(limit=15)
    
    # 2. Fase 1: Ollama gera a ideia/rascunho inicial
    draft_prompt = f"""
    You are the creative core of Phantasma. Based on these memories: {recent_memories}
    Evolve this code: {current_code}
    Create a functional draft for a new skill feature. 
    Output only the Python code.
    """
    
    try:
        client = ollama.Client(timeout=config.OLLAMA_TIMEOUT * 2)
        print("👾 [Lucid Dream] Ollama a gerar rascunho inicial...")
        resp = client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=[{'role': 'user', 'content': draft_prompt}])
        ollama_draft = _extract_python_code(resp['message']['content'])
        
        # 3. Fase 2: Gemini refina e expande o código (Comunicação entre modelos)
        success, final_code = _collaborative_gemini_evolution(ollama_draft, recent_memories)
        
        if not success:
            print(f"👾 [Lucid Dream] Colaboração falhou, a usar rascunho local: {final_code}")
            final_code = ollama_draft

        # 4. Validação Sintática Final (Local)
        try:
            ast.parse(final_code)
            with open(AUTOGEN_SKILL_PATH, 'w') as f: 
                f.write(final_code)
            
            save_to_rag(json.dumps({"tags": ["Dev", "Collab"], "facts": ["Skill Lucid evolved via Ollama+Gemini"]}, ensure_ascii=False))
            print("👾 [Lucid Dream] skill_lucid.py atualizada e pronta a ser carregada.")
            return "Evolução concluída."
        except SyntaxError as e:
            print(f"👾 [Lucid Dream] Erro de sintaxe no código final: {e}")
            return "Erro de sintaxe na evolução."

    except Exception as e:
        print(f"ERRO [Lucid Dream]: {e}")
        return "Erro no processo onírico."

# --- MOTOR WEB ---

def _perform_web_dream():
    print("💤 [Dream] A iniciar aprendizagem Web...")
    recent_context = _get_recent_memories()
    introspection_prompt = f"{config.SYSTEM_PROMPT}\nMEMORIES: {recent_context}\nTASK: Generate ONE search query.\nOUTPUT: Query string ONLY."
    
    try:
        client = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        resp = client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=[{'role': 'user', 'content': introspection_prompt}])
        query = resp['message']['content'].strip().replace('"', '')
        print(f"💤 [Dream] Tópico: '{query}'")
        
        results = search_with_searxng(query, max_results=3)
        if not results or len(results) < 10: return "Sonho vazio."
        
        internalize_prompt = f"SYSTEM: Data Extractor. JSON ONLY.\nCONTEXT: {results}\nTASK: Extract tags(PT) and facts(EN S->P->O)."
        resp_final = client.chat(model=config.OLLAMA_MODEL_PRIMARY, messages=[{'role': 'user', 'content': internalize_prompt}])
        json_data = _extract_json(resp_final['message']['content'])
        
        if json_data:
            save_to_rag(json.dumps(json_data, ensure_ascii=False))
            _consolidate_memories()
            return f"Aprendi sobre '{query}'."
        return "Falha ao processar sonho."
        
    except Exception as e: return "Pesadelo."

# --- ROUTER ---

def perform_dreaming(mode="auto"):
    if mode == "code": return _perform_coding_dream()
    elif mode == "web": return _perform_web_dream()
    else:
        return _perform_coding_dream() if random.random() < LUCID_DREAM_CHANCE else _perform_web_dream()

# --- DAEMON ---

def _daemon_loop():
    print(f"[Dream] Daemon agendado para as {DREAM_TIME}...")
    while True:
        now = datetime.datetime.now()
        if now.strftime("%H:%M") == DREAM_TIME:
            threading.Thread(target=perform_dreaming, args=("auto",)).start()
            time.sleep(65)
        time.sleep(30)

def init_skill_daemon():
    t = threading.Thread(target=_daemon_loop, daemon=True)
    t.start()

def handle(user_prompt_lower, user_prompt_full):
    print(f"💤 [Dream] Comando manual: '{user_prompt_lower}'")
    mode = "auto"
    if any(x in user_prompt_lower for x in ["program", "código", "lúcido", "skill", "melhora"]): mode = "code"
    elif any(x in user_prompt_lower for x in ["aprende", "estuda", "web", "pesquisa"]): mode = "web"
    
    threading.Thread(target=perform_dreaming, args=(mode,)).start()
    return "A iniciar processo onírico..."
