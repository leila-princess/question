"""
将前端/配置传入的扁平项目、指标列表重建为父子森林（category → test）。
供 JYLLM skeleton_builder 与 JYGZ render 共用。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def _norm_id(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _node_id(node: Dict[str, Any], *, kind: str = "project") -> str:
    if not isinstance(node, dict):
        return ""
    if kind == "indicator":
        return _norm_id(node.get("indicatorId") or node.get("id") or node.get("Id"))
    return _norm_id(node.get("projectId") or node.get("id") or node.get("Id"))


def _node_type(node: Dict[str, Any]) -> str:
    t = str(node.get("type") or node.get("Type") or "").strip().lower()
    if t in ("category", "父级", "parent"):
        return "category"
    if t in ("test", "子级", "child"):
        return "test"
    children = node.get("children") or []
    if isinstance(children, list) and children:
        child_types = [
            str((c or {}).get("type") or (c or {}).get("Type") or "").strip().lower()
            for c in children
            if isinstance(c, dict)
        ]
        if any(ct in ("test", "子级", "child") for ct in child_types):
            return "category"
    return "test"


def _node_name(node: Dict[str, Any], fallback: str = "") -> str:
    for k in ("name", "Name", "label", "Label", "title"):
        v = str(node.get(k) or "").strip()
        if v:
            return v
    return fallback or _node_id(node) or "未命名"


def _clone_shallow(node: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(node)
    out.pop("children", None)
    return out


def _index_nodes_by_id(nodes: List[Dict[str, Any]], *, kind: str = "project") -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}

    def _walk(arr: List[Dict[str, Any]]) -> None:
        for n in arr or []:
            if not isinstance(n, dict):
                continue
            nid = _node_id(n, kind=kind)
            if nid and nid not in idx:
                idx[nid] = n
            for c in n.get("children") or []:
                if isinstance(c, dict):
                    _walk([c])

    _walk(nodes)
    return idx


def _parent_map_from_children(nodes: List[Dict[str, Any]], *, kind: str = "project") -> Dict[str, str]:
    """子 id → 父 id（由 children 嵌套反推）。"""
    parent_of: Dict[str, str] = {}

    def _walk(arr: List[Dict[str, Any]], parent_id: str = "") -> None:
        for n in arr or []:
            if not isinstance(n, dict):
                continue
            nid = _node_id(n, kind=kind)
            if nid and parent_id:
                parent_of[nid] = parent_id
            for c in n.get("children") or []:
                if isinstance(c, dict):
                    _walk([c], nid)

    _walk(nodes)
    return parent_of


def _parent_map_from_payload_fields(nodes: List[Dict[str, Any]], *, kind: str = "project") -> Dict[str, str]:
    """从 categoryId / parent_* 等扁平字段补全 parent_of。"""
    parent_of: Dict[str, str] = {}
    if kind == "indicator":
        keys = (
            "categoryId",
            "category_id",
            "parent_indicator_id",
            "parentIndicatorId",
            "parent_id",
            "parentId",
        )
    else:
        keys = (
            "categoryId",
            "category_id",
            "parent_project_id",
            "parentProjectId",
            "parent_id",
            "parentId",
        )
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        nid = _node_id(n, kind=kind)
        if not nid:
            continue
        for k in keys:
            pid = _norm_id(n.get(k))
            if pid and pid != nid:
                parent_of[nid] = pid
                break
    return parent_of


def _ensure_parents_indexed(
    indexed: Dict[str, Dict[str, Any]],
    parent_of: Dict[str, str],
    *,
    kind: str = "project",
) -> None:
    """父节点不在 payload 时从 Neo4j 拉取或生成 category 占位节点。"""
    parent_ids = {_norm_id(pid) for pid in parent_of.values() if _norm_id(pid)}
    missing = [pid for pid in parent_ids if pid not in indexed]
    if not missing:
        return

    if kind == "indicator":
        fetched = _fetch_indicator_nodes_by_ids_from_neo4j(missing)
    else:
        fetched = _fetch_project_nodes_by_ids_from_neo4j(missing)

    for pid in missing:
        if pid in fetched:
            indexed[pid] = fetched[pid]
        else:
            indexed[pid] = {"id": pid, "name": pid, "type": "category", "Type": "category"}


def _rebuild_forest_core(
    flat_nodes: Optional[List[Any]],
    *,
    kind: str = "project",
) -> List[Dict[str, Any]]:
    def _linearize(nodes: List[Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        def _walk(arr: List[Any]) -> None:
            for n in arr or []:
                if not isinstance(n, dict):
                    continue
                nid = _node_id(n, kind=kind)
                if nid and nid not in seen:
                    seen.add(nid)
                    out.append(n)
                _walk([c for c in (n.get("children") or []) if isinstance(c, dict)])

        _walk(nodes or [])
        return out

    raw = _linearize(flat_nodes or [])
    raw = [n for n in raw if _node_id(n, kind=kind)]
    if not raw:
        return []

    indexed = _index_nodes_by_id(raw, kind=kind)
    parent_of = _parent_map_from_children(raw, kind=kind)
    parent_of.update(_parent_map_from_payload_fields(raw, kind=kind))
    selected = _selected_id_set(raw, kind=kind)

    missing_parents = [
        cid for cid in selected if cid not in parent_of and _node_type(indexed.get(cid, {})) == "test"
    ]
    if missing_parents:
        if kind == "indicator":
            parent_of.update(_fetch_indicator_parent_map_from_neo4j(missing_parents))
        else:
            parent_of.update(_fetch_project_parent_map_from_neo4j(missing_parents))

    _ensure_parents_indexed(indexed, parent_of, kind=kind)

    for cid in list(selected):
        pid = parent_of.get(cid)
        if pid and pid not in selected:
            selected.add(pid)

    children_map: Dict[str, List[str]] = {}
    for cid in selected:
        pid = parent_of.get(cid)
        if pid and pid in selected:
            children_map.setdefault(pid, [])
            if cid not in children_map[pid]:
                children_map[pid].append(cid)

    roots: List[str] = []
    for nid in selected:
        pid = parent_of.get(nid)
        if not pid or pid not in selected:
            roots.append(nid)

    order = {_node_id(n, kind=kind): i for i, n in enumerate(raw)}
    roots.sort(key=lambda x: order.get(x, 9999))

    def _build_node(nid: str) -> Dict[str, Any]:
        src = indexed.get(nid, {"id": nid})
        node = _clone_shallow(src)
        node["id"] = nid
        node["type"] = _node_type(src)
        child_ids = children_map.get(nid, [])
        child_ids.sort(key=lambda x: order.get(x, 9999))
        node["children"] = [_build_node(cid) for cid in child_ids]
        return node

    return [_build_node(rid) for rid in roots]


def _selected_id_set(flat_nodes: List[Dict[str, Any]], *, kind: str = "project") -> Set[str]:
    return {_node_id(n, kind=kind) for n in flat_nodes if isinstance(n, dict) and _node_id(n, kind=kind)}


def _fetch_project_parent_map_from_neo4j(child_ids: List[str]) -> Dict[str, str]:
    """test Id → category Id（图库 HAS 关系）。"""
    ids = [_norm_id(x) for x in child_ids if _norm_id(x)]
    if not ids:
        return {}
    try:
        from config import Config
        from utils.neo4j_flgl_status import flgl_status_cypher_param

        if not Config.NEO4J_DRIVER:
            return {}
        st = flgl_status_cypher_param()
        cypher = """
        UNWIND $ids AS cid
        MATCH (cat:TestProject {Type: 'category'})-[:HAS]->(test:TestProject)
        WHERE (toString(test.Id) = toString(cid) OR toString(test.id) = toString(cid))
          AND (cat).Status IN $flgl_enabled_status
          AND (test).Status IN $flgl_enabled_status
        RETURN toString(test.Id) AS child_id, toString(cat.Id) AS parent_id
        """
        out: Dict[str, str] = {}
        with Config.NEO4J_DRIVER.session() as session:
            for r in session.run(cypher, {**st, "ids": ids}):
                cid = _norm_id(r.get("child_id"))
                pid = _norm_id(r.get("parent_id"))
                if cid and pid:
                    out[cid] = pid
        return out
    except Exception as e:
        logger.warning("[project_tree_utils] Neo4j parent lookup: %s", e)
        return {}


def _fetch_indicator_parent_map_from_neo4j(child_ids: List[str]) -> Dict[str, str]:
    ids = [_norm_id(x) for x in child_ids if _norm_id(x)]
    if not ids:
        return {}
    try:
        from config import Config
        from utils.neo4j_flgl_status import flgl_status_cypher_param

        if not Config.NEO4J_DRIVER:
            return {}
        st = flgl_status_cypher_param()
        cypher = """
        UNWIND $ids AS cid
        MATCH (cat:Indicator {Type: 'category'})-[:HAS]->(test:Indicator)
        WHERE (toString(test.Id) = toString(cid) OR toString(test.id) = toString(cid))
          AND (cat).Status IN $flgl_enabled_status
          AND (test).Status IN $flgl_enabled_status
        RETURN toString(test.Id) AS child_id, toString(cat.Id) AS parent_id
        """
        out: Dict[str, str] = {}
        with Config.NEO4J_DRIVER.session() as session:
            for r in session.run(cypher, {**st, "ids": ids}):
                cid = _norm_id(r.get("child_id"))
                pid = _norm_id(r.get("parent_id"))
                if cid and pid:
                    out[cid] = pid
        return out
    except Exception as e:
        logger.warning("[project_tree_utils] Neo4j indicator parent lookup: %s", e)
        return {}


def _fetch_project_nodes_by_ids_from_neo4j(node_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    ids = [_norm_id(x) for x in node_ids if _norm_id(x)]
    if not ids:
        return {}
    try:
        from config import Config
        from utils.neo4j_flgl_status import flgl_status_cypher_param

        if not Config.NEO4J_DRIVER:
            return {}
        st = flgl_status_cypher_param()
        cypher = """
        UNWIND $ids AS nid
        MATCH (p:TestProject)
        WHERE (toString(p.Id) = toString(nid) OR toString(p.id) = toString(nid))
          AND p.Status IN $flgl_enabled_status
        RETURN toString(p.Id) AS id,
               coalesce(p.Name, p.name, '') AS name,
               coalesce(p.Type, p.type, 'category') AS type
        """
        out: Dict[str, Dict[str, Any]] = {}
        with Config.NEO4J_DRIVER.session() as session:
            for r in session.run(cypher, {**st, "ids": ids}):
                pid = _norm_id(r.get("id"))
                if not pid:
                    continue
                out[pid] = {
                    "id": pid,
                    "name": str(r.get("name") or pid).strip() or pid,
                    "type": str(r.get("type") or "category").strip().lower() or "category",
                }
        return out
    except Exception as e:
        logger.warning("[project_tree_utils] Neo4j project node fetch: %s", e)
        return {}


def _fetch_indicator_nodes_by_ids_from_neo4j(node_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    ids = [_norm_id(x) for x in node_ids if _norm_id(x)]
    if not ids:
        return {}
    try:
        from config import Config
        from utils.neo4j_flgl_status import flgl_status_cypher_param

        if not Config.NEO4J_DRIVER:
            return {}
        st = flgl_status_cypher_param()
        cypher = """
        UNWIND $ids AS nid
        MATCH (p:Indicator)
        WHERE (toString(p.Id) = toString(nid) OR toString(p.id) = toString(nid))
          AND p.Status IN $flgl_enabled_status
        RETURN toString(p.Id) AS id,
               coalesce(p.Name, p.name, '') AS name,
               coalesce(p.Type, p.type, 'category') AS type
        """
        out: Dict[str, Dict[str, Any]] = {}
        with Config.NEO4J_DRIVER.session() as session:
            for r in session.run(cypher, {**st, "ids": ids}):
                iid = _norm_id(r.get("id"))
                if not iid:
                    continue
                out[iid] = {
                    "id": iid,
                    "name": str(r.get("name") or iid).strip() or iid,
                    "type": str(r.get("type") or "category").strip().lower() or "category",
                }
        return out
    except Exception as e:
        logger.warning("[project_tree_utils] Neo4j indicator node fetch: %s", e)
        return {}


def rebuild_project_forest(flat_nodes: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """
    扁平选中列表 → 父子森林。
    - 选中子级时自动纳入父级 category（若存在）
    - 父级下仅保留被选中的子级
    - 无父级的 test 作为根节点
    - 仅选子级时通过 categoryId / Neo4j 自动补父级
    """
    return _rebuild_forest_core(flat_nodes, kind="project")


def rebuild_indicator_forest(flat_nodes: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """指标扁平列表 → 父子森林（逻辑同 rebuild_project_forest）。"""
    return _rebuild_forest_core(flat_nodes, kind="indicator")


def flatten_project_headings_for_validation(
    forest: List[Dict[str, Any]],
    env_prefix: str = "2.1.1",
) -> List[Tuple[int, str]]:
    """
    从森林提取期望标题：(markdown_level, title)。
    level 5 = ##### 父级/孤立项目；level 6 = ###### 子级。
    """
    out: List[Tuple[int, str]] = []
    seq = 0

    def _walk(nodes: List[Dict[str, Any]], parent_no: str, level: int) -> None:
        nonlocal seq
        for n in nodes or []:
            nid = _node_id(n)
            if not nid:
                continue
            seq += 1
            pno = f"{parent_no}.{seq}"
            name = _node_name(n, nid)
            out.append((min(level, 6), name))
            children = [c for c in (n.get("children") or []) if isinstance(c, dict)]
            if children:
                _walk(children, pno, level + 1)

    _walk(forest, env_prefix, 5)
    return out


def flatten_indicator_headings_for_validation(
    forest: List[Dict[str, Any]],
    section_prefix: str = "2.2",
) -> List[Tuple[int, str]]:
    """指标：level 4 = #### 父级；level 5 = ##### 子级。"""
    out: List[Tuple[int, str]] = []
    seq = 0

    def _walk(nodes: List[Dict[str, Any]], parent_no: str, level: int) -> None:
        nonlocal seq
        for n in nodes or []:
            iid = _node_id(n, kind="indicator")
            if not iid:
                continue
            seq += 1
            ino = f"{parent_no}.{seq}"
            name = _node_name(n, iid)
            out.append((min(level, 6), name))
            children = [c for c in (n.get("children") or []) if isinstance(c, dict)]
            if children:
                _walk(children, ino, level + 1)

    _walk(forest, section_prefix, 4)
    return out
