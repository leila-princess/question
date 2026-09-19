"""
FLGL 解析数据导入 Neo4j 时的名称重复检测与处置（replace / skip / rename）。

- database：图库中已存在「同标签 + 同 Name + 不同 Id」的节点。
- in_file：本批待导入中「同 node_type + 同 Name + 不同 Id」。
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

NODE_KEY_SPECS: List[Tuple[str, str, bool]] = [
    ("EquipmentType", "EquipmentType", False),
    ("Equipment", "Equipment", False),
    ("Environment", "Environment", True),
    ("TestProject", "TestProject", False),
    ("Indicator", "Indicator", False),
    ("TestMethod", "TestMethod", True),
    ("standard", "Standard", True),
]

JKEY_TO_CYPHER = {a: b for a, b, _ in NODE_KEY_SPECS}
JKEY_TO_LOWER = {a: c for a, b, c in NODE_KEY_SPECS}


def _norm_name(s: Any) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip())


def _node_id(node: dict) -> Optional[str]:
    v = node.get("Id") or node.get("id")
    return str(v) if v is not None and v != "" else None


def _node_name(node: dict) -> str:
    return _norm_name(node.get("Name") or node.get("name") or "")


def collect_in_file_name_conflicts(data: dict) -> List[dict]:
    out: List[dict] = []
    nodes = data.get("nodes") or {}
    for jkey, cypher_label, _ in NODE_KEY_SPECS:
        by_name: Dict[str, List[dict]] = {}
        for node in nodes.get(jkey) or []:
            nid = _node_id(node)
            nm = _node_name(node)
            if not nid or not nm:
                continue
            by_name.setdefault(nm, []).append(node)
        for name, nlist in by_name.items():
            if len(nlist) < 2:
                continue
            all_ids = [x for x in (_node_id(n) for n in nlist) if x]
            for node in nlist:
                iid = _node_id(node)
                if not iid:
                    continue
                peer = [x for x in all_ids if x and x != iid]
                out.append(
                    {
                        "conflict_id": f"in_file:{jkey}:{iid}",
                        "kind": "in_file",
                        "node_type": jkey,
                        "cypher_label": cypher_label,
                        "import_id": iid,
                        "import_name": name,
                        "peer_import_ids": peer,
                        "message": "与本次待导入的其它同类型节点重名",
                    }
                )
    return out


def _find_conflicting_in_graph(
    session, cypher_label: str, lower: bool, name: str, import_id: str
) -> List[dict]:
    if not name or not import_id:
        return []
    if lower:
        q = f"""
        MATCH (n:{cypher_label})
        WHERE n.name = $name AND n.id <> $import_id
        RETURN n.id as id, n.name as name
        """
        r = session.run(q, name=name, import_id=import_id)
    else:
        q = f"""
        MATCH (n:{cypher_label})
        WHERE n.`Name` = $name AND n.`Id` <> $import_id
        RETURN n.`Id` as id, n.`Name` as name
        """
        r = session.run(q, name=name, import_id=import_id)
    return [dict(x) for x in r]


def collect_database_name_conflicts(session, data: dict) -> List[dict]:
    """(jkey, import_id) 聚合图库中同名的已有节点（多条）。"""
    buckets: Dict[Tuple[str, str], dict] = {}
    nodes = data.get("nodes") or {}
    for jkey, cypher_label, lower in NODE_KEY_SPECS:
        for node in nodes.get(jkey) or []:
            iid = _node_id(node)
            nm = _node_name(node)
            if not iid or not nm:
                continue
            hits = _find_conflicting_in_graph(
                session, cypher_label, lower, nm, str(iid)
            )
            if not hits:
                continue
            key = (jkey, str(iid))
            if key not in buckets:
                buckets[key] = {
                    "import_name": nm,
                    "hits": [],
                }
            buckets[key]["hits"].extend(hits)
    out: List[dict] = []
    for (jkey, iid), rec in buckets.items():
        cypher_label = JKEY_TO_CYPHER[jkey]
        lower = JKEY_TO_LOWER[jkey]
        seen: Set[str] = set()
        unique_hits: List[dict] = []
        for h in rec["hits"]:
            eid = h.get("id")
            if eid is None or str(eid) in seen:
                continue
            seen.add(str(eid))
            unique_hits.append(
                {"id": str(eid), "name": h.get("name") or ""}
            )
        if not unique_hits:
            continue
        out.append(
            {
                "conflict_id": f"db:{jkey}:{iid}",
                "kind": "database",
                "node_type": jkey,
                "cypher_label": cypher_label,
                "uses_lower_case": lower,
                "import_id": iid,
                "import_name": rec["import_name"],
                "existing": unique_hits,
            }
        )
    return out


def collect_all_conflicts(
    session, data: dict
) -> Tuple[List[dict], List[dict], Dict[str, dict]]:
    in_file = collect_in_file_name_conflicts(data)
    in_db = collect_database_name_conflicts(session, data)
    index: Dict[str, dict] = {}
    for c in in_file + in_db:
        index[c["conflict_id"]] = c
    return in_db, in_file, index


def expected_resolution_keys(db_conf: List, in_f: List) -> Set[str]:
    return {c["conflict_id"] for c in (db_conf + in_f)}


def _merge_resolution_list(resolution_list: List[dict]) -> Dict[str, dict]:
    m: Dict[str, dict] = {}
    for r in resolution_list or []:
        cid = r.get("conflict_id")
        if not cid:
            continue
        m[str(cid)] = r
    return m


def is_conflict_resolved(
    conflict_id: str, cobj: dict, rmap: Dict[str, dict]
) -> bool:
    if conflict_id in rmap and str(
        rmap[conflict_id].get("action", "")
    ).strip().lower() in ("replace", "skip", "rename"):
        return True
    jk = cobj.get("node_type")
    iid = cobj.get("import_id")
    if cobj.get("kind") == "database" and jk and iid is not None:
        p = f"in_file:{jk}:{iid}"
        if p in rmap and str(rmap[p].get("action", "")).lower() == "skip":
            return True
    if cobj.get("kind") == "in_file" and jk and iid is not None:
        p = f"db:{jk}:{iid}"
        if p in rmap and str(rmap[p].get("action", "")).lower() == "skip":
            return True
    return False


def validate_resolutions_against_index(
    resolutions: List[dict], index: Dict[str, dict]
) -> None:
    rmap = _merge_resolution_list(resolutions)
    if not index:
        if rmap:
            raise ValueError("当前数据已无名称冲突，请勿提交 resolutions")
        return
    for cid in rmap:
        if cid not in index:
            raise ValueError(f"冲突项不存在或已变化: {cid}，请关闭弹窗后重新点导入以刷新检测")
    missing: List[str] = []
    for cid, cobj in index.items():
        if is_conflict_resolved(cid, cobj, rmap):
            continue
        missing.append(cid)
    if missing:
        raise ValueError("以下名称冲突项尚未处理: " + ", ".join(missing))

    for cid, r in rmap.items():
        a = (r.get("action") or "").strip().lower()
        if a not in ("replace", "skip", "rename"):
            raise ValueError(f"非法 action: {cid} -> {a}")


def apply_name_conflict_resolutions(
    data: dict,
    resolutions: List[dict],
    conflict_index: Dict[str, dict],
) -> Tuple[dict, Set[str], List[Tuple[str, str, bool]]]:
    """
    返回: (新 data, skip 的 import_id, replace 待 DETACH DELETE 的 (cypher_label, eid, uses_lower))。
    """
    work = copy.deepcopy(data)
    rmap = _merge_resolution_list(resolutions)
    skipped: Set[str] = set()
    to_delete: List[Tuple[str, str, bool]] = []
    seen_delete: Set[Tuple[str, str, bool]] = set()

    if not (work.get("nodes") and isinstance(work["nodes"], dict)):
        work["nodes"] = work.get("nodes") or {}
    if "relationships" not in work or work["relationships"] is None:
        work["relationships"] = []

    for cid, r in rmap.items():
        cobj = conflict_index.get(cid)
        if not cobj:
            raise ValueError(f"未识别的冲突项: {cid}")
        action = (r.get("action") or "").strip().lower()
        if not action:
            raise ValueError(f"未指定 action: {cid}")
        kind = cobj.get("kind")
        jkey = cobj["node_type"]
        iid = cobj["import_id"]

        if action == "skip":
            if iid is not None:
                skipped.add(str(iid))
            continue

        if action == "rename":
            new_name = _norm_name(r.get("new_name", ""))
            if not new_name:
                raise ValueError(f"重命名需提供 new_name: {cid}")
            arr = (work.get("nodes") or {}).get(jkey) or []
            found = False
            for n in arr:
                if n and str(_node_id(n)) == str(iid):
                    n["Name"] = new_name
                    found = True
                    break
            if not found:
                raise ValueError(f"未找到待重命名节点: {jkey} {iid}")
            continue

        if action == "replace" and kind == "database":
            for ex in cobj.get("existing") or []:
                eid = (ex or {}).get("id")
                if eid is None:
                    continue
                tup = (
                    cobj["cypher_label"],
                    str(eid),
                    cobj.get("uses_lower_case", False),
                )
                if tup not in seen_delete:
                    seen_delete.add(tup)
                    to_delete.append(tup)
            continue

        if action == "replace" and kind == "in_file":
            raise ValueError("文件内重名请使用 跳过 或 重命名，不能选择「替换图库」")
        if action and action not in ("replace", "skip", "rename"):
            raise ValueError(f"非法 action: {action}")

    if skipped:
        for jkey, _, _ in NODE_KEY_SPECS:
            arr = (work.get("nodes") or {}).get(jkey)
            if not arr:
                continue
            (work["nodes"])[jkey] = [n for n in arr if str(_node_id(n)) not in skipped]

    # 去掉与 skip 有关的关系
    if skipped:
        rels = work.get("relationships") or []
        work["relationships"] = [
            rel
            for rel in rels
            if str(rel.get("from_id")) not in skipped
            and str(rel.get("to_id")) not in skipped
        ]
    return work, skipped, to_delete


def detach_delete_nodes(
    session, to_delete: List[Tuple[str, str, bool]]
) -> int:
    n = 0
    for label, eid, low in to_delete:
        if low:
            session.run(
                f"MATCH (a:{label} {{id: $eid}}) DETACH DELETE a", eid=eid
            )
        else:
            session.run(
                f"MATCH (a:{label} {{`Id`: $eid}}) DETACH DELETE a", eid=eid
            )
        n += 1
    return n


def normalize_environment_nodes_for_import(session, data: dict) -> dict:
    """
    导入前按 Name 复用图库已有 Environment，去掉待创建的重名环境节点并重映射关系 Id。
    兜底：历史 JSON 或未走 Neo4j 预加载注册表时，仍避免重复建环境节点。
    """
    work = copy.deepcopy(data)
    nodes = work.get("nodes")
    if not isinstance(nodes, dict):
        return work

    env_nodes = list(nodes.get("Environment") or [])
    if not env_nodes:
        return work

    existing_by_name: Dict[str, str] = {}
    try:
        rows = session.run(
            """
            MATCH (n:Environment)
            WHERE n.Name IS NOT NULL AND trim(n.Name) <> ''
            RETURN n.Name AS name, n.Id AS id
            """
        )
        for rec in rows:
            nm = _norm_name(rec.get("name"))
            eid = rec.get("id")
            if nm and eid is not None and nm not in existing_by_name:
                existing_by_name[nm] = str(eid)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取图库环境节点失败，跳过环境去重: %s", e)
        return work

    id_remap: Dict[str, str] = {}
    kept: List[dict] = []
    seen_names: Dict[str, str] = {}

    for node in env_nodes:
        nid = _node_id(node)
        nm = _node_name(node)
        if not nid:
            kept.append(node)
            continue

        canonical: Optional[str] = None
        if nm in existing_by_name:
            canonical = existing_by_name[nm]
        elif nm in seen_names:
            canonical = seen_names[nm]

        if canonical:
            if str(nid) != canonical:
                id_remap[str(nid)] = canonical
            continue

        if nm:
            seen_names[nm] = str(nid)
        kept.append(node)

    nodes["Environment"] = kept

    for rel in work.get("relationships") or []:
        if rel.get("from_type") == "Environment":
            fid = str(rel.get("from_id") or "")
            if fid in id_remap:
                rel["from_id"] = id_remap[fid]
        if rel.get("to_type") == "Environment":
            tid = str(rel.get("to_id") or "")
            if tid in id_remap:
                rel["to_id"] = id_remap[tid]

    return work


def run_flgl_neo4j_import(session, data: dict) -> Dict[str, int]:
    """
    执行与 upload_routes.import_to_neo4j 中一致的写库逻辑。返回
    { nodes_created, relationships_created }。
    """
    nodes_created = 0
    relationships_created = 0

    def _with_id_aliases(properties: dict) -> dict:
        """
        统一节点主键字段，避免关系按 Id 匹配不到节点：
        - 优先保证有 Id
        - 同时补齐 id/name/... 的小写别名，兼容旧查询
        """
        props = dict(properties or {})

        if not props.get("Id") and props.get("id"):
            props["Id"] = props.get("id")
        if not props.get("id") and props.get("Id"):
            props["id"] = props.get("Id")

        alias_pairs = [
            ("Name", "name"),
            ("Label", "label"),
            ("Description", "description"),
            ("Type", "type"),
            ("Status", "status"),
            ("User_id", "user_id"),
            ("Create_time", "create_time"),
        ]
        for up, low in alias_pairs:
            if props.get(up) is not None and props.get(low) in (None, ""):
                props[low] = props.get(up)
            if props.get(low) is not None and props.get(up) in (None, ""):
                props[up] = props.get(low)
        return props

    def create_node_with_properties(tx, label, properties):
        properties = _with_id_aliases(properties)
        node_id = properties.get("Id")
        if not node_id:
            return False
        q = f"""
        MERGE (n:{label} {{`Id`: $Id}})
        SET n = $properties
        RETURN n
        """
        tx.run(q, Id=node_id, properties=properties)
        return True

    def create_relationship_with_properties(tx, rel_type, from_id, to_id, properties=None):
        q = f"""
        MATCH (a {{`Id`: $from_id}})
        MATCH (b {{`Id`: $to_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        """
        if properties:
            q += " SET r = $props"
            tx.run(q, from_id=from_id, to_id=to_id, props=properties)
        else:
            tx.run(q, from_id=from_id, to_id=to_id)
        return True

    nodes = data.get("nodes", {})

    for node in nodes.get("EquipmentType", []):
        try:
            session.execute_write(create_node_with_properties, "EquipmentType", node)
            nodes_created += 1
        except Exception as e:  # noqa: BLE001
            logger.error("创建设备类型节点失败: %s - %s", node.get("Id"), e)

    for node in nodes.get("Equipment", []):
        try:
            session.execute_write(create_node_with_properties, "Equipment", node)
            nodes_created += 1
        except Exception as e:  # noqa: BLE001
            logger.error("创建设备节点失败: %s - %s", node.get("Id"), e)

    for node in nodes.get("Environment", []):
        try:
            session.execute_write(
                create_node_with_properties, "Environment", node
            )
            nodes_created += 1
        except Exception as e:  # noqa: BLE001
            logger.error("创建环境节点失败: %s - %s", node.get("Id") or node.get("id"), e)

    for node in nodes.get("TestProject", []):
        try:
            session.execute_write(
                create_node_with_properties, "TestProject", node
            )
            nodes_created += 1
        except Exception as e:  # noqa: BLE001
            logger.error("创建项目节点失败: %s - %s", node.get("Id"), e)

    for node in nodes.get("Indicator", []):
        try:
            session.execute_write(
                create_node_with_properties, "Indicator", node
            )
            nodes_created += 1
        except Exception as e:  # noqa: BLE001
            logger.error("创建指标节点失败: %s - %s", node.get("Id"), e)

    for node in nodes.get("TestMethod", []):
        try:
            session.execute_write(
                create_node_with_properties, "TestMethod", node
            )
            nodes_created += 1
        except Exception as e:  # noqa: BLE001
            logger.error("创建测试方法节点失败: %s - %s", node.get("Id") or node.get("id"), e)

    for node in nodes.get("standard", []):
        try:
            session.execute_write(
                create_node_with_properties, "Standard", node
            )
            nodes_created += 1
        except Exception as e:  # noqa: BLE001
            logger.error("创建标准节点失败: %s - %s", node.get("Id") or node.get("id"), e)

    for rel in data.get("relationships", []):
        rel_type = rel.get("type")
        from_id = rel.get("from_id")
        to_id = rel.get("to_id")
        rel_props = {
            k: v
            for k, v in rel.items()
            if k
            not in ("type", "from_id", "to_id", "from_type", "to_type")
        }
        if not rel_type or not from_id or not to_id:
            logger.warning("关系缺少必要字段: %s", rel)
            continue
        try:
            session.execute_write(
                create_relationship_with_properties,
                rel_type,
                from_id,
                to_id,
                rel_props if rel_props else None,
            )
            relationships_created += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("创建关系失败: %s (%s -> %s) - %s", rel_type, from_id, to_id, e)

    return {
        "nodes_created": nodes_created,
        "relationships_created": relationships_created,
    }
