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
# Above this, what is stored is a transcript, not a fact. The largest
# memories in the store were 2170 chars of tagged JSON; retrieval OR-ed their
# keywords together, so a short question matched half the store and the model
# answered with whatever ranked first.
MEMORY_DISTIL_THRESHOLD = 400


def _distil_for_memory(text: str) -> str:
    """Reduce a long capture to the part worth remembering.

    Distilled with the local LLM, because the whole problem is that the
    assistant itself produced the rambling. If the model is unavailable the
    original is kept: losing a memory is worse than storing a long one, and
    the threshold above means the short common case is untouched.
    """
    if len(text) <= MEMORY_DISTIL_THRESHOLD:
        return text
    try:
        import ollama

        from config import config as cfg

        client = ollama.Client(host=cfg.llm.host)
        resp = client.chat(
            model=cfg.llm.model,
            messages=[{
                "role": "user",
                "content": (
                    "Reduz este texto a no maximo 3 frases com os factos "
                    "essenciais que devem ser lembrados. Sem introducoes, "
                    "sem repeticoes, sem commentarios, sem markdown. Se nao "
                    "contiver factos a guardar, responde apenas: VAZIO\n\n"
                    f"{text[:6000]}"
                ),
            }],
            options={"temperature": 0.1, "num_ctx": 8192},
        )
        out = (resp.get("message", {}).get("content") or "").strip()
        if not out or out.upper().startswith("VAZIO"):
            return ""
        if len(out) >= len(text):
            # The model padded it back to the original size: no gain, so keep
            # the source of truth rather than a lossy copy.
            return text
        return out
    except Exception as e:
        print(f"RAG: distilacao falhou, guarda o original ({e})")
        return text



def save_to_rag(text):
    """Guarda texto simples ou JSON estruturado pelas skills no RAG.

    Ignora entradas vazias ou demasiado curtas para não indexar ruído.
    """
    if not text or not text.strip():
        return
    clean_text = text.strip()
    if len(clean_text) > 5:
        clean_text = _distil_for_memory(clean_text)
        if not clean_text:
            return
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
        # The question's punctuation must not end up inside the SQL pattern.
        # "quem e o bimby?" yields the token "bimby?", and in SQL LIKE only
        # "%" and "_" are wildcards -- "?" is a literal, so the query became
        # LIKE '%bimby?%', which matches nothing. Measured: '%bimby?%' -> 0
        # memories, '%bimby%' -> 4. The knowledge was in the store and the
        # search could not see it, so the assistant answered it had none.
        # Normalise, keep words of 4+ characters, and escape LIKE wildcards.
        cleaned = "".join(
            c if c.isalnum() or c.isspace() else " " for c in prompt.lower()
        )
        # Question words are not subjects. Keywords are OR-ed, so "quem" in
        # "quem e o bimby?" matched every memory that happened to contain it and
        # the real Bimby rows lost the LIMIT 5 race -- the answer degraded to
        # "uma referencia a gatos que tem nomes proprios". Filter them, and
        # ignore a token that is only a question word.
        stop = {
            "quem", "qual", "quais", "quando", "onde", "porque", "porque",
            "como", "para", "sobre", "fala", "diz", "diz-me", "conta",
            "faz", "esta", "estao", "isso", "isto", "aquilo", "aqui",
            "obrigado", "obrigada", "ola", "bom", "boa", "dia", "noite",
            # NOT stopping subjects: "gato" must stay searchable, or
            # "como e o gato?" would retrieve nothing.
        }
        keywords = [w for w in cleaned.split() if len(w) > 3 and w not in stop]
        if not keywords:
            return ""
        keywords = [w.replace("%", "").replace("_", "") for w in keywords]
        keywords = [w for w in keywords if w]
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
