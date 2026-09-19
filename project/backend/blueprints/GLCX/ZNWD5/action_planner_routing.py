"""
ZNWD5 ActionPlanner 路由：三层分发 Tier0 / FAQ / Action Planner。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from blueprints.GLCX.ZNWD4.question_route_lock import RouteLock
from blueprints.GLCX.ZNWD4.route_lock_resolver import safe_resolve_route_lock
from blueprints.GLCX.ZNWD4.routing_catalog import PURPOSE_CHAT, PURPOSE_META, guess_purpose_from_question
from blueprints.GLCX.ZNWD4.routing_telemetry import log_routing_event
from blueprints.GLCX.ZNWD4.znwd4_history_context import enrich_short_follow_up
from blueprints.GLCX.ZNWD4.znwd4_langchain_routing import ROUTE_PLAN_INTERNAL, stream_route_plan
from blueprints.GLCX.ZNWD4.znwd4_llm_errors import is_llm_unavailable_error
from blueprints.GLCX.ZNWD4.znwd4_thinking_emit import emit_milestone

from .action_plan_schema import ActionPlan, parse_action_plan_json, plan_primary_action, plan_purpose_node
from .action_plan_validator import validate_action_plan, validation_errors_fatal
from .faq_lock_allowlist import (
    filter_lock_for_planner_mode,
    is_faq_whitelist_lock,
    is_tier0_lock,
    lock_tier,
)
from .prompts.action_planner_prompt import (
    build_action_planner_human,
    build_action_planner_system_prompt,
    build_repair_human,
)
from .zvec_grounding import ground_question

logger = logging.getLogger(__name__)


def znwd5_routing_mode() -> str:
    return (os.environ.get("ZNWD5_ROUTING_MODE") or "legacy").strip().lower()


def planner_skip_locks() -> bool:
    return os.environ.get("ZNWD5_PLANNER_SKIP_LOCKS", "").strip().lower() in ("1", "true", "yes")


def should_use_action_planner_fast_path(lock: Optional[RouteLock]) -> bool:
    """Tier0：chat / meta / 无槽 enum → legacy 0-LLM。"""
    if planner_skip_locks():
        return False
    return is_tier0_lock(lock)


def _lc_kwargs(client: Any, model: str, llm_limits: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    kw: Dict[str, Any] = {"model": model, "temperature": 0}
    if client:
        bu = getattr(client, "base_url", None)
        if bu:
            kw["base_url"] = str(bu)
        key = getattr(client, "api_key", None)
        if key:
            kw["api_key"] = key
    if llm_limits:
        if llm_limits.get("max_tokens"):
            kw["max_tokens"] = int(llm_limits["max_tokens"])
        if llm_limits.get("timeout"):
            kw["timeout"] = float(llm_limits["timeout"])
        if llm_limits.get("request_timeout"):
            kw["request_timeout"] = float(llm_limits["request_timeout"])
    return kw


def _call_planner_llm(
    client: Any,
    model: str,
    question: str,
    grounding: Dict[str, Any],
    *,
    llm_limits: Optional[Dict[str, Any]] = None,
    repair: Optional[Tuple[str, List[str]]] = None,
) -> str:
    llm = ChatOpenAI(**_lc_kwargs(client, model, llm_limits))
    sys_msg = build_action_planner_system_prompt()
    if repair:
        raw_plan, errs = repair
        human = build_repair_human(question, raw_plan, errs)
    else:
        human = build_action_planner_human(question, grounding)
    resp = llm.invoke([SystemMessage(content=sys_msg), HumanMessage(content=human)])
    content = resp.content
    if isinstance(content, list):
        content = "".join(str(x) for x in content)
    return str(content or "")


def _plan_to_route_plan(
    plan: ActionPlan,
    *,
    question: str,
    grounding: Dict[str, Any],
    effective_question: str,
    history_snapshot: Optional[Dict[str, Any]] = None,
    llm_calls: int = 1,
) -> Dict[str, Any]:
    purpose = plan_purpose_node(plan)
    action = plan_primary_action(plan)
    best = grounding.get("best") if isinstance(grounding.get("best"), dict) else {}
    kw = str(best.get("name") or grounding.get("keyword") or "").strip()

    route_plan: Dict[str, Any] = {
        "source": "action_planner",
        "routing_tier": "action_planner",
        "route_id": "R-ACTION-PLANNER",
        "purpose": purpose.lower() if purpose else "graph_query",
        "purpose_node": purpose,
        "biz_type": action,
        "keyword": kw,
        "retrieval_question": effective_question,
        "params_draft": {
            "purpose_node": purpose,
            "biz_type": action,
            "focus": kw,
        },
        "action_plan": plan.to_dict(),
        "grounding": grounding,
        "answer_mode": plan.answer_mode,
        "answer_strategy": "db_synthesize" if plan.answer_mode == "synthesize" else "db_direct",
        "match_confidence": plan.confidence,
        "probe_snapshot": {
            "best": best,
            "hits": grounding.get("candidates") or [],
            "zvec_used": bool(grounding.get("zvec_used")),
        },
        "zvec_used": bool(grounding.get("zvec_used")),
        "llm_routing_calls": llm_calls,
        "faq_slot_verified": False,
    }
    if history_snapshot:
        route_plan["history_snapshot"] = history_snapshot
    return route_plan


def _yield_langchain_fallback(
    client: Any,
    model: str,
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    driver: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
    route_clarify: Optional[Dict[str, Any]] = None,
    effective_question: str = "",
    history_snapshot: Optional[Dict[str, Any]] = None,
    route_lock: Optional[RouteLock] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """Action Planner 失败后降级 langchain Step1/2（不走 probe_hybrid 锁路径）。"""
    yield emit_milestone("动作规划失败，降级 langchain LLM 路由…", kind="route")
    plan_out: Optional[Dict[str, Any]] = None
    try:
        for ev, data in stream_route_plan(
            client,
            model,
            question,
            user_id=user_id,
            session_id=session_id,
            driver=driver,
            llm_limits=llm_limits,
            route_clarify=route_clarify,
            _skip_enrich=bool(effective_question),
            _effective_question=effective_question or question,
            _history_snapshot=history_snapshot,
            _route_lock=route_lock,
        ):
            if ev == ROUTE_PLAN_INTERNAL and isinstance(data, dict):
                plan_out = data.get("plan") if isinstance(data.get("plan"), dict) else None
                if plan_out:
                    plan_out["routing_tier"] = "langchain"
                    plan_out["source"] = plan_out.get("source") or "langchain"
            yield ev, data
    except Exception as exc:
        if is_llm_unavailable_error(exc):
            logger.warning("[ZNWD5][planner] langchain fallback LLM unavailable: %s", exc)
            yield emit_milestone("LLM 不可用，probe-only 降级…", kind="route")
            from blueprints.GLCX.ZNWD4.znwd4_probe_hybrid_routing import _probe_fallback_plan
            from blueprints.GLCX.ZNWD4.pcsr_assembly import build_evidence_for_question, extract_history_focus
            from blueprints.GLCX.ZNWD4.route_scorer import score_routes

            eq = effective_question or question
            hf = extract_history_focus(eq)
            evidence = build_evidence_for_question(eq, driver=driver, lock=route_lock, history_focus=hf)
            scores = score_routes(eq, evidence, top_k=3)
            fb = _probe_fallback_plan(
                question=question,
                effective_question=eq,
                route_lock=route_lock,
                evidence=evidence,
                scores=scores,
                driver=driver,
                history_focus=hf,
                history_snapshot=history_snapshot,
                reason="planner_langchain_unavailable",
            )
            if fb:
                fb["routing_tier"] = "fallback"
                yield (ROUTE_PLAN_INTERNAL, {"plan": fb})
                return
        raise


def stream_action_plan_route(
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
    """ActionPlanner 主路由：grounding → LLM → validate → ROUTE_PLAN_INTERNAL。"""
    if route_clarify and isinstance(route_clarify, dict):
        orig = str(route_clarify.get("originalQuestion") or route_clarify.get("original_question") or "").strip()
        if orig:
            question = orig

    effective_question, history_snapshot = enrich_short_follow_up(
        question,
        user_id=user_id,
        session_id=session_id,
        client=client,
        model=model,
    )
    effective_question = str(effective_question or question or "").strip()
    route_lock = filter_lock_for_planner_mode(safe_resolve_route_lock(effective_question))

    yield emit_milestone("正在 Zvec 实体 grounding…", kind="route")
    grounding = ground_question(effective_question, driver=driver)
    yield emit_milestone("正在 LLM 动作规划…", kind="route")

    raw = ""
    plan: Optional[ActionPlan] = None
    errors: List[str] = []
    llm_calls = 0
    try:
        raw = _call_planner_llm(
            client, model, effective_question, grounding, llm_limits=llm_limits
        )
        llm_calls += 1
        plan = parse_action_plan_json(raw)
        if not plan.question:
            plan.question = effective_question
        errors, plan = validate_action_plan(
            plan,
            question=effective_question,
            candidates=grounding.get("candidates") or [],
        )
        if errors:
            logger.warning("[ZNWD5][planner] validation errors: %s", errors[:5])
            yield emit_milestone("动作规划校验未通过，正在修复…", kind="route")
            raw2 = _call_planner_llm(
                client,
                model,
                effective_question,
                grounding,
                llm_limits=llm_limits,
                repair=(raw, errors),
            )
            llm_calls += 1
            plan = parse_action_plan_json(raw2)
            if not plan.question:
                plan.question = effective_question
            errors, plan = validate_action_plan(
                plan,
                question=effective_question,
                candidates=grounding.get("candidates") or [],
            )
    except Exception as exc:
        logger.exception("[ZNWD5][planner] LLM/parse failed: %s", exc)
        if is_llm_unavailable_error(exc):
            yield from _yield_langchain_fallback(
                client,
                model,
                question,
                user_id=user_id,
                session_id=session_id,
                driver=driver,
                llm_limits=llm_limits,
                route_clarify=route_clarify,
                effective_question=effective_question,
                history_snapshot=history_snapshot,
                route_lock=route_lock,
            )
            return
        raise

    if errors or plan is None:
        fatal = validation_errors_fatal(errors)
        if not fatal and plan is not None and plan.total_step_count > 0:
            logger.warning(
                "[ZNWD5][planner] non-fatal validation warnings (%d), proceeding: %s",
                len(errors),
                errors[:3],
            )
            errors = []
        else:
            logger.warning("[ZNWD5][planner] fallback langchain after validation: %s", errors)
            yield from _yield_langchain_fallback(
                client,
                model,
                question,
                user_id=user_id,
                session_id=session_id,
                driver=driver,
                llm_limits=llm_limits,
                route_clarify=route_clarify,
                effective_question=effective_question,
                history_snapshot=history_snapshot,
                route_lock=route_lock,
            )
            return

    if plan is None or (errors and validation_errors_fatal(errors)):
        return

    route_plan = _plan_to_route_plan(
        plan,
        question=question,
        grounding=grounding,
        effective_question=effective_question,
        history_snapshot=history_snapshot,
        llm_calls=llm_calls,
    )
    event_id = log_routing_event(
        question=question,
        effective_question=effective_question,
        user_id=user_id,
        session_id=session_id,
        route_id="R-ACTION-PLANNER",
        meta={
            "tier": "L3",
            "system": "action_planner",
            "llm_calls": llm_calls,
            "steps": plan.total_step_count,
            "answer_mode": plan.answer_mode,
        },
        plan=route_plan,
    )
    if event_id:
        route_plan["routing_event_id"] = event_id

    yield emit_milestone("动作规划完成。", kind="route")
    yield (ROUTE_PLAN_INTERNAL, {"plan": route_plan})


def stream_route_plan_znwd5_dispatch(
    legacy_fn: Any,
    client: Any,
    model: str,
    question: str,
    **kwargs: Any,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """ZNWD5 三层路由分发：Tier0 legacy / Tier1 FAQ 锁 / Tier2 Action Planner。"""
    if znwd5_routing_mode() != "action_planner":
        yield from legacy_fn(client, model, question, **kwargs)
        return

    effective_q = str(question or "").strip()
    lock = filter_lock_for_planner_mode(safe_resolve_route_lock(effective_q))
    tier = lock_tier(lock)

    if not lock:
        guessed = guess_purpose_from_question(effective_q)
        if guessed in (PURPOSE_CHAT, PURPOSE_META):
            yield from legacy_fn(client, model, question, **kwargs)
            return

    if tier == "tier0" and should_use_action_planner_fast_path(lock):
        yield from legacy_fn(client, model, question, **kwargs)
        return

    if tier == "faq" and is_faq_whitelist_lock(lock, effective_q):
        from .faq_lock_routing import stream_faq_lock_route

        yield from stream_faq_lock_route(
            client,
            model,
            question,
            lock,  # type: ignore[arg-type]
            user_id=kwargs.get("user_id"),
            session_id=kwargs.get("session_id"),
            driver=kwargs.get("driver"),
            llm_limits=kwargs.get("llm_limits"),
            route_clarify=kwargs.get("route_clarify"),
        )
        return

    yield from stream_action_plan_route(client, model, question, **kwargs)


__all__ = [
    "planner_skip_locks",
    "should_use_action_planner_fast_path",
    "stream_action_plan_route",
    "stream_route_plan_znwd5_dispatch",
    "znwd5_routing_mode",
]
