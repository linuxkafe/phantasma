"""
Pipeline utilities: timing, logging, result types.

Provides:
- Result: Standardized result container for pipeline stages
- timed: Decorator for timing function execution
- timer: Context manager for timing code blocks
- setup_logging: Configure structured logging for journald
- log_stage: Log pipeline stage results with structured fields
"""

import functools
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar


@dataclass
class Result:
    """Result of a pipeline stage.

    Standardized container for success/failure with data, error message,
    and timing information. Used throughout the voice pipeline.

    Attributes:
        success: True if operation succeeded, False otherwise.
        data: Result data on success (type varies by stage).
        error: Error message on failure.
        duration_ms: Operation duration in milliseconds.
    """

    success: bool
    data: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0

    @classmethod
    def ok(cls, data: Any, duration_ms: float = 0.0) -> "Result":
        """Create successful result.

        Args:
            data: Result data.
            duration_ms: Optional duration in milliseconds.

        Returns:
            Result with success=True.
        """
        return cls(success=True, data=data, duration_ms=duration_ms)

    @classmethod
    def fail(cls, error: str, duration_ms: float = 0.0) -> "Result":
        """Create failed result.

        Args:
            error: Error message.
            duration_ms: Optional duration in milliseconds.

        Returns:
            Result with success=False.
        """
        return cls(success=False, error=error, duration_ms=duration_ms)


T = TypeVar("T")


def timed(func: Callable[..., T]) -> Callable[..., tuple[T, float]]:
    """Decorator that returns (result, duration_ms).

    Wraps a function to measure execution time. Returns tuple of
    (original_return_value, duration_in_milliseconds).

    Args:
        func: Function to time.

    Returns:
        Wrapped function returning (result, duration_ms).
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> tuple[T, float]:
        start = time.perf_counter()
        result = func(*args, **kwargs)
        duration_ms = (time.perf_counter() - start) * 1000
        return result, duration_ms

    return wrapper


@contextmanager
def timer():
    """Context manager that yields a function returning elapsed ms.

    Usage:
        with timer() as get_elapsed:
            do_something()
            elapsed = get_elapsed()  # milliseconds

    Yields:
        Callable returning elapsed milliseconds since context entry.
    """
    start = time.perf_counter()
    yield lambda: (time.perf_counter() - start) * 1000


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure structured logging for journald.

    Creates logger named "phantasma" with ISO8601 timestamp format.
    Adds StreamHandler if none exists. Idempotent: safe to call multiple times.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR).
            Default "INFO". Case-insensitive.

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger("phantasma")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    # One destination, not two. See the note above.
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def log_stage(logger: logging.Logger, stage: str, result: Result, **extra):
    """Log a pipeline stage result with structured fields.

    Formats log as: stage=<name> status=ok|error duration_ms=<ms> [extra...]
    Extra kwargs are appended as key=value pairs.

    Args:
        logger: Logger instance (from setup_logging()).
        stage: Stage name (e.g., "stt", "llm", "tts", "playback").
        result: Result object from pipeline stage.
        **extra: Additional structured fields to include.
    """
    fields = " ".join(f"{k}={v}" for k, v in extra.items())
    if result.success:
        logger.info(f"stage={stage} status=ok duration_ms={result.duration_ms:.1f} {fields}")
    else:
        logger.error(
            f"stage={stage} status=error "
            f"duration_ms={result.duration_ms:.1f} error={result.error} {fields}"
        )


logger = setup_logging()
