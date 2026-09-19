"""ZNWD4 可复用的 RelFact Neo4j 查询实现。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from utils.neo4j_flgl_status import flgl_status_cypher_param

EXTENDED_RELS = frozenset({
    "DERIVED_FROM", "GOVERNED_BY", "CONSTRAINED_BY",
    "LIMITED_BY", "APPLIES_TO", "CITES",
})


def _parse_json_field(val: Any) -> Any:
    if val is None or isinstance(val, (list, dict)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def _serialize_node(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "type") and not hasattr(v, "labels"):
        return {
            "type": getattr(v, "type", ""),
            "properties": dict(v.items()) if hasattr(v, "items") else {},
        }
    if hasattr(v, "labels") and hasattr(v, "items"):
        return {
            "labels": list(v.labels),
            "properties": {x: y for x, y in v.items() if y is not None},
            "__elementId": getattr(v, "element_id", None),
        }
    return v


def _run(driver: Any, cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    if driver is None:
        return [{"error": "Neo4j driver not available"}]
    out: List[Dict[str, Any]] = []
    try:
        with driver.session() as session:
            for rec in session.run(cypher, **params):
                row: Dict[str, Any] = {}
                for key in rec.keys():
                    row[key] = _serialize_node(rec.get(key))
                out.append(row)
    except Exception as e:
        out.append({"error": str(e)})
    return out


def neo_file_by_std(driver: Any, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    std_kw = str(params.get("std_code") or params.get("keyword") or params.get("value") or "").strip()
    limit = max(1, min(int(params.get("limit") or 20), 100))
    if not std_kw:
        return [{"error": "neo_file_by_std requires std_code"}], "", {}
    cypher = """
    MATCH (f:File)
    WHERE f.extract_status = 'done'
      AND (
        toLower(coalesce(f.Std_code,'')) CONTAINS toLower($kw)
        OR toLower(coalesce(f.Name,'')) CONTAINS toLower($kw)
      )
    RETURN f AS n
    LIMIT $limit
    """
    run_p = {"kw": std_kw, "limit": limit}
    rows = _run(driver, cypher, run_p)
    return rows, cypher, run_p


def neo_methods_from_file(driver: Any, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    std_kw = str(params.get("std_code") or params.get("keyword") or "").strip()
    file_id = str(params.get("file_id") or "").strip()
    limit = max(1, min(int(params.get("limit") or 50), 200))
    stp = flgl_status_cypher_param()
    if file_id:
        cypher = """
        MATCH (tm:TestMethod)-[r:DERIVED_FROM]->(f:File {Id: $file_id})
        WHERE tm.Status IS NULL OR tm.Status IN $flgl_enabled_status
        RETURN tm AS n, r AS r, f AS m
        LIMIT $limit
        """
        run_p = {**stp, "file_id": file_id, "limit": limit}
    elif std_kw:
        cypher = """
        MATCH (f:File)
        WHERE toLower(coalesce(f.Std_code,'')) CONTAINS toLower($kw)
           OR toLower(coalesce(f.Name,'')) CONTAINS toLower($kw)
        MATCH (tm:TestMethod)-[r:DERIVED_FROM]->(f)
        WHERE tm.Status IS NULL OR tm.Status IN $flgl_enabled_status
        RETURN tm AS n, r AS r, f AS m
        LIMIT $limit
        """
        run_p = {**stp, "kw": std_kw, "limit": limit}
    else:
        return [{"error": "neo_methods_from_file requires file_id or std_code"}], "", {}
    return _run(driver, cypher, run_p), cypher, run_p


def neo_governed_by_file(driver: Any, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    project_kw = str(params.get("project_keyword") or params.get("keyword") or "").strip()
    std_kw = str(params.get("std_code") or "").strip()
    limit = max(1, min(int(params.get("limit") or 30), 200))
    stp = flgl_status_cypher_param()
    cypher = """
    MATCH (tp:TestProject)-[r:GOVERNED_BY]->(f:File)
    WHERE (tp.Status IS NULL OR tp.Status IN $flgl_enabled_status)
      AND ($project_kw = '' OR toLower(coalesce(tp.Name,'')) CONTAINS toLower($project_kw))
      AND ($std_kw = '' OR toLower(coalesce(f.Std_code,'')) CONTAINS toLower($std_kw))
    RETURN tp AS n, r AS r, f AS m
    LIMIT $limit
    """
    run_p = {**stp, "project_kw": project_kw, "std_kw": std_kw, "limit": limit}
    return _run(driver, cypher, run_p), cypher, run_p


def neo_constrained_by_file(driver: Any, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    env_kw = str(params.get("environment_keyword") or params.get("keyword") or "").strip()
    limit = max(1, min(int(params.get("limit") or 30), 200))
    stp = flgl_status_cypher_param()
    cypher = """
    MATCH (env:Environment)-[r:CONSTRAINED_BY]->(f:File)
    WHERE (env.Status IS NULL OR env.Status IN $flgl_enabled_status)
      AND ($env_kw = '' OR toLower(coalesce(env.Name,'')) CONTAINS toLower($env_kw))
    RETURN env AS n, r AS r, f AS m
    LIMIT $limit
    """
    run_p = {**stp, "env_kw": env_kw, "limit": limit}
    return _run(driver, cypher, run_p), cypher, run_p


def neo_rel_fact_query(driver: Any, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    rel_type = str(params.get("rel_type") or "DERIVED_FROM").strip()
    if rel_type not in EXTENDED_RELS:
        return [{"error": f"unsupported rel_type {rel_type}"}], "", {}
    from_type = str(params.get("from_type") or "TestMethod").strip()
    to_type = str(params.get("to_type") or "File").strip()
    kw = str(params.get("keyword") or params.get("value") or "").strip()
    where_side = str(params.get("where_side") or "to").strip()
    limit = max(1, min(int(params.get("limit") or 30), 200))
    stp = flgl_status_cypher_param()

    if where_side == "from":
        where = "($kw = '' OR toLower(coalesce(a.Name,'')) CONTAINS toLower($kw) OR toLower(coalesce(a.Std_code,'')) CONTAINS toLower($kw))"
    else:
        where = "($kw = '' OR toLower(coalesce(b.Name,'')) CONTAINS toLower($kw) OR toLower(coalesce(b.Std_code,'')) CONTAINS toLower($kw))"

    cypher = f"""
    MATCH (a:{from_type})-[r:{rel_type}]->(b:{to_type})
    WHERE {where}
    RETURN a AS n, r AS r, b AS m
    LIMIT $limit
    """
    run_p = {**stp, "kw": kw, "limit": limit}
    return _run(driver, cypher, run_p), cypher, run_p


def rel_fact_param_match(driver: Any, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    from blueprints.RelFact.retrieval.rel_fact_rag import retrieve

    query = str(params.get("query") or "").strip()
    top_k = int(params.get("top_k") or 5)
    if not query:
        return [{"error": "rel_fact_param_match requires query"}], "", {}
    result = retrieve(query, top_k=top_k, driver=driver)
    rows: List[Dict[str, Any]] = []
    for hit in result.get("hits") or []:
        rows.append({
            "n": {
                "properties": {
                    "Id": hit.get("file_id"),
                    "Name": hit.get("name"),
                    "Std_code": hit.get("std_code"),
                    "fact_index": json.dumps(hit.get("conditions") or [], ensure_ascii=False),
                },
                "labels": ["File"],
            },
            "score": hit.get("score"),
            "conditions": hit.get("conditions"),
            "content_slots": hit.get("content_slots"),
            "methods": hit.get("methods"),
        })
    note = f"rel_fact_param_match query={query[:80]}"
    return rows, note, {"query": query, "top_k": top_k}


def extract_std_code_from_query(query: str) -> str:
    m = re.search(r"(GB[/／]?T?\s*[\d.]+(?:\.\d+)*\s*[—\-–]?\s*\d{4})|(GJB\s*[\d.]+(?:\.\d+)*)", query or "", re.I)
    return (m.group(0) or "").strip() if m else ""
