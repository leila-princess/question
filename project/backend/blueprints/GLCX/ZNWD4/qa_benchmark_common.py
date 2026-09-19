"""
ZNWD4 智能问答评测公共模块：题库 schema、gold 标注、维度/难度分类。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .routing_catalog import ROUTE_CATALOG, get_route

# category → (route_id, purpose, probe_labels)
CATEGORY_ROUTE: Dict[str, Tuple[str, str, List[str]]] = {
    "闲聊": ("R-CHAT-direct", "chat", []),
    "元信息": ("R-META-stats", "meta_info", []),
    "枚举-TestProject": ("R-TEST_PROJECT-enum", "enum_list", []),
    "枚举-Environment": ("R-ENVIRONMENT-list", "enum_list", []),
    "枚举-Indicator": ("R-INDICATOR-list_all", "enum_list", []),
    "枚举-Equipment": ("R-EQUIPMENT-list", "enum_list", []),
    "枚举-EquipmentType": ("R-EQUIPMENT_TYPE-level1_list", "enum_list", []),
    "装备-试验项目": ("R-TEST_PROJECT-by_equipment", "query_equipment_test_projects", ["Equipment"]),
    "环境-试验项目": ("R-TEST_PROJECT-by_environment", "query_environment_test_projects", ["Environment"]),
    "装备环境-试验项目": ("R-TEST_PROJECT-by_equip_and_env", "query_equip_env_test_projects", ["Equipment", "Environment"]),
    "装备-考核指标": ("R-INDICATOR-by_equipment", "query_equipment_indicators", ["Equipment"]),
    "装备类型-考核指标": ("R-INDICATOR-by_equip_type", "query_equip_type_indicators", ["EquipmentType"]),
    "项目-试验方法": ("R-TEST_METHOD-by_project", "query_project_methods", ["TestProject"]),
    "指标-试验方法": ("R-TEST_METHOD-by_indicator", "query_indicator_methods", ["Indicator"]),
    "装备-试验方法(经项目)": ("R-TEST_METHOD-by_equipment_via_project", "query_equipment_methods_via_project", ["Equipment"]),
    "装备-试验方法(经指标)": ("R-TEST_METHOD-by_equipment_via_indicator", "query_equipment_methods_via_indicator", ["Equipment"]),
    "装备类型-装备": ("R-EQUIPMENT-under_type", "query_equipment_under_type", ["EquipmentType"]),
    "装备类型-二级类别": ("R-EQUIPMENT_TYPE-level2_under_level1", "query_level2_equipment_types", ["EquipmentType"]),
    "装备-所属类型": ("R-EQUIPMENT_TYPE-belongs_to", "query_equipment_belongs_to_type", ["Equipment"]),
    "项目-装备": ("R-EQUIPMENT-by_project", "query_equipment_by_project", ["TestProject"]),
    "反向-项目-装备": ("R-EQUIPMENT-by_project", "query_equipment_by_project", ["TestProject"]),
    "指标-装备": ("R-EQUIPMENT-by_indicator", "query_equipment_by_indicator", ["Indicator"]),
    "反向-指标-装备": ("R-EQUIPMENT-by_indicator", "query_equipment_by_indicator", ["Indicator"]),
    "环境属性": ("R-ENVIRONMENT-properties", "query_environment_properties", ["Environment"]),
    "项目-环境": ("R-ENVIRONMENT-for_project", "query_project_environment", ["TestProject"]),
    "标准-项目": ("R-STANDARD-for_projects", "query_project_standards", ["TestProject"]),
    "标准-方法": ("R-STANDARD-methods_in_standard", "query_standard_methods", ["File"]),
    "标准-元数据": ("R-STANDARD-methods_in_standard", "query_standard_methods", ["File"]),
    "方法-SYNR": ("R-TEST_METHOD-content", "query_method_content", ["TestMethod"]),
    "方法-SYTJ": ("R-TEST_METHOD-content", "query_method_content", ["TestMethod"]),
    "方法-SYGC": ("R-TEST_METHOD-content", "query_method_content", ["TestMethod"]),
    "方法-JGPD": ("R-TEST_METHOD-content", "query_method_content", ["TestMethod"]),
    "向量库-文档概述": ("R-KNOWLEDGE-keyword_doc", "query_document", []),
    "向量库-章节": ("R-STANDARD-chapter_content", "query_document", []),
    "向量库-关键词": ("R-KNOWLEDGE-keyword_doc", "query_document", []),
    "拼装-项目+方法": ("R-C-PROJECT_THEN_METHOD-default", "composite_project_then_method", ["Equipment"]),
    "拼装-指标+方法": ("R-C-INDICATOR_THEN_METHOD-default", "composite_indicator_then_method", ["Equipment"]),
    "拼装-环境设置": (
        "R-C-EQUIP_PROJECT_ENV_SETTINGS-default",
        "composite_equip_project_env_settings",
        ["Equipment", "TestProject"],
    ),
    "多实体-装备环境项目": ("R-C-PROJECT_ENV_METHOD-default", "composite_project_env_method", ["Equipment", "Environment"]),
    "多步-项目再方法": ("R-C-PROJECT_THEN_METHOD-default", "composite_project_then_method", ["Equipment"]),
    "多步-指标再方法": ("R-C-INDICATOR_THEN_METHOD-default", "composite_indicator_then_method", ["Equipment"]),
    "多问-项目与指标": ("R-C-INTENT_PLAN-default", "composite_intent_plan", ["Equipment"]),
    "多问-环境与项目": ("R-C-INTENT_PLAN-default", "composite_intent_plan", ["Environment"]),
    "多问-项目方法环境": ("R-C-INTENT_PLAN-default", "composite_intent_plan", ["Equipment"]),
}

LABEL_SLOT_KEY: Dict[str, str] = {
    "Equipment": "equipment_keywords",
    "Environment": "environment_keywords",
    "TestProject": "project_keywords",
    "Indicator": "indicator_keywords",
    "EquipmentType": "equipment_type_keywords",
    "TestMethod": "method_keywords",
    "File": "standard_keywords",
}

DIMENSION_BY_CATEGORY: Dict[str, str] = {
    "闲聊": "boundary",
    "元信息": "boundary",
}

DIFFICULTY_BY_CATEGORY: Dict[str, str] = {
    "闲聊": "easy",
    "元信息": "easy",
}

# 问句实体抽取（简化版，覆盖主要模板）
QUESTION_ENTITY_PATTERNS: List[Tuple[str, str, str]] = [
    (r"^(.+?)在(.+?)环境下有哪些试验项目", "equip_env", "equip_env"),
    (r"^(.+?)在(.+?)条件下需要做哪些试验", "equip_env", "equip_env"),
    (r"^(.+?)做(.+?)试验时的环境条件", "composite_env_q", "composite_env"),
    (r"^(.+?)做(.+?)试验时的环境设置", "composite_env_q", "composite_env"),
    (r"^(.+?)有哪些试验项目和方法", "Equipment", "equipment_keywords"),
    (r"^(.+?)的考核指标和试验方法", "Equipment", "equipment_keywords"),
    (r"^(.+?)有哪些试验项目和考核指标", "Equipment", "equipment_keywords"),
    (r"^(.+?)环境下有哪些试验项目", "Environment", "environment_keywords"),
    (r"^在(.+?)条件下要做哪些试验", "Environment", "environment_keywords"),
    (r"^(.+?)环境对应的试验项目", "Environment", "environment_keywords"),
    (r"^(.+?)要考核哪些指标", "Equipment", "equipment_keywords"),
    (r"^(.+?)的考核指标有哪些", "Equipment", "equipment_keywords"),
    (r"^(.+?)有哪些试验项目", "Equipment", "equipment_keywords"),
    (r"^(.+?)的试验项目有哪些", "Equipment", "equipment_keywords"),
    (r"^查询(.+?)关联的试验项目", "Equipment", "equipment_keywords"),
    (r"^(.+?)有哪些试验方法", "Equipment", "equipment_keywords"),
    (r"^(.+?)的试验方法是什么", "TestProject", "project_keywords"),
    (r"^查询(.+?)对应的测试方法", "TestProject", "project_keywords"),
    (r"^(.+?)指标对应的试验方法", "Indicator", "indicator_keywords"),
    (r"^(.+?)类装备有哪些", "EquipmentType", "equipment_type_keywords"),
    (r"^(.+?)环境的分类和要求", "Environment", "environment_keywords"),
    (r"^介绍一下(.+?)试验环境", "Environment", "environment_keywords"),
    (r"^(.+?)标准包含哪些试验方法", "File", "standard_keywords"),
    (r"^(.+?)标准的主要内容", "File", "standard_keywords"),
    (r"^(.+?)第[\d.]+节的内容", "File", "standard_keywords"),
    (r"^(.+?)涉及哪些装备", "TestProject", "project_keywords"),
    (r"^(.+?)指标涉及哪些装备", "Indicator", "indicator_keywords"),
    (r"^(.+?)的试验方法内容", "TestMethod", "method_keywords"),
    (r"^(.+?)的具体步骤", "TestMethod", "method_keywords"),
    (r"^(.+?)方法正文", "TestMethod", "method_keywords"),
    (r"^(.+?)依据哪些标准", "TestProject", "project_keywords"),
    (r"^(.+?)在什么环境下试验", "TestProject", "project_keywords"),
    (r"^(.+?)属于什么装备类型", "Equipment", "equipment_keywords"),
    (r"^(.+?)属于哪类装备", "Equipment", "equipment_keywords"),
    (r"^请先告诉我(.+?)有哪些试验项目", "Equipment", "equipment_keywords"),
    (r"^先查询(.+?)的考核指标", "Equipment", "equipment_keywords"),
    (r"^(.+?)的属性是什么", "Environment", "environment_keywords"),
    (r"^(.+?)的说明是什么", "Equipment", "equipment_keywords"),
]

ANSWER_ENTITY_PATTERNS: List[Tuple[str, str, str]] = [
    (r"^(.+?)环境相关的试验项目", "Environment", "environment_keywords"),
    (r"^(.+?)相关的试验项目", "Equipment", "equipment_keywords"),
    (r"^(.+?)的考核指标", "Equipment", "equipment_keywords"),
    (r"试验项目「(.+?)」", "TestProject", "project_keywords"),
    (r"指标「(.+?)」", "Indicator", "indicator_keywords"),
    (r"装备类型「(.+?)」", "EquipmentType", "equipment_type_keywords"),
    (r"试验环境「(.+?)」", "Environment", "environment_keywords"),
    (r"标准/文件「(.+?)」", "File", "standard_keywords"),
    (r"文件「(.+?)」", "File", "standard_keywords"),
    (r"^(.+?)做「(.+?)」试验时的环境设置", "composite_env", "composite_env"),
]


def category_base(category: str) -> str:
    return (category or "").split("-变体")[0].strip()


def classify_dimension(category: str, question: str = "") -> str:
    base = category_base(category)
    if base in DIMENSION_BY_CATEGORY:
        return DIMENSION_BY_CATEGORY[base]
    if base.startswith("枚举") or base in ("闲聊", "元信息"):
        return "boundary"
    if base.startswith("多步"):
        return "multi_step"
    if base.startswith("多问"):
        return "multi_question"
    if base.startswith("拼装") or base.startswith("多实体"):
        return "composite"
    if base.startswith("向量库") or base.startswith("标准-"):
        return "vector"
    if "反向" in base or "经" in base or base.startswith("装备-试验方法"):
        return "multi_hop"
    if base == "装备环境-试验项目" or "和" in (question or ""):
        return "multi_entity"
    if base.startswith("属性-"):
        return "simple"
    return "simple"


def classify_difficulty(category: str, dimension: str = "") -> str:
    base = category_base(category)
    if base in DIFFICULTY_BY_CATEGORY:
        return DIFFICULTY_BY_CATEGORY[base]
    dim = dimension or classify_dimension(category)
    if dim == "boundary":
        return "easy"
    if dim in ("multi_step", "multi_question", "composite"):
        return "hard"
    if dim in ("multi_entity", "multi_hop", "vector"):
        return "medium"
    if base.startswith("方法-") or base == "环境属性":
        return "medium"
    return "easy"


def _entity(label: str, name: str, surface: str | None = None) -> Dict[str, Any]:
    canonical = (name or "").strip()
    surf = (surface or canonical).strip()
    return {
        "label": label,
        "canonical": canonical,
        "surface": surf,
        "slot_key": LABEL_SLOT_KEY.get(label, f"{label.lower()}_keywords"),
    }


def extract_entities_from_question(question: str, category: str = "") -> List[Dict[str, Any]]:
    q = (question or "").strip()
    for pat, label, slot_key in QUESTION_ENTITY_PATTERNS:
        m = re.match(pat, q)
        if not m:
            continue
        if label == "equip_env":
            return [
                _entity("Equipment", m.group(1).strip()),
                _entity("Environment", m.group(2).strip()),
            ]
        if label == "composite_env_q":
            eq, proj = m.group(1).strip(), m.group(2).strip()
            proj = re.sub(r"试验$", "", proj).strip()
            return [_entity("Equipment", eq), _entity("TestProject", proj)]
        return [_entity(label, m.group(1).strip())]
    return []


def extract_entities_from_answer(answer: str, category: str = "", question: str = "") -> List[Dict[str, Any]]:
    from_q = extract_entities_from_question(question, category) if question else []
    if from_q:
        return from_q
    a = (answer or "").strip()
    for pat, label, _slot_key in ANSWER_ENTITY_PATTERNS:
        m = re.search(pat, a)
        if not m:
            continue
        if label == "composite_env":
            return [_entity("Equipment", m.group(1).strip()), _entity("TestProject", m.group(2).strip())]
        return [_entity(label, m.group(1).strip())]
    return []


def build_gold_slots(entities: List[Dict[str, Any]]) -> Dict[str, Any]:
    slots: Dict[str, Any] = {}
    for ent in entities:
        sk = ent["slot_key"]
        slots.setdefault(sk, [])
        if ent["canonical"] and ent["canonical"] not in slots[sk]:
            slots[sk].append(ent["canonical"])
    for sk in (
        "equipment_keywords",
        "environment_keywords",
        "project_keywords",
        "indicator_keywords",
        "equipment_type_keywords",
        "method_keywords",
        "standard_keywords",
    ):
        if slots.get(sk):
            slots["focus"] = slots[sk][0]
            break
    return slots


def enrich_gold_metadata(
    *,
    question: str,
    answer: str,
    category: str,
    entities: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    base = category_base(category)
    route_id, purpose, probe_labels = CATEGORY_ROUTE.get(base, ("", "", []))
    if not route_id:
        for prefix, tpl in CATEGORY_ROUTE.items():
            if base.startswith(prefix.split("-")[0]) and prefix in category:
                route_id, purpose, probe_labels = tpl
                break
    ents = entities if entities is not None else extract_entities_from_answer(answer, category, question)
    slots = build_gold_slots(ents)
    entry = get_route(route_id) if route_id else None
    internal = entry.internal if entry else {}
    return {
        "route_id": route_id,
        "purpose": purpose or (entry.purpose if entry else ""),
        "user_biz": str(internal.get("user_biz") or ""),
        "scenario": str(internal.get("scenario") or internal.get("comp_id") or ""),
        "slots": slots,
        "probe_labels": probe_labels or list(entry.keyword_nodes if entry else []),
    }


def enrich_item(item: Dict[str, Any], idx: int = 0) -> Dict[str, Any]:
    q = str(item.get("question") or "")
    a = str(item.get("answer") or "")
    cat = str(item.get("category") or "")
    dim = item.get("dimension") or classify_dimension(cat, q)
    diff = item.get("difficulty") or classify_difficulty(cat, dim)
    ents = item.get("entities") or extract_entities_from_answer(a, cat, q)
    gold = item.get("gold") or enrich_gold_metadata(question=q, answer=a, category=cat, entities=ents)
    return {
        **item,
        "id": item.get("id") or f"qa-{idx:05d}",
        "question": q,
        "answer": a,
        "category": cat,
        "dimension": dim,
        "difficulty": diff,
        "entities": ents,
        "gold": gold,
    }


def load_benchmark_items(path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    raw = doc.get("items") or doc.get("pairs") or []
    items = [enrich_item(it, i + 1) for i, it in enumerate(raw)]
    return doc, items


def route_user_biz(route_id: str) -> str:
    entry = get_route(route_id or "")
    if not entry:
        return ""
    return str((entry.internal or {}).get("user_biz") or "")


def route_scenario(route_id: str) -> str:
    entry = get_route(route_id or "")
    if not entry:
        return ""
    internal = entry.internal or {}
    return str(internal.get("scenario") or internal.get("comp_id") or "")


__all__ = [
    "CATEGORY_ROUTE",
    "LABEL_SLOT_KEY",
    "build_gold_slots",
    "category_base",
    "classify_dimension",
    "classify_difficulty",
    "enrich_gold_metadata",
    "enrich_item",
    "extract_entities_from_answer",
    "extract_entities_from_question",
    "load_benchmark_items",
    "route_scenario",
    "route_user_biz",
]
