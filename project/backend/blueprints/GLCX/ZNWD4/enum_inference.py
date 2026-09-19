"""
枚举类问句：识别「有哪些设备类型」等并推断 Neo4j 节点标签（不 import ZNWD）。
"""

from __future__ import annotations

import re
from typing import Any, Dict

from .meta_ops import NODE_LABELS
from .rel_inference import looks_like_type_equipment_relation

_LABEL_ZH = {
    "EquipmentType": "设备类型",
    "Equipment": "设备",
    "Environment": "试验环境",
    "TestProject": "测试项目",
    "Indicator": "指标",
    "TestMethod": "测试方法",
    "File": "文件",
}


def node_type_zh(label: str) -> str:
    return _LABEL_ZH.get(label, label)


def looks_like_enumeration(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if looks_like_type_equipment_relation(t):
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


def infer_enum_node_type(question: str, retrieval_question: str = "") -> str:
    """从问句推断 neo_list_nodes 的 node_type。"""
    t = (question or retrieval_question or "").strip()
    if "设备类型" in t or "装备类型" in t or "设备类别" in t:
        return "EquipmentType"
    if ("设备" in t or "装备" in t) and "类型" not in t:
        if re.search(r"(有哪些|都有什么|列出|列举)", t):
            if "项目" not in t and "指标" not in t and "方法" not in t:
                return "Equipment"
    if "环境类型" in t or (re.search(r"(有哪些|都有什么|列出|列举)", t) and "环境" in t and "项目" not in t):
        return "Environment"
    if "测试项目" in t or "试验项目" in t or (
        "项目" in t and ("测试" in t or "试验" in t) and re.search(r"(有哪些|都有什么|列出|列举|全部|所有)", t)
    ):
        return "TestProject"
    if "指标" in t and re.search(r"(有哪些|都有什么|列出|列举)", t):
        return "Indicator"
    if ("测试方法" in t or "试验方法" in t or "方法" in t) and re.search(
        r"(有哪些|都有什么|列出|列举)", t
    ):
        return "TestMethod"
    if "文件" in t and re.search(r"(有哪些|都有什么|列出|列举)", t):
        return "File"
    # 泛化「有哪些类型」默认设备类型，而非测试项目
    if re.search(r"(有哪些|都有什么).{0,6}类型", t):
        return "EquipmentType"
    # 「有哪些装备/设备」等未命中上文时默认具体装备，勿误枚举 EquipmentType
    return "Equipment"


def resolve_enum_node_type(
    question: str,
    retrieval_question: str = "",
    params_draft: Dict[str, Any] | None = None,
) -> str:
    """规则优先；LLM draft 仅在合法且与问句一致时采用。"""
    inferred = infer_enum_node_type(question, retrieval_question)
    draft = params_draft or {}
    draft_type = str(draft.get("node_type") or "").strip()
    if draft_type not in NODE_LABELS:
        return inferred
    if draft_type == inferred:
        return draft_type
    # 问句已明确设备类型时，勿采用 LLM 误填的 TestProject
    t = (question or retrieval_question or "").strip()
    if ("设备类型" in t or "装备类型" in t) and draft_type == "TestProject":
        return "EquipmentType"
    if "环境" in t and "项目" not in t and draft_type == "TestProject":
        return "Environment"
    return inferred
