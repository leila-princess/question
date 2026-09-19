"""Indicator 层级工具：构树、打平、叶子展开。"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Set

from utils.neo4j_flgl_status import flgl_status_cypher_param


def _sid(v: Any) -> str:
    return str(v or "").strip()


def build_indicator_tree(flat_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    roots: List[Dict[str, Any]] = []
    for raw in flat_nodes or []:
        if not isinstance(raw, dict):
            continue
        nid = _sid(raw.get("id") or raw.get("Id"))
        if not nid:
            continue
        node = dict(raw)
        node["id"] = nid
        node.setdefault("type", str(node.get("Type") or node.get("type") or "category"))
        node["parent_id"] = _sid(node.get("parent_id") or node.get("ParentId"))
        node["children"] = []
        by_id[nid] = node

    for nid, node in by_id.items():
        pid = _sid(node.get("parent_id"))
        if pid and pid in by_id and pid != nid:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)
    return roots


def flatten_indicator_tree(tree: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def _walk(nodes: List[Dict[str, Any]]) -> None:
        for n in nodes or []:
            out.append(n)
            _walk(n.get("children") or [])

    _walk(tree or [])
    return out


def normalize_indicator_roots(indicators: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将打平或树形指标列表统一为根节点森林（含无子级的 category 孤节点）。"""
    if not indicators:
        return []
    nested_child_ids: Set[str] = set()
    for ind in indicators:
        for ch in ind.get("children") or []:
            cid = _sid(ch.get("id") or ch.get("Id"))
            if cid:
                nested_child_ids.add(cid)
    if nested_child_ids:
        roots = [
            ind for ind in indicators
            if _sid(ind.get("id") or ind.get("Id")) not in nested_child_ids
        ]
        if roots:
            return roots
    flat = flatten_indicator_tree(indicators)
    return build_indicator_tree(flat)


def apply_indicator_forest_to_graph(
    source_id: str,
    indicators: List[Dict[str, Any]],
    *,
    add_node: Callable[[str, str, str, Any], None],
    add_edge: Callable[[str, str, str], None],
    link_rel: str = "CON",
) -> None:
    """将指标根节点（含 type=category 孤节点）及 HAS 子级写入图谱。"""
    if not source_id:
        return
    roots = normalize_indicator_roots(indicators)

    def _walk(ind: Dict[str, Any], parent_id: str = "") -> None:
        ind_id = _sid(ind.get("id"))
        if not ind_id:
            return
        ind_name = str(ind.get("name") or ind_id)
        add_node(ind_id, ind_name, "Indicator", ind)
        if parent_id:
            add_edge(parent_id, ind_id, "HAS")
        else:
            add_edge(source_id, ind_id, link_rel)
        for child in ind.get("children") or []:
            _walk(child, ind_id)

    for root in roots:
        _walk(root)


def expand_indicator_ids_to_leaf_ids(
    indicator_ids: List[Any],
    *,
    run_cypher: Callable[..., List[Dict[str, Any]]],
) -> List[str]:
    """
    将用户选择的指标 id 扩展为叶子指标 id：
    - 若节点无子级，返回自身
    - 若有子级，递归收集所有叶子
    """
    seeds = [_sid(x) for x in (indicator_ids or []) if _sid(x)]
    if not seeds:
        return []

    stp = flgl_status_cypher_param()
    leaf_ids: List[str] = []
    seen: Set[str] = set()

    for sid in seeds:
        if sid in seen:
            continue
        seen.add(sid)
        cypher = """
        MATCH (root:Indicator {Id: $root_id})
        WHERE (root).Status IN $flgl_enabled_status
        OPTIONAL MATCH path = (root)-[:HAS*0..]->(desc:Indicator)
        WHERE (desc).Status IN $flgl_enabled_status
        WITH DISTINCT desc
        OPTIONAL MATCH (desc)-[:HAS]->(child:Indicator)
        WHERE (child).Status IN $flgl_enabled_status
        WITH desc, count(child) AS child_count
        WHERE child_count = 0
        RETURN DISTINCT desc.Id AS id
        """
        rows = run_cypher(cypher, {**stp, "root_id": sid}) or []
        added = False
        for r in rows:
            lid = _sid(r.get("id"))
            if lid and lid not in leaf_ids:
                leaf_ids.append(lid)
                added = True
        if not added and sid not in leaf_ids:
            leaf_ids.append(sid)
    return leaf_ids
