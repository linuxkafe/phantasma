"""Tests for pipeline utilities."""

import time

from src.pipeline.utils import Result, setup_logging, timed, timer


class TestResult:
    """Test Result dataclass."""

    def test_ok_result(self):
        r = Result.ok("data", 100.0)
        assert r.success is True
        assert r.data == "data"
        assert r.duration_ms == 100.0

    def test_fail_result(self):
        r = Result.fail("error", 50.0)
        assert r.success is False
        assert r.error == "error"
        assert r.duration_ms == 50.0


class TestTimed:
    """Test timed decorator."""

    def test_timed_returns_tuple(self):
        @timed
        def slow_func():
            time.sleep(0.01)
            return "done"

        result, duration = slow_func()
        assert result == "done"
        assert duration >= 10  # at least 10ms


class TestTimer:
    """Test timer context manager."""

    def test_timer_yields_callable(self):
        with timer() as get_elapsed:
            time.sleep(0.01)
            elapsed = get_elapsed()
        assert elapsed >= 10


class TestSetupLogging:
    """Test logging setup."""

    def test_setup_logging_returns_logger(self):
        logger = setup_logging("DEBUG")
        assert logger.name == "phantasma"
        assert logger.level == 10  # DEBUG
