"""ZNWD6 计划校验：binding ⊆ τ、template 合法。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .ontology_loader import get_template, list_template_ids


def verify_plan(plan: Dict[str, Any], tau: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    errors: List[str] = []
    binding = plan.get("znwd6_binding") if isinstance(plan.get("znwd6_binding"), dict) else {}
    allowed = {
        str(c.get("entity_id") or "")
        for c in (tau.get("flat_candidates") or [])
        if c.get("entity_id")
    }
    for b in binding.get("bindings") or []:
        eid = str((b or {}).get("entity_id") or "")
        if eid and eid not in allowed:
            errors.append(f"binding entity not in tau: {eid}")

    tid = str(plan.get("template_id") or "")
    if tid and tid not in list_template_ids():
        errors.append(f"invalid template_id: {tid}")
    elif tid and not get_template(tid):
        errors.append(f"missing template config: {tid}")

    if str(plan.get("answer_strategy") or "") == "db_direct" and not plan.get("params_draft"):
        if tid not in ("enum_by_focus_type", "chitchat"):
            errors.append("db_direct requires params_draft")

    return errors, plan
