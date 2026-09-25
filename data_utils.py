import sqlite3
from datetime import datetime, timedelta

import config


# --- SETUP ---
def setup_database():
    """Cria as tabelas 'memories' e 'cache' na BD se não existirem."""
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()

        # Tabela de Memórias (RAG)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            text TEXT NOT NULL
        );
        """)

        # Tabela de Cache (Respostas Rápidas)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            prompt TEXT PRIMARY KEY,
            response TEXT NOT NULL,
            timestamp DATETIME NOT NULL
        );
        """)

        conn.commit()
        conn.close()
        print(f"Base de dados e Cache inicializadas em '{config.DB_PATH}'.")
    except Exception as e:
        print(f"ERRO: Falha ao inicializar a base de dados SQLite: {e}")


# --- RAG (MEMÓRIA DE LONGO PRAZO) ---
def save_to_rag(text):
    """Guarda texto simples ou JSON estruturado pelas skills no RAG.

    Ignora entradas vazias ou demasiado curtas para não indexar ruído.
    """
    if not text or not text.strip():
        return
    clean_text = text.strip()
    if len(clean_text) > 5:
        try:
            conn = sqlite3.connect(config.DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO memories (timestamp, text) VALUES (?, ?)",
                (datetime.now(), clean_text),
            )
            conn.commit()
            conn.close()
            print("RAG: Memória guardada.")
        except Exception as e:
            print(f"ERRO: Falha ao guardar memória RAG: {e}")


def save_fact_to_rag(text):
    """Alias de save_to_rag (paridade com os nomes históricos do remote)."""
    save_to_rag(text)


def retrieve_from_rag(prompt, max_results=5):
    """Recupera factos por relevância léxica, ordenados cronologicamente (DESC).

    Devolve apenas conteúdo bruto, sem cabeçalho descritivo, para não
    confundir o LLM externo. O assistant.py trata da sanitização final.
    """
    try:
        # Filtro de palavras curtas para evitar ruído
        keywords = [word for word in prompt.lower().split() if len(word) > 3]
        if not keywords:
            return ""

        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()

        query_parts = ["text LIKE ?"] * len(keywords)
        params = [f"%{word}%" for word in keywords]

        # Ordenação DESC garante que os factos mais recentes (Bimby)
        # aparecem antes dos antigos (Ophiuchus) no contexto do LLM.
        sql = (
            "SELECT text FROM memories WHERE "
            f"{' OR '.join(query_parts)} ORDER BY timestamp DESC LIMIT {max_results}"
        )

        cursor.execute(sql, params)
        results = cursor.fetchall()
        conn.close()

        if results:
            print("RAG: Contexto recuperado.")
            return "\n".join([row[0] for row in results])
        return ""

    except Exception as e:
        print(f"ERRO: Falha ao recuperar da BD RAG: {e}")
        return ""


# --- MANUTENÇÃO (PURGA DE RUÍDO) ---
def purge_poisoned_memories():
    """Remove entradas com alucinações poéticas recorrentes do RAG."""
    bad_patterns = ["Sombra", "Fúria da Memória", "Silêncio", "Eco das sombras"]
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        for pattern in bad_patterns:
            cursor.execute("DELETE FROM memories WHERE text LIKE ?", (f"%{pattern}%",))
        conn.commit()
        count = conn.total_changes
        conn.close()
        return f"Limpeza concluída: {count} memórias envenenadas removidas."
    except Exception as e:
        return f"Erro na limpeza: {e}"


# --- CACHE (RESPOSTAS RÁPIDAS) ---
def get_cached_response(prompt):
    """Tenta recuperar uma resposta exata da cache (válida por 24h)."""
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT response, timestamp FROM cache WHERE prompt = ?", (prompt,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            response, ts = row
            # Validade de 24 horas para evitar respostas obsoletas
            try:
                if datetime.now() - datetime.fromisoformat(ts) < timedelta(hours=24):
                    return response
            except ValueError:
                return response
        return None
    except Exception as e:
        print(f"AVISO: Erro ao ler cache: {e}")
        return None


def save_cached_response(prompt, response):
    """Guarda uma resposta na cache para uso futuro."""
    if not prompt or not response:
        return
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO cache (prompt, response, timestamp) "
            "VALUES (?, ?, ?)",
            (prompt, response, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"AVISO: Erro ao gravar cache: {e}")
