"""Deferred execution: turning a sentence into something that happens later.

Two pieces, deliberately separate. `parsing` is pure -- given a sentence and a
clock it returns an instant, and it never touches the disk or the pipeline.
`store` is the durable half, and `runner` is the thread that fires.

The split is what makes the awkward rules testable: the owner's 8/20/tomorrow
rule is decided at three different times of day, and a test that has to move a
wall clock to check it is a test nobody trusts.
"""

from src.scheduler.parsing import (
                                   EVENING_HOUR,
                                   MORNING_HOUR,
                                   Schedule,
                                   parse_schedule,
                                   resolve_moment,
)
from src.scheduler.runner import (
                                   SchedulerRunner,
                                   get_shared_runner,
                                   get_shared_store,
                                   register_shared_runner,
)
from src.scheduler.store import ScheduledTask, ScheduleStore

__all__ = ["Schedule", "ScheduledTask", "ScheduleStore", "SchedulerRunner",
           "parse_schedule", "resolve_moment", "get_shared_runner",
           "get_shared_store", "register_shared_runner",
           "MORNING_HOUR", "EVENING_HOUR"]
