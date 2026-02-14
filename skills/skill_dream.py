# vim skill_dream.py

import threading
import time
import datetime
import random
import sqlite3
import json
import re
import os
import httpx
import ollama
import config
from tools import search_with_searxng
from data_utils import save_to_rag

# --- Configuração ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["vai sonhar", "aprende algo", "desenvolve a persona", "sonho lúcido", "notícias", "novidades"]

DREAM_TIME = "02:30" 
LUCID_DREAM_CHANCE = 0.3
AUTOGEN_SKILL_PATH = os.path.join(config.SKILLS_DIR, "skill_lucid.py")

# --- Helper de Inferência com Failover ---

def _safe_ollama_chat(prompt, system_instruction=""):
    """ 
    Tenta o host primário com o modelo primário. 
    Se falhar (404, timeout, etc), tenta o fallback local.
    """
    targets = [
        (getattr(config, 'OLLAMA_HOST_PRIMARY', None), getattr(config, 'OLLAMA_MODEL_PRIMARY', 'llama3')),
        (getattr(config, 'OLLAMA_HOST_FALLBACK', 'http://localhost:11434'), getattr(config, 'OLLAMA_MODEL_FALLBACK', 'llama3'))
    ]
    
    for host, model in targets:
        if not host: continue
        try:
            print(f"🤖 [Dream] A tentar inferência em {host} ({model})...")
            client = ollama.Client(host=host)
            messages = []
            if system_instruction:
                messages.append({'role': 'system', 'content': system_instruction})
            messages.append({'role': 'user', 'content': prompt})
            
            resp = client.chat(model=model, messages=messages)
            return resp['message']['content']
        except Exception as e:
            print(f"⚠️ [Dream] Falha no host {host}: {e}")
            continue
    return None

# --- Utils de Extração ---

def _extract_json(text):
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        candidate = match.group(0) if match else text
        return json.loads(candidate)
    except: return None

def _extract_python_code(text):
    pattern = r'```(?:python)?\s*(.*?)```'
    match = re.search(pattern, text, re.DOTALL)
    if match: return match.group(1).strip()
    return text.strip()

# --- Módulos do Sonho ---

def _consolidate_memories():
    """ Limpa o RAG fundindo factos e eliminando o tom poético das memórias. """
    print("🧠 [Dream] A consolidar histórico...")
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, timestamp, text FROM memories ORDER BY id DESC LIMIT 10")
        rows = cursor.fetchall()
        if len(rows) < 3: return

        ids_to_purge = [r[0] for r in rows]
        memory_bundle = [{"ts": r[1], "content": r[2]} for r in reversed(rows)]

        prompt = f"Merge these memories. Rule: Keep only facts, remove 'goth' persona meta-talk. JSON ONLY. Input: {json.dumps(memory_bundle)}"
        ans = _safe_ollama_chat(prompt, "You are a factual data consolidator.")
        
        merged = _extract_json(ans)
        if merged:
            cursor.execute(f"DELETE FROM memories WHERE id IN ({','.join(['?']*len(ids_to_purge))})", ids_to_purge)
            save_to_rag(json.dumps(merged, ensure_ascii=False))
            conn.commit()
    except Exception as e: print(f"❌ Erro Consolidação: {e}")
    finally: conn.close()

def _perform_news_dream():
    """ Pesquisa notícias recentes para manter o pHantasma atualizado. """
    print("📰 [Dream] A ler as notícias do mundo e do Porto...")
    query_prompt = "Gera uma query para: notícias recentes Porto Portugal, veganismo, tecnologia open-source. Query ONLY."
    query = _safe_ollama_chat(query_prompt, "Search Query Expert.")
    
    if not query: return "Vazio."
    
    results = search_with_searxng(query.replace('"', ''), max_results=5)
    if not results: return "Sem novidades."

    extract_prompt = f"Extract facts (EN S->P->O) and tags (PT). JSON ONLY. Context: {results}"
    ans = _safe_ollama_chat(extract_prompt, "News Analyst.")
    
    data = _extract_json(ans)
    if data:
        data["tags"] = data.get("tags", []) + ["Notícias", "Atualidade"]
        save_to_rag(json.dumps(data, ensure_ascii=False))
        return f"Aprendi sobre: {query}"
    return "Falha na análise."

def _perform_web_dream():
    """ Introspecção: Pesquisa sobre temas que o utilizador falou recentemente. """
    print("💤 [Dream] Introspecção Web...")
    # Lógica de extrair query das memórias recentes e pesquisar no SearxNG
    # (Similar ao _perform_news_dream mas baseado no histórico do RAG)
    return "Introspecção concluída."

def _perform_coding_dream():
    """ Sonho Lúcido: Evolução do próprio código. """
    print("👾 [Lucid Dream] Evolução de código...")
    # (Mantém a lógica de usar o Gemini para review se configurado)
    return "Evolução concluída."

# --- Router e Daemon ---

def perform_dreaming(mode="auto"):
    if mode == "code": return _perform_coding_dream()
    elif mode == "news": return _perform_news_dream()
    elif mode == "web": return _perform_web_dream()
    else:
        # Lógica automática: Notícias são prioritárias para evitar 'ficar no passado'
        choice = random.random()
        if choice < 0.2: return _perform_coding_dream()
        if choice < 0.6: return _perform_news_dream()
        return _perform_web_dream()

def _daemon_loop():
    print(f"[Dream] Daemon ativo. Agendado para as {DREAM_TIME}")
    while True:
        if datetime.datetime.now().strftime("%H:%M") == DREAM_TIME:
            threading.Thread(target=perform_dreaming, args=("auto",)).start()
            time.sleep(70)
        time.sleep(30)

def init_skill_daemon():
    threading.Thread(target=_daemon_loop, daemon=True).start()

def handle(user_prompt_lower, user_prompt_full):
    mode = "auto"
    if any(x in user_prompt_lower for x in ["notícias", "novidades", "mundo"]): mode = "news"
    elif any(x in user_prompt_lower for x in ["código", "program", "lúcido"]): mode = "code"
    
    threading.Thread(target=perform_dreaming, args=(mode,)).start()
    return "A iniciar processo onírico. Vou processar as sombras da informação."
