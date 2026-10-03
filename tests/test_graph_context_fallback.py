"""What the graph says when the question names nothing.

Measured on production, 2026-10-03, against 200 nodes and 1306 edges:

    retrieve_neighborhood("quem sou eu")          -> 0
    retrieve_neighborhood("e em Lisboa?")         -> 0
    retrieve_neighborhood("o que sabes de mim")  -> 0
    graph_context_text(...)                       -> "" for all three

The graph held a top affinity of 61 on "capitalismo tardio" and the assistant
saw none of it. The rule was "return the neighbourhood of a node the prompt
names", and a person rarely names a node -- they ask questions.

Two failures had to be fixed, and the second is why the first fix did not work:

1. Nothing matched at all, so there was nothing to return.
2. A WEAK substring match -- the assistant's own previous reply, which lives in
   the graph as a node with affinity 0.0 -- counted as a match. Measured:
   "quem sou eu" substring-matches the node
   "Olha, não sou uma pessoa que se apresenta com sorriso". `nodes` was then
   non-empty, so a fallback written as `if not nodes` never ran for the exact
   question it was written for, and MIN_AFFINITY dropped the weak match
   afterwards, returning "".

So the affinity filter has to run BEFORE the decision, not after it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brain import memory_graph as mg  # noqa: E402


def _ro(path):
    """A connection like the production one: rows addressable by column name."""
    import sqlite3

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


QUERIES = [
    "quem sou eu",
    "e em Lisboa?",
    "o que sabes de mim",
    "o que tastes",
]


def test_a_question_that_names_no_node_still_gets_context(monkeypatch, tmp_path):
    """The personality is what dominates, so it must reach the model.

    Stubbed graph, so the assertion is about the DECISION and not about whatever
    happens to be in this checkout's database. A test that reads a real graph
    passes on the machine that wrote it and says nothing on another.
    """
    import sqlite3

    db = tmp_path / "g.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE memory_graph (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_key TEXT NOT NULL UNIQUE,
            node_type TEXT NOT NULL,
            label TEXT NOT NULL,
            source TEXT, target TEXT,
            affinity REAL NOT NULL DEFAULT 0.0,
            weight REAL NOT NULL DEFAULT 1.0,
            touch_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    now = "2026-10-03T00:00:00"
    con.executemany(
        "INSERT INTO memory_graph (node_key,node_type,label,source,target,affinity,"
        "weight,touch_count,created_at,updated_at) VALUES (?,?,?,?,?,?,1,0,?,?)",
        [
            ("node:dominante", "node", "capitalismo tardio", None, None, 61.0, now, now),
            ("node:segundo", "node", "Plataformas Digitais", None, None, 4.0, now, now),
            # The trap: the assistant's own previous reply, a weak substring hit.
            ("node:resposta", "node",
             "Olha, não sou uma pessoa que se apresenta com sorriso",
             None, None, 0.0, now, now),
        ],
    )
    con.commit()
    con.close()

    # row_factory matters: the production `_connect` sets it, and the code
    # indexes rows by column name.
    monkeypatch.setattr(mg, "_connect", lambda: _ro(db))

    for q in QUERIES:
        got = mg.retrieve_neighborhood(q)
        labels = [i["label"] for i in got]
        assert "capitalismo tardio" in labels, (
            f"{q!r} nao recebeu contexto dominante: {labels}. O grafo sabe quem e "
            f"esta casa e o modelo nao fica a saber."
        )


def test_a_weak_substring_hit_does_not_displace_the_dominant_nodes(monkeypatch, tmp_path):
    """The specific trap, isolated.

    "quem sou eu" DOES substring-match a node -- the assistant's own reply. That
    made `nodes` non-empty, which meant the dominant-nodes fallback never ran,
    and the weak node was then dropped by MIN_AFFINITY, so the function returned
    nothing at all. The common case was hiding the rare one.
    """
    import sqlite3

    db = tmp_path / "g2.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE memory_graph (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_key TEXT NOT NULL UNIQUE,
            node_type TEXT NOT NULL,
            label TEXT NOT NULL,
            source TEXT, target TEXT,
            affinity REAL NOT NULL DEFAULT 0.0,
            weight REAL NOT NULL DEFAULT 1.0,
            touch_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    now = "2026-10-03T00:00:00"
    con.executemany(
        "INSERT INTO memory_graph (node_key,node_type,label,source,target,affinity,"
        "weight,touch_count,created_at,updated_at) VALUES (?,?,?,?,?,?,1,0,?,?)",
        [
            ("node:a", "node", "capitalismo tardio", None, None, 61.0, now, now),
            ("node:b", "node", "não sou eu", None, None, 0.0, now, now),
        ],
    )
    con.commit()
    con.close()

    # row_factory matters: the production `_connect` sets it, and the code
    # indexes rows by column name.
    monkeypatch.setattr(mg, "_connect", lambda: _ro(db))

    got = [i["label"] for i in mg.retrieve_neighborhood("quem sou eu")]
    assert got == ["capitalismo tardio"], got
    assert "não sou eu" not in got, (
        "a resposta anterior do assistente, com afinidade 0, foi injectada como "
        "contexto -- e era o que impedia o dominante de chegar"
    )


def test_the_dominant_nodes_come_back_ranked(monkeypatch, tmp_path):
    """Prevalence is the point, so order matters and affinity 0 must not."""
    import sqlite3

    db = tmp_path / "g3.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE memory_graph (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_key TEXT NOT NULL UNIQUE,
            node_type TEXT NOT NULL,
            label TEXT NOT NULL,
            source TEXT, target TEXT,
            affinity REAL NOT NULL DEFAULT 0.0,
            weight REAL NOT NULL DEFAULT 1.0,
            touch_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO memory_graph VALUES
            (1,'n1','node','alto',NULL,NULL,10.0,1.0,0,'t','t'),
            (2,'n2','node','baixo',NULL,NULL,0.5,1.0,0,'t','t'),
            (3,'n3','node','nulo',NULL,NULL,0.0,1.0,0,'t','t');
        """
    )
    con.commit()
    con.close()

    # row_factory matters: the production `_connect` sets it, and the code
    # indexes rows by column name.
    monkeypatch.setattr(mg, "_connect", lambda: _ro(db))
    got = [i["label"] for i in mg.retrieve_neighborhood("pergunta qualquer")]
    assert got[0] == "alto", got
    assert "nulo" not in got, f"afinidade zero entrou como contexto: {got}"
