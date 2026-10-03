"""The graph: what a reaction reinforces, and what the LLM is shown.

src/brain/memory_graph.py and src/brain/reactions.py were at 0% when this file
was written, and both were the source of real defects found by measurement
rather than by test:

- retrieve_neighborhood returned neighbours at affinity 0.00, including the
  assistant's own previous reply. Asked "ola" it retrieved a stored answer
  about cats: a bad reply became a node and fed the next one. MIN_AFFINITY
  exists because of that, and the invariant -- no context below the
  threshold -- is what has to stay true.
- find_node_for_text decides whether a reaction reinforces the node the
  message was actually about. A loose match here rewards the wrong topic and
  the owner never finds out.

Both are silent when broken: a reaction that reinforces nothing, and context
that is merely noise, look the same from the outside.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brain import memory_graph as mg  # noqa: E402


@pytest.fixture
def graph(tmp_path, monkeypatch):
    """A real memory.db, created by the module's own init."""
    db = tmp_path / "brain.db"
    monkeypatch.setattr(mg.config, "DB_PATH", str(db))
    mg.init_db()
    return db


def _row(db, node_key):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM memory_graph WHERE node_key = ?", (node_key,)
    ).fetchone()
    con.close()
    return dict(row) if row else None


def _insert_node(db, key, label, affinity, weight=1.0, source="memory"):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO memory_graph (node_key, node_type, label, source, affinity,"
        " weight, touch_count, created_at, updated_at)"
        " VALUES (?, 'node', ?, ?, ?, ?, 1, '2026-01-01', '2026-01-01')",
        (key, label, source, affinity, weight),
    )
    con.commit()
    con.close()


# --- the affinity threshold ------------------------------------------------

def test_zero_affinity_is_not_context(graph):
    """The defect: a stored answer with affinity 0.00 was handed to the LLM
    for an unrelated question, and became knowledge for the next turn."""
    _insert_node(graph, "node:resposta", "Olá! O nome Bimby parece associado "
                 "a gatos com nomes próprios", 0.0, source="assistant")
    assert mg.graph_context_text("olá") == "", (
        "a zero-affinity node was offered as context; the graph is quoting "
        "its own past answers"
    )


def test_context_below_the_threshold_is_excluded(graph):
    _insert_node(graph, "node:fraco", "assunto fracamente ligado", 0.01)
    assert mg.graph_context_text("assunto fracamente ligado") == ""


def test_context_at_the_threshold_is_included(graph):
    _insert_node(graph, "node:limite", "assunto mesmo ligado",
                 mg.MIN_AFFINITY)
    out = mg.graph_context_text("assunto mesmo ligado")
    assert "assunto mesmo ligado" in out


def test_relevant_node_still_reaches_the_llm(graph):
    """The threshold must not become a mute button."""
    _insert_node(graph, "node:capitalismo", "capitalismo tardio", 61.0)
    _insert_node(graph, "node:plataformas", "Plataformas Digitais", 4.0)
    out = mg.graph_context_text("plataformas digitais")
    assert "Plataformas Digitais" in out, (
        "a node well above the threshold was withheld: the fix muted it"
    )


