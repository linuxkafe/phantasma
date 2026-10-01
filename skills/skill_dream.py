# vim skill_dream.py

import ast  # Essencial para lidar com aspas simples do LLM
import datetime
import json
import os
import random
import re
import sqlite3
import threading
import time

import ollama

import config
from data_utils import save_to_rag
from tools import search_with_searxng

# --- Configuração ---
TRIGGER_TYPE = "contains"
TRIGGERS = ["vai sonhar", "aprende algo", "desenvolve a persona", "sonho lúcido",
            "notícias", "novidades", "otimiza grafo", "gmif agora"]

DREAM_TIME = "02:30"
LUCID_DREAM_CHANCE = 0

# Variável de controlo global (Prioridade ao OFF)
DREAM_ENABLED = True

# --- Optimização do grafo (GMIF) ---
# Before this lived in skills/skill_gmif_dream.py, which the loader never
# loaded: a legacy skill needs TRIGGERS plus handle(), and that module declared
# handle() but no TRIGGERS, so `init_skill_daemon` was never reached and the
# 03:00 cycle never ran. It is here now, in the skill the loader already loads.
GMIF_DREAM_ENABLED = True
MAX_RESEARCH_PER_CYCLE = 3

# The level a gap may be promoted to, and the type that promotion may claim.
# 'external' because the supporting text comes from a web search, not from
# logic: recording it as 'logical' would assert the relation was derived, when
# it was merely quoted from a page that might be wrong.
GMIF_DESIRED_LEVEL = {
    'weak_edge': "M3",
    'disconnected_pair': "M2",
    'missing_requirement': "M3",
    'causal_gap': "M4",
}
GMIF_VALIDATION_TYPE = "external"

# --- Helper de Inferência com Failover ---

def _safe_ollama_chat(prompt, system_instruction=""):
    """ Tenta o host primário e depois o fallback. """
    targets = [
        (getattr(config, 'OLLAMA_HOST_PRIMARY', None), getattr(config, 'OLLAMA_MODEL_PRIMARY', 'llama3:8b-instruct-8k')),
        (getattr(config, 'OLLAMA_HOST_FALLBACK', 'http://localhost:11434'), getattr(config, 'OLLAMA_MODEL_FALLBACK', 'llama3:8b-instruct-8k'))
    ]

    for host, model in targets:
        try:
            # A per-call timeout, not the global OLLAMA_TIMEOUT.
            #
            # The global value is 600s and it governs ordinary conversation,
            # where a long answer is desirable. The dream cycle chains several
            # of these calls: measured 61.7s for a two-word "OK" against the
            # primary host, because it is a cold local model generating without
            # a warm cache. At the global timeout one step can hold the cycle for
            # ten minutes, and the cycle is what the /admin/brain button waits
            # on -- which is why "Sleep & Dream" appeared to do nothing.
            #
            # Falling back to the next host on timeout is the desired behaviour,
            # not an error: a step that cannot finish should not stall the rest.
            client = ollama.Client(
                host=host,
                timeout=float(getattr(config, "DREAM_OLLAMA_TIMEOUT", 90)),
            )
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

def _as_memory(text, tags=None, facts=None):
    """Store one fact with the keyword structure the rest of the RAG expects.

    The dream already asks the model for tags -- the news prompt literally
    requests {"noticias": [...], "tags": [...]} -- and then threw them away,
    saving f"Noticia: {fact}". Retrieval matches on keywords with LIKE, so a
    memory without them is one the RAG cannot find. The worst offenders were
    the LLM's own "not enough information" replies, stored as knowledge.

    Anything without tags or facts is stored as plain text: inventing tags for
    a sentence the model refused to tag would be worse than not tagging it.
    """
    text = (text or "").strip()
    if not text:
        return None
    if not tags and not facts:
        if len(text) < 12 or "não contém informações" in text.lower():
            return None
        return text
    return json.dumps(
        {"tags": [str(t) for t in (tags or [])][:8],
         "facts": [str(f) for f in (facts or [])][:8] or [text[:300]]},
        ensure_ascii=False,
    )


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

