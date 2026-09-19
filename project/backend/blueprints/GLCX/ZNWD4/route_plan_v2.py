"""ZNWD4 分步路由 JSON 解析与 legacy RoutePlan 归一化。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .routing_catalog import PURPOSE_CHAT, get_route, resolve_route

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I | re.M)


def _strip_json(raw: str) -> str:
    s = (raw or "").strip()
    s = _JSON_FENCE.sub("", s).strip()
    m = re.search(r"\{[\s\S]*\}", s)
    return m.group(0) if m else s


def _loads(raw: str) -> Dict[str, Any]:
    try:
        obj = json.loads(_strip_json(raw))
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def parse_step1_json(raw: str) -> Dict[str, Any]:
    obj = _loads(raw)
    purpose = str(obj.get("purpose") or "").strip()
    purpose_zh = str(obj.get("purpose_zh") or "").strip()
    keywords = obj.get("keywords") if isinstance(obj.get("keywords"), list) else []
    keywords = [str(x).strip() for x in keywords if str(x).strip()]
    kw = str(obj.get("keyword") or (keywords[0] if keywords else "")).strip()
    if kw and kw not in keywords:
        keywords = [kw] + keywords
    use_history = bool(obj.get("use_history"))
    return {
        "step": 1,
        "purpose": purpose,
        "purpose_zh": purpose_zh,
        "keywords": keywords,
        "keyword": kw or (keywords[0] if keywords else ""),
        "use_history": use_history,
    }


def _parse_confidence(raw: Any) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        v = float(raw)
    else:
        s = str(raw).strip().lower()
        if s in ("high", "高", "h"):
            return 0.92
        if s in ("medium", "中", "m"):
            return 0.75
        if s in ("low", "低", "l"):
            return 0.45
        try:
            v = float(s)
        except ValueError:
            return 0.0
    return max(0.0, min(1.0, v))


def _parse_route_candidates(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("route_id") or "").strip()
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append(
            {
                "route_id": rid,
                "confidence": _parse_confidence(item.get("confidence")),
                "reason": str(item.get("reason") or item.get("match_reason") or "").strip(),
            }
        )
    out.sort(key=lambda x: x["confidence"], reverse=True)
    return out


def parse_step2_json(raw: str) -> Dict[str, Any]:
    obj = _loads(raw)
    route_id = str(obj.get("route_id") or "").strip()
    params = obj.get("params_draft") if isinstance(obj.get("params_draft"), dict) else {}
    keywords = obj.get("keywords") if isinstance(obj.get("keywords"), list) else []
    keywords = [str(x).strip() for x in keywords if str(x).strip()]
    focus = str(obj.get("focus") or params.get("focus") or (keywords[0] if keywords else "")).strip()
    confidence = _parse_confidence(obj.get("confidence"))
    route_candidates = _parse_route_candidates(obj.get("route_candidates"))
    if route_id and not route_candidates:
        route_candidates = [
            {
                "route_id": route_id,
                "confidence": confidence if confidence > 0 else 0.85,
                "reason": str(obj.get("match_reason") or "").strip(),
            }
        ]
    elif route_id and route_candidates and route_candidates[0].get("route_id") != route_id:
        route_candidates.insert(
            0,
            {
                "route_id": route_id,
                "confidence": confidence if confidence > 0 else route_candidates[0]["confidence"],
                "reason": str(obj.get("match_reason") or "").strip(),
            },
        )
    if route_candidates and confidence <= 0:
        confidence = float(route_candidates[0].get("confidence") or 0.0)
    return {
        "step": 2,
        "route_id": route_id,
        "params_draft": params,
        "keywords": keywords,
        "focus": focus,
        "match_reason": str(obj.get("match_reason") or "").strip(),
        "confidence": confidence,
        "route_candidates": route_candidates,
    }


def normalize_to_legacy_plan(
    v2: Dict[str, Any],
    *,
    question: str = "",
    retrieval_question: str = "",
) -> Dict[str, Any]:
    """RoutePlanV2 → ZNWD1 兼容 plan（供 plan_reconcile / expand_playbook）。"""
    route_id = str(v2.get("route_id") or "").strip()
    resolved = resolve_route(route_id) or {}
    rq = str(v2.get("retrieval_question") or retrieval_question or question).strip()
    kw = str(v2.get("keyword") or "").strip() or rq[:80]
    params = v2.get("params_draft") if isinstance(v2.get("params_draft"), dict) else {}

    if resolved.get("direct_llm") or v2.get("purpose") == PURPOSE_CHAT:
        return {
            "route_id": route_id or "R-CHAT-direct",
            "purpose": v2.get("purpose") or PURPOSE_CHAT,
            "answer_strategy": "direct_llm",
            "retrieval_question": rq,
            "keyword": kw,
            "params_draft": params,
            "probe_snapshot": v2.get("probe_snapshot") if isinstance(v2.get("probe_snapshot"), dict) else {},
            "match_confidence": "high",
        }

    plan: Dict[str, Any] = {
        "route_id": route_id,
        "purpose": v2.get("purpose") or resolved.get("purpose", ""),
        "user_biz": resolved.get("user_biz", ""),
        "scenario": str(v2.get("scenario") or resolved.get("scenario", "default")),
        "match_confidence": str(v2.get("match_confidence") or resolved.get("match_confidence") or "high"),
        "answer_strategy": str(v2.get("answer_strategy") or resolved.get("answer_strategy") or "db_synthesize"),
        "answer_mode": "database_synthesize"
        if str(v2.get("answer_strategy") or resolved.get("answer_strategy") or "") == "db_synthesize"
        else "database_direct",
        "retrieval_question": rq,
        "keyword": kw,
        "params_draft": params,
        "probe_snapshot": v2.get("probe_snapshot") if isinstance(v2.get("probe_snapshot"), dict) else {},
        "use_history": bool(v2.get("use_history")),
    }
    if v2.get("history_snapshot"):
        plan["history_snapshot"] = v2["history_snapshot"]
    if not v2.get("scenario") and question:
        from .question_route_lock import resolve_route_lock

        lock = resolve_route_lock(question)
        if lock and lock.extra.get("scenario"):
            plan["scenario"] = str(lock.extra["scenario"])
    return plan


def merge_slots_from_keywords(step1: Dict[str, Any], step2: Dict[str, Any], route_id: str) -> Dict[str, Any]:
    entry = get_route(route_id)
    params = dict(step2.get("params_draft") or {})
    kws = step2.get("keywords") or step1.get("keywords") or []
    if not isinstance(kws, list):
        kws = [str(kws)]
    kws = [str(x).strip() for x in kws if str(x).strip()]
    focus = str(step2.get("focus") or step1.get("keyword") or (kws[0] if kws else "")).strip()
    purpose = str(entry.purpose or "") if entry else ""
    if entry:
        if purpose == "composite_equip_project_env_settings":
            if not params.get("equipment_keywords") and kws:
                params["equipment_keywords"] = kws[:1] if len(kws) == 1 else list(kws)
        else:
            for slot in entry.required_slots:
                if slot.endswith("_keywords") and not params.get(slot):
                    if len(kws) == 1:
                        params[slot] = kws
                    elif kws:
                        params[slot] = kws
    if focus:
        params.setdefault("focus", focus)
    return params
