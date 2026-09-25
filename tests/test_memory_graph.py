"""Tests for memory graph — FlyBrain_affinity-weighted topic store (memory.db)."""

import pytest


@pytest.fixture()
def graph(tmp_path, monkeypatch):
    """Memory-graph module pointed at a throwaway SQLite DB."""
    db = tmp_path / "memory.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(db))
    monkeypatch.setenv("DB_PATH", str(db))
    import config

    monkeypatch.setattr(config, "DB_PATH", str(db))
    monkeypatch.setattr(config, "MEMORY_DB_PATH", str(db))

    from src.brain import memory_graph as g

    g.init_db()
    return g


def test_index_memory_creates_nodes_and_current_topic(graph):
    """tags become nodes, first tag is current topic."""
    keys = graph.index_memory(
        {"tags": ["capitalismo", "alternativas"], "novelty": 0.3}
    )
    assert "node:capitalismo" in keys
    assert "node:alternativas" in keys
    assert graph.get_current_topic() == "node:capitalismo"


def test_mermaid_edges_indexed(graph):
    """A --> B relations become directed edges, labelled by [] content."""
    graph.index_memory(
        {
            "tags": ["capitalismo"],
            "mermaid": "graph TD; A[capitalismo] -->|melhor que| B[alternativas]",
        }
    )
    items = graph.retrieve_neighborhood("o que sabes sobre capitalismo?")
    edges = [i for i in items if i["type"] == "edge"]
    assert edges, "expected an edge for the capitalismo -> alternativas relation"
    assert "capitalismo -> alternativas" in edges[0]["relation"]


def test_reward_applies_to_current_topic(graph):
    """A FlyBrain reward is applied to the current topic node."""
    graph.index_memory({"tags": ["capitalismo"]})
    assert graph.get_current_topic() == "node:capitalismo"

    before = graph.retrieve_neighborhood("capitalismo")[0]
    graph.apply_reward(1.0)
    after = graph.retrieve_neighborhood("capitalismo")[0]

    assert after["affinity"] - before["affinity"] == pytest.approx(1.0)


def test_reward_without_topic_is_noop(graph):
    """apply_reward returns None when no current topic exists."""
    assert graph.apply_reward(1.0) is None


def test_retrieve_case_insensitive(graph):
    """Prompt matching is case-insensitive even when tags are lowercase."""
    graph.index_memory({"tags": ["capitalismo"]})
    items = graph.retrieve_neighborhood("CAPITALISMO")
    assert any("capitalismo" in i["label"] for i in items)


def test_graph_context_text_renders(graph):
    """graph_context_text produces a non-empty snippet only when matching."""
    assert graph.graph_context_text("boguszzz") == ""
    graph.index_memory({"tags": ["capitalismo"]})
    out = graph.graph_context_text("o que sabes sobre capitalismo?")
    assert "capitalismo" in out
