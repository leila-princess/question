"""从标准节段文本抽取结构化条件参数（规则，无 LLM）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

TEMP_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*[°℃]\s*C?|(-?\d+(?:\.\d+)?)\s*摄氏度",
    re.I,
)
TIME_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(h|min|s|小时|分钟|秒)\b",
    re.I,
)
HUMIDITY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?:RH|相对湿度)?|相对湿度\s*(\d+(?:\.\d+)?)\s*%",
    re.I,
)
RANGE_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*[~～\-—]\s*(-?\d+(?:\.\d+)?)\s*([°℃%h小时分钟秒]*)?",
)
TOL_RE = re.compile(r"±\s*(\d+(?:\.\d+)?)\s*([°℃%]?)?")
OP_PATTERNS = [
    (re.compile(r"不大于|不超过|至多"), "<="),
    (re.compile(r"不小于|至少|不少于"), ">="),
]

_TIME_UNIT_MAP = {
    "h": "h",
    "min": "min",
    "s": "s",
    "小时": "h",
    "分钟": "min",
    "秒": "s",
}


def _normalize_unit_time(raw: str) -> str:
    return _TIME_UNIT_MAP.get((raw or "").lower(), raw or "")


def _make_condition(
    param_type: str,
    value: str,
    unit: str = "",
    op: str = "=",
    clause_no: str = "",
    slot: str = "",
    raw: str = "",
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "type": param_type,
        "value": value,
        "unit": unit,
        "op": op,
    }
    if clause_no:
        item["clause_no"] = clause_no
    if slot:
        item["slot"] = slot
    if raw:
        item["raw"] = raw[:200]
    return item


def _detect_op(context: str) -> str:
    for pat, op in OP_PATTERNS:
        if pat.search(context or ""):
            return op
    return "="


def extract_conditions_from_text(
    text: str,
    *,
    clause_no: str = "",
    slot: str = "",
) -> List[Dict[str, Any]]:
    if not (text or "").strip():
        return []
    out: List[Dict[str, Any]] = []
    seen = set()

    def add(item: Dict[str, Any]) -> None:
        key = (item.get("type"), item.get("value"), item.get("unit"), item.get("op"))
        if key in seen:
            return
        seen.add(key)
        out.append(item)

    for m in TEMP_RE.finditer(text):
        val = m.group(1) or m.group(2)
        ctx = text[max(0, m.start() - 20): m.end() + 20]
        add(_make_condition("temperature", val, "℃", _detect_op(ctx), clause_no, slot, m.group(0)))

    for m in TIME_RE.finditer(text):
        val, unit_raw = m.group(1), m.group(2)
        unit = _normalize_unit_time(unit_raw)
        ctx = text[max(0, m.start() - 20): m.end() + 20]
        add(_make_condition("duration", val, unit, _detect_op(ctx), clause_no, slot, m.group(0)))

    for m in HUMIDITY_RE.finditer(text):
        val = m.group(1) or m.group(2)
        ctx = text[max(0, m.start() - 20): m.end() + 20]
        add(_make_condition("humidity", val, "%", _detect_op(ctx), clause_no, slot, m.group(0)))

    for m in RANGE_RE.finditer(text):
        u = (m.group(3) or "").strip()
        ptype = "range"
        unit = ""
        if "℃" in u or "°" in u:
            ptype, unit = "temperature", "℃"
        elif "%" in u:
            ptype, unit = "humidity", "%"
        elif u:
            ptype, unit = "duration", _normalize_unit_time(u)
        add(_make_condition(
            ptype,
            f"{m.group(1)}~{m.group(2)}",
            unit,
            "=",
            clause_no,
            slot,
            m.group(0),
        ))

    for m in TOL_RE.finditer(text):
        add(_make_condition("tolerance", m.group(1), (m.group(2) or "").strip(), "±", clause_no, slot, m.group(0)))

    return out


def extract_conditions(sections: List[Dict[str, Any]], buckets: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for slot, secs in (buckets or {}).items():
        for sec in secs or []:
            clause_no = str(sec.get("section_no") or "").strip()
            text = str(sec.get("text") or "")
            out.extend(extract_conditions_from_text(text, clause_no=clause_no, slot=slot))
    return out
