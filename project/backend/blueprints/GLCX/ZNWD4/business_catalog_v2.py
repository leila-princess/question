"""
ZNWD4 业务目录 v2：biz_key → business_id + 关系默认参数（仅代码侧，不注入 LLM）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

from .business_catalog import (
    BIZ_DOCUMENT,
    BIZ_ENUM_NODES,
    BIZ_EQUIP_PROJECT_BY_METHOD,
    BIZ_METHOD_CONTENT,
    BIZ_NODE_KW,
    BIZ_NODE_PROP,
    BIZ_NODE_PROP_REV,
    BIZ_PROJECT_TO_METHOD,
    BIZ_REL_ONE_HOP,
    BIZ_REL_REVERSE_ONE_HOP,
    BIZ_REL_TWO_HOP,
)

# (purpose_node, known_type, hop_direction) -> (business_id, rel param defaults)
_HOP_ONE: Dict[Tuple[str, str, str], Tuple[str, Dict[str, Any]]] = {
    ("TestProject", "Environment", "forward"): (
        BIZ_REL_ONE_HOP,
        {"from_type": "Environment", "to_type": "TestProject", "rel_type": "CON", "where_side": "from"},
    ),
    ("Environment", "TestProject", "reverse"): (
        BIZ_REL_ONE_HOP,
        {"from_type": "Environment", "to_type": "TestProject", "rel_type": "CON", "where_side": "to"},
    ),
    ("TestProject", "Equipment", "forward"): (
        BIZ_REL_ONE_HOP,
        {"from_type": "Equipment", "to_type": "TestProject", "rel_type": "CON", "where_side": "from"},
    ),
    ("Equipment", "TestProject", "reverse"): (
        BIZ_REL_REVERSE_ONE_HOP,
        {"from_type": "Equipment", "to_type": "TestProject", "rel_type": "CON", "where_side": "to"},
    ),
    ("Indicator", "Equipment", "forward"): (
        BIZ_REL_ONE_HOP,
        {"from_type": "Equipment", "to_type": "Indicator", "rel_type": "CON", "where_side": "from"},
    ),
    ("Equipment", "Indicator", "reverse"): (
        BIZ_REL_REVERSE_ONE_HOP,
        {"from_type": "Equipment", "to_type": "Indicator", "rel_type": "CON", "where_side": "to"},
    ),
    ("TestMethod", "TestProject", "forward"): (
        BIZ_PROJECT_TO_METHOD,
        {},
    ),
    ("TestProject", "TestMethod", "reverse"): (
        BIZ_REL_REVERSE_ONE_HOP,
        {"from_type": "TestProject", "to_type": "TestMethod", "rel_type": "CON", "where_side": "to"},
    ),
    ("TestMethod", "Indicator", "forward"): (
        BIZ_REL_ONE_HOP,
        {"from_type": "Indicator", "to_type": "TestMethod", "rel_type": "CON", "where_side": "from"},
    ),
    ("Indicator", "TestMethod", "reverse"): (
        BIZ_REL_REVERSE_ONE_HOP,
        {"from_type": "Indicator", "to_type": "TestMethod", "rel_type": "CON", "where_side": "to"},
    ),
    ("Equipment", "EquipmentType", "reverse"): (
        BIZ_REL_ONE_HOP,
        {"from_type": "EquipmentType", "to_type": "Equipment", "rel_type": "HAS", "where_side": "to"},
    ),
    ("Equipment", "EquipmentType", "forward"): (
        BIZ_REL_ONE_HOP,
        {"from_type": "EquipmentType", "to_type": "Equipment", "rel_type": "HAS", "where_side": "from"},
    ),
    ("File", "TestMethod", "forward"): (
        BIZ_REL_REVERSE_ONE_HOP,
        {"from_type": "TestMethod", "to_type": "File", "rel_type": "BELONGS_TO", "where_side": "to"},
    ),
    ("TestMethod", "File", "reverse"): (
        BIZ_REL_ONE_HOP,
        {"from_type": "TestMethod", "to_type": "File", "rel_type": "BELONGS_TO", "where_side": "from"},
    ),
    ("TestProject", "TestProject", "forward"): (
        BIZ_REL_ONE_HOP,
        {"from_type": "TestProject", "to_type": "TestProject", "rel_type": "HAS", "where_side": "from"},
    ),
}


def resolve_biz_key(
    *,
    purpose_node: str,
    biz_type: str,
    known_type: str = "",
    hop_direction: str = "forward",
    biz_key: str = "",
    question: str = "",
) -> str:
    """purpose+biz_type → biz_key 字符串（用于日志/前端）。"""
    p = (purpose_node or "").strip()
    t = (biz_type or "").strip().lower()
    k = (known_type or "").strip()
    d = (hop_direction or "forward").strip().lower()
    if (biz_key or "").strip():
        return biz_key.strip()
    if t == "document":
        return "document"
    if t == "enum":
        return f"{p}.enum"
    if t == "node_kw":
        return f"{p}.node_kw"
    if t == "node_prop":
        return f"{p}.node_prop"
    if t == "node_prop_rev":
        return f"{p}.node_prop_rev"
    if t == "hop_n" and p and k:
        hop = "hop2" if _question_needs_hop2(question, p, k) else ""
        via = _hop2_suffix(question, p, k) if hop else ""
        base = f"{p}.from_{k}"
        if hop:
            return f"{base}.{hop}{via}"
        return f"{base}.{'rev' if d == 'reverse' else 'fwd'}"
    return ""


def resolve_business_from_plan(
    plan: Dict[str, Any],
    *,
    question: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """
    从 RoutePlan（purpose_node + biz_type）解析 business_id 与关系参数补丁。
    若无法解析返回 ("", {})。
    """
    draft = plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {}
    purpose = str(plan.get("purpose_node") or draft.get("purpose_node") or "").strip()
    biz_type = str(plan.get("biz_type") or draft.get("biz_type") or "").strip().lower()
    known = str(plan.get("known_type") or draft.get("known_type") or "").strip()
    hop_dir = str(plan.get("hop_direction") or draft.get("hop_direction") or "forward").strip().lower()
    if hop_dir not in ("forward", "reverse"):
        hop_dir = "forward"

    if biz_type == "document":
        return BIZ_DOCUMENT, {
            "query": str(draft.get("query") or plan.get("retrieval_question") or question)[:500],
            "section_prefix": str(draft.get("section_prefix") or "").strip(),
        }

    if biz_type == "enum" and purpose:
        return BIZ_ENUM_NODES, {"node_type": purpose}

    if biz_type == "node_kw" and purpose:
        return BIZ_NODE_KW, {
            "node_type": purpose,
            "field": str(draft.get("field") or "Name"),
            "value": str(draft.get("focus") or draft.get("value") or plan.get("keyword") or "").strip(),
            "focus_id": str(draft.get("focus_id") or "").strip(),
        }

    if biz_type == "node_prop_rev" and purpose:
        return BIZ_NODE_PROP_REV, {
            "node_type": purpose,
            "field": str(draft.get("field") or "Type"),
            "value": str(draft.get("value") or draft.get("focus") or "").strip(),
        }

    if biz_type == "node_prop" and purpose:
        if purpose == "TestMethod":
            return BIZ_METHOD_CONTENT, {}
        return BIZ_NODE_PROP, {
            "node_type": purpose,
            "field": str(draft.get("field") or "Name"),
            "value": str(draft.get("focus") or draft.get("value") or "").strip(),
            "focus_id": str(draft.get("focus_id") or "").strip(),
            "property_fields": draft.get("property_fields") if isinstance(draft.get("property_fields"), list) else [],
        }

    if biz_type == "hop_n" and purpose and known:
        key = (purpose, known, hop_dir)
        if key in _HOP_ONE:
            bid, rel_defaults = _HOP_ONE[key]
            if bid == BIZ_PROJECT_TO_METHOD:
                return bid, {
                    "project_name": str(draft.get("focus") or draft.get("project_name") or "").strip(),
                }
            patch = dict(rel_defaults)
            return bid, patch

        q = (question or "").strip()
        if purpose == "TestMethod" and known == "Equipment" and _question_needs_hop2(q, purpose, known):
            if _hop2_suffix(q, purpose, known) == "_via_IND":
                return BIZ_REL_TWO_HOP, {
                    "from_type": "Equipment",
                    "to_type": "Indicator",
                    "rel_type": "CON",
                    "where_side": "from",
                }
            return BIZ_REL_TWO_HOP, {}

        if looks_like_equip_project_by_method_question(q):
            return BIZ_EQUIP_PROJECT_BY_METHOD, {}

    return "", {}


def looks_like_equip_project_by_method_question(text: str) -> bool:
    from .method_inference import looks_like_equip_project_by_method

    return looks_like_equip_project_by_method(text)


def _question_needs_hop2(question: str, purpose: str, known: str) -> bool:
    t = (question or "")
    if purpose == "TestMethod" and known == "Equipment":
        return bool(
            re.search(r"(方法|试验方法|测试方法)", t)
            and not re.search(r"(有哪些|有什么).{0,8}项目", t)
        )
    if purpose == "TestProject" and known == "EquipmentType":
        return True
    if purpose == "TestMethod" and known == "Environment":
        return True
    return False


def _hop2_suffix(question: str, purpose: str, known: str) -> str:
    t = (question or "")
    if purpose == "TestMethod" and known == "Equipment":
        if re.search(r"(指标|考核|性能)", t):
            return "_via_IND"
        return "_via_TP"
    return ""
