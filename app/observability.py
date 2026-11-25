"""
Privacy-first Rius tracing for Atlas AI.
No telemetry is exported without a valid RIUS_API_KEY.
Ensures zero customer data leakage by disabling payload capture by default.
"""

import os
import logging
from functools import wraps
from typing import Any, Callable

log = logging.getLogger(__name__)


def initialize() -> None:
    """
    Call once at application startup before instrumented app modules are loaded.
    Reads configuration from environment variables or .env.
    """
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("RIUS_API_KEY", "").strip()
    disabled = os.getenv("RIUS_DISABLED", "false").lower() in ("1", "true", "yes")

    if not api_key or disabled:
        log.info("Rius observability disabled (no key provided or kill switch active)")
        return

    try:
        import rius
        service_name = os.getenv("RIUS_SERVICE_NAME", "atlas-ai-enterprise")
        capture_content = os.getenv("RIUS_CAPTURE_CONTENT", "false").lower() in ("1", "true", "yes")

        rius.init(
            api_key=api_key,
            service_name=service_name,
            capture_content=capture_content,
        )
        log.info("Rius observability enabled (service: %s, capture_content: %s)", service_name, capture_content)
    except Exception:
        log.exception("Rius initialization failed; application will continue safely without telemetry")


def trace(name: str) -> Callable:
    """
    Decorate sync or async functions for span tracing.
    Privacy-first: never exports sensitive input arguments or return values.
    """
    def decorate(fn: Callable) -> Callable:
        try:
            import rius
            return rius.observe(name=name, capture_input=False, capture_output=False)(fn)
        except (ImportError, Exception):
            @wraps(fn)
            def fallback(*args: Any, **kwargs: Any) -> Any:
                return fn(*args, **kwargs)
            return fallback
    return decorate
