"""Expand IntentPlan to meta_step_groups and legacy RoutePlan fields."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .intent_plan import (
    CHAIN_EQUIP_PROJECT_METHOD,
    GOAL_REGISTRY,
    IntentPlan,
    IntentStep,
    intent_plan_to_dict,
)
from .playbook_resolver import expand_business
from .user_biz_catalog import COMP_INTENT_PLAN

logger = logging.getLogger(__name__)


def _rel_eq_tp_params(slots: Dict[str, Any]) -> Dict[str, Any]:
    eq = slots.get("equipment_keywords") or []
    focus = str(slots.get("focus") or (eq[0] if eq else "")).strip()
    return {
        "from_type": "Equipment",
        "to_type": "TestProject",
        "rel_type": "CON",
        "where_side": "from",
        "match_mode": "fuzzy",
        "focus": focus,
        "limit": 500,
    }


def _rel_eq_ind_params(slots: Dict[str, Any]) -> Dict[str, Any]:
    eq = slots.get("equipment_keywords") or []
    focus = str(slots.get("focus") or (eq[0] if eq else "")).strip()
    return {
        "from_type": "Equipment",
        "to_type": "Indicator",
        "rel_type": "CON",
        "where_side": "from",
        "match_mode": "fuzzy",
        "focus": focus,
        "limit": 200,
    }


def _expand_equip_project_method_chain(step: IntentStep) -> List[List[Dict[str, Any]]]:
    """N:1 anchored chain: equip→project → filter project in results → project Id→method."""
    slots = {**step.slots, **step.anchor_slots}
    equip = str(slots.get("focus") or "").strip()
    proj_hints = step.filter_hints.get("project_keywords") or slots.get("project_keywords") or []
    proj_kw = str(proj_hints[0]).strip() if proj_hints else ""

    groups: List[List[Dict[str, Any]]] = []
    g1_raw = expand_business("BIZ_REL_ONE_HOP", _rel_eq_tp_params(slots))
    if g1_raw:
        g1 = []
        for st in g1_raw:
            s = dict(st)
            s["step_id"] = "s1"
            s["purpose"] = f"装备「{equip}」关联试验项目"
            g1.append(s)
        groups.append(g1)

    if proj_kw:
        groups.append([
            {
                "step_id": "s1b",
                "op_code": "neo_property_filter",
                "params": {
                    "target": "m",
                    "field": "Name",
                    "value": proj_kw,
                    "match_mode": "contains",
                },
                "purpose": f"在装备关联项目中定位「{proj_kw}」",
            }
        ])

    ref_step = "s1b" if proj_kw else "s1"
    groups.append([
        {
            "step_id": "s2",
            "op_code": "neo_relation_query",
            "params": {
                "from_type": "TestProject",
                "to_type": "TestMethod",
                "rel_type": "CON",
                "where_side": "from",
                "match_mode": "in",
                "from_field": "Id",
                "from_values_ref": {"step_id": ref_step, "target": "n", "field": "Id"},
                "limit": 200,
            },
            "purpose": f"项目「{proj_kw or equip}」关联试验方法",
        }
    ])
    return groups


def _expand_parallel_step(step: IntentStep, *, idx: int) -> List[List[Dict[str, Any]]]:
    sid = step.step_id or f"intent{idx}"
    slots = {**step.slots, **step.anchor_slots}
    equip = str(slots.get("focus") or "").strip()
    spec = GOAL_REGISTRY.get(step.goal)

    if step.goal == "test_projects":
        g_raw = expand_business("BIZ_REL_ONE_HOP", _rel_eq_tp_params(slots))
        if g_raw:
            g = []
            for st in g_raw:
                s = dict(st)
                s["step_id"] = sid
                s["purpose"] = f"装备「{equip}」关联试验项目"
                g.append(s)
            return [g]
    if step.goal == "indicators":
        g_raw = expand_business("BIZ_REL_ONE_HOP", _rel_eq_ind_params(slots))
        if g_raw:
            g = []
            for st in g_raw:
                s = dict(st)
                s["step_id"] = sid
                s["purpose"] = f"装备「{equip}」关联考核指标"
                g.append(s)
            return [g]
    if step.goal == "methods" and step.chain == CHAIN_EQUIP_PROJECT_METHOD:
        return _expand_equip_project_method_chain(step)

    title = spec.title if spec else step.goal
    logger.warning("[ZNWD4][intent_expand] unsupported goal=%s", step.goal)
    return [[{"step_id": sid, "op_code": "skip", "params": {}, "purpose": title, "skip_db": True}]]


def expand_intent_plan_meta_groups(plan: IntentPlan) -> List[List[Dict[str, Any]]]:
    groups: List[List[Dict[str, Any]]] = []
    if len(plan.steps) == 1 and plan.steps[0].chain == CHAIN_EQUIP_PROJECT_METHOD:
        return _expand_equip_project_method_chain(plan.steps[0])
    for i, step in enumerate(plan.steps):
        groups.extend(_expand_parallel_step(step, idx=i + 1))
    return groups


def intent_plan_milestone(plan: IntentPlan) -> str:
    titles = []
    for s in plan.steps:
        spec = GOAL_REGISTRY.get(s.goal)
        titles.append(spec.title if spec else s.goal)
    if len(titles) == 1 and plan.steps[0].chain:
        return f"理解为您要查：{titles[0]}（经装备定位项目后再查方法）"
    return f"理解为您要查 {'、'.join(titles)}（共 {len(plan.steps)} 步）"


def build_legacy_plan_from_intent(
    plan: IntentPlan,
    *,
    driver: Any = None,
    history_snapshot: Optional[Dict[str, Any]] = None,
    probe_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Produce RoutePlan dict consumed by plan_reconcile / pipeline."""
    meta_groups = expand_intent_plan_meta_groups(plan)
    merged_slots: Dict[str, Any] = {}
    for st in plan.steps:
        merged_slots.update(st.slots)

    first = plan.steps[0]
    kw = str(merged_slots.get("focus") or "").strip()
    if not kw:
        eq = merged_slots.get("equipment_keywords")
        if isinstance(eq, list) and eq:
            kw = str(eq[0]).strip()

    return {
        "route_id": f"R-{COMP_INTENT_PLAN.replace('COMP_', 'C-')}-default",
        "purpose": "composite_intent_plan",
        "purpose_node": "",
        "user_biz": COMP_INTENT_PLAN,
        "user_biz_title": "多意图检索",
        "scenario": "default",
        "match_confidence": "high",
        "answer_strategy": "db_direct",
        "keyword": kw,
        "params_draft": merged_slots,
        "probe_snapshot": probe_snapshot or {},
        "retrieval_question": plan.question,
        "intent_plan": intent_plan_to_dict(plan),
        "intent_plan_expanded": True,
        "playbook": True,
        "meta_step_groups": meta_groups,
        "multi_step": True,
        "business_id": COMP_INTENT_PLAN,
        "history_snapshot": history_snapshot or {},
    }
