"""规则 binding：问句字面实体优先，减少 LLM 误选 / 漏绑。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from blueprints.GLCX.ZNWD4.slot_focus import extract_equipment_focus

from .ontology_loader import get_template, slot_key_for_node_type

_EQUIP_TEMPLATES: Set[str] = {
    "equip_to_projects",
    "equip_to_indicators",
    "equip_and_env_to_projects",
    "equip_type_to_indicators",
    "composite_project_then_method",
    "composite_project_and_indicator",
    "composite_indicator_then_method",
    "composite_project_and_indicator",
    "composite_project_env_method",
    "composite_env_settings",
    "method_by_project_or_indicator",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def _strip_leading_query(s: str) -> str:
    t = (s or "").strip()
    for prefix in ("查询一下", "请查询", "查询", "请告诉我", "告诉我", "请列出", "列出"):
        if t.startswith(prefix):
            return t[len(prefix) :].strip()
    return t


def _candidates_by_type(tau: Dict[str, Any], node_type: str) -> List[Dict[str, Any]]:
    return [
        c
        for c in (tau.get("flat_candidates") or [])
        if str(c.get("node_type") or "") == node_type
    ]


def _pick_name_in_question(question: str, cands: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    qn = _norm(question)
    if not qn or not cands:
        return None
    matched: List[tuple] = []
    for c in cands:
        name = str(c.get("name") or "").strip()
        nn = _norm(name)
        if len(nn) >= 2 and nn in qn:
            matched.append((len(nn), float(c.get("score") or 0), c))
    if not matched:
        return None
    matched.sort(key=lambda x: (-x[0], -x[1]))
    return matched[0][2]


def _extract_environment_keyword(question: str, tau: Dict[str, Any]) -> str:
    _, env = _parse_equip_env_pair(question)
    if env:
        return env
    picked = _pick_name_in_question(question, _candidates_by_type(tau, "Environment"))
    if picked:
        return str(picked.get("name") or "")
    n = _norm(question)
    m = re.search(
        r"在([\u4e00-\u9fffA-Za-z0-9·\-]{2,24}?)(?:环境|条件下?)",
        n,
    )
    if m:
        return (m.group(1) or "").strip()
    m = re.search(r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,20}?)环境下", n)
    if m:
        return (m.group(1) or "").strip()
    return ""


def _parse_equip_env_pair(question: str) -> tuple[str, str]:
    n = _norm(_strip_leading_query(question))
    for pat in (
        r"^(.+?)在(.+?)环境下",
        r"^(.+?)在(.+?)环境",
        r"^(.+?)于(.+?)环境下",
    ):
        m = re.search(pat, n)
        if m:
            eq = _strip_leading_query(m.group(1) or "").strip()
            env = (m.group(2) or "").strip()
            if len(eq) >= 2 and len(env) >= 1:
                return eq, env
    return "", ""


def _equipment_name(question: str, tau: Dict[str, Any]) -> str:
    eq_env, env = _parse_equip_env_pair(question)
    if eq_env:
        return eq_env
    focus = (extract_equipment_focus(question) or "").strip()
    if focus and env and focus.endswith(env):
        focus = focus[: -len(env)].rstrip("在").strip()
    if focus:
        return focus
    picked = _pick_name_in_question(question, _candidates_by_type(tau, "Equipment"))
    if picked:
        return str(picked.get("name") or "")
    picked_type = _pick_name_in_question(question, _candidates_by_type(tau, "EquipmentType"))
    if picked_type:
        return str(picked_type.get("name") or "")
    return ""


def _binding_row(name: str, node_type: str, *, span: str = "", entity_id: str = "") -> Dict[str, Any]:
    return {
        "span": span or name,
        "entity_id": entity_id,
        "node_type": node_type,
        "name": name,
        "slot_key": slot_key_for_node_type(node_type),
    }


def try_rule_binding(
    question: str,
    tau: Dict[str, Any],
    template_id: str,
) -> Optional[Dict[str, Any]]:
    """若问句含明确实体且 τ 可验证，返回 binding；否则 None。"""
    q = (question or "").strip()
    if not q:
        return None
    if not get_template(template_id) and template_id not in _EQUIP_TEMPLATES:
        return None

    bindings: List[Dict[str, Any]] = []
    tid = template_id

    eq = _equipment_name(q, tau)
    if eq and template_id in _EQUIP_TEMPLATES:
        eq_cands = _candidates_by_type(tau, "Equipment")
        row = _pick_name_in_question(q, eq_cands)
        if row:
            bindings.append(
                _binding_row(
                    str(row.get("name") or eq),
                    "Equipment",
                    span=eq,
                    entity_id=str(row.get("entity_id") or ""),
                )
            )
        else:
            bindings.append(_binding_row(eq, "Equipment", span=eq))

    if template_id == "equip_and_env_to_projects" or template_id == "composite_project_env_method":
        env = _extract_environment_keyword(q, tau)
        if env:
            env_row = _pick_name_in_question(env, _candidates_by_type(tau, "Environment"))
            if not env_row:
                env_row = _pick_name_in_question(q, _candidates_by_type(tau, "Environment"))
            bindings.append(
                _binding_row(
                    str((env_row or {}).get("name") or env),
                    "Environment",
                    span=env,
                    entity_id=str((env_row or {}).get("entity_id") or ""),
                )
            )

    if template_id == "equip_type_to_indicators":
        et = _pick_name_in_question(q, _candidates_by_type(tau, "EquipmentType"))
        if et:
            bindings.append(
                _binding_row(
                    str(et.get("name") or ""),
                    "EquipmentType",
                    entity_id=str(et.get("entity_id") or ""),
                )
            )

    if not bindings:
        return None

    return {
        "bindings": bindings,
        "template_id": tid,
        "intent": "rule_bind",
        "doc_hints": [],
    }
