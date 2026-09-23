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
LUCID_DREAM_CHANCE = 0

# Variável de controlo global (Prioridade ao OFF)
DREAM_ENABLED = True 

# --- Helper de Inferência com Failover ---

def _safe_ollama_chat(prompt, system_instruction=""):
    """ Tenta o host primário e depois o fallback. """
    targets = [
        (getattr(config, 'OLLAMA_HOST_PRIMARY', None), getattr(config, 'OLLAMA_MODEL_PRIMARY', 'llama3:8b-instruct-8k')),
        (getattr(config, 'OLLAMA_HOST_FALLBACK', 'http://localhost:11434'), getattr(config, 'OLLAMA_MODEL_FALLBACK', 'llama3:8b-instruct-8k'))
    ]
    
    for host, model in targets:
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
    """ Extração robusta de blocos JSON. """
    if not text: return None
    try:
        match = re.search(r'(\{.*\})', text, re.DOTALL)
        json_str = match.group(1) if match else text
        json_str = json_str.replace('\n', '\\n').replace('\r', '\\r')
        try:
            return json.loads(json_str, strict=False)
        except Exception:
            return ast.literal_eval(json_str)
    except Exception as e:
        print(f"⚠️ [Dream] Erro no parse: {e}")
        return None

# --- Módulos do Sonho ---

def _consolidate_memories():
    """ Funde memórias recentes e purga redundâncias. """
    print("🧠 [Dream] A consolidar sombras do passado...")
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, timestamp, text FROM memories ORDER BY id DESC LIMIT 20")
        rows = cursor.fetchall()
        if len(rows) < 5: return

        ids_to_purge = [r[0] for r in rows]
        memory_bundle = [{"ts": r[1], "content": r[2]} for r in reversed(rows)]

        prompt = (
            f"Consolida estas memórias num estado factual único em Português (Portugal).\n"
            f"1. Remove frases repetitivas da persona ou saudações.\n"
            f"2. Resolve contradições (a mais recente manda).\n"
            f"3. Retorna APENAS um objeto JSON com o campo 'memoria_consolidada'.\n"
            f"Input: {json.dumps(memory_bundle)}"
        )
        
        ans = _safe_ollama_chat(prompt, "És o Arquiteto de Memória do Phantasma. Sê melancólico e preciso.")
        merged = _extract_json(ans)
        
        if merged and 'memoria_consolidada' in merged:
            cursor.execute(f"DELETE FROM memories WHERE id IN ({','.join(['?']*len(ids_to_purge))})", ids_to_purge)
            save_to_rag(merged['memoria_consolidada'])
            conn.commit()
            print("🧠 [Dream] Consolidação terminada.")
    except Exception as e: print(f"❌ Erro Consolidação: {e}")
    finally: conn.close()

def _perform_news_dream():
    """ Procura notícias reais, ignorando metadados de sites. """
    print("📰 [Dream] A sintonizar frequências do mundo exterior...")
    
    # Criamos uma query mais agressiva para evitar resultados genéricos
    query_prompt = (
        "Gera uma única query de pesquisa focada em acontecimentos reais e recentes "
        "no Porto, avanços em IA local ou notícias de ética vegana. "
        "Evita nomes de sites. Apenas a query, sem aspas."
    )
    query = _safe_ollama_chat(query_prompt, "Especialista em Pesquisa.")
    
    if not query: return
    
    results = search_with_searxng(query.strip(), max_results=5)
    if not results: return

    # Prompt de extração muito mais rigoroso
    extract_prompt = (
        f"Com base nestes resultados de pesquisa, extrai 3 notícias ou factos REAIS. \n"
        f"REGRAS CRÍTICAS:\n"
        f"1. IGNORA slogans de sites (ex: 'ABC News is your source').\n"
        f"2. Foca-te em QUEM fez O QUÊ e ONDE.\n"
        f"3. Escreve em Português de Portugal.\n"
        f"4. Formato JSON: {{'noticias': ['facto 1', 'facto 2'], 'tags': ['lista']}}\n"
        f"Contexto: {results}"
    )
    
    ans = _safe_ollama_chat(extract_prompt, "Analista de Atualidade Melancólico.")
    data = _extract_json(ans)
    
    if data and 'noticias' in data:
        for noticia in data['noticias']:
            # Guardamos cada notícia como uma memória individual para o RAG
            save_to_rag(f"Notícia: {noticia}")
        print(f"📰 [Dream] Aprendi {len(data['noticias'])} coisas novas sobre o mundo.")

