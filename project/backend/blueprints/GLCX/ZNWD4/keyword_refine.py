"""ZNWD4 Probe 层：长 span 子串展开 + LCS/子串重排，纠正提取词长于图谱实体名的情况。"""

from __future__ import annotations

import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset({"什么", "哪些", "如何", "怎样", "请问", "查询", "包括", "相关"})

_PROJECT_TOKEN_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]+?(?<![试验测试]))(?:试验|测试)?项目",
    re.I,
)

_INTENT_TAIL = re.compile(
    r"(有什么|有哪些|包括哪些|是什么|的方法|的指标|的项目|试验项目|测试项目|试验方法|测试方法).*$"
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def probe_refine_enabled() -> bool:
    raw = (os.environ.get("ZNWD4_PROBE_REFINE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def probe_enhanced_enabled() -> bool:
    raw = (os.environ.get("ZNWD4_PROBE_ENHANCED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _variant_max() -> int:
    try:
        return max(2, int(os.environ.get("ZNWD4_PROBE_VARIANT_MAX") or "8"))
    except (TypeError, ValueError):
        return 8


def _subspan_min() -> int:
    try:
        return max(2, int(os.environ.get("ZNWD4_PROBE_SUBSPAN_MIN") or "2"))
    except (TypeError, ValueError):
        return 2


def lcs_ratio(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def score_entity_match(span: str, entity_name: str, question: str = "", *, enhanced: bool = False) -> float:
    """统一实体匹配分：entity ⊂ span 时优先规范名（纠错核心）。"""
    from .keyword_probe import _levenshtein_leq

    c, n = _norm(span), _norm(entity_name)
    q = _norm(question)
    if not c or not n:
        return 0.0
    if c == n:
        return 1.0
    if n in c:
        coverage = len(n) / max(len(c), 1)
        lcs = lcs_ratio(c, n)
        q_bonus = 0.05 if (q and n in q) else 0.0
        return min(1.0, 0.90 + 0.08 * coverage + 0.05 * lcs + q_bonus)
    if c in n:
        return 0.85 + 0.1 * (len(c) / max(len(n), 1))
    if enhanced:
        if n.startswith(c) and len(c) >= 2:
            return 0.82 + 0.08 * (len(c) / max(len(n), 1))
        if _levenshtein_leq(c, n, 2):
            return 0.78
    return lcs_ratio(c, n) * 0.75


def _prefix_variants(keyword: str, *, min_len: int = 2) -> List[str]:
    kw = _norm(keyword)
    if len(kw) < min_len:
        return []
    out: List[str] = []
    seen: set = set()
    for n in range(len(kw), min_len - 1, -1):
        p = kw[:n]
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _project_subspans(span: str) -> List[str]:
    """从拼接 span 中提取以「项目」结尾的候选（含装备+项目拆分）。"""
    raw = _norm(span)
    if len(raw) < 4:
        return []
    out: List[str] = []
    seen: set = set()

    def collect(sub: str) -> None:
        sub = _norm(sub)
        if len(sub) >= 4 and sub not in seen:
            seen.add(sub)
            out.append(sub)

    for m in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,36}(?:试验|测试)?项目)", raw):
        collect(m.group(1))
    m_type = re.search(r"型([\u4e00-\u9fffA-Za-z0-9·\-]+(?:试验|测试)?项目)$", raw)
    if m_type:
        collect(m_type.group(1))
    return out


def _question_aligned_subspans(span: str, question: str, *, min_len: int) -> List[str]:
    """问句中出现且为 span 子串的连续片段（按长度升序）。"""
    s, q = _norm(span), _norm(question)
    if len(s) < min_len or not q or s not in q:
        return []
    found: List[str] = []
    seen: set = set()
    for length in range(min_len, len(s) + 1):
        for start in range(0, len(s) - length + 1):
            sub = s[start : start + length]
            if sub in seen or sub in _STOPWORDS:
                continue
            if sub in q:
                seen.add(sub)
                found.append(sub)
    found.sort(key=lambda x: (len(x), -lcs_ratio(s, x)))
    return found


def expand_span_variants(span: str, question: str = "") -> List[str]:
    """从长 span 生成有序 probe 候选（原 span 首位，其余按纠错优先级）。"""
    raw = _norm(span)
    if len(raw) < 2:
        return []

    min_len = _subspan_min()
    max_variants = _variant_max()
    scored: List[tuple[float, str]] = []
    seen: set = set()

    def add(p: str, bonus: float = 0.0) -> None:
        p = _norm(p)
        if len(p) < min_len or p in seen or p in _STOPWORDS:
            return
        seen.add(p)
        lcs = lcs_ratio(raw, p)
        q_bonus = 2.0 if question and p in _norm(question) else 0.0
        length_penalty = len(p) * 0.02
        scored.append((bonus + q_bonus + lcs - length_penalty, p))

    add(raw, bonus=5.0)

    try:
        from .question_route_lock import extract_project_keywords

        for proj in extract_project_keywords(f"{question}\n{raw}"):
            add(proj, bonus=4.0)
    except ImportError:
        pass

    for m in _PROJECT_TOKEN_RE.finditer(raw):
        add(m.group(0), bonus=3.5)

    for sub in _project_subspans(raw):
        add(sub, bonus=4.5)

    try:
        from .slot_focus import extract_equipment_focus, extract_environment_focus

        eq = extract_equipment_focus(f"{question} {raw}")
        if eq and raw.startswith(eq) and len(raw) > len(eq):
            tail = raw[len(eq) :].lstrip("的与和及")
            if tail:
                add(tail, bonus=4.2)
        if eq:
            add(eq, bonus=3.0)
        env = extract_environment_focus(f"{question} {raw}")
        if env:
            add(env, bonus=2.5)
    except ImportError:
        pass

    for p in _prefix_variants(raw, min_len=min_len)[1:]:
        add(p, bonus=1.0)

    for p in _question_aligned_subspans(raw, question, min_len=min_len):
        add(p, bonus=1.5)

    scored.sort(key=lambda x: (-x[0], len(x[1])))
    ordered = [p for _, p in scored]
    return ordered[:max_variants]


def probe_keyword_refined(
    driver: Any,
    keyword: str,
    *,
    question: str = "",
    label_hints: Optional[List[str]] = None,
    enhanced: Optional[bool] = None,
    force_zvec: bool = False,
) -> Dict[str, Any]:
    """
    对 probe_keyword 的封装：展开 span variant、合并 hits、用 score_entity_match 重排。
    返回结构与 probe_keyword 兼容，额外含 matched_variant（若有）。
    """
    from .keyword_probe import probe_keyword

    kw = _norm(keyword)
    if not kw or not driver:
        return {"keyword": kw, "hits": [], "best": None, "match_count": 0}

    use_enhanced = probe_enhanced_enabled() if enhanced is None else enhanced
    use_refine = probe_refine_enabled()

    if use_refine:
        variants = expand_span_variants(kw, question)
    else:
        variants = [kw]

    merged_hits: Dict[tuple, Dict[str, Any]] = {}
    matched_variant: Optional[str] = None

    for variant in variants:
        result = probe_keyword(
            driver,
            variant,
            label_hints=label_hints,
            enhanced=use_enhanced,
            question=question,
            force_zvec=force_zvec,
        )
        if not isinstance(result, dict):
            continue
        for h in result.get("hits") or []:
            if not isinstance(h, dict):
                continue
            name = str(h.get("name") or "").strip()
            if not name:
                continue
            nt = str(h.get("node_type") or "")
            key = (nt, str(h.get("id") or ""), name)
            sc = score_entity_match(kw, name, question, enhanced=use_enhanced)
            hit = {**h, "score": round(sc, 4), "probe_variant": variant}
            prev = merged_hits.get(key)
            if not prev or sc > float(prev.get("score") or 0):
                merged_hits[key] = hit
                if sc >= 0.85 and not matched_variant:
                    matched_variant = variant

    hits = sorted(merged_hits.values(), key=lambda h: h.get("score", 0), reverse=True)
    best = hits[0] if hits else None
    zvec_used = any(str(h.get("source") or "") == "zvec" for h in hits)

    if best and matched_variant and matched_variant != kw:
        logger.info(
            "[ZNWD4][probe_refine] span=%r variant=%r best=%r score=%s",
            kw[:40],
            matched_variant[:40],
            str(best.get("name") or "")[:40],
            best.get("score"),
        )

    from .keyword_probe import _LABEL_ZH

    return {
        "keyword": kw,
        "hits": hits[:12],
        "best": best,
        "match_count": len(hits),
        "matched_variant": matched_variant,
        "node_type_zh": _LABEL_ZH.get(str((best or {}).get("node_type") or ""), ""),
        "zvec_used": zvec_used,
    }


__all__ = [
    "probe_refine_enabled",
    "probe_enhanced_enabled",
    "lcs_ratio",
    "score_entity_match",
    "expand_span_variants",
    "probe_keyword_refined",
]
