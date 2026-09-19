"""ZNWD6 高召回实体探测 → TypedKeywordLattice τ。"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from blueprints.GLCX.ZNWD4.keyword_probe import extract_focus_candidates

from ..config import grounding_max_per_span, grounding_score_threshold, use_znwd5_zvec

logger = logging.getLogger(__name__)

_probe_request_cache: Dict[str, List[Dict[str, Any]]] = {}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _probe_span(keyword: str, *, question: str, driver: Any) -> List[Dict[str, Any]]:
    surface = _normalize(keyword)
    if len(surface) < 2:
        return []
    rows: List[Dict[str, Any]] = []
    threshold = grounding_score_threshold()
    max_n = grounding_max_per_span()

    if use_znwd5_zvec():
        try:
            from blueprints.GLCX.ZNWD5.zvec_entity_probe import zvec_semantic_probe

            for hit in zvec_semantic_probe(surface, question=question, topk=max(max_n * 2, 12)):
                score = float(hit.get("zvec_score") or 0.0)
                if score < threshold:
                    continue
                rows.append(
                    {
                        "entity_id": str(hit.get("id") or ""),
                        "node_type": str(hit.get("node_type") or ""),
                        "name": str(hit.get("name") or ""),
                        "score": round(score, 4),
                        "source": "zvec_hybrid",
                    }
                )
        except Exception as exc:
            logger.warning("[ZNWD6][tau] zvec probe failed kw=%r: %s", surface[:30], exc)

    if driver is not None:
        try:
            from blueprints.GLCX.ZNWD4.keyword_probe import probe_keyword

            probe = probe_keyword(driver, surface, question=question)
            for hit in (probe.get("hits") or [])[:max_n]:
                if not isinstance(hit, dict):
                    continue
                score = float(hit.get("score") or 0.0)
                if score < threshold * 0.85:
                    continue
                rows.append(
                    {
                        "entity_id": str(hit.get("id") or ""),
                        "node_type": str(hit.get("node_type") or ""),
                        "name": str(hit.get("name") or ""),
                        "score": round(score, 4),
                        "source": str(hit.get("source") or "contains"),
                    }
                )
        except Exception as exc:
            logger.debug("[ZNWD6][tau] contains probe skipped: %s", exc)

    seen: set = set()
    out: List[Dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: x.get("score", 0), reverse=True):
        key = (r.get("node_type"), r.get("entity_id"), r.get("name"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= max_n:
            break
    return out


def build_tau(
    question: str,
    *,
    driver: Any = None,
    probe_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """构建 τ：每个 mention span 对应高召回候选列表。"""
    q = (question or "").strip()
    spans: List[Dict[str, Any]] = []
    probe_keywords: List[str] = []
    cache = probe_cache if probe_cache is not None else _probe_request_cache

    for item in extract_focus_candidates(q)[:8]:
        if isinstance(item, dict):
            kw = _normalize(str(item.get("keyword") or ""))
        else:
            kw = _normalize(str(item or ""))
        if kw and kw not in probe_keywords:
            probe_keywords.append(kw)

    if not probe_keywords and q:
        probe_keywords = [_normalize(q)]

    def _probe_cached(kw: str) -> List[Dict[str, Any]]:
        if kw in cache:
            return cache[kw]
        result = _probe_span(kw, question=q, driver=driver)
        cache[kw] = result
        return result

    zvec_used = False
    kws = probe_keywords[:6]
    if len(kws) <= 1:
        for kw in kws:
            candidates = _probe_cached(kw)
            if any(c.get("source") == "zvec_hybrid" for c in candidates):
                zvec_used = True
            spans.append({"span": kw, "candidates": candidates})
    else:
        workers = min(4, len(kws))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_probe_cached, kws))
        for kw, candidates in zip(kws, results):
            if any(c.get("source") == "zvec_hybrid" for c in candidates):
                zvec_used = True
            spans.append({"span": kw, "candidates": candidates})

    flat = [c for s in spans for c in (s.get("candidates") or [])]
    return {
        "question": q,
        "spans": spans,
        "flat_candidates": flat,
        "zvec_used": zvec_used,
        "probe_ran": bool(flat),
    }
