"""Route between ZNWD4 routing backends (probe-first hybrid vs legacy langchain vs MLP)."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Generator, Optional, Tuple

from .route_clarify import ROUTE_CLARIFY_EVENT
from .znwd4_langchain_routing import ROUTE_PLAN_INTERNAL, stream_route_plan

RouteStreamFn = Callable[..., Generator[Tuple[str, Dict[str, Any]], None, None]]


def znwd4_routing_mode() -> str:
    """Default: probe_hybrid (probe → ECS fast path → Hybrid LLM). Optional: langchain, mlp_llm_ask."""
    return (os.environ.get("ZNWD4_ROUTING_MODE") or "probe_hybrid").strip().lower()


def resolve_route_stream_fn() -> RouteStreamFn:
    mode = znwd4_routing_mode()
    if mode in ("probe_hybrid", "probe-first", "probe_first", "efhr_l3"):
        from .znwd4_probe_hybrid_routing import stream_route_plan_probe_hybrid

        return stream_route_plan_probe_hybrid
    if mode in ("langchain", "legacy", "llm", "znwd4_hybrid"):
        return stream_route_plan
    from .znwd4_mlp_routing import stream_route_plan_mlp

    return stream_route_plan_mlp


def stream_route_plan_auto(
    client: Any,
    model: str,
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    driver: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
    route_clarify: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    fn = resolve_route_stream_fn()
    kwargs: Dict[str, Any] = {
        "user_id": user_id,
        "session_id": session_id,
        "driver": driver,
        "llm_limits": llm_limits,
    }
    if fn.__name__ in (
        "stream_route_plan_mlp",
        "stream_route_plan",
        "stream_route_plan_probe_hybrid",
    ):
        kwargs["route_clarify"] = route_clarify
    yield from fn(client, model, question, **kwargs)


__all__ = [
    "ROUTE_PLAN_INTERNAL",
    "ROUTE_CLARIFY_EVENT",
    "znwd4_routing_mode",
    "resolve_route_stream_fn",
    "stream_route_plan_auto",
]
