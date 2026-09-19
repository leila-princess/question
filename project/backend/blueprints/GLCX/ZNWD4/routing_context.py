"""Per-request LLM client/model for routing experiments (contextvars)."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class RoutingLlmContext:
    client: Any
    model: str
    llm_limits: Optional[Dict[str, Any]] = None


_routing_llm_ctx: ContextVar[Optional[RoutingLlmContext]] = ContextVar(
    "znwd4_routing_llm_ctx",
    default=None,
)

_llm_call_counts: ContextVar[Optional[Dict[str, int]]] = ContextVar(
    "znwd4_llm_call_counts",
    default=None,
)


def set_routing_llm_context(
    client: Any,
    model: str,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> None:
    _routing_llm_ctx.set(RoutingLlmContext(client=client, model=model, llm_limits=llm_limits))


def get_routing_llm_context() -> Optional[RoutingLlmContext]:
    return _routing_llm_ctx.get()


def begin_llm_call_tracking() -> None:
    _llm_call_counts.set({})


def record_llm_call(site: str) -> None:
    counts = _llm_call_counts.get()
    if counts is None:
        return
    key = (site or "unknown").strip() or "unknown"
    counts[key] = counts.get(key, 0) + 1


def log_llm_call_summary(question: str) -> None:
    counts = _llm_call_counts.get()
    if not counts:
        return
    total = sum(counts.values())
    sites = ",".join(f"{k}:{v}" for k, v in sorted(counts.items()))
    q = (question or "").strip()
    logger.info("[ZNWD4][llm_summary] q=%r calls=%d sites=%s", q[:120], total, sites)


def clear_routing_llm_context() -> None:
    _routing_llm_ctx.set(None)
    _llm_call_counts.set(None)
