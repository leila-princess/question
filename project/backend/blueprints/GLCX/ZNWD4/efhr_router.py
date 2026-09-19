"""EFHR routing-only entry: L0 lock → probe → ECS → (optional) LLM fallback."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .pcsr_assembly import (
    build_evidence_for_question,
    pcsr_slots,
    slots_ready_for_route,
)
from .probe_preflight import build_evidence_bundle
from .question_route_lock import (
    RouteLock,
    apply_route_lock_params,
    locked_slot_values,
    resolve_route_lock,
    skip_llm_for_lock,
    skip_llm_routing_when_locked,
)
from .route_scorer import get_route_entry, score_routes, should_abstain_from_llm
from .routing_catalog import PURPOSE_CHAT, PURPOSE_META, filter_routes_by_purpose, get_route, guess_purpose_from_question


def _lock_to_prediction(lock: RouteLock, evidence: Any) -> Tuple[str, str, Dict[str, Any], Dict[str, Any]]:
    params = apply_route_lock_params({}, lock)
    slots = pcsr_slots(route_id=lock.route_id, lock=lock, evidence=evidence, raw_slots=params)
    meta = {"tier": "L0", "llm_calls": 0, "probe_ran": evidence.probe_ran, "route_lock": lock.reason}
    return lock.route_id, lock.purpose, slots, meta


def _ecs_to_prediction(
    question: str,
    evidence: Any,
    scores: List[Any],
) -> Tuple[str, str, Dict[str, Any], Dict[str, Any]]:
    top = scores[0]
    entry = get_route_entry(top.route_id)
    purpose = entry.purpose if entry else guess_purpose_from_question(question)
    raw: Dict[str, Any] = {"focus": ""}
    if entry:
        for slot in entry.required_slots:
            if slot.endswith("_keywords"):
                raw[slot] = []
    slots = pcsr_slots(route_id=top.route_id, lock=None, evidence=evidence, raw_slots=raw)
    meta = {
        "tier": "L2",
        "llm_calls": 0,
        "probe_ran": evidence.probe_ran,
        "ecs_score": top.score,
        "ecs_breakdown": top.breakdown,
    }
    return top.route_id, purpose, slots, meta


def _guess_to_prediction(question: str, evidence: Any) -> Tuple[str, str, Dict[str, Any], Dict[str, Any]]:
    from .routing_catalog import list_routes

    purpose = guess_purpose_from_question(question)
    if purpose == PURPOSE_CHAT:
        return "R-CHAT-direct", purpose, {}, {"tier": "L1", "llm_calls": 0, "probe_ran": evidence.probe_ran}
    candidates = filter_routes_by_purpose(purpose)
    if not candidates:
        candidates = [r for r in list_routes() if r.purpose == purpose]
    if not candidates and purpose not in (PURPOSE_CHAT, PURPOSE_META):
        purpose = guess_purpose_from_question(question)
        candidates = filter_routes_by_purpose(purpose)
    route_id = candidates[0].route_id if candidates else "R-TEST_PROJECT-by_equipment"
    raw: Dict[str, Any] = {}
    entry = get_route(route_id)
    if entry:
        for slot in entry.required_slots:
            if slot.endswith("_keywords"):
                raw[slot] = []
    slots = pcsr_slots(route_id=route_id, lock=None, evidence=evidence, raw_slots=raw)
    return route_id, purpose, slots, {"tier": "L1", "llm_calls": 0, "probe_ran": evidence.probe_ran}


def ecs_fast_path_ready(
    question: str,
    evidence: Any,
    scores: List[Any],
) -> bool:
    """ECS 高置信且 probe 槽位就绪 → 可走 0 LLM 快路径。"""
    if not scores or not should_abstain_from_llm(scores):
        return False
    top = scores[0]
    return slots_ready_for_route(top.route_id, evidence)


def route_question_efhr(
    question: str,
    *,
    driver: Any = None,
    client: Any = None,
    model: str = "",
    llm_limits: Optional[Dict[str, Any]] = None,
    allow_llm_fallback: bool = False,
    history_focus: str = "",
) -> Tuple[str, str, Dict[str, Any], Dict[str, Any]]:
    """
    Routing-only EFHR pipeline.
    Returns (route_id, purpose, slots, meta).
    """
    t0 = time.perf_counter()
    q = (question or "").strip()

    lock = resolve_route_lock(q)
    evidence = build_evidence_for_question(
        q,
        driver=driver,
        lock=lock,
        history_focus=history_focus,
    )

    if lock and skip_llm_for_lock(lock):
        rid, purpose, slots, meta = _lock_to_prediction(lock, evidence)
        meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return rid, purpose, slots, meta

    scores = score_routes(q, evidence, top_k=int(os.environ.get("EFHR_L3_TOP_K", "3")))
    if ecs_fast_path_ready(q, evidence, scores):
        rid, purpose, slots, meta = _ecs_to_prediction(q, evidence, scores)
        meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return rid, purpose, slots, meta

    if lock and skip_llm_routing_when_locked() and slots_ready_for_route(lock.route_id, evidence):
        rid, purpose, slots, meta = _lock_to_prediction(lock, evidence)
        meta["tier"] = "L0+PCSR"
        meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return rid, purpose, slots, meta

    if allow_llm_fallback and client and model:
        from .znwd4_langchain_routing import run_route_plan

        plan, _ = run_route_plan(client, model, q, driver=driver, llm_limits=llm_limits)
        rid = str(plan.get("route_id") or "")
        purpose = str(plan.get("purpose") or "")
        params = plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {}
        slots = pcsr_slots(route_id=rid, lock=lock, evidence=evidence, raw_slots=params)
        meta = {
            "tier": "L3",
            "llm_calls": 2,
            "probe_ran": evidence.probe_ran,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        return rid, purpose, slots, meta

    if scores and scores[0].score >= 0.25:
        rid, purpose, slots, meta = _ecs_to_prediction(q, evidence, scores)
        meta["tier"] = "L2-fallback"
        meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return rid, purpose, slots, meta

    rid, purpose, slots, meta = _guess_to_prediction(q, evidence)
    meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return rid, purpose, slots, meta


# Backward-compatible re-exports
_pcsr_slots = pcsr_slots
_lock_to_prediction = _lock_to_prediction

__all__ = [
    "route_question_efhr",
    "ecs_fast_path_ready",
    "_pcsr_slots",
    "_lock_to_prediction",
    "pcsr_slots",
]
