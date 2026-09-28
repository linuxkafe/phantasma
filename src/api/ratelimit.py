"""In-process rate limiting for the admin authentication endpoints.

Why this exists
---------------
``/admin/login`` has no attempt limit. It generates a six-digit OTP and mails
it, so an attacker can call it in a loop to (a) bomb the allowlisted mailbox
with OTP mail and (b) obtain a fresh 1-in-1,000,000 guess surface every time,
unlimited, for free. ``/admin/verify`` inherits the same problem.

The OTP check itself is sound -- ``secrets.compare_digest`` is timing-safe and
the code pops the entry so an OTP is single-use. The gap is the *number of
attempts*, not the comparison.

Why in-process and not Redis
----------------------------
The service runs as a single process. A Redis dependency for a sliding window
over two routes is a large operational cost for no benefit here. The
consequence of that choice is stated rather than hidden: **with multiple
workers, each worker keeps its own window**, so the effective limit is
per-worker. :func:`describe` reports that so nobody mistakes this for a
global limit. Revisit when the service is scaled out.

Configuration (env):
    PHANTASMA_LOGIN_RATE_LIMIT    attempts per window (default 5)
    PHANTASMA_LOGIN_RATE_WINDOW   window seconds          (default 600)
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Deque, Dict


class SlidingWindowLimiter:
    """Sliding-window counter, keyed by an arbitrary string.

    Attempts older than the window are discarded on each call, so a burst
    cannot be laundered by waiting exactly one period.
    """

    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """Record an attempt and report whether it is permitted.

        A rejected attempt is NOT recorded, so a client that keeps hammering
        while blocked does not extend its own lockout forever.
        """
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.max_attempts:
                return False
            hits.append(now)
            return True

    def remaining(self, key: str, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return self.max_attempts
            live = sum(1 for t in hits if t > cutoff)
            return max(0, self.max_attempts - live)

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Seconds until the next attempt would be allowed (0 if allowed now)."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            live = [t for t in hits if t > cutoff]
            if len(live) < self.max_attempts:
                return 0
            return max(1, int(round(live[0] + self.window_seconds - now)))

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def prune(self, now: float | None = None) -> int:
        """Drop fully-expired keys. Bounds memory under key spraying."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            dead = [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
            for k in dead:
                self._hits.pop(k, None)
            return len(dead)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Keyed by client IP. Two independent budgets so that exhausting the OTP-verify
# budget does not also block a legitimate user from *requesting* a new code, and
# vice versa: a user who mistypes their code must still be able to ask for a
# fresh one.
login_limiter = SlidingWindowLimiter(
    _env_int("PHANTASMA_LOGIN_RATE_LIMIT", 5),
    float(_env_int("PHANTASMA_LOGIN_RATE_WINDOW", 600)),
)
verify_limiter = SlidingWindowLimiter(
    _env_int("PHANTASMA_VERIFY_RATE_LIMIT", 10),
    float(_env_int("PHANTASMA_VERIFY_RATE_WINDOW", 600)),
)


def client_key() -> str:
    """Best-effort client identity for rate limiting.

    Deliberately does NOT trust X-Forwarded-For: there is no reverse proxy in
    front of the service, so that header is client-controlled and trusting it
    would let anyone bypass the limit by sending a random value. If a proxy is
    ever added, this is the line that must change -- and it must change to
    "trust the last proxy hop", not "trust the header".
    """
    return request_remote_addr()


def request_remote_addr() -> str:
    from flask import request

    return request.remote_addr or "unknown"


def describe() -> dict:
    """Operational description, for the health endpoint and for tests."""
    return {
        "login": {
            "max_attempts": login_limiter.max_attempts,
            "window_seconds": login_limiter.window_seconds,
        },
        "verify": {
            "max_attempts": verify_limiter.max_attempts,
            "window_seconds": verify_limiter.window_seconds,
        },
        "scope": "per-process, per-client-ip",
        "caveat": "with multiple workers each worker keeps its own window",
    }
