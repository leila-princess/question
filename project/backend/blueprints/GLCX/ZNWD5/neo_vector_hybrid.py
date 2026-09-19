"""
Neo4j Deep Integration A/B：db.index.vector.queryNodes + 关系遍历（Phase 4）。
需 Neo4j 5.13+ 且节点已写入 embedding 属性并创建 vector index。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from .embedding_service import encode_query

logger = logging.getLogger(__name__)


def neo_vector_index_available(driver: Any) -> bool:
    if driver is None:
        return False
    try:
        with driver.session() as session:
            rec = session.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v").single()
            version = str((rec or {}).get("v") or "")
            major_minor = tuple(int(x) for x in version.split(".")[:2] if x.isdigit())
            return major_minor >= (5, 13)
    except Exception:
        return False


def neo_vector_hybrid_query(driver: Any, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    示例 Cypher：
    CALL db.index.vector.queryNodes('equipment_embedding', $qvec, $topk)
    YIELD node, score
    MATCH (node)-[r:CON]->(m:Indicator)
    RETURN node AS n, r, m, score
    """
    if not driver:
        return [{"error": "Neo4j driver not initialized"}]

    if os.environ.get("ZNWD5_NEO_VECTOR_ENABLED", "0").lower() not in ("1", "true", "yes"):
        return [{"error": "Neo4j vector hybrid disabled; set ZNWD5_NEO_VECTOR_ENABLED=1"}]

    q = str(params.get("query") or "").strip()
    label = str(params.get("node_label") or "Equipment")
    rel = str(params.get("rel_type") or "CON")
    to_label = str(params.get("to_label") or "").strip()
    top_k = max(1, min(int(params.get("top_k") or 5), 20))
    index_name = str(params.get("index_name") or f"{label.lower()}_embedding_idx")

    q_vec = encode_query(q)
    cypher = f"""
    CALL db.index.vector.queryNodes($index_name, $topk, $qvec)
    YIELD node, score
    WITH node, score
    WHERE $label IN labels(node)
    """
    if to_label:
        cypher += f"""
    MATCH (node)-[r:{rel}]->(m:{to_label})
    RETURN node AS n, r, m, score
    ORDER BY score DESC
    LIMIT $limit
    """
    else:
        cypher += """
    RETURN node AS n, score
    ORDER BY score DESC
    LIMIT $limit
    """

    rows: List[Dict[str, Any]] = []
    try:
        with driver.session() as session:
            for rec in session.run(
                cypher,
                index_name=index_name,
                qvec=q_vec,
                topk=top_k,
                label=label,
                limit=top_k,
            ):
                row: Dict[str, Any] = {"score": rec.get("score")}
                n = rec.get("n")
                if n is not None:
                    row["n"] = {"labels": list(n.labels), "properties": dict(n.items())}
                r = rec.get("r")
                if r is not None:
                    row["r"] = {"type": r.type, "properties": dict(r.items())}
                m = rec.get("m")
                if m is not None:
                    row["m"] = {"labels": list(m.labels), "properties": dict(m.items())}
                rows.append(row)
    except Exception as exc:
        logger.warning("[ZNWD5][neo_vector] 查询失败: %s", exc)
        return [{"error": f"neo_vector_hybrid_query failed: {exc}"}]
    return rows
