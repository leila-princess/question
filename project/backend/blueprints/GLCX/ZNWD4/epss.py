"""EPSS — Evidence-Projected Span Selection (span lattice + compatibility scoring)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .keyword_probe import _score_match
from .probe_preflight import EvidenceBundle
from .routing_catalog import get_route

_MIN_SPAN = 2
_MAX_SPAN = 36
_MAX_LATTICE = 96
_EXEC_THRESHOLD = 0.72


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def _index_probe_executable(keyword: str, canonical_name: str) -> float:
    """
    Score whether keyword can hit entity via CONTAINS (entity.Name ⊇ kw).
    Over-long θ̃ (kw ⊃ name) scores low even if strings overlap.
    """
    pn, nn = _norm(keyword), _norm(canonical_name)
    if not pn or not nn:
        return 0.0
    if pn == nn:
        return 1.0
    if pn in nn:
        return 0.88 + 0.1 * (len(pn) / max(len(nn), 1))
    if nn in pn:
        return 0.42 * (len(nn) / max(len(pn), 1))
    return _score_match(pn, nn) * 0.75


def _question_substrings(question: str, *, max_count: int = 80) -> List[str]:
    """Contiguous substrings of q (length-bounded), longest first."""
    q = _norm(question)
    if len(q) < _MIN_SPAN:
        return []
    seen: set = set()
    out: List[str] = []
    for length in range(min(len(q), _MAX_SPAN), _MIN_SPAN - 1, -1):
        for i in range(0, len(q) - length + 1):
            sub = q[i : i + length]
            if sub in seen:
                continue
            seen.add(sub)
            out.append(sub)
            if len(out) >= max_count:
                return out
    return out


def _theta_substrings(keywords: Sequence[str], question: str) -> List[str]:
    """Substrings of LLM keywords that also appear in the question."""
    qn = _norm(question)
    seen: set = set()
    out: List[str] = []
    for kw in keywords:
        kn = _norm(kw)
        if len(kn) < _MIN_SPAN:
            continue
        for length in range(min(len(kn), _MAX_SPAN), _MIN_SPAN - 1, -1):
            for i in range(0, len(kn) - length + 1):
                sub = kn[i : i + length]
                if sub not in qn or sub in seen:
                    continue
                seen.add(sub)
                out.append(sub)
    out.sort(key=len, reverse=True)
    return out[:40]


def build_span_lattice(
    question: str,
    keywords: Sequence[str],
    focus: str,
    evidence: EvidenceBundle,
) -> List[str]:
    """Candidate spans from q × ℰ × θ̃ (no intent-regex trimming)."""
    seen: set = set()
    lattice: List[str] = []

    def add(s: str) -> None:
        sn = _norm(s)
        if len(sn) < _MIN_SPAN or len(sn) > _MAX_SPAN or sn in seen:
            return
        seen.add(sn)
        lattice.append(sn)

    for c in evidence.candidates or []:
        add(c)
    for hit in evidence.hits or []:
        if isinstance(hit, dict):
            add(str(hit.get("name") or ""))
    for hit in (evidence.best_by_label or {}).values():
        if isinstance(hit, dict):
            add(str(hit.get("name") or ""))

    for kw in list(keywords) + ([focus] if focus else []):
        add(kw)
        kn = _norm(kw)
        qn = _norm(question)
        if kn and qn and kn in qn:
            add(kn)

    for sub in _theta_substrings(keywords, question):
        add(sub)
    for sub in _question_substrings(question):
        add(sub)

    lattice.sort(key=lambda x: (-len(x), x))
    return lattice[:_MAX_LATTICE]


def score_span_candidate(
    span: str,
    *,
    keyword: str,
    question: str,
    evidence: EvidenceBundle,
    route_id: str,
) -> float:
    """C_span(s; θ̃, q, ℰ) with question-anchor hard constraint."""
    s = _norm(span)
    qn = _norm(question)
    if len(s) < _MIN_SPAN or s not in qn:
        return -1.0

    kw = _norm(keyword) or s
    sim_theta = _score_match(s, kw) if kw else 0.5

    sim_ev = 0.0
    for hit in evidence.hits or []:
        if isinstance(hit, dict):
            sim_ev = max(sim_ev, _score_match(s, str(hit.get("name") or "")))
    for hit in (evidence.best_by_label or {}).values():
        if isinstance(hit, dict):
            sim_ev = max(sim_ev, _score_match(s, str(hit.get("name") or "")))

    entry = get_route(route_id)
    route_labels = set(entry.keyword_nodes or []) if entry else set()
    type_align = 0.0
    for label in route_labels:
        hit = (evidence.best_by_label or {}).get(label)
        if isinstance(hit, dict):
            type_align = max(type_align, _score_match(s, str(hit.get("name") or "")))

    len_pen = 0.08 * (len(s) / max(len(qn), 1))
    return 0.38 * sim_theta + 0.37 * sim_ev + 0.25 * type_align - len_pen


def top_span_candidates(
    question: str,
    keywords: Sequence[str],
    focus: str,
    evidence: EvidenceBundle,
    route_id: str,
    *,
    top_k: int = 8,
) -> List[Tuple[str, float]]:
    primary = _norm(focus) or (_norm(keywords[0]) if keywords else "")
    lattice = build_span_lattice(question, keywords, focus, evidence)
    scored: List[Tuple[str, float]] = []
    seen: set = set()
    for span in lattice:
        if span in seen:
            continue
        seen.add(span)
        sc = score_span_candidate(
            span, keyword=primary, question=question, evidence=evidence, route_id=route_id
        )
        if sc >= 0:
            scored.append((span, sc))
    scored.sort(key=lambda x: (-x[1], -len(x[0])))
    return scored[:top_k]


def keyword_executability_score(
    keywords: Sequence[str],
    focus: str,
    evidence: EvidenceBundle,
    route_id: str,
) -> float:
    """How well current θ̃ aligns with ℰ for route-required types."""
    entry = get_route(route_id)
    labels = list(entry.keyword_nodes or []) if entry else []
    if not labels:
        return 1.0
    probes = [_norm(focus)] if focus else []
    probes.extend(_norm(k) for k in keywords if k)
    probes = [p for p in probes if len(p) >= _MIN_SPAN]
    if not probes:
        return 0.0
    best = 0.0
    for label in labels:
        name = evidence.best_name(label) if evidence else ""
        if not name:
            continue
        for p in probes:
            best = max(best, _index_probe_executable(p, name))
    return best


def executability_gap(
    keywords: Sequence[str],
    focus: str,
    evidence: EvidenceBundle,
    route_id: str,
) -> float:
    return max(0.0, 1.0 - keyword_executability_score(keywords, focus, evidence, route_id))


@dataclass
class EpssResult:
    keywords: List[str]
    focus: str
    projected: bool = False
    gap_before: float = 0.0
    gap_after: float = 0.0
    chosen_span: str = ""
    chosen_score: float = 0.0
    candidates: List[Tuple[str, float]] = field(default_factory=list)


def project_keywords(
    keywords: Sequence[str],
    focus: str,
    question: str,
    evidence: EvidenceBundle,
    route_id: str,
    *,
    gap_threshold: float = 1.0 - _EXEC_THRESHOLD,
) -> EpssResult:
    """
    Π(θ̃; q, ℰ): pick argmax span on lattice when executability gap is high.
    Returns updated keywords/focus (may equal input if already executable).
    """
    kws = [str(x).strip() for x in keywords if str(x).strip()]
    foc = (focus or "").strip() or (kws[0] if kws else "")
    gap_before = executability_gap(kws, foc, evidence, route_id)

    if gap_before < gap_threshold and kws:
        return EpssResult(
            keywords=kws,
            focus=foc,
            projected=False,
            gap_before=gap_before,
            gap_after=gap_before,
        )

    cands = top_span_candidates(question, kws, foc, evidence, route_id, top_k=8)
    if not cands:
        return EpssResult(
            keywords=kws,
            focus=foc,
            projected=False,
            gap_before=gap_before,
            gap_after=gap_before,
            candidates=[],
        )

    best_span, best_score = cands[0]
    gap_after = executability_gap([best_span], best_span, evidence, route_id)

    if best_score < 0.25 and gap_before < gap_threshold:
        return EpssResult(
            keywords=kws,
            focus=foc,
            projected=False,
            gap_before=gap_before,
            gap_after=gap_before,
            candidates=cands,
        )

    # Project when gap high or chosen span scores better on evidence
    current_exec = keyword_executability_score(kws, foc, evidence, route_id)
    projected_exec = keyword_executability_score([best_span], best_span, evidence, route_id)
    if projected_exec <= current_exec and gap_before < gap_threshold:
        return EpssResult(
            keywords=kws,
            focus=foc,
            projected=False,
            gap_before=gap_before,
            gap_after=gap_before,
            candidates=cands,
        )

    return EpssResult(
        keywords=[best_span],
        focus=best_span,
        projected=True,
        gap_before=gap_before,
        gap_after=gap_after,
        chosen_span=best_span,
        chosen_score=best_score,
        candidates=cands,
    )


def format_span_candidates_for_prompt(candidates: Sequence[Tuple[str, float]], limit: int = 5) -> str:
    lines: List[str] = []
    for i, (span, sc) in enumerate(candidates[:limit]):
        lines.append(f"  {i + 1}. {span} (compat={sc:.3f})")
    return "\n".join(lines) if lines else "  （无候选）"


__all__ = [
    "build_span_lattice",
    "score_span_candidate",
    "top_span_candidates",
    "keyword_executability_score",
    "executability_gap",
    "EpssResult",
    "project_keywords",
    "format_span_candidates_for_prompt",
]
