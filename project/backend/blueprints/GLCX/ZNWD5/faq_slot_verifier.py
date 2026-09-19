"""
FAQ 程序锁路径：轻量 LLM 槽位校验（问句 + probe hits → slots_ok / corrected_slots）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from blueprints.GLCX.ZNWD4.probe_preflight import EvidenceBundle
from blueprints.GLCX.ZNWD4.question_route_lock import RouteLock
from blueprints.GLCX.ZNWD4.routing_catalog import get_route

logger = logging.getLogger(__name__)

_SLOT_KEYS = (
    "equipment_keywords",
    "environment_keywords",
    "project_keywords",
    "indicator_keywords",
    "method_keywords",
    "equipment_type_keywords",
    "standard_keywords",
)


def faq_slot_verify_enabled() -> bool:
    return os.environ.get("ZNWD5_FAQ_SLOT_VERIFY", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _lc_kwargs(client: Any, model: str, llm_limits: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    kw: Dict[str, Any] = {"model": model, "temperature": 0}
    if client:
        bu = getattr(client, "base_url", None)
        if bu:
            kw["base_url"] = str(bu)
        key = getattr(client, "api_key", None)
        if key:
            kw["api_key"] = key
    if llm_limits:
        if llm_limits.get("max_tokens"):
            kw["max_tokens"] = int(llm_limits["max_tokens"])
        if llm_limits.get("timeout"):
            kw["timeout"] = float(llm_limits["timeout"])
    return kw


def _extract_json(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _slots_from_evidence(evidence: EvidenceBundle) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    label_to_slot = {
        "Equipment": "equipment_keywords",
        "Environment": "environment_keywords",
        "TestProject": "project_keywords",
        "Indicator": "indicator_keywords",
        "TestMethod": "method_keywords",
        "EquipmentType": "equipment_type_keywords",
        "File": "standard_keywords",
    }
    for label, slot_key in label_to_slot.items():
        name = evidence.best_name(label)
        if name:
            out[slot_key] = [name]
    return out


def build_verify_prompt(
    question: str,
    lock: RouteLock,
    evidence: EvidenceBundle,
    draft_slots: Dict[str, Any],
) -> Tuple[str, str]:
    entry = get_route(lock.route_id)
    required = list(entry.required_slots if entry else [])
    hits_summary: List[str] = []
    for label, hit in (evidence.best_by_label or {}).items():
        hits_summary.append(
            f"- {label}: {hit.get('name')} (id={hit.get('id')}, score={hit.get('score')})"
        )
    draft_lines = []
    for k in _SLOT_KEYS:
        v = draft_slots.get(k)
        if v:
            draft_lines.append(f"  {k}: {v}")
    sys_msg = (
        "你是装备试验知识图谱槽位校验器。根据用户问句与 probe 命中，判断 draft 槽位是否正确。\n"
        "只输出 JSON：{\"ok\": true/false, \"slots\": {\"equipment_keywords\": [\"...\"], ...}}\n"
        "ok=true 表示 draft 槽位与问句意图一致；ok=false 时在 slots 给出修正（可留空 {}）。"
    )
    human = (
        f"问句：{question}\n"
        f"route_id：{lock.route_id}\n"
        f"required_slots：{required}\n"
        f"probe hits：\n" + ("\n".join(hits_summary) or "（无）") + "\n"
        f"draft slots：\n" + ("\n".join(draft_lines) or "  （空）")
    )
    return sys_msg, human


def _probe_confirms_draft_slots(
    lock: RouteLock,
    evidence: EvidenceBundle,
    draft_slots: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    """LLM 误判时：probe canonical 与 draft 一致则仍放行。"""
    if not getattr(evidence, "probe_ran", False):
        return False, draft_slots
    from blueprints.GLCX.ZNWD4.pcsr_assembly import _SLOT_TO_LABEL
    from blueprints.GLCX.ZNWD4.routing_catalog import get_route

    entry = get_route(lock.route_id)
    req = [s for s in (entry.required_slots if entry else []) if str(s).endswith("_keywords")]
    if not req:
        return False, draft_slots
    merged = dict(draft_slots)
    for sk in req:
        vals = merged.get(sk)
        if not isinstance(vals, list) or not any(str(x).strip() for x in vals):
            ev_vals = _slots_from_evidence(evidence).get(sk)
            if ev_vals:
                merged[sk] = ev_vals
                vals = ev_vals
            else:
                return False, merged
        label = _SLOT_TO_LABEL.get(sk, "")
        canonical = evidence.best_name(label) if label else ""
        if not canonical:
            return False, merged
        draft0 = str(vals[0]).strip()
        if canonical not in draft0 and draft0 not in canonical:
            merged[sk] = [canonical]
    if not merged.get("focus"):
        for sk in _SLOT_KEYS:
            vals = merged.get(sk)
            if isinstance(vals, list) and vals:
                merged["focus"] = str(vals[0])
                break
    return True, merged


def verify_faq_slots(
    client: Any,
    model: str,
    question: str,
    lock: RouteLock,
    evidence: EvidenceBundle,
    draft_slots: Dict[str, Any],
    *,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any], str]:
    """
    返回 (ok, merged_slots, raw_response)。
    LLM 不可用或解析失败时 ok=False。
    """
    if not faq_slot_verify_enabled():
        entry = get_route(lock.route_id)
        req = [s for s in (entry.required_slots if entry else []) if s.endswith("_keywords")]
        ok = all(
            isinstance(draft_slots.get(s), list) and any(str(x).strip() for x in draft_slots.get(s) or [])
            for s in req
        ) if req else True
        return ok, dict(draft_slots), ""

    sys_msg, human = build_verify_prompt(question, lock, evidence, draft_slots)
    try:
        llm = ChatOpenAI(**_lc_kwargs(client, model, llm_limits))
        resp = llm.invoke([SystemMessage(content=sys_msg), HumanMessage(content=human)])
        content = resp.content
        if isinstance(content, list):
            content = "".join(str(x) for x in content)
        raw = str(content or "")
        parsed = _extract_json(raw)
        ok = bool(parsed.get("ok"))
        corrected = parsed.get("slots") if isinstance(parsed.get("slots"), dict) else {}
        merged = dict(draft_slots)
        for k, v in corrected.items():
            if isinstance(v, list) and v:
                merged[k] = [str(x).strip() for x in v if str(x).strip()]
        if not merged.get("focus"):
            for sk in _SLOT_KEYS:
                vals = merged.get(sk)
                if isinstance(vals, list) and vals:
                    merged["focus"] = str(vals[0])
                    break
        if not ok:
            ev_ok, ev_merged = _probe_confirms_draft_slots(lock, evidence, merged)
            if ev_ok:
                ok = True
                merged = ev_merged
        return ok, merged, raw
    except Exception as exc:
        logger.warning("[ZNWD5][faq_verify] LLM failed: %s", exc)
        entry = get_route(lock.route_id)
        req = [s for s in (entry.required_slots if entry else []) if s.endswith("_keywords")]
        ok = all(
            isinstance(draft_slots.get(s), list) and any(str(x).strip() for x in draft_slots.get(s) or [])
            for s in req
        ) if req else bool(draft_slots.get("focus"))
        return ok, dict(draft_slots), ""


__all__ = [
    "build_verify_prompt",
    "faq_slot_verify_enabled",
    "verify_faq_slots",
]
