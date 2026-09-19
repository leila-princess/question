"""本体模式图与业务代价模型。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import networkx as nx

from .schema import RELATION_SCHEMA
from .types import IntentJson

_SCHEMA_GRAPH: Optional[nx.DiGraph] = None


def _build_base_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    for rel, pairs in RELATION_SCHEMA.items():
        for src, dst in pairs:
            g.add_edge(src, dst, rel=rel, cost=1.0)
    return g


def get_schema_graph() -> nx.DiGraph:
    """返回本体有向图（节点=实体类型，边=关系）。"""
    global _SCHEMA_GRAPH
    if _SCHEMA_GRAPH is None:
        _SCHEMA_GRAPH = _build_base_graph()
    return _SCHEMA_GRAPH.copy()


def adjust_edge_cost(
    u: str,
    v: str,
    data: Dict[str, Any],
    intent_json: IntentJson,
) -> float:
    """
    根据意图动态调整边代价。

    - 查指标：Equipment→Indicator 奖励（×0.5）；Equipment→TestProject 惩罚（×5，避免误走项目）。
    - 查项目：Equipment→TestProject、Environment→TestProject 奖励（×0.5）。
    - 查方法：指向 TestMethod 的边 ×0.5；经指标路径 Indicator→TestMethod 在 via_indicator 时 ×0.5。
    - 查文件：指向 File 的边 ×0.5。
    """
    base = float(data.get("cost", 1.0))
    intent_type = str(intent_json.get("intent_type") or "")
    additional = intent_json.get("additional_filters") or {}
    route = str(additional.get("route") or "")

    if intent_type == "查指标":
        if u == "Equipment" and v == "Indicator":
            base *= 0.5
        if u == "Equipment" and v == "TestProject":
            base *= 5.0

    if intent_type == "列举":
        return base

    if intent_type == "查项目":
        if v == "TestProject" and u in ("Equipment", "Environment"):
            base *= 0.5

    if intent_type == "查方法":
        if v == "TestMethod":
            base *= 0.5
        if route == "via_indicator" and u == "Indicator" and v == "TestMethod":
            base *= 0.5
        if route == "via_project" and u == "TestProject" and v == "TestMethod":
            base *= 0.5

    if intent_type == "查文件" and v == "File":
        base *= 0.5

    return base


def weighted_graph(intent_json: IntentJson) -> nx.DiGraph:
    """构建带动态代价的图副本。"""
    g = get_schema_graph()
    wg = nx.DiGraph()
    for u, v, data in g.edges(data=True):
        cost = adjust_edge_cost(u, v, data, intent_json)
        wg.add_edge(u, v, rel=data.get("rel"), cost=cost)
    return wg


def find_best_linear_path(
    intent_json: IntentJson,
    start: str,
    target: str,
) -> Optional[Tuple[list, float]]:
    """
    在带权模式图上用 Dijkstra 求 start→target 最优线性路径。

    Returns:
        (节点序列, 总代价) 或 None
    """
    wg = weighted_graph(intent_json)
    if start not in wg or target not in wg:
        return None
    try:
        path = nx.dijkstra_path(wg, start, target, weight="cost")
        cost = nx.dijkstra_path_length(wg, start, target, weight="cost")
        return path, float(cost)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
