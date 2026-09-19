"""RelFactRAG：关系属性 + File.fact_index 参数匹配检索。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from ..extractors.param_extractor import extract_conditions_from_text

TEMP_Q_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*[°℃]")
TIME_Q_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(h|小时|min|分钟)")
STD_Q_RE = re.compile(r"(GB[/／]?T?\s*[\d.]+(?:\.\d+)*-?\d*)|(GJB\s*[\d.]+)", re.I)
CLAUSE_Q_RE = re.compile(r"(\d+(?:\.\d+)+)")


def _parse_query(q: str) -> Dict[str, Any]:
    temps = TEMP_Q_RE.findall(q or "")
    times = TIME_Q_RE.findall(q or "")
    stds = STD_Q_RE.findall(q or "")
    std_codes = [s for pair in stds for s in pair if s]
    clauses = CLAUSE_Q_RE.findall(q or "")
    return {
        "temperatures": temps,
        "durations": times,
        "std_codes": std_codes,
        "clauses": clauses,
        "parsed_conditions": extract_conditions_from_text(q),
    }


def _parse_json_field(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def _pm_score(query_conds: List[Dict], target_conds: List[Dict]) -> float:
    if not query_conds or not target_conds:
        return 0.0
    best = 0.0
    for qc in query_conds:
        for tc in target_conds:
            if qc.get("type") != tc.get("type"):
                continue
            if str(qc.get("value")) == str(tc.get("value")):
                best = max(best, 1.0)
            elif qc.get("type") == "temperature" and str(qc.get("value")) in str(tc.get("value", "")):
                best = max(best, 0.8)
    return best


def retrieve(
    query: str,
    *,
    top_k: int = 5,
    driver: Any = None,
) -> Dict[str, Any]:
    parsed = _parse_query(query)
    hits: List[Dict[str, Any]] = []

    if driver is None:
        return {"query": query, "parsed": parsed, "hits": [], "error": "no driver"}

    cypher = """
    MATCH (f:File)
    WHERE f.fact_index IS NOT NULL AND f.extract_status = 'done'
    RETURN f.Id AS file_id, f.Name AS name, f.Std_code AS std_code,
           f.fact_index AS fact_index, f.content_slots AS content_slots,
           f.File_kind AS file_kind
    LIMIT 200
    """
    with driver.session() as session:
        rows = list(session.run(cypher))

        for r in rows:
            fact_index = _parse_json_field(r.get("fact_index")) or {}
            conditions = fact_index.get("conditions") or []
            score = _pm_score(parsed.get("parsed_conditions") or [], conditions)
            std_code = str(r.get("std_code") or "")
            for sc in parsed.get("std_codes") or []:
                if sc.replace(" ", "").upper() in std_code.replace(" ", "").upper():
                    score = max(score, 0.5)
            if score <= 0 and not parsed.get("std_codes"):
                continue
            hits.append({
                "file_id": r.get("file_id"),
                "name": r.get("name"),
                "std_code": std_code,
                "score": score,
                "conditions": conditions,
                "content_slots": _parse_json_field(r.get("content_slots")),
            })

        hits.sort(key=lambda x: x.get("score", 0), reverse=True)
        hits = hits[:top_k]

        for h in hits:
            fid = h.get("file_id")
            rel_rows = session.run(
                """
                MATCH (tm:TestMethod)-[d:DERIVED_FROM]->(f:File {Id: $fid})
                RETURN tm.Id AS method_id, tm.Name AS method_name, d AS rel
                LIMIT 5
                """,
                fid=fid,
            )
            h["methods"] = [dict(x) for x in rel_rows]

    return {"query": query, "parsed": parsed, "hits": hits}
