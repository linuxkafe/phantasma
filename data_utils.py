import sqlite3
import re
import unicodedata
import time
from datetime import datetime
import config

# Lista de palavras irrelevantes para a cache (Stop Words PT)
STOP_WORDS = {
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas",
    "por", "pelo", "pela", "pelos", "pelas", "para", "com",
    "que", "e", "é", "eh", "se", "como", "mas", "ou",
    "diz", "dime", "dizme", "sabes", "podes", "queria", "quero"
}

CACHE_TTL = 604800  # 1 Semana em segundos (7 * 24 * 60 * 60)

def setup_database():
    """ Cria as tabelas necessárias (Memórias RAG + Cache Ollama). """
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        # Tabela RAG (Existente)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            text TEXT NOT NULL
        );
        """)

        # Tabela Cache Inteligente (Nova)
        # normalized_key: A pergunta "limpa"
        # response: A resposta do Ollama
        # timestamp: Data em segundos (epoch) para verificar a semana
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS response_cache (
            normalized_key TEXT PRIMARY KEY,
            response TEXT NOT NULL,
            timestamp REAL NOT NULL
        );
        """)
        
        conn.commit()
        conn.close()
        print(f"Base de dados '{config.DB_PATH}' verificada (Memória + Cache).")
    except Exception as e:
        print(f"ERRO: Falha ao inicializar a base de dados SQLite: {e}")

# --- Funções RAG (Mantidas) ---

def save_to_rag(transcription_text):
    if not transcription_text: return 
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO memories (timestamp, text) VALUES (?, ?)", (datetime.now(), transcription_text))
        conn.commit(); conn.close()
        print(f"RAG: Memória guardada: '{transcription_text}'")
    except Exception as e: print(f"ERRO RAG Save: {e}")

def retrieve_from_rag(prompt, max_results=3):
    try:
        keywords = [word for word in prompt.lower().split() if len(word) > 3]
        if not keywords: return "" 
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        query_parts = []; params = []
        for word in keywords:
            query_parts.append("text LIKE ?")
            params.append(f"%{word}%")
        if not query_parts: return ""
        sql_query = f"SELECT text FROM memories WHERE {' OR '.join(query_parts)} ORDER BY timestamp DESC LIMIT {max_results}"
        cursor.execute(sql_query, params)
        results = cursor.fetchall()
        conn.close()
        if results:
            context_str = "CONTEXTO ANTIGO (Usa isto se relevante):\n"
            for row in results: context_str += f"- {row[0]}\n"
            return context_str
        return ""
    except Exception as e: print(f"ERRO RAG Retrieve: {e}"); return ""

# --- Novas Funções de Cache ---

def normalize_prompt(text):
    """ 
    Remove acentos, pontuação, minúsculas e palavras supérfluas. 
    Ex: "Quem é o primeiro ministro?" -> "quem primeiro ministro"
    """
    try:
        # 1. Unicode Normalize (remove acentos: é -> e)
        text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
        
        # 2. Lowercase e remover tudo o que não for letra/número
        text = re.sub(r'[^\w\s]', '', text.lower())
        
        # 3. Remover Stop Words
        words = text.split()
        filtered_words = [w for w in words if w not in STOP_WORDS]
        
        return " ".join(filtered_words).strip()
    except Exception:
        return text.lower().strip() # Fallback

def get_cached_response(raw_prompt):
    """ Verifica se existe resposta válida (menos de 1 semana) para o prompt normalizado. """
    key = normalize_prompt(raw_prompt)
    if not key: return None

    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT response, timestamp FROM response_cache WHERE normalized_key = ?", (key,))
        row = cursor.fetchone()
        conn.close()

        if row:
            response, timestamp = row
            # Verificar validade (1 semana)
            if time.time() - timestamp < CACHE_TTL:
                print(f"CACHE HIT: '{raw_prompt}' -> '{key}'")
                return response
            else:
                print(f"CACHE EXPIRED: '{key}' (Mais de 7 dias)")
                return None
        return None
    except Exception as e:
        print(f"ERRO Cache Get: {e}")
        return None

def save_cached_response(raw_prompt, response):
    """ Guarda a resposta na cache com o timestamp atual. """
    key = normalize_prompt(raw_prompt)
    if not key or not response: return

    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        # REPLACE INTO atualiza se a chave já existir, ou insere se não
        cursor.execute("REPLACE INTO response_cache (normalized_key, response, timestamp) VALUES (?, ?, ?)", 
                       (key, response, time.time()))
        conn.commit()
        conn.close()
        print(f"CACHE SAVED: '{key}'")
    except Exception as e:
        print(f"ERRO Cache Save: {e}")
