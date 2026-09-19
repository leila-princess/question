"""Disambiguate probe hits when multiple graph nodes share the same display name."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hierarchy_focus import parse_equipment_type_hierarchy

_PARENT_SCORE_BONUS = 10.0
_CHILD_SCORE_BONUS = 5.0


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip()).lower()


def entity_clarify_margin() -> float:
    try:
        return float(os.environ.get("ZNWD4_ENTITY_CLARIFY_MARGIN") or "3")
    except ValueError:
        return 3.0


def entity_clarify_enabled() -> bool:
    raw = (os.environ.get("ZNWD4_ENTITY_CLARIFY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass
class DisambiguationResult:
    best: Optional[Dict[str, Any]]
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    scored_hits: List[Dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False
    reason: str = ""


def _equipment_type_label(hit: Dict[str, Any]) -> str:
    name = str(hit.get("name") or "").strip()
    parent = str(hit.get("type_value") or "").strip()
    if name and parent and parent not in name:
        return f"{parent} · {name}"
    return name or parent or str(hit.get("id") or "")


def _score_equipment_type_hit(
    hit: Dict[str, Any],
    *,
    question: str,
    parent_hint: str = "",
    child_hint: str = "",
) -> float:
    base = float(hit.get("score") or 0.0)
    name = str(hit.get("name") or "").strip()
    type_val = str(hit.get("type_value") or "").strip()
    qn = _norm(question)

    if parent_hint and type_val:
        ph = _norm(parent_hint)
        tv = _norm(type_val)
        if ph == tv or ph in tv or tv in ph:
            base += _PARENT_SCORE_BONUS
        elif ph in qn and (tv in qn or ph in tv):
            base += _PARENT_SCORE_BONUS * 0.8

    if child_hint and name:
        ch = _norm(child_hint)
        nm = _norm(name)
        if ch == nm or ch in nm or nm in ch:
            base += _CHILD_SCORE_BONUS

    if name and _norm(name) in qn:
        base += 1.0
    if type_val and _norm(type_val) in qn:
        base += 2.0

    return base


def disambiguate_equipment_type_hits(
    hits: List[Dict[str, Any]],
    *,
    question: str,
    parent_hint: str = "",
    child_hint: str = "",
) -> DisambiguationResult:
    et_hits = [h for h in hits if str(h.get("node_type") or "") == "EquipmentType"]
    if not et_hits:
        return DisambiguationResult(best=None)

    hf = parse_equipment_type_hierarchy(question)
    if not parent_hint and hf and hf.has_parent:
        parent_hint = hf.parent_type
    if not child_hint and hf and hf.child_name:
        child_hint = hf.child_name

    scored: List[Dict[str, Any]] = []
    for h in et_hits:
        sc = _score_equipment_type_hit(
            h,
            question=question,
            parent_hint=parent_hint,
            child_hint=child_hint,
        )
        scored.append({**h, "disambig_score": round(sc, 4)})

    scored.sort(key=lambda x: float(x.get("disambig_score") or 0), reverse=True)
    best = scored[0] if scored else None
    if not best:
        return DisambiguationResult(best=None)

    names = [str(h.get("name") or "").strip() for h in scored if str(h.get("name") or "").strip()]
    same_name = len(names) >= 2 and len(set(names)) == 1
    margin = entity_clarify_margin()
    ambiguous = False
    reason = ""

    if same_name and len(scored) >= 2:
        top = float(scored[0].get("disambig_score") or 0)
        second = float(scored[1].get("disambig_score") or 0)
        if (top - second) < margin:
            ambiguous = True
            reason = "same_name_low_margin"

    candidates = scored[:6] if ambiguous else []
    return DisambiguationResult(
        best=best,
        candidates=candidates,
        scored_hits=scored,
        ambiguous=ambiguous and entity_clarify_enabled(),
        reason=reason,
    )


def disambiguate_probe_snapshot(
    probe_snapshot: Dict[str, Any],
    *,
    question: str,
    parent_hint: str = "",
    child_hint: str = "",
) -> Dict[str, Any]:
    """Re-rank probe hits; update best and attach disambiguation metadata."""
    if not isinstance(probe_snapshot, dict):
        return probe_snapshot or {}
    hits = probe_snapshot.get("hits") if isinstance(probe_snapshot.get("hits"), list) else []
    if not hits:
        return probe_snapshot

    result = disambiguate_equipment_type_hits(
        hits,
        question=question,
        parent_hint=parent_hint,
        child_hint=child_hint,
    )
    out = dict(probe_snapshot)
    if result.best:
        out["best"] = result.best
        score_by_id = {
            str(h.get("id") or "").strip(): float(h.get("disambig_score") or 0)
            for h in result.scored_hits
            if str(h.get("id") or "").strip()
        }

        def _hit_rank(h: Dict[str, Any]) -> float:
            hid = str(h.get("id") or "").strip()
            if hid in score_by_id:
                return score_by_id[hid]
            return float(h.get("score") or 0)

        out["hits"] = sorted(hits, key=_hit_rank, reverse=True)
    out["entity_disambiguation"] = {
        "ambiguous": result.ambiguous,
        "reason": result.reason,
        "candidates": [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "type_value": c.get("type_value"),
                "label": _equipment_type_label(c),
                "disambig_score": c.get("disambig_score"),
            }
            for c in result.candidates
        ],
    }
    return out


__all__ = [
    "DisambiguationResult",
    "disambiguate_equipment_type_hits",
    "disambiguate_probe_snapshot",
    "entity_clarify_enabled",
    "entity_clarify_margin",
    "_equipment_type_label",
]
