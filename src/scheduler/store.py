"""The durable half of scheduling: what is pending, and it survives a restart.

A schedule the assistant forgets when the service restarts is worse than no
scheduling at all, because the owner was told it was set. So this writes to
disk before the owner is told anything, and the runner re-reads at boot.

One JSON file, not a table. The whole set is a handful of rows, it is read as a
unit, and it is written as a unit; a table would buy transactions over a
collection that never needs to be half-updated. The write is still atomic
(temp file + `os.replace`), because the one failure that actually loses data is
a crash halfway through the write.

Naive local time, no timezone. The owner thinks in the wall clock in this room,
and pHantasma runs on that clock. Storing an offset would mean re-deciding what
"as 8" means twice a year, at the exact moment a task is most likely to be
judged wrong.
"""

import json
import logging
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)

__all__ = ["ScheduledTask", "ScheduleStore"]


@dataclass
class ScheduledTask:
    """One pending task.

    Attributes:
        id: Short opaque handle, used by the cancel endpoint.
        action: The sentence to replay, already stripped of its time phrase.
        fire_at: ISO local datetime.
        created_at: ISO local datetime, for the page's ordering and for
            answering "since when is this pending".
        source: "voice", "chat" or "ui" -- who asked. Not cosmetic: it is how a
            bug report says whether the sentence came from the STT or a typo.
    """

    id: str
    action: str
    fire_at: str
    created_at: str
    source: str = "voice"
    note: str = field(default="", repr=False)

    def when(self) -> datetime:
        """The fire time as a datetime, or a very old one if it is unreadable.

        Falling back to the epoch means a corrupt row is simply *due*, and the
        runner tries to run it and reports the failure. Silently dropping it
        would leave the owner with a light that never comes on and a card that
        quietly disappeared from the page.
        """
        try:
            return datetime.fromisoformat(self.fire_at)
        except (TypeError, ValueError):
            return datetime.fromtimestamp(0)


class ScheduleStore:
    """Pending tasks, persisted as one JSON file.

    Every mutation writes through to disk under a lock. The runner is a
    separate thread from the HTTP handlers and from the voice loop, so
    "read, decide, remove, write" is genuinely concurrent and the lock is what
    stops two of them firing the same task.
    """

    def __init__(self, path: str, lock=None):
        self.path = str(path)
        # Injected so tests can pass an unsynchronised object deliberately, and
        # so two ScheduleStore instances over one path can share one lock.
        self._lock = lock if lock is not None else threading.RLock()
        self._tasks: list[ScheduledTask] = []
        self.load()

    # --- persistence -----------------------------------------------------

    def load(self) -> list[ScheduledTask]:
        """Re-read the file, discarding anything unreadable.

        A missing file is the normal first-boot case and is not an error. A
        corrupt one is logged loudly and treated as empty rather than raised:
        refusing to start because a cache-shaped file is malformed would take
        the whole assistant down over scheduling.
        """
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except FileNotFoundError:
                self._tasks = []
                return []
            except (OSError, ValueError) as exc:
                logger.error("Agendamentos ilegiveis em %s: %s. A treatar como vazio.",
                             self.path, exc)
                self._tasks = []
                return []

            tasks = []
            for row in raw if isinstance(raw, list) else []:
                if not isinstance(row, dict) or not row.get("action"):
                    continue
                tasks.append(ScheduledTask(
                    id=str(row.get("id") or uuid.uuid4().hex[:12]),
                    action=str(row["action"]),
                    fire_at=str(row.get("fire_at") or ""),
                    created_at=str(row.get("created_at") or ""),
                    source=str(row.get("source") or "voice"),
                    note=str(row.get("note") or ""),
                ))
            self._tasks = tasks
            return list(self._tasks)

    def _write_locked(self) -> None:
        """Persist the current set atomically. Caller already holds the lock."""
        directory = os.path.dirname(self.path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            # Same directory as the target, so os.replace is a rename within
            # one filesystem and is therefore atomic. A temp file in /tmp
            # would make the replace a cross-device copy, which is not atomic
            # and would defeat the point of doing it at all.
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=directory,
                prefix=".schedules-", suffix=".tmp", delete=False,
            )
            try:
                with handle:
                    json.dump([asdict(task) for task in self._tasks], handle,
                              ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, self.path)
            except BaseException:
                # Never leave the scratch file behind on a failed write.
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            # The caller has usually already told the owner it is scheduled.
            # Losing the file means losing the task, so this is loud, but it
            # must not take down the request that triggered it.
            logger.error("Nao foi possivel gravar agendamentos em %s: %s",
                         self.path, exc)

    # --- queries ---------------------------------------------------------

    def pending(self) -> list[ScheduledTask]:
        """Every task still to fire, soonest first."""
        with self._lock:
            return sorted(self._tasks, key=lambda t: t.when())

    def get(self, task_id: str) -> Optional[ScheduledTask]:
        with self._lock:
            return next((t for t in self._tasks if t.id == task_id), None)

    # --- mutations -------------------------------------------------------

    def add(self, action: str, fire_at: datetime, source: str = "voice",
            note: str = "") -> ScheduledTask:
        """Record a task and persist it before returning.

        Returns:
            The stored task, with its id assigned.
        """
        with self._lock:
            task = ScheduledTask(
                id=uuid.uuid4().hex[:12],
                action=action,
                fire_at=fire_at.isoformat(timespec="seconds"),
                created_at=datetime.now().isoformat(timespec="seconds"),
                source=source,
                note=note,
            )
            self._tasks.append(task)
            self._write_locked()
            return task

    def cancel(self, task_id: str) -> bool:
        """Remove one task by id. True if it existed."""
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if t.id != task_id]
            if len(self._tasks) == before:
                return False
            self._write_locked()
            return True

    def cancel_where(self, predicate: Callable[[ScheduledTask], bool]) -> int:
        """Remove every task the predicate accepts. Returns how many went.

        This is the "cancela a luz do balcao" path, where the owner names the
        task by what it *does* rather than by an id they never saw. The
        predicate receives the whole task so a caller can match on the action
        text and still honour the time if it needs to.
        """
        with self._lock:
            kept = [t for t in self._tasks if not predicate(t)]
            removed = len(self._tasks) - len(kept)
            if removed:
                self._tasks = kept
                self._write_locked()
            return removed

    def claim_due(self, now: Optional[datetime] = None) -> list[ScheduledTask]:
        """Take every task whose moment has arrived, and remove it atomically.

        Claim-and-remove rather than claim-then-remove, so a task cannot be
        handed out twice: the removal is part of the same locked write. A task
        is gone the moment it is claimed, which means a crash mid-execution
        loses the task -- the alternative, leaving it pending, would re-run it
        on the next boot and switch the light twice.

        Returns:
            The tasks that were due, oldest first.
        """
        now = now or datetime.now()
        with self._lock:
            due = [t for t in self._tasks if t.when() <= now]
            if due:
                self._tasks = [t for t in self._tasks if t.when() > now]
                self._write_locked()
            return sorted(due, key=lambda t: t.when())

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)
