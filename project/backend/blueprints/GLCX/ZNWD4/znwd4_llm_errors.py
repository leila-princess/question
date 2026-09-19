"""Detect LLM/API failures that should trigger probe-first fallback."""

from __future__ import annotations

from typing import Any


def is_llm_unavailable_error(exc: BaseException) -> bool:
    """True when routing/synthesis should degrade instead of hard-failing."""
    name = type(exc).__name__
    if name in (
        "APIConnectionError",
        "ConnectError",
        "ConnectionError",
        "ConnectTimeout",
        "InternalServerError",
        "APIStatusError",
        "RateLimitError",
        "ServiceUnavailableError",
        "Timeout",
        "ReadTimeout",
        "HTTPStatusError",
    ):
        return True

    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    try:
        if status is not None and int(status) in (408, 429, 500, 502, 503, 504):
            return True
    except (TypeError, ValueError):
        pass

    msg = str(exc).lower()
    needles = (
        "connection error",
        "connection refused",
        "积极拒绝",
        "bad gateway",
        "service unavailable",
        "error code: 502",
        "error code: 503",
        "error code: 504",
        "error code: 500",
        "502",
        "503",
        "504",
        "timeout",
        "timed out",
    )
    return any(n in msg for n in needles)


def is_truthy(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


__all__ = ["is_llm_unavailable_error", "is_truthy"]