def _archive_purged_memories(conn, rows):
    """Keep what consolidation deletes, so a bad merge is recoverable.

    The consolidation below is a real DELETE of the 20 most recent rows and then
    a single INSERT of the model's summary. If the model misreads them -- it is
    a local 8B model running unattended at 02:30 -- the original text is gone and
    nothing on the host records it. That is the one irreversible step in the
    dream, so the rows are archived first, in the same transaction as the delete:
    if the archive fails the delete does not happen either.

    The archive is append-only and holds the raw rows, so restoring is an
    INSERT back into `memories`.
    """
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS memories_purged ("
            " id INTEGER PRIMARY KEY,"
            " timestamp DATETIME,"
            " text TEXT,"
            " purged_at TEXT NOT NULL,"
            " reason TEXT NOT NULL)"
        )
        purged_at = datetime.datetime.now().isoformat()
        conn.executemany(
            "INSERT OR IGNORE INTO memories_purged (id, timestamp, text, purged_at, reason)"
            " VALUES (?,?,?,?,?)",
            [(r[0], r[1], r[2], purged_at, "dream_consolidation") for r in rows],
        )
        return len(rows)
    except Exception as e:
        print(f"❌ [Dream] Arquivo falhou, consolidação abortada: {e}")
        raise


def _consolidate_memories():
    """ Funde memórias recentes e purga redundâncias. """
    print("🧠 [Dream] A consolidar sombras do passado...")
    conn = None
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

        ans = _safe_ollama_chat(
            prompt, "És o Arquiteto de Memória do Phantasma. Sê melancólico e preciso."
        )
        merged = _extract_json(ans)

        if merged and 'memoria_consolidada' in merged:
            archived = _archive_purged_memories(conn, rows)
            marks = ",".join(["?"] * len(ids_to_purge))
            cursor.execute(f"DELETE FROM memories WHERE id IN ({marks})", ids_to_purge)
            mem = _as_memory(merged['memoria_consolidada'],
                             tags=merged.get('tags'))
            if mem:
                save_to_rag(mem)
            conn.commit()
            print(f"🧠 [Dream] Consolidação terminada: {archived} memórias arquivadas em memories_purged.")
    except Exception as e: print(f"❌ Erro Consolidação: {e}")
    finally:
        if conn: conn.close()

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
        tags = data.get('tags') or []
        for noticia in data['noticias']:
            # One memory per fact, keeping the tags the model returned.
            mem = _as_memory(noticia, tags=tags)
            if mem:
                save_to_rag(mem)
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
                mem = _as_memory(data['conhecimento'],
                                 tags=data.get('tags') or [query.strip()])
                if mem:
                    save_to_rag(mem)
                print(f"💤 [Dream] Aprofundei sobre: {query}")
    except Exception as e: print(f"⚠️ Erro Introspecção: {e}")

# --- Redução de memória a conceitos GMIF ---

