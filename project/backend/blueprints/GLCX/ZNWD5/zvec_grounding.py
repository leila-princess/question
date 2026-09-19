"""
ZNWD5 Zvec Grounding：问句实体 TopK 结构化候选（不由 LLM 猜实体）。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from blueprints.GLCX.ZNWD4.keyword_probe import extract_focus_candidates

from .zvec_entity_probe import zvec_semantic_probe

logger = logging.getLogger(__name__)

_DEFAULT_TOPK = 3


def grounding_topk() -> int:
    raw = (os.environ.get("ZNWD5_GROUNDING_TOPK") or str(_DEFAULT_TOPK)).strip()
    try:
        return max(1, min(12, int(raw)))
    except ValueError:
        return _DEFAULT_TOPK


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _classify_label(name: str, node_type: str) -> str:
    nt = (node_type or "").strip()
    if nt == "EquipmentType":
        return "equipment_type"
    if nt == "Equipment":
        return "equipment"
    if nt == "Environment":
        return "environment"
    if nt == "TestProject":
        return "test_project"
    if nt == "Indicator":
        return "indicator"
    if nt == "TestMethod":
        return "test_method"
    if nt == "File":
        return "file"
    return "other"


def _row_to_candidate(
    *,
    keyword: str,
    name: str,
    node_type: str,
    entity_id: str,
    score: float,
    source: str,
    properties: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "keyword": keyword,
        "node_type": node_type,
        "name": name,
        "id": entity_id,
        "score": round(float(score or 0.0), 4),
        "source": source,
        "label": _classify_label(name, node_type),
        "properties": dict(properties or {}),
    }


def _dedupe_candidates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        key = (r.get("node_type"), r.get("id"), r.get("name"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out


def ground_question(
    question: str,
    *,
    driver: Any = None,
    keyword: str = "",
    label_hints: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    对问句（及可选 keyword）做 Zvec + FTS grounding，返回 TopK 候选与 focus 切片。
    """
    q = (question or "").strip()
    kw = _normalize(keyword or q)
    topk = grounding_topk()

    candidates: List[Dict[str, Any]] = []

    focus_slices = extract_focus_candidates(q) if q else []
    probe_keywords = [kw] if kw else []
    for fs in focus_slices[:4]:
        if isinstance(fs, dict):
            fk = _normalize(str(fs.get("keyword") or ""))
        else:
            fk = _normalize(str(fs or ""))
        if fk and fk not in probe_keywords:
            probe_keywords.append(fk)

    for pk in probe_keywords[:6]:
        if not pk:
            continue
        try:
            zvec_rows = zvec_semantic_probe(
                pk,
                label_hints=label_hints,
                question=q or pk,
                topk=max(topk * 2, 8),
            )
            for row in zvec_rows:
                candidates.append(
                    _row_to_candidate(
                        keyword=pk,
                        name=str(row.get("name") or ""),
                        node_type=str(row.get("node_type") or ""),
                        entity_id=str(row.get("id") or ""),
                        score=float(row.get("zvec_score") or 0.0),
                        source="zvec",
                        properties=row.get("properties") if isinstance(row.get("properties"), dict) else {},
                    )
                )
        except Exception as exc:
            logger.warning("[ZNWD5][grounding] zvec probe failed kw=%r: %s", pk[:30], exc)

        if driver is not None:
            try:
                from . import keyword_probe as kp5

                probe = kp5.probe_keyword(
                    driver,
                    pk,
                    label_hints=label_hints,
                    question=q,
                )
                for hit in (probe.get("hits") or [])[:topk]:
                    if not isinstance(hit, dict):
                        continue
                    candidates.append(
                        _row_to_candidate(
                            keyword=pk,
                            name=str(hit.get("name") or ""),
                            node_type=str(hit.get("node_type") or ""),
                            entity_id=str(hit.get("id") or ""),
                            score=float(hit.get("score") or 0.0),
                            source=str(hit.get("source") or "contains"),
                        )
                    )
            except Exception as exc:
                logger.debug("[ZNWD5][grounding] contains probe skipped: %s", exc)

    top = _dedupe_candidates(candidates)[:topk]
    best = top[0] if top else {}
    zvec_used = any(str(c.get("source") or "") == "zvec" for c in top)

    return {
        "question": q,
        "keyword": kw,
        "topk": topk,
        "candidates": top,
        "best": best,
        "focus_slices": focus_slices,
        "zvec_used": zvec_used,
    }


def focus_in_question_or_grounding(focus: str, question: str, candidates: List[Dict[str, Any]]) -> bool:
    """focus 须在问句或 TopK 名称中出现（允许子串/归一化）。"""
    f = _normalize(focus)
    if not f:
        return True
    q = _normalize(question)
    if f in q:
        return True
    for c in candidates or []:
        nm = _normalize(str(c.get("name") or ""))
        if not nm:
            continue
        if f in nm or nm in f:
            return True
    return False


def best_focus_for_step(
    focus: str,
    *,
    purpose_node: str,
    question: str,
    candidates: List[Dict[str, Any]],
) -> str:
    """focus 不合法时降级为 Top1 或同 node_type Top1。"""
    if focus_in_question_or_grounding(focus, question, candidates):
        return focus
    if not candidates:
        return focus
    pn = (purpose_node or "").strip()
    typed = [c for c in candidates if str(c.get("node_type") or "") == pn]
    pick = typed[0] if typed else candidates[0]
    return str(pick.get("name") or focus)


__all__ = [
    "best_focus_for_step",
    "focus_in_question_or_grounding",
    "ground_question",
    "grounding_topk",
]
