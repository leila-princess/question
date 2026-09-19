"""
ZNWD3 图谱路径查找器（1-2 跳基础版）。
当业务路由失败时，自动搜索图谱中实体间的最短路径，生成可执行的 meta_ops steps。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .meta_ops import NODE_LABELS, RELATION_SCHEMA

logger = logging.getLogger(__name__)

# 预定义的路径模板
# (from_type, to_type) -> path_steps
_PATH_TEMPLATES = {
    # 1 跳路径
    ("Equipment", "TestProject"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "Equipment", "to_type": "TestProject", "rel_type": "CON",
            "where_side": "from", "match_mode": "fuzzy", "from_field": "Name",
        }}
    ],
    ("Equipment", "Indicator"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "Equipment", "to_type": "Indicator", "rel_type": "CON",
            "where_side": "from", "match_mode": "fuzzy", "from_field": "Name",
        }}
    ],
    ("Environment", "TestProject"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "Environment", "to_type": "TestProject", "rel_type": "CON",
            "where_side": "from", "match_mode": "fuzzy", "from_field": "Name",
        }}
    ],
    ("TestProject", "TestMethod"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "TestProject", "to_type": "TestMethod", "rel_type": "CON",
            "where_side": "from", "match_mode": "fuzzy", "from_field": "Name",
        }}
    ],
    ("Indicator", "TestMethod"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "Indicator", "to_type": "TestMethod", "rel_type": "CON",
            "where_side": "from", "match_mode": "fuzzy", "from_field": "Name",
        }}
    ],
    ("EquipmentType", "Equipment"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "EquipmentType", "to_type": "Equipment", "rel_type": "HAS",
            "where_side": "from", "match_mode": "fuzzy", "from_field": "Name",
        }}
    ],
    ("TestMethod", "File"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "TestMethod", "to_type": "File", "rel_type": "BELONGS_TO",
            "where_side": "from", "match_mode": "fuzzy", "from_field": "Name",
        }}
    ],
    # 反向 1 跳
    ("TestProject", "Equipment"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "Equipment", "to_type": "TestProject", "rel_type": "CON",
            "where_side": "to", "match_mode": "fuzzy", "from_field": "Name",
            "result_type": "Equipment", "anchor_type": "TestProject",
        }}
    ],
    ("TestProject", "Environment"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "Environment", "to_type": "TestProject", "rel_type": "CON",
            "where_side": "to", "match_mode": "fuzzy", "from_field": "Name",
            "result_type": "Environment", "anchor_type": "TestProject",
        }}
    ],
    # 2 跳路径
    ("Equipment", "TestMethod"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "Equipment", "to_type": "TestProject", "rel_type": "CON",
            "where_side": "from", "match_mode": "fuzzy", "from_field": "Name",
        }},
        {"op_code": "neo_relation_query", "params": {
            "from_type": "TestProject", "to_type": "TestMethod", "rel_type": "CON",
            "where_side": "from", "match_mode": "in", "from_field": "Id",
            "from_values_ref": {"step_id": "s1", "target": "m", "field": "Id"},
            "limit": 200,
        }}
    ],
    ("EquipmentType", "Indicator"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "EquipmentType", "to_type": "Equipment", "rel_type": "HAS",
            "where_side": "from", "match_mode": "fuzzy", "from_field": "Name",
        }},
        {"op_code": "neo_relation_query", "params": {
            "from_type": "Equipment", "to_type": "Indicator", "rel_type": "CON",
            "where_side": "from", "match_mode": "in", "from_field": "Id",
            "from_values_ref": {"step_id": "s1", "target": "m", "field": "Id"},
            "limit": 200,
        }}
    ],
    ("TestProject", "File"): [
        {"op_code": "neo_relation_query", "params": {
            "from_type": "TestProject", "to_type": "TestMethod", "rel_type": "CON",
            "where_side": "from", "match_mode": "fuzzy", "from_field": "Name",
        }},
        {"op_code": "neo_relation_query", "params": {
            "from_type": "TestMethod", "to_type": "File", "rel_type": "BELONGS_TO",
            "where_side": "from", "match_mode": "in", "from_field": "Id",
            "from_values_ref": {"step_id": "s1", "target": "m", "field": "Id"},
            "limit": 200,
        }}
    ],
}


def find_path(
    from_type: str,
    to_type: str,
    *,
    focus: str = "",
    focus_id: str = "",
) -> Optional[List[Dict[str, Any]]]:
    """
    查找两个节点类型之间的路径，返回可执行的 meta_ops steps。
    
    Args:
        from_type: 起点节点类型
        to_type: 终点节点类型
        focus: 起点关键词
        focus_id: 起点节点 Id（优先使用）
    
    Returns:
        meta_ops steps 列表，如果找不到路径则返回 None
    """
    key = (from_type, to_type)
    template = _PATH_TEMPLATES.get(key)
    
    if not template:
        logger.info("[ZNWD3][path] no path found: %s -> %s", from_type, to_type)
        return None
    
    # 深拷贝模板并填充参数
    steps = []
    for i, step_template in enumerate(template):
        step = {
            "step_id": f"s{i+1}",
            "op_code": step_template["op_code"],
            "params": dict(step_template["params"]),
            "purpose": f"{from_type} -> {to_type} 路径第{i+1}跳",
        }
        
        # 填充 focus
        if focus and i == 0:
            params = step["params"]
            if params.get("match_mode") == "fuzzy":
                params["value"] = focus
            elif params.get("match_mode") == "fuzzy_any":
                params["value_list"] = [focus]
        
        # 填充 focus_id
        if focus_id and i == 0:
            step["params"]["focus_id"] = focus_id
            step["params"]["match_mode"] = "exact"
            step["params"]["value"] = ""
        
        steps.append(step)
    
    logger.info(
        "[ZNWD3][path] found path: %s -> %s, steps=%d",
        from_type, to_type, len(steps),
    )
    return steps


def find_path_from_probe(
    probe_results: Dict[str, Dict[str, Any]],
    *,
    target_type: str = "",
    intent: str = "",
) -> Optional[List[Dict[str, Any]]]:
    """
    从 probe 结果自动查找路径。
    
    Args:
        probe_results: probe 验证结果
        target_type: 目标节点类型（如知道要找什么）
        intent: 意图类型
    
    Returns:
        meta_ops steps 列表
    """
    if not probe_results:
        return None
    
    # 获取第一个 probe 命中的实体作为起点
    first_kw = None
    first_res = None
    for kw, res in probe_results.items():
        if res.get("node_type"):
            first_kw = kw
            first_res = res
            break
    
    if not first_res:
        return None
    
    from_type = first_res.get("node_type", "")
    from_id = first_res.get("id", "")
    from_name = first_res.get("name", first_kw or "")
    
    # 推断目标类型
    if target_type:
        to_type = target_type
    elif intent == "project":
        to_type = "TestProject"
    elif intent == "indicator":
        to_type = "Indicator"
    elif intent == "method" or intent == "content":
        to_type = "TestMethod"
    elif intent == "environment":
        to_type = "Environment"
    elif intent == "document":
        to_type = "File"
    else:
        # 默认：如果是装备，找项目
        if from_type == "Equipment":
            to_type = "TestProject"
        elif from_type == "Environment":
            to_type = "TestProject"
        elif from_type == "TestProject":
            to_type = "TestMethod"
        else:
            return None
    
    # 查找路径
    steps = find_path(from_type, to_type, focus=from_name, focus_id=from_id)
    
    if steps:
        steps[0]["purpose"] = f"{from_name}({from_type}) -> {to_type}"
    
    return steps