def _analyze_graph_gaps():
    """Find the weak and missing links in the GMIF-classified graph.

    The four shapes are what the cycle can actually act on: an edge asserted at
    a low level, a pair of nodes that share vocabulary but have no edge, a node
    whose dependencies are unstated, and an edge that claims consequence with no
    mechanism behind it.
    """
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    nodes = cur.execute(
        "SELECT id, node_key, label, node_gmif_type, node_gmif_confidence "
        "FROM memory_graph WHERE node_type = 'node'"
    ).fetchall()
    edges = cur.execute(
        "SELECT id, source, target, label, gmif_level, gmif_validation_type, "
        "gmif_extraction_confidence, gmif_validation_confidence "
        "FROM memory_graph WHERE node_type = 'edge'"
    ).fetchall()

    gaps = {
        'weak_edges': [],
        'disconnected_pairs': [],
        'missing_requirements': [],
        'causal_gaps': [],
        'node_count': len(nodes),
        'edge_count': len(edges),
    }

    for edge in edges:
        extraction = edge['gmif_extraction_confidence'] or 0
        validation = edge['gmif_validation_confidence'] or 0
        if edge['gmif_level'] in ('M1', 'M2'):
            if extraction > 0.6 or validation > 0.5:
                gaps['weak_edges'].append({
                    'id': edge['id'],
                    'source': edge['source'],
                    'target': edge['target'],
                    'label': edge['label'],
                    'current_level': edge['gmif_level'],
                    'extraction_conf': extraction,
                    'validation_conf': validation,
                })
        # An edge that asserts a consequence while carrying no confidence at all
        # is the dangerous case: it reads as knowledge and is not.
        if edge['gmif_level'] in ('M3', 'M4') and not (validation > 0.3):
            gaps['causal_gaps'].append({
                'id': edge['id'],
                'source': edge['source'],
                'target': edge['target'],
                'label': edge['label'],
                'current_level': edge['gmif_level'],
            })

    labels = [n['label'] for n in nodes]
    seen = set()
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            key = tuple(sorted((str(a), str(b))))
            if key in seen:
                continue
            seen.add(key)
            has_edge = any(
                (e['source'] == a and e['target'] == b) or (e['source'] == b and e['target'] == a)
                for e in edges
            )
            if has_edge:
                continue
            shared = _shared_words(a, b)
            # One shared word is not evidence of a missing link: it is how most
            # pairs of Portuguese sentences relate. Two is the first count that
            # suggests a shared subject rather than a shared word.
            if shared < 2:
                continue
            gaps['disconnected_pairs'].append({
                'source': a, 'target': b, 'shared_words': shared,
            })

    for node in nodes:
        if not node['node_gmif_type']:
            gaps['missing_requirements'].append({
                'node': node['label'],
                'node_key': node['node_key'],
            })

    conn.close()
    return gaps


def _shared_words(a, b):
    """Words the two labels share, ignoring case and punctuation."""
    def words(s):
        return set(re.findall(r'[\wÀ-ÿ]+', str(s).lower()))
    return len(words(a) & words(b))


def _research_gap(gap_type, gap_data):
    """Search for the evidence that would justify a stronger link.

    Returns the model's synthesis, or None. The desired level is read from
    GMIF_DESIRED_LEVEL here and passed on explicitly: the previous version
    computed it in this function and then referenced it from
    _apply_research_to_graph, where it did not exist.
    """
    templates = {
        'weak_edge': (
            f"Gera uma query de pesquisa para entender a relação '{gap_data['label']}' "
            f"entre '{gap_data['source']}' e '{gap_data['target']}'. Apenas a query, sem aspas."
        ),
        'disconnected_pair': (
            f"Gera uma query de pesquisa sobre a relação entre '{gap_data['source']}' e "
            f"'{gap_data['target']}' (palavras comuns: {gap_data['shared_words']}). "
            f"Apenas a query."
        ),
        'missing_requirement': (
            f"Gera uma query de pesquisa sobre o que '{gap_data['node']}' depende ou "
            f"requer para ser válido. Apenas a query."
        ),
        'causal_gap': (
            f"Gera uma query para encontrar mecanismos causais que expliquem como "
            f"'{gap_data['source']}' leva a '{gap_data['target']}'. Apenas a query."
        ),
    }
    if gap_type not in templates:
        return None

    query = _safe_ollama_chat(templates[gap_type], "Especialista em Pesquisa Científica.")
    if not query:
        return None

    results = search_with_searxng(query.strip(), max_results=5)
    if not results:
        return None

    desired_level = GMIF_DESIRED_LEVEL[gap_type]
    synth_prompt = (
        f"Com base nestes resultados, sintetiza o conhecimento que sustenta a relação "
        f"entre '{gap_data.get('source', gap_data.get('node'))}' e "
        f"'{gap_data.get('target', gap_data.get('node'))}'. "
        f"Retorna JSON: {{'conhecimento': '...', 'confianca': 0.0-1.0, "
        f"'tipo_relacao': 'depends_on|causes|part_of|similar|contradicts', "
        f"'evidencia': ['trecho1', 'trecho2']}}. "
        f"Contexto: {results}"
    )

    ans = _safe_ollama_chat(synth_prompt, "Cientista de Conhecimento Rigoroso.")
    parsed = _extract_json(ans)
    if not parsed:
        return None
    # The level travels with the result, so the writer never has to know it.
    parsed['desired_level'] = desired_level
    return parsed


