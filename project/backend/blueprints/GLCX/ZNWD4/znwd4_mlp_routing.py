"""ZNWD4 MLP+LLM-ask routing stream (SSE milestones compatible with legacy langchain routing)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Generator, Optional, Tuple

from .learned_router_bundle import load_mlp_bundle, predict_mlp_route_topk
from .mlp_llm_ask_router import route_question_mlp_llm_ask
from .route_lock_resolver import safe_resolve_route_lock
from .question_route_lock import (
    apply_route_lock_to_step1,
    build_locked_step2,
    skip_llm_for_lock,
)
from .pcsr_assembly import finalize_locked_plan
from .route_clarify import (
    ROUTE_CLARIFY_EVENT,
    build_route_clarify_payload,
    needs_route_clarify,
    route_clarify_enabled,
)
from .route_validator import assemble_legacy_plan
from .routing_catalog import PURPOSE_CHAT, _purpose_zh
from .routing_telemetry import log_routing_clarify_prompt, log_routing_event, log_user_route_choice
from .znwd4_history_context import enrich_short_follow_up
from .znwd4_langchain_routing import ROUTE_PLAN_INTERNAL
from .znwd4_thinking_emit import emit_milestone, milestone_route_step1, milestone_route_step2

logger = logging.getLogger(__name__)


def _purpose_zh_for_lock(lock: Any) -> str:
    return _purpose_zh(lock.purpose)


def _assemble_and_log(
    *,
    question: str,
    effective_question: str,
    user_id: Optional[str],
    session_id: Optional[str],
    step1: Dict[str, Any],
    step2: Dict[str, Any],
    rid: str,
    meta: Dict[str, Any],
    slots: Optional[Dict[str, Any]],
    driver: Any,
    history_snapshot_pre: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    plan = assemble_legacy_plan(
        question=effective_question,
        step1=step1,
        step2=step2,
        probe_snapshot={},
        probe_source="mlp_llm_ask",
        driver=driver,
        history_snapshot=history_snapshot_pre,
    )
    event_id = log_routing_event(
        question=question,
        effective_question=effective_question,
        user_id=user_id,
        session_id=session_id,
        route_id=rid,
        meta=meta,
        step1=step1,
        step2=step2,
        plan=plan,
        slots=slots,
    )
    if event_id:
        plan["routing_event_id"] = event_id
    return plan


def stream_route_plan_mlp(
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
    user_route_id = None
    clarify_event_id = None
    if route_clarify and isinstance(route_clarify, dict):
        user_route_id = str(route_clarify.get("selectedRouteId") or route_clarify.get("selected_route_id") or "").strip() or None
        clarify_event_id = str(route_clarify.get("clarifyEventId") or route_clarify.get("clarify_event_id") or "").strip() or None
        orig = str(route_clarify.get("originalQuestion") or route_clarify.get("original_question") or "").strip()
        if orig:
            question = orig

    history_snapshot_pre: Dict[str, Any] = {}
    if user_route_id:
        effective_question = question
    else:
        if user_id and session_id:
            from .memory_store import get_memory_store
            from .znwd4_llm_context import llm_context_enrich_enabled

            _store = get_memory_store()
            if (
                llm_context_enrich_enabled()
                and _store
                and _store.get_message_count(user_id, session_id) > 0
            ):
                yield emit_milestone("【上下文】正在结合对话历史与摘要理解问题…", kind="route")

        effective_question, history_snapshot_pre = enrich_short_follow_up(
            question,
            user_id=user_id,
            session_id=session_id,
            client=client,
            model=model,
        )
        effective_question = str(effective_question or question or "").strip()
        if effective_question != question:
            yield emit_milestone(f"结合上下文，理解为「{effective_question}」…", kind="route")

    route_lock = safe_resolve_route_lock(effective_question)
    if route_lock and skip_llm_for_lock(route_lock) and not user_route_id:
        yield emit_milestone(
            f"程序意图锁：{_purpose_zh_for_lock(route_lock)}（{route_lock.route_id}）",
            kind="route",
        )
        yield emit_milestone("MLP 路由：程序锁直出…", kind="route")
        plan = finalize_locked_plan(
            question=effective_question,
            lock=route_lock,
            driver=driver,
            history_snapshot=history_snapshot_pre,
            probe_source="lock_slotless",
        )
        event_id = log_routing_event(
            question=question,
            effective_question=effective_question,
            user_id=user_id,
            session_id=session_id,
            route_id=route_lock.route_id,
            meta={"tier": "L0", "system": "mlp_llm_ask", "route_lock": route_lock.reason},
            step1=apply_route_lock_to_step1({}, route_lock, effective_question),
            step2=build_locked_step2(route_lock),
            plan=plan,
        )
        if event_id:
            plan["routing_event_id"] = event_id
        yield emit_milestone("检索方案已确定。", kind="route")
        yield (ROUTE_PLAN_INTERNAL, {"plan": plan})
        return

    if user_route_id:
        yield emit_milestone("已收到您的选择，正在按您确认的业务意图继续检索…", kind="route")
        if clarify_event_id:
            log_user_route_choice(
                clarify_event_id=clarify_event_id,
                gold_route_id=user_route_id,
                question=question,
                user_id=user_id,
                session_id=session_id,
                selected_label=str(
                    route_clarify.get("selectedLabel") or route_clarify.get("selected_label") or ""
                ),
            )
    else:
        yield emit_milestone("【MLP】正在匹配业务方案…", kind="route")

    bundle = load_mlp_bundle()
    if not user_route_id and route_clarify_enabled():
        topk = predict_mlp_route_topk(effective_question, bundle, top_k=4)
        if needs_route_clarify(topk):
            payload = build_route_clarify_payload(effective_question, driver=driver, mlp_topk=topk)
            if payload.get("options"):
                log_routing_clarify_prompt(
                    payload=payload,
                    question=question,
                    effective_question=effective_question,
                    user_id=user_id,
                    session_id=session_id,
                    meta={"mlp_topk": topk},
                )
                yield emit_milestone("需要您帮忙确认一下具体想了解哪类信息…", kind="route")
                yield (ROUTE_CLARIFY_EVENT, payload)
                return

    rid, purpose, slots, meta, step1, step2 = route_question_mlp_llm_ask(
        effective_question,
        client=client,
        model=model,
        driver=driver,
        llm_limits=llm_limits,
        bundle=bundle,
        user_route_id=user_route_id,
    )

    if user_route_id:
        meta["gold_route_id"] = user_route_id
        meta["label_source"] = "user_choice"

    mlp_conf = meta.get("mlp_confidence")
    if not user_route_id:
        conf_txt = f"（置信度 {mlp_conf:.0%}）" if isinstance(mlp_conf, (int, float)) else ""
        yield emit_milestone(f"MLP 路由：{rid}{conf_txt}", kind="route")

    if purpose == PURPOSE_CHAT:
        ms1 = milestone_route_step1("chat")
        if ms1:
            yield emit_milestone(ms1, kind="route")
        plan = _assemble_and_log(
            question=question,
            effective_question=effective_question,
            user_id=user_id,
            session_id=session_id,
            step1=step1,
            step2=step2,
            rid=rid,
            meta=meta,
            slots=slots,
            driver=driver,
            history_snapshot_pre=history_snapshot_pre,
        )
        yield emit_milestone("检索方案已确定：闲聊直答。", kind="route")
        yield (ROUTE_PLAN_INTERNAL, {"plan": plan})
        return

    if meta.get("llm_calls"):
        yield emit_milestone("【LLM】正在按槽位识别实体…", kind="route")

    ms1 = milestone_route_step1(step1.get("purpose_zh") or step1.get("purpose", ""))
    if ms1:
        yield emit_milestone(ms1, kind="route")

    ms2 = milestone_route_step2(step2.get("route_id", ""), step2.get("match_reason", ""))
    if ms2:
        yield emit_milestone(ms2, kind="route")

    yield emit_milestone("【Probe】正在验证关键词并组装检索方案…", kind="route")
    plan = _assemble_and_log(
        question=question,
        effective_question=effective_question,
        user_id=user_id,
        session_id=session_id,
        step1=step1,
        step2=step2,
        rid=rid,
        meta=meta,
        slots=slots,
        driver=driver,
        history_snapshot_pre=history_snapshot_pre,
    )
    yield emit_milestone("检索方案已确定。", kind="route")
    yield (ROUTE_PLAN_INTERNAL, {"plan": plan})


def run_route_plan_mlp(client: Any, model: str, question: str, **kwargs: Any) -> Tuple[Dict[str, Any], list]:
    plan: Dict[str, Any] = {}
    notes: list = []
    for ev, data in stream_route_plan_mlp(client, model, question, **kwargs):
        if ev == ROUTE_PLAN_INTERNAL:
            plan = data.get("plan") or {}
        elif ev == "thinking":
            notes.append(str(data.get("content") or ""))
    return plan, notes


__all__ = ["stream_route_plan_mlp", "run_route_plan_mlp", "ROUTE_CLARIFY_EVENT"]
