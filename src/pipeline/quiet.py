"""Night mode: when the assistant answers without waiting to be spoken to.

The configuration existed since the start -- QUIET_START/QUIET_END in config.py,
documented as "quiet hours (night mode)" -- and nothing ever read it. No code
consulted quiet_hours, so the feature was declared and inert. This module is the
first consumer.

The window is 23:00 to 07:00 by default and crosses midnight, so the usual
`start <= now < end` comparison is wrong for every hour after midnight. The
overnight case is handled explicitly instead of being approximated.

Days are configurable per weekday because a night that starts at 23:00 on
Friday should not be assumed to end at 07:00 on Saturday morning: the owner
can decide which nights are quiet. A day with no entry is quiet, so adding a
new day never silently opens a hole in the schedule.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime
from typing import Optional

logger = logging.getLogger("phantasma.quiet")

# Monday=0 .. Sunday=6, matching datetime.weekday().
DAY_NAMES = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

DEFAULT_START = 23
DEFAULT_END = 7


def _clamp_hour(value, fallback: int) -> int:
    """Coerce to a valid 0-23 hour. Admin input is a string from a form."""
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return fallback
    if 0 <= hour <= 23:
        return hour
    logger.warning("Quiet hour %r out of range 0-23, using %d", value, fallback)
    return fallback


@dataclass
class QuietWindow:
    """One overnight window, as hours. start==end means a 24h window."""
    start: int = DEFAULT_START
    end: int = DEFAULT_END

    @classmethod
    def parse(cls, raw) -> "QuietWindow":
        if isinstance(raw, QuietWindow):
            return raw
        if isinstance(raw, dict):
            return cls(
                _clamp_hour(raw.get("start"), DEFAULT_START),
                _clamp_hour(raw.get("end"), DEFAULT_END),
            )
        return cls(DEFAULT_START, DEFAULT_END)

    def contains(self, moment: dtime) -> bool:
        if self.start == self.end:
            return True
        if self.start < self.end:
            return dtime(self.start, 0) <= moment < dtime(self.end, 0)
        # Overnight: 23 -> 07 wraps past midnight.
        return moment >= dtime(self.start, 0) or moment < dtime(self.end, 0)

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end}


@dataclass
class QuietSchedule:
    """A window per weekday. Absent days fall back to the default window."""

    days: dict = field(default_factory=dict)
    default: QuietWindow = field(default_factory=QuietWindow)

    @classmethod
    def parse(cls, raw) -> "QuietSchedule":
        """Accept the admin form's JSON or a plain {"start","end"} dict."""
        if isinstance(raw, QuietSchedule):
            return raw
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                logger.warning("Quiet schedule is not valid JSON, using default")
                return cls()
        if not isinstance(raw, dict):
            return cls()

        default = QuietWindow.parse(raw.get("default") or raw)
        days = {}
        for key in DAY_KEYS:
            if key in raw:
                days[key] = QuietWindow.parse(raw[key])
        return cls(days=days, default=default)

    def window_for(self, weekday: int) -> QuietWindow:
        if 0 <= weekday < 7:
            found = self.days.get(DAY_KEYS[weekday])
            if found is not None:
                return found
        return self.default

    def is_quiet(self, moment: Optional[datetime] = None) -> bool:
        now = moment or datetime.now()
        return self.window_for(now.weekday()).contains(now.time())

    def as_dict(self) -> dict:
        out = {"default": self.default.as_dict()}
        for key in DAY_KEYS:
            if key in self.days:
                out[key] = self.days[key].as_dict()
        return out


_SCHEDULE: Optional[QuietSchedule] = None


def get_schedule() -> QuietSchedule:
    """The active schedule, read from the admin override or the env config."""
    global _SCHEDULE
    if _SCHEDULE is None:
        _SCHEDULE = _load_from_settings()
    return _SCHEDULE


def set_schedule(schedule) -> None:
    """Apply a new schedule in-process, without a restart."""
    global _SCHEDULE
    _SCHEDULE = schedule if isinstance(schedule, QuietSchedule) else QuietSchedule.parse(schedule)


def _load_from_settings() -> QuietSchedule:
    try:
        from src.settings_store import get_setting

        raw = get_setting("quiet_schedule", None)
        if raw:
            return QuietSchedule.parse(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Quiet schedule read failed, using config: %s", exc)

    import config

    window = QuietWindow(
        _clamp_hour(getattr(config.config.quiet_hours, "start", None), DEFAULT_START),
        _clamp_hour(getattr(config.config.quiet_hours, "end", None), DEFAULT_END),
    )
    return QuietSchedule(default=window)


def is_quiet(moment: Optional[datetime] = None) -> bool:
    """True when the assistant should answer without being spoken to."""
    return get_schedule().is_quiet(moment)
