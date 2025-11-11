import sqlite3
from datetime import datetime
import config

def setup_database():
    """ Cria a tabela 'memories' na BD se não existir. """
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            text TEXT NOT NULL
        );
        """)
        conn.commit()
        conn.close()
        print(f"Base de dados RAG '{config.DB_PATH}' inicializada.")
    except Exception as e:
        print(f"ERRO: Falha ao inicializar a base de dados SQLite: {e}")

def save_to_rag(transcription_text):
    """ Guarda a transcrição do utilizador na BD RAG. """
    if not transcription_text:
        return 
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO memories (timestamp, text) VALUES (?, ?)",
            (datetime.now(), transcription_text)
        )
        conn.commit()
        conn.close()
        print(f"RAG: Memória guardada: '{transcription_text}'")
    except Exception as e:
        print(f"ERRO: Falha ao guardar a transcrição na BD RAG: {e}")

def retrieve_from_rag(prompt, max_results=3):
    """
    Recupera memórias relevantes da BD RAG (método LIKE simples).
    """
    try:
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
            
        sql_query = f"SELECT text FROM memories WHERE {' OR '.join(query_parts)} ORDER BY timestamp DESC LIMIT {max_results}"
        
        cursor.execute(sql_query, params)
        results = cursor.fetchall()
        conn.close()

        if results:
            context_str = "CONTEXTO ANTIGO (Usa isto para responder se for relevante):\n"
            for row in results:
                context_str += f"- {row[0]}\n"
            print(f"RAG: Contexto encontrado:\n{context_str}")
            return context_str
        else:
            print("RAG: Nenhum contexto relevante encontrado.")
            return ""

    except Exception as e:
        print(f"ERRO: Falha ao recuperar da BD RAG: {e}")
        return ""
