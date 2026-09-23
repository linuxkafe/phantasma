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
    """
    Guarda o texto na base de dados RAG (memória de longo prazo) com a
    limpeza minima para nao indexar respostas vazias ou a persona.
    """
    if not text or not text.strip():
        return
    clean_text = text.strip()
    if len(clean_text) > 5:
        try:
            datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
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
    """
    Recupera memórias relevantes com TIMESTAMPS para dar contexto temporal.
    """
    try:
        # Filtro de palavras curtas para evitar ruído
        keywords = [word for word in prompt.lower().split() if len(word) > 3]
        if not keywords:
            return ""

        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()

        query_parts = []
        params = []
        for word in keywords:
            query_parts.append("text LIKE ?")
            params.append(f"%{word}%")

        sql_query = (
            f"SELECT timestamp, text FROM memories "
            f"WHERE {' OR '.join(query_parts)} "
            f"ORDER BY timestamp DESC LIMIT {max_results}"
        )

        cursor.execute(sql_query, params)
        results = cursor.fetchall()
        conn.close()

        if results:
            context_str = "MEMÓRIAS PESSOAIS DO UTILIZADOR (Ordenadas da mais recente para a antiga):\n"
            context_str += "NOTA: Se houver contradições, a informação com a DATA MAIS RECENTE é a verdadeira.\n\n"

            for row in results:
                ts = row[0]
                try:
                    if isinstance(ts, str):
                        ts = ts.split(".")[0]  # Limpa milissegundos
                except Exception:
                    pass

                context_str += f"- [{ts}] {row[1]}\n"

            print("RAG: Contexto recuperado.")
            return context_str
        else:
            return ""

    except Exception as e:
        print(f"ERRO: Falha ao recuperar da BD RAG: {e}")
        return ""


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
            response, timestamp_str = row
            try:
                cached_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
                if datetime.now() - cached_time < timedelta(hours=24):
                    print("CACHE: Resposta recuperada da base de dados.")
                    return response
            except Exception:
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
            "INSERT OR REPLACE INTO cache (prompt, response, timestamp) VALUES (?, ?, ?)",
            (prompt, response, datetime.now()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"AVISO: Erro ao gravar cache: {e}")
