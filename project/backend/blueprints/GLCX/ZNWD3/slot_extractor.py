"""
ZNWD3 槽位提取：基于关键词匹配和正则从问题中提取槽位。
"""

from __future__ import annotations

import re
from typing import Dict, List

SLOT_KEYS = ["equipment", "environment", "project", "indicator", "standard", "equipment_type"]

_SLOT_KEYWORDS: Dict[str, List[str]] = {
    "equipment": [
        "无人机", "坦克", "装甲", "雷达", "导弹", "舰船", "飞机", "直升机",
        "战车", "自行火炮", "装备", "设备", "武器", "平台", "系统",
        "汽车", "火炮", "火箭", "卫星",
    ],
    "environment": [
        "复杂电磁", "复杂地理", "盐雾", "高温", "低温", "湿热", "干热",
        "寒冷", "振动", "冲击", "淋雨", "沙尘", "高原", "高海拔", "深海",
        "极寒", "酷热", "环境", "气候", "条件",
    ],
    "project": [
        "试验项目", "测试项目", "项目", "试验", "测试", "鉴定", "定型",
        "考核", "验证",
    ],
    "indicator": [
        "指标", "考核指标", "性能指标", "试验指标", "测试指标",
    ],
    "standard": [
        "标准", "GB", "GJB", "规范", "条文", "章节",
    ],
    "equipment_type": [
        "装备类型", "设备类型", "地面装备", "空中装备", "海上装备",
        "陆地", "航空", "舰载", "车载",
    ],
}


def extract_slots(text: str) -> Dict[str, List[str]]:
    t = (text or "").strip().lower()
    slots: Dict[str, List[str]] = {k: [] for k in SLOT_KEYS}
    for slot_key, keywords in _SLOT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in t:
                slots[slot_key].append(kw)
    equip_match = re.search(r"^(.+?)(在|的|有|进行|做|开展|呢|啊|吗)", t)
    if equip_match:
        candidate = equip_match.group(1).strip()
        candidate = re.sub(r"^(请问|问下|帮我查|查询|那|换)", "", candidate).strip()
        if candidate and len(candidate) <= 20:
            is_keyword = any(candidate in kw for kws in _SLOT_KEYWORDS.values() for kw in kws)
            if not is_keyword and not slots["equipment"]:
                slots["equipment"] = [candidate]
    return slots


def has_slot(slots: Dict[str, List[str]], slot_key: str) -> bool:
    return bool(slots.get(slot_key))


def extract_slot_value(slots: Dict[str, List[str]], slot_key: str) -> str:
    vals = slots.get(slot_key, [])
    return vals[0] if vals else ""


def detect_intent(text: str, slots: Dict[str, List[str]]) -> str:
    t = (text or "").strip().lower()
    if re.match(r"^(你好|您好|嗨|hello|hi|在吗|谢谢|感谢|再见|拜拜)\s*[!！。.?？]*$", t, re.I):
        return "greeting"
    if any(kw in t for kw in ["具体内容", "正文", "步骤", "怎么做", "如何进行"]):
        return "content"
    if has_slot(slots, "standard") or any(kw in t for kw in ["标准", "条文", "章节"]):
        return "document"
    if has_slot(slots, "indicator") or any(kw in t for kw in ["考核指标", "性能指标", "指标"]):
        return "indicator"
    if has_slot(slots, "project") or any(kw in t for kw in ["试验项目", "测试项目"]):
        return "project"
    if any(kw in t for kw in ["方法", "试验方法", "测试方法"]):
        return "method"
    if has_slot(slots, "environment"):
        return "environment"
    if has_slot(slots, "equipment"):
        return "equipment"
    return "enum"


def merge_slots(current: Dict[str, List[str]], prev: Dict[str, List[str]]) -> Dict[str, List[str]]:
    merged = {k: list(v) for k, v in current.items()}
    for k in SLOT_KEYS:
        if not merged.get(k) and prev.get(k):
            merged[k] = list(prev[k])
    return merged
