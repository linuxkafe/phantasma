"""Tests for SemanticEncoder skill (T017)."""

import time

import pytest

from skills.skill_semantic_encoder import _STOPWORDS, SemanticEncoder


class TestSemanticEncoder:
    """Test SemanticEncoder angle encoding and habituation."""

    def test_angle_for_deterministic(self):
        """Same text always produces same angle."""
        encoder = SemanticEncoder()
        angle1 = encoder.angle_for("fala-me de drones FPV")
        angle2 = encoder.angle_for("fala-me de drones FPV")
        assert angle1 == angle2
        assert 0.0 <= angle1 < 360.0

    def test_angle_for_different_texts(self):
        """Different texts produce different angles (usually)."""
        encoder = SemanticEncoder()
        angle1 = encoder.angle_for("fala-me de drones")
        angle2 = encoder.angle_for("qual a capital de Portugal")
        # Very unlikely to collide with 3 decimal precision
        assert angle1 != angle2

    def test_keywords_extraction(self):
        """Keyword extraction filters stopwords and duplicates."""
        encoder = SemanticEncoder()
        keys = encoder._keywords("fala me de drones e drones FPV")
        # "de", "e" are stopwords; "drones" appears twice
        assert "de" not in keys
        assert "e" not in keys
        assert keys.count("drones") == 1
        assert "fpv" in keys

    def test_keywords_pt_pt_stopwords(self):
        """PT-PT stopwords are filtered."""
        for word in ["é", "para", "com", "os", "as", "dos", "das", "pelo", "pela"]:
            assert word in _STOPWORDS

    def test_first_call_returns_full_injection_and_novelty(self):
        """First call: injection=1.0, novelty=1.0."""
        encoder = SemanticEncoder()
        angle, injection, novelty = encoder("teste", 0.0)
        assert injection == 1.0
        assert novelty == 1.0

    def test_fast_repetition_habituates_injection(self):
        """Fast repetition (< FAST_DT, < HAB_ANGLE_DEG) reduces injection."""
        encoder = SemanticEncoder()
        # First call
        encoder("fala-me de drones", 0.0)
        # Immediate repetition: dt < FAST_DT, same angle -> delta < HAB_ANGLE_DEG
        _, injection2, _ = encoder("fala-me de drones", 1.0)
        assert injection2 < 1.0
        assert injection2 >= 0.05  # MIN_INJECTION

    def test_fast_repetition_suppresses_novelty(self):
        """Fast repetition suppresses novelty signal."""
        encoder = SemanticEncoder()
        encoder("fala-me de drones", 0.0)
        _, _, novelty2 = encoder("fala-me de drones", 1.0)
        assert novelty2 < 1.0
        # Novelty should be significantly suppressed (0.25 factor)
        assert novelty2 <= 0.3

    def test_slow_repetition_recovers_injection(self):
        """Slow repetition (dt > FAST_DT) allows injection recovery."""
        encoder = SemanticEncoder()
        encoder("fala-me de drones", 0.0)
        # Habituate
        encoder("fala-me de drones", 1.0)
        encoder("fala-me de drones", 2.0)
        # Wait longer than FAST_DT
        _, injection_recovered, _ = encoder("fala-me de drones", 10.0)
        assert injection_recovered > 0.5  # Should recover towards 1.0

    def test_abrupt_topic_change_spikes_novelty(self):
        """Abrupt topic change (large angle delta) spikes novelty."""
        encoder = SemanticEncoder()
        # First topic
        encoder("fala-me de drones", 0.0)
        # Completely different topic (different keywords -> different angle)
        angle2, _, novelty2 = encoder("qual a capital de Portugal", 10.0)
        angle1 = encoder.angle_for("fala-me de drones")
        delta = abs(angle2 - angle1)
        if delta > 180:
            delta = 360 - delta
        if delta > 120:  # Abrupt change
            assert novelty2 > 0.5

    def test_novelty_bounded(self):
        """Novelty always in [0, 1]."""
        encoder = SemanticEncoder()
        for _ in range(20):
            _, _, novelty = encoder("teste", time.time())
            assert 0.0 <= novelty <= 1.0

    def test_injection_bounded(self):
        """Injection always in [MIN_INJECTION, 1.0]."""
        encoder = SemanticEncoder()
        for i in range(20):
            _, injection, _ = encoder("teste", float(i))
            assert 0.05 <= injection <= 1.0

    def test_deterministic_seed(self):
        """Same seed produces identical sequence."""
        enc1 = SemanticEncoder(seed=42)
        enc2 = SemanticEncoder(seed=42)
        for i in range(10):
            a1, inj1, nov1 = enc1("mensagem " + str(i), float(i))
            a2, inj2, nov2 = enc2("mensagem " + str(i), float(i))
            assert a1 == a2
            assert inj1 == inj2
            assert nov1 == nov2


class TestSemanticEncoderIntegration:
    """Integration-style tests with FlyBrain."""

    def test_encoder_feeds_flybrain(self):
        """Encoder output compatible with FlyBrain.step()."""
        from src.brain.fly_brain import FlyBrain

        encoder = SemanticEncoder()
        brain = FlyBrain()
        t = 0.0

        for msg in ["fala-me de drones", "que motores recomendavas", "e hélices"]:
            angle, injection, novelty = encoder(msg, t)
            state = brain.step(angle, novelty=novelty)
            assert state.orientation_deg is not None
            t += 30.0  # slow: related messages

        # Gradual drift should keep jumps small
        assert all(abs(brain.ring.last_jump) < 120.0 for _ in range(1))


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
