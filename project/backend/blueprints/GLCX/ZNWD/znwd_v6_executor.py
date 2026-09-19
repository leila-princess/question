"""ZNWD V6 元操作执行器。

负责把 ``retrieval_meta_ops.META_OPS`` 中的受控步骤转换为参数化
Neo4j 查询或 Chroma 检索，并统一序列化结果。这里不接受任意 Cypher。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from .retrieval_meta_ops import (
    META_OPS,
    NODE_ENTITY_FIELDS,
    NODE_LABELS,
    RELATION_SCHEMA,
    clamp_meta_op_params,
    validate_meta_op,
)
from utils.neo4j_flgl_status import flgl_status_cypher_param

logger = logging.getLogger(__name__)

ZNWD_CHROMA_UI_MAX = 24


def _safe_json(value: Any, limit: int = 4000) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _natural_section_key(value: str) -> Tuple[Any, ...]:
    parts = re.split(r"(\d+)", value or "")
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)


def normalize_section_prefix_param(raw: Any) -> str:
    """规范章节号；仅接受 ``1``、``1.2``、``1.2.3`` 这一类前缀。"""
    text = str(raw or "").strip()
    text = re.sub(r"^(?:第\s*)?", "", text)
    text = re.sub(r"\s*(?:章|节|条)\s*$", "", text)
    text = text.strip(" .。")
    if not text:
        return ""
    match = re.fullmatch(r"\d+(?:\.\d+){0,8}", text)
    return match.group(0) if match else ""


def _extract_section_no_from_chunk(chunk: Dict[str, Any]) -> str:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    for key in ("section", "section_no", "chapter", "heading_no"):
        value = str(metadata.get(key) or "").strip()
        if value:
            normalized = normalize_section_prefix_param(value)
            return normalized or value
    text = f"{chunk.get('title', '')} {chunk.get('content', '')}"
    match = re.search(r"\b(\d+(?:\.\d+){0,6})\b", text)
    return match.group(1) if match else ""


def _extract_minio_path_from_metadata(metadata: Dict[str, Any]) -> str:
    if not isinstance(metadata, dict):
        return ""
    for key in (
        "source_file",
        "minio_path",
        "minIo_id",
        "minio_id",
        "original_file",
        "object_path",
    ):
        value = str(metadata.get(key) or "").strip()
        if value and value.lower() not in ("none", "null"):
            return value
    return ""


def _infer_file_format_from_minio_path(path: str) -> str:
    clean = (path or "").lower().split("?", 1)[0]
    if clean.endswith(".pdf"):
        return "pdf"
    if clean.endswith(".docx"):
        return "docx"
    if clean.endswith(".doc"):
        return "doc"
    return "other"


def normalize_chroma_rows_for_client(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        normalized = dict(row)
        metadata = (
            normalized.get("metadata")
            if isinstance(normalized.get("metadata"), dict)
            else {}
        )
        path = _extract_minio_path_from_metadata(metadata)
        if path:
            normalized["minio_path"] = path
            if not str(normalized.get("source_file") or "").strip():
                normalized["source_file"] = path
        normalized["file_format"] = _infer_file_format_from_minio_path(
            path or str(normalized.get("source_file") or "")
        )
        out.append(normalized)
    return out


def _rows_non_empty(
    neo_rows: List[Dict[str, Any]], chroma_rows: List[Dict[str, Any]]
) -> bool:
    for row in list(neo_rows or []) + list(chroma_rows or []):
        if isinstance(row, dict) and not row.get("error"):
            return True
    return False


def chroma_rows_nonempty(chroma_rows: List[Dict[str, Any]]) -> bool:
    return _rows_non_empty([], chroma_rows)


def _serialize_neo_value(value: Any) -> Any:
    if hasattr(value, "type") and not hasattr(value, "labels"):
        return {
            "type": getattr(value, "type", ""),
            "properties": dict(value.items()) if hasattr(value, "items") else {},
        }
    if hasattr(value, "labels") and hasattr(value, "items"):
        return {
            "labels": list(value.labels),
            "properties": {
                key: item for key, item in value.items() if item is not None
            },
            "__elementId": getattr(value, "element_id", None),
        }
    if isinstance(value, list):
        return [_serialize_neo_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_neo_value(item) for key, item in value.items()}
    return value


def _run_neo_cypher(
    driver: Any, cypher: str, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    if not driver:
        return [{"error": "Neo4j driver not initialized"}]
    out: List[Dict[str, Any]] = []
    try:
        logger.info(
            "[V6][Cypher执行]\n---- Cypher ----\n%s\n---- 参数 ----\n%s",
            (cypher or "").strip(),
            _safe_json(params, 12000),
        )
        with driver.session() as session:
            result = session.run(cypher, **params)
            for record in result:
                row: Dict[str, Any] = {}
                for key in record.keys():
                    row[key] = _serialize_neo_value(record.get(key))
                out.append(row)
    except Exception as exc:
        logger.error("[V6][exec] neo cypher failed: %s", exc)
        return [{"error": f"neo4j query failed: {exc}"}]
    return out


def _valid_identifier(value: str, allowed: List[str]) -> bool:
    return value in allowed


def _valid_field(node_type: str, field: str) -> bool:
    return field in (NODE_ENTITY_FIELDS.get(node_type) or [])


def _execute_neo_step(
    driver: Any, op_code: str, params: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Optional[str], Dict[str, Any]]:
    cfg = META_OPS.get(op_code) or {}
    template = str(cfg.get("cypher_template") or "")

    if op_code == "neo_node_query":
        node_type = str(params.get("node_type") or "Equipment")
        field = str(params.get("field") or "Name")
        if not _valid_identifier(node_type, NODE_LABELS):
            return [{"error": f"invalid node_type: {node_type}"}], None, {}
        if not _valid_field(node_type, field):
            return [{"error": f"invalid field for {node_type}: {field}"}], None, {}
        value = str(params.get("value") or "")
        limit = max(1, min(int(params.get("limit") or 50), 300))
        cypher = (
            f"MATCH (n:{node_type}) "
            f"WHERE toLower(coalesce(n.{field},'')) CONTAINS toLower($value) "
            "RETURN n LIMIT $limit"
        )
        run_params = {"value": value, "limit": limit}
        return _run_neo_cypher(driver, cypher, run_params), cypher, run_params

    if op_code == "neo_list_nodes":
        node_type = str(params.get("node_type") or "Equipment")
        if not _valid_identifier(node_type, NODE_LABELS):
            return [{"error": f"invalid node_type: {node_type}"}], None, {}
        limit = max(1, min(int(params.get("limit") or 200), 500))
        status_filter = int(params.get("status_filter", 1))
        if status_filter:
            status_params = flgl_status_cypher_param()
            cypher = (
                f"MATCH (n:{node_type}) "
                "WHERE n.Status IS NULL OR n.Status IN $flgl_enabled_status "
                "RETURN n "
                "ORDER BY coalesce(toString(n.Name), toString(n.Label), "
                "toString(n.Id), '') LIMIT $limit"
            )
            run_params = {**status_params, "limit": limit}
        else:
            cypher = (
                f"MATCH (n:{node_type}) RETURN n "
                "ORDER BY coalesce(toString(n.Name), toString(n.Label), "
                "toString(n.Id), '') LIMIT $limit"
            )
            run_params = {"limit": limit}
        return _run_neo_cypher(driver, cypher, run_params), cypher, run_params

    if op_code == "neo_relation_query":
        from_type = str(params.get("from_type") or "")
        rel_type = str(params.get("rel_type") or "")
        to_type = str(params.get("to_type") or "")
        field = str(params.get("from_field") or "Name")
        if from_type not in NODE_LABELS or to_type not in NODE_LABELS:
            return [{"error": "invalid relation node type"}], None, {}
        if (from_type, to_type) not in (RELATION_SCHEMA.get(rel_type) or []):
            return [
                {"error": f"illegal relation triple: {from_type}-[{rel_type}]->{to_type}"}
            ], None, {}
        side = str(params.get("where_side") or "from").lower()
        side = side if side in ("from", "to") else "from"
        side_type = from_type if side == "from" else to_type
        if not _valid_field(side_type, field):
            return [{"error": f"invalid field for {side_type}: {field}"}], None, {}
        mode = str(params.get("match_mode") or "fuzzy").lower()
        alias = "n" if side == "from" else "m"
        limit = max(1, min(int(params.get("limit") or 30), 500))
        run_params: Dict[str, Any] = {"limit": limit}
        if mode in ("in", "fuzzy_any"):
            values = params.get("value_list")
            if not isinstance(values, list):
                values = [params.get("value")] if params.get("value") else []
            values = [str(value) for value in values if str(value or "").strip()]
            if not values:
                return [], None, {"skipped": True, "reason": "empty_value_list"}
            run_params["value_list"] = values
            if mode == "in":
                where_clause = (
                    f"toString(coalesce({alias}.{field},'')) IN $value_list"
                )
            else:
                where_clause = (
                    f"ANY(v IN $value_list WHERE "
                    f"toLower(coalesce({alias}.{field},'')) CONTAINS toLower(v))"
                )
        else:
            run_params["value"] = str(params.get("value") or "")
            where_clause = (
                f"toLower(coalesce({alias}.{field},'')) CONTAINS toLower($value)"
            )
        cypher = template.format(
            node1=from_type,
            rel=rel_type,
            node2=to_type,
            where_clause=where_clause,
        )
        return _run_neo_cypher(driver, cypher, run_params), cypher, run_params

    if template:
        run_params = dict(params)
        return (
            _run_neo_cypher(driver, template, run_params),
            template,
            run_params,
        )
    return [{"error": f"unsupported neo op: {op_code}"}], None, {}


def _chroma_onnx_fallback_query(
    collection: Any, query: str, top_k: int
) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    try:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        embedding_fn = ONNXMiniLM_L6_V2()
        query_embedding = embedding_fn([query])[0]
        result = collection.query(
            query_embeddings=[query_embedding], n_results=top_k
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for index, document in enumerate(documents):
            metadata = (
                metadatas[index]
                if index < len(metadatas) and isinstance(metadatas[index], dict)
                else {}
            )
            chunks.append(
                {
                    "doc_id": metadata.get("doc_id", ""),
                    "title": metadata.get("title", ""),
                    "type": metadata.get("type", ""),
                    "source_file": metadata.get("source_file", ""),
                    "content": document or "",
                    "distance": (
                        distances[index] if index < len(distances) else None
                    ),
                    "metadata": dict(metadata),
                }
            )
    except Exception as exc:
        logger.error("[V6][exec][Chroma] ONNX fallback failed: %s", exc)
        return [{"error": f"Knowledge base search failed: {exc}"}]
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
    top_k = max(1, min(int(top_k), 200))
    where = {"doc_id": doc_id} if doc_id else None
    try:
        result = collection.query(
            query_texts=[query], n_results=top_k, where=where
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        chunks: List[Dict[str, Any]] = []
        for index, document in enumerate(documents):
            metadata = (
                metadatas[index]
                if index < len(metadatas) and isinstance(metadatas[index], dict)
                else {}
            )
            if title_keyword and title_keyword not in str(
                metadata.get("title") or ""
            ):
                continue
            chunks.append(
                {
                    "doc_id": metadata.get("doc_id", ""),
                    "title": metadata.get("title", ""),
                    "content": document or "",
                    "distance": (
                        distances[index] if index < len(distances) else None
                    ),
                    "metadata": metadata,
                }
            )
        return chunks
    except Exception:
        chunks = _chroma_onnx_fallback_query(collection, query, top_k)
        return [
            row
            for row in chunks
            if (
                not doc_id or str(row.get("doc_id") or "") == doc_id
            )
            and (
                not title_keyword
                or title_keyword in str(row.get("title") or "")
            )
        ]


def _score_filter(
    chunks: List[Dict[str, Any]],
    min_score: float = 0.0,
    max_distance: float = 2.0,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for chunk in chunks:
        distance = chunk.get("distance")
        score = chunk.get("score")
        if isinstance(score, (int, float)) and float(score) < float(min_score):
            continue
        if (
            isinstance(distance, (int, float))
            and float(distance) > float(max_distance)
        ):
            continue
        out.append(chunk)
    return out


def _simple_rerank(
    chunks: List[Dict[str, Any]], query: str, top_k: int = 12
) -> List[Dict[str, Any]]:
    terms = [
        part for part in re.split(r"[\s,，。；;]+", query or "") if part
    ]
    scored: List[Dict[str, Any]] = []
    for chunk in chunks:
        text = f"{chunk.get('title', '')} {chunk.get('content', '')}"
        hits = sum(text.count(term) for term in terms)
        distance = chunk.get("distance")
        penalty = float(distance) if isinstance(distance, (int, float)) else 0.0
        normalized = dict(chunk)
        normalized["score"] = hits * 2.0 - penalty
        scored.append(normalized)
    scored.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return scored[: max(1, min(int(top_k), 200))]


def _section_expand(
    chunks: List[Dict[str, Any]],
    section_prefix: str,
    doc_id: str = "",
    title_keyword: str = "",
    max_chunks: int = 200,
) -> List[Dict[str, Any]]:
    prefix = normalize_section_prefix_param(section_prefix)
    if not prefix:
        return list(chunks)
    out: List[Dict[str, Any]] = []
    for chunk in chunks:
        if doc_id and str(chunk.get("doc_id") or "") != doc_id:
            continue
        if title_keyword and title_keyword not in str(chunk.get("title") or ""):
            continue
        section_no = _extract_section_no_from_chunk(chunk)
        if section_no == prefix or section_no.startswith(prefix + "."):
            normalized = dict(chunk)
            normalized["section_no"] = section_no
            out.append(normalized)
    out.sort(key=lambda item: _natural_section_key(str(item.get("section_no") or "")))
    if not out and chunks:
        return list(chunks)[:max_chunks]
    return out[:max_chunks]


def _node_properties(row: Dict[str, Any], target: str) -> Dict[str, Any]:
    value = row.get(target)
    if not isinstance(value, dict):
        return {}
    properties = value.get("properties")
    return properties if isinstance(properties, dict) else {}


def _resolve_step_reference(
    reference: Dict[str, Any],
    step_outputs: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    step_id = str(reference.get("step_id") or "").strip()
    target = str(reference.get("target") or "n").strip()
    field = str(reference.get("field") or "Id").strip()
    values: List[str] = []
    seen = set()
    for row in step_outputs.get(step_id, []):
        if not isinstance(row, dict) or row.get("error"):
            continue
        props = _node_properties(row, target)
        value = props.get(field)
        if value is None and field in row:
            value = row.get(field)
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            values.append(text)
    return values


def _property_filter(
    rows: List[Dict[str, Any]], target: str, field: str, value: str
) -> List[Dict[str, Any]]:
    needle = str(value or "").lower()
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if target == "r":
            rel = row.get("r")
            props = (
                rel.get("properties")
                if isinstance(rel, dict)
                and isinstance(rel.get("properties"), dict)
                else {}
            )
        else:
            props = _node_properties(row, target)
        if needle in str(props.get(field) or "").lower():
            out.append(row)
    return out


def execute_meta_steps(
    driver: Any,
    collection: Any,
    steps: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """顺序执行白名单步骤，返回 ``neo_rows, chroma_rows, step_results``。"""
    neo_rows: List[Dict[str, Any]] = []
    chroma_rows: List[Dict[str, Any]] = []
    step_results: List[Dict[str, Any]] = []
    step_outputs: Dict[str, List[Dict[str, Any]]] = {}

    for index, raw_step in enumerate((steps or [])[:16]):
        if not isinstance(raw_step, dict):
            continue
        step_id = str(raw_step.get("step_id") or f"s{index + 1}")
        op_code = str(raw_step.get("op_code") or "").strip()
        params = (
            dict(raw_step.get("params") or {})
            if isinstance(raw_step.get("params"), dict)
            else {}
        )
        params = clamp_meta_op_params(op_code, params)

        reference = params.get("from_values_ref")
        if isinstance(reference, dict):
            params["value_list"] = _resolve_step_reference(
                reference, step_outputs
            )
            params["match_mode"] = "in"

        errors = validate_meta_op(op_code, params)
        if errors:
            rows = [{"error": "; ".join(errors)}]
            step_outputs[step_id] = rows
            step_results.append(
                {
                    "step_id": step_id,
                    "op_code": op_code,
                    "rows": rows,
                    "validation_errors": errors,
                    "cypher": None,
                }
            )
            continue

        kind = str((META_OPS.get(op_code) or {}).get("kind") or "")
        if kind == "neo4j":
            if op_code == "neo_property_filter":
                target = str(params.get("target") or "m")
                source = neo_rows
                rows = _property_filter(
                    source,
                    target,
                    str(params.get("field") or ""),
                    str(params.get("value") or ""),
                )
                neo_rows = rows
                cypher = None
                cypher_params: Dict[str, Any] = {}
            else:
                rows, cypher, cypher_params = _execute_neo_step(
                    driver, op_code, params
                )
                neo_rows.extend(rows)
            step_outputs[step_id] = rows
            step_results.append(
                {
                    "step_id": step_id,
                    "op_code": op_code,
                    "rows": rows,
                    "cypher": cypher,
                    "cypher_params": cypher_params,
                }
            )
            continue

        if op_code == "chroma_vector_search":
            chroma_rows = _chroma_vector_query(
                collection,
                str(params.get("query") or ""),
                int(params.get("top_k") or 12),
                str(params.get("doc_id") or ""),
                str(params.get("title_keyword") or ""),
            )
        elif op_code == "chroma_score_filter":
            chroma_rows = _score_filter(
                chroma_rows,
                float(params.get("min_score") or 0.0),
                float(params.get("max_distance") or 2.0),
            )
        elif op_code == "chroma_rerank":
            chroma_rows = _simple_rerank(
                chroma_rows,
                str(params.get("query") or ""),
                int(params.get("top_k") or 12),
            )
        elif op_code == "chroma_section_expand":
            chroma_rows = _section_expand(
                chroma_rows,
                str(params.get("section_prefix") or ""),
                str(params.get("doc_id") or ""),
                str(params.get("title_keyword") or ""),
                int(params.get("max_chunks") or 200),
            )
        else:
            chroma_rows = [{"error": f"unsupported op: {op_code}"}]

        step_outputs[step_id] = chroma_rows
        step_results.append(
            {
                "step_id": step_id,
                "op_code": op_code,
                "rows": chroma_rows,
                "cypher": None,
                "cypher_note": "Chroma 向量检索/处理（无 Cypher）",
            }
        )

    return neo_rows, chroma_rows, step_results


def _neo_node_labels(node: Any) -> List[str]:
    if not isinstance(node, dict):
        return []
    labels = node.get("labels") or []
    return [labels] if isinstance(labels, str) else list(labels)


def _neo_prop_id(properties: Dict[str, Any]) -> str:
    return str(properties.get("Id") or properties.get("id") or "").strip()


def rerank_neo_by_interest(
    neo_rows: List[Dict[str, Any]],
    user_interest: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not neo_rows or not user_interest:
        return neo_rows
    legacy = [
        str(item).strip()
        for item in (user_interest.get("equipment_ids") or [])
        if str(item).strip()
    ]
    type_ids = {
        str(item).strip()
        for item in (user_interest.get("equipment_type_node_ids") or [])
        if str(item).strip()
    }
    equipment_ids = {
        str(item).strip()
        for item in (user_interest.get("equipment_instance_ids") or [])
        if str(item).strip()
    }
    type_labels = {
        str(item).strip()
        for item in (user_interest.get("primary_equipment_type_labels") or [])
        if str(item).strip()
    }
    environment_ids = {
        str(item).strip()
        for item in (user_interest.get("environment_ids") or [])
        if str(item).strip()
    }
    for item in legacy:
        if item.startswith("ZB_"):
            equipment_ids.add(item)
        elif item.startswith("ZBLX_"):
            type_ids.add(item)
        else:
            type_labels.add(item)

    out: List[Dict[str, Any]] = []
    for row in neo_rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        normalized = dict(row)
        score = 0
        for key in ("n", "m"):
            node = normalized.get(key)
            if not isinstance(node, dict):
                continue
            labels = _neo_node_labels(node)
            properties = (
                node.get("properties")
                if isinstance(node.get("properties"), dict)
                else {}
            )
            node_id = _neo_prop_id(properties)
            equipment_type_id = str(
                properties.get("EquipmentTypeId")
                or properties.get("equipmentTypeId")
                or ""
            ).strip()
            type_value = str(
                properties.get("Type") or properties.get("type") or ""
            ).strip()
            if "Equipment" in labels:
                if node_id in equipment_ids:
                    score += 12
                if equipment_type_id in type_ids:
                    score += 10
            if "EquipmentType" in labels:
                if node_id in type_ids:
                    score += 10
                if type_value in type_labels:
                    score += 8
            if "Environment" in labels and node_id in environment_ids:
                score += 5
        normalized["interest_score"] = score
        out.append(normalized)
    out.sort(
        key=lambda item: (
            item.get("interest_score", 0) if isinstance(item, dict) else 0
        ),
        reverse=True,
    )
    return out


def run_neo_cypher(
    driver: Any, cypher: str, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    return _run_neo_cypher(driver, cypher, params)


def run_chroma_vector_search(
    collection: Any,
    query: str,
    top_k: int = 12,
    doc_id: str = "",
    title_keyword: str = "",
) -> List[Dict[str, Any]]:
    return _chroma_vector_query(
        collection, query, top_k, doc_id, title_keyword
    )


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
