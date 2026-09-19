"""ZNWD6 延迟关联：图 hop 结果 → Zvec 文档检索。"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_NAME_KEYS = ("Name", "name", "title", "method_name", "project_name", "standard_name")


def _row_names(row: Any) -> List[str]:
    if not isinstance(row, dict):
        return []
    out: List[str] = []
    props = row.get("properties") if isinstance(row.get("properties"), dict) else row
    if not isinstance(props, dict):
        props = row
    for key in _NAME_KEYS:
        v = str(props.get(key) or row.get(key) or "").strip()
        if v and len(v) >= 2 and v not in out:
            out.append(v)
    return out


def build_doc_query(question: str, neo_rows: List[Any], *, max_hints: int = 8) -> str:
    q = (question or "").strip()
    hints: List[str] = []
    for row in neo_rows or []:
        for name in _row_names(row):
            if name not in hints:
                hints.append(name)
            if len(hints) >= max_hints:
                break
        if len(hints) >= max_hints:
            break
    if not hints:
        return q
    return f"{q} {' '.join(hints)}"


def enrich_doc_rows(
    question: str,
    neo_rows: List[Any],
    chroma_collection: Any = None,
    *,
    top_k: int = 12,
) -> List[Dict[str, Any]]:
    """结合图检索实体名做 Zvec hybrid 文档检索，返回 chroma 兼容行。"""
    query = build_doc_query(question, neo_rows)
    if not query.strip():
        return []
    try:
        from blueprints.GLCX.ZNWD5.zvec_doc_retriever import (
            extract_doc_query_hints,
            zvec_doc_hybrid_search,
            zvec_doc_rerank,
        )

        hints = extract_doc_query_hints(question)
        chunks = zvec_doc_hybrid_search(
            query,
            top_k=top_k,
            chroma_collection=chroma_collection,
            title_keyword=str(hints.get("title_keyword") or ""),
            std_code=str(hints.get("std_code") or ""),
            doc_id=str(hints.get("doc_id") or ""),
        )
        if not chunks or (len(chunks) == 1 and chunks[0].get("error")):
            return []
        ranked = zvec_doc_rerank(
            chunks,
            query,
            top_k=min(8, top_k),
            section_hint=str(hints.get("section_hint") or ""),
            overview=bool(hints.get("overview")),
        )
        for c in ranked:
            c.setdefault("source", "znwd6_deferred_doc")
        logger.info("[ZNWD6][deferred_doc] query=%r hits=%d", query[:80], len(ranked))
        return ranked
    except Exception as exc:
        logger.warning("[ZNWD6][deferred_doc] failed: %s", exc)
        return []


def should_run_deferred_doc(plan: Dict[str, Any], chroma_rows: List[Any]) -> bool:
    if str(plan.get("source") or "") != "znwd6":
        return False
    if not plan.get("znwd6_deferred_doc"):
        return False
    if chroma_rows:
        nonempty = [c for c in chroma_rows if isinstance(c, dict) and not c.get("error")]
        if nonempty:
            return False
    return True
