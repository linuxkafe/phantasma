"""Skill: Feedback (++/--) — Reward/punishment for FlyBrain DAN plasticity.

Intercepts explicit "++" and "--" user inputs as reward/punishment signals
for the Mushroom Body dopaminergic neurons (DANs). These do NOT reach the LLM
(privacy: feedback is internal to the neuromodulatory system).

Trigger: Exact match on "++" (reward) or "--" (punishment).
Priority: 100 (highest) to intercept before any other skill.
"""

from skills.base import Skill, TriggerType
from src.brain.fly_brain import FlyBrain


class FeedbackSkill(Skill):
    """Handles ++ (reward) and -- (punishment) feedback commands.

    This skill directly manipulates the FlyBrain's Mushroom Body plasticity
    via dopaminergic neuron (DAN) signals. It runs at maximum priority to
    ensure feedback never reaches the LLM.

    Attributes:
        NAME: "feedback" for logging.
        TRIGGERS: ["++", "--"] — exact match only.
        TRIGGER_TYPE: EXACT — no partial matching.
        PRIORITY: 100 — highest, intercepts before all other skills.
    """

    NAME = "feedback"
    TRIGGERS = ["++", "--"]
    TRIGGER_TYPE = TriggerType.EXACT
    PRIORITY = 100  # High priority: intercept before other skills

    def __init__(self, context=None):
        """Initialize with optional pipeline context.

        Args:
            context: SkillContext with fly_brain. If provided, uses the
                shared FlyBrain instance. Otherwise creates a new one
                (for testing/standalone use).
        """
        super().__init__(context)
        if context and context.fly_brain:
            self._fly_brain: FlyBrain = context.fly_brain
        else:
            self._fly_brain = FlyBrain()

    def handle(self, text: str) -> str:
        """Process ++ or -- feedback and update FlyBrain.

        Args:
            text: Either "++" (reward) or "--" (punishment), exact match.

        Returns:
            Confirmation message in Portuguese describing the action taken.
            Empty string if input doesn't match (shouldn't happen due to EXACT trigger).
        """
        text = text.strip()

        if text == "++":
            # Reward: strengthen approach MBONs via DAN+
            self._fly_brain.step(
                topic_angle_deg=self._fly_brain.ring.orientation_deg,
                novelty=0.0,
                reward=+1.0,
            )
            return "reforço registado (DAN+)"

        elif text == "--":
            # Punishment: strengthen avoidance MBONs via DAN-
            self._fly_brain.step(
                topic_angle_deg=self._fly_brain.ring.orientation_deg,
                novelty=0.0,
                reward=-1.0,
            )
            return "punição registada (DAN-)"

        return ""
