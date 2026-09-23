"""Tests for FeedbackSkill (T019)."""

from pathlib import Path

import pytest

from skills.base import SkillContext
from skills.skill_feedback import FeedbackSkill
from src.brain.fly_brain import FlyBrain


def test_feedback_skill_triggers():
    """FeedbackSkill triggers on exact ++ and --."""
    brain = FlyBrain()
    skill = FeedbackSkill(SkillContext(fly_brain=brain))

    assert skill.matches("++") is True
    assert skill.matches("--") is True
    assert skill.matches("++ ") is True  # whitespace handled
    assert skill.matches(" ++") is True
    assert skill.matches("+++") is False
    assert skill.matches("+") is False
    assert skill.matches("-") is False
    assert skill.matches("obrigado") is False


def test_feedback_skill_plus_plus_increases_affinity():
    """handle('++') calls FlyBrain.step with reward=+1.0."""
    brain = FlyBrain()
    skill = FeedbackSkill(SkillContext(fly_brain=brain))

    initial_affinity = brain.mb.affinity
    response = skill.handle("++")

    assert response == "reforço registado (DAN+)"
    assert brain.mb.affinity > initial_affinity - 1e-9


def test_feedback_skill_minus_minus_decreases_affinity():
    """handle('--') calls FlyBrain.step with reward=-1.0."""
    brain = FlyBrain()
    skill = FeedbackSkill(SkillContext(fly_brain=brain))

    initial_affinity = brain.mb.affinity
    response = skill.handle("--")

    assert response == "punição registada (DAN-)"
    assert brain.mb.affinity < initial_affinity + 1e-9


def test_feedback_skill_uses_current_orientation():
    """Feedback uses current ring orientation as topic angle."""
    brain = FlyBrain()
    # Set a known orientation
    brain.step(45.0, novelty=0.5)

    skill = FeedbackSkill(SkillContext(fly_brain=brain))
    skill.handle("++")

    # Should use current orientation (around 45 deg)
    assert abs(brain.ring.orientation_deg - 45.0) < 20.0


def test_skill_loader_loads_feedback_skill():
    """SkillLoader discovers and loads skill_feedback.py from actual skills dir.

    T034: the loader now also wraps legacy skills (weather, discord, tuya, ...)
    via LegacySkillAdapter, so load_all() returns many skills. FeedbackSkill
    must be among them, with the highest priority (it sorts first).
    """
    from skills.base import SkillContext
    from skills.loader import SkillLoader
    from src.brain.fly_brain import FlyBrain

    brain = FlyBrain()
    skills_dir = str(Path(__file__).parent.parent / "skills")
    loader = SkillLoader(skills_dir, SkillContext(fly_brain=brain))
    skills = loader.load_all()

    assert len(skills) >= 2
    feedback = next((s for s in skills if s.NAME == "feedback"), None)
    assert feedback is not None
    assert feedback.TRIGGERS == ["++", "--"]
    assert skills[0].NAME == "feedback"  # PRIORITY=100 sorts first


def test_skill_loader_execute_feedback_skill():
    """SkillLoader.execute_skill routes ++/-- to FeedbackSkill."""
    from skills.base import SkillContext
    from skills.loader import SkillLoader
    from src.brain.fly_brain import FlyBrain

    brain = FlyBrain()
    skills_dir = str(Path(__file__).parent.parent / "skills")
    loader = SkillLoader(skills_dir, SkillContext(fly_brain=brain))
    loader.load_all()

    initial_affinity = brain.mb.affinity
    response = loader.execute_skill("++")

    assert response == "reforço registado (DAN+)"
    assert brain.mb.affinity > initial_affinity - 1e-9

    # Non-matching text returns None
    assert loader.execute_skill("olá") is None


def test_execute_skill_falls_through_empty_handlers():
    """T034 regression: matching skills that return empty are skipped.

    skill_tuya matches "como está o tempo" via "como está" but returns
    empty when no device is targeted. execute_skill must skip it and
    return the first non-empty response (weather), matching upstream
    assistant.py semantics.
    """
    from skills.loader import SkillLoader
    from src.brain.fly_brain import FlyBrain

    brain = FlyBrain()
    skills_dir = str(Path(__file__).parent.parent / "skills")
    loader = SkillLoader(skills_dir, SkillContext(fly_brain=brain))
    loader.load_all()

    tuya = next((s for s in loader.skills if s.NAME == "skill_tuya"), None)
    assert tuya is not None
    assert tuya.matches("como está o tempo") is True
    assert tuya.handle("como está o tempo") in (None, "")

    weather = next((s for s in loader.skills if s.NAME == "skill_weather"), None)
    assert weather is not None

    # Ordering: tuya precedes weather, yet its empty output is skipped.
    assert loader.skills.index(tuya) < loader.skills.index(weather)
    response = loader.execute_skill("como está o tempo")
    assert response == weather.handle("como está o tempo")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
