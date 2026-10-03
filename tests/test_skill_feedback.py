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


def test_skill_matching_is_case_insensitive():
    """Triggers match regardless of user capitalization (Discord/UI text).

    Legacy CONTAINS triggers e.g. "o que ouves" must match "O que ouves?".
    """
    from skills.base import SkillContext
    from skills.loader import SkillLoader

    skills_dir = str(Path(__file__).parent.parent / "skills")
    loader = SkillLoader(skills_dir, SkillContext())
    skills = loader.load_all()

    wyth = next((s for s in skills if s.NAME == "skill_what_you_hear"), None)
    assert wyth is not None
    assert wyth.matches("O que ouves?") is True
    assert wyth.matches("O que escutas?") is True
    assert wyth.matches("O QUE ESTÁS A OUVIR") is True

    weather = next((s for s in skills if s.NAME == "skill_weather"), None)
    assert weather is not None
    assert weather.matches("Como está o tempo?") is True
    assert weather.matches("COMO ESTÁ O TEMPO?") is True


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

    Built on stubs rather than on a real collision between two skills. The
    previous version used "como está o tempo", which matched `skill_tuya` via the
    bare "como está" trigger and then answered through `skill_weather`. That
    coupling to a real trigger collision is what hid the guest-tier refusal:
    tuya on a weather question is "Não tens acesso a tuya." for a guest, so the
    trigger is now narrowed (see skill_tuya._get_tuya_triggers) and this test no
    longer had a phrase that worked.

    What is under test is the mechanism, not the phrase: a skill that matches and
    returns nothing must not end the turn, and the next matching skill must
    answer.
    """
    from skills.base import Skill, SkillContext
    from skills.loader import SkillLoader

    class _Silent(Skill):
        NAME = "test_silent"
        PRIORITY = 30

        def __init__(self, context):
            super().__init__(context)
            self.triggered: list[str] = []

        def matches(self, text: str) -> bool:
            return True

        def handle(self, text: str) -> str:
            self.triggered.append(text)
            return ""

    class _Answers(Skill):
        NAME = "test_answers"
        PRIORITY = 10

        def __init__(self, context):
            super().__init__(context)

        def matches(self, text: str) -> bool:
            return True

        def handle(self, text: str) -> str:
            return "resposta do segundo"

    loader = SkillLoader(str(Path(__file__).parent.parent / "skills"),
                         SkillContext())
    loader.skills = [_Silent(loader.context), _Answers(loader.context)]

    response = loader.execute_skill("qualquer coisa")

    assert loader.skills[0].triggered == ["qualquer coisa"], (
        "a primeira skill tem de ter sido chamada; se nao foi, o teste passou "
        "sem exercitar o fall-through"
    )
    assert response == "resposta do segundo", (
        f"o handler vazio devia dar lugar ao seguinte, veio {response!r}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
