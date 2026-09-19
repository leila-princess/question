"""
ZNWD3 RoutePlan 组装器。
将语义提取或确认器的结果组装为统一的 RoutePlan JSON。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .slot_extractor import extract_slot_value, has_slot

logger = logging.getLogger(__name__)

# 意图到 user_biz/scenario 的映射
_INTENT_MAP = {
    "project": ("UBIZ_TEST_PROJECT", "enum"),
    "indicator": ("UBIZ_INDICATOR", "list_all"),
    "method": ("UBIZ_TEST_METHOD", "by_project"),
    "environment": ("UBIZ_ENVIRONMENT", "list"),
    "equipment": ("UBIZ_EQUIPMENT", "under_type"),
    "document": ("UBIZ_STANDARD", "chapter_content"),
    "content": ("UBIZ_TEST_METHOD", "content"),
    "enum": ("", ""),
    "greeting": ("", ""),
}

# 有装备时的场景映射
_EQUIP_SCENARIO_MAP = {
    "project": ("UBIZ_TEST_PROJECT", "by_equipment"),
    "indicator": ("UBIZ_INDICATOR", "by_equipment"),
    "method": ("UBIZ_TEST_METHOD", "by_equipment_via_project"),
}

# 有环境时的场景映射
_ENV_SCENARIO_MAP = {
    "project": ("UBIZ_TEST_PROJECT", "by_environment"),
}


def assemble_route_plan(
    mode: str,
    question: str,
    slots: Dict[str, List[str]],
    probe_results: Dict[str, Dict[str, Any]],
    *,
    extraction_result: Optional[Dict[str, Any]] = None,
    confirm_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    组装 RoutePlan JSON。
    
    Args:
        mode: "extract" 或 "confirm"
        question: 原始问题
        slots: 槽位字典
        probe_results: probe 验证结果
        extraction_result: 方案 A 的提取结果
        confirm_result: 方案 B 的确认结果
    """
    q = (question or "").strip()
    intent = ""
    user_biz = ""
    scenario = ""
    match_confidence = "medium"

    if mode == "extract" and extraction_result:
        intent = extraction_result.get("intent", "enum")
        # 根据意图和槽位推断 user_biz/scenario
        if has_slot(slots, "equipment"):
            biz_scenario = _EQUIP_SCENARIO_MAP.get(intent)
            if biz_scenario:
                user_biz, scenario = biz_scenario
                match_confidence = "high" if probe_results else "medium"
        elif has_slot(slots, "environment"):
            biz_scenario = _ENV_SCENARIO_MAP.get(intent)
            if biz_scenario:
                user_biz, scenario = biz_scenario
                match_confidence = "high" if probe_results else "medium"
        
        if not user_biz:
            biz_scenario = _INTENT_MAP.get(intent, ("", ""))
            user_biz, scenario = biz_scenario
            match_confidence = "medium" if user_biz else "low"

    elif mode == "confirm" and confirm_result:
        selected = confirm_result.get("selected", {})
        user_biz = selected.get("user_biz", "")
        scenario = selected.get("scenario", "")
        confidence = confirm_result.get("confidence", "low")
        if confidence == "high":
            match_confidence = "high"
        elif confidence == "medium":
            match_confidence = "medium"
        else:
            match_confidence = "low"
        
        if not user_biz:
            match_confidence = "low"

    # 构建 params_draft
    params_draft = {
        "equipment_keywords": slots.get("equipment", []),
        "equipment_type_keywords": slots.get("equipment_type", []),
        "environment_keywords": slots.get("environment", []),
        "project_keywords": slots.get("project", []),
        "indicator_keywords": slots.get("indicator", []),
        "standard_keywords": slots.get("standard", []),
        "focus": extract_slot_value(slots, "equipment") or extract_slot_value(slots, "environment") or extract_slot_value(slots, "project") or q[:50],
    }

    # 构建 probe_snapshot
    probe_snapshot = {}
    if probe_results:
        best_kw = None
        best_res = None
        for kw, res in probe_results.items():
            if res.get("node_type"):
                best_kw = kw
                best_res = res
                break
        if best_res:
            probe_snapshot = {
                "best": {
                    "node_type": best_res.get("node_type", ""),
                    "id": best_res.get("id", ""),
                    "name": best_res.get("name", best_kw or ""),
                }
            }

    # 确定 answer_strategy
    if intent == "greeting":
        answer_strategy = "direct_llm"
    elif match_confidence in ("high", "medium") and user_biz:
        answer_strategy = "db_synthesize"
    else:
        answer_strategy = "db_synthesize"

    plan = {
        "user_biz": user_biz,
        "scenario": scenario,
        "match_confidence": match_confidence,
        "answer_strategy": answer_strategy,
        "use_history": False,
        "retrieval_question": q,
        "keyword": params_draft["focus"],
        "params_draft": params_draft,
        "probe_snapshot": probe_snapshot,
        "intent": intent,
        "mode": mode,
    }

    # 如果置信度低，设置 fallback 字段
    if match_confidence == "low" or not user_biz:
        plan["purpose_node"] = _infer_purpose_node(intent, slots, probe_results)
        plan["biz_type"] = _infer_biz_type(intent, slots)

    logger.info(
        "[ZNWD3][assemble] mode=%s user_biz=%s scenario=%s confidence=%s",
        mode, user_biz, scenario, match_confidence,
    )
    return plan


def _infer_purpose_node(
    intent: str,
    slots: Dict[str, List[str]],
    probe_results: Dict[str, Dict[str, Any]],
) -> str:
    """推断 fallback 的目的节点类型。"""
    # 从 probe 结果中找最相关的节点类型
    for kw, res in probe_results.items():
        nt = res.get("node_type", "")
        if nt:
            return nt
    
    # 基于意图推断
    if intent == "project":
        return "TestProject"
    if intent == "indicator":
        return "Indicator"
    if intent == "method" or intent == "content":
        return "TestMethod"
    if intent == "environment":
        return "Environment"
    if intent == "equipment":
        return "Equipment"
    if intent == "document":
        return ""
    return "TestProject"


def _infer_biz_type(intent: str, slots: Dict[str, List[str]]) -> str:
    """推断 fallback 的动作范式。"""
    if has_slot(slots, "equipment") or has_slot(slots, "environment"):
        return "hop_n"
    if intent in ("enum", "project", "indicator", "environment", "equipment"):
        return "enum"
    if intent == "content":
        return "node_prop"
    if intent == "document":
        return "document"
    return "hop_n"
