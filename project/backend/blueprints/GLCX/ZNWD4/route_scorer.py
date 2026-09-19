"""EFHR Step-2: Evidence-Constraint Scoring (ECS) over route catalog."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .probe_preflight import EvidenceBundle
from .question_route_lock import resolve_route_lock
from .routing_catalog import ROUTE_CATALOG, RouteEntry, guess_purpose_from_question, list_routes

_DEFAULT_WEIGHTS = {"pattern": 0.35, "type_align": 0.35, "schema": 0.15, "context": 0.15}

_MULTI_INTENT_GOALS = (
    (re.compile(r"(相关标准|依据标准|标准文件|适用标准|执行标准)"), "standards"),
    (re.compile(r"(试验方法|测试方法)"), "methods"),
    (re.compile(r"(什么|哪种|哪个|在哪).{0,8}(试验|测试)?环境"), "environment"),
    (re.compile(r"(考核|性能|试验)?指标"), "indicators"),
)


def looks_like_multi_intent_question(question: str) -> bool:
    """问句含多个独立检索目标时，不宜 ECS 单路由快路径。"""
    q = (question or "").strip()
    if not q:
        return False
    hits = sum(1 for pat, _ in _MULTI_INTENT_GOALS if pat.search(q))
    if hits >= 2:
        return True
    if re.search(r"[？?].+[？?]", q):
        return hits >= 1 and re.search(r"(相关标准|试验方法|测试方法)", q)
    return False


def _ecs_weights() -> Dict[str, float]:
    raw = (os.environ.get("EFHR_ECS_WEIGHTS") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {**_DEFAULT_WEIGHTS, **{k: float(v) for k, v in data.items()}}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return dict(_DEFAULT_WEIGHTS)


@dataclass
class RouteScore:
    route_id: str
    score: float
    breakdown: Dict[str, float] = field(default_factory=dict)
    reason: str = ""


def _pattern_score(question: str, entry: RouteEntry) -> float:
    q = (question or "").strip()
    purpose = entry.purpose
    guessed = guess_purpose_from_question(q)
    if purpose == guessed:
        return 1.0
    lock = resolve_route_lock(q)
    if lock and lock.route_id == entry.route_id:
        return 1.0
    if purpose in guessed or guessed in purpose:
        return 0.6
    return 0.0


def _type_align_score(evidence: EvidenceBundle, entry: RouteEntry) -> float:
    if not entry.keyword_nodes:
        return 0.5 if not evidence.best_by_label else 0.3
    if not evidence.best_by_label:
        return 0.0
    matched = sum(1 for lbl in entry.keyword_nodes if lbl in evidence.best_by_label)
    return matched / max(1, len(entry.keyword_nodes))


def _schema_score(evidence: EvidenceBundle, entry: RouteEntry) -> float:
    req = [s for s in (entry.required_slots or []) if s.endswith("_keywords")]
    if not req:
        return 0.5
    filled = 0
    for slot in req:
        label = slot.replace("_keywords", "").replace("equipment_type", "EquipmentType")
        label_map = {
            "equipment": "Equipment",
            "environment": "Environment",
            "project": "TestProject",
            "indicator": "Indicator",
            "method": "TestMethod",
            "standard": "File",
        }
        for prefix, neo_label in label_map.items():
            if slot.startswith(prefix):
                if neo_label in evidence.best_by_label:
                    filled += 1
                break
    return filled / max(1, len(req))


def _context_score(question: str, entry: RouteEntry) -> float:
    q = re.sub(r"\s+", "", question or "")
    title = entry.title or ""
    if not title:
        return 0.3
    hints = re.findall(r"[\u4e00-\u9fff]{2,6}", title)
    hit = sum(1 for h in hints if h in q)
    return min(1.0, hit / max(1, len(hints)))


def score_routes(
    question: str,
    evidence: EvidenceBundle,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    top_k: int = 3,
) -> List[RouteScore]:
    _ = history
    weights = _ecs_weights()
    scored: List[RouteScore] = []
    for entry in list_routes():
        breakdown = {
            "pattern": _pattern_score(question, entry),
            "type_align": _type_align_score(evidence, entry),
            "schema": _schema_score(evidence, entry),
            "context": _context_score(question, entry),
        }
        total = sum(breakdown[k] * weights.get(k, 0.0) for k in breakdown)
        scored.append(
            RouteScore(
                route_id=entry.route_id,
                score=round(total, 4),
                breakdown=breakdown,
                reason=f"ecs={total:.3f}",
            )
        )
    scored.sort(key=lambda x: (-x.score, x.route_id))
    return scored[: max(1, top_k)]


def should_abstain_from_llm(scores: List[RouteScore], margin: Optional[float] = None) -> bool:
    if not scores:
        return False
    m = margin
    if m is None:
        try:
            m = float(os.environ.get("EFHR_TAU2", "0.15"))
        except (TypeError, ValueError):
            m = 0.15
    top = scores[0].score
    second = scores[1].score if len(scores) > 1 else 0.0
    tau1 = float(os.environ.get("EFHR_TAU1", "0.72"))
    return top >= tau1 and (top - second) >= m


def get_route_entry(route_id: str) -> Optional[RouteEntry]:
    return ROUTE_CATALOG.get(route_id)


def should_use_ecs_fast_path(
    question: str,
    evidence: EvidenceBundle,
    scores: List[RouteScore],
) -> bool:
    """ECS top 高置信 + 槽位 probe 就绪。"""
    from .pcsr_assembly import slots_ready_for_route

    if looks_like_multi_intent_question(question):
        return False
    if not scores or not should_abstain_from_llm(scores):
        return False
    return slots_ready_for_route(scores[0].route_id, evidence)


__all__ = [
    "RouteScore",
    "score_routes",
    "should_abstain_from_llm",
    "should_use_ecs_fast_path",
    "get_route_entry",
    "looks_like_multi_intent_question",
]
