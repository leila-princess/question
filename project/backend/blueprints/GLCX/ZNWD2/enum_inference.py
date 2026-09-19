"""枚举类问句识别（对齐 ZNWD1 enum_inference.py）。"""

from __future__ import annotations

import re

from .schema import NODE_LABELS


def looks_like_enumeration(text: str) -> bool:
    """「有哪些设备类型/列举全部环境」等全库枚举问句。"""
    t = (text or "").strip()
    if not t:
        return False
    if _looks_like_type_equipment_relation(t):
        return False
    if re.search(
        r"(?:查询|请问|想了解一下|想了解)?\s*[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*的\s*(?:测试|试验)?\s*"
        r"(?:项目|指标|方法|测试方法|试验方法)\s*(?:有哪些|都有什么|包括哪些|是什么)",
        t,
    ):
        return False
    if re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*(?:有什么|有哪些|包括哪些)\s*"
        r"(?:测试项目|试验项目|指标|方法|测试方法|试验方法)",
        t,
    ) and not re.search(r"(全部|所有|整个库|整个系统|一共)", t):
        return False
    if re.search(r"(有哪些|都有什么|列出|列举|显示|查看|全部|所有)", t) and re.search(
        r"(设备类型|装备类型|设备类别|环境类型|试验项目|测试项目|指标|测试方法|试验方法|文件|环境|设备|装备)",
        t,
    ):
        return True
    if re.search(r"(设备类型|装备类型|环境类型)\s*(有哪些|是什么|都有什么|包括哪些)", t):
        return True
    return False


def _looks_like_type_equipment_relation(text: str) -> bool:
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
    type_eq = re.compile(
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24})\s*(?:有什么|有哪些|包括哪些)\s*(?:具体)?\s*(?:的)?\s*(?:设备|装备)",
        re.I,
    )
    type_eq_alt = re.compile(
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24})\s*(?:的|下)\s*(?:具体)?\s*(?:设备|装备)",
        re.I,
    )
    return bool(type_eq.search(t) or type_eq_alt.search(t))


def infer_enum_node_type(question: str) -> str:
    """从问句推断列举的节点标签。"""
    t = (question or "").strip()
    if "设备类型" in t or "装备类型" in t or "设备类别" in t:
        return "EquipmentType"
    if ("设备" in t or "装备" in t) and "类型" not in t:
        if re.search(r"(有哪些|都有什么|列出|列举|全部|所有)", t):
            if "项目" not in t and "指标" not in t and "方法" not in t:
                return "Equipment"
    if "环境类型" in t or (
        re.search(r"(有哪些|都有什么|列出|列举|全部|所有)", t) and "环境" in t and "项目" not in t
    ):
        return "Environment"
    if "测试项目" in t or "试验项目" in t or (
        "项目" in t and ("测试" in t or "试验" in t) and re.search(r"(有哪些|都有什么|列出|列举|全部|所有)", t)
    ):
        return "TestProject"
    if "指标" in t and re.search(r"(有哪些|都有什么|列出|列举|全部|所有)", t):
        return "Indicator"
    if ("测试方法" in t or "试验方法" in t or "方法" in t) and re.search(
        r"(有哪些|都有什么|列出|列举|全部|所有)", t
    ):
        return "TestMethod"
    if "文件" in t and re.search(r"(有哪些|都有什么|列出|列举|全部|所有)", t):
        return "File"
    if re.search(r"(有哪些|都有什么).{0,6}类型", t):
        return "EquipmentType"
    return "EquipmentType"


def resolve_enum_node_type(question: str, draft_type: str = "") -> str:
    inferred = infer_enum_node_type(question)
    if draft_type not in NODE_LABELS:
        return inferred
    if draft_type == inferred:
        return draft_type
    t = (question or "").strip()
    if ("设备类型" in t or "装备类型" in t) and draft_type == "TestProject":
        return "EquipmentType"
    if "环境" in t and "项目" not in t and draft_type == "TestProject":
        return "Environment"
    return inferred
