"""EFHR Step-1: Index Probe First — build EvidenceBundle before any LLM routing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .keyword_probe import extract_focus_candidates
from .keyword_refine import probe_enhanced_enabled, probe_keyword_refined, probe_refine_enabled, score_entity_match

_SEARCH_ORDER = ("TestProject", "Environment", "Equipment", "Indicator", "TestMethod", "EquipmentType", "File")

_INTENT_TAIL = re.compile(
    r"(有什么|有哪些|包括哪些|是什么|的方法|的指标|的项目|试验项目|测试项目|试验方法|测试方法).*$"
)


@dataclass
class EvidenceBundle:
    candidates: List[str] = field(default_factory=list)
    hits: List[Dict[str, Any]] = field(default_factory=list)
    best_by_label: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    multi_entity: bool = False
    probe_ran: bool = False
    zvec_used: bool = False

    def best_name(self, label: str) -> str:
        hit = self.best_by_label.get(label) or {}
        return str(hit.get("name") or "").strip()

    def best_id(self, label: str) -> str:
        hit = self.best_by_label.get(label) or {}
        return str(hit.get("id") or "").strip()


_ORAL_PREFIX = re.compile(
    r"^(?:帮我|请|麻烦|查询一下|查一下|查下|帮我查下|帮我看看|看看|查询)\s*",
    re.I,
)


def _strip_oral_prefix(question: str) -> str:
    q = (question or "").strip()
    prev = None
    while q != prev:
        prev = q
        q = _ORAL_PREFIX.sub("", q).strip()
    return q


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def _score_hit(candidate: str, hit: Dict[str, Any], question: str) -> float:
    """图谱命中质量：基于 score_entity_match，问句含规范名加分。"""
    name = str(hit.get("name") or "").strip()
    if not name:
        return -1.0
    probe_sc = hit.get("score")
    if probe_sc is not None:
        try:
            base = float(probe_sc) * 20.0
        except (TypeError, ValueError):
            base = score_entity_match(candidate, name, question, enhanced=probe_enhanced_enabled()) * 20.0
    else:
        base = score_entity_match(candidate, name, question, enhanced=probe_enhanced_enabled()) * 20.0
    q = _norm(question)
    if _norm(name) in q:
        base += 2.0
    return base


def _rank_candidates(candidates: List[str], question: str) -> List[str]:
    q = _strip_oral_prefix(question)
    q2 = _INTENT_TAIL.sub("", q).strip()
    scored: List[Tuple[float, str]] = []
    seen: set = set()
    for c in candidates:
        c = (c or "").strip()
        if len(c) < 2 or c in seen:
            continue
        seen.add(c)
        bonus = 0.0
        if c == q2:
            bonus += 15.0
        if q2 and c == q2:
            bonus += 10.0
        if _INTENT_TAIL.search(c):
            bonus -= 12.0
        scored.append((bonus - len(c) * 0.03, c))
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    return [c for _, c in scored]


def _maybe_update_best(
    best_by_label: Dict[str, Dict[str, Any]],
    *,
    candidate: str,
    hit: Dict[str, Any],
    question: str,
) -> None:
    nt = str(hit.get("node_type") or "").strip()
    if not nt:
        return
    new_score = _score_hit(candidate, hit, question)
    prev = best_by_label.get(nt)
    if prev:
        prev_score = _score_hit(candidate, prev, question)
        if new_score <= prev_score:
            return
    best_by_label[nt] = hit


def _run_probe(
    driver: Any,
    keyword: str,
    *,
    question: str,
    label_hints: Optional[List[str]],
    enhanced: bool,
    force_zvec: bool,
) -> Dict[str, Any]:
    from .keyword_probe import probe_keyword

    probe_fn = probe_keyword_refined if probe_refine_enabled() else None
    if probe_fn:
        return probe_fn(
            driver,
            keyword,
            question=question,
            label_hints=label_hints,
            enhanced=enhanced,
            force_zvec=force_zvec,
        )
    return probe_keyword(
        driver,
        keyword,
        label_hints=label_hints,
        enhanced=enhanced,
        question=question,
        force_zvec=force_zvec,
    )


def build_evidence_bundle(
    question: str,
    *,
    driver: Any = None,
    label_hints: Optional[List[str]] = None,
    history_focus: str = "",
    enhanced: bool = False,
    seed_keywords: Optional[List[str]] = None,
    typed_probes: Optional[List[Tuple[str, str]]] = None,
    force_zvec: bool = False,
) -> EvidenceBundle:
    q_norm = _strip_oral_prefix(question)
    use_enhanced = enhanced or probe_enhanced_enabled()
    candidates = extract_focus_candidates(q_norm) or extract_focus_candidates(question)
    if history_focus and history_focus not in candidates:
        candidates.insert(0, history_focus)
    for kw in seed_keywords or []:
        kw = (kw or "").strip()
        if kw and kw not in candidates:
            candidates.insert(0, kw)
    seen: set = set()
    uniq: List[str] = []
    for c in candidates:
        c = (c or "").strip()
        if len(c) >= 2 and c not in seen:
            seen.add(c)
            uniq.append(c)
    uniq = _rank_candidates(uniq, question)

    labels = list(label_hints) if label_hints else list(_SEARCH_ORDER)
    hits: List[Dict[str, Any]] = []
    best_by_label: Dict[str, Dict[str, Any]] = {}
    probe_ran = False
    zvec_used = False

    def _ingest_result(result: Dict[str, Any], *, cand: str) -> None:
        nonlocal zvec_used
        if not isinstance(result, dict):
            return
        if result.get("zvec_used"):
            zvec_used = True
        for h in result.get("hits") or []:
            if isinstance(h, dict):
                hits.append(h)
        best = result.get("best")
        if isinstance(best, dict):
            _maybe_update_best(
                best_by_label,
                candidate=cand,
                hit=best,
                question=question,
            )

    if driver:
        for kw, node_label in typed_probes or []:
            kw = (kw or "").strip()
            if len(kw) < 1:
                continue
            probe_ran = True
            hint = [node_label] if node_label else labels
            _ingest_result(
                _run_probe(
                    driver,
                    kw,
                    question=question,
                    label_hints=hint,
                    enhanced=use_enhanced,
                    force_zvec=force_zvec or bool(node_label),
                ),
                cand=kw,
            )

        for cand in uniq[:6]:
            probe_ran = True
            _ingest_result(
                _run_probe(
                    driver,
                    cand,
                    question=question,
                    label_hints=labels,
                    enhanced=use_enhanced,
                    force_zvec=force_zvec,
                ),
                cand=cand,
            )

    entity_labels = set(best_by_label.keys())
    return EvidenceBundle(
        candidates=uniq,
        hits=hits,
        best_by_label=best_by_label,
        multi_entity=len(entity_labels) > 1,
        probe_ran=probe_ran,
        zvec_used=zvec_used,
    )


__all__ = ["EvidenceBundle", "build_evidence_bundle"]