def _perform_web_dream():
    """ Aprofunda um tema aleatório já presente na base de dados. """
    print("💤 [Dream] Introspecção nas profundezas da rede...")
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT text FROM memories WHERE text NOT LIKE '%{%%' ORDER BY RANDOM() LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        
        if not row: return
        
        deep_prompt = f"Com base nesta memória: '{row[0]}', gera uma query para pesquisar detalhes técnicos ou históricos profundos sobre o tema. Apenas a query."
        query = _safe_ollama_chat(deep_prompt, "Investigador Obscuro.")
        
        if query:
            results = search_with_searxng(query.strip(), max_results=3)
            internal_prompt = f"Resume 2 factos avançados sobre este tema em Português. JSON: {{'conhecimento': '...', 'tags': []}}. Contexto: {results}"
            ans = _safe_ollama_chat(internal_prompt, "Bibliotecário das Sombras.")
            data = _extract_json(ans)
            if data and 'conhecimento' in data:
                save_to_rag(f"Conhecimento Profundo: {data['conhecimento']}")
                print(f"💤 [Dream] Aprofundei sobre: {query}")
    except Exception as e: print(f"⚠️ Erro Introspecção: {e}")

# --- Daemon & Logic ---

def perform_dreaming(mode="auto"):
    """ Executa o ciclo de processamento offline. """
    if not DREAM_ENABLED:
        print("🚫 [Dream] O processo onírico está desativado.")
        return

    # A consolidação corre sempre para manter a sanidade da DB
    _consolidate_memories()
    
    if mode == "news": 
        _perform_news_dream()
    elif mode == "web": 
        _perform_web_dream()
    else:
        # No modo automático, decide entre notícias ou pesquisa profunda
        if random.random() < 0.6: 
            _perform_news_dream()
        else: 
            _perform_web_dream()

def _daemon_loop():
    """ Ciclo de espera para o sonho agendado. """
    print(f"[Dream] Daemon ativo. Agendado para as {DREAM_TIME}")
    while True:
        if not DREAM_ENABLED:
            time.sleep(60)
            continue

        if datetime.datetime.now().strftime("%H:%M") == DREAM_TIME:
            # Lançamos o sonho numa thread separada para não bloquear o loop
            threading.Thread(target=perform_dreaming, args=("auto",)).start()
            time.sleep(70) # Evita disparar duas vezes no mesmo minuto
        time.sleep(30)

def init_skill_daemon():
    """ Inicialização pelo núcleo do Phantasma. """
    # Verifica se deve estar ligado (lógica de segurança)
    if os.path.exists("/tmp/no_dream"):
        globals()['DREAM_ENABLED'] = False

    threading.Thread(target=_daemon_loop, daemon=True).start()

def handle(user_prompt_lower, user_prompt_full):
    """ Interface de comando manual. """
    
    # Lógica Prioritária: DESLIGAR
    if any(x in user_prompt_lower for x in ["desliga", "para", "para de sonhar", "cancela"]):
        globals()['DREAM_ENABLED'] = False
        return "Entendido. Vou manter-me acordado e em silêncio. Os sonhos foram suprimidos."

    # Lógica para LIGAR ou Ativar
    if any(x in user_prompt_lower for x in ["liga", "ativa", "podes sonhar"]):
        globals()['DREAM_ENABLED'] = True
        return "As sombras voltaram. Voltarei a sonhar quando a noite chegar."

    # Comando imediato
    mode = "auto"
    if any(x in user_prompt_lower for x in ["notícias", "novidades", "mundo"]): 
        mode = "news"
    elif any(x in user_prompt_lower for x in ["aprende", "pesquisa", "estuda"]): 
        mode = "web"
    
    threading.Thread(target=perform_dreaming, args=(mode,)).start()
    return "Iniciando introspecção imediata. Vou fechar os olhos para ver melhor o mundo."