def test_the_neighbourhood_still_expands_along_an_edge(graph):
    """Neighbours come from edges, not from affinity alone: two unrelated
    nodes stay independent. Asserted because the expansion is the part of
    the query the threshold was added next to."""
    _insert_node(graph, "node:capitalismo", "capitalismo tardio", 61.0)
    _insert_node(graph, "node:plataformas", "Plataformas Digitais", 4.0)
    con = sqlite3.connect(graph)
    con.execute(
        "INSERT INTO memory_graph (node_key, node_type, label, source,"
        " target, affinity, weight, touch_count, created_at, updated_at)"
        " VALUES ('node:edge', 'edge', 'Capitalismo Tardio -> Plataformas',"
        " 'node:capitalismo', 'node:plataformas', 0.15, 0.77, 1,"
        " '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()
    out = mg.graph_context_text("plataformas digitais")
    assert "capitalismo tardio" in out, "the edge did not bring its neighbour"


def test_the_neighbourhood_expands_along_the_source_side_too(graph):
    """Both directions of an edge have to work.

    The first version of this assertion used an edge whose *target* was the
    matched node, and it passed even with the comparison bug reintroduced --
    because `LOWER(target) = LOWER(label)` happened to line up. Matching on
    the source side is what actually broke: source/target hold node_keys
    ("node:capitalismo"), the comparison used the label, so it never matched.
    A test on one side of an asymmetric bug is half a test.
    """
    _insert_node(graph, "node:capitalismo", "capitalismo tardio", 61.0)
    _insert_node(graph, "node:plataformas", "Plataformas Digitais", 4.0)
    con = sqlite3.connect(graph)
    con.execute(
        "INSERT INTO memory_graph (node_key, node_type, label, source, target,"
        " affinity, weight, touch_count, created_at, updated_at)"
        " VALUES ('node:edge', 'edge', 'Plataformas -> Capitalismo',"
        " 'node:plataformas', 'node:capitalismo', 0.15, 0.77, 1,"
        " '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()
    out = mg.graph_context_text("plataformas digitais")
    assert "capitalismo tardio" in out, (
        "the edge's source side did not expand; the relation would have "
        "rendered as a raw node_key"
    )


def test_relations_render_as_labels_not_node_keys(graph):
    """The consumer of this string is the LLM and the 3D view; "node:cap" is
    a database detail leaking into both."""
    _insert_node(graph, "node:plataformas", "Plataformas Digitais", 4.0)
    _insert_node(graph, "node:capitalismo", "capitalismo tardio", 61.0)
    con = sqlite3.connect(graph)
    con.execute(
        "INSERT INTO memory_graph (node_key, node_type, label, source, target,"
        " affinity, weight, touch_count, created_at, updated_at)"
        " VALUES ('node:edge', 'edge', 'E', 'node:plataformas',"
        " 'node:capitalismo', 0.15, 0.77, 1, '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()
    out = mg.graph_context_text("plataformas digitais")
    assert "node:" not in out, f"a node_key leaked into the context: {out!r}"


def test_a_greeting_still_carries_the_dominant_context(graph):
    """An unrelated question DOES get context, and that is deliberate.

    This test asserted the opposite: with a node at affinity 61, "bom dia" must
    return "". It was my own test, written when the rule was "return the
    neighbourhood of a node the prompt names" -- and the owner's instruction
    reversed that rule:

        "as mais prevalentes devem ser usadas na resposta, isso define a
         personalidade"

    What dominates the graph is who this house is. A top affinity of 61 on
    "capitalismo tardio" is a statement about the assistant, and handing that to
    the model on every message -- greeting, question, aside -- is what makes the
    register consistent instead of dependent on whether the owner happened to
    name a node.

    The counter-argument was a real one, and it is why the decision is recorded
    here rather than made silently: context on a greeting is context that can be
    wasted, and a weaker node could surface on "bom dia" and colour a
    conversation it has nothing to do with. The counter to that is MIN_AFFINITY,
    which drops the assistant's own previous replies (measured: they live in the
    graph as nodes at affinity 0.0) -- the exact noise this rule could have
    introduced.

    What it must NOT become is "a greeting invents context": the dominant nodes
    and nothing else.
    """
    _insert_node(graph, "node:capitalismo", "capitalismo tardio", 61.0)
    text = mg.graph_context_text("bom dia")
    assert "capitalismo tardio" in text, (
        f'"bom dia" nao recebeu contexto dominante: {text!r}. A personalidade '
        f"e o que domina o grafo, e nao depende de a pergunta nomear um no."
    )


def test_a_greeting_never_gets_the_noise(graph):
    """The other half: affinity 0 is not context, whatever the question is.

    Without this, "always include the dominant nodes" could inject the
    assistant's own previous answer as context -- and those live in the graph as
    nodes. A bad answer would then feed itself: ask about cats, get an answer
    about cats, that answer becomes a node, and the next unrelated question
    retrieves it.
    """
    _insert_node(graph, "node:capitalismo", "capitalismo tardio", 61.0)
    _insert_node(graph, "node:anterior", "Não, isso é um erro comum.", 0.0)
    text = mg.graph_context_text("bom dia")
    assert "erro comum" not in text, (
        f"a resposta anterior do assistente entrou como contexto: {text!r}"
    )


# --- find_node_for_text: which node a reaction reinforces -------------------

def test_finds_the_node_the_text_names(graph):
    _insert_node(graph, "node:bimby", "Bimby", 1.0)
    found = mg.find_node_for_text("o nosso gato chama-se Bimby")
    assert found is not None and found["node_key"] == "node:bimby"


def test_does_not_invent_a_node_from_prose(graph):
    """Returning a match for text that names nothing would reward whatever
    node the matcher guessed, and the owner would see a reinforced topic they
    never reacted to."""
    _insert_node(graph, "node:bimby", "Bimby", 1.0)
    assert mg.find_node_for_text("o que achas do tempo") is None


def test_empty_text_finds_nothing(graph):
    _insert_node(graph, "node:bimby", "Bimby", 1.0)
    assert mg.find_node_for_text("") is None


# --- apply_reward: the FlyBrain effect of a reaction -----------------------

def test_positive_reward_raises_affinity(graph):
    _insert_node(graph, "node:bimby", "Bimby", 1.0)
    mg.set_current_topic("node:bimby")
    assert mg.apply_reward(1.0) == "node:bimby"
    assert _row(graph, "node:bimby")["affinity"] == pytest.approx(2.0)


def test_negative_reward_lowers_affinity(graph):
    _insert_node(graph, "node:bimby", "Bimby", 1.0)
    mg.set_current_topic("node:bimby")
    mg.apply_reward(-1.0)
    assert _row(graph, "node:bimby")["affinity"] == pytest.approx(0.0)


def test_rewards_accumulate(graph):
    _insert_node(graph, "node:bimby", "Bimby", 0.0)
    mg.set_current_topic("node:bimby")
    for _ in range(3):
        mg.apply_reward(0.5)
    assert _row(graph, "node:bimby")["affinity"] == pytest.approx(1.5)


def test_reward_without_a_topic_is_a_no_op(graph):
    """Silently rewarding nothing is correct: there is no topic to reinforce,
    and inventing one would attach the reaction to whatever came first."""
    assert mg.get_current_topic() is None
    assert mg.apply_reward(1.0) is None


def test_explicit_topic_key_overrides_the_current_one(graph):
    _insert_node(graph, "node:a", "A", 1.0)
    _insert_node(graph, "node:b", "B", 1.0)
    mg.set_current_topic("node:a")
    mg.apply_reward(1.0, topic_key="node:b")
    assert _row(graph, "node:a")["affinity"] == pytest.approx(1.0)
    assert _row(graph, "node:b")["affinity"] == pytest.approx(2.0)


# --- reactions: which emoji carry which weight -----------------------------

def test_owner_set_weights_are_used(graph, monkeypatch):
    """A weight saved in /admin/config has to reach reward_for, or the page
    is decorative again."""
    from src import settings_store
    from src.brain import reactions

    thumb = "\U0001F44D"
    settings = settings_store.get_reaction_weights()
    settings[thumb] = 0.25
    settings_store.set_reaction_weights(settings, updated_by="test")
    reactions.reload_reaction_weights()
    assert reactions.reward_for(thumb) == 0.25
    settings_store.clear_setting(settings_store.REACTION_WEIGHTS_KEY)
    reactions.reload_reaction_weights()


def test_unmapped_emoji_is_none_not_zero(graph):
    """None and 0.0 are different events: "no opinion" and "neutral opinion"
    must not be the same, or every stray emoji on the network trains the
    graph."""
    from src.brain import reactions

    reactions.reload_reaction_weights()
    assert reactions.reward_for("\U0001F600") is None


def test_describe_exposes_the_current_weights(graph):
    from src.brain import reactions

    reactions.reload_reaction_weights()
    info = reactions.describe()
    assert info["emojis"], "describe() returned no emoji entries"
    assert len(info["emojis"]) == len(reactions.SUPPORTED_EMOJI)
    for entry in info["emojis"]:
        assert entry["reward"] == reactions.reward_for(entry["emoji"])
        assert entry["polarity"] in ("positive", "negative")


# --- record(): one reaction, one reply, one node ---------------------------

def _stub_flybrain(monkeypatch):
    """A FlyBrain that records the turn without the audio pipeline."""
    from src.brain import reactions

    stepped = []

    class _Ring:
        orientation_deg = 12.0

    class _Brain:
        """The interface record() actually uses: brain.ring.orientation_deg
        is read before brain.step(), so a stub missing it fails the way the
        real object would."""

        ring = _Ring()

        def step(self, topic_angle_deg, novelty, reward):
            stepped.append(reward)
            return True

    monkeypatch.setattr(reactions, "_resolve_brain", lambda: _Brain())
    return stepped


def test_reaction_reinforces_the_node_the_reply_mentions(graph, monkeypatch):
    """The rule that started this: a thumbs-up on an answer about one topic
    must not credit whatever topic happened to be current."""
    from src.brain import reactions

    _insert_node(graph, "node:capitalismo", "capitalismo tardio", 1.0)
    _insert_node(graph, "node:bimby", "Bimby", 1.0)
    mg.set_current_topic("node:capitalismo")

    _stub_flybrain(monkeypatch)
    report = reactions.record("\U0001F44D", message_text="o nosso gato Bimby")

    assert report["graph"] == "rewarded", f"report: {report}"
    assert _row(graph, "node:bimby")["affinity"] == pytest.approx(2.0), (
        "the reaction credited the ambient topic instead of the reply's"
    )
    assert _row(graph, "node:capitalismo")["affinity"] == pytest.approx(1.0)


def test_reaction_with_no_match_leaves_the_graph_alone(graph, monkeypatch):
    """An honest no_match beats a confident wrong node."""
    from src.brain import reactions

    _insert_node(graph, "node:capitalismo", "capitalismo tardio", 1.0)
    mg.set_current_topic("node:capitalismo")
    _stub_flybrain(monkeypatch)

    report = reactions.record("\U0001F44D", message_text="uma opinião sobre nada")
    assert report["graph"] == "no_match", f"report: {report}"
    assert _row(graph, "node:capitalismo")["affinity"] == pytest.approx(1.0), (
        "the graph was written for a reply that named no node"
    )
    assert report["topic_key"] is None


def test_reaction_still_steps_the_flybrain_on_no_match(graph, monkeypatch):
    """The gesture always has an effect; only the graph write is conditional."""
    from src.brain import reactions

    _insert_node(graph, "node:capitalismo", "capitalismo tardio", 1.0)
    stepped = _stub_flybrain(monkeypatch)
    report = reactions.record("\U0001F44D", message_text="nada sobre nada")
    assert report["flybrain"] == "stepped", f"report: {report}"
    assert stepped, "the FlyBrain turn was skipped on a no_match"


def test_unsupported_emoji_is_refused_with_the_supported_list(graph, monkeypatch):
    from src.brain import reactions

    _insert_node(graph, "node:bimby", "Bimby", 1.0)
    _stub_flybrain(monkeypatch)
    report = reactions.record("\U0001F600", message_text="Bimby")
    assert report["applied"] is False
    assert report["reason"] == "no_reward_for_emoji"
    assert report["supported"], "the caller was not told what is supported"
    assert _row(graph, "node:bimby")["affinity"] == pytest.approx(1.0)


def test_negative_reaction_lowers_the_reply_node(graph, monkeypatch):
    from src.brain import reactions

    _insert_node(graph, "node:bimby", "Bimby", 2.0)
    _stub_flybrain(monkeypatch)
    reactions.record("\U0001F621", message_text="sobre o Bimby")
    assert _row(graph, "node:bimby")["affinity"] == pytest.approx(1.0)


def test_a_no_match_is_queued_for_the_sleep_cycle(graph, monkeypatch):
    """A reply that rewarded no node is queued, not offered as a button.

    The front end used to raise a "Guardar esta resposta como nó do grafo"
    control on the message whenever this happened, so the common case -- a good
    answer the graph has never heard of -- ended in something the owner had to
    notice and press. He asked for the placement to be decided in the sleep
    session instead and never surfaced, so the reply is queued here and
    `_place_unplaced_reactions` (skills/skill_dream.py) decides what it is.

    The refusal itself is unchanged and still correct: the graph is not written
    by a click, and the existing no_match test still holds.
    """
    from src.brain import reactions, unplaced

    _insert_node(graph, "node:capitalismo", "capitalismo tardio", 1.0)
    mg.set_current_topic("node:capitalismo")
    _stub_flybrain(monkeypatch)

    report = reactions.record("\U0001F44D", message_text="uma resposta sobre nada")

    assert report["graph"] == "no_match", f"report: {report}"
    assert report.get("queued") is True, (
        f"the reply was lost instead of queued: {report}"
    )
    assert _row(graph, "node:capitalismo")["affinity"] == pytest.approx(1.0), (
        "queuing must not also write the graph"
    )

    con = sqlite3.connect(graph)
    unplaced.ensure_schema(con)
    rows = unplaced.pending(con)
    con.close()
    assert len(rows) == 1, f"expected exactly one queued reply, got {rows}"
    assert rows[0]["message_text"] == "uma resposta sobre nada"
    assert rows[0]["outcome"] is None, "a fresh row must be unresolved"
