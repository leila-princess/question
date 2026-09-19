"""
ZNWD5 图实体语义 probe：Zvec 向量 + FTS，输出格式与 ZNWD4 probe_snapshot 兼容。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .embedding_service import encode_query
from .zvec_index_manager import get_zvec_manager

logger = logging.getLogger(__name__)

_SEARCH_ORDER = (
    "TestProject",
    "Environment",
    "Equipment",
    "Indicator",
    "TestMethod",
    "EquipmentType",
)

_LABEL_ZH = {
    "Equipment": "设备",
    "EquipmentType": "设备类型",
    "Environment": "试验环境",
    "TestProject": "测试项目",
    "Indicator": "指标",
    "TestMethod": "测试方法",
    "File": "文件",
}

_ENUM_QUESTION_RE = re.compile(
    r"有哪些|包括哪些|列举|都有什么|有什么试验项目|有什么项目|有包括哪些|装备类别有哪些|试验项目有哪些"
)


def _normalize_name(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _names_overlap(a: str, b: str) -> bool:
    na, nb = _normalize_name(a), _normalize_name(b)
    if not na or not nb:
        return False
    return na in nb or nb in na


def zvec_semantic_probe(
    keyword: str,
    *,
    label_hints: Optional[List[str]] = None,
    question: str = "",
    topk: int = 12,
) -> List[Dict[str, Any]]:
    kw = _normalize_name(keyword)
    if not kw:
        return []
    hints = [x for x in (label_hints or []) if x in _SEARCH_ORDER]
    node_types = hints if hints else list(_SEARCH_ORDER)

    mgr = get_zvec_manager()
    if mgr.graph_count() == 0:
        logger.warning("[ZNWD5][zvec_probe] graph_entities 索引为空，跳过语义 probe")
        return []

    q_vec = encode_query(question or kw)
    hits = mgr.search_graph_entities(
        q_vec,
        topk=topk,
        node_types=node_types,
        fts_text=kw if len(kw) >= 2 else None,
    )
    if not hits:
        hits = mgr.search_graph_fts(kw, topk=topk, node_types=node_types)

    rows: List[Dict[str, Any]] = []
    for h in hits:
        fields = h.get("fields") or {}
        nt = str(fields.get("node_type") or "")
        rows.append(
            {
                "node_type": nt,
                "id": str(fields.get("neo4j_id") or ""),
                "name": str(fields.get("name") or ""),
                "properties": dict(fields),
                "zvec_score": float(h.get("score") or 0.0),
            }
        )
    return rows


def merge_zvec_into_probe_result(
    base: Dict[str, Any],
    zvec_rows: List[Dict[str, Any]],
    *,
    keyword: str,
    question: str = "",
) -> Dict[str, Any]:
    """将 Zvec 命中合并进 ZNWD4 probe 结果，语义分优先。"""
    from blueprints.GLCX.ZNWD4.keyword_probe import _score_match

    kw = _normalize_name(keyword)
    q = (question or "").strip()
    is_enum = bool(_ENUM_QUESTION_RE.search(q or kw))
    hits: List[Dict[str, Any]] = list(base.get("hits") or [])
    seen = {(h.get("node_type"), h.get("id"), h.get("name")) for h in hits if isinstance(h, dict)}

    for row in zvec_rows:
        key = (row.get("node_type"), row.get("id"), row.get("name"))
        if key in seen:
            continue
        seen.add(key)
        name = row.get("name") or ""
        lexical = _score_match(kw, name, question=question)
        semantic = float(row.get("zvec_score") or 0.0)
        combined = max(lexical, semantic * 0.85 + lexical * 0.15)
        if _names_overlap(kw, name):
            combined = max(combined, 0.95)
        if is_enum and row.get("node_type") == "Equipment" and not _names_overlap(kw, name):
            combined = min(combined, 0.35)
        hits.append(
            {
                "node_type": row.get("node_type"),
                "id": row.get("id"),
                "name": name,
                "type_value": str((row.get("properties") or {}).get("type") or ""),
                "score": round(combined, 4),
                "source": "zvec",
            }
        )

    hits.sort(key=lambda h: h.get("score", 0), reverse=True)
    best = hits[0] if hits else base.get("best")
    if hits and (not best or hits[0].get("score", 0) >= (best or {}).get("score", 0)):
        best = hits[0]

    out = dict(base)
    out["hits"] = hits[:12]
    out["best"] = best
    out["match_count"] = len(hits)
    out["node_type_zh"] = _LABEL_ZH.get(str((best or {}).get("node_type") or ""), "")
    out["zvec_used"] = bool(zvec_rows)
    return out
