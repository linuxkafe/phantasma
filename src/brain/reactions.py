"""FlyBrain reaction feedback, shared by every surface.

Why this module exists
----------------------
The reaction-to-reward mapping and the FlyBrain update already worked, but only
for Discord: ``skills/skill_discord.py`` held the map, the ``_fly_brain.step()``
call and the memory-graph bridge inline in an async event handler. There was no
way to react from the web chat, and no second implementation would have been
correct either -- two copies of a reward path is two places for them to diverge,
and the FlyBrain state is shared, so a divergence is a state corruption.

So the logic moved here and Discord calls it. One implementation, both surfaces.

The reward map
--------------
Unchanged from the Discord original:

    👍 +1.0   👎 -1.0   ❤️ +1.0   🔥 +1.0   😡 -1.0   😢 -0.5

The negative ones are as important as the positive ones. A brain trained only
on approval never learns to avoid the thing that was rejected, which is the
whole point of a reward signal rather than a like counter.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

import config

logger = logging.getLogger("phantasma.reaction")

REACTION_REWARD = {
    "\U0001f44d": +1.0,  # thumbs up
    "\U0001f44e": -1.0,  # thumbs down
    "❤️": +1.0,  # heart
    "\U0001f525": +1.0,  # fire
    "\U0001f621": -1.0,  # rage
    "\U0001f622": -0.5,  # cry
}

# The set the UI should offer. Derived from the map so the two cannot diverge.
SUPPORTED_EMOJI = tuple(REACTION_REWARD.keys())


def reload_reaction_weights() -> None:
    """Re-read the owner-set weights from the store.

    The map was a module constant, so the weights the owner set in /admin
    would never reach the assistant until the next deploy -- which is the same
    "a setting that is not a setting" bug the persona had.
    """
    global REACTION_REWARD, SUPPORTED_EMOJI
    try:
        from src.settings_store import get_reaction_weights

        REACTION_REWARD = get_reaction_weights()
        SUPPORTED_EMOJI = tuple(REACTION_REWARD.keys())
    except Exception as e:  # pragma: no cover - never break a reaction
        logger.warning(f"Could not load reaction weights: {e}")


def reward_for(emoji: str) -> Optional[float]:
    """The FlyBrain reward for an emoji, or None if it carries no signal.

    An unmapped emoji is None, not zero: "express no opinion" and "express a
    neutral opinion" must not be the same event, or every unrelated emoji on a
    message would be a training signal.
    """
    if not emoji:
        return None
    return REACTION_REWARD.get(str(emoji).strip())


def record(
    emoji: str,
    source: str = "web",
    actor: str = "local",
    message_text: str | None = None,
) -> dict:
    """Apply a reaction's reward to the FlyBrain and the memory graph.

    Returns a report -- never raises for a missing brain, because a reaction
    arriving before the pipeline is up is normal, not exceptional. The caller
    needs to know what happened, so the outcome is data rather than an absence.
    """
    reward = reward_for(emoji)
    if reward is None:
        return {
            "applied": False,
            "reason": "no_reward_for_emoji",
            "emoji": emoji,
            "supported": list(SUPPORTED_EMOJI),
        }

    report = {
        "applied": False,
        "emoji": emoji,
        "reward": reward,
        "source": source,
        "actor": actor,
    }

    # --- FlyBrain -----------------------------------------------------------
    try:
        brain = _resolve_brain()
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        report["reason"] = f"flybrain_unavailable: {type(exc).__name__}"
        logger.warning("Reaction reward could not reach the FlyBrain: %s", exc)
        return report

    if brain is None:
        report["reason"] = "flybrain_unavailable"
        return report

    try:
        brain.step(
            topic_angle_deg=brain.ring.orientation_deg,
            novelty=0.1,
            reward=reward,
        )
        report["flybrain"] = "stepped"
    except Exception as exc:  # noqa: BLE001
        report["flybrain"] = f"error: {type(exc).__name__}: {exc}"
        logger.warning("FlyBrain step failed for a reaction: %s", exc)
        return report

    # --- memory graph -------------------------------------------------------
    # A reaction judges ONE reply, so the node it strengthens is resolved from
    # that reply's text. It used to use `get_current_topic()` -- the ambient
    # topic -- which credited every reaction to whatever happened to be current.
    # Measured: a thumbs-up on an answer about mortality reported
    # `node:capitalismo tardio`, silently and looking successful.
    #
    # With no match the graph is NOT written, and the report says so. An honest
    # `no_match` beats a confident wrong node, and the FlyBrain step above has
    # already happened either way, so the gesture always has an effect.
    try:
        from src.brain.memory_graph import (
            apply_reward,
            find_node_for_text,
            get_current_topic,
            init_db,
        )

        init_db()
        target = find_node_for_text(message_text) if message_text else None
        if target:
            key = apply_reward(reward, topic_key=target["node_key"])
            report["topic_key"] = key or target["node_key"]
            report["graph"] = "rewarded" if key else "no_topic"
            report["graph_node"] = target["label"]
        elif message_text:
            # Text was supplied and named nothing: refuse rather than guess.
            report["graph"] = "no_match"
            report["topic_key"] = None
            # ...but do not lose it. The reply is queued for the sleep cycle,
            # which decides where it belongs with the rest of the graph open.
            # The front end used to offer a button here and expect the owner to
            # press it; the placement is the brain's housekeeping, not theirs,
            # and it is a better judgement made there than from a chat bubble.
            try:
                from src.brain import unplaced

                qconn = sqlite3.connect(config.DB_PATH)
                try:
                    unplaced.ensure_schema(qconn)
                    qid = unplaced.enqueue(
                        qconn, message_text, emoji=emoji, reward=reward,
                        source=source, actor=actor,
                    )
                    qconn.commit()
                finally:
                    qconn.close()
                report["queued"] = bool(qid)
            except Exception as exc:  # noqa: BLE001 - a queue must never cost the gesture
                report["queued"] = False
                logger.warning(
                    "Could not queue an unplaced reaction: %s: %s",
                    type(exc).__name__, exc,
                )
        else:
            # No text at all (Discord, or an older client): fall back to the
            # topic, and label the report so it can never be mistaken for a
            # match on content.
            current = get_current_topic()
            if current:
                key = apply_reward(reward)
                report["topic_key"] = key
                report["graph"] = "topic_fallback"
            else:
                report["graph"] = "no_current_topic"
    except Exception as exc:  # noqa: BLE001
        # The brain already stepped; losing the graph edge must not undo that.
        report["graph"] = f"error: {type(exc).__name__}: {exc}"
        logger.warning("Memory-graph reward failed: %s", exc)

    report["applied"] = report.get("flybrain") == "stepped"
    return report


def _resolve_brain():
    """The one FlyBrain instance, whoever is asking.

    There was a latent bug here: skill_discord built its OWN FlyBrain while the
    assistant held another. Two instances over one SQLite store means a reaction
    arriving over Discord stepped a brain whose state the assistant never sees,
    and vice versa -- each half writing rows the other had not read. The
    assistant's pipeline is registered once at construction and reused; the
    Discord skill now resolves through here too, so a reaction from either
    surface lands on the same brain.
    """
    from src.brain.fly_brain import get_shared_fly_brain

    return get_shared_fly_brain()


def describe() -> dict:
    """For the /admin/config page: the map, in a form a UI can render."""
    return {
        "emojis": [
            {
                "emoji": e,
                "reward": REACTION_REWARD[e],
                "polarity": "positive" if REACTION_REWARD[e] > 0 else "negative",
            }
            for e in SUPPORTED_EMOJI
        ],
    }
