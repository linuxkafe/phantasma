"""Dynamic skill loader for pHantasma.

Discovers, loads, and manages skill modules from the skills/ directory.
Each skill_*.py file is imported and either:
  - its Skill subclass is instantiated with the shared pipeline context, or
  - the module is wrapped in a LegacySkillAdapter when it exposes the legacy
    interface (module-level TRIGGERS/TRIGGER_TYPE/handle(lower, full)).

The adapter lets the DSL-era skills (weather, discord, tuya, ...) keep their
upstream form while the new SkillLoader drives them, so no 20-module migration
is required to unblock weather/discord (T034 RC3).

The loader handles:
- Dynamic module import with error isolation
- Priority-based skill ordering
- Trigger matching and execution
- Graceful degradation on skill failures
"""

import importlib
import logging
import sys
from pathlib import Path
from typing import Any, List, Optional

from skills.base import Skill, SkillContext, TriggerType

logger = logging.getLogger(__name__)

# Legacy module-level TRIGGER_TYPE strings -> TriggerType enum.
_LEGACY_TRIGGER_MAP = {
    "startswith": TriggerType.STARTSWITH,
    "contains": TriggerType.CONTAINS,
    "exact": TriggerType.EXACT,
    "regex": TriggerType.REGEX,
}


class LegacySkillAdapter(Skill):
    """Wrap a legacy skill module into the Skill interface.

    Legacy skills (as shipped upstream in linuxkafe/phantasma) declare
    module-level ``TRIGGERS`` / ``TRIGGER_TYPE`` (string) and a handler
    ``handle(user_prompt_lower, user_prompt_full)``. Some also expose
    ``init_skill_daemon()`` for background daemons (discord, weather, tuya...).

    This adapter adapts that contract to the ``Skill`` ABC without touching the
    skill source, so remote provenance stays intact and diffing stays trivial.
    """

    def __init__(
        self,
        module: Any,
        context: Optional[SkillContext] = None,
        trig_type: str = "contains",
        priority: int = 0,
    ):
        self._module = module
        self._name = getattr(module, "__name__", "legacy").split(".")[-1]
        # Instance attrs shadow the Skill class defaults; keeps matches() and
        # sorting working without property/attribute conflicts.
        self.NAME = self._name
        self.PRIORITY = priority
        self.TRIGGERS = list(getattr(module, "TRIGGERS", []))
        self.TRIGGER_TYPE = _LEGACY_TRIGGER_MAP.get(
            getattr(module, "TRIGGER_TYPE", trig_type), TriggerType.CONTAINS
        )
        super().__init__(context)

    def matches(self, text: str) -> bool:
        triggers = self.TRIGGERS
        if not triggers:
            return False
        t = self.TRIGGER_TYPE
        stripped = text.strip().lower()
        triggers_lower = [x.lower() for x in triggers]
        if t == TriggerType.EXACT:
            return stripped in triggers_lower
        if t == TriggerType.STARTSWITH:
            return any(stripped.startswith(x) for x in triggers_lower)
        if t == TriggerType.CONTAINS:
            # Word boundary on both sides. Plain substring matching let the
            # tapo skill's "ver" trigger fire on "verde": asked "O que e o
            # Capuchinho Verde?" the assistant answered "Vigia inacessivel."
            # because it thought it had been asked to look through a camera.
            # Substring matching on short triggers steals unrelated questions.
            import re as _re

            for x in triggers_lower:
                if not x:
                    continue
                if _re.search(r"\b" + _re.escape(x) + r"\b", stripped):
                    return True
            return False
        if t == TriggerType.REGEX:
            import re

            return any(re.search(x, stripped) for x in triggers)
        return False

    def handle(self, text: str) -> str:
        """Call the legacy handle(lower, full) contract."""
        return self._module.handle(text.lower(), text)

    def startup(self) -> None:
        """Start the module daemon if it declares init_skill_daemon()."""
        init_daemon = getattr(self._module, "init_skill_daemon", None)
        if init_daemon:
            try:
                init_daemon()
                logger.info(f"Started daemon for legacy skill: {self._name}")
            except Exception as e:
                logger.error(f"Daemon start failed for {self._name}: {e}")


