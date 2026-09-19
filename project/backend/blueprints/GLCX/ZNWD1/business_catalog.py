"""
ZNWD1 业务目录：business_id → 描述与默认 answer_mode。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

BIZ_PROJECT_TO_METHOD = "BIZ_PROJECT_TO_METHOD"
BIZ_METHOD_CONTENT = "BIZ_METHOD_CONTENT"
BIZ_DOCUMENT = "BIZ_DOCUMENT"
BIZ_ENUM_NODES = "BIZ_ENUM_NODES"
BIZ_REL_ONE_HOP = "BIZ_REL_ONE_HOP"
BIZ_REL_REVERSE_ONE_HOP = "BIZ_REL_REVERSE_ONE_HOP"
BIZ_EQUIP_PROJECT_BY_METHOD = "BIZ_EQUIP_PROJECT_BY_METHOD"
BIZ_REL_TWO_HOP = "BIZ_REL_TWO_HOP"
BIZ_NODE_KW = "BIZ_NODE_KW"
BIZ_NODE_PROP = "BIZ_NODE_PROP"
BIZ_NODE_PROP_REV = "BIZ_NODE_PROP_REV"

_CATALOG: Dict[str, Dict[str, Any]] = {
    BIZ_PROJECT_TO_METHOD: {
        "title": "按测试项目名称查方法",
        "answer_mode_default": "llm_synthesize",
        "user_goals": ("method", "methods", "试验方法", "测试方法"),
    },
    BIZ_METHOD_CONTENT: {
        "title": "查试验方法正文（图库 SYNR 优先）",
        "answer_mode_default": "database_direct",
        "user_goals": ("method_content", "content", "方法内容", "具体内容"),
    },
    BIZ_DOCUMENT: {
        "title": "标准/文档章节检索",
        "answer_mode_default": "llm_synthesize",
        "user_goals": ("document", "standard", "章节", "标准"),
    },
    BIZ_ENUM_NODES: {
        "title": "列举某类节点",
        "answer_mode_default": "database_direct",
        "user_goals": ("list", "enumerate", "有哪些类型"),
    },
    BIZ_REL_ONE_HOP: {
        "title": "单跳关系查询（向后：从起点查终点）",
        "answer_mode_default": "database_direct",
        "user_goals": ("relation",),
    },
    BIZ_REL_REVERSE_ONE_HOP: {
        "title": "单跳关系查询（向前：从终点反查起点）",
        "answer_mode_default": "database_direct",
        "user_goals": ("relation", "discovery", "哪些设备", "反查"),
    },
    BIZ_EQUIP_PROJECT_BY_METHOD: {
        "title": "按试验方法反查设备与测试项目（两步：项目→方法，设备→项目）",
        "answer_mode_default": "database_direct",
        "user_goals": ("relation", "discovery", "哪些设备", "哪些项目", "方法反查"),
    },
    BIZ_REL_TWO_HOP: {
        "title": "两步关系查询（如 项目→方法 再 设备→项目）",
        "answer_mode_default": "database_direct",
        "user_goals": ("relation", "two_hop", "两跳"),
    },
    BIZ_NODE_KW: {
        "title": "按名称关键词定位节点",
        "answer_mode_default": "database_direct",
        "user_goals": ("node_kw", "lookup"),
    },
    BIZ_NODE_PROP: {
        "title": "读取节点属性",
        "answer_mode_default": "llm_synthesize",
        "user_goals": ("node_prop", "property"),
    },
    BIZ_NODE_PROP_REV: {
        "title": "按属性值筛选节点",
        "answer_mode_default": "database_direct",
        "user_goals": ("node_prop_rev", "filter"),
    },
}


def get_business(business_id: str) -> Optional[Dict[str, Any]]:
    return _CATALOG.get((business_id or "").strip())


def list_business_ids() -> list:
    return list(_CATALOG.keys())


def catalog_for_routing_prompt() -> str:
    lines = []
    for bid, cfg in _CATALOG.items():
        lines.append(f"- {bid}: {cfg.get('title')}")
    return "\n".join(lines)
