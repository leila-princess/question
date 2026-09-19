"""
ZNWD4 统一路由目录：route_id ↔ purpose / keyword_nodes ↔ Playbook 或 Atomic。

由 user_biz_catalog 场景表自动生成，LLM 只选 route_id；节点类型由 probe + validator 决定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .business_catalog import BIZ_DOCUMENT, BIZ_ENUM_NODES
from .user_biz_catalog import (
    COMPOSITE_REGISTRY,
    SCENARIO_REGISTRY,
    ScenarioSpec,
    USER_BIZ_TITLES,
)

# --- purpose 枚举（Step1 LLM 输出）---
PURPOSE_CHAT = "chat"
PURPOSE_ENUM = "enum_list"
PURPOSE_DOCUMENT = "query_document"
PURPOSE_META = "meta_info"

# 场景 → purpose 映射（同一 purpose 可对应多条 route）
_SCENARIO_PURPOSE: Dict[Tuple[str, str], str] = {
    ("UBIZ_TEST_PROJECT", "by_equipment"): "query_equipment_test_projects",
    ("UBIZ_EQUIPMENT", "list"): PURPOSE_ENUM,
    ("UBIZ_TEST_PROJECT", "by_environment"): "query_environment_test_projects",
    ("UBIZ_TEST_PROJECT", "by_method"): "query_projects_by_method",
    ("UBIZ_TEST_PROJECT", "by_equip_and_env"): "query_equip_env_test_projects",
    ("UBIZ_TEST_PROJECT", "enum"): PURPOSE_ENUM,
    ("UBIZ_INDICATOR", "by_equipment"): "query_equipment_indicators",
    ("UBIZ_INDICATOR", "by_equip_type"): "query_equip_type_indicators",
    ("UBIZ_INDICATOR", "list_all"): PURPOSE_ENUM,
    ("UBIZ_TEST_METHOD", "by_project"): "query_project_methods",
    ("UBIZ_TEST_METHOD", "by_indicator"): "query_indicator_methods",
    ("UBIZ_TEST_METHOD", "by_equipment_via_project"): "query_equipment_methods_via_project",
    ("UBIZ_TEST_METHOD", "by_equipment_via_indicator"): "query_equipment_methods_via_indicator",
    ("UBIZ_TEST_METHOD", "content"): "query_method_content",
    ("UBIZ_EQUIPMENT", "under_type"): "query_equipment_under_type",
    ("UBIZ_EQUIPMENT", "by_project"): "query_equipment_by_project",
    ("UBIZ_EQUIPMENT", "by_indicator"): "query_equipment_by_indicator",
    ("UBIZ_EQUIPMENT_TYPE", "level1_list"): PURPOSE_ENUM,
    ("UBIZ_EQUIPMENT_TYPE", "level2_under_level1"): "query_level2_equipment_types",
    ("UBIZ_EQUIPMENT_TYPE", "belongs_to"): "query_equipment_belongs_to_type",
    ("UBIZ_ENVIRONMENT", "list"): PURPOSE_ENUM,
    ("UBIZ_ENVIRONMENT", "properties"): "query_environment_properties",
    ("UBIZ_ENVIRONMENT", "for_project"): "query_project_environment",
    ("UBIZ_STANDARD", "for_projects"): "query_project_standards",
    ("UBIZ_STANDARD", "methods_in_standard"): "query_standard_methods",
    ("UBIZ_STANDARD", "chapter_content"): PURPOSE_DOCUMENT,
    ("UBIZ_STANDARD", "condition_param"): "query_standard_condition",
    ("UBIZ_KNOWLEDGE", "keyword_doc"): PURPOSE_DOCUMENT,
}

_COMPOSITE_PURPOSE: Dict[str, str] = {
    "COMP_PROJECT_THEN_METHOD": "composite_project_then_method",
    "COMP_PROJECT_ENV_METHOD": "composite_project_env_method",
    "COMP_INDICATOR_THEN_METHOD": "composite_indicator_then_method",
    "COMP_EQUIP_PROJECT_ENV_SETTINGS": "composite_equip_project_env_settings",
    "COMP_INTENT_PLAN": "composite_intent_plan",
}

_SCENARIO_KEYWORD_NODES: Dict[Tuple[str, str], List[str]] = {
    ("UBIZ_TEST_PROJECT", "by_equipment"): ["Equipment"],
    ("UBIZ_EQUIPMENT", "list"): [],
    ("UBIZ_TEST_PROJECT", "by_environment"): ["Environment"],
    ("UBIZ_TEST_PROJECT", "by_method"): ["TestMethod"],
    ("UBIZ_TEST_PROJECT", "by_equip_and_env"): ["Equipment", "Environment"],
    ("UBIZ_TEST_PROJECT", "enum"): [],
    ("UBIZ_INDICATOR", "by_equipment"): ["Equipment"],
    ("UBIZ_INDICATOR", "by_equip_type"): ["EquipmentType"],
    ("UBIZ_INDICATOR", "list_all"): [],
    ("UBIZ_TEST_METHOD", "by_project"): ["TestProject"],
    ("UBIZ_TEST_METHOD", "by_indicator"): ["Indicator"],
    ("UBIZ_TEST_METHOD", "by_equipment_via_project"): ["Equipment"],
    ("UBIZ_TEST_METHOD", "by_equipment_via_indicator"): ["Equipment"],
    ("UBIZ_TEST_METHOD", "content"): ["TestMethod", "TestProject"],
    ("UBIZ_EQUIPMENT", "under_type"): ["EquipmentType"],
    ("UBIZ_EQUIPMENT", "by_project"): ["TestProject"],
    ("UBIZ_EQUIPMENT", "by_indicator"): ["Indicator"],
    ("UBIZ_EQUIPMENT_TYPE", "level1_list"): [],
    ("UBIZ_EQUIPMENT_TYPE", "level2_under_level1"): ["EquipmentType"],
    ("UBIZ_EQUIPMENT_TYPE", "belongs_to"): ["Equipment"],
    ("UBIZ_ENVIRONMENT", "list"): [],
    ("UBIZ_ENVIRONMENT", "properties"): ["Environment"],
    ("UBIZ_ENVIRONMENT", "for_project"): ["TestProject"],
    ("UBIZ_STANDARD", "for_projects"): ["TestProject"],
    ("UBIZ_STANDARD", "methods_in_standard"): ["TestMethod", "File"],
    ("UBIZ_STANDARD", "chapter_content"): [],
    ("UBIZ_STANDARD", "condition_param"): ["File"],
    ("UBIZ_KNOWLEDGE", "keyword_doc"): [],
}

_COMPOSITE_KEYWORD_NODES: Dict[str, List[str]] = {
    "COMP_PROJECT_THEN_METHOD": ["Equipment"],
    "COMP_PROJECT_ENV_METHOD": ["Equipment", "Environment"],
    "COMP_INDICATOR_THEN_METHOD": ["Equipment"],
    "COMP_EQUIP_PROJECT_ENV_SETTINGS": ["Equipment", "Environment", "TestProject"],
    "COMP_INTENT_PLAN": ["Equipment"],
}


@dataclass
class RouteEntry:
    route_id: str
    purpose: str
    purpose_zh: str
    title: str
    keyword_nodes: List[str]
    required_slots: List[str]
    answer_strategy: str
    internal: Dict[str, Any] = field(default_factory=dict)


def _route_id_playbook(user_biz: str, scenario: str) -> str:
    ub = user_biz.replace("UBIZ_", "")
    return f"R-{ub}-{scenario}"


def _route_id_composite(comp_id: str) -> str:
    return f"R-{comp_id.replace('COMP_', 'C-')}-default"


def _purpose_zh(purpose: str) -> str:
    _ZH = {
        PURPOSE_CHAT: "闲聊/问候",
        PURPOSE_ENUM: "全库列举",
        PURPOSE_DOCUMENT: "标准/知识文档检索",
        PURPOSE_META: "元信息",
        "query_equipment_test_projects": "查某装备有哪些试验项目",
        "query_environment_test_projects": "查某环境有哪些试验项目",
        "query_projects_by_method": "查某试验方法涉及哪些项目",
        "query_equip_env_test_projects": "查装备在某环境下的试验项目",
        "query_equipment_indicators": "查某装备考核指标",
        "query_equip_type_indicators": "查某类装备考核指标",
        "query_project_methods": "查某项目有哪些试验方法",
        "query_indicator_methods": "查某指标关联的方法",
        "query_equipment_methods_via_project": "查某装备经项目对应的方法",
        "query_equipment_methods_via_indicator": "查某装备经指标对应的方法",
        "query_method_content": "查试验方法正文/步骤",
        "query_equipment_under_type": "查某装备类型下有哪些具体装备",
        "query_equipment_by_project": "查某项目涉及哪些装备",
        "query_equipment_by_indicator": "查某指标涉及哪些装备",
        "query_level2_equipment_types": "查一级类别下二级装备类型",
        "query_equipment_belongs_to_type": "查某装备属于哪类装备类型",
        "query_environment_properties": "查环境分类/分级/组合/约束",
        "query_project_environment": "查某项目在什么环境试验",
        "query_project_standards": "查项目相关标准文件",
        "query_standard_methods": "查标准包含的试验方法",
        "composite_project_then_method": "先查项目再查方法",
        "composite_project_env_method": "装备+环境下项目及方法",
        "composite_indicator_then_method": "先查指标再查方法",
        "composite_equip_project_env_settings": "装备项目环境设置",
        "composite_intent_plan": "多意图并列检索",
    }
    return _ZH.get(purpose, purpose)


def _entry_from_scenario(user_biz: str, scenario: str, spec: ScenarioSpec) -> RouteEntry:
    purpose = _SCENARIO_PURPOSE.get((user_biz, scenario), "general_query")
    return RouteEntry(
        route_id=_route_id_playbook(user_biz, scenario),
        purpose=purpose,
        purpose_zh=_purpose_zh(purpose),
        title=spec.title,
        keyword_nodes=list(_SCENARIO_KEYWORD_NODES.get((user_biz, scenario), [])),
        required_slots=list(spec.required_slots or []),
        answer_strategy=spec.answer_strategy,
        internal={"kind": "playbook", "user_biz": user_biz, "scenario": scenario},
    )


def _entry_from_composite(comp_id: str, spec: ScenarioSpec) -> RouteEntry:
    purpose = _COMPOSITE_PURPOSE.get(comp_id, "composite_query")
    return RouteEntry(
        route_id=_route_id_composite(comp_id),
        purpose=purpose,
        purpose_zh=_purpose_zh(purpose),
        title=spec.title,
        keyword_nodes=list(_COMPOSITE_KEYWORD_NODES.get(comp_id, [])),
        required_slots=list(spec.required_slots or []),
        answer_strategy=spec.answer_strategy,
        internal={"kind": "composite", "user_biz": comp_id, "scenario": "default"},
    )


def build_route_catalog() -> Dict[str, RouteEntry]:
    catalog: Dict[str, RouteEntry] = {}
    for (ub, sc), spec in SCENARIO_REGISTRY.items():
        e = _entry_from_scenario(ub, sc, spec)
        catalog[e.route_id] = e
    for cid, spec in COMPOSITE_REGISTRY.items():
        e = _entry_from_composite(cid, spec)
        catalog[e.route_id] = e
    catalog["R-CHAT-direct"] = RouteEntry(
        route_id="R-CHAT-direct",
        purpose=PURPOSE_CHAT,
        purpose_zh=_purpose_zh(PURPOSE_CHAT),
        title="闲聊/问候",
        keyword_nodes=[],
        required_slots=[],
        answer_strategy="direct_llm",
        internal={"kind": "direct_llm"},
    )
    catalog["R-META-stats"] = RouteEntry(
        route_id="R-META-stats",
        purpose=PURPOSE_META,
        purpose_zh=_purpose_zh(PURPOSE_META),
        title="图库/向量库统计",
        keyword_nodes=[],
        required_slots=[],
        answer_strategy="db_direct",
        internal={"kind": "meta_info"},
    )
    return catalog


ROUTE_CATALOG: Dict[str, RouteEntry] = build_route_catalog()


def get_route(route_id: str) -> Optional[RouteEntry]:
    return ROUTE_CATALOG.get((route_id or "").strip())


def list_routes() -> List[RouteEntry]:
    return list(ROUTE_CATALOG.values())


def filter_routes_by_purpose(purpose: str) -> List[RouteEntry]:
    p = (purpose or "").strip()
    if not p:
        return []
    return [r for r in ROUTE_CATALOG.values() if r.purpose == p]


def routes_prompt_markdown(routes: List[RouteEntry]) -> str:
    lines = [
        "| route_id | 目的 | 关键词节点 | 说明 | 槽位 |",
        "|----------|------|------------|------|------|",
    ]
    for r in routes:
        nodes = "、".join(r.keyword_nodes) if r.keyword_nodes else "（列举/文档，可不 probe）"
        slots = "、".join(r.required_slots) if r.required_slots else "—"
        lines.append(f"| {r.route_id} | {r.purpose_zh} | {nodes} | {r.title} | {slots} |")
    return "\n".join(lines)


def resolve_route(route_id: str) -> Optional[Dict[str, Any]]:
    """映射到 legacy plan 字段（user_biz/scenario 或 direct_llm）。"""
    entry = get_route(route_id)
    if not entry:
        return None
    internal = dict(entry.internal or {})
    out: Dict[str, Any] = {
        "route_id": entry.route_id,
        "purpose": entry.purpose,
        "answer_strategy": entry.answer_strategy,
        "keyword_nodes": entry.keyword_nodes,
        "required_slots": entry.required_slots,
    }
    if internal.get("kind") == "direct_llm":
        out["direct_llm"] = True
        return out
    out["user_biz"] = internal.get("user_biz", "")
    out["scenario"] = internal.get("scenario", "default")
    out["match_confidence"] = "high"
    return out


_GREETING = re.compile(
    r"^(你好|您好|嗨|hello|hi|在吗|谢谢|感谢|再见|拜拜|你是谁|你能做什么)\s*[!！。.?？]*$",
    re.I,
)


def guess_purpose_from_question(question: str) -> str:
    q = (question or "").strip()
    if _GREETING.match(q):
        return PURPOSE_CHAT
    if re.search(r"(图数据库|neo4j|图库|向量库|chroma|文档片段).{0,16}(多少|统计|有多少)", q, re.I):
        return PURPOSE_META
    if re.search(r"(有哪些|都有什么|列出|列举|清单|登记).{0,8}(装备|设备)", q) and not re.search(
        r"(试验项目|测试项目|指标|方法|环境|类型|二级|具体)", q
    ):
        return PURPOSE_ENUM
    if re.search(r"(有哪些|列举|清单|全部).{0,8}(试验项目|测试项目)", q) and not re.search(
        r"(装备|设备|环境).{0,12}(有|的)", q
    ):
        return PURPOSE_ENUM if "项目" in q else "query_equipment_test_projects"
    if re.search(r"(属于|归为).{0,12}(什么|哪).{0,8}装备类型", q):
        return "query_equipment_belongs_to_type"
    if re.search(r"二级", q) and not re.search(r"具体\s*(?:设备|装备)", q):
        if re.search(r"二级.{0,8}(?:装备|设备)?(?:类别|类型)", q) or re.search(
            r"(?:有什么|有哪些).{0,12}二级", q
        ):
            return "query_level2_equipment_types"
    if re.search(r"具体\s*(?:设备|装备)", q) and re.search(r"(?:有什么|有哪些|包括哪些)", q):
        if not re.search(r"(?:设备类型|装备类型|设备类别)", q):
            return "query_equipment_under_type"
    if re.search(r"(有哪些|有什么).{0,12}(?:的)?\s*(?:设备|装备)", q) and not re.search(
        r"(试验项目|测试项目|指标|方法)", q
    ):
        if not re.search(r"(?:设备类型|装备类型|设备类别|二级)", q):
            if re.search(r"(有哪些|都有什么|列出|列举|清单|登记|全部).{0,8}(装备|设备)", q):
                return PURPOSE_ENUM
            return "query_equipment_under_type"
    if re.search(r"(有哪些|有什么).{0,12}(试验项目|测试项目)", q) and re.search(r"(装备|设备|坦克|无人机)", q):
        return "query_equipment_test_projects"
    if re.search(r"(考核|性能|试验)?指标", q) and re.search(r"(装备|设备|坦克|无人机)", q):
        return "query_equipment_indicators"
    if re.search(r"(试验|测试)?项目", q) and re.search(
        r"(有哪些|有什么|包括哪些|都有哪些|分别).{0,12}(?:试验|测试)?\s*方法", q
    ):
        return "query_project_methods"
    if re.search(r"(试验|测试)?项目", q) and re.search(
        r"(相关标准|依据标准|标准文件|适用标准|执行标准)", q
    ):
        return "query_project_standards"
    if re.search(r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?(?:试验|测试)(?!项目).{0,24}"
        r"(?:有哪些|有什么|包括哪些|都有哪些|分别).{0,12}(?:试验|测试)?\s*方法", q):
        return "query_project_methods"
    if re.search(r"(方法正文|怎么做|步骤|SYTJ|SYNR)", q, re.I):
        return "query_method_content"
    if re.search(r"(GB/?T|标准|章节|条文)", q, re.I):
        return PURPOSE_DOCUMENT
    if re.search(r"(有哪些|有什么).{0,8}方法", q):
        return "query_project_methods"
    return "general_query"


def list_purpose_options_markdown() -> str:
    purposes = sorted({r.purpose for r in ROUTE_CATALOG.values()})
    lines = ["| purpose | 中文 |", "|---------|------|"]
    for p in purposes:
        lines.append(f"| {p} | {_purpose_zh(p)} |")
    return "\n".join(lines)
