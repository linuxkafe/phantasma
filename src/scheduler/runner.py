"""The thread that makes a stored intent become a thing that happened.

Nothing here decides *what* to run or *when*. The parser decided when, the
store decided that it must survive, and this file only does the one thing left:
notice that the moment arrived, hand the sentence to the same path a spoken
sentence would have taken, and say what happened.

That "same path" is the whole design. `executor` is injected and is expected to
be `pipeline.respond_to_text`, which means a scheduled "liga a luz do balcao"
is resolved by the same skill ordering as a live one -- including that four
skills all claim the nickname "luz do balcao" and the owner validated that
order in production. Reimplementing the resolution here would mean a second
copy of the priority rules, and it would drift within a week.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from src.scheduler.store import ScheduledTask, ScheduleStore

logger = logging.getLogger(__name__)

__all__ = ["SchedulerRunner"]


class SchedulerRunner:
    """Polls the store and fires what is due.

    Attributes:
        store: Where pending tasks live.
        executor: Callable taking the action sentence and returning the answer
            string, or None. Injected rather than imported so the scheduler can
            be tested without a pipeline, an audio device, or the whole house.
        speaker: Optional callable that says a line aloud. Kept separate from
            `executor` because the answer to "what happened" is not the answer
            the pipeline would have produced to a question.
        poll_seconds: How often to look. Fifteen seconds is a compromise: the
            owner naming "as 8" is not to the second, and a short interval buys
            only battery and CPU.
    """

    def __init__(self, store: ScheduleStore,
                 executor: Optional[Callable[[str], Optional[str]]] = None,
                 speaker: Optional[Callable[[str], Any]] = None,
                 poll_seconds: float = 15.0):
        self.store = store
        self.executor = executor
        self.speaker = speaker
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # One task at a time. Two due tasks both switching devices while the
        # voice loop is mid-sentence is a way to make the assistant sound
        # broken, and the cost of serialising is seconds.
        self._firing = threading.Lock()
        self._last_results: list[tuple[str, str, str]] = []

    # --- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Begin polling. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler",
                                        daemon=True)
        self._thread.start()
        logger.info("Agendador iniciado (%d tarefa(s) pendente(s), poll=%.0fs)",
                    len(self.store), self.poll_seconds)

    def stop(self, timeout: float = 2.0) -> None:
        """Ask the loop to finish. Idempotent."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a scheduler must outlive its bugs
                # Never let an exception kill the thread: the symptom of a dead
                # scheduler is that nothing ever happens again, and there is no
                # other signal that anything is wrong.
                logger.exception("Erro no ciclo do agendador")
            # Event.wait, not sleep: stop() then takes effect immediately
            # instead of after a full poll interval.
            self._stop.wait(self.poll_seconds)

    # --- firing ----------------------------------------------------------

    def tick(self, now: Optional[datetime] = None) -> list[ScheduledTask]:
        """Claim and run everything due. Safe to call directly in a test.

        Returns:
            The tasks this tick handled, whether or not they succeeded. The
            claim has already removed them from the store by then.
        """
        due = self.store.claim_due(now)
        if not due:
            return []
        with self._firing:
            for task in due:
                self._fire(task)
        return due

    def _fire(self, task: ScheduledTask) -> None:
        """Run one task, then say what happened.

        The failure path is the one that matters. A task whose device is
        unreachable must not raise out of here and take the remaining due tasks
        with it -- the other tasks are unrelated and still deserve to run.
        """
        logger.info("Agendamento a disparar: %r (agendado para %s)",
                    task.action, task.fire_at)
        answer = None
        failed = False
        try:
            if self.executor is None:
                failed = True
                logger.error("Agendador sem executor: %r nao pode correr",
                             task.action)
            else:
                answer = self.executor(task.action)
                # None means no skill claimed it. That is a failure the owner
                # must hear about: silence would read as "the light came on".
                failed = answer is None
        except Exception as exc:  # noqa: BLE001 - one task must not stop the rest
            failed = True
            answer = f"Falhou: {exc}"
            logger.exception("Agendamento %s falhou", task.id)

        self._record(task, answer, failed)

        if self.speaker is not None:
            try:
                self.speaker(self._announcement(task, answer, failed))
            except Exception:  # noqa: BLE001 - speaking is not the task
                logger.exception("Nao consegui anunciar o agendamento %s", task.id)

    def _announcement(self, task: ScheduledTask, answer: Optional[str],
                      failed: bool) -> str:
        """The sentence said aloud when the task fires.

        On success it repeats what the skill said, because "a luz do balcao
        ligada" is the confirmation the owner actually wants -- hearing the
        device report itself beats hearing the scheduler claim it did something.

        On failure it says so, and by what went wrong, rather than staying quiet.
        """
        if failed:
            reason = answer or "nenhum skill respondeu"
            return (f"O agendamento para as {task.when():%H:%M} falhou: "
                    f"{task.action}. Motivo: {reason}")
        return f"Agendamento: {answer}"

    def _record(self, task: ScheduledTask, answer: Optional[str],
                failed: bool) -> None:
        """Keep a bounded tail of recent outcomes, for the page and for bugs."""
        self._last_results.append((
            task.when().isoformat(timespec="minutes"),
            task.action,
            "falhou" if failed else (answer or ""),
        ))
        del self._last_results[:-20]

    def recent_results(self) -> list[tuple[str, str, str]]:
        """The last twenty firings, newest last. Used by the `/` page."""
        return list(self._last_results)

    # --- injected collaborators -----------------------------------------

    def set_executor(self, executor: Callable[[str], Optional[str]]) -> None:
        self.executor = executor

    def set_speaker(self, speaker: Callable[[str], Any]) -> None:
        self.speaker = speaker


_shared: Optional[SchedulerRunner] = None


def get_shared_runner() -> Optional[SchedulerRunner]:
    """The runner this process is using, if it has one.

    The skill, the HTTP handlers and the pipeline all need the same store, and
    passing it down through three layers of constructor would mean the UI and
    the voice loop could end up looking at different files. One process, one
    runner, registered at boot -- the same shape `src.brain.fly_brain` already
    uses for exactly this reason.
    """
    return _shared


def register_shared_runner(runner: Optional[SchedulerRunner]) -> None:
    global _shared
    _shared = runner


def get_shared_store() -> Optional[ScheduleStore]:
    runner = get_shared_runner()
    return runner.store if runner else None


def wait_for_registration(timeout: float = 2.0) -> Optional[SchedulerRunner]:
    """Block briefly for boot to register the runner.

    Only used by the web layer at import time, where the alternative is an
    endpoint that 404s for the first second after a restart.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runner = get_shared_runner()
        if runner is not None:
            return runner
        time.sleep(0.02)
    return get_shared_runner()
