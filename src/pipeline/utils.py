"""
Pipeline utilities: timing, logging, result types.
"""

import functools
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar


@dataclass
class Result:
    """Result of a pipeline stage."""

    success: bool
    data: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0

    @classmethod
    def ok(cls, data: Any, duration_ms: float = 0.0) -> "Result":
        return cls(success=True, data=data, duration_ms=duration_ms)

    @classmethod
    def fail(cls, error: str, duration_ms: float = 0.0) -> "Result":
        return cls(success=False, error=error, duration_ms=duration_ms)


T = TypeVar("T")


def timed(func: Callable[..., T]) -> Callable[..., tuple[T, float]]:
    """Decorator that returns (result, duration_ms)."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> tuple[T, float]:
        start = time.perf_counter()
        result = func(*args, **kwargs)
        duration_ms = (time.perf_counter() - start) * 1000
        return result, duration_ms

    return wrapper


@contextmanager
def timer():
    """Context manager that yields a function returning elapsed ms."""
    start = time.perf_counter()
    yield lambda: (time.perf_counter() - start) * 1000


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure structured logging for journald."""
    logger = logging.getLogger("phantasma")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

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
    """Log a pipeline stage result with structured fields."""
    fields = " ".join(f"{k}={v}" for k, v in extra.items())
    if result.success:
        logger.info(
            f"stage={stage} status=ok duration_ms={result.duration_ms:.1f} {fields}"
        )
    else:
        logger.error(
            f"stage={stage} status=error "
            f"duration_ms={result.duration_ms:.1f} error={result.error} {fields}"
        )


logger = setup_logging()
