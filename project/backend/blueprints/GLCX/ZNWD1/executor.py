"""
ZNWD V6：元操作步骤执行（Neo4j + Chroma），与 v4/v5 解耦，便于后续删除旧版代码。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from utils.neo4j_flgl_status import flgl_status_cypher_param

from .meta_ops import META_OPS, NODE_LABELS

logger = logging.getLogger(__name__)

ZNWD_CHROMA_UI_MAX = 3
_LOG_JSON_MAX = 8000


def _safe_json(obj: Any, limit: int = _LOG_JSON_MAX) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) > limit:
        return s[:limit] + f"...(truncated, total_len={len(s)})"
    return s


def _neo_row_one_line(row: Dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return str(row)[:200]
    if row.get("error"):
        return f"error={row.get('error')}"
    parts: List[str] = []
    for key in ("n", "m", "r"):
        if key not in row:
            continue
        v = row[key]
        if isinstance(v, dict):
            if "labels" in v:
                lab = (v.get("labels") or ["?"])[0]
                p = v.get("properties") or {}
                nm = str(p.get("Name") or p.get("Label") or p.get("Id") or "")[:60]
                parts.append(f"{key}:{lab}({nm})")
            elif v.get("type"):
                parts.append(f"{key}:REL[{v.get('type')}]")
        else:
            parts.append(f"{key}:{str(v)[:40]}")
    return " | ".join(parts) if parts else str(row)[:200]


def format_neo_preview_lines(rows: List[Dict[str, Any]], *, max_lines: int = 12) -> List[str]:
    """供思考区流式展示：每行一条图谱命中摘要。"""
    if not rows:
        return []
    out: List[str] = []
    for i, r in enumerate(rows):
        if i >= max_lines:
            out.append(f"…共 {len(rows)} 条")
            break
        if isinstance(r, dict) and r.get("error"):
            out.append(f"- 错误: {r.get('error')}")
            continue
        out.append(f"- {_neo_row_one_line(r)}")
    return out


def format_chroma_preview_lines(rows: List[Dict[str, Any]], *, max_lines: int = 6) -> List[str]:
    if not rows:
        return []
    out: List[str] = []
    for i, c in enumerate(rows):
        if i >= max_lines:
            out.append(f"…共 {len(rows)} 条文档片段")
            break
        if not isinstance(c, dict):
            continue
        tit = str(c.get("title") or c.get("doc_id") or "文档")[:48]
        sec = str(c.get("section") or "")[:24]
        out.append(f"- {tit}" + (f" ({sec})" if sec else ""))
    return out


def _summarize_neo_rows(rows: List[Dict[str, Any]], max_lines: int = 25) -> str:
    if not rows:
        return "  (无)"
    lines = []
    for i, r in enumerate(rows):
        if i >= max_lines:
            lines.append(f"  ... 共 {len(rows)} 行，仅展示前 {max_lines} 行")
            break
        lines.append(f"  [{i}] {_neo_row_one_line(r)}")
    return "\n".join(lines)


def _summarize_chroma_rows(rows: List[Dict[str, Any]], max_lines: int = 15) -> str:
    if not rows:
        return "  (无)"
    lines = []
    for i, c in enumerate(rows):
        if i >= max_lines:
            lines.append(f"  ... 共 {len(rows)} 条，仅展示前 {max_lines} 条")
            break
        if not isinstance(c, dict):
            lines.append(f"  [{i}] <非dict>")
            continue
        if c.get("error"):
            lines.append(f"  [{i}] error={c.get('error')}")
            continue
        did = str(c.get("doc_id") or "")[:36]
        tit = str(c.get("title") or "")[:50]
        sec = str(c.get("section_no") or "")
        clen = len(str(c.get("content") or ""))
        dist = c.get("distance")
        sc = c.get("score")
        lines.append(f"  [{i}] doc_id={did!r} title={tit!r} section={sec!r} len={clen} dist={dist} score={sc}")
    return "\n".join(lines)


def normalize_section_prefix_param(raw: str) -> str:
    s = (raw or "").strip().strip(".")
    if not s:
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)*", s):
        return s
    if re.fullmatch(r"第\s*\d+\s*章", s):
        return ""
    m = re.search(r"第\s*(\d+(?:\.\d+)*)", s)
    if m:
        return m.group(1)
    return ""


def _natural_section_key(sec: str) -> Tuple:
    nums = [int(x) for x in re.findall(r"\d+", sec or "")]
    return tuple(nums) if nums else (10**9,)


def _extract_section_no_from_chunk(c: Dict[str, Any]) -> str:
    md = c.get("metadata") if isinstance(c.get("metadata"), dict) else {}
    for k in ("section", "section_no", "chapter", "heading_no"):
        v = str(md.get(k) or "").strip()
        if v:
            return v
    text = f"{c.get('title','')} {c.get('content','')}"
    m = re.search(r"\b(\d+(?:\.\d+){0,6})\b", text)
    return m.group(1) if m else ""


def _extract_minio_path_from_metadata(md: Dict[str, Any]) -> str:
    if not isinstance(md, dict):
        return ""
    for k in ("source_file", "minio_path", "minIo_id", "minio_id", "original_file", "object_path"):
        v = str(md.get(k) or "").strip()
        if v and v.lower() not in ("none", "null"):
            return v
    return ""


def _infer_file_format_from_minio_path(path: str) -> str:
    p = (path or "").lower().split("?", 1)[0]
    if p.endswith(".pdf"):
        return "pdf"
    if p.endswith(".docx"):
        return "docx"
    if p.endswith(".doc"):
        return "doc"
    return "other"


def normalize_chroma_rows_for_client(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in rows or []:
        if not isinstance(c, dict):
            continue
        n = dict(c)
        md = n.get("metadata") if isinstance(n.get("metadata"), dict) else {}
        path = _extract_minio_path_from_metadata(md)
        if path:
            n["minio_path"] = path
            if not str(n.get("source_file") or "").strip():
                n["source_file"] = path
        n["file_format"] = _infer_file_format_from_minio_path(path or str(n.get("source_file") or ""))
        out.append(n)
    return out


def _rows_non_empty(neo_rows: List[Dict[str, Any]], chroma_rows: List[Dict[str, Any]]) -> bool:
    def ok_block(xs: List[Dict[str, Any]]) -> bool:
        for x in xs or []:
            if not isinstance(x, dict):
                continue
            if x.get("error"):
                continue
            return True
        return False

    return ok_block(neo_rows) or ok_block(chroma_rows)


def chroma_rows_nonempty(chroma_rows: List[Dict[str, Any]]) -> bool:
    return _rows_non_empty([], chroma_rows)


def _run_neo_cypher(driver, cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not driver:
        return [{"error": "Neo4j driver not initialized"}]
    out: List[Dict[str, Any]] = []
    try:
        cy_log = (cypher or "").strip()
        pr_log = json.dumps(params, ensure_ascii=False, default=str)[:12000]
        logger.debug("[ZNWD][CYPHER_EXEC] %s", cy_log.replace("\n", " "))
        logger.debug("[ZNWD][CYPHER_EXEC][PARAMS] %s", pr_log)
        logger.info(
            "[V6][Cypher执行]\n---- Cypher ----\n%s\n---- 参数 ----\n%s",
            cy_log,
            pr_log,
        )
        with driver.session() as session:
            result = session.run(cypher, **params)
            for rec in result:
                row: Dict[str, Any] = {}
                for k in rec.keys():
                    v = rec.get(k)
                    if hasattr(v, "type") and not hasattr(v, "labels"):
                        row[k] = {
                            "type": getattr(v, "type", ""),
                            "properties": dict(v.items()) if hasattr(v, "items") else {},
                        }
                    elif hasattr(v, "labels") and hasattr(v, "items"):
                        row[k] = {
                            "labels": list(v.labels),
                            "properties": {x: y for x, y in v.items() if y is not None},
                            "__elementId": getattr(v, "element_id", None),
                        }
                    else:
                        row[k] = v
                out.append(row)
    except Exception as e:
        logger.error("[V6][exec] neo cypher failed: %s", e)
        return [{"error": f"neo4j query failed: {e}"}]
    return out


def _execute_neo_step(
    driver, op_code: str, params: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Optional[str], Dict[str, Any]]:
    """
    执行一步 Neo 元操作。返回 (行结果, 实际执行的 Cypher 或 None, 绑定参数副本)。
    未执行数据库时（校验失败、跳过）cypher 为 None。
    """
    cfg = META_OPS.get(op_code) or {}
    template = cfg.get("cypher_template", "")
    cypher_for_log = ""
    if op_code == "neo_node_query":
        node_type = str(params.get("node_type") or "Equipment")
        field = str(params.get("field") or "Name")
        value = str(params.get("value") or "")
        limit = int(params.get("limit") or 50)
        where_clause = f"toLower(coalesce(n.{field},'')) CONTAINS toLower($value)"
        cypher = f"MATCH (n:{node_type}) WHERE {where_clause} RETURN n LIMIT $limit"
        cypher_for_log = cypher
        run_p = {"value": value, "limit": limit}
        logger.info(
            "[V6][exec][NEO] op=%s cypher=\n%s\nparams=%s",
            op_code,
            cypher_for_log,
            _safe_json(run_p, 2000),
        )
        return _run_neo_cypher(driver, cypher, run_p), cypher, dict(run_p)
    if op_code == "neo_equipment_under_type":
        kw = str(params.get("keyword") or params.get("value") or "").strip()
        focus_id = str(params.get("focus_id") or "").strip()
        limit = max(1, min(int(params.get("limit") or 500), 500))
        if not kw and not focus_id:
            return [{"error": "neo_equipment_under_type requires keyword or focus_id"}], None, {}
        stp = flgl_status_cypher_param()
        cypher = """
        MATCH (et:EquipmentType)
        WHERE (et.Status IS NULL OR et.Status IN $flgl_enabled_status)
          AND (
            ($focus_id <> '' AND toString(coalesce(et.Id,'')) = $focus_id)
            OR (
              $focus_id = '' AND (
                toLower(coalesce(et.Name,'')) CONTAINS toLower($kw)
                OR toLower(coalesce(et.Type,'')) CONTAINS toLower($kw)
              )
            )
          )
        WITH collect(DISTINCT et) AS seeds
        UNWIND seeds AS s
        WITH seeds, collect(DISTINCT coalesce(s.Type,'')) AS type_vals
        MATCH (et:EquipmentType)
        WHERE (et.Status IS NULL OR et.Status IN $flgl_enabled_status)
          AND (
            et IN seeds
            OR ANY(tv IN type_vals WHERE tv <> '' AND coalesce(et.Type,'') = tv)
          )
        WITH collect(DISTINCT et) AS all_et
        UNWIND all_et AS et
        MATCH (e:Equipment)
        WHERE (e.Status IS NULL OR e.Status IN $flgl_enabled_status)
          AND (
            (et)-[:HAS]->(e)
            OR toString(coalesce(e.EquipmentTypeId,'')) = toString(coalesce(et.Id,''))
          )
        RETURN DISTINCT et AS n, e AS m
        LIMIT $limit
        """
        run_p = {**stp, "kw": kw, "focus_id": focus_id, "limit": limit}
        logger.info("[V6][exec][NEO] op=%s kw=%r focus_id=%r limit=%s", op_code, kw[:40], focus_id, limit)
        return _run_neo_cypher(driver, cypher, run_p), cypher, dict(run_p)
    if op_code == "neo_equipment_indicators":
        kw = str(params.get("keyword") or params.get("value") or "").strip()
        focus_id = str(params.get("focus_id") or "").strip()
        limit = max(1, min(int(params.get("limit") or 200), 500))
        if not kw and not focus_id:
            return [{"error": "neo_equipment_indicators requires keyword or focus_id"}], None, {}
        stp = flgl_status_cypher_param()
        cypher = """
        MATCH (e:Equipment)
        WHERE (e.Status IS NULL OR e.Status IN $flgl_enabled_status)
          AND (
            ($focus_id <> '' AND toString(coalesce(e.Id,'')) = $focus_id)
            OR (
              $focus_id = '' AND toLower(coalesce(e.Name,'')) CONTAINS toLower($kw)
            )
          )
        WITH e
        OPTIONAL MATCH (e)-[:CON]->(i1:Indicator)
        WHERE i1 IS NULL OR i1.Status IS NULL OR i1.Status IN $flgl_enabled_status
        OPTIONAL MATCH (i2:Indicator)
        WHERE (i2.Status IS NULL OR i2.Status IN $flgl_enabled_status)
          AND toLower(coalesce(i2.Equipment_name,'')) CONTAINS toLower(coalesce(e.Name,''))
        WITH e, collect(DISTINCT i1) + collect(DISTINCT i2) AS inds
        UNWIND inds AS i
        WITH e, i
        WHERE i IS NOT NULL
        OPTIONAL MATCH (p:Indicator)-[:HAS]->(i)
        RETURN DISTINCT
          e AS n,
          i{
            .*,
            parent_id: coalesce(p.Id, ''),
            parent_name: coalesce(p.Name, ''),
            hierarchy_type: CASE
              WHEN toLower(coalesce(i.Type, '')) IN ['category', 'test'] THEN toLower(coalesce(i.Type, ''))
              WHEN p IS NULL THEN 'category'
              ELSE 'test'
            END
          } AS m
        LIMIT $limit
        """
        run_p = {**stp, "kw": kw, "focus_id": focus_id, "limit": limit}
        logger.info("[V6][exec][NEO] op=%s kw=%r focus_id=%r limit=%s", op_code, kw[:40], focus_id, limit)
        return _run_neo_cypher(driver, cypher, run_p), cypher, dict(run_p)
    if op_code == "neo_equip_project_by_method":
        from .method_inference import expand_method_standard_keywords

        method_kw = str(params.get("method_keyword") or params.get("keyword") or "").strip()
        method_id = str(params.get("method_id") or "").strip()
        raw_list = params.get("keyword_list") if isinstance(params.get("keyword_list"), list) else []
        kws: List[str] = []
        seen_kw: set = set()
        for src in ([method_kw] if method_kw else []) + [str(x) for x in raw_list if str(x).strip()]:
            for v in expand_method_standard_keywords(src):
                lk = v.lower()
                if lk not in seen_kw:
                    seen_kw.add(lk)
                    kws.append(v)
        limit = max(1, min(int(params.get("limit") or 200), 500))
        if not kws and not method_id:
            return [{"error": "neo_equip_project_by_method requires method_keyword or method_id"}], None, {}
        stp = flgl_status_cypher_param()
        cypher = """
        MATCH (e:Equipment)-[:CON]->(tp:TestProject)-[:CON]->(tm:TestMethod)
        WHERE (e.Status IS NULL OR e.Status IN $flgl_enabled_status)
          AND (tp.Status IS NULL OR tp.Status IN $flgl_enabled_status)
          AND (tm.Status IS NULL OR tm.Status IN $flgl_enabled_status)
          AND (
            ($method_id <> '' AND toString(coalesce(tm.Id,'')) = $method_id)
            OR (
              $method_id = '' AND size($kws) > 0 AND (
                ANY(k IN $kws WHERE toLower(coalesce(tm.Name,'')) CONTAINS toLower(k))
                OR ANY(k IN $kws WHERE toLower(coalesce(tm.Source_name,'')) CONTAINS toLower(k))
              )
            )
          )
        RETURN DISTINCT e AS e, tp AS tp, tm AS tm
        ORDER BY coalesce(toString(e.Name), ''), coalesce(toString(tp.Name), '')
        LIMIT $limit
        """
        run_p = {**stp, "kws": kws, "method_id": method_id, "limit": limit}
        logger.info(
            "[V6][exec][NEO] op=%s method_id=%r kws=%s limit=%s",
            op_code,
            method_id,
            kws[:5],
            limit,
        )
        return _run_neo_cypher(driver, cypher, run_p), cypher, dict(run_p)
    if op_code == "neo_testproject_list_hierarchical":
        limit = int(params.get("limit") or 500)
        limit = max(1, min(limit, 500))
        status_filter = int(params.get("status_filter", 1))
        if status_filter:
            stp = flgl_status_cypher_param()
            cypher = """
            MATCH (tp:TestProject)
            WHERE tp.Status IS NULL OR tp.Status IN $flgl_enabled_status
            OPTIONAL MATCH (parent:TestProject)-[:HAS]->(tp)
            WHERE parent IS NULL OR parent.Status IS NULL OR parent.Status IN $flgl_enabled_status
            RETURN tp AS n, parent
            ORDER BY coalesce(toString(tp.Name), toString(tp.Id), '')
            LIMIT $limit
            """
            run_p = {**stp, "limit": limit}
        else:
            cypher = """
            MATCH (tp:TestProject)
            OPTIONAL MATCH (parent:TestProject)-[:HAS]->(tp)
            RETURN tp AS n, parent
            ORDER BY coalesce(toString(tp.Name), toString(tp.Id), '')
            LIMIT $limit
            """
            run_p = {"limit": limit}
        logger.info(
            "[V6][exec][NEO] op=%s limit=%s status_filter=%s",
            op_code,
            limit,
            status_filter,
        )
        return _run_neo_cypher(driver, cypher, run_p), cypher, dict(run_p)
    if op_code == "neo_list_nodes":
        node_type = str(params.get("node_type") or "Equipment")
        if node_type not in NODE_LABELS:
            return [{"error": f"invalid node_type: {node_type}"}], None, {}
        limit = int(params.get("limit") or 200)
        limit = max(1, min(limit, 500))
        status_filter = int(params.get("status_filter", 1))
        if status_filter:
            stp = flgl_status_cypher_param()
            cypher = (
                f"MATCH (n:{node_type}) "
                "WHERE n.Status IS NULL OR n.Status IN $flgl_enabled_status "
                "RETURN n "
                "ORDER BY coalesce(toString(n.Name), toString(n.Label), toString(n.Id), '') "
                "LIMIT $limit"
            )
            run_p = {**stp, "limit": limit}
        else:
            cypher = (
                f"MATCH (n:{node_type}) RETURN n "
                "ORDER BY coalesce(toString(n.Name), toString(n.Label), toString(n.Id), '') "
                "LIMIT $limit"
            )
            run_p = {"limit": limit}
        logger.info(
            "[V6][exec][NEO] op=%s node_type=%s limit=%s status_filter=%s",
            op_code,
            node_type,
            limit,
            status_filter,
        )
        return _run_neo_cypher(driver, cypher, run_p), cypher, dict(run_p)
    if op_code == "neo_relation_query":
        from_type = str(params.get("from_type") or "")
        rel_type = str(params.get("rel_type") or "")
        to_type = str(params.get("to_type") or "")
        from_field = str(params.get("from_field") or "Name")
        from_field_alt = str(params.get("from_field_alt") or "").strip()
        mode = str(params.get("match_mode") or "fuzzy").lower()
        if mode not in ("in", "fuzzy_any"):
            mode = "fuzzy"
        value = str(params.get("value") or "")
        value_list = params.get("value_list") if isinstance(params.get("value_list"), list) else []
        limit = int(params.get("limit") or 30)
        limit = max(1, min(limit, 500))
        where_side = str(params.get("where_side") or "from").lower()
        if where_side not in ("from", "to"):
            where_side = "from"
        wn = "n" if where_side == "from" else "m"
        if mode == "in":
            where_clause = f"toString(coalesce({wn}.{from_field},'')) IN $value_list"
        elif mode == "fuzzy_any":
            where_clause = (
                f"ANY(v IN $value_list WHERE toLower(coalesce({wn}.{from_field},'')) "
                f"CONTAINS toLower(v))"
            )
        else:
            primary = f"toLower(coalesce({wn}.{from_field},'')) CONTAINS toLower($value)"
            if from_field_alt and re.match(r"^[A-Za-z][A-Za-z0-9_]*$", from_field_alt):
                alt = f"toLower(coalesce({wn}.{from_field_alt},'')) CONTAINS toLower($value)"
                where_clause = f"({primary} OR {alt})"
            else:
                where_clause = primary
        cypher = template.format(node1=from_type, rel=rel_type, node2=to_type, where_clause=where_clause)
        if to_type == "TestProject":
            cypher = (
                f"MATCH (n:{from_type})-[r:{rel_type}]->(m:{to_type}) "
                f"WHERE {where_clause} "
                "OPTIONAL MATCH (parent:TestProject)-[:HAS]->(m) "
                "RETURN n, r, m, parent LIMIT $limit"
            )
        cypher_for_log = cypher
        run_params: Dict[str, Any] = {"limit": limit}
        if mode in ("in", "fuzzy_any"):
            run_params["value_list"] = [
                str(x) for x in (value_list if value_list else [value]) if str(x).strip()
            ]
            if not run_params["value_list"]:
                logger.warning("[V6][exec][NEO] op=%s match_mode=%s 但 value_list 为空，跳过", op_code, mode)
                return [], None, {"skipped": True, "reason": "empty_value_list"}
        else:
            run_params["value"] = value
        logger.info(
            "[V6][exec][NEO] op=%s cypher=\n%s\nparams=%s",
            op_code,
            cypher_for_log,
            _safe_json(run_params, 4000),
        )
        return _run_neo_cypher(driver, cypher, run_params), cypher, dict(run_params)
    if op_code == "neo_relation_lookup":
        cypher = template.format(
            node1=params["node1"],
            rel=params["rel"],
            node2=params["node2"],
            where_clause="toLower(coalesce(n.Name,'')) CONTAINS toLower($value)",
        )
        run_p = {"value": params.get("keyword", ""), "limit": params.get("limit", 30)}
        logger.info("[V6][exec][NEO] op=%s cypher=\n%s", op_code, cypher)
        return _run_neo_cypher(driver, cypher, run_p), cypher, dict(run_p)
    if template:
        logger.info("[V6][exec][NEO] op=%s template 直跑 params=%s", op_code, _safe_json(params, 2000))
        run_p = dict(params)
        return _run_neo_cypher(driver, template, run_p), template, run_p
    return [{"error": f"unsupported neo op: {op_code}"}], None, {}


def _chroma_onnx_fallback_query(collection: Any, query: str, top_k: int) -> List[Dict[str, Any]]:
    """当 collection.query(query_texts=...) 失败时，使用 ONNX 嵌入 + query_embeddings 回退（原 v4 search_knowledge_base 逻辑）。"""
    chunks: List[Dict[str, Any]] = []
    try:
        logger.info("[V6][exec][Chroma] ONNX fallback query=%r top_k=%s", query, top_k)
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        ef = ONNXMiniLM_L6_V2()
        query_embedding = ef([query])[0]
        results = collection.query(query_embeddings=[query_embedding], n_results=top_k)
        if results and results.get("documents") and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                metadata = (
                    results["metadatas"][0][i]
                    if results.get("metadatas") and results["metadatas"][0]
                    else {}
                )
                if not isinstance(metadata, dict):
                    metadata = {}
                chunks.append(
                    {
                        "doc_id": metadata.get("doc_id", ""),
                        "title": metadata.get("title", ""),
                        "type": metadata.get("type", ""),
                        "source_file": metadata.get("source_file", ""),
                        "content": (doc or "")[:500],
                        "distance": None,
                        "metadata": dict(metadata),
                    }
                )
        logger.info("[V6][exec][Chroma] ONNX fallback 返回 %d 条", len(chunks))
    except Exception as e:
        logger.error("[V6][exec][Chroma] ONNX fallback 失败: %s", e)
        return [{"error": f"Knowledge base search failed: {e}"}]
    return chunks


def _chroma_vector_query(
    collection: Any,
    query: str,
    top_k: int = 12,
    doc_id: str = "",
    title_keyword: str = "",
) -> List[Dict[str, Any]]:
    if not collection:
        return [{"error": "Chroma collection not initialized"}]
    chunks: List[Dict[str, Any]] = []
    where = {"doc_id": doc_id} if doc_id else None
    try:
        res = collection.query(query_texts=[query], n_results=max(1, min(top_k, 200)), where=where)
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc in enumerate(docs):
            md = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
            if title_keyword and title_keyword not in str(md.get("title") or ""):
                continue
            chunks.append(
                {
                    "doc_id": md.get("doc_id", ""),
                    "title": md.get("title", ""),
                    "content": doc or "",
                    "distance": dists[i] if i < len(dists) else None,
                    "metadata": md,
                }
            )
        return chunks
    except Exception:
        basic = _chroma_onnx_fallback_query(collection, query, top_k=top_k)
        for b in basic:
            if not isinstance(b, dict) or b.get("error"):
                continue
            if doc_id and str(b.get("doc_id") or "") != doc_id:
                continue
            if title_keyword and title_keyword not in str(b.get("title") or ""):
                continue
            md = b.get("metadata") if isinstance(b.get("metadata"), dict) else {}
            if not md:
                md = {
                    "doc_id": b.get("doc_id", ""),
                    "title": b.get("title", ""),
                    "type": b.get("type", ""),
                    "source_file": b.get("source_file", ""),
                }
            chunks.append({**b, "distance": b.get("distance"), "metadata": md})
        return chunks


def _score_filter(chunks: List[Dict[str, Any]], min_score: float = 0.0, max_distance: float = 2.0) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in chunks:
        d = c.get("distance")
        s = c.get("score")
        if s is not None and isinstance(s, (int, float)) and float(s) < float(min_score):
            continue
        if d is not None and isinstance(d, (int, float)) and float(d) > float(max_distance):
            continue
        out.append(c)
    return out


def _simple_rerank(chunks: List[Dict[str, Any]], query: str, top_k: int = 12) -> List[Dict[str, Any]]:
    terms = [x for x in re.split(r"[\s,，。；;]+", query or "") if x]
    scored = []
    for c in chunks:
        text = f"{c.get('title','')} {c.get('content','')}"
        hit = sum(text.count(t) for t in terms)
        dist = c.get("distance")
        dist_penalty = float(dist) if isinstance(dist, (int, float)) else 0.0
        score = hit * 2.0 - dist_penalty
        n = dict(c)
        n["score"] = score
        scored.append(n)
    scored.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return scored[: max(1, min(top_k, 200))]


def _section_expand(
    chunks: List[Dict[str, Any]],
    section_prefix: str,
    doc_id: str = "",
    title_keyword: str = "",
) -> List[Dict[str, Any]]:
    prefix = normalize_section_prefix_param(section_prefix)
    if not prefix:
        return list(chunks) if chunks else []
    out: List[Dict[str, Any]] = []
    for c in chunks:
        if doc_id and str(c.get("doc_id") or "") != doc_id:
            continue
        if title_keyword and title_keyword not in str(c.get("title") or ""):
            continue
        sec = _extract_section_no_from_chunk(c)
        if not sec:
            continue
        if sec == prefix or sec.startswith(prefix + "."):
            n = dict(c)
            n["section_no"] = sec
            out.append(n)
    out.sort(key=lambda x: _natural_section_key(str(x.get("section_no") or "")))
    if not out and chunks:
        logger.warning(
            "[V6][exec][CHROMA] chroma_section_expand: 前缀 %r 未命中任何带章节号的片段，保留扩展前 %d 条",
            prefix,
            len(chunks),
        )
        return list(chunks)
    return out


def execute_meta_steps(
    driver: Any,
    collection: Any,
    steps: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    neo_rows: List[Dict[str, Any]] = []
    neo_current: List[Dict[str, Any]] = []
    chroma_rows: List[Dict[str, Any]] = []
    step_results: List[Dict[str, Any]] = []
    step_outputs: Dict[str, List[Dict[str, Any]]] = {}

    def _resolve_ref_values(ref_cfg: Dict[str, Any]) -> List[str]:
        sid = str(ref_cfg.get("step_id") or "").strip()
        tgt = str(ref_cfg.get("target") or "n").strip()
        fld = str(ref_cfg.get("field") or "Id").strip()
        rows = step_outputs.get(sid) or []
        vals: List[str] = []
        seen = set()
        for r in rows:
            if not isinstance(r, dict):
                continue
            obj = r.get(tgt)
            if not isinstance(obj, dict):
                continue
            props = obj.get("properties") if isinstance(obj.get("properties"), dict) else {}
            v = str(props.get(fld) or "").strip()
            if v and v not in seen:
                seen.add(v)
                vals.append(v)
        return vals

    for st in steps:
        op_code = st["op_code"]
        params = dict(st["params"])
        if isinstance(params.get("from_values_ref"), dict):
            refd = params["from_values_ref"]
            if op_code == "neo_relation_query" and isinstance(refd, dict) and not str(refd.get("target") or "").strip():
                ft = str(params.get("from_type") or "")
                tt = str(params.get("to_type") or "")
                if ft == "TestProject" and tt == "TestMethod":
                    refd["target"] = "n"
                elif ft in ("Equipment", "Environment") and tt == "TestProject":
                    refd["target"] = "m"
            params["value_list"] = _resolve_ref_values(params["from_values_ref"])
            if params.get("match_mode") != "in":
                params["match_mode"] = "in"
            if op_code == "neo_relation_query" and isinstance(refd, dict):
                rfl = str(refd.get("field") or "Id").strip()
                if rfl:
                    params["from_field"] = rfl
        logger.info(
            "[V6][exec][STEP] id=%s purpose=%r op=%s params=%s",
            st.get("step_id"),
            st.get("purpose", ""),
            op_code,
            _safe_json(params, 4000),
        )
        # 跳过标记为 skip_db 的步骤（仅用于合成 LLM 提示意图，不执行数据库查询）
        if st.get("skip_db"):
            logger.info("[V6][exec][STEP] id=%s op=%s 跳过（skip_db=True）", st.get("step_id"), op_code)
            step_outputs[str(st["step_id"])] = []
            step_results.append(
                {
                    "step_id": st["step_id"],
                    "op_code": op_code,
                    "rows": [],
                    "cypher": None,
                    "note": "skip_db=True，未执行数据库查询，仅标记意图供合成 LLM 使用",
                }
            )
            continue
        if op_code.startswith("neo_") and op_code != "neo_property_filter":
            res, ran_cypher, ran_params = _execute_neo_step(driver, op_code, params)
            if isinstance(res, list):
                neo_current = res
                neo_rows.extend(res)
                step_outputs[str(st["step_id"])] = res
            err_ct = sum(1 for x in (res or []) if isinstance(x, dict) and x.get("error"))
            logger.info(
                "[V6][exec][STEP] id=%s op=%s 完成 neo_rows=%d errors=%d\n摘要:\n%s",
                st.get("step_id"),
                op_code,
                len(res) if isinstance(res, list) else 0,
                err_ct,
                _summarize_neo_rows(res if isinstance(res, list) else [], max_lines=20),
            )
            step_results.append(
                {
                    "step_id": st["step_id"],
                    "op_code": op_code,
                    "rows": res,
                    "cypher": ran_cypher,
                    "cypher_params": ran_params,
                }
            )
            continue
        if op_code == "neo_property_filter":
            target = str(params.get("target") or "m")
            field = str(params.get("field") or "Name")
            value = str(params.get("value") or "")
            match_mode = str(params.get("match_mode") or "contains").lower()
            prev_batch = list(neo_current)
            filtered: List[Dict[str, Any]] = []
            for row in neo_current:
                if not isinstance(row, dict):
                    continue
                obj = row.get(target)
                if not isinstance(obj, dict):
                    continue
                props = obj.get("properties") if isinstance(obj.get("properties"), dict) else {}
                fv = str(props.get(field) or "")
                if match_mode == "exact":
                    ok = fv == value
                else:
                    ok = value.lower() in fv.lower()
                if ok:
                    filtered.append(row)
            neo_current = filtered
            if prev_batch and len(neo_rows) >= len(prev_batch):
                neo_rows = neo_rows[: -len(prev_batch)]
            neo_rows.extend(filtered)
            step_outputs[str(st["step_id"])] = filtered
            logger.info(
                "[V6][exec][STEP] id=%s op=neo_property_filter 完成 过滤后行数=%d\n摘要:\n%s",
                st.get("step_id"),
                len(filtered),
                _summarize_neo_rows(filtered, max_lines=20),
            )
            step_results.append(
                {
                    "step_id": st["step_id"],
                    "op_code": op_code,
                    "rows": filtered,
                    "cypher": None,
                    "cypher_note": "neo_property_filter 仅在上一步 Neo 结果中按字段值过滤，未执行新的 Cypher",
                }
            )
            continue
        if op_code == "chroma_vector_search":
            res = _chroma_vector_query(
                collection,
                query=str(params.get("query") or ""),
                top_k=int(params.get("top_k", 12)),
                doc_id=str(params.get("doc_id") or ""),
                title_keyword=str(params.get("title_keyword") or ""),
            )
            chroma_rows = res
            step_outputs[str(st["step_id"])] = res
            logger.info(
                "[V6][exec][CHROMA] id=%s op=chroma_vector_search 命中=%d\n摘要:\n%s",
                st.get("step_id"),
                len(res) if isinstance(res, list) else 0,
                _summarize_chroma_rows(res if isinstance(res, list) else [], max_lines=12),
            )
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": res})
            continue
        if op_code == "chroma_score_filter":
            chroma_rows = _score_filter(
                chroma_rows,
                min_score=float(params.get("min_score", 0.0)),
                max_distance=float(params.get("max_distance", 2.0)),
            )
            step_outputs[str(st["step_id"])] = chroma_rows
            logger.info(
                "[V6][exec][CHROMA] id=%s op=chroma_score_filter 后条数=%d\n摘要:\n%s",
                st.get("step_id"),
                len(chroma_rows),
                _summarize_chroma_rows(chroma_rows, max_lines=12),
            )
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": chroma_rows})
            continue
        if op_code == "chroma_rerank":
            chroma_rows = _simple_rerank(
                chroma_rows,
                query=str(params.get("query") or ""),
                top_k=int(params.get("top_k", 12)),
            )
            step_outputs[str(st["step_id"])] = chroma_rows
            logger.info(
                "[V6][exec][CHROMA] id=%s op=chroma_rerank 后条数=%d\n摘要:\n%s",
                st.get("step_id"),
                len(chroma_rows),
                _summarize_chroma_rows(chroma_rows, max_lines=12),
            )
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": chroma_rows})
            continue
        if op_code == "chroma_section_expand":
            chroma_rows = _section_expand(
                chroma_rows,
                section_prefix=str(params.get("section_prefix") or ""),
                doc_id=str(params.get("doc_id") or ""),
                title_keyword=str(params.get("title_keyword") or ""),
            )
            step_outputs[str(st["step_id"])] = chroma_rows
            logger.info(
                "[V6][exec][CHROMA] id=%s op=chroma_section_expand 后条数=%d\n摘要:\n%s",
                st.get("step_id"),
                len(chroma_rows),
                _summarize_chroma_rows(chroma_rows, max_lines=20),
            )
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": chroma_rows})
            continue
        step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": [{"error": "unsupported op"}]})
    logger.info(
        "[V6][exec][SUMMARY] 全步执行完毕 neo_total_accum=%d chroma_final=%d\nneo 末段摘要:\n%s\nchroma 末段摘要:\n%s",
        len(neo_rows),
        len(chroma_rows),
        _summarize_neo_rows(neo_rows[-30:] if len(neo_rows) > 30 else neo_rows, max_lines=15),
        _summarize_chroma_rows(chroma_rows, max_lines=15),
    )
    return neo_rows, chroma_rows, step_results


def _neo_node_labels(node: Any) -> List[str]:
    if not isinstance(node, dict):
        return []
    labs = node.get("labels") or []
    if isinstance(labs, str):
        return [labs]
    return list(labs)


def _neo_prop_id(props: Dict[str, Any]) -> str:
    return str(props.get("Id") or props.get("id") or "").strip()


def rerank_neo_by_interest(
    neo_rows: List[Dict[str, Any]],
    user_interest: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not neo_rows:
        return neo_rows
    if not user_interest:
        return neo_rows
    legacy = [str(x).strip() for x in (user_interest.get("equipment_ids") or []) if str(x).strip()]
    et_node_ids = {str(x).strip() for x in (user_interest.get("equipment_type_node_ids") or []) if str(x).strip()}
    equip_inst_ids = {str(x).strip() for x in (user_interest.get("equipment_instance_ids") or []) if str(x).strip()}
    primary_labels = {
        str(x).strip() for x in (user_interest.get("primary_equipment_type_labels") or []) if str(x).strip()
    }
    for x in legacy:
        if x.startswith("ZB_"):
            equip_inst_ids.add(x)
        elif x.startswith("ZBLX_"):
            et_node_ids.add(x)
        else:
            primary_labels.add(x)
    env_ids = {str(x).strip() for x in (user_interest.get("environment_ids") or []) if str(x).strip()}
    if not et_node_ids and not equip_inst_ids and not primary_labels and not env_ids:
        return neo_rows
    out: List[Dict[str, Any]] = []
    for row in neo_rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        r = dict(row)
        score = 0
        for key in ("n", "m"):
            node = r.get(key)
            if not isinstance(node, dict):
                continue
            labs = _neo_node_labels(node)
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            nid = _neo_prop_id(props)
            et_ref = str(props.get("EquipmentTypeId") or props.get("equipmentTypeId") or "").strip()
            type_prop = str(props.get("Type") or props.get("type") or "").strip()
            if "Equipment" in labs:
                if nid and nid in equip_inst_ids:
                    score += 12
                if et_ref and et_ref in et_node_ids:
                    score += 10
            if "EquipmentType" in labs:
                if nid and nid in et_node_ids:
                    score += 10
                if type_prop and type_prop in primary_labels:
                    score += 8
            if "Environment" in labs and nid in env_ids:
                score += 5
        r["interest_score"] = score
        out.append(r)
    out.sort(key=lambda x: x.get("interest_score", 0) if isinstance(x, dict) else 0, reverse=True)
    logger.info(
        "[V6][exec][interest] reranked neo_rows=%d top_score=%s",
        len(out),
        out[0].get("interest_score") if out and isinstance(out[0], dict) else 0,
    )
    return out


def run_neo_cypher(driver: Any, cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """供 V6 显式 TOOL 层直接执行参数化 Cypher（与元操作执行器共用日志与序列化逻辑）。"""
    return _run_neo_cypher(driver, cypher, params)


def run_chroma_vector_search(
    collection: Any,
    query: str,
    top_k: int = 12,
    doc_id: str = "",
    title_keyword: str = "",
) -> List[Dict[str, Any]]:
    """供 V6 显式 TOOL 层调用向量检索（与 chroma_vector_search 元操作一致）。"""
    return _chroma_vector_query(collection, query, top_k, doc_id, title_keyword)


__all__ = (
    "ZNWD_CHROMA_UI_MAX",
    "chroma_rows_nonempty",
    "execute_meta_steps",
    "normalize_chroma_rows_for_client",
    "normalize_section_prefix_param",
    "rerank_neo_by_interest",
    "run_chroma_vector_search",
    "run_neo_cypher",
)
