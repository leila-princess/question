"""
ZNWD5 ActionPlan 展开：逐步构造 RoutePlan 片段 → resolve_business_from_plan → expand_business。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from blueprints.GLCX.ZNWD4.business_catalog import BIZ_DOCUMENT, BIZ_ENUM_NODES, BIZ_REL_ONE_HOP, BIZ_REL_REVERSE_ONE_HOP
from blueprints.GLCX.ZNWD4.business_catalog_v2 import resolve_business_from_plan
from blueprints.GLCX.ZNWD4.business_expand import expand_business

from .action_plan_schema import ActionPlan, ActionStep, SubQuestion

logger = logging.getLogger(__name__)


def _use_prev_to_ref(step: ActionStep, *, known_type: str, purpose_node: str) -> Optional[Dict[str, Any]]:
    if not step.use_prev:
        return None
    ref = step.use_prev
    side = ref.side
    if side == "to":
        target = "m"
    else:
        target = "n"
    if known_type == "Equipment" and purpose_node == "TestProject":
        target = "m"
    if known_type == "TestProject" and purpose_node == "TestMethod":
        target = "m" if side == "to" else "n"
    return {
        "step_id": ref.step_id,
        "target": target,
        "field": ref.field if ref.field in ("Id", "Name") else "Id",
    }


def _step_to_route_plan(step: ActionStep, *, question: str) -> Dict[str, Any]:
    draft: Dict[str, Any] = {
        "focus": step.focus,
        "focus_id": step.focus_id,
        "purpose_node": step.purpose_node,
        "biz_type": step.action,
        "known_type": step.known_type,
        "hop_direction": step.hop_direction,
    }
    if step.action == "document":
        draft["query"] = step.query or question
    if step.property_fields:
        draft["property_fields"] = list(step.property_fields)
    if step.action == "node_prop_rev":
        draft["value"] = step.focus
    return {
        "purpose_node": step.purpose_node,
        "biz_type": step.action,
        "known_type": step.known_type,
        "hop_direction": step.hop_direction,
        "params_draft": draft,
        "retrieval_question": question,
    }


def _patch_step_params(
    step: ActionStep,
    params: Dict[str, Any],
    *,
    business_id: str,
) -> Dict[str, Any]:
    p = dict(params or {})
    if step.focus and not p.get("focus") and not p.get("project_name"):
        if business_id == "BIZ_PROJECT_TO_METHOD":
            p["project_name"] = step.focus
        else:
            p["focus"] = step.focus
    if step.focus_id and not p.get("focus_id"):
        p["focus_id"] = step.focus_id
    if step.use_prev:
        ref = _use_prev_to_ref(
            step,
            known_type=step.known_type,
            purpose_node=step.purpose_node,
        )
        if ref:
            if ref.get("field") == "Id" or step.known_type == "TestProject":
                p["match_mode"] = "in"
                p["from_values_ref"] = ref
            elif ref.get("field") == "Name":
                p.setdefault("match_mode", "fuzzy_any")
                p["from_values_ref"] = ref
    if step.property_fields:
        p["property_fields"] = list(step.property_fields)
    if step.action == "document" and step.query:
        p["query"] = step.query
    return p


def expand_action_step(
    step: ActionStep,
    *,
    question: str,
    group_id: str = "",
) -> List[Dict[str, Any]]:
    route = _step_to_route_plan(step, question=question)
    bid, rel_patch = resolve_business_from_plan(route, question=question)

    if (
        step.use_prev
        and step.action == "hop_n"
        and step.known_type == "TestProject"
        and step.purpose_node == "TestMethod"
    ):
        bid = BIZ_REL_ONE_HOP
        rel_patch = {
            "from_type": "TestProject",
            "to_type": "TestMethod",
            "rel_type": "CON",
            "where_side": "from",
        }

    if (
        step.use_prev
        and step.action == "hop_n"
        and step.known_type == "Equipment"
        and step.purpose_node == "TestProject"
        and step.hop_direction == "reverse"
    ):
        bid = BIZ_REL_REVERSE_ONE_HOP
        rel_patch = {
            "from_type": "Equipment",
            "to_type": "TestProject",
            "rel_type": "CON",
            "where_side": "to",
        }

    if not bid:
        if step.action == "document":
            params = _patch_step_params(
                step,
                {"query": step.query or question},
                business_id=BIZ_DOCUMENT,
            )
            steps = expand_business(BIZ_DOCUMENT, params, retrieval_question=question)
            out: List[Dict[str, Any]] = []
            for st in steps:
                s = dict(st)
                s["step_id"] = step.step_id
                s["action_planner"] = {
                    "group_id": group_id,
                    "logical_step_id": step.step_id,
                    "action": step.action,
                    "purpose_node": step.purpose_node,
                }
                out.append(s)
            return out
        if step.action == "enum":
            node_type = step.purpose_node or step.known_type or "TestProject"
            params = _patch_step_params(
                step,
                {"node_type": node_type, "limit": 300},
                business_id=BIZ_ENUM_NODES,
            )
            steps = expand_business(BIZ_ENUM_NODES, params, retrieval_question=question)
            out = []
            for st in steps:
                s = dict(st)
                s["step_id"] = step.step_id
                s["action_planner"] = {
                    "group_id": group_id,
                    "logical_step_id": step.step_id,
                    "action": step.action,
                    "purpose_node": step.purpose_node,
                }
                out.append(s)
            return out
        logger.warning("[ZNWD5][expand] cannot resolve business for step %s", step.step_id)
        return []

    params = _patch_step_params(step, rel_patch, business_id=bid)
    steps = expand_business(bid, params, retrieval_question=question)
    if not steps and step.use_prev and bid in (BIZ_REL_ONE_HOP, BIZ_REL_REVERSE_ONE_HOP):
        ref = _use_prev_to_ref(
            step,
            known_type=step.known_type,
            purpose_node=step.purpose_node,
        )
        if ref:
            steps = [
                {
                    "step_id": step.step_id,
                    "op_code": "neo_relation_query",
                    "params": {
                        "from_type": rel_patch.get("from_type"),
                        "to_type": rel_patch.get("to_type"),
                        "rel_type": rel_patch.get("rel_type", "CON"),
                        "where_side": rel_patch.get("where_side", "from"),
                        "match_mode": "in",
                        "from_field": "Id",
                        "from_values_ref": ref,
                        "limit": 200,
                    },
                    "purpose": step.note or f"{step.known_type}→{step.purpose_node}（引用上步）",
                }
            ]
    out: List[Dict[str, Any]] = []
    for st in steps:
        s = dict(st)
        s["step_id"] = step.step_id
        s["action_planner"] = {
            "group_id": group_id,
            "logical_step_id": step.step_id,
            "action": step.action,
            "purpose_node": step.purpose_node,
        }
        out.append(s)
    return out


def expand_action_steps(
    steps: List[ActionStep],
    *,
    question: str,
    group_id: str = "",
) -> List[Dict[str, Any]]:
    all_steps: List[Dict[str, Any]] = []
    for step in steps:
        expanded = expand_action_step(step, question=question, group_id=group_id)
        all_steps.extend(expanded)
    return _remap_from_values_refs(all_steps)


def expand_action_plan(
    plan: ActionPlan,
    *,
    question: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    展开 ActionPlan 为 meta_ops steps。
    返回 (flat_steps, step_groups) — groups 用于多问拼装。
    """
    q = (question or plan.question or "").strip()
    flat: List[Dict[str, Any]] = []
    groups: List[Dict[str, Any]] = []

    if plan.steps:
        flat = expand_action_steps(plan.steps, question=q, group_id="main")
        groups.append({"group_id": "main", "question": q, "steps": list(plan.steps), "meta_steps": flat})

    for i, sq in enumerate(plan.sub_questions):
        gid = f"sq{i + 1}"
        meta = expand_action_steps(sq.steps, question=sq.question or q, group_id=gid)
        flat.extend(meta)
        groups.append({"group_id": gid, "question": sq.question, "steps": list(sq.steps), "meta_steps": meta})

    return flat, groups


