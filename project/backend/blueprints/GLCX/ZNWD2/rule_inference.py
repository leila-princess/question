"""问句规则推断：在 LLM 之前/之后校正意图（对齐 ZNWD1 消歧）。"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .enum_inference import infer_enum_node_type, looks_like_enumeration, resolve_enum_node_type
from .schema import NODE_LABELS
from .types import IntentJson


def extract_type_focus_for_equipment(text: str) -> str:
    """「地面装备有哪些具体设备」→ 地面装备"""
    patterns = (
        re.compile(
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24})\s*(?:有什么|有哪些|包括哪些)\s*(?:具体)?\s*(?:的)?\s*(?:设备|装备)",
            re.I,
        ),
        re.compile(
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24})\s*(?:的|下)\s*(?:具体)?\s*(?:设备|装备)",
            re.I,
        ),
    )
    for pat in patterns:
        m = pat.search(text or "")
        if m:
            focus = re.sub(r"\s+", "", (m.group(1) or "").strip())
            focus = re.sub(r"(的|下|中)$", "", focus).strip()
            if len(focus) >= 2 and "设备类型" not in focus and "装备类型" not in focus:
                return focus
    return ""


def looks_like_type_equipment_relation(text: str) -> bool:
    t = (text or "").strip()
    if not t or "设备类型" in t or "装备类型" in t:
        return False
    return bool(extract_type_focus_for_equipment(t))


def looks_like_object_indicator_relation(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*(?:有什么|有哪些|包括哪些).{0,12}"
        r"(?:考核指标|性能指标|试验指标|测试指标|指标)",
        t,
    ):
        return True
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*的\s*(?:考核|性能|试验|测试)?\s*指标", t))


def looks_like_object_project_relation(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.search(r"(?:设备类型|装备类型|环境类型)", t):
        return False
    if re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*(?:有什么|有哪些|包括哪些).{0,10}"
        r"(?:试验项目|测试项目|项目)",
        t,
    ):
        return True
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*的\s*(?:试验项目|测试项目)", t))


def _extract_entity_keyword(text: str, *, stop_words: tuple[str, ...] = ()) -> str:
    """从「XX有哪些指标/项目」中提取 XX。"""
    patterns = (
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24})\s*(?:有什么|有哪些|包括哪些)",
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24})\s*的",
    )
    for pat in patterns:
        m = re.search(pat, text or "")
        if m:
            kw = re.sub(r"\s+", "", (m.group(1) or "").strip())
            for sw in stop_words:
                if sw in kw:
                    kw = kw.replace(sw, "").strip()
            if len(kw) >= 2:
                return kw
    return ""


def infer_intent_from_rules(user_query: str) -> Optional[Dict[str, Any]]:
    """规则优先推断；命中则可直接返回，无需 LLM。"""
    q = (user_query or "").strip()
    if not q:
        return None

    if looks_like_enumeration(q):
        node_type = infer_enum_node_type(q)
        return {
            "intent_type": "列举",
            "start_entities": [node_type],
            "target_entities": [node_type],
            "start_filters": None,
            "environment_constraint": None,
            "additional_filters": None,
        }

    if looks_like_type_equipment_relation(q):
        focus = extract_type_focus_for_equipment(q)
        sf = {"Name": focus} if focus else None
        return {
            "intent_type": "查关联",
            "start_entities": ["EquipmentType"],
            "target_entities": ["Equipment"],
            "start_filters": sf,
            "environment_constraint": None,
            "additional_filters": None,
        }

    if looks_like_object_indicator_relation(q):
        kw = _extract_entity_keyword(q, stop_words=("考核", "性能", "试验", "测试"))
        return {
            "intent_type": "查指标",
            "start_entities": ["Equipment"],
            "target_entities": ["Indicator"],
            "start_filters": {"Name": kw} if kw else None,
            "environment_constraint": None,
            "additional_filters": None,
        }

    if looks_like_object_project_relation(q):
        kw = _extract_entity_keyword(q, stop_words=("试验", "测试"))
        return {
            "intent_type": "查项目",
            "start_entities": ["Equipment"],
            "target_entities": ["TestProject"],
            "start_filters": {"Name": kw} if kw else None,
            "environment_constraint": None,
            "additional_filters": None,
        }

    return None


def correct_intent_from_rules(user_query: str, intent: IntentJson) -> IntentJson:
    """LLM 结果校正：枚举问句不得落入查指标等。"""
    q = (user_query or "").strip()
    if looks_like_enumeration(q):
        node_type = resolve_enum_node_type(
            q,
            str((intent.get("target_entities") or [""])[0]),
        )
        return IntentJson(
            intent_type="列举",
            start_entities=[node_type],
            target_entities=[node_type],
            start_filters=None,
            environment_constraint=None,
            additional_filters=None,
        )

    if looks_like_type_equipment_relation(q) and intent.get("intent_type") != "查关联":
        focus = extract_type_focus_for_equipment(q)
        return IntentJson(
            intent_type="查关联",
            start_entities=["EquipmentType"],
            target_entities=["Equipment"],
            start_filters={"Name": focus} if focus else None,
            environment_constraint=None,
            additional_filters=None,
        )

    return intent
