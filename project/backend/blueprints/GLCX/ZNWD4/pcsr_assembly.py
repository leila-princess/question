"""Probe-Canonicalized Slot Resolution (PCSR) — unified plan assembly."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .probe_preflight import EvidenceBundle, build_evidence_bundle
from .question_route_lock import (
    RouteLock,
    apply_route_lock_to_step1,
    build_locked_step2,
    locked_slot_values,
    resolve_route_lock,
)
from .routing_catalog import PURPOSE_CHAT, PURPOSE_ENUM, get_route

_SLOT_TO_LABEL = {
    "equipment_keywords": "Equipment",
    "environment_keywords": "Environment",
    "project_keywords": "TestProject",
    "indicator_keywords": "Indicator",
    "method_keywords": "TestMethod",
    "equipment_type_keywords": "EquipmentType",
    "standard_keywords": "File",
}


def extract_history_focus(text: str) -> str:
    m = re.search(r"【承前主体】\s*([^。；;\n]+)", text or "")
    return (m.group(1) or "").strip() if m else ""


def slot_key_to_label(slot_key: str) -> str:
    return _SLOT_TO_LABEL.get(slot_key, "")


def _project_explicit_in_question(question: str, slots: Dict[str, Any]) -> bool:
    q = re.sub(r"\s+", "", question or "")
    proj = slots.get("project_keywords")
    if not isinstance(proj, list) or not proj:
        return False
    name = str(proj[0]).strip()
    return bool(name and name in q)


def _lock_seed_keywords(lock: Optional[RouteLock]) -> List[str]:
    if not lock:
        return []
    seeds: List[str] = []
    seen: set = set()
    for kw in locked_slot_values(lock):
        if kw and kw not in seen:
            seen.add(kw)
            seeds.append(kw)
    extra = lock.extra if isinstance(lock.extra, dict) else {}
    for key in _SLOT_TO_LABEL:
        vals = extra.get(key)
        if not isinstance(vals, list):
            continue
        for v in vals:
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                seeds.append(s)
    return seeds


def _lock_typed_probe_targets(lock: Optional[RouteLock]) -> List[Tuple[str, str]]:
    """程序锁槽位 → (keyword, node_label) 定向 Zvec/CONTAINS probe。"""
    if not lock:
        return []
    targets: List[Tuple[str, str]] = []
    seen: set = set()
    extra = lock.extra if isinstance(lock.extra, dict) else {}
    for slot_key, label in _SLOT_TO_LABEL.items():
        vals = extra.get(slot_key)
        if not isinstance(vals, list) and lock.slot_key == slot_key:
            vals = locked_slot_values(lock)
        if not isinstance(vals, list):
            continue
        for raw in vals:
            kw = str(raw).strip()
            key = (kw, label)
            if kw and key not in seen:
                seen.add(key)
                targets.append(key)
    return targets


def _apply_slot_from_evidence(
    out: Dict[str, Any],
    *,
    slot_key: str,
    evidence: EvidenceBundle,
    fallback: Optional[List[str]] = None,
) -> None:
    label = slot_key_to_label(slot_key)
    canonical = evidence.best_name(label) if label else ""
    vals = [canonical] if canonical else [str(x).strip() for x in (fallback or []) if str(x).strip()]
    if not vals:
        return
    out[slot_key] = vals
    if not out.get("focus"):
        out["focus"] = vals[0]
    if slot_key == "project_keywords":
        out["project_name"] = vals[0]
    if slot_key == "environment_keywords":
        out["environment_value_list"] = vals
        hit_id = evidence.best_id(label) if label else ""
        if hit_id:
            out["environment_focus_id"] = hit_id
    if slot_key == "equipment_keywords":
        out["equipment_value_list"] = vals
        hit_id = evidence.best_id(label) if label else ""
        if hit_id:
            out["focus_id"] = hit_id


def sanitize_composite_env_slots(
    slots: Dict[str, Any],
    *,
    lock: Optional[RouteLock] = None,
    question: str = "",
) -> Dict[str, Any]:
    """by_environment 场景清除误带的 project_keywords（如与装备名相同）。"""
    out = dict(slots)
    scenario = str((lock.extra or {}).get("scenario") if lock else "") or ""
    if not scenario and out.get("environment_keywords") and not _project_explicit_in_question(question, out):
        scenario = "by_environment"
    if scenario == "by_environment":
        equip = out.get("equipment_keywords") or []
        equip0 = str(equip[0]).strip() if equip else ""
        proj = out.get("project_keywords")
        proj0 = str(proj[0]).strip() if isinstance(proj, list) and proj else ""
        if not _project_explicit_in_question(question, out) or (proj0 and equip0 and proj0 == equip0):
            out.pop("project_keywords", None)
            out.pop("project_name", None)
    if lock and lock.extra.get("scenario"):
        out["scenario"] = str(lock.extra["scenario"])
    return out


def pcsr_slots(
    *,
    route_id: str,
    lock: Optional[RouteLock],
    evidence: EvidenceBundle,
    raw_slots: Optional[Dict[str, Any]] = None,
    question: str = "",
    preserve_filled: bool = False,
) -> Dict[str, Any]:
    """Prefer probe canonical names over regex lock / LLM spans."""
    out = dict(raw_slots or {})
    entry = get_route(route_id)
    slot_keys = list(entry.required_slots if entry else [])
    if lock and lock.slot_key:
        slot_keys = [lock.slot_key] + [k for k in slot_keys if k != lock.slot_key]

    purpose = str(entry.purpose or "") if entry else ""
    if purpose == "composite_equip_project_env_settings":
        for slot_key, label in (
            ("equipment_keywords", "Equipment"),
            ("environment_keywords", "Environment"),
            ("project_keywords", "TestProject"),
        ):
            canonical = evidence.best_name(label) if label else ""
            if canonical:
                out[slot_key] = [canonical]
                if slot_key == "project_keywords":
                    out["project_name"] = canonical
                if slot_key == "environment_keywords":
                    out["environment_value_list"] = [canonical]
                    hit_id = evidence.best_id(label) if label else ""
                    if hit_id:
                        out["environment_focus_id"] = hit_id
                if slot_key == "equipment_keywords":
                    out["equipment_value_list"] = [canonical]
                    hit_id = evidence.best_id(label) if label else ""
                    if hit_id:
                        out["focus_id"] = hit_id
        if out.get("equipment_keywords"):
            out["focus"] = out["equipment_keywords"][0]
        elif out.get("environment_keywords"):
            out["focus"] = out["environment_keywords"][0]
        if lock and lock.extra:
            for k, v in lock.extra.items():
                if v and k.endswith("_keywords") and k not in out:
                    out[k] = list(v) if isinstance(v, list) else [str(v)]
        return sanitize_composite_env_slots(out, lock=lock, question=question)

    for slot_key in slot_keys:
        if not slot_key.endswith("_keywords"):
            continue
        label = slot_key_to_label(slot_key)
        canonical = evidence.best_name(label) if label else ""
        if canonical:
            if preserve_filled:
                existing = out.get(slot_key)
                if isinstance(existing, list) and any(str(x).strip() for x in existing):
                    continue
                if isinstance(existing, str) and existing.strip():
                    continue
            out[slot_key] = [canonical]
            out["focus"] = canonical
            if slot_key == "project_keywords":
                out["project_name"] = canonical
            if slot_key == "environment_keywords":
                out["environment_value_list"] = [canonical]
                hit_id = evidence.best_id(label) if label else ""
                if hit_id:
                    out["environment_focus_id"] = hit_id
            if slot_key == "equipment_keywords":
                out["equipment_value_list"] = [canonical]
                hit_id = evidence.best_id(label) if label else ""
                if hit_id:
                    out["focus_id"] = hit_id
            continue
        if lock and lock.slot_key == slot_key:
            vals = locked_slot_values(lock)
            if vals:
                out[slot_key] = vals
                out["focus"] = vals[0]

    if lock and isinstance(lock.extra, dict):
        for slot_key in _SLOT_TO_LABEL:
            if out.get(slot_key):
                continue
            fallback = lock.extra.get(slot_key)
            fb_list = list(fallback) if isinstance(fallback, list) else None
            _apply_slot_from_evidence(out, slot_key=slot_key, evidence=evidence, fallback=fb_list)

    return out


def evidence_to_probe_snapshot(evidence: EvidenceBundle, label: str = "") -> Dict[str, Any]:
    if not evidence or not evidence.best_by_label:
        return {"zvec_used": bool(evidence and evidence.zvec_used)}
    base: Dict[str, Any] = {"zvec_used": bool(evidence.zvec_used)}
    if label and label in evidence.best_by_label:
        best = evidence.best_by_label[label]
        return {**base, "best": best, "hits": evidence.hits[:5]}
    if len(evidence.best_by_label) == 1:
        best = next(iter(evidence.best_by_label.values()))
        return {**base, "best": best, "hits": evidence.hits[:5]}
    for lbl in evidence.best_by_label:
        best = evidence.best_by_label[lbl]
        if best:
            return {**base, "best": best, "hits": evidence.hits[:5]}
    return base


def lock_probe_has_primary_hit(lock: RouteLock, evidence: EvidenceBundle) -> bool:
    """程序锁对应主槽位已在 probe 中命中（可 0 LLM，不必等 ECS 高置信）。"""
    if not lock or not lock.slot_key or not evidence.probe_ran:
        return False
    label = slot_key_to_label(lock.slot_key)
    return bool(label and evidence.best_name(label))


def slots_ready_for_route(route_id: str, evidence: EvidenceBundle) -> bool:
    """ECS 快路径：required_slots 对应 label 均已在 probe 中命中。"""
    entry = get_route(route_id)
    if not entry:
        return False
    req = [s for s in (entry.required_slots or []) if s.endswith("_keywords")]
    if not req:
        return True
    if not evidence.probe_ran:
        return False
    for slot in req:
        label = slot_key_to_label(slot)
        if label and label not in evidence.best_by_label:
            return False
    return True


def build_evidence_for_question(
    question: str,
    *,
    driver: Any = None,
    lock: Optional[RouteLock] = None,
    history_focus: str = "",
) -> EvidenceBundle:
    hints = list(lock.probe_labels) if lock and lock.probe_labels else None
    seed_keywords = _lock_seed_keywords(lock)
    typed_probes = _lock_typed_probe_targets(lock)
    force_zvec = bool(lock and (lock.slot_key or typed_probes))
    return build_evidence_bundle(
        question,
        driver=driver,
        label_hints=hints,
        history_focus=history_focus,
        seed_keywords=seed_keywords or None,
        typed_probes=typed_probes or None,
        force_zvec=force_zvec,
    )


def finalize_locked_plan(
    *,
    question: str,
    lock: RouteLock,
    driver: Any = None,
    history_focus: str = "",
    history_snapshot: Optional[Dict[str, Any]] = None,
    probe_source: str = "pcsr",
) -> Dict[str, Any]:
    """Probe-first assembly for route locks (entity + enum)."""
    evidence = build_evidence_for_question(
        question,
        driver=driver,
        lock=lock,
        history_focus=history_focus,
    )
    step1 = apply_route_lock_to_step1({}, lock, question)
    raw_slots = pcsr_slots(route_id=lock.route_id, lock=lock, evidence=evidence, raw_slots={}, question=question)
    step2 = build_locked_step2(lock)
    step2["params_draft"] = raw_slots
    step2["focus"] = str(raw_slots.get("focus") or "")
    step2["keywords"] = list(raw_slots.get(f"{lock.slot_key}", [])) if lock.slot_key else []

    label = slot_key_to_label(lock.slot_key) if lock.slot_key else ""
    probe_snapshot = evidence_to_probe_snapshot(evidence, label)

    from .route_validator import assemble_legacy_plan

    return assemble_legacy_plan(
        question=question,
        step1=step1,
        step2=step2,
        probe_snapshot=probe_snapshot,
        probe_source=probe_source,
        driver=driver,
        history_snapshot=history_snapshot,
    )


def finalize_ecs_plan(
    *,
    question: str,
    route_id: str,
    purpose: str,
    evidence: EvidenceBundle,
    driver: Any = None,
    history_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """0-LLM ECS fast path plan assembly."""
    slots = pcsr_slots(route_id=route_id, lock=None, evidence=evidence, raw_slots={"focus": ""}, question=question)
    step1 = {"purpose": purpose, "keywords": []}
    if slots.get("focus"):
        step1["keyword"] = slots["focus"]
        step1["keywords"] = [slots["focus"]]
    step2 = {
        "route_id": route_id,
        "params_draft": slots,
        "focus": slots.get("focus", ""),
        "keywords": step1.get("keywords", []),
        "match_reason": "ecs_fast_path",
    }
    entry = get_route(route_id)
    label = ""
    if entry and entry.keyword_nodes:
        label = entry.keyword_nodes[0]
    probe_snapshot = evidence_to_probe_snapshot(evidence, label)
    from .route_validator import assemble_legacy_plan

    return assemble_legacy_plan(
        question=question,
        step1=step1,
        step2=step2,
        probe_snapshot=probe_snapshot,
        probe_source="ecs",
        driver=driver,
        history_snapshot=history_snapshot,
    )


def apply_pcsr_to_step2(
    *,
    question: str,
    route_id: str,
    step1: Dict[str, Any],
    step2: Dict[str, Any],
    driver: Any = None,
    history_focus: str = "",
) -> Tuple[Dict[str, Any], EvidenceBundle]:
    lock = resolve_route_lock(question)
    evidence = build_evidence_for_question(
        question,
        driver=driver,
        lock=lock,
        history_focus=history_focus,
    )
    raw = step2.get("params_draft") if isinstance(step2.get("params_draft"), dict) else {}
    if not raw:
        from .route_plan_v2 import merge_slots_from_keywords

        raw = merge_slots_from_keywords(step1, step2, route_id)
    slots = pcsr_slots(route_id=route_id, lock=lock, evidence=evidence, raw_slots=raw, question=question)
    out_step2 = dict(step2)
    out_step2["params_draft"] = slots
    if slots.get("focus"):
        out_step2["focus"] = slots["focus"]
    return out_step2, evidence


def finalize_intent_plan(
    plan: "IntentPlan",
    *,
    driver: Any = None,
    history_snapshot: Optional[Dict[str, Any]] = None,
    history_focus: str = "",
) -> Dict[str, Any]:
    """Build legacy RoutePlan from unified IntentPlan (multi-step / anchored chain)."""
    from .intent_plan import IntentPlan
    from .intent_plan_expand import build_legacy_plan_from_intent

    if not isinstance(plan, IntentPlan):
        raise TypeError("finalize_intent_plan expects IntentPlan")
    evidence = build_evidence_for_question(
        plan.question,
        driver=driver,
        lock=None,
        history_focus=history_focus,
    )
    probe_snapshot = evidence_to_probe_snapshot(evidence, "Equipment")
    legacy = build_legacy_plan_from_intent(
        plan,
        driver=driver,
        history_snapshot=history_snapshot,
        probe_snapshot=probe_snapshot,
    )
    draft = legacy.get("params_draft") if isinstance(legacy.get("params_draft"), dict) else {}
    if draft.get("equipment_keywords"):
        slots = pcsr_slots(
            route_id=str(legacy.get("route_id") or ""),
            lock=None,
            evidence=evidence,
            raw_slots=draft,
            question=plan.question,
            preserve_filled=True,
        )
        legacy["params_draft"] = slots
        legacy["keyword"] = str(slots.get("focus") or legacy.get("keyword") or "")
    return legacy


__all__ = [
    "extract_history_focus",
    "slot_key_to_label",
    "lock_probe_has_primary_hit",
    "sanitize_composite_env_slots",
    "pcsr_slots",
    "evidence_to_probe_snapshot",
    "slots_ready_for_route",
    "build_evidence_for_question",
    "finalize_locked_plan",
    "finalize_ecs_plan",
    "finalize_intent_plan",
    "apply_pcsr_to_step2",
]
