"""Neo4j 关系 MERGE 工具（File 与业务节点）。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from config import Config

from .schema import EXTENDED_REL_TYPES, serialize_json_field

logger = logging.getLogger(__name__)

ALLOWED_NODE_LABELS = frozenset({
    "EquipmentType", "Equipment", "Environment", "TestProject",
    "Indicator", "TestMethod", "File",
})


def _flatten_props(props: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (props or {}).items():
        if isinstance(v, (list, dict)):
            out[k] = serialize_json_field(v)
        else:
            out[k] = v
    return out


def merge_relationship(
    rel_type: str,
    from_label: str,
    from_id: str,
    to_label: str,
    to_id: str,
    props: Optional[Dict[str, Any]] = None,
    *,
    driver: Any = None,
) -> bool:
    if rel_type not in EXTENDED_REL_TYPES and rel_type != "BELONGS_TO":
        logger.warning("Unknown rel_type: %s", rel_type)
    if from_label not in ALLOWED_NODE_LABELS or to_label not in ALLOWED_NODE_LABELS:
        logger.warning("Invalid node labels: %s -> %s", from_label, to_label)
        return False
    drv = driver or Config.NEO4J_DRIVER
    if drv is None:
        return False
    flat = _flatten_props(props or {})
    cypher = f"""
    MATCH (a:{from_label} {{Id: $from_id}})
    MATCH (b:{to_label} {{Id: $to_id}})
    MERGE (a)-[r:{rel_type}]->(b)
    SET r += $props
    """
    try:
        with drv.session() as session:
            session.run(cypher, from_id=from_id, to_id=to_id, props=flat)
        return True
    except Exception as e:
        logger.error("merge_relationship failed %s: %s", rel_type, e)
        return False


def merge_file_relations(file_id: str, links: List[Dict[str, Any]], *, driver: Any = None) -> int:
    """批量 MERGE 关系。link 格式：
    {rel_type, from_label, from_id, to_label, to_id, props}
    或 legacy: {node_type, node_ids} -> BELONGS_TO File->node
    """
    drv = driver or Config.NEO4J_DRIVER
    if not drv or not file_id:
        return 0
    n = 0
    for link in links or []:
        if link.get("node_type") and link.get("node_ids"):
            for nid in link.get("node_ids") or []:
                if merge_relationship(
                    "BELONGS_TO", "File", file_id, link["node_type"], nid,
                    link.get("props") or {},
                    driver=drv,
                ):
                    n += 1
            continue
        rel_type = link.get("rel_type") or "BELONGS_TO"
        from_label = link.get("from_label") or "File"
        from_id = link.get("from_id") or file_id
        to_label = link.get("to_label") or link.get("node_type") or ""
        to_id = link.get("to_id") or ""
        if not to_label or not to_id:
            continue
        if merge_relationship(
            rel_type, from_label, from_id, to_label, to_id,
            link.get("props") or link.get("rel_props") or {},
            driver=drv,
        ):
            n += 1
    return n
