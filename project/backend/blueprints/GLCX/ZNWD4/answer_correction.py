"""Post-answer correction hints and rerun application."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .route_choice_nlg import phrase_route_option
from .route_clarify import _merge_candidates
from .route_plan_v2 import normalize_to_legacy_plan
from .routing_catalog import get_route, resolve_route
from .znwd4_llm_errors import is_truthy

SLOT_LABELS: Dict[str, str] = {
    "equipment_keywords": "装备",
    "equipment_type_keywords": "装备类型",
    "equipment_type_parent": "一级装备类别",
    "environment_keywords": "试验环境",
    "project_keywords": "试验项目",
    "indicator_keywords": "考核指标",
    "standard_keywords": "标准",
    "focus": "主关键词",
}

FEEDBACK_KINDS: List[Dict[str, str]] = [
    {"id": "wrong_route", "label": "路由意图不对"},
    {"id": "wrong_slots", "label": "关键词/槽位不对"},
    {"id": "wrong_retrieval", "label": "检索结果不对"},
    {"id": "wrong_answer", "label": "答复表述不对"},
    {"id": "other", "label": "其他"},
]


def answer_feedback_enabled() -> bool:
    raw = (os.environ.get("ZNWD4_ANSWER_FEEDBACK") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _max_routes() -> int:
    try:
        return max(2, min(6, int(os.environ.get("ZNWD4_ANSWER_CORRECTION_MAX_ROUTES") or "4")))
    except ValueError:
        return 4


def _predicted_slots(plan: Dict[str, Any], rec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(rec, dict) and isinstance(rec.get("params"), dict):
        return dict(rec["params"])
    pd = plan.get("params_draft")
    return dict(pd) if isinstance(pd, dict) else {}


def _build_route_options(question: str, plan: Dict[str, Any], *, driver: Any) -> List[Dict[str, Any]]:
    predicted_rid = str(plan.get("route_id") or "").strip()
    seed: List[Tuple[str, float]] = []
    if predicted_rid:
        seed.append((predicted_rid, 1.0))
    merged = _merge_candidates(question, seed, driver=driver, max_options=_max_routes())
    options: List[Dict[str, Any]] = []
    for idx, (rid, conf) in enumerate(merged):
        entry = get_route(rid)
        if not entry:
            continue
        options.append(
            {
                "id": f"route_{idx + 1}",
                "routeId": rid,
                "label": phrase_route_option(question, rid, entry),
                "confidence": round(float(conf), 4),
            }
        )
    return options


def _build_slot_chips(slots: Dict[str, Any]) -> List[Dict[str, Any]]:
    chips: List[Dict[str, Any]] = []
    for key, label in SLOT_LABELS.items():
        val = slots.get(key)
        if isinstance(val, list) and val:
            chips.append(
                {
                    "slotKey": key,
                    "label": label,
                    "values": [str(x) for x in val if str(x).strip()],
                }
            )
        elif isinstance(val, str) and val.strip():
            chips.append({"slotKey": key, "label": label, "values": [val.strip()]})
    parent = slots.get("equipment_type_parent")
    if isinstance(parent, str) and parent.strip():
        if not any(c.get("slotKey") == "equipment_type_parent" for c in chips):
            chips.append(
                {"slotKey": "equipment_type_parent", "label": SLOT_LABELS["equipment_type_parent"], "values": [parent.strip()]}
            )
    return chips


def build_correction_hints(
    plan: Dict[str, Any],
    rec: Optional[Dict[str, Any]],
    question: str,
    *,
    driver: Any = None,
) -> Dict[str, Any]:
    if not answer_feedback_enabled():
        return {}
    predicted_rid = str(plan.get("route_id") or "").strip()
    entry = get_route(predicted_rid) if predicted_rid else None
    predicted_label = ""
    if entry:
        predicted_label = phrase_route_option(question, predicted_rid, entry)
    slots = _predicted_slots(plan, rec)
    return {
        "enabled": True,
        "feedbackKinds": FEEDBACK_KINDS,
        "predictedRouteId": predicted_rid,
        "predictedRouteLabel": predicted_label,
        "predictedSlots": slots,
        "slotChips": _build_slot_chips(slots),
        "routeOptions": _build_route_options(question, plan, driver=driver),
    }


def _turn_id(payload: Dict[str, Any]) -> str:
    return str(payload.get("turnId") or payload.get("turn_id") or "").strip()


def _original_question(payload: Dict[str, Any]) -> str:
    return str(payload.get("originalQuestion") or payload.get("original_question") or "").strip()


def validate_feedback_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload must be object"
    if not _turn_id(payload):
        return False, "turnId required"
    rating = str(payload.get("rating") or "").strip().lower()
    if rating not in ("positive", "negative"):
        return False, "rating must be positive or negative"
    if rating == "negative":
        kind = str(payload.get("feedbackKind") or payload.get("feedback_kind") or "").strip()
        if not kind:
            return False, "feedbackKind required for negative feedback"
    corrected_rid = str(payload.get("correctedRouteId") or payload.get("corrected_route_id") or "").strip()
    if corrected_rid and not resolve_route(corrected_rid):
        return False, f"unknown route: {corrected_rid}"
    return True, ""


def validate_correction_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate stream rerun body (`answerCorrection`); rating is optional (defaults negative)."""
    if not isinstance(payload, dict):
        return False, "payload must be object"
    if not _turn_id(payload):
        return False, "turnId required"
    if not _original_question(payload):
        return False, "originalQuestion required"
    if not str(payload.get("feedbackKind") or payload.get("feedback_kind") or "").strip():
        return False, "feedbackKind required"
    rating = str(payload.get("rating") or "negative").strip().lower()
    if rating not in ("positive", "negative"):
        return False, "rating must be positive or negative"
    corrected_rid = str(payload.get("correctedRouteId") or payload.get("corrected_route_id") or "").strip()
    if corrected_rid and not resolve_route(corrected_rid):
        return False, f"unknown route: {corrected_rid}"
    if is_truthy(payload.get("rerun")):
        if not corrected_rid:
            return False, "correctedRouteId required for rerun"
        if not resolve_route(corrected_rid):
            return False, f"unknown route: {corrected_rid}"
    return True, ""


