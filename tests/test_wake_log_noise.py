"""Tests for the wake-score log.

Reported on 2026-09-29: the journal was filling with

    🔬 Wake scores: ola_fantasma=0.0008

every five seconds, twice per tick, and the owner asked for it only when the
values are near waking. Two separate faults, and the second was hiding the
first:

* the line was logged unconditionally at INFO, so a number meaning "silence,
  0.08% of threshold" was written forever;
* every record went out TWICE, once through the "phantasma" logger's own
  handler and once through the root logger's, because `propagate` was left at
  its default. Both copies looked fine, which is why the volume went unnoticed
  until it was something to read past.

A log that only prints when something is happening is a diagnostic. A log that
prints on a loop is a tax.
"""

from __future__ import annotations

import logging

import pytest

from assistant import _wake_score_is_worth_logging
from src.pipeline import utils


def test_the_phantasma_logger_does_not_propagate_to_the_root():
    """One destination, not two.

    The module-level `setup_logging()` attaches a handler to the "phantasma"
    logger. Anything that also calls `logging.basicConfig()` -- a library, a
    dependency, a script -- attaches one to the ROOT logger, and with
    propagate left at True every record was emitted by both. The symptom was
    each line appearing twice in journald with two different formats, which
    reads as a bug in the message rather than in the logging setup.
    """
    logger = utils.setup_logging()
    assert logger.propagate is False, (
        "records propagate to the root logger, so every line is written twice"
    )


def test_setting_up_logging_twice_still_yields_one_handler():
    """Idempotent.

    Identified by our formatter, not by class: the test session has pytest's
    own handlers on the same logger and some of them are StreamHandlers too, so
    a class check counts the harness. What matters is that calling this twice
    does not add a second handler formatted the way ours is -- that is the
    duplication when setup_logging runs again after something else touched the
    logger.
    """
    utils.setup_logging()
    logger = utils.setup_logging()
    ours = [
        h for h in logger.handlers
        if getattr(h, "formatter", None) is not None
        and "%(asctime)s %(levelname)s %(name)s %(message)s" in h.formatter._fmt
    ]
    assert len(ours) == 1, f"a second setup added another of our handlers: {logger.handlers}"


def test_a_record_is_written_once(caplog):
    """The observable consequence, rather than the setting that causes it."""
    utils.setup_logging()
    logger = logging.getLogger("phantasma")
    # Attach the same StreamHandler to root, which is what a library doing
    # basicConfig() would do.
    root = logging.getLogger()
    root.addHandler(logger.handlers[0])
    try:
        written = []
        logger.handlers[0].stream = _ListStream(written)
        root.handlers[0].stream = _ListStream(written)
        logger.info("uma linha")
        assert len(written) == 1, f"the line was written {len(written)} times"
    finally:
        root.removeHandler(root.handlers[-1])


class _ListStream:
    def __init__(self, sink):
        self._sink = sink

    def write(self, s):
        self._sink.append(s)

    def flush(self):
        pass


# --- the "only when near" rule ---------------------------------------------


def _log_line(top_score, last_score, threshold=0.5):
    """The real predicate, imported -- not a copy of it.

    The first version of this test reimplemented the comparison locally, which
    meant it would have passed against a completely broken implementation: it
    was asserting that its own transcription of the rule matched itself. If the
    rule changes, this test must fail.
    """
    return _wake_score_is_worth_logging(top_score, last_score, threshold)


@pytest.mark.parametrize(
    "score, last, should_print, why",
    [
        (0.0008, 0.0, False, "silence in an empty room"),
        (0.05, 0.05, False, "flat and far from the threshold"),
        (0.30, 0.29, True, "a fifth of the way to waking: worth seeing"),
        (0.45, 0.30, True, "close to the threshold"),
        (0.90, 0.90, True, "above the threshold: it should have fired"),
        (0.12, 0.01, True, "rising fast from silence -- the shape of a word starting"),
    ],
)
def test_a_score_prints_only_when_it_is_worth_reading(
    score, last, should_print, why
):
    assert _log_line(score, last) is should_print, why


def test_the_near_threshold_rule_follows_the_configured_confidence():
    """A house with a strict threshold must not log every distant mumble, and a
    permissive one must not hide a score that is genuinely close. The same
    number is printed in one configuration and not the other."""
    strict = _log_line(0.12, 0.12, threshold=0.9)    # 13% of a high bar
    permissive = _log_line(0.12, 0.12, threshold=0.2)  # 60% of a low one
    assert permissive is True
    assert strict is False
