"""
ZNWD5 文档混合检索：Zvec 向量 + FTS，输出格式与 Chroma 行兼容。
hybrid 模式下 zvec 无文档时回退 Chroma。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from .embedding_service import encode_query
from .zvec_index_manager import get_zvec_manager

logger = logging.getLogger(__name__)


def _doc_engine() -> str:
    return os.environ.get("ZNWD5_DOC_ENGINE", "hybrid").strip().lower()


def _normalize_digits(text: str) -> str:
    trans = str.maketrans("０１２３４５６７８９", "0123456789")
    return (text or "").translate(trans)


def _extract_section_hint(query: str) -> str:
    q = _normalize_digits(query or "")
    m = re.search(r"第\s*(\d+(?:\.\d+)*)", q)
    if m:
        return m.group(1)
    m2 = re.search(r"第\s*(\d+)\s*节", q)
    return m2.group(1) if m2 else ""


def _section_variants(hint: str) -> List[str]:
    h = (hint or "").strip()
    if not h:
        return []
    out = [h]
    if h.isdigit() and len(h) >= 2:
        if len(h) == 2:
            out.append(f"{h[0]}.{h[1]}")
        elif len(h) == 3:
            out.append(f"{h[0]}.{h[1:]}")
            out.append(f"{h[:2]}.{h[2:]}")
    if "." in h:
        out.append(h.replace(".", ""))
    seen: set = set()
    uniq: List[str] = []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def _section_match_score(section_no: str, hint: str) -> int:
    sec = _normalize_digits(str(section_no or "")).strip()
    if not hint:
        return 1
    best = 99
    for v in _section_variants(hint):
        if sec == v or sec.startswith(v + ".") or sec.startswith(v):
            return 0
        if v in sec or sec in v:
            best = min(best, 2)
    return best


def _extract_title_keyword(query: str) -> str:
    q = re.sub(r"\s+", "", query or "")
    for pat in (
        r"(机动车暴露于天气老化[^，？?是什么]*?)",
        r"(无人机系统[^，？?是什么]*?试验方案)",
        r"([\u4e00-\u9fa5]{6,30}?试验方案)",
        r"([\u4e00-\u9fa5]{8,40}?标准)",
    ):
        m = re.search(pat, q)
        if m:
            kw = m.group(1).strip("的有哪些是什么")
            if len(kw) >= 6:
                return kw
    return ""


def _is_overview_query(query: str) -> bool:
    return bool(re.search(r"主要内容|概述|讲什么|文档概述|标准内容", query or ""))


def extract_doc_query_hints(query: str) -> Dict[str, Any]:
    """从问句解析文档检索参数（供 ZNWD6 deferred_doc 等调用）。"""
    q = (query or "").strip()
    std = _extract_std_code(q)
    title_kw = _extract_title_keyword(q) or std
    return {
        "std_code": std,
        "title_keyword": title_kw,
        "section_hint": _extract_section_hint(q),
        "overview": _is_overview_query(q),
        "doc_id": "WRJ-HJSY-2025-001" if "试验方案" in q else "",
    }


def _extract_std_code(query: str) -> str:
    m = re.search(r"(G\s*B\s*/?\s*T\s*[\d\.\-]+(?:\s*[-–—]\s*\d{4})?)", query or "", re.I)
    return _normalize_std_code(m.group(1)) if m else ""


def _normalize_std_code(code: str) -> str:
    s = re.sub(r"\s+", "", (code or "").strip())
    s = s.replace("／", "/").replace("–", "-").replace("—", "-").upper()
    if s.startswith("GBT") and not s.startswith("GB/T"):
        s = "GB/T" + s[3:]
    return s


def _std_code_key(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", _normalize_std_code(code))


def _std_code_matches(a: str, b: str) -> bool:
    ka, kb = _std_code_key(a), _std_code_key(b)
    if not ka or not kb:
        return False
    return ka == kb or ka in kb or kb in ka


def _title_matches_keyword(title: str, keyword: str) -> bool:
    if not keyword:
        return True
    tn = re.sub(r"\s+", "", (title or ""))
    kn = re.sub(r"\s+", "", (keyword or ""))
    if kn in tn or tn in kn:
        return True
    if _std_code_matches(title, keyword):
        return True
    if len(kn) >= 6:
        for i in range(0, len(kn) - 5):
            if kn[i : i + 8] in tn:
                return True
    return False


def _chroma_fallback_search(
    query: str,
    collection: Any,
    *,
    top_k: int = 12,
) -> List[Dict[str, Any]]:
    if collection is None:
        return [{"error": "Chroma collection unavailable"}]
    q = (query or "").strip()
    if not q:
        return [{"error": "empty query"}]

    def _parse_results(res: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc in enumerate(docs):
            md = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
            dist = dists[i] if i < len(dists) else None
            score = 1.0 - float(dist) if dist is not None else 0.0
            out.append(
                {
                    "doc_id": md.get("doc_id") or "",
                    "title": md.get("title") or "",
                    "content": str(doc or "")[:5000],
                    "distance": float(dist) if dist is not None else 1.0,
                    "score": score,
                    "section_no": md.get("section_no") or md.get("section") or "",
                    "section": md.get("section") or md.get("section_no") or "",
                    "metadata": dict(md),
                    "source": "chroma",
                }
            )
        return out

    res = None
    try:
        q_vec = encode_query(q)
        res = collection.query(
            query_embeddings=[q_vec],
            n_results=max(1, min(top_k, 200)),
        )
    except Exception as exc:
        logger.warning("[ZNWD5][doc] chroma BGE embedding query failed: %s", exc)
        try:
            res = collection.query(
                query_texts=[q],
                n_results=max(1, min(top_k, 200)),
            )
        except Exception as exc2:
            logger.warning("[ZNWD5][doc] chroma query_texts failed: %s", exc2)
            return [{"error": f"Chroma fallback failed: {exc2}"}]

    out = _parse_results(res or {})
    logger.info("[ZNWD5][doc] chroma fallback query=%r hits=%d", q[:60], len(out))
    return out


def _hits_to_chunks(
    hits: List[Dict[str, Any]],
    *,
    title_keyword: str = "",
    section_hint: str = "",
) -> List[Dict[str, Any]]:
    all_chunks: List[Dict[str, Any]] = []
    matched: List[Dict[str, Any]] = []
    for h in hits:
        fields = h.get("fields") or {}
        title = str(fields.get("title") or "")
        chunk = {
            "doc_id": fields.get("doc_id") or "",
            "title": title,
            "content": str(fields.get("content") or "")[:5000],
            "distance": 1.0 - float(h.get("score") or 0.0),
            "score": float(h.get("score") or 0.0),
            "section_no": str(fields.get("section") or ""),
            "section": str(fields.get("section") or ""),
            "metadata": dict(fields),
            "source": "zvec",
        }
        all_chunks.append(chunk)
        if not title_keyword or _title_matches_keyword(title, title_keyword):
            matched.append(chunk)

    chunks = matched if matched else all_chunks
    if section_hint and chunks:
        chunks.sort(
            key=lambda c: (
                _section_match_score(str(c.get("section_no") or ""), section_hint),
                str(c.get("section_no") or ""),
            )
        )
    return chunks


def zvec_doc_hybrid_search(
    query: str,
    *,
    top_k: int = 12,
    doc_id: str = "",
    title_keyword: str = "",
    std_code: str = "",
    chroma_collection: Any = None,
) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return [{"error": "empty query"}]

    engine = _doc_engine()
    mgr = get_zvec_manager()

    if mgr.doc_count() == 0:
        if engine in ("hybrid", "chroma") and chroma_collection is not None:
            return _chroma_fallback_search(q, chroma_collection, top_k=top_k)
        if engine == "chroma" and chroma_collection is not None:
            return _chroma_fallback_search(q, chroma_collection, top_k=top_k)
        return [{"error": "Zvec documents index empty; run sync_chroma_to_zvec.py"}]

    if not title_keyword:
        title_keyword = _extract_title_keyword(q) or _extract_std_code(q)
    if not std_code:
        std_code = _extract_std_code(q) or title_keyword
    std_code = _normalize_std_code(std_code) if std_code else ""

    hints = extract_doc_query_hints(q)
    if not title_keyword:
        title_keyword = str(hints.get("title_keyword") or "")
    section_hint = str(hints.get("section_hint") or "") or _extract_section_hint(q)
    overview = bool(hints.get("overview"))
    if not doc_id:
        doc_id = str(hints.get("doc_id") or "")
    q_vec = encode_query(q)
    hits = mgr.search_documents_hybrid(
        q_vec,
        q,
        topk=max(1, min(top_k, 200)),
        std_code=std_code,
        doc_id=doc_id,
    )
    chunks = _hits_to_chunks(hits, title_keyword=title_keyword, section_hint=section_hint)

    if overview and chunks:
        def _sec_sort_key(c: Dict[str, Any]) -> tuple:
            sec = str(c.get("section_no") or "")
            try:
                num = float(sec.split(".")[0]) if sec else 9999.0
            except ValueError:
                num = 9999.0
            return (num, sec)

        chunks.sort(key=_sec_sort_key)

    if not chunks and std_code:
        hits = mgr.search_documents_hybrid(
            q_vec,
            q,
            topk=max(1, min(top_k, 200)),
            std_code="",
            doc_id=doc_id,
        )
        filtered = [
            h
            for h in hits
            if _std_code_matches(str((h.get("fields") or {}).get("std_code") or ""), std_code)
            or _std_code_matches(str((h.get("fields") or {}).get("title") or ""), std_code)
        ]
        chunks = _hits_to_chunks(filtered or hits, title_keyword=title_keyword, section_hint=section_hint)

    if not chunks and engine == "hybrid" and chroma_collection is not None:
        return _chroma_fallback_search(q, chroma_collection, top_k=top_k)

    logger.info("[ZNWD5][zvec_doc] query=%r hits=%d", q[:60], len(chunks))
    return chunks


def zvec_doc_filter(
    chunks: List[Dict[str, Any]],
    *,
    min_score: float = 0.0,
    max_distance: float = 2.0,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in chunks or []:
        if not isinstance(c, dict) or c.get("error"):
            continue
        sc = float(c.get("score") or 0.0)
        dist = float(c.get("distance") if c.get("distance") is not None else (1.0 - sc))
        if sc >= min_score and dist <= max_distance:
            out.append(c)
    return out


def zvec_doc_rerank(
    chunks: List[Dict[str, Any]],
    query: str,
    *,
    top_k: int = 12,
    section_hint: str = "",
    overview: bool = False,
) -> List[Dict[str, Any]]:
    """简易重排：关键词覆盖 + zvec score + 章节匹配。"""
    q = (query or "").strip().lower()
    if not q or not chunks:
        return list(chunks or [])[:top_k]
    hint = section_hint or _extract_section_hint(query)
    tokens = [t for t in re.split(r"\s+", q) if len(t) >= 2]

    def _rank(c: Dict[str, Any]) -> float:
        text = f"{c.get('title','')} {c.get('content','')}".lower()
        cover = sum(1 for t in tokens if t in text) / max(1, len(tokens))
        sec_bonus = 0.0
        if hint:
            sm = _section_match_score(str(c.get("section_no") or ""), hint)
            sec_bonus = {0: 0.35, 1: 0.15, 2: 0.05}.get(sm, 0.0)
        return float(c.get("score") or 0.0) * 0.55 + cover * 0.35 + sec_bonus

    ranked = sorted(chunks, key=_rank, reverse=True)
    if overview:
        def _sec_sort_key(c: Dict[str, Any]) -> tuple:
            sec = str(c.get("section_no") or "")
            try:
                num = float(sec.split(".")[0]) if sec else 9999.0
            except ValueError:
                num = 9999.0
            return (num, sec)

        ranked.sort(key=_sec_sort_key)
    return ranked[:top_k]