def _apply_research_to_graph(research_results, gap_type, gap_data):
    """Write the researched link into the graph, with its evidence attached.

    Writes are gated: an empty synthesis or an empty evidence list is refused,
    because an unevidenced promotion is exactly the false certainty this cycle
    exists to remove. Every write also lands in graph_edit_audit through
    graph_edit.py, so it is reversible.
    """
    if not research_results:
        return False
    knowledge = research_results.get('conhecimento')
    evidence = research_results.get('evidencia') or []
    desired_level = research_results.get('desired_level') or GMIF_DESIRED_LEVEL.get(gap_type)
    if not knowledge or not evidence:
        print("⚠️ [Dream-GMIF] Recusado: síntese sem evidência.")
        return False

    confidence = float(research_results.get('confianca', 0.5) or 0.0)
    rel_type = research_results.get('tipo_relacao', 'related_to')
    now = datetime.datetime.now().isoformat()

    conn = sqlite3.connect(config.DB_PATH)
    try:
        if gap_type in ('weak_edge', 'causal_gap'):
            conn.execute(
                "UPDATE memory_graph SET gmif_level = ?, gmif_validation_type = ?, "
                "gmif_validation_confidence = ?, gmif_source_chunks = ?, "
                "gmif_classified_at = ?, gmif_classified_by = ? "
                "WHERE node_key = ?",
                (desired_level, GMIF_VALIDATION_TYPE, confidence,
                 json.dumps(evidence), now, "dream_gmif_research",
                 gap_data.get('node_key') or f"edge:{gap_data['source']}|{gap_data['target']}"),
            )
            changed = conn.total_changes
        elif gap_type == 'disconnected_pair':
            source = gap_data['source']
            target = gap_data['target']
            conn.execute(
                "INSERT OR IGNORE INTO memory_graph "
                "(node_key, node_type, label, source, target, affinity, weight, "
                " created_at, updated_at, gmif_level, gmif_validation_type, "
                " gmif_extraction_confidence, gmif_validation_confidence, "
                " gmif_source_chunks, gmif_classified_at, gmif_classified_by) "
                "VALUES (?, 'edge', ?, ?, ?, 0.0, 1.0, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"edge:{source}|{target}", f"{source} {rel_type} {target}",
                 source, target, now, now, desired_level, GMIF_VALIDATION_TYPE,
                 confidence, confidence, json.dumps(evidence), now, "dream_gmif_research"),
            )
            changed = conn.total_changes
        else:
            changed = 0
        conn.commit()
        if changed:
            print(f"🧬 [Dream-GMIF] {gap_type} escrito em {desired_level} "
                  f"(validação={GMIF_VALIDATION_TYPE}, conf={confidence})")
        return bool(changed)
    except Exception as e:
        print(f"⚠️ [Dream-GMIF] Erro a aplicar pesquisa: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def _reduce_to_concepts():
    """Turn recent raw memories into GMIF-classified concept nodes.

    This is the "reduce memory to concepts" half: each row's text is distilled,
    the concepts become nodes with edges, and the classifier stamps them. The
    original rows stay in `memories` -- consolidation is what removes them, and
    it archives them first.
    """
    from src.brain.memory_graph import materialize_memories
    from src.pipeline.gmif_classifier import classify_all_edges, classify_all_nodes

    report = materialize_memories(only_unparsed=True)
    if report["nodes_written"] or report["edges_written"]:
        print(f"🧬 [Dream-GMIF] Materializados {report['nodes_written']} conceitos e "
              f"{report['edges_written']} ligações de {report['memories_read']} memórias.")

    conn = sqlite3.connect(config.DB_PATH)
    try:
        e = classify_all_edges(conn)
        n = classify_all_nodes(conn)
        conn.commit()
        print(f"🧬 [Dream-GMIF] Classificados: {e} arestas, {n} nós.")
    except Exception as exc:
        print(f"⚠️ [Dream-GMIF] Classificação falhou: {exc}")
    finally:
        conn.close()


def _optimize_graph(materialize: bool = True):
    """The graph step: materialise concepts, find gaps, research, apply.

    ``materialize=False`` is for the night cycle, which already ran the
    materialisation before the destructive consolidation -- see
    :func:`perform_dreaming` for why the order matters.
    """
    if not GMIF_DREAM_ENABLED:
        return
    if materialize:
        print("🧬 [Dream-GMIF] A reduzir memória a conceitos...")
        _reduce_to_concepts()

    gaps = _analyze_graph_gaps()
    print(f"🧬 [Dream-GMIF] {gaps['edge_count']} arestas, {gaps['node_count']} nós | "
          f"{len(gaps['weak_edges'])} fracas, {len(gaps['disconnected_pairs'])} desconectadas, "
          f"{len(gaps['missing_requirements'])} sem tipo, {len(gaps['causal_gaps'])} causais")

    researched = applied = 0
    for gap_type, gap_list in (
        ('causal_gap', gaps['causal_gaps']),
        ('weak_edge', gaps['weak_edges']),
        ('disconnected_pair', gaps['disconnected_pairs']),
        ('missing_requirement', gaps['missing_requirements']),
    ):
        for gap in gap_list[:MAX_RESEARCH_PER_CYCLE]:
            if researched >= MAX_RESEARCH_PER_CYCLE:
                break
            research = _research_gap(gap_type, gap)
            if not research:
                continue
            researched += 1
            if _apply_research_to_graph(research, gap_type, gap):
                applied += 1
            save_to_rag(f"Dream-GMIF Insight ({gap_type}): {research.get('conhecimento', '')}")
            time.sleep(5)

    print(f"🧬 [Dream-GMIF] Ciclo completo: {researched} pesquisados, "
          f"{applied} aplicados ao grafo.")


# --- Daemon & Logic ---

def perform_dreaming(mode="auto"):
    """ Executa o ciclo de processamento offline. """
    if not DREAM_ENABLED:
        print("🚫 [Dream] O processo onírico está desativado.")
        return

    # Concepts are extracted BEFORE the purge, not after. Consolidation deletes
    # the 20 most recent rows outright and replaces them with a single summary
    # whose tags are never asked for (and so are always absent). If the graph
    # were built afterwards, every concept those 20 rows held would be destroyed
    # with them, once per night. Extracting first makes the concepts the thing
    # that survives and the raw repetitions the thing that goes -- which is what
    # "reduce memory to concepts, remove repetitions" has to mean.
    if GMIF_DREAM_ENABLED:
        _reduce_to_concepts()

    _consolidate_memories()

    # The analysis/research half. materialize=False: the concepts were already
    # extracted above, and the filter has nothing new to index afterwards (the
    # consolidation's summary carries no tags by construction).
    _optimize_graph(materialize=False)

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

    # Optimização do grafo -- checked before the generic "liga"/"desliga", so
    # "liga gmif" does not also wake the whole dream cycle.
    if "gmif" in user_prompt_lower or "grafo" in user_prompt_lower:
        if any(x in user_prompt_lower for x in ["desliga", "cancela", "para"]):
            globals()['GMIF_DREAM_ENABLED'] = False
            return "Deixo de mexer no grafo. As ligações ficam como estão."
        if any(x in user_prompt_lower for x in ["liga", "ativa"]):
            globals()['GMIF_DREAM_ENABLED'] = True
            return "Volto a olhar para o grafo durante o sono."
        threading.Thread(target=_optimize_graph, daemon=True).start()
        return "A olhar para o grafo: reduzir a conceitos, procurar as ligações fracas."

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
