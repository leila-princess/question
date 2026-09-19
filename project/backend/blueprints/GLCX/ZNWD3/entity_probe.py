"""
ZNWD3 实体探测：程序直接调用 Neo4j probe，不经过 LLM 工具轮次。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .keyword_probe import compress_probe_for_tool, probe_keyword

logger = logging.getLogger(__name__)


def probe_entities(
    driver: Any,
    keywords: List[str],
    *,
    label_hints: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    对每个关键词执行 probe，返回验证后的实体字典。
    
    返回: {keyword: {"node_type": "...", "id": "...", "name": "...", "hits": [...]}}
    """
    results: Dict[str, Dict[str, Any]] = {}
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        try:
            res = probe_keyword(driver, kw, label_hints=label_hints or [])
            best = res.get("best")
            if isinstance(best, dict) and best.get("node_type"):
                results[kw] = {
                    "node_type": best.get("node_type", ""),
                    "id": best.get("id", ""),
                    "name": best.get("name", kw),
                    "hits": res.get("hits", [])[:5],
                }
                logger.info("[ZNWD3][probe] keyword=%r -> %s(%s)", kw, best.get("node_type"), best.get("name"))
            else:
                logger.info("[ZNWD3][probe] keyword=%r -> 无命中", kw)
        except Exception as e:
            logger.warning("[ZNWD3][probe] keyword=%r failed: %s", kw, e)
    return results


def get_probe_node_types(probe_results: Dict[str, Dict[str, Any]]) -> List[str]:
    """从 probe 结果中提取所有命中的节点类型。"""
    types: List[str] = []
    seen: set = set()
    for kw, res in probe_results.items():
        nt = res.get("node_type", "")
        if nt and nt not in seen:
            seen.add(nt)
            types.append(nt)
    return types


def get_probe_best_by_type(probe_results: Dict[str, Dict[str, Any]], node_type: str) -> Optional[Dict[str, Any]]:
    """按节点类型获取最佳 probe 结果。"""
    for kw, res in probe_results.items():
        if res.get("node_type") == node_type:
            return res
    return None
