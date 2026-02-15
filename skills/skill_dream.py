# vim skill_dream.py

import threading
import time
import datetime
import random
import sqlite3
import json
import re
import os
import ast  # Essencial para lidar com aspas simples do LLM
import ollama
import config
from tools import search_with_searxng
from data_utils import save_to_rag

# --- Configuração ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["vai sonhar", "aprende algo", "desenvolve a persona", "sonho lúcido", "notícias", "novidades"]

DREAM_TIME = "02:30" 
LUCID_DREAM_CHANCE = 0.3

# --- Helper de Inferência com Failover ---

def _safe_ollama_chat(prompt, system_instruction=""):
    """ Tenta o host primário e depois o fallback, tal como no assistant.py """
    targets = [
        (getattr(config, 'OLLAMA_HOST_PRIMARY', None), getattr(config, 'OLLAMA_MODEL_PRIMARY', 'llama3')),
        (getattr(config, 'OLLAMA_HOST_FALLBACK', 'http://localhost:11434'), getattr(config, 'OLLAMA_MODEL_FALLBACK', 'llama3'))
    ]
    
    for host, model in targets:
        if not host: continue
        try:
            client = ollama.Client(host=host, timeout=config.OLLAMA_TIMEOUT)
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

# --- Utils de Extração Robusta ---

def _extract_json(text):
    """ Extração com tripla camada de segurança: Regex -> JSON Strict -> AST Fallback """
    if not text: return None
    try:
        # 1. Isolar o bloco entre chavetas
        match = re.search(r'(\{.*\})', text, re.DOTALL)
        json_str = match.group(1) if match else text
        
        # 2. Sanitizar quebras de linha literais que corrompem o JSON
        json_str = json_str.replace('\n', '\\n').replace('\r', '\\r')

        try:
            # Tentativa JSON padrão
            return json.loads(json_str, strict=False)
        except:
            # Fallback para aspas simples ou lixo de formatação
            return ast.literal_eval(json_str)
    except Exception as e:
        print(f"⚠️ [Dream] Erro fatal no parse de conhecimento: {e}")
        return None

# --- Módulos do Sonho ---

def _consolidate_memories():
    """ Funde memórias recentes, mantendo factos e purgando a persona repetitiva. """
    print("🧠 [Dream] A consolidar histórico...")
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, timestamp, text FROM memories ORDER BY id DESC LIMIT 15")
        rows = cursor.fetchall()
        if len(rows) < 5: return

        ids_to_purge = [r[0] for r in rows]
        memory_bundle = [{"ts": r[1], "content": r[2]} for r in reversed(rows)]

        prompt = (
            f"Merge these memories into a single state. Rules: \n"
            f"1. Resolve contradictions (latest is true). \n"
            f"2. REMOVE goth/persona filler phrases. \n"
            f"3. Return ONLY a Mermaid graph in JSON.\n\n"
            f"Input: {json.dumps(memory_bundle)}"
        )
        
        ans = _safe_ollama_chat(prompt, "You are a Factual Knowledge Consolidator.")
        merged = _extract_json(ans)
        
        if merged and isinstance(merged, dict):
            cursor.execute(f"DELETE FROM memories WHERE id IN ({','.join(['?']*len(ids_to_purge))})", ids_to_purge)
            save_to_rag(json.dumps(merged, ensure_ascii=False))
            conn.commit()
            print("🧠 [Dream] Consolidação terminada.")
    except Exception as e: print(f"❌ Erro Consolidação: {e}")
    finally: conn.close()

def _perform_news_dream():
    """ Mantém o pHantasma atualizado com notícias locais e de nicho. """
    print("📰 [Dream] A sintonizar frequências de notícias...")
    query_prompt = "Generate a search query for: Porto Portugal local news, latest vegan ethics, open-source AI news. Query ONLY."
    query = _safe_ollama_chat(query_prompt, "Search Query Expert.")
    
    if not query: return
    
    results = search_with_searxng(query.replace('"', ''), max_results=5)
    if not results: return

    extract_prompt = f"Convert to SPO facts (English) and Portuguese tags. JSON ONLY. Context: {results}"
    ans = _safe_ollama_chat(extract_prompt, "News Analyst.")
    
    data = _extract_json(ans)
    if data:
        data["tags"] = data.get("tags", []) + ["Notícias", "Atualidade"]
        save_to_rag(json.dumps(data, ensure_ascii=False))
        return f"Notícias processadas."

def _perform_web_dream():
    """ Introspecção: Escolhe um tema falado recentemente e aprofunda conhecimento. """
    print("💤 [Dream] Introspecção Web...")
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT text FROM memories ORDER BY RANDOM() LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        
        if not row: return
        
        deep_prompt = f"Based on this memory: '{row[0]}', generate a search query to learn more details about the core topic. Query ONLY."
        query = _safe_ollama_chat(deep_prompt, "Research Assistant.")
        
        if query:
            results = search_with_searxng(query.replace('"', ''), max_results=3)
            internal_prompt = f"Extract 3 NEW facts from this research. JSON format with tags. Context: {results}"
            ans = _safe_ollama_chat(internal_prompt, "Knowledge Architect.")
            data = _extract_json(ans)
            if data:
                save_to_rag(json.dumps(data, ensure_ascii=False))
                print(f"💤 [Dream] Aprofundei sobre: {query}")
    except: pass

# --- Daemon & Logic ---

def perform_dreaming(mode="auto"):
    # Consolidação corre sempre no início para limpar a base
    _consolidate_memories()
    
    if mode == "news": _perform_news_dream()
    elif mode == "web": _perform_web_dream()
    else:
        choice = random.random()
        if choice < 0.5: _perform_news_dream() # Notícias são prioritárias
        else: _perform_web_dream()

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
    elif any(x in user_prompt_lower for x in ["aprende", "pesquisa", "estuda"]): mode = "web"
    
    threading.Thread(target=perform_dreaming, args=(mode,)).start()
    return "A iniciar processo onírico. Vou digerir as sombras da informação."