def _keyword_slot_keys() -> Tuple[str, ...]:
    return (
        "equipment_keywords",
        "equipment_type_keywords",
        "environment_keywords",
        "project_keywords",
        "indicator_keywords",
        "standard_keywords",
        "method_keywords",
    )


def _slots_differ_from_predicted(corrected: Dict[str, Any], predicted: Dict[str, Any]) -> bool:
    for k in _keyword_slot_keys():
        if corrected.get(k) != predicted.get(k):
            return True
    return False


def reslot_for_route(
    question: str,
    route_id: str,
    *,
    existing_slots: Optional[Dict[str, Any]] = None,
    predicted_route_id: str = "",
    predicted_slots: Optional[Dict[str, Any]] = None,
    user_edited_slots: bool = False,
    driver: Any = None,
    lock: Any = None,
    evidence: Any = None,
) -> Dict[str, Any]:
    """换路由或澄清意图后，按新 route 从问句重抽槽位并剔除不兼容槽。"""
    from .question_route_lock import extract_project_keywords, resolve_route_lock

    entry = get_route(route_id)
    required = set(entry.required_slots if entry else [])
    route_changed = bool(predicted_route_id and route_id != predicted_route_id)
    slots = dict(existing_slots or {})

    if route_changed and not user_edited_slots:
        for k in list(_keyword_slot_keys()):
            if k not in required and k in slots:
                del slots[k]
        if "equipment_type_parent" in slots and "equipment_type_keywords" not in required:
            slots.pop("equipment_type_parent", None)

    if route_id in (
        "R-EQUIPMENT-by_project",
        "R-TEST_METHOD-by_project",
        "R-ENVIRONMENT-for_project",
        "R-STANDARD-for_projects",
    ):
        if not user_edited_slots or route_changed:
            proj = extract_project_keywords(question)
            if proj:
                slots["project_keywords"] = proj
                slots["focus"] = proj[0]
                slots["project_name"] = proj[0]

    if route_id == "R-EQUIPMENT-by_indicator":
        if not user_edited_slots or route_changed:
            from .question_route_lock import extract_indicator_keywords

            ind = extract_indicator_keywords(question)
            if ind:
                slots["indicator_keywords"] = ind
                slots["focus"] = ind[0]

    if route_id == "R-TEST_PROJECT-by_method":
        if not user_edited_slots or route_changed:
            from .question_route_lock import extract_method_keywords

            meth = extract_method_keywords(question)
            if meth:
                slots["method_keywords"] = meth
                slots["focus"] = meth[0]

    if driver:
        if lock is None:
            lock = resolve_route_lock(question)
        if evidence is None:
            from .pcsr_assembly import build_evidence_for_question

            evidence = build_evidence_for_question(question, driver=driver, lock=lock)
        from .pcsr_assembly import pcsr_slots

        slots = pcsr_slots(
            route_id=route_id,
            lock=lock,
            evidence=evidence,
            raw_slots=slots,
            question=question,
            preserve_filled=user_edited_slots and not route_changed,
        )
        from .pcsr_assembly import sanitize_composite_env_slots

        slots = sanitize_composite_env_slots(slots, lock=lock, question=question)

    return slots


