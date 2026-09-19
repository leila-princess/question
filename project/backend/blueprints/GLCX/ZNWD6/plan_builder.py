"""将 binding + template 转为 ZNWD4 可 reconcile 的 route plan。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from blueprints.GLCX.ZNWD4.routing_catalog import _route_id_composite, _route_id_playbook

from .ontology_loader import get_template, slot_key_for_node_type


def _tau_probe_hits(tau: Dict[str, Any]) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for h in tau.get("flat_candidates") or []:
        if not isinstance(h, dict):
            continue
        row = dict(h)
        row["id"] = str(row.get("id") or row.get("entity_id") or "").strip()
        hits.append(row)
    return hits


def _params_from_binding(binding: Dict[str, Any]) -> tuple[Dict[str, List[str]], str]:
    params: Dict[str, List[str]] = {}
    focus = ""
    for b in binding.get("bindings") or []:
        if not isinstance(b, dict):
            continue
        role = str(b.get("role") or "source").lower()
        if role in ("target", "goal"):
            continue
        sk = str(b.get("slot_key") or slot_key_for_node_type(str(b.get("node_type") or "")))
        name = str(b.get("name") or "").strip()
        if not name:
            continue
        params.setdefault(sk, [])
        if name not in params[sk]:
            params[sk].append(name)
        if not focus:
            focus = name
    for hint in binding.get("doc_hints") or []:
        h = str(hint).strip()
        if h:
            params.setdefault("standard_keywords", [])
            if h not in params["standard_keywords"]:
                params["standard_keywords"].append(h)
    return params, focus


def _apply_guided_binding_ids(params: Dict[str, Any], binding: Dict[str, Any], *, goal_type: str = "") -> Dict[str, Any]:
    """引导式：把 entity_id / 目标节点类型写入 params_draft，避免再走 NL 消歧。"""
    out = dict(params)
    gt = (goal_type or str(binding.get("goal_type") or "")).strip()
    for b in binding.get("bindings") or []:
        if not isinstance(b, dict):
            continue
        role = str(b.get("role") or "source").lower()
        nt = str(b.get("node_type") or "").strip()
        eid = str(b.get("entity_id") or "").strip()
        name = str(b.get("name") or "").strip()
        if role in ("target", "goal") and nt:
            gt = nt
            out["goal_node_type"] = nt
            continue
        if not eid or not nt:
            continue
        if nt == "TestProject":
            out["project_id"] = eid
            out.setdefault("focus_id", eid)
        elif nt == "Environment":
            out["environment_focus_id"] = eid
            out.setdefault("focus_id", eid)
        elif nt == "Equipment":
            out["equipment_id"] = eid
            out.setdefault("focus_id", eid)
        elif nt == "Indicator":
            out["indicator_id"] = eid
            out.setdefault("focus_id", eid)
        elif nt == "TestMethod":
            out["method_id"] = eid
            out.setdefault("focus_id", eid)
        elif nt == "EquipmentType":
            out["equipment_type_id"] = eid
            out.setdefault("focus_id", eid)
        if name:
            out.setdefault("focus", name)
    if gt:
        out["goal_node_type"] = gt
        out["to_type"] = gt
    return out


def _equipment_from_binding(binding: Dict[str, Any]) -> str:
    for b in binding.get("bindings") or []:
        if not isinstance(b, dict):
            continue
        if str(b.get("node_type") or "") == "Equipment":
            nm = str(b.get("name") or "").strip()
            if nm:
                return nm
    return ""


def _patch_intent_plan_equipment(ip: Any, equip: str) -> None:
    if not equip or not ip:
        return
    slots = {"equipment_keywords": [equip], "focus": equip}
    for st in ip.steps:
        st.slots = {**slots, **(st.slots or {})}
        st.anchor_slots = dict(slots)


def _build_intent_plan_route(
    question: str,
    tau: Dict[str, Any],
    binding: Dict[str, Any],
    *,
    template_id: str,
    driver: Any = None,
) -> Optional[Dict[str, Any]]:
    from blueprints.GLCX.ZNWD4.intent_plan import build_intent_plan
    from blueprints.GLCX.ZNWD4.pcsr_assembly import finalize_intent_plan

    ip = build_intent_plan(question, driver=driver)
    if not ip:
        return None
    params, focus = _params_from_binding(binding)
    eq = _equipment_from_binding(binding) or focus
    if eq:
        _patch_intent_plan_equipment(ip, eq)
    plan = finalize_intent_plan(ip, driver=driver)
    draft = plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {}
    merged = dict(draft)
    for k, v in params.items():
        if v and not merged.get(k):
            merged[k] = v
    if eq:
        merged.setdefault("equipment_keywords", [eq])
        merged["focus"] = eq
    plan.update(
        {
            "source": "znwd6",
            "routing_tier": "znwd6",
            "routing_path_source": "znwd6",
            "template_id": template_id,
            "params_draft": merged,
            "keyword": eq or focus or plan.get("keyword") or question[:80],
            "focus": eq or focus or plan.get("focus") or "",
            "match_confidence": "high" if merged else "medium",
            "znwd6_binding": binding,
            "zvec_used": bool(tau.get("zvec_used")),
        }
    )
    return plan


def build_route_plan(
    question: str,
    tau: Dict[str, Any],
    binding: Dict[str, Any],
    *,
    template_id: str,
    driver: Any = None,
) -> Dict[str, Any]:
    q = (question or "").strip()
    tpl = get_template(template_id)
    composite_kind = str(tpl.get("composite_kind") or "")

    if composite_kind == "intent_plan":
        intent_plan = _build_intent_plan_route(q, tau, binding, template_id=template_id, driver=driver)
        if intent_plan:
            return intent_plan

    params, focus = _params_from_binding(binding)
    if binding.get("guided"):
        params = _apply_guided_binding_ids(
            params,
            binding,
            goal_type=str(binding.get("goal_type") or ""),
        )
        focus = str(params.get("focus") or focus or "").strip() or focus
    user_biz = str(tpl.get("user_biz") or "")
    scenario = str(tpl.get("scenario") or "default")
    answer_strategy = str(tpl.get("answer_strategy") or "db_direct")
    doc_mode = bool(tpl.get("doc_mode"))

    if template_id == "equip_to_projects" and params.get("equipment_keywords"):
        params.setdefault("limit", 500)

    if template_id in ("equip_and_env_to_projects", "composite_project_env_method"):
        eq_kw = params.get("equipment_keywords") or []
        if eq_kw:
            focus = str(eq_kw[0])
        for b in binding.get("bindings") or []:
            if not isinstance(b, dict):
                continue
            if str(b.get("node_type") or "") != "Environment":
                continue
            env_name = str(b.get("name") or "").strip()
            eid = str(b.get("entity_id") or "").strip()
            if env_name:
                params.setdefault("environment_keywords", [])
                if env_name not in params["environment_keywords"]:
                    params["environment_keywords"].append(env_name)
                params["environment_label"] = env_name
            if eid:
                params["environment_focus_id"] = eid

    if template_id == "chitchat" or answer_strategy == "direct_llm":
        route_id = "R-CHAT-direct"
    elif user_biz.startswith("COMP_"):
        route_id = _route_id_composite(user_biz)
    elif user_biz and scenario:
        route_id = _route_id_playbook(user_biz, scenario)
    else:
        route_id = f"R-ZNWD6-{template_id}"

    if doc_mode and answer_strategy not in ("direct_llm", "db_direct_doc"):
        answer_strategy = "db_synthesize"

    if answer_strategy == "db_direct_doc":
        answer_mode = "database_direct"
    elif answer_strategy == "db_synthesize":
        answer_mode = "synthesize"
    else:
        answer_mode = "direct"

    plan: Dict[str, Any] = {
        "source": "znwd6",
        "routing_tier": "znwd6",
        "routing_path_source": "znwd6",
        "route_id": route_id,
        "user_biz": user_biz,
        "scenario": scenario,
        "template_id": template_id,
        "purpose": "graph_query" if answer_strategy != "direct_llm" else "chat",
        "keyword": focus or q[:80],
        "focus": focus,
        "retrieval_question": q,
        "params_draft": params,
        "match_confidence": "high" if params else "medium",
        "answer_strategy": answer_strategy,
        "answer_mode": answer_mode,
        "probe_snapshot": {
            "tau": tau,
            "binding": binding,
            "hits": _tau_probe_hits(tau),
            "best": (_tau_probe_hits(tau) or [{}])[0],
            "zvec_used": bool(tau.get("zvec_used")),
        },
        "zvec_used": bool(tau.get("zvec_used")),
        "znwd6_binding": binding,
        "znwd6_tau": {
            "span_count": len(tau.get("spans") or []),
            "candidate_count": len(tau.get("flat_candidates") or []),
        },
    }
    if doc_mode:
        plan["znwd6_deferred_doc"] = True
    return plan
