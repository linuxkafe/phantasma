"""Ambient noise floor, used to move the wake word threshold with the room.

Why this exists
---------------
Measured in production on 2026-09-29 at 03:41, inside quiet hours: a hotword
fired at score 0.80 against a flat threshold of 0.70, played the "Sim."
acknowledgement, then transcribed to nothing. The trigger was not the wake
word -- the room was. A constant threshold cannot tell a quiet room from a
noisy one, so every noise burst is scored against the same bar.

The obvious fix, "raise the threshold when the input is loud", is wrong and
actively harmful: the loudest thing in the room is the user saying the wake
word. An instantaneous level would raise the bar precisely at the moment the
person is trying to be heard, and a fixed penalty would make the assistant
deaf exactly when it matters.

So the level is not read from the input, it is read from a floor that only
moves down at the speed of the noise and up slowly:

- below the floor  -> the floor follows immediately (a quiet room is
  recognised at once)
- above the floor  -> the floor creeps up, so a one-second utterance cannot
  lift it, only a sustained hum can

The result is a floor that describes the room rather than the speaker, and a
threshold that only tightens when the room genuinely makes the model less
reliable.

The floor starts at full scale minus a wide margin, i.e. "assume silence".
Until the room has been heard, the base threshold is used unchanged, so a cold
start never silently makes the assistant harder to wake.
"""

import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger("phantasma.noise")

# int16 full scale. dBFS is computed against this.
_FULL_SCALE = 32768.0

# Below this the floor is treated as "digital silence" and the estimate is
# frozen, because averaging a run of zeros would otherwise walk the floor
# towards -inf and make the next real signal look like a noise burst.
_SILENCE_DB = -90.0


def rms_dbfs(samples: np.ndarray) -> float:
    """Root-mean-square level of int16 samples, in dBFS."""
    if samples is None or len(samples) == 0:
        return _SILENCE_DB
    arr = np.asarray(samples, dtype="float64")
    rms = float(np.sqrt(np.mean(arr**2)))
    if rms <= 0.0:
        return _SILENCE_DB
    return 20.0 * math.log10(rms / _FULL_SCALE)


class NoiseFloor:
    """An asymmetric running estimate of the room's noise level.

    ``update`` is called once per audio window. ``db`` is the current floor in
    dBFS, or None while the estimate is still unknown.
    """

    def __init__(
        self,
        quiet_db: float = -60.0,
        loud_db: float = -35.0,
        max_bump: float = 0.20,
        rise_alpha: float = 0.005,
        initial_db: Optional[float] = None,
    ):
        """
        quiet_db: at or below this floor, the threshold is untouched.
        loud_db:  at or above this floor, the full max_bump is applied.
        max_bump: the most the threshold may ever be raised, in score units.
        rise_alpha: how fast the floor may climb. 0.005 over 80 ms windows is
            ~1 dB per 16 s of sustained noise; a wake word is ~1 s and moves it
            by a negligible amount.
        initial_db: seed the floor, e.g. to resume across a restart.
        """
        self.quiet_db = float(quiet_db)
        self.loud_db = float(loud_db)
        self.max_bump = float(max_bump)
        self.rise_alpha = float(rise_alpha)
        self._db: Optional[float] = None if initial_db is None else float(initial_db)

    @property
    def db(self) -> Optional[float]:
        """Current floor in dBFS, or None while still unknown."""
        return self._db

    @property
    def known(self) -> bool:
        return self._db is not None

    def update(self, samples: np.ndarray) -> Optional[float]:
        """Fold one window of audio into the floor. Returns the new floor."""
        level = rms_dbfs(samples)
        if level <= _SILENCE_DB:
            # Digital silence says nothing about the room; letting it through
            # would drag the floor down and inflate the penalty that follows.
            return self._db

        if self._db is None:
            self._db = level
        elif level < self._db:
            # A quieter room is adopted at once, so a door closing or a fan
            # stopping relaxes the threshold immediately.
            self._db = level
        else:
            self._db += self.rise_alpha * (level - self._db)
        return self._db

    def penalty(self) -> float:
        """How much to raise the threshold, given the current floor."""
        if self._db is None or self.max_bump <= 0.0:
            return 0.0
        if self._db <= self.quiet_db:
            return 0.0
        if self._db >= self.loud_db:
            return self.max_bump
        span = self.loud_db - self.quiet_db
        if span <= 0.0:
            return self.max_bump
        return self.max_bump * (self._db - self.quiet_db) / span

    def describe(self) -> str:
        """One line for the log: the floor and what it does to the bar."""
        if self._db is None:
            return "noise floor: unknown (base threshold)"
        return f"noise floor {self._db:.1f} dBFS, threshold +{self.penalty():.3f}"