class SkillLoader:
    """Discovers, loads, and manages skills.

    Scans the skills directory for skill_*.py files, imports each module,
    finds the Skill subclass (or wraps the legacy interface), and
    instantiates it with the shared context.

    Attributes:
        skills_dir: Path to skills directory.
        context: SkillContext shared across all skills.
        skills: List of loaded Skill instances (sorted by priority).
        _loaded_modules: Cache of imported module objects.
        project_root: Project root directory (parent of skills_dir).
    """

    def __init__(self, skills_dir: str, context: Optional[SkillContext] = None):
        """Initialize loader with skills directory and optional context.

        Args:
            skills_dir: Path to skills directory (containing skill_*.py files).
            context: Pipeline context to pass to skills (TTS, LLM, FlyBrain, config).
        """
        self.skills_dir = Path(skills_dir)
        self.context = context or SkillContext()
        # Skills ask "which skill is this?" without knowing about the loader.
        self.context.resolve_skill = self.resolve_skill_name
        self.skills: List[Skill] = []
        self._loaded_modules: dict[str, Any] = {}
        # Project root is parent of skills_dir (for imports like src.brain)
        self.project_root = self.skills_dir.parent

    def load_all(self) -> List[Skill]:
        """Discover and load all skill_*.py files in skills_dir.

        Clears any previously loaded skills. Adds project root to sys.path
        to allow skills to import from src.brain, skills.base, etc.

        Returns:
            List of loaded Skill instances, sorted by priority (highest first).
        """
        self.skills = []
        self._loaded_modules = {}

        if not self.skills_dir.exists():
            logger.warning(f"Skills directory not found: {self.skills_dir}")
            return self.skills

        # Add project root to sys.path for imports (so skills.base, src.brain work)
        if str(self.project_root) not in sys.path:
            sys.path.insert(0, str(self.project_root))

        for skill_file in sorted(self.skills_dir.glob("skill_*.py")):
            module_name = f"skills.{skill_file.stem}"
            try:
                skill = self._load_skill(module_name)
                if skill:
                    self.skills.append(skill)
                    logger.info(
                        f"Loaded skill: {skill.NAME} (priority={skill.PRIORITY})"
                    )
            except Exception as e:
                logger.error(f"Failed to load skill {module_name}: {e}")

        # Sort by priority (highest first)
        self.skills.sort(key=lambda s: s.PRIORITY, reverse=True)
        return self.skills

    def start_daemons(self) -> None:
        """Invoke init_skill_daemon() on every loaded legacy skill.

        The legacy daemon skills (discord, weather, system_stats, tuya, ...)
        need their module-level daemons started once at boot; upstream did this
        inline in assistant.load_skills().
        """
        for skill in self.skills:
            if isinstance(skill, LegacySkillAdapter):
                skill.startup()

    def _load_skill(self, module_name: str) -> Optional[Skill]:
        """Load a single skill module.

        Prefers a Skill subclass; falls back to the LegacySkillAdapter when the
        module declares legacy TRIGGERS + handle(). Returns None when the module
        has neither (e.g. skills.skill_semantic_encoder) or fails to import.

        Args:
            module_name: Full module name (e.g., "skills.skill_feedback").

        Returns:
            Skill instance if successful, None otherwise. Errors are logged but
            don't crash the loader.
        """
        if module_name in self._loaded_modules:
            return None

        try:
            module = importlib.import_module(module_name)
            self._loaded_modules[module_name] = module

            # Find Skill subclass in module (skip base Skill class)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Skill)
                    and attr is not Skill
                ):
                    return attr(self.context)

            # Legacy interface: module-level TRIGGERS + handle(lower, full)
            if hasattr(module, "handle") and hasattr(module, "TRIGGERS"):
                trig_type = getattr(
                    module,
                    "TRIGGER_TYPE",
                    "contains",
                )
                return LegacySkillAdapter(
                    module,
                    context=self.context,
                    trig_type=trig_type,
                )

            logger.warning(
                f"No Skill subclass or legacy interface found in {module_name}"
            )
            return None

        except Exception as e:
            logger.error(f"Error importing {module_name}: {e}")
            return None

    def resolve_matching_skills(self, text: str) -> List[str]:
        """Every skill whose ``matches`` accepts ``text``, in execution order.

        All of them, not the first. ``execute_skill`` walks the whole list and
        takes the first one that returns something non-empty, so the skill that
        ends up answering a request is only knowable in advance as "the first
        one that matches and does not decline". Naming just the first match and
        authorising on that is fail-open: "acende a luz da sala" matches
        ``skill_chacon``, ``skill_tuya`` and ``skill_xiaomi``, so refusing a
        guest on the first alone would hand them straight to the second.

        Same predicates and same order as ``find_matching_skill``, so what a
        caller is told may happen is what happens.
        """
        return [s.NAME for s in self.skills if s.matches(text)]

    def resolve_skill_name(self, text: str) -> Optional[str]:
        """The first skill that would handle ``text``, or ``None``.

        Convenience for callers that only want a label. Anything making an
        authorisation decision wants :meth:`resolve_matching_skills` instead.
        """
        matches = self.resolve_matching_skills(text)
        return matches[0] if matches else None

    def find_matching_skill(self, text: str) -> Optional[Skill]:
        """Find the first skill that matches the input text.

        Skills are checked in priority order (highest first), so higher-priority
        skills can intercept triggers before lower-priority ones.

        Args:
            text: User input text.

        Returns:
            Matching Skill instance, or None if no match.
        """
        for skill in self.skills:
            if skill.matches(text):
                return skill
        return None

    def execute_skill(self, text: str) -> Optional[str]:
        """Find and execute matching skill.

        Iterates skills in priority order and returns the FIRST non-empty
        response. Matches upstream assistant.py semantics: a skill may
        match a trigger but return an empty result (e.g. tuya's "como
        está" catches "como está o tempo" but has no device) — those are
        skipped so the next matching skill (e.g. weather) is consulted.
        If a matched skill raises, logs and continues (does not crash).

        Args:
            text: User input text.

        Returns:
            First non-empty skill response, or None if nothing matched.
        """
        for skill in self.skills:
            if not skill.matches(text):
                continue
            try:
                response = skill.handle(text)
            except Exception as e:
                logger.error(f"Skill {skill.NAME} failed: {e}")
                continue
            if response:
                return response
        return None
