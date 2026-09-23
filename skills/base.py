"""Base Skill class and trigger types for pHantasma skills.

This module defines the core abstractions for the skills system:
- TriggerType: Enum for trigger matching strategies
- SkillContext: Container for pipeline service references
- Skill: Abstract base class that all skills must inherit from

Skills are dynamically loaded Python modules in skills/skill_*.py that
define TRIGGERS and implement a handle() method.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class TriggerType(Enum):
    """How the trigger text is matched against user input.

    Attributes:
        STARTSWITH: Match if input starts with any trigger string.
        CONTAINS: Match if input contains any trigger string.
        EXACT: Match if input exactly equals a trigger string.
        REGEX: Match if input matches any trigger regex pattern.
    """

    STARTSWITH = "startswith"
    CONTAINS = "contains"
    EXACT = "exact"
    REGEX = "regex"


@dataclass
class SkillContext:
    """Context passed to skills for accessing pipeline services.

    Attributes:
        tts: TTS service instance (PiperTTS or compatible).
        llm: LLM service instance (OllamaLLM or compatible).
        config: Configuration object (Config instance).
        fly_brain: FlyBrain instance for neuromodulatory state.
    """

    tts: Any = None
    llm: Any = None
    config: Any = None
    fly_brain: Any = None


class Skill(ABC):
    """Base class for all pHantasma skills.

    Each skill defines a set of TRIGGERS and implements handle() to process
    matched input. Skills are loaded dynamically by SkillLoader at startup.

    Class Attributes:
        TRIGGERS: List of trigger strings/patterns. Required.
        TRIGGER_TYPE: How to match triggers (default: EXACT).
        PRIORITY: Higher = checked first (default: 0). Use for disambiguation.
        NAME: Human-readable skill name for logging (default: "base").

    Instance Attributes:
        context: SkillContext with pipeline service references.
    """

    TRIGGERS: list[str] = []
    TRIGGER_TYPE: TriggerType = TriggerType.EXACT
    PRIORITY: int = 0
    NAME: str = "base"

    def __init__(self, context: Optional[SkillContext] = None):
        """Initialize skill with optional pipeline context.

        Args:
            context: SkillContext providing access to TTS, LLM, config, FlyBrain.
                If None, creates empty context (skill cannot access services).
        """
        self.context = context or SkillContext()

    @abstractmethod
    def handle(self, text: str) -> str:
        """Handle a triggered input and return response text.

        This is the main entry point when a skill's trigger matches.
        The skill should process the input and return text to be spoken.

        Args:
            text: The user input that matched this skill's trigger.
                Exact match includes the trigger; STARTSWITH/CONTAINS
                includes the full user utterance.

        Returns:
            Response text to be spoken via TTS. Empty string = silent success.
        """
        pass

    def matches(self, text: str) -> bool:
        """Check if input matches this skill's triggers.

        Uses the skill's TRIGGER_TYPE to determine matching strategy.

        Args:
            text: User input text to check against triggers.

        Returns:
            True if any trigger matches according to TRIGGER_TYPE.
        """
        if self.TRIGGER_TYPE == TriggerType.EXACT:
            return text.strip() in self.TRIGGERS
        elif self.TRIGGER_TYPE == TriggerType.STARTSWITH:
            return any(text.strip().startswith(t) for t in self.TRIGGERS)
        elif self.TRIGGER_TYPE == TriggerType.CONTAINS:
            return any(t in text.strip() for t in self.TRIGGERS)
        elif self.TRIGGER_TYPE == TriggerType.REGEX:
            import re

            return any(re.search(t, text.strip()) for t in self.TRIGGERS)
        return False