def _remap_from_values_refs(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """确保 from_values_ref 的 step_id 与展开后的 logical step_id 一致。"""
    id_map = {}
    for s in steps:
        ap = s.get("action_planner") if isinstance(s.get("action_planner"), dict) else {}
        logical = str(ap.get("logical_step_id") or s.get("step_id") or "")
        actual = str(s.get("step_id") or "")
        if logical:
            id_map[logical] = actual

    out: List[Dict[str, Any]] = []
    for s in steps:
        s2 = dict(s)
        params = dict(s2.get("params") or {})
        ref = params.get("from_values_ref")
        if isinstance(ref, dict):
            ref_sid = str(ref.get("step_id") or "").strip()
            if ref_sid in id_map:
                ref = dict(ref)
                ref["step_id"] = id_map[ref_sid]
                params["from_values_ref"] = ref
        s2["params"] = params
        out.append(s2)
    return out


def action_plan_to_reconcile_rec(
    plan: ActionPlan,
    *,
    question: str,
    grounding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造 pipeline 可用的 reconcile 替代结构（跳过 Playbook）。"""
    q = (question or plan.question or "").strip()
    first = plan.steps[0] if plan.steps else (
        plan.sub_questions[0].steps[0] if plan.sub_questions and plan.sub_questions[0].steps else None
    )
    route = _step_to_route_plan(first, question=q) if first else {}
    bid, params = resolve_business_from_plan(route, question=q) if first else ("", {})
    if first:
        params = _patch_step_params(first, params, business_id=bid)

    mode = plan.answer_mode
    strategy = "db_synthesize" if mode == "synthesize" else ("db_direct" if mode == "direct" else "db_synthesize")

    return {
        "business_id": bid,
        "params": params,
        "keyword": str((grounding or {}).get("keyword") or (first.focus if first else "")),
        "answer_strategy": strategy,
        "answer_mode": "database_synthesize" if mode == "synthesize" else (
            "database_direct" if mode == "direct" else "database_synthesize"
        ),
        "retrieval_question": q,
        "action_plan": plan.to_dict(),
        "action_plan_groups": [],
        "multi_step": plan.total_step_count > 1 or bool(plan.sub_questions),
        "playbook": None,
        "user_biz": "",
        "scenario": "",
        "source": "action_planner",
    }


__all__ = [
    "action_plan_to_reconcile_rec",
    "expand_action_plan",
    "expand_action_step",
]
