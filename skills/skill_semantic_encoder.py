"""Skill: Semantic Encoder — text → topic angle + habituation.

Ported from dfb/sim/semantic_encoder.py. Converts transcribed text into
deterministic topic angles for the E-PG ring attractor, with pre-synaptic
habituation (repetition reduces injection amplitude) and novelty detection.

This is a standalone class (not a Skill subclass) used by the pipeline
to encode user utterances before feeding to FlyBrain.
"""

import hashlib
import re
from collections import deque

import numpy as np

from src.brain.fly_brain import circular_distance

# Constants from dfb (tunable via config in T023)
MIN_INJECTION = 0.05
FAST_DT = 4.0  # seconds: inter-arrival below this counts as "too fast"
HAB_ANGLE_DEG = 30.0  # messages closer than this are "the same topic"

# PT-PT stopwords (extended from dfb's PT-BR/EN biased list)
_STOPWORDS = {
    "a", "o", "e", "de", "do", "da", "em", "no", "na", "com", "para", "por",
    "um", "uma", "que", "and", "the", "of", "to", "you", "is", "in", "it",
    "i", "me", "my", "this", "that",
    # PT-PT additions
    "é", "para", "com", "os", "as", "dos", "das", "pelo", "pela",
    "aquele", "aquela", "isto", "isso", "aquilo", "seu", "sua",
    "meu", "minha", "nos", "nosso", "nossa", "você", "vocês",
    "também", "muito", "mais", "menos", "bem", "mal", "sim", "não",
}


class SemanticEncoder:
    """Maps messages onto deterministic topic angles and injectable currents.

    Uses a hash-based angle encoding for deterministic topic mapping,
    with habituation dynamics that reduce injection amplitude on rapid
    repetition of similar topics, and novelty detection for abrupt changes.

    Attributes:
        _last_angle: Previous topic angle (degrees).
        _last_t: Timestamp of last message (seconds).
        _injection: Current injection amplitude (0.05..1.0).
        _recent: History of recent (angle, timestamp) pairs.
        _rng: Random state for any stochastic operations.

    Example:
        >>> encoder = SemanticEncoder()
        >>> angle, inj, nov = encoder("liga a luz", time.time())
        >>> print(f"Angle: {angle:.1f}°, Injection: {inj:.2f}, Novelty: {nov:.2f}")
    """

    def __init__(self, seed: int = 42):
        """Initialize encoder with deterministic seed.

        Args:
            seed: Random seed for reproducibility. Default 42.
        """
        self._last_angle: float | None = None
        self._last_t: float = 0.0
        self._injection: float = 1.0
        self._recent: deque = deque(maxlen=8)
        self._rng = np.random.RandomState(seed)

    # ------------------------------------------------------------------
    # Angle encoding
    # ------------------------------------------------------------------

    @staticmethod
    def _keywords(text: str) -> list[str]:
        """Extract salient keywords from text, filtering stopwords.

        Args:
            text: Input text (Portuguese or English).

        Returns:
            List of up to 3 most recent salient keywords (non-stopwords).
        """
        tokens = re.findall(r"[a-zA-Z0-9áéíóúâêôãõçü]+", text.lower())
        seen = []
        for tok in tokens:
            if tok in _STOPWORDS or tok in seen:
                continue
            seen.append(tok)
        return seen[-3:]  # keep the three most recent salient keywords

    def angle_for(self, text: str) -> float:
        """Deterministic 0..360 deg angle for a message.

        Uses SHA-256 hash of keywords for consistent topic mapping.
        Same keywords → same angle (deterministic).

        Args:
            text: Input text to encode.

        Returns:
            Angle in degrees [0, 360).
        """
        keys = self._keywords(text) or ["none"]
        digest = hashlib.sha256("|".join(keys).encode("utf-8")).digest()
        return (int.from_bytes(digest[:3], "big") % 360000) / 1000.0

    # ------------------------------------------------------------------
    # Habituation
    # ------------------------------------------------------------------

    def _update_habituation(self, delta_deg: float, dt: float) -> None:
        """Update injection amplitude based on topic similarity and timing.

        If the new topic is close to the previous one (< HAB_ANGLE_DEG)
        and arrived quickly (< FAST_DT), reduce injection (habituation).
        Otherwise, recover injection toward 1.0.

        Args:
            delta_deg: Angular distance from previous topic (degrees).
            dt: Time since last message (seconds).
        """
        if self._last_angle is None:
            self._injection = 1.0
            return
        if abs(delta_deg) < HAB_ANGLE_DEG and dt <= FAST_DT:
            self._injection = max(MIN_INJECTION, self._injection * 0.55)
        else:
            self._injection = min(
                1.0, self._injection + 0.4 + 0.15 * abs(delta_deg) / 180.0
            )

    def novelty(self, delta_deg: float, dt: float) -> float:
        """Compute novelty signal in [0, 1].

        High for abrupt topic changes, low for repetition.
        Suppressed by 0.25x if same topic arrives too fast.

        Args:
            delta_deg: Angular distance from previous topic (degrees).
            dt: Time since last message (seconds).

        Returns:
            Novelty value in [0, 1].
        """
        if self._last_angle is None:
            return 1.0
        novelty = float(np.clip(abs(delta_deg) / 120.0, 0.0, 1.0))
        if abs(delta_deg) < HAB_ANGLE_DEG and dt <= FAST_DT:
            novelty *= 0.25  # repetition suppresses the novelty signal
        return novelty

    # ------------------------------------------------------------------
    # Public step
    # ------------------------------------------------------------------

    def __call__(self, text: str, t: float) -> tuple[float, float, float]:
        """Encode ``text`` -> (angle, injection_amplitude, novelty).

        Main entry point. Updates internal state and returns the
        encoded angle, current injection amplitude, and novelty.

        Args:
            text: User utterance text.
            t: Current timestamp (time.time()).

        Returns:
            Tuple of (angle_deg, injection_amplitude, novelty).
        """
        angle = self.angle_for(text)
        dt = max(t - self._last_t, 0.0) if self._last_t is not None else 0.0
        delta = circular_distance(self._last_angle or angle, angle)
        self._update_habituation(delta, dt)
        nov = self.novelty(delta, dt)
        self._last_angle = angle
        self._last_t = t
        self._recent.append((angle, t))
        return angle, float(self._injection), nov

