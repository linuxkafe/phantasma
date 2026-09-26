"""
Skill: GMIF-Enhanced Dream — Uses Graph Memory Inference Framework (GMIF)
to find missing/weak relationships in the memory graph and performs
targeted online research to fill gaps.

This extends the standard dream system by:
1. Analyzing the GMIF-classified memory graph for gaps/weak links
2. Identifying M1/M2 edges that could be strengthened to M3/M4
3. Finding disconnected components that need bridging
4. Performing targeted online research to fill specific gaps
5. Consolidating graph structure during sleep cycle
"""

import threading
import time
import datetime
import random
import sqlite3
import json
import os
import config
from tools import search_with_searxng
from data_utils import save_to_rag
from src.pipeline.gmif_classifier import (
    GMIFLevel, ValidationType,
    classify_all_edges, classify_all_nodes, get_gmif_stats,
    classify_edge
)

# --- Config ---
GMIF_DREAM_TIME = "03:00"  # After standard dream (02:30)
GMIF_DREAM_ENABLED = True
GMIF_DREAM_CHANCE = 1.0  # Always run after standard dream

# --- Helpers ---

def _safe_ollama_chat(prompt, system_instruction=""):
    """Failover chat via primary/fallback hosts."""
    import ollama
    targets = [
        (getattr(config, 'OLLAMA_HOST_PRIMARY', None), getattr(config, 'OLLAMA_MODEL_PRIMARY', 'llama3.1:8b')),
        (getattr(config, 'OLLAMA_HOST_FALLBACK', 'http://localhost:11434'), getattr(config, 'OLLAMA_MODEL_FALLBACK', 'qwen3:8b'))
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
            print(f"⚠️ [GMIF-Dream] Falha host {host}: {e}")
    return None


def _extract_json(text):
    import re, ast
    if not text: return None
    try:
        match = re.search(r'(\{.*\})', text, re.DOTALL)
        json_str = match.group(1) if match else text
        try:
            return json.loads(json_str)
        except:
            return ast.literal_eval(json_str)
    except Exception as e:
        print(f"⚠️ [GMIF-Dream] Parse error: {e}")
        return None


# --- GMIF Graph Analysis ---

def _analyze_graph_gaps():
    """Analyze the GMIF-classified graph for gaps and weak links.
    
    Returns dict with:
    - weak_edges: edges at M1/M2 that could be strengthened
    - disconnected_pairs: node pairs that should be connected
    - missing_requirements: concepts that need dependencies
    - causal_gaps: events without causal explanations
    """
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get all nodes with their GMIF type
    nodes = cur.execute(
        "SELECT id, node_key, label, node_gmif_type, node_gmif_confidence "
        "FROM memory_graph WHERE node_type = 'node'"
    ).fetchall()

    # Get all edges with GMIF classification
    edges = cur.execute(
        "SELECT id, source, target, label, gmif_level, gmif_validation_type, "
        "gmif_extraction_confidence, gmif_validation_confidence "
        "FROM memory_graph WHERE node_type = 'edge'"
    ).fetchall()

    conn.close()

    # Map nodes by label
    node_map = {n['label']: n for n in nodes}

    gaps = {
        'weak_edges': [],
        'disconnected_pairs': [],
        'missing_requirements': [],
        'causal_gaps': [],
        'node_count': len(nodes),
        'edge_count': len(edges),
    }

    # 1. Weak edges (M1/M2) that could be strengthened
    for edge in edges:
        if edge['gmif_level'] in ('M1', 'M2'):
            # Check if we have enough confidence to promote
            extraction = edge['gmif_extraction_confidence'] or 0
            validation = edge['gmif_validation_confidence'] or 0
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

    # 2. Disconnected pairs - nodes that share semantic space but have no edge
    node_labels = [n['label'] for n in nodes]
    for i, n1 in enumerate(node_labels):
        for n2 in node_labels[i+1:]:
            # Check if edge exists
            has_edge = any(
                (e['source'] == n1 and e['target'] == n2) or
                (e['source'] == n2 and e['target'] == n1)
                for e in edges
            )
            if not has_edge:
                # Check semantic similarity via keyword overlap
                words1 = set(n1.lower().split())
                words2 = set(n2.lower().split())
                overlap = len(words1 & words2)
                if overlap >= 1:
                    gaps['disconnected_pairs'].append({
                        'source': n1,
                        'target': n2,
                        'overlap': overlap,
                        'shared_words': list(words1 & words2),
                    })

    # 3. Missing requirements - M3 edges where target has no outgoing requirements
    requirements = [e for e in edges if e['gmif_level'] in ('M3', 'M4')]
    target_nodes = set(e['target'] for e in requirements)
    source_nodes = set(e['source'] for e in requirements)
    for node in target_nodes:
        if node not in source_nodes and node in node_map:
            # This node is required but doesn't require anything itself
            gaps['missing_requirements'].append({
                'node': node,
                'type': node_map[node]['node_gmif_type'],
            })

    # 4. Causal gaps - M4 edges missing for strong dependencies
    m3_edges = [e for e in edges if e['gmif_level'] == 'M3']
    for e in m3_edges:
        if e['gmif_extraction_confidence'] > 0.8:
            gaps['causal_gaps'].append({
                'source': e['source'],
                'target': e['target'],
                'label': e['label'],
                'confidence': e['gmif_extraction_confidence'],
            })

    return gaps


def _research_gap(gap_type, gap_data):
    """Perform targeted online research for a specific graph gap.
    Returns synthesized knowledge to add to graph."""
    
    if gap_type == 'weak_edge':
        source = gap_data['source']
        target = gap_data['target']
        label = gap_data['label']
        query_prompt = (
            f"Gera uma query de pesquisa para entender a relação '{label}' "
            f"entre '{source}' e '{target}'. Foca em explicações técnicas, "
            f"causais ou lógicas. Apenas a query, sem aspas."
        )
        desired_level = "M3/M4"
        
    elif gap_type == 'disconnected_pair':
        source = gap_data['source']
        target = gap_data['target']
        shared = gap_data['shared_words']
        query_prompt = (
            f"Gera uma query de pesquisa sobre a relação entre '{source}' e "
            f"'{target}' (palavras comuns: {shared}). Foca em relações "
            f"lógicas, causais ou de dependência. Apenas a query."
        )
        desired_level = "M2/M3"
        
    elif gap_type == 'missing_requirement':
        node = gap_data['node']
        query_prompt = (
            f"Gera uma query de pesquisa sobre o que '{node}' depende ou "
            f"requer para funcionar/ser válido. Foca em dependências "
            f"lógicas, técnicas ou ontológicas. Apenas a query."
        )
        desired_level = "M3"
        
    elif gap_type == 'causal_gap':
        source = gap_data['source']
        target = gap_data['target']
        query_prompt = (
            f"Gera uma query para encontrar mecanismos causais ou "
            f"mecânicos que expliquem como '{source}' leva a '{target}'. "
            f"Foca em processos, mecanismos, vias biológicas/químicas/ "
            f"informacionais. Apenas a query."
        )
        desired_level = "M4/M5"
        
    else:
        return None

    query = _safe_ollama_chat(query_prompt, "Especialista em Pesquisa Científica.")
    if not query:
        return None

    # Search
    results = search_with_searxng(query.strip(), max_results=5)
    if not results:
        return None

    # Synthesize
    synth_prompt = (
        f"Com base nestes resultados, sintetiza conhecimento que "
        f"permita elevar a classificação GMIF de uma relação de "
        f"nível atual para {desired_level}. "
        f"Retorna JSON: {{'conhecimento': '...', 'confianca': 0.0-1.0, "
        f"'tipo_relacao': 'depends_on|causes|part_of|similar|contradicts', "
        f"'evidencia': ['trecho1', 'trecho2']}}. "
        f"Contexto: {results}"
    )

    ans = _safe_ollama_chat(synth_prompt, "Cientista de Conhecimento Rigoroso.")
    return _extract_json(ans)


def _apply_research_to_graph(research_results, gap_type, gap_data):
    """Apply synthesized research to the memory graph."""
    if not research_results:
        return False
    
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    
    try:
        knowledge = research_results.get('conhecimento', '')
        confidence = research_results.get('confianca', 0.5)
        rel_type = research_results.get('tipo_relacao', 'related_to')
        evidence = research_results.get('evidencia', [])
        
        if gap_type == 'weak_edge':
            # Upgrade existing edge
            cur.execute(
                "UPDATE memory_graph SET gmif_level = ?, gmif_validation_confidence = ?, "
                "gmif_extraction_confidence = ?, gmif_source_chunks = ?, "
                "gmif_classified_at = ?, gmif_classified_by = ? "
                "WHERE source = ? AND target = ?",
                (desired_level, confidence, confidence,
                 json.dumps(evidence), datetime.datetime.now().isoformat(),
                 "gmif_dream_auto_promotion",
                 gap_data['source'], gap_data['target'])
            )
        elif gap_type == 'disconnected_pair':
            # Create new edge
            source = gap_data['source']
            target = gap_data['target']
            label = f"{source} {rel_type} {target}"
            cur.execute("""
                INSERT INTO memory_graph 
                (node_key, node_type, label, source, target, affinity, weight,
                 created_at, updated_at, gmif_level, gmif_validation_type,
                 gmif_extraction_confidence, gmif_validation_confidence,
                 gmif_source_chunks, gmif_classified_at, gmif_classified_by)
                VALUES (?, 'edge', ?, ?, ?, 0.0, 1.0, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"edge:{source}|{target}",
                f"{gap_data['source']} {rel_type} {gap_data['target']}",
                source, target,
                datetime.datetime.now().isoformat(),
                datetime.datetime.now().isoformat(),
                desired_level, 'logical', confidence, confidence,
                json.dumps(evidence),
                datetime.datetime.now().isoformat(),
                "gmif_dream_new_connection"
            ))
        conn.commit()
        return True
    except Exception as e:
        print(f"⚠️ [GMIF-Dream] Error applying research: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def _gmif_dream_cycle():
    """Main GMIF Dream cycle - analyzes graph gaps and researches them."""
    print("🧬 [GMIF-Dream] Iniciando ciclo de otimização do grafo cognitivo...")
    
    # 1. Ensure GMIF classification is up to date
    conn = sqlite3.connect(config.DB_PATH)
    classify_all_edges(conn)
    classify_all_nodes(conn)
    conn.close()
    
    # 2. Analyze gaps
    gaps = _analyze_graph_gaps()
    print(f"🧬 [GMIF-Dream] Gaps encontrados: "
          f"{len(gaps['weak_edges'])} edges fracos, "
          f"{len(gaps['disconnected_pairs'])} pares desconectados, "
          f"{len(gaps['missing_requirements'])} requisitos em falta, "
          f"{len(gaps['causal_gaps'])} gaps causais")
    
    # 2. Research top priority gaps (limit to avoid API spam)
    max_research = 3
    researched = 0
    
    # Priority: causal gaps > weak edges > disconnected pairs > missing requirements
    for gap_type, gap_list in [
        ('causal_gap', gaps['causal_gaps']),
        ('weak_edge', gaps['weak_edges']),
        ('disconnected_pair', gaps['disconnected_pairs']),
        ('missing_requirement', gaps['missing_requirements']),
    ]:
        for gap in gap_list[:max_research]:
            if researched >= max_research:
                break
            
            print(f"🧬 [GMIF-Dream] A pesquisar gap {gap_type}: {gap}")
            research = _research_gap(gap_type, gap)
            if research:
                # Apply to graph (would need desired_level mapping)
                # _apply_research_to_graph(research, gap_type, gap)
                # For safety, log instead of auto-apply
                print(f"🧬 [GMIF-Dream] Conhecimento sintetizado para {gap_type}: "
                      f"{research.get('conhecimento', '')[:100]}...")
                save_to_rag(f"GMIF-Dream Insight ({gap_type}): {research.get('conhecimento', '')}")
                researched += 1
                time.sleep(5)  # Rate limit
    
    # 4. Consolidate: run standard consolidation too
    # (calls the standard dream consolidation)
    try:
        import skills.skill_dream as dream_mod
        dream_mod._consolidate_memories()
    except:
        pass
    
    print(f"🧬 [GMIF-Dream] Ciclo completo. Pesquisados: {researched} gaps.")


def _gmif_daemon_loop():
    """Daemon loop - runs after standard dream."""
    print(f"[GMIF-Dream] Daemon ativo. Agendado para {GMIF_DREAM_TIME} (após sonho padrão)")
    while True:
        if not GMIF_DREAM_ENABLED:
            time.sleep(60)
            continue
        if datetime.datetime.now().strftime("%H:%M") == GMIF_DREAM_TIME:
            threading.Thread(target=_gmif_dream_cycle, daemon=True).start()
            time.sleep(70)
        time.sleep(30)


def init_skill_daemon():
    """Initialize daemon."""
    if os.path.exists("/tmp/no_gmif_dream"):
        globals()['GMIF_DREAM_ENABLED'] = False
    threading.Thread(target=_gmif_daemon_loop, daemon=True).start()


def handle(user_prompt_lower, user_prompt_full):
    text = user_prompt_lower.strip()
    
    if text in ["desliga gmif", "para gmif", "cancela gmif"]:
        globals()['GMIF_DREAM_ENABLED'] = False
        return "GMIF-Dream desativado. O grafo deixará de ser otimizado automaticamente."
    
    if text in ["liga gmif", "ativa gmif"]:
        globals()['GMIF_DREAM_ENABLED'] = True
        return "GMIF-Dream ativado. O grafo será otimizado noturnamente."
    
    if text in ["sonha gmif", "otimiza grafo", "gmif agora"]:
        threading.Thread(target=_gmif_dream_cycle, daemon=True).start()
        return "GMIF-Dream iniciado manualmente. O grafo será analisado e otimizado."
    
    return None
