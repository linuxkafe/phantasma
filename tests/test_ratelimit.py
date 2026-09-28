"""Tests for the admin rate limiter.

The security property under test: a caller who has exhausted a budget is
blocked, and stays blocked, for the whole window -- and the block cannot be
laundered by resetting the counter at exactly the right moment.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.ratelimit import SlidingWindowLimiter, describe  # noqa: E402


class TestSlidingWindowBasics:
    def test_allows_up_to_the_limit(self):
        lim = SlidingWindowLimiter(3, 60)
        assert [lim.allow("a", now=0.0) for _ in range(3)] == [True, True, True]

    def test_blocks_after_the_limit(self):
        lim = SlidingWindowLimiter(2, 60)
        lim.allow("a", now=0.0)
        lim.allow("a", now=0.1)
        assert lim.allow("a", now=0.2) is False

    def test_keys_are_independent(self):
        lim = SlidingWindowLimiter(1, 60)
        assert lim.allow("a", now=0.0) is True
        assert lim.allow("b", now=0.0) is True
        assert lim.allow("a", now=0.1) is False

    def test_window_expiry_reopens(self):
        lim = SlidingWindowLimiter(2, 10)
        lim.allow("a", now=0.0)
        lim.allow("a", now=1.0)
        assert lim.allow("a", now=2.0) is False
        # first attempt is now older than the 10s window
        assert lim.allow("a", now=11.0) is True

    def test_rejected_attempts_do_not_extend_the_lockout(self):
        """A client hammering while blocked must not reset its own timer."""
        lim = SlidingWindowLimiter(1, 10)
        assert lim.allow("a", now=0.0) is True
        # hammer for 8 seconds while blocked
        for t in range(1, 9):
            assert lim.allow("a", now=float(t)) is False
        # the original attempt aged out at t=10, not t=17
        assert lim.allow("a", now=10.5) is True

    def test_is_a_true_sliding_window_not_a_fixed_bucket(self):
        """A fixed bucket would let a burst straddle the boundary.

        Limit 2, window 10. Attempts at t=9 and t=10 straddle t=10. In a fixed
        bucket those land in different windows and all are allowed; in a sliding
        window the t=9 attempt is still live at t=10.
        """
        lim = SlidingWindowLimiter(2, 10)
        assert lim.allow("a", now=9.0) is True
        assert lim.allow("a", now=9.5) is True
        assert lim.allow("a", now=10.0) is False


class TestRemainingAndRetryAfter:
    def test_remaining_counts_down(self):
        lim = SlidingWindowLimiter(3, 60)
        assert lim.remaining("a", now=0.0) == 3
        lim.allow("a", now=0.0)
        assert lim.remaining("a", now=0.0) == 2

    def test_remaining_never_negative(self):
        lim = SlidingWindowLimiter(1, 60)
        for _ in range(5):
            lim.allow("a", now=0.0)
        assert lim.remaining("a", now=0.0) == 0

    def test_retry_after_zero_when_allowed(self):
        lim = SlidingWindowLimiter(5, 60)
        assert lim.retry_after("a", now=0.0) == 0

    def test_retry_after_counts_to_expiry(self):
        lim = SlidingWindowLimiter(1, 30)
        lim.allow("a", now=0.0)
        assert lim.retry_after("a", now=0.0) == 30
        assert lim.retry_after("a", now=10.0) == 20

    def test_reset_clears(self):
        lim = SlidingWindowLimiter(1, 60)
        lim.allow("a", now=0.0)
        lim.reset("a")
        assert lim.allow("a", now=0.0) is True


class TestPrune:
    def test_prune_drops_expired_keys(self):
        lim = SlidingWindowLimiter(1, 10)
        lim.allow("a", now=0.0)
        assert len(lim._hits) == 1
        assert lim.prune(now=100.0) == 1
        assert len(lim._hits) == 0

    def test_prune_keeps_live_keys(self):
        lim = SlidingWindowLimiter(1, 100)
        lim.allow("a", now=50.0)
        assert lim.prune(now=60.0) == 0
        assert len(lim._hits) == 1


class TestValidation:
    def test_rejects_zero_attempts(self):
        with pytest.raises(ValueError):
            SlidingWindowLimiter(0, 60)

    def test_rejects_nonpositive_window(self):
        with pytest.raises(ValueError):
            SlidingWindowLimiter(1, 0)


class TestThreadSafety:
    def test_concurrent_calls_never_exceed_the_limit(self):
        """The limiter is called from request threads; the limit must hold."""
        lim = SlidingWindowLimiter(20, 60)
        granted = []
        lock = threading.Lock()

        def worker():
            for _ in range(50):
                if lim.allow("shared", now=1.0):
                    with lock:
                        granted.append(1)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(granted) == 20, f"granted {len(granted)}, expected exactly 20"


class TestDescribe:
    def test_reports_scope_and_caveat(self):
        """The per-process limitation must be reported, not hidden."""
        d = describe()
        assert d["scope"] == "per-process, per-client-ip"
        assert "multiple workers" in d["caveat"]
        assert d["login"]["max_attempts"] >= 1
        assert d["verify"]["max_attempts"] >= 1


class TestDoesNotTrustForwardedFor:
    def test_source_ignores_x_forwarded_for(self):
        """There is no proxy; trusting the header would let anyone bypass.

        Asserts on the executable body, not the whole file: the docstring
        deliberately NAMES the header to explain why it is not trusted, so a
        naive substring search over the file fails on the explanation itself.
        """
        import ast
        import textwrap

        path = Path(__file__).resolve().parent.parent / "src" / "api" / "ratelimit.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "client_key")
        # Strip docstrings: only executable code is asserted on.
        code = [
            n
            for n in fn.body
            if not (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            )
        ]
        src = textwrap.dedent(ast.unparse(ast.Module(body=code, type_ignores=[])))
        assert "forwarded" not in src.lower()
        assert "remote_addr" in src

    def test_real_time_used_by_default(self):
        lim = SlidingWindowLimiter(1, 0.5)
        assert lim.allow("a") is True
        assert lim.allow("a") is False
        time.sleep(0.55)
        assert lim.allow("a") is True
