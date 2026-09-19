"""
单跳关系问句：推断 from_type / to_type / focus（如 设备→试验项目）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple, Tuple

from .meta_ops import RELATION_SCHEMA

_PROJECT_GOAL = re.compile(r"(试验项目|测试项目)", re.I)
_METHOD_GOAL = re.compile(r"(试验方法|测试方法|方法)", re.I)
_INDICATOR_GOAL = re.compile(r"(考核指标|性能指标|试验指标|测试指标|指标)", re.I)

_OBJECT_INDICATOR_REL = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*(?:有什么|有哪些|包括哪些).{0,12}"
    r"(?:考核指标|性能指标|试验指标|测试指标|指标)",
    re.I,
)

_OBJECT_PROJECT_REL = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*(?:有什么|有哪些|包括哪些).{0,10}"
    r"(?:试验项目|测试项目|项目)",
    re.I,
)

_TYPE_EQUIPMENT_REL = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24})\s*(?:有什么|有哪些|包括哪些)\s*(?:具体)?\s*(?:的)?\s*(?:设备|装备)",
    re.I,
)

# 「地面装备的具体装备」等
_TYPE_EQUIPMENT_REL_ALT = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24})\s*(?:的|下)\s*(?:具体)?\s*(?:设备|装备)",
    re.I,
)

# 向前查找（反向单跳）：已知终点实体，查关联的起点实体
_REVERSE_EQUIP_BY_PROJECT = re.compile(
    r"(哪些|哪一些|什么|都有哪些|有哪些).{0,24}(?:的)?\s*(?:设备|装备).{0,24}(?:有|具备|关联).{0,40}(?:试验|测试)?\s*项目",
    re.I,
)
# 项目在前：「X项目和Y项目都有哪些设备要做」
_REVERSE_EQUIP_BY_PROJECT_ALT = re.compile(
    r"(?:试验|测试)?\s*项目.{0,56}(?:哪些|哪一些|什么|都有哪些|有哪些).{0,28}(?:设备|装备)"
    r".{0,24}(?:要做|需要|涉及|关联)?",
    re.I,
)
_REVERSE_ENV_BY_PROJECT = re.compile(
    r"(哪些|哪一些|什么|都有哪些|有哪些).{0,24}(?:的)?\s*环境.{0,24}(?:有|具备|关联).{0,40}(?:试验|测试)?\s*项目",
    re.I,
)
_REVERSE_EQUIP_BY_INDICATOR = re.compile(
    r"(哪些|哪一些|什么|都有哪些|有哪些).{0,24}(?:的)?\s*(?:设备|装备).{0,24}(?:有|具备|关联).{0,40}指标",
    re.I,
)
_REVERSE_PROJECT_BY_METHOD = re.compile(
    r"(哪些|哪一些|什么|都有哪些|有哪些).{0,24}(?:的)?\s*(?:试验|测试)?\s*项目.{0,24}(?:有|具备|关联|使用|采用).{0,40}(?:试验|测试)?\s*方法",
    re.I,
)

# 向前查找三元组：answer_entity -> (from_type, to_type, rel_type)；过滤条件在 to 端（where_side=to）
_REVERSE_TRIPLES: Dict[str, Tuple[str, str, str]] = {
    "equipment": ("Equipment", "TestProject", "CON"),
    "environment": ("Environment", "TestProject", "CON"),
    "indicator": ("Equipment", "Indicator", "CON"),
    "project": ("TestProject", "TestMethod", "CON"),
    "method": ("Indicator", "TestMethod", "CON"),
}


def looks_like_type_equipment_relation(text: str) -> bool:
    """某设备类型/类别下有哪些具体设备/装备，如「地面装备有什么具体装备」。"""
    t = (text or "").strip()
    if not t:
        return False
    if "设备类型" in t or "装备类型" in t or "设备类别" in t:
        return False
    if re.search(r"(全部|所有|整个库|整个系统|一共).{0,8}(设备|装备)", t):
        return False
    if re.search(r"具体\s*(?:设备|装备)", t) and re.search(r"(?:有什么|有哪些|包括哪些)", t):
        if not re.search(r"(?:设备类型|装备类型|设备类别)", t):
            return True
    return bool(_TYPE_EQUIPMENT_REL.search(t) or _TYPE_EQUIPMENT_REL_ALT.search(t))


def extract_type_focus_for_equipment(text: str) -> str:
    for pat in (_TYPE_EQUIPMENT_REL, _TYPE_EQUIPMENT_REL_ALT):
        m = pat.search(text or "")
        if m:
            focus = re.sub(r"\s+", "", (m.group(1) or "").strip())
            focus = re.sub(r"(的|下|中)$", "", focus).strip()
            if len(focus) >= 2:
                return focus
    return ""


def resolve_type_equipment_rel_params(
    question: str,
    retrieval_question: str = "",
    *,
    node_type: str = "",
    node_name: str = "",
    node_id: str = "",
    params_draft: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    draft = params_draft or {}
    text = f"{question}\n{retrieval_question}"
    focus = (
        extract_type_focus_for_equipment(text)
        or str(draft.get("focus") or "").strip()
        or node_name
        or ""
    )
    out: Dict[str, Any] = {
        "from_type": "EquipmentType",
        "to_type": "Equipment",
        "rel_type": "HAS",
        "focus": focus,
    }
    nid = str(draft.get("focus_id") or node_id or "").strip()
    if draft.get("match_all_equipment_types"):
        out["match_all_equipment_types"] = True
    elif node_type == "EquipmentType" and nid:
        out["focus_id"] = nid
    parent = str(draft.get("equipment_type_parent") or draft.get("parent_type") or "").strip()
    if parent:
        out["parent_type"] = parent
        out["equipment_type_parent"] = parent
    et_kws = draft.get("equipment_type_keywords")
    if isinstance(et_kws, list) and et_kws:
        out["focus"] = str(et_kws[0]).strip() or focus
    return out


_EQUIP_BELONGS_TO_TYPE = re.compile(
    r"(?:属于|归为|算作|算是|是).{0,16}(?:什么|哪(?:一)?(?:种|类)|哪些).{0,10}(?:装备|设备)?\s*类型",
    re.I,
)
_EQUIP_BELONGS_TO_TYPE_ALT = re.compile(
    r"(?:什么|哪(?:一)?(?:种|类)).{0,16}(?:装备|设备)\s*类型",
    re.I,
)
_EQUIP_BELONGS_TO_FOCUS = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:属于|归为).{0,20}(?:什么|哪).{0,12}(?:装备|设备)?\s*类型",
    re.I,
)
_EQUIP_BELONGS_TO_FOCUS_ALT = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:的)?\s*(?:装备|设备)\s*类型",
    re.I,
)


def looks_like_equipment_belongs_to_type(text: str) -> bool:
    """某具体装备属于哪类装备类型，如「无人机属于什么装备类型」。"""
    t = (text or "").strip()
    if not t:
        return False
    if looks_like_type_equipment_relation(t):
        return False
    if re.search(r"(?:类型|类别).{0,8}(?:下|中|里).{0,8}(?:有|包括|哪些)", t):
        return False
    return bool(_EQUIP_BELONGS_TO_TYPE.search(t) or _EQUIP_BELONGS_TO_TYPE_ALT.search(t))


def extract_equipment_focus_for_belongs_to_type(text: str) -> str:
    for pat in (_EQUIP_BELONGS_TO_FOCUS, _EQUIP_BELONGS_TO_FOCUS_ALT):
        m = pat.search(text or "")
        if m:
            focus = re.sub(r"\s+", "", (m.group(1) or "").strip())
            focus = re.sub(r"(的|下|中|属于|什么|哪|类|种|型)$", "", focus).strip()
            if len(focus) >= 2 and focus not in ("什么", "哪些", "哪类", "哪种", "装备", "设备"):
                return focus
    return ""


def resolve_equipment_belongs_to_type_params(
    question: str,
    retrieval_question: str = "",
    *,
    node_type: str = "",
    node_name: str = "",
    node_id: str = "",
    params_draft: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """EquipmentType-[HAS]->Equipment，where 过滤 Equipment 端，返回 EquipmentType。"""
    draft = params_draft or {}
    text = f"{question}\n{retrieval_question}"
    focus = (
        extract_equipment_focus_for_belongs_to_type(text)
        or str(draft.get("focus") or "").strip()
        or (node_name if node_type == "Equipment" else "")
        or ""
    )
    out: Dict[str, Any] = {
        "from_type": "EquipmentType",
        "to_type": "Equipment",
        "rel_type": "HAS",
        "where_side": "to",
        "result_type": "EquipmentType",
        "anchor_type": "Equipment",
        "focus": focus,
    }
    nid = str(draft.get("focus_id") or node_id or "").strip()
    if nid:
        out["focus_id"] = nid
    return out


def looks_like_object_indicator_relation(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _OBJECT_INDICATOR_REL.search(t):
        return True
    if re.search(r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*的\s*(?:考核|性能|试验|测试)?\s*指标", t):
        return True
    return False


def looks_like_equipment_by_indicator(text: str) -> bool:
    """某考核指标涉及哪些装备（反查）。"""
    t = (text or "").strip()
    if not t or _METHOD_GOAL.search(t):
        return False
    if not re.search(r"指标", t):
        return False
    if _REVERSE_EQUIP_BY_INDICATOR.search(t):
        return True
    if re.search(r"指标.{0,48}(哪些|哪一些|什么|都有哪些|有哪些).{0,24}(设备|装备)", t):
        return True
    if (
        re.search(r"(哪些|哪一些|什么|都有哪些|有哪些).{0,24}(设备|装备)", t)
        and re.search(r"(有|具备|关联|涉及|考核).{0,16}指标", t)
    ):
        return True
    if looks_like_type_equipment_relation(t) or looks_like_equipment_belongs_to_type(t):
        return False
    return False


def looks_like_projects_by_method(text: str) -> bool:
    """某试验方法涉及哪些试验项目（反查）。"""
    t = (text or "").strip()
    if not t or not _METHOD_GOAL.search(t):
        return False
    if re.search(r"(设备|装备|环境)", t) and not _REVERSE_PROJECT_BY_METHOD.search(t):
        return False
    if _REVERSE_PROJECT_BY_METHOD.search(t):
        return True
    if re.search(r"(试验|测试)?\s*方法.{0,48}(哪些|哪一些|什么|都有哪些|有哪些).{0,24}(试验|测试)?\s*项目", t):
        return True
    if (
        re.search(r"(哪些|哪一些|什么|都有哪些|有哪些).{0,24}(试验|测试)?\s*项目", t)
        and _METHOD_GOAL.search(t)
        and not re.search(r"(设备|装备|环境)", t)
    ):
        return True
    return False


def looks_like_equipment_by_project(text: str) -> bool:
    """某试验项目涉及哪些装备（反查，含「项目…哪些设备要做」语序）。"""
    t = (text or "").strip()
    if not t or _METHOD_GOAL.search(t):
        return False
    if not re.search(r"项目", t):
        return False
    if _REVERSE_EQUIP_BY_PROJECT.search(t) or _REVERSE_EQUIP_BY_PROJECT_ALT.search(t):
        return True
    if re.search(r"项目.{0,48}(哪些|哪一些|什么|都有哪些|有哪些).{0,24}(设备|装备)", t):
        return True
    if (
        re.search(r"(哪些|哪一些|什么|都有哪些|有哪些).{0,24}(设备|装备)", t)
        and re.search(r"(有|具备|关联|要做|需要|涉及).{0,16}项目", t)
    ):
        return True
    if looks_like_type_equipment_relation(t) or looks_like_equipment_belongs_to_type(t):
        return False
    return False


def looks_like_object_project_relation(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if relation_answer_entity_type(t) in ("equipment", "environment"):
        return False
    if _OBJECT_PROJECT_REL.search(t):
        return True
    if re.search(r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*的\s*(?:试验项目|测试项目)", t):
        return True
    return False


def relation_answer_entity_type(text: str) -> str:
    """
    推断用户要列出的实体类型（答案侧）。
    equipment | environment | indicator | project | method | unknown
    """
    t = (text or "").strip()
    if not t:
        return "unknown"
    if looks_like_equipment_by_project(t) or _REVERSE_EQUIP_BY_PROJECT.search(t) or (
        re.search(r"(哪些|哪一些|什么|都有哪些|有哪些).{0,20}(设备|装备)", t)
        and re.search(r"(有|具备|关联|要做|需要|涉及).{0,16}项目", t)
        and not _METHOD_GOAL.search(t)
    ):
        return "equipment"
    if looks_like_projects_by_method(t) or _REVERSE_PROJECT_BY_METHOD.search(t):
        return "project"
    if _REVERSE_ENV_BY_PROJECT.search(t) or (
        re.search(r"(哪些|哪一些|什么|都有哪些|有哪些).{0,20}环境", t)
        and re.search(r"(有|具备|关联).{0,16}项目", t)
    ):
        return "environment"
    if looks_like_equipment_by_indicator(t) or _REVERSE_EQUIP_BY_INDICATOR.search(t) or (
        re.search(r"(哪些|哪一些|什么|都有哪些|有哪些).{0,20}(设备|装备)", t)
        and re.search(r"(有|具备|关联).{0,16}指标", t)
    ):
        return "indicator"
    if _REVERSE_PROJECT_BY_METHOD.search(t) or (
        re.search(r"(哪些|哪一些|什么|都有哪些|有哪些).{0,20}(?:试验|测试)?\s*项目", t)
        and _METHOD_GOAL.search(t)
        and not re.search(r"(设备|装备|环境)", t)
    ):
        return "project"
    if re.search(r"(测试项目|试验项目)", t) and _METHOD_GOAL.search(t):
        return "method"
    if _METHOD_GOAL.search(t) and "项目" not in t and "指标" not in t:
        return "method"
    if _INDICATOR_GOAL.search(t):
        return "indicator"
    if _PROJECT_GOAL.search(t) or "试验" in t or "测试" in t:
        return "project"
    return "unknown"


def looks_like_reverse_relation(text: str) -> bool:
    """是否「向前查找」：已知锚点实体，查关联的另一端列表。"""
    t = (text or "").strip()
    if not t:
        return False
    if relation_answer_entity_type(t) in ("equipment", "environment", "indicator", "project"):
        if re.search(r"(哪些|哪一些|什么|都有哪些|有哪些)", t):
            return True
    return bool(
        looks_like_equipment_by_project(t)
        or looks_like_equipment_by_indicator(t)
        or looks_like_projects_by_method(t)
        or _REVERSE_EQUIP_BY_PROJECT.search(t)
        or _REVERSE_EQUIP_BY_PROJECT_ALT.search(t)
        or _REVERSE_ENV_BY_PROJECT.search(t)
        or _REVERSE_EQUIP_BY_INDICATOR.search(t)
        or _REVERSE_PROJECT_BY_METHOD.search(t)
    )


def _extract_anchor_focus(text: str, anchor_type: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if anchor_type == "TestProject":
        for pat in (
            r"(?:有|具备|关联).{0,8}([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:的)?\s*(?:试验|测试)?\s*项目",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:试验|测试)\s*项目",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:试验|测试)(?!方法)",
        ):
            m = re.search(pat, t)
            if m:
                core = re.sub(r"\s+", "", (m.group(1) or "").strip())
                core = re.sub(r"(的|与|和|及)$", "", core).strip()
                if len(core) >= 2 and core not in ("测试", "试验", "设备", "装备", "哪些", "什么", "环境"):
                    return core
    if anchor_type == "TestMethod":
        m = re.search(
            r"(?:有|具备|关联|使用|采用).{0,12}([\u4e00-\u9fffA-Za-z0-9·\-/\.]{2,40}?)\s*(?:试验|测试)?\s*方法",
            t,
        )
        if m:
            return re.sub(r"\s+", "", (m.group(1) or "").strip())
    if anchor_type == "Indicator":
        m = re.search(r"(?:有|具备|关联).{0,12}([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)指标", t)
        if m:
            return re.sub(r"\s+", "", (m.group(1) or "").strip())
    return ""


def resolve_reverse_rel_params(
    question: str,
    retrieval_question: str = "",
    *,
    node_type: str = "",
    node_name: str = "",
    node_id: str = "",
    params_draft: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """向前查找：MATCH (from)-[rel]->(to) WHERE 过滤 to 端，RETURN from 列表。"""
    draft = params_draft or {}
    text = f"{question}\n{retrieval_question}"
    answer = relation_answer_entity_type(text)
    if answer not in _REVERSE_TRIPLES:
        answer = "equipment" if looks_like_reverse_relation(text) else "project"
    fr, to, rel = _REVERSE_TRIPLES.get(answer, _REVERSE_TRIPLES["equipment"])

    draft_fr = str(draft.get("from_type") or "").strip()
    draft_to = str(draft.get("to_type") or "").strip()
    draft_rel = str(draft.get("rel_type") or draft.get("relation_type") or "CON").strip()
    if draft_fr and draft_to and draft_rel and _relation_allowed(draft_fr, draft_rel, draft_to):
        if str(draft.get("where_side") or "").lower() == "to" or str(draft.get("result_type") or "") == draft_fr:
            fr, to, rel = draft_fr, draft_to, draft_rel

    focus = str(draft.get("focus") or "").strip()
    focus_id = str(draft.get("focus_id") or "").strip()
    if node_type == to:
        focus = focus or node_name
        focus_id = focus_id or node_id
    elif not focus:
        focus = _extract_anchor_focus(text, to) or node_name
        if node_type == to and node_id:
            focus_id = node_id

    anchor_kws: List[str] = []
    if to == "TestProject":
        anchor_kws = draft.get("project_keywords") if isinstance(draft.get("project_keywords"), list) else []
    elif to == "Indicator":
        anchor_kws = draft.get("indicator_keywords") if isinstance(draft.get("indicator_keywords"), list) else []
    elif to == "TestMethod":
        anchor_kws = draft.get("method_keywords") if isinstance(draft.get("method_keywords"), list) else []
    anchor_kws = [str(x).strip() for x in anchor_kws if str(x).strip()]
    if not anchor_kws and focus:
        anchor_kws = [focus]

    out: Dict[str, Any] = {
        "from_type": fr,
        "to_type": to,
        "rel_type": rel,
        "where_side": "to",
        "result_type": fr,
        "anchor_type": to,
        "focus": focus,
    }
    if len(anchor_kws) > 1:
        out["match_mode"] = "fuzzy_any"
        out["value_list"] = anchor_kws
        out["focus"] = ""
    elif anchor_kws:
        out["focus"] = anchor_kws[0]
    if focus_id:
        out["focus_id"] = focus_id
    return out


def _relation_allowed(fr: str, rel: str, to: str) -> bool:
    return (fr, to) in (RELATION_SCHEMA.get(rel) or [])


def _pick_to_type(from_type: str, text: str, draft: Dict[str, Any]) -> str:
    t = text or ""
    draft_to = str(draft.get("to_type") or "").strip()
    has_project = bool(_PROJECT_GOAL.search(t)) or (
        "项目" in t and not _METHOD_GOAL.search(t) and "指标" not in t
    )
    has_method = bool(_METHOD_GOAL.search(t))
    has_indicator = bool(_INDICATOR_GOAL.search(t))

    if from_type == "Equipment":
        if has_indicator or looks_like_object_indicator_relation(t):
            return "Indicator"
        if has_project or looks_like_object_project_relation(t):
            return "TestProject"
        if draft_to and _relation_allowed(from_type, "CON", draft_to):
            return draft_to
        return "TestProject"
    if from_type == "Environment":
        return "TestProject"
    if from_type == "TestProject":
        if has_method:
            return "TestMethod"
        if has_indicator:
            return "Indicator"
        if draft_to and _relation_allowed(from_type, "CON", draft_to):
            return draft_to
        return "TestMethod"
    if from_type == "Indicator":
        return "TestMethod"
    if draft_to and _relation_allowed(from_type, "CON", draft_to):
        return draft_to
    return "TestMethod"


def resolve_rel_one_hop_params(
    question: str,
    retrieval_question: str = "",
    *,
    node_type: str = "",
    node_name: str = "",
    node_id: str = "",
    params_draft: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    draft = params_draft or {}
    text = f"{question}\n{retrieval_question}"
    fr = str(draft.get("from_type") or node_type or "TestProject").strip()
    rel = str(draft.get("rel_type") or "CON").strip()
    to = _pick_to_type(fr, text, draft)
    if not _relation_allowed(fr, rel, to):
        if fr == "Equipment":
            to = (
                "Indicator"
                if (_INDICATOR_GOAL.search(text) or looks_like_object_indicator_relation(text))
                else "TestProject"
            )
        elif fr == "TestProject":
            to = "TestMethod"
    focus = str(draft.get("focus") or node_name or "").strip()
    out: Dict[str, Any] = {
        "from_type": fr,
        "to_type": to,
        "rel_type": rel,
        "focus": focus,
    }
    nid = str(draft.get("focus_id") or node_id or "").strip()
    if nid:
        out["focus_id"] = nid
    return out
