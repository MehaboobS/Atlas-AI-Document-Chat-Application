"""
Atlas AI - Observability and Tracing
Integrates telemetry for RAG steps (embed, search, answer).
Gracefully falls back to local execution timing if external tracing is disabled.
"""

import functools
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


def trace(name: str) -> Callable:
    """
    Decorator for tracing operations (e.g. @trace("embed"), @trace("answer")).
    Captures execution duration, logs metrics, and re-raises exceptions cleanly.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start_time
                logger.info("[TRACE] %s completed in %.3fs", name, elapsed)
                return result
            except Exception as exc:
                elapsed = time.perf_counter() - start_time
                logger.error("[TRACE] %s failed after %.3fs: %s", name, elapsed, exc)
                raise
        return wrapper
    return decorator
