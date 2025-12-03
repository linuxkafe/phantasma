import threading
import time
import datetime
import random
import sqlite3
import json
import re
import ollama
import config
from tools import search_with_searxng
from data_utils import save_to_rag

# --- Configuração ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["vai sonhar", "aprende algo", "desenvolve a persona", "vai estudar"]

# Hora a que o assistente vai "sonhar" sozinho (formato 24h)
DREAM_TIME = "02:30" 

# Configuração de Consolidação
MEMORY_CHUNK_SIZE = 5

def _get_recent_memories(limit=3):
    """ Lê as últimas entradas para contexto simples. """
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT text FROM memories ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        if not rows: return "No previous memories."
        return "\n".join([r[0] for r in reversed(rows)])
    except Exception as e:
        print(f"ERRO [Dream] Ler DB: {e}")
        return ""

def _repair_malformed_json(text):
    """ Tenta corrigir erros comuns de alucinação JSON do LLM. """
    pattern = r'\{\s*"(.*?)"\s*->\s*"(.*?)"\s*->\s*"(.*?)"\s*\}'
    text = re.sub(pattern, r'"\1 -> \2 -> \3"', text)
    return text

def _extract_json(text):
    """ Tenta extrair um objeto JSON válido de uma string suja. """
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        candidate = match.group(0) if match else text
        return json.loads(candidate)
    except json.JSONDecodeError:
        try:
            fixed_text = _repair_malformed_json(candidate)
            return json.loads(fixed_text)
        except:
            return None
    except Exception:
        return None

def _consolidate_memories():
    """ TAREFA DE MANUTENÇÃO: Funde memórias. """
    print("🧠 [Dream] A iniciar consolidação de memória...")
    
    conn = None
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, text FROM memories ORDER BY id DESC LIMIT ?", (MEMORY_CHUNK_SIZE,))
        rows = cursor.fetchall()
        
        if len(rows) < MEMORY_CHUNK_SIZE:
            return

        ids_to_delete = [r[0] for r in rows]
        
        processed_texts = []
        for r in rows:
            txt = r[1]
            try:
                json.loads(txt)
                processed_texts.append(txt)
            except:
                processed_texts.append(f"RAW_TEXT_ENTRY: {txt}")

        consolidation_prompt = f"""
        SYSTEM: You are a Database Cleaner. Input contains mixed JSON and RAW TEXT.
        INPUT DATA: {json.dumps(processed_texts, ensure_ascii=False)}
        TASK: Convert RAW_TEXT_ENTRY items into "Subject -> Predicate -> Object" facts (English). Merge with existing JSON. Flatten arrays. Output SINGLE valid JSON.
        OUTPUT FORMAT: {{ "tags": ["TagPT"], "facts": ["Subj -> verb -> Obj"] }}
        """
        
        client = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        
        # LÊ O LIMITE DO CONFIG
        ctx_limit = getattr(config, 'OLLAMA_CONTEXT_SIZE', 4096)

        resp = client.chat(
            model=config.OLLAMA_MODEL_PRIMARY, 
            messages=[{'role': 'user', 'content': consolidation_prompt}],
            options={'num_ctx': ctx_limit}
        )

        merged_json_obj = _extract_json(resp['message']['content'])
        
        if not merged_json_obj:
            print("ERRO [Dream] Consolidação falhou: JSON inválido.")
            return

        merged_json_str = json.dumps(merged_json_obj, ensure_ascii=False)
        
        placeholders = ', '.join('?' * len(ids_to_delete))
        cursor.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids_to_delete)
        cursor.execute("INSERT INTO memories (timestamp, text) VALUES (?, ?)", (datetime.datetime.now(), merged_json_str))
        
        conn.commit()
        print(f"🧠 [Dream] Consolidação concluída! {len(ids_to_delete)} itens fundidos.")
        
    except Exception as e:
        print(f"ERRO [Dream] Falha na consolidação: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

def perform_dreaming():
    """ Ciclo de Sonho Completo. """
    print("💤 [Dream] A iniciar processo de aprendizagem noturna...")
    
    recent_context = _get_recent_memories()
    
    introspection_prompt = f"""
    {config.SYSTEM_PROMPT}
    PREVIOUS MEMORIES: {recent_context}
    TASK: Analyze knowledge gaps. Based on ETHICAL CORE/PERSONA, generate ONE search query.
    OUTPUT: Search query string ONLY. No quotes.
    """
    
    try:
        client = ollama.Client(timeout=config.OLLAMA_TIMEOUT)
        
        # LÊ O LIMITE DO CONFIG
        ctx_limit = getattr(config, 'OLLAMA_CONTEXT_SIZE', 4096)
        
        # --- CORREÇÃO 1 ---
        resp_intro = client.chat(
            model=config.OLLAMA_MODEL_PRIMARY, 
            messages=[{'role': 'user', 'content': introspection_prompt}],
            options={'num_ctx': ctx_limit}
        )
        search_query = resp_intro['message']['content'].strip().replace('"', '')
        
        print(f"💤 [Dream] Tópico: '{search_query}'")
        
        search_results = search_with_searxng(search_query, max_results=3)
        if not search_results or len(search_results) < 10:
            return "Sonho vazio (sem dados)."

        internalize_prompt = f"""
        SYSTEM: You are a Data Extractor. Output JSON ONLY.
        WEB CONTEXT: {search_results}
        TASK: Extract knowledge to JSON.
        RULES: 1. CLEAN DATA only. 2. "tags": Array of keyword strings in PORTUGUESE. 3. "facts": Array of STRINGS in ENGLISH. 4. Strict JSON syntax.
        OUTPUT FORMAT: {{ "tags": ["TagPT"], "facts": ["Subject -> verb -> Object"] }}
        """
        
        # --- CORREÇÃO 2 ---
        resp_final = client.chat(
            model=config.OLLAMA_MODEL_PRIMARY, 
            messages=[{'role': 'user', 'content': internalize_prompt}],
            options={'num_ctx': ctx_limit}
        )
        
        json_data = _extract_json(resp_final['message']['content'])
        
        if not json_data:
            print(f"ERRO [Dream] JSON Inválido: {resp_final['message']['content']}")
            return "Falha ao estruturar o sonho."
            
        dense_thought = json.dumps(json_data, ensure_ascii=False)
        save_to_rag(dense_thought)
        print(f"💤 [Dream] Conhecimento arquivado: {json_data.get('tags', [])}")
        
        _consolidate_memories()
        return f"Conhecimento sobre '{search_query}' assimilado."

    except Exception as e:
        print(f"ERRO CRÍTICO [Dream]: {e}")
        return "Pesadelo de conexão."

def _daemon_loop():
    print(f"[Dream] Daemon agendado para as {DREAM_TIME}...")
    while True:
        now = datetime.datetime.now()
        current_time = now.strftime("%H:%M")
        if current_time == DREAM_TIME:
            try:
                perform_dreaming()
                time.sleep(65)
            except Exception as e:
                print(f"ERRO CRÍTICO [Dream Daemon]: {e}")
                time.sleep(60)
        time.sleep(30)

def init_skill_daemon():
    t = threading.Thread(target=_daemon_loop, daemon=True)
    t.start()

def handle(user_prompt_lower, user_prompt_full):
    return perform_dreaming()
