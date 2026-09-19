"""环境节点属性问答：问句字段识别、关键词清洗、检索行收窄。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_ENV_KEYWORD_NOISE = frozenset(
    {
        "分类",
        "分级",
        "组合",
        "约束",
        "要求",
        "属性",
        "环境",
        "试验环境",
        "测试环境",
        "有哪些",
        "有什么",
        "是什么",
        "怎样",
        "如何",
        "介绍",
        "说明",
        "描述",
        "讲讲",
        "说说",
        "一下",
    }
)

_DEFAULT_ENV_PROPERTY_FIELDS = ["Type", "Class", "Combination", "Limit", "Request", "Name", "Description"]

_FIELD_PATTERNS: List[tuple[str, List[str]]] = [
    ("Type", [r"分类", r"类型(?!名)", r"\btype\b"]),
    ("Class", [r"分级", r"等级", r"\bclass\b", r"\blevel\b"]),
    ("Combination", [r"典型组合", r"组合", r"\bcombination\b"]),
    ("Limit", [r"约束", r"限制", r"\blimit\b", r"\bconstraint\b"]),
    ("Request", [r"要求", r"\brequest\b", r"\brequirement\b"]),
    ("Name", [r"名称", r"叫什么", r"名字"]),
    ("Description", [r"描述", r"简介", r"\bdescription\b"]),
]


def detect_environment_property_fields(question: str) -> List[str]:
    """从问句识别用户关心的 Environment 属性字段；未指明时返回空（由调用方决定默认）。"""
    q = (question or "").strip()
    if not q:
        return []
    found: List[str] = []
    for field, patterns in _FIELD_PATTERNS:
        for pat in patterns:
            if re.search(pat, q, re.I):
                found.append(field)
                break
    out: List[str] = []
    for f in found:
        if f not in out:
            out.append(f)
    return out


def resolve_environment_property_fields(question: str) -> List[str]:
    """问句未指明具体字段时，介绍/属性类问句返回全套；仅「分类」等则只返回对应字段。"""
    fields = detect_environment_property_fields(question)
    if not fields:
        q = (question or "").strip()
        if re.search(r"介绍|说明|描述|属性|讲讲|说说", q):
            return list(_DEFAULT_ENV_PROPERTY_FIELDS)
        return list(_DEFAULT_ENV_PROPERTY_FIELDS)
    if "Description" not in fields:
        fields.append("Description")
    return fields


def sanitize_environment_keywords(keywords: List[str]) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for raw in keywords or []:
        s = str(raw or "").strip()
        if not s or s in _ENV_KEYWORD_NOISE or s in seen:
            continue
        if len(s) < 2:
            continue
        seen.add(s)
        out.append(s)
    return out


def _node_props(row: Dict[str, Any]) -> Dict[str, Any]:
    n = row.get("n")
    if not isinstance(n, dict):
        return {}
    props = n.get("properties")
    return props if isinstance(props, dict) else {}


def _node_id(row: Dict[str, Any]) -> str:
    props = _node_props(row)
    return str(props.get("Id") or props.get("id") or "").strip()


def _node_name(row: Dict[str, Any]) -> str:
    props = _node_props(row)
    return str(props.get("Name") or props.get("name") or "").strip()


def narrow_environment_property_rows(
    neo_rows: List[Dict[str, Any]],
    *,
    focus: str = "",
    focus_id: str = "",
) -> List[Dict[str, Any]]:
    """环境属性问答：多命中时优先 focus_id / 环境名精确匹配，避免 CONTAINS 扩召回。"""
    rows = [r for r in (neo_rows or []) if isinstance(r, dict) and not r.get("error")]
    if not rows:
        return rows
    fid = (focus_id or "").strip()
    if fid:
        exact = [r for r in rows if _node_id(r) == fid]
        if exact:
            return exact
    name = (focus or "").strip()
    if name:
        norm = re.sub(r"\s+", "", name).lower()
        exact_name = [
            r
            for r in rows
            if re.sub(r"\s+", "", _node_name(r)).lower() == norm
        ]
        if exact_name:
            return exact_name
        contains = [
            r
            for r in rows
            if norm in re.sub(r"\s+", "", _node_name(r)).lower()
        ]
        if len(contains) == 1:
            return contains
        if contains:
            return contains[:1]
    return rows[:1] if len(rows) > 1 else rows


def apply_environment_property_plan_slots(
    slots: Dict[str, Any],
    question: str,
    probe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out = dict(slots or {})
    out["property_fields"] = resolve_environment_property_fields(question)
    env_kws = out.get("environment_keywords") if isinstance(out.get("environment_keywords"), list) else []
    cleaned = sanitize_environment_keywords([str(x) for x in env_kws])
    if cleaned:
        out["environment_keywords"] = cleaned
    elif probe:
        pname = str(probe.get("name") or "").strip()
        if pname:
            out["environment_keywords"] = [pname]
    if probe and str(probe.get("node_type") or "") == "Environment":
        pid = str(probe.get("id") or probe.get("node_id") or "").strip()
        if pid:
            out["focus_id"] = pid
    return out
