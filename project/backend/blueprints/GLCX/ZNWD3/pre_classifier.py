"""
ZNWD3 预分类器（方案 B）。
基于槽位组合和 probe 结果生成 2-3 个候选业务。
纯程序化，不依赖 LLM。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .slot_extractor import has_slot, extract_slot_value

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    user_biz: str
    scenario: str
    confidence: float
    title: str


# 预分类规则表
# (has_equipment, has_environment, has_project, has_indicator, has_standard, has_equipment_type) -> Candidates
_CLASSIFICATION_RULES = [
    # 装备 + 项目 → 试验项目
    (True, False, True, False, False, False, [
        Candidate("UBIZ_TEST_PROJECT", "by_equipment", 0.90, "装备的试验项目"),
    ]),
    # 装备 + 指标 → 考核指标
    (True, False, False, True, False, False, [
        Candidate("UBIZ_INDICATOR", "by_equipment", 0.90, "装备的考核指标"),
    ]),
    # 装备 + 环境 → 装备+环境下的项目
    (True, True, False, False, False, False, [
        Candidate("UBIZ_TEST_PROJECT", "by_equip_and_env", 0.85, "装备在环境下的试验项目"),
    ]),
    # 装备 + 环境 + 项目 → 装备+环境下的项目
    (True, True, True, False, False, False, [
        Candidate("UBIZ_TEST_PROJECT", "by_equip_and_env", 0.90, "装备在环境下的试验项目"),
    ]),
    # 仅装备 → 默认查项目
    (True, False, False, False, False, False, [
        Candidate("UBIZ_TEST_PROJECT", "by_equipment", 0.75, "装备的试验项目"),
        Candidate("UBIZ_INDICATOR", "by_equipment", 0.60, "装备的考核指标"),
    ]),
    # 仅环境 → 环境下的项目
    (False, True, False, False, False, False, [
        Candidate("UBIZ_TEST_PROJECT", "by_environment", 0.80, "环境下的试验项目"),
    ]),
    # 仅项目 → 项目的方法
    (False, False, True, False, False, False, [
        Candidate("UBIZ_TEST_METHOD", "by_project", 0.80, "项目的试验方法"),
    ]),
    # 仅指标 → 列举指标
    (False, False, False, True, False, False, [
        Candidate("UBIZ_INDICATOR", "list_all", 0.75, "考核指标列举"),
    ]),
    # 装备类型 → 装备类别
    (False, False, False, False, False, True, [
        Candidate("UBIZ_EQUIPMENT_TYPE", "level2_under_level1", 0.80, "装备类型下的具体装备"),
    ]),
    # 标准 → 标准检索
    (False, False, False, False, True, False, [
        Candidate("UBIZ_STANDARD", "chapter_content", 0.80, "标准条文检索"),
    ]),
]


def pre_classify(
    slots: Dict[str, List[str]],
    probe_results: Optional[Dict[str, Dict[str, Any]]] = None,
    intent: str = "",
) -> List[Candidate]:
    """
    基于槽位组合生成候选业务列表。
    
    Args:
        slots: 槽位字典
        probe_results: probe 验证结果（可选，用于提升置信度）
        intent: 意图类型（project/indicator/method/environment/equipment/enum/document/content）
    
    Returns:
        候选业务列表，按置信度降序
    """
    has_equip = has_slot(slots, "equipment")
    has_env = has_slot(slots, "environment")
    has_proj = has_slot(slots, "project")
    has_ind = has_slot(slots, "indicator")
    has_std = has_slot(slots, "standard")
    has_et = has_slot(slots, "equipment_type")

    # 根据 probe 结果提升置信度
    probe_boost = 0.0
    if probe_results:
        for kw, res in probe_results.items():
            if res.get("node_type") in ("Equipment", "Environment", "TestProject"):
                probe_boost = 0.05
                break

    # 匹配规则
    for rule in _CLASSIFICATION_RULES:
        r_equip, r_env, r_proj, r_ind, r_std, r_et, candidates = rule
        if (r_equip == has_equip and r_env == has_env and r_proj == has_proj and
                r_ind == has_ind and r_std == has_std and r_et == has_et):
            # 应用 probe 提升
            return [
                Candidate(c.user_biz, c.scenario, min(c.confidence + probe_boost, 0.95), c.title)
                for c in candidates
            ]

    # 基于意图兜底
    if intent == "indicator":
        if has_equip:
            return [Candidate("UBIZ_INDICATOR", "by_equipment", 0.70 + probe_boost, "装备的考核指标")]
        return [Candidate("UBIZ_INDICATOR", "list_all", 0.65, "考核指标列举")]
    if intent == "method":
        if has_proj:
            return [Candidate("UBIZ_TEST_METHOD", "by_project", 0.70 + probe_boost, "项目的试验方法")]
        if has_equip:
            return [Candidate("UBIZ_TEST_METHOD", "by_equipment_via_project", 0.60, "装备经项目的方法")]
        return [Candidate("UBIZ_TEST_METHOD", "by_project", 0.50, "试验方法")]
    if intent == "document":
        return [Candidate("UBIZ_STANDARD", "chapter_content", 0.70, "标准条文检索")]
    if intent == "content":
        return [Candidate("UBIZ_TEST_METHOD", "content", 0.70, "方法正文")]
    if intent == "environment":
        if has_proj:
            return [Candidate("UBIZ_ENVIRONMENT", "for_project", 0.70, "项目的环境条件")]
        return [Candidate("UBIZ_ENVIRONMENT", "list", 0.60, "试验环境列举")]
    if intent == "equipment":
        if has_et:
            return [Candidate("UBIZ_EQUIPMENT", "under_type", 0.70, "装备类型下的具体装备")]
        return [Candidate("UBIZ_EQUIPMENT", "under_type", 0.50, "装备")]

    # 完全无法判断
    return []


def get_top_candidates(
    slots: Dict[str, List[str]],
    probe_results: Optional[Dict[str, Dict[str, Any]]] = None,
    intent: str = "",
    max_count: int = 3,
) -> List[Candidate]:
    """获取前 N 个候选，用于 LLM 确认。"""
    candidates = pre_classify(slots, probe_results, intent)
    # 去重（同 user_biz+scenario 只保留最高置信度）
    seen = set()
    unique = []
    for c in candidates:
        key = (c.user_biz, c.scenario)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique[:max_count]