def apply_answer_correction(
    payload: Dict[str, Any],
    *,
    question: str,
    driver: Any = None,
) -> Dict[str, Any]:
    """Build plan from user correction; skips LLM routing."""
    orig = _original_question(payload) or (question or "").strip()
    route_id = str(payload.get("correctedRouteId") or payload.get("corrected_route_id") or "").strip()
    if not route_id:
        raise ValueError("correctedRouteId required for rerun")

    resolved = resolve_route(route_id)
    if not resolved:
        raise ValueError(f"unknown route: {route_id}")

    corrected_slots = payload.get("correctedSlots") if "correctedSlots" in payload else None
    if corrected_slots is None and "corrected_slots" in payload:
        corrected_slots = payload.get("corrected_slots")
    if not isinstance(corrected_slots, dict):
        corrected_slots = {}

    predicted_slots_raw = payload.get("predictedSlots") or payload.get("predicted_slots") or {}
    if not isinstance(predicted_slots_raw, dict):
        predicted_slots_raw = {}
    predicted_route_id = str(
        payload.get("predictedRouteId") or payload.get("predicted_route_id") or ""
    ).strip()
    has_explicit_corrected = "correctedSlots" in payload or "corrected_slots" in payload
    user_edited_slots = bool(
        has_explicit_corrected and _slots_differ_from_predicted(corrected_slots, predicted_slots_raw)
    )
    if not corrected_slots and predicted_slots_raw:
        corrected_slots = dict(predicted_slots_raw)

    lock = None
    evidence = None
    probe_snapshot: Dict[str, Any] = {}
    if driver:
        from .pcsr_assembly import (
            build_evidence_for_question,
            evidence_to_probe_snapshot,
        )
        from .question_route_lock import resolve_route_lock

        lock = resolve_route_lock(orig)
        evidence = build_evidence_for_question(orig, driver=driver, lock=lock)
        if lock and lock.extra:
            for k, v in lock.extra.items():
                if v and (k not in corrected_slots or not corrected_slots.get(k)):
                    corrected_slots[k] = list(v) if isinstance(v, list) else [str(v)]
        corrected_slots = reslot_for_route(
            orig,
            route_id,
            existing_slots=corrected_slots,
            predicted_route_id=predicted_route_id,
            predicted_slots=predicted_slots_raw,
            user_edited_slots=user_edited_slots,
            driver=driver,
            lock=lock,
            evidence=evidence,
        )
        entry = get_route(route_id)
        label = ""
        if entry and entry.keyword_nodes:
            label = entry.keyword_nodes[0]
        probe_snapshot = evidence_to_probe_snapshot(evidence, label) if evidence else {}

    step1: Dict[str, Any] = {
        "purpose": resolved.get("purpose", ""),
        "keywords": [orig] if orig else [],
        "keyword": orig[:80],
    }
    step2: Dict[str, Any] = {
        "route_id": route_id,
        "params_draft": corrected_slots,
        "keywords": list(step1.get("keywords") or []),
        "focus": orig[:80],
        "match_confidence": "high",
        "confidence": 1.0,
    }

    if driver:
        from .route_validator import assemble_legacy_plan

        plan = assemble_legacy_plan(
            question=orig,
            step1=step1,
            step2=step2,
            probe_snapshot=probe_snapshot,
            probe_source="answer_correction",
            driver=driver,
            slot_source="user_correction" if user_edited_slots else "answer_correction",
        )
    else:
        v2: Dict[str, Any] = {
            "route_id": route_id,
            "purpose": resolved.get("purpose", ""),
            "match_confidence": "high",
            "retrieval_question": orig,
            "keyword": orig[:80],
            "params_draft": corrected_slots,
            "answer_strategy": resolved.get("answer_strategy", "db_synthesize"),
        }
        plan = normalize_to_legacy_plan(v2, question=orig, retrieval_question=orig)

    plan["match_confidence"] = "high"
    plan["user_answer_correction"] = True
    if lock and lock.extra.get("scenario"):
        plan["scenario"] = str(lock.extra["scenario"])
        if isinstance(plan.get("params_draft"), dict):
            plan["params_draft"]["scenario"] = plan["scenario"]
    plan["parent_turn_id"] = _turn_id(payload)
    plan["parent_routing_event_id"] = str(
        payload.get("routingEventId") or payload.get("routing_event_id") or ""
    )
    return plan


__all__ = [
    "SLOT_LABELS",
    "FEEDBACK_KINDS",
    "answer_feedback_enabled",
    "build_correction_hints",
    "validate_feedback_payload",
    "validate_correction_payload",
    "apply_answer_correction",
    "reslot_for_route",
]
