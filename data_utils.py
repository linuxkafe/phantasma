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
            
        # CORREÇÃO: Selecionamos também o timestamp
        sql_query = f"SELECT timestamp, text FROM memories WHERE {' OR '.join(query_parts)} ORDER BY timestamp DESC LIMIT {max_results}"
        
        cursor.execute(sql_query, params)
        results = cursor.fetchall()
        conn.close()

        if results:
            # CORREÇÃO: Cabeçalho explicito para o LLM
            context_str = "MEMÓRIAS PESSOAIS DO UTILIZADOR (Ordenadas da mais recente para a antiga):\n"
            context_str += "NOTA: Se houver contradições, a informação com a DATA MAIS RECENTE é a verdadeira.\n\n"
            
            for row in results:
                # row[0] é data, row[1] é texto
                ts = row[0]
                # Tenta formatar a data se for string, ou usa direta
                try:
                    if isinstance(ts, str):
                        # Corta os milissegundos para ficar limpo (YYYY-MM-DD HH:MM:SS)
                        ts = ts.split('.')[0]
                except: pass
                
                context_str += f"- [{ts}] {row[1]}\n"
                
            print(f"RAG: Contexto recuperado:\n{context_str}")
            return context_str
        else:
            print("RAG: Nenhum contexto relevante encontrado.")
            return ""

    except Exception as e:
        print(f"ERRO: Falha ao recuperar da BD RAG: {e}")
        return ""
