"""
Tier1 FAQ 程序锁路由：规则锁 → Zvec probe → LLM 槽位校验 → finalize_locked_plan。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Generator, Optional, Tuple

from blueprints.GLCX.ZNWD4.pcsr_assembly import (
    build_evidence_for_question,
    extract_history_focus,
    finalize_locked_plan,
    pcsr_slots,
)
from blueprints.GLCX.ZNWD4.question_route_lock import RouteLock
from blueprints.GLCX.ZNWD4.routing_telemetry import log_routing_event
from blueprints.GLCX.ZNWD4.znwd4_history_context import enrich_short_follow_up
from blueprints.GLCX.ZNWD4.znwd4_langchain_routing import ROUTE_PLAN_INTERNAL
from blueprints.GLCX.ZNWD4.znwd4_thinking_emit import emit_milestone

from .faq_slot_verifier import verify_faq_slots

logger = logging.getLogger(__name__)

_SLOT_KEYS = (
    "equipment_keywords",
    "environment_keywords",
    "project_keywords",
    "indicator_keywords",
    "method_keywords",
    "equipment_type_keywords",
    "standard_keywords",
)


def _draft_slots_confirmed_by_evidence(
    lock: RouteLock,
    evidence: Any,
    draft_slots: Dict[str, Any],
) -> bool:
    """PCSR 槽位与 probe 一致时，LLM 误判仍走 FAQ。"""
    if not getattr(evidence, "probe_ran", False):
        return False
    from blueprints.GLCX.ZNWD4.pcsr_assembly import _SLOT_TO_LABEL
    from blueprints.GLCX.ZNWD4.routing_catalog import get_route

    entry = get_route(lock.route_id)
    req = [s for s in (entry.required_slots if entry else []) if str(s).endswith("_keywords")]
    if not req:
        return bool(draft_slots.get("focus"))
    for sk in req:
        vals = draft_slots.get(sk)
        if not isinstance(vals, list) or not any(str(x).strip() for x in vals):
            return False
        label = _SLOT_TO_LABEL.get(sk, "")
        canonical = evidence.best_name(label) if label else ""
        if not canonical:
            return False
        draft0 = str(vals[0]).strip()
        if canonical not in draft0 and draft0 not in canonical:
            return False
    return True


def stream_faq_lock_route(
    client: Any,
    model: str,
    question: str,
    lock: RouteLock,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    driver: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
    route_clarify: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """FAQ 白名单程序锁：Zvec 填槽 + 轻量 LLM 校验 + Playbook 计划。"""
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
    history_focus = extract_history_focus(effective_question)

    yield emit_milestone(
        f"FAQ 程序锁：{lock.route_id}（{lock.reason}）",
        kind="route",
    )
    yield emit_milestone("正在 Zvec 实体 grounding…", kind="route")

    evidence = build_evidence_for_question(
        effective_question,
        driver=driver,
        lock=lock,
        history_focus=history_focus,
    )

    draft_slots = pcsr_slots(
        route_id=lock.route_id,
        lock=lock,
        evidence=evidence,
        raw_slots={},
        question=effective_question,
    )
    if lock.slot_key and not draft_slots.get(lock.slot_key):
        from blueprints.GLCX.ZNWD4.slot_focus import extract_equipment_focus, extract_environment_focus

        if lock.slot_key == "equipment_keywords":
            ef = extract_equipment_focus(effective_question) or str(lock.focus or "").strip()
            if ef:
                draft_slots["equipment_keywords"] = [ef]
                draft_slots["equipment_value_list"] = [ef]
                draft_slots["focus"] = ef
        elif lock.slot_key == "environment_keywords":
            env = extract_environment_focus(effective_question) or str(lock.focus or "").strip()
            if env:
                from blueprints.GLCX.ZNWD4.slot_focus import expand_environment_keywords

                draft_slots["environment_keywords"] = expand_environment_keywords(env)[:3]
                draft_slots["environment_value_list"] = draft_slots["environment_keywords"]
                draft_slots["focus"] = env

    yield emit_milestone("正在 LLM 校验槽位…", kind="route")
    ok, merged_slots, _raw = verify_faq_slots(
        client,
        model,
        effective_question,
        lock,
        evidence,
        draft_slots,
        llm_limits=llm_limits,
    )

    if not ok:
        doc_reasons = {"document_chapter", "document_keyword", "standard_metadata", "standard_condition_param"}
        if lock.reason in doc_reasons and (draft_slots.get("standard_keywords") or lock.focus):
            ok = True
            merged_slots = dict(draft_slots)
            if lock.focus and not merged_slots.get("standard_keywords"):
                merged_slots["standard_keywords"] = [lock.focus]
                merged_slots["focus"] = lock.focus
        elif _draft_slots_confirmed_by_evidence(lock, evidence, draft_slots):
            ok = True
            merged_slots = dict(draft_slots)
    if not ok:
        logger.info("[ZNWD5][faq_lock] slot verify failed, escalate to action_planner q=%r", question[:60])
        yield emit_milestone("槽位校验未通过，转 Action Planner…", kind="route")
        from .action_planner_routing import stream_action_plan_route

        yield from stream_action_plan_route(
            client,
            model,
            question,
            user_id=user_id,
            session_id=session_id,
            driver=driver,
            llm_limits=llm_limits,
            route_clarify=route_clarify,
        )
        return

    plan = finalize_locked_plan(
        question=effective_question,
        lock=lock,
        driver=driver,
        history_focus=history_focus,
        history_snapshot=history_snapshot,
        probe_source="faq_lock",
    )
    if isinstance(plan.get("params_draft"), dict):
        plan["params_draft"].update(
            {k: v for k, v in merged_slots.items() if v is not None}
        )
    plan["source"] = "faq_lock"
    plan["routing_tier"] = "faq"
    plan["faq_slot_verified"] = True
    plan["zvec_used"] = bool(evidence.zvec_used)
    plan["llm_routing_calls"] = 1
    probe_snap = plan.get("probe_snapshot") if isinstance(plan.get("probe_snapshot"), dict) else {}
    probe_snap["zvec_used"] = bool(evidence.zvec_used)
    plan["probe_snapshot"] = probe_snap

    event_id = log_routing_event(
        question=question,
        effective_question=effective_question,
        user_id=user_id,
        session_id=session_id,
        route_id=lock.route_id,
        meta={
            "tier": "L1",
            "system": "faq_lock",
            "route_lock": lock.reason,
            "llm_calls": 1,
            "zvec_used": evidence.zvec_used,
        },
        plan=plan,
    )
    if event_id:
        plan["routing_event_id"] = event_id

    yield emit_milestone("FAQ 检索方案已确定。", kind="route")
    yield (ROUTE_PLAN_INTERNAL, {"plan": plan})


__all__ = ["stream_faq_lock_route"]
