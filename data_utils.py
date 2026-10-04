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
#
# 2026-10-04. Esta cache era SÓ LEITURA: `save_cached_response` não era chamada
# em lado nenhum do código, em dev nem em prod, e a tabela `cache` tinha 0
# linhas. O docstring do `assistant.py` prometia "Cache -> RAG + SearXNG ->
# Ollama" e a leitura existia; a escrita é que não. Resultado: o dono perguntou
# "qual o teu limite para a estupidez humana?" uma vez, voltou a perguntar pelo
# Discord, e a casa gastou 42-65 s a gerar outra vez uma resposta que já tinha.
# Um cache que nunca enche nunca acerta, e o sintoma -- "devia ter pegado na
# cache" -- parece um problema de acerto quando é de escrita.
#
# A chave é o texto normalizado, não o texto cru. A mesma pergunta chega por
# voz ("qual o teu limite...?") e por Discord com o mesmo texto, mas divergia
# em whitespace e pontuação, e o `WHERE prompt = ?` exacto nunca as juntava.

CACHE_KIND_CONVERSATION = "conversation"
CACHE_KIND_LIVE = "live"

# Uma resposta de conversa pode ser servida durante um dia. Uma leitura de
# sensor não: o UV de Lisboa correu 0.1 -> 4.35 -> 0.0 no mesmo dia de
# 2026-10-04, e é precisamente uma leitura dessas que a cache devolvia como
# se fosse o presente. 15 min e o máximo que uma leitura viva aguenta antes de
# ser mentira.
CACHE_TTL_HOURS = {CACHE_KIND_CONVERSATION: 24.0, CACHE_KIND_LIVE: 0.25}

_CACHE_READ_WARNED = False


def _cache_normalise(prompt):
    """Fold the differences that are not differences in meaning.

    Case, surrounding whitespace, and terminal punctuation. Anything more
    aggressive would merge two genuinely different questions, and a wrong
    cache hit is worse than a slow answer: the house would confidently repeat
    an answer to a question nobody asked.
    """
    return " ".join((prompt or "").strip().rstrip(".!?").split()).lower()


def _ensure_cache_schema(cursor):
    """Idempotent migration for the `kind` column.

    The cache table predates the split between conversation and live readings.
    ALTER TABLE has no IF NOT EXISTS in SQLite, so the PRAGMA check is what
    makes this safe to run on every start -- and it has to be safe on every
    start, because a half-applied migration would take the whole cache down
    with it.
    """
    cursor.execute(
        "SELECT name FROM pragma_table_info('cache') WHERE name = 'kind'"
    )
    if cursor.fetchone() is None:
        cursor.execute(
            "ALTER TABLE cache ADD COLUMN kind TEXT NOT NULL DEFAULT 'conversation'"
        )
        # Explicit. This runs on the READ path, and a caller that opens a
        # transaction and closes the connection without committing leaves the
        # migration to the rollback -- which would make the column missing on
        # every run while looking like it had worked.
        cursor.connection.commit()


def get_cached_response(prompt, kinds=None):
    """Recover an exact prior answer, if it is still inside its own TTL.

    Args:
        prompt: user text, in any capitalisation or punctuation. The key is
            the normalised form, so the same question by voice and by Discord
            hits the same row.
        kinds: only accept these kinds. This is a SAFETY valve, not a filter:
            the caller passes `("live",)` to replay a reading while refusing to
            replay anything else, because a device command cached under the same
            key would otherwise be answered without being performed.

    Returns:
        The cached response, or None. None means "not usable" -- either never
        stored or past its kind's TTL. An expired row is left in place and just
        not returned, so the next occurrence overwrites it instead of the table
        growing without bound.
    """
    key = _cache_normalise(prompt)
    if not key:
        return None
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        _ensure_cache_schema(cursor)
        if kinds:
            marks = ",".join("?" * len(kinds))
            cursor.execute(
                f"SELECT response, timestamp, kind FROM cache WHERE prompt = ? "
                f"AND kind IN ({marks})",
                (key, *kinds),
            )
        else:
            cursor.execute(
                "SELECT response, timestamp, kind FROM cache WHERE prompt = ?",
                (key,),
            )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None
        response, ts, kind = row
        ttl = CACHE_TTL_HOURS.get(kind, CACHE_TTL_HOURS[CACHE_KIND_CONVERSATION])
        try:
            if datetime.now() - datetime.fromisoformat(ts) < timedelta(hours=ttl):
                return response
        except ValueError:
            # An unparseable timestamp used to return the answer forever, which
            # is the one outcome a cache must never produce: if we cannot tell
            # how old it is, we cannot claim it is fresh.
            return None
        return None
    except Exception as e:
        # Reported once per process, and loudly. The failure mode here is not a
        # wrong answer, it is NO answers ever cached -- which looks exactly like
        # "the cache has a bad key" and sends you looking in the wrong place.
        # That is what happened for as long as this function only read from a
        # table nothing wrote to.
        global _CACHE_READ_WARNED
        if not _CACHE_READ_WARNED:
            _CACHE_READ_WARNED = True
            print(f"AVISO: cache de respostas inoperacional: {e}")
        return None


def save_cached_response(prompt, response, kind=CACHE_KIND_CONVERSATION):
    """Keep an answer for the next identical question.

    Both branches are cached, as of 2026-10-04: answers that went through the
    model AND answers a skill produced on its own. The direct-skill branch had
    no cache at all, which is why asking the same thing twice by voice and then
    by Discord paid full price twice.

    `kind` decides the TTL, not whether to store. `live` is for anything
    derived from a sensor, a forecast or a thermostat.
    """
    key = _cache_normalise(prompt)
    if not key or not response:
        return
    if kind not in CACHE_TTL_HOURS:
        # An unknown kind would silently inherit the 24 h conversation TTL, and
        # a reading of tomorrow's weather is exactly what must not get that.
        kind = CACHE_KIND_LIVE
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        _ensure_cache_schema(cursor)
        cursor.execute(
            "INSERT OR REPLACE INTO cache (prompt, response, timestamp, kind) "
            "VALUES (?, ?, ?, ?)",
            (key, response, datetime.now().isoformat(), kind),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"AVISO: Erro ao gravar cache: {e}")
