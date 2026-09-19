"""Low-confidence route disambiguation: candidate selection + user-facing payload."""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .learned_router_bundle import load_mlp_bundle, predict_mlp_route_topk
from .probe_preflight import build_evidence_bundle
from .route_choice_nlg import build_clarify_prompt, phrase_route_option
from .route_scorer import score_routes
from .routing_catalog import PURPOSE_CHAT, get_route

ROUTE_CLARIFY_EVENT = "route_clarify"


def route_clarify_enabled() -> bool:
    raw = (os.environ.get("ZNWD4_ROUTE_CLARIFY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _clarify_threshold() -> float:
    try:
        raw = os.environ.get("ZNWD4_LLM_CLARIFY_THRESHOLD") or os.environ.get("ZNWD4_MLP_CLARIFY_THRESHOLD") or "0.82"
        return float(raw)
    except ValueError:
        return 0.82


def _clarify_margin() -> float:
    try:
        raw = os.environ.get("ZNWD4_LLM_CLARIFY_MARGIN") or os.environ.get("ZNWD4_MLP_CLARIFY_MARGIN") or "0.06"
        return float(raw)
    except ValueError:
        return 0.06


def _max_options() -> int:
    try:
        return max(2, min(5, int(os.environ.get("ZNWD4_ROUTE_CLARIFY_MAX_OPTIONS") or "4")))
    except ValueError:
        return 4


def needs_route_clarify(topk: List[Tuple[str, float]]) -> bool:
    if not route_clarify_enabled() or len(topk) < 2:
        return False
    top_conf = topk[0][1]
    second_conf = topk[1][1]
    if top_conf < _clarify_threshold():
        return True
    if (top_conf - second_conf) < _clarify_margin():
        return True
    return False


def topk_from_step2(step2: Dict[str, Any]) -> List[Tuple[str, float]]:
    """从 Step2 LLM JSON 提取 (route_id, confidence) 排序列表。"""
    if not isinstance(step2, dict):
        return []
    cands = step2.get("route_candidates")
    if isinstance(cands, list) and cands:
        out: List[Tuple[str, float]] = []
        seen: set = set()
        for item in cands:
            if not isinstance(item, dict):
                continue
            rid = str(item.get("route_id") or "").strip()
            if not rid or rid in seen:
                continue
            seen.add(rid)
            try:
                conf = float(item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            out.append((rid, max(0.0, min(1.0, conf))))
        out.sort(key=lambda x: x[1], reverse=True)
        if out:
            return out
    rid = str(step2.get("route_id") or "").strip()
    if not rid:
        return []
    try:
        conf = float(step2.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf <= 0:
        conf = 0.85
    return [(rid, conf)]


def needs_llm_route_clarify(step2: Dict[str, Any]) -> bool:
    return needs_route_clarify(topk_from_step2(step2))


def _merge_candidates(
    question: str,
    seed_topk: List[Tuple[str, float]],
    *,
    driver: Any,
    max_options: int,
) -> List[Tuple[str, float]]:
    seen: set = set()
    merged: List[Tuple[str, float]] = []
    for rid, conf in seed_topk:
        if rid and rid not in seen:
            seen.add(rid)
            merged.append((rid, conf))
    if len(merged) >= max_options:
        return merged[:max_options]

    evidence = build_evidence_bundle(question, driver=driver)
    for sc in score_routes(question, evidence, top_k=max_options):
        if sc.route_id not in seen:
            seen.add(sc.route_id)
            merged.append((sc.route_id, sc.score))
        if len(merged) >= max_options:
            break
    return merged[:max_options]


def _build_clarify_payload_from_topk(
    question: str,
    topk: List[Tuple[str, float]],
    *,
    source: str,
) -> Dict[str, Any]:
    options: List[Dict[str, Any]] = []
    for idx, (rid, conf) in enumerate(topk):
        entry = get_route(rid)
        if not entry or entry.purpose == PURPOSE_CHAT:
            continue
        options.append(
            {
                "id": f"opt_{idx + 1}",
                "routeId": rid,
                "label": phrase_route_option(question, rid, entry),
                "confidence": round(float(conf), 4),
            }
        )

    if len(options) < 2:
        return {}

    event_id = str(uuid.uuid4())
    top_field = "llmTop" if source == "llm" else "mlpTop"
    return {
        "clarifyEventId": event_id,
        "originalQuestion": question,
        "prompt": build_clarify_prompt(question),
        "options": options[: _max_options()],
        top_field: [{"routeId": r, "confidence": round(c, 4)} for r, c in topk[:3]],
        "clarifySource": source,
    }


def build_route_clarify_payload(
    question: str,
    *,
    driver: Any = None,
    mlp_topk: Optional[List[Tuple[str, float]]] = None,
) -> Dict[str, Any]:
    bundle = load_mlp_bundle()
    topk = mlp_topk or predict_mlp_route_topk(question, bundle, top_k=_max_options())
    candidates = _merge_candidates(question, topk, driver=driver, max_options=_max_options())
    return _build_clarify_payload_from_topk(question, candidates, source="mlp")


def build_route_clarify_payload_from_llm(
    question: str,
    step2: Dict[str, Any],
    *,
    driver: Any = None,
) -> Dict[str, Any]:
    topk = topk_from_step2(step2)
    candidates = _merge_candidates(question, topk, driver=driver, max_options=_max_options())
    if len(candidates) < 2:
        return {}
    if not needs_route_clarify(candidates):
        return {}
    return _build_clarify_payload_from_topk(question, candidates, source="llm")


__all__ = [
    "ROUTE_CLARIFY_EVENT",
    "route_clarify_enabled",
    "needs_route_clarify",
    "needs_llm_route_clarify",
    "topk_from_step2",
    "build_route_clarify_payload",
    "build_route_clarify_payload_from_llm",
]
