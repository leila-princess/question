"""
LLM 一次性意图锁：purpose + route_id + keywords → RouteLock。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .prompts.llm_route_lock_prompt import (
    build_llm_route_lock_system_prompt,
    build_llm_route_lock_user_prompt,
)
from .question_route_lock import RouteLock
from .routing_catalog import get_route
from .routing_context import get_routing_llm_context, record_llm_call
from .znwd4_experiment_config import route_lock_llm_min_confidence

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I | re.M)

_LABEL_TO_SLOT: Dict[str, str] = {
    "Equipment": "equipment_keywords",
    "EquipmentType": "equipment_type_keywords",
    "Environment": "environment_keywords",
    "TestProject": "project_keywords",
    "Indicator": "indicator_keywords",
    "TestMethod": "method_keywords",
    "File": "standard_keywords",
}


def _strip_json(raw: str) -> str:
    s = (raw or "").strip()
    s = _JSON_FENCE.sub("", s).strip()
    m = re.search(r"\{[\s\S]*\}", s)
    return m.group(0) if m else s


def _parse_llm_lock_json(raw: str) -> Dict[str, Any]:
    try:
        obj = json.loads(_strip_json(raw))
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _normalize_keywords(kws: Any, *, limit: int = 8) -> List[str]:
    if not isinstance(kws, list):
        if isinstance(kws, str) and kws.strip():
            return [kws.strip()]
        return []
    out: List[str] = []
    seen: set = set()
    for x in kws:
        s = str(x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _pick_slot_key(entry: Any, draft_slot: str) -> str:
    slot = str(draft_slot or "").strip()
    required = list(getattr(entry, "required_slots", None) or [])
    if slot and slot in required:
        return slot
    if required:
        return required[0]
    nodes = list(getattr(entry, "keyword_nodes", None) or [])
    if nodes:
        return _LABEL_TO_SLOT.get(nodes[0], "")
    return slot


def _llm_lock_to_route_lock(obj: Dict[str, Any], question: str) -> Optional[RouteLock]:
    route_id = str(obj.get("route_id") or "").strip()
    if not route_id:
        return None
    entry = get_route(route_id)
    if not entry:
        logger.info("[ZNWD4][llm_route_lock] unknown route_id=%s", route_id)
        return None

    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    min_conf = route_lock_llm_min_confidence()
    if route_id != "R-CHAT-direct" and confidence < min_conf:
        logger.info(
            "[ZNWD4][llm_route_lock] low confidence %.2f < %.2f route=%s",
            confidence,
            min_conf,
            route_id,
        )
        return None

    keywords = _normalize_keywords(obj.get("keywords"))
    slot_key = _pick_slot_key(entry, str(obj.get("slot_key") or ""))
    purpose = str(obj.get("purpose") or entry.purpose or "").strip()
    reason = str(obj.get("reason") or "llm_route_lock").strip() or "llm_route_lock"

    extra: Dict[str, Any] = {}
    if route_id == "R-CHAT-direct":
        keywords = []
        slot_key = ""

    return RouteLock(
        purpose=purpose or entry.purpose,
        route_id=route_id,
        probe_labels=list(entry.keyword_nodes or []),
        slot_key=slot_key,
        focus=keywords[0] if keywords else "",
        reason=reason,
        keywords=list(keywords),
        extra=extra,
    )


def resolve_route_lock_llm(question: str) -> Optional[RouteLock]:
    """单次 LLM 识别意图锁；无 routing_context 时返回 None（由 resolver 回退 rules）。"""
    try:
        q = str(question or "").strip()
    except Exception:
        return None
    if not q:
        return None

    ctx = get_routing_llm_context()
    if not ctx or not ctx.client or not ctx.model:
        return None

    sys_prompt = build_llm_route_lock_system_prompt()
    user_prompt = build_llm_route_lock_user_prompt(q)
    max_tokens = 512
    if ctx.llm_limits:
        try:
            max_tokens = min(int(ctx.llm_limits.get("max_output_tokens") or 512), 768)
        except (TypeError, ValueError):
            pass

    try:
        record_llm_call("route_lock")
        resp = ctx.client.chat.completions.create(
            model=ctx.model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=max_tokens,
        )
        raw = ""
        if resp and resp.choices:
            choice = resp.choices[0]
            if choice and choice.message:
                raw = (choice.message.content or "").strip()
    except Exception as e:
        logger.warning("[ZNWD4][llm_route_lock] LLM call failed: %s", e)
        return None

    obj = _parse_llm_lock_json(raw)
    if not obj:
        logger.info("[ZNWD4][llm_route_lock] parse failed raw=%r", raw[:200])
        return None

    lock = _llm_lock_to_route_lock(obj, q)
    if lock:
        logger.info(
            "[ZNWD4][llm_route_lock] hit route=%s keywords=%s conf=%s",
            lock.route_id,
            lock.keywords,
            obj.get("confidence"),
        )
    return lock
