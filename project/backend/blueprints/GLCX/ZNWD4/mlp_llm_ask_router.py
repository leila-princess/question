"""MLP route + LLM per-slot ask + PCSR (production router_mlp_llm_ask)."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .efhr_router import _lock_to_prediction, pcsr_slots
from .learned_router_bundle import load_mlp_bundle, predict_mlp_route
from .pcsr_assembly import build_evidence_for_question
from .probe_preflight import build_evidence_bundle
from .prompts.mlp_slot_ask import SLOT_TO_LABEL, build_route_slot_ask_prompt
from .question_route_lock import (
    RouteLock,
    apply_route_lock_to_step1,
    build_locked_step2,
    resolve_route_lock,
    skip_llm_for_lock,
)
from .routing_catalog import PURPOSE_CHAT, get_route, guess_purpose_from_question

logger = logging.getLogger(__name__)

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


def _llm_chat(
    client: Any,
    model: str,
    system: str,
    user: str,
    *,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Tuple[str, int]:
    max_tokens = 1024
    if llm_limits:
        try:
            max_tokens = min(int(llm_limits.get("max_output_tokens") or 1024), 1024)
        except (TypeError, ValueError):
            pass
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip(), 1


def slots_from_slot_guided(step2: Dict[str, Any], route_id: str) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    focus = str(step2.get("focus") or "").strip()
    assignments = step2.get("slot_assignments")
    if isinstance(assignments, list):
        for item in assignments:
            if not isinstance(item, dict):
                continue
            span = str(item.get("surface_span") or item.get("span") or "").strip()
            if not span:
                continue
            sk = str(item.get("slot_key") or "").strip()
            if not sk:
                lbl = str(item.get("node_label") or "").strip()
                sk = SLOT_TO_LABEL.get(lbl, "")
            if not sk.endswith("_keywords"):
                continue
            params.setdefault(sk, [])
            if span not in params[sk]:
                params[sk].append(span)
            if not focus:
                focus = span
    if focus:
        params["focus"] = focus
    entry = get_route(route_id)
    if entry:
        for slot in entry.required_slots:
            if slot.endswith("_keywords") and slot not in params:
                params[slot] = []
    return params


def _pcsr_finalize(
    question: str,
    route_id: str,
    raw_slots: Dict[str, Any],
    *,
    driver: Any,
    lock: Optional[RouteLock] = None,
) -> Tuple[str, str, Dict[str, Any], bool]:
    entry = get_route(route_id)
    purpose = entry.purpose if entry else guess_purpose_from_question(question)
    if purpose == PURPOSE_CHAT or route_id == "R-CHAT-direct":
        return "R-CHAT-direct", PURPOSE_CHAT, {}, False
    hints = list(entry.keyword_nodes) if entry else None
    evidence = build_evidence_bundle(
        question, driver=driver, label_hints=lock.probe_labels if lock else hints
    )
    slots = pcsr_slots(route_id=route_id, lock=lock, evidence=evidence, raw_slots=raw_slots)
    return route_id, purpose, slots, bool(evidence.probe_ran)


def route_question_mlp_llm_ask(
    question: str,
    *,
    client: Any = None,
    model: str = "",
    driver: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
    bundle: Optional[Dict[str, Any]] = None,
    user_route_id: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    MLP+LLM-ask routing pipeline.

    Returns (route_id, purpose, slots, meta, step1, step2) for plan assembly & telemetry.
    """
    t0 = time.perf_counter()
    llm_calls = 0
    q = (question or "").strip()
    bundle = bundle or load_mlp_bundle()

    lock = resolve_route_lock(q)
    if lock and skip_llm_for_lock(lock):
        evidence = build_evidence_for_question(q, driver=driver, lock=lock)
        rid, purpose, slots, meta = _lock_to_prediction(lock, evidence)
        meta["system"] = "mlp_llm_ask"
        meta["tier"] = "L0"
        meta["mlp_route"] = rid
        meta["mlp_confidence"] = 1.0
        meta["llm_calls"] = 0
        meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        step1 = apply_route_lock_to_step1({}, lock, q)
        step2 = build_locked_step2(lock)
        step2["params_draft"] = slots
        step2["focus"] = str(slots.get("focus") or "")
        return rid, purpose, slots, meta, step1, step2

    rid, mlp_conf = predict_mlp_route(q, bundle)
    if user_route_id:
        rid = str(user_route_id).strip()
        mlp_conf = 1.0
    entry = get_route(rid)
    if not entry:
        meta = {
            "tier": "learned",
            "llm_calls": 0,
            "probe_ran": False,
            "system": "mlp_llm_ask",
            "mlp_route": rid,
            "mlp_confidence": round(mlp_conf, 4),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        return rid, "", {}, meta, {}, {"route_id": rid}

    if entry.purpose == PURPOSE_CHAT:
        meta = {
            "tier": "learned",
            "llm_calls": 0,
            "probe_ran": False,
            "system": "mlp_llm_ask",
            "mlp_route": rid,
            "mlp_confidence": round(mlp_conf, 4),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        step1 = {"purpose": PURPOSE_CHAT, "purpose_zh": "闲聊", "keywords": []}
        step2 = {"route_id": "R-CHAT-direct", "params_draft": {}, "keywords": []}
        return "R-CHAT-direct", PURPOSE_CHAT, {}, meta, step1, step2

    if lock:
        evidence = build_evidence_for_question(q, driver=driver, lock=lock)
        rid, purpose, slots, meta = _lock_to_prediction(lock, evidence)
        meta["system"] = "mlp_llm_ask"
        meta["tier"] = "L0+MLP"
        meta["mlp_route"] = predict_mlp_route(q, bundle)[0]
        meta["mlp_confidence"] = round(mlp_conf, 4)
        meta["llm_calls"] = 0
        meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        step1 = apply_route_lock_to_step1({}, lock, q)
        step2 = build_locked_step2(lock)
        step2["params_draft"] = slots
        step2["focus"] = str(slots.get("focus") or "")
        return rid, purpose, slots, meta, step1, step2

    step2_llm: Dict[str, Any] = {}
    if client and model:
        sys_prompt = build_route_slot_ask_prompt(entry)
        user_prompt = (
            f"用户问句：\n{q}\n\n"
            f"固定 route_id={rid}，请按槽位反问找出 surface_span，不要修改 route。"
        )
        try:
            raw, n = _llm_chat(client, model, sys_prompt, user_prompt, llm_limits=llm_limits)
            llm_calls += n
            step2_llm = _loads(raw)
        except Exception as e:
            logger.warning("[ZNWD4][mlp_llm_ask] LLM slot ask failed: %s", e)
            step2_llm = {}

    if not step2_llm.get("route_id"):
        step2_llm["route_id"] = rid
    raw_slots = slots_from_slot_guided(step2_llm, rid)
    rid2, purpose, slots, probe_ran = _pcsr_finalize(q, rid, raw_slots, driver=driver)

    from .routing_catalog import _purpose_zh

    step1 = {
        "purpose": purpose,
        "purpose_zh": _purpose_zh(purpose),
        "keywords": list(slots.get("focus") and [slots["focus"]] or []),
    }
    kws: List[str] = []
    for k, v in slots.items():
        if k.endswith("_keywords") and isinstance(v, list):
            kws.extend(str(x) for x in v if str(x).strip())
    step2 = {
        "route_id": rid2,
        "params_draft": dict(slots),
        "keywords": kws,
        "focus": str(slots.get("focus") or ""),
        "match_reason": str(step2_llm.get("match_reason") or "MLP+槽位反问"),
        "slot_assignments": step2_llm.get("slot_assignments"),
    }

    meta = {
        "tier": "learned+llm",
        "llm_calls": llm_calls,
        "probe_ran": probe_ran,
        "system": "mlp_llm_ask",
        "mlp_route": rid,
        "mlp_confidence": round(mlp_conf, 4),
        "user_route_choice": bool(user_route_id),
        "llm_step2": step2_llm,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    return rid2, purpose, slots, meta, step1, step2


__all__ = ["route_question_mlp_llm_ask", "slots_from_slot_guided"]
