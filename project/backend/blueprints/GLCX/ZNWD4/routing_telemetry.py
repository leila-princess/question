"""ZNWD4 routing telemetry for incremental MLP retraining.

Label collection strategy (see module doc + EFHR/scripts/build_routebench_from_telemetry.py):

1. **Auto-gold (high confidence)**
   - L0 route_lock hits → gold route_id from lock
   - MLP confidence >= ZNWD4_MLP_AUTO_LABEL_CONF (default 0.92) AND retrieval neo_hits > 0

2. **Probe-oracle (batch)**
   - Offline: re-run probe; if canonical name matches slot focus → accept slots

3. **Implicit negative**
   - User rephrases within same session within 60s → mark prior route as candidate wrong
   - Zero neo/chroma hits on non-chat routes → needs_review

4. **Human gold**
   - Admin labels via review UI or exported JSONL `gold_route_id` field
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_DEFAULT_DIR = Path(__file__).resolve().parents[4] / "data" / "znwd4_routing_telemetry"


def telemetry_enabled() -> bool:
    raw = (os.environ.get("ZNWD4_ROUTING_TELEMETRY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def telemetry_dir() -> Path:
    raw = (os.environ.get("ZNWD4_ROUTING_TELEMETRY_DIR") or "").strip()
    if raw:
        return Path(raw)
    return _DEFAULT_DIR


def _auto_label_threshold() -> float:
    try:
        return float(os.environ.get("ZNWD4_MLP_AUTO_LABEL_CONF") or "0.92")
    except ValueError:
        return 0.92


def _today_log_path() -> Path:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return telemetry_dir() / f"routing_events_{day}.jsonl"


def log_routing_event(
    *,
    question: str,
    effective_question: str = "",
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    route_id: str = "",
    meta: Optional[Dict[str, Any]] = None,
    step1: Optional[Dict[str, Any]] = None,
    step2: Optional[Dict[str, Any]] = None,
    plan: Optional[Dict[str, Any]] = None,
    slots: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if not telemetry_enabled():
        return None

    event_id = str(uuid.uuid4())
    mlp_conf = (meta or {}).get("mlp_confidence")
    label_status = "pending"
    label_source = None
    gold_route_id = None

    if (meta or {}).get("route_lock") or (meta or {}).get("tier") == "L0":
        label_status = "auto_gold"
        label_source = "route_lock"
        gold_route_id = route_id
    elif isinstance(mlp_conf, (int, float)) and mlp_conf >= _auto_label_threshold():
        label_status = "auto_candidate"
        label_source = "mlp_high_conf"
    elif (meta or {}).get("user_route_choice") or (meta or {}).get("label_source") == "user_choice":
        label_status = "auto_gold"
        label_source = "user_choice"
        gold_route_id = str((meta or {}).get("gold_route_id") or route_id)
    elif (meta or {}).get("user_answer_correction") or (meta or {}).get("label_source") == "user_answer_correction":
        label_status = "auto_gold"
        label_source = "user_answer_correction"
        gold_route_id = str((meta or {}).get("gold_route_id") or route_id)

    record: Dict[str, Any] = {
        "event_id": event_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "effective_question": effective_question or question,
        "user_id": user_id or "anonymous",
        "session_id": session_id or "",
        "routing_mode": (os.environ.get("ZNWD4_ROUTING_MODE") or "langchain").strip(),
        "predicted_route_id": route_id,
        "predicted_slots": slots or (step2 or {}).get("params_draft") or {},
        "mlp_confidence": mlp_conf,
        "meta": meta or {},
        "step1": step1 or {},
        "step2": step2 or {},
        "plan_route_id": (plan or {}).get("route_id") or route_id,
        "label_status": label_status,
        "label_source": label_source,
        "gold_route_id": gold_route_id,
        "gold_slots": None,
        "execution": None,
    }

    try:
        path = _today_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        logger.debug("[ZNWD4][telemetry] logged %s route=%s", event_id[:8], route_id)
        return event_id
    except Exception as e:
        logger.warning("[ZNWD4][telemetry] write failed: %s", e)
        return None


def log_execution_outcome(
    *,
    session_id: str,
    question: str,
    neo_hits: int = 0,
    chroma_hits: int = 0,
    answer_strategy: str = "",
    business_id: str = "",
) -> None:
    """Append execution outcome line (linked by session_id + question)."""
    if not telemetry_enabled():
        return
    record = {
        "type": "execution_outcome",
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id or "",
        "question": question,
        "neo_hits": neo_hits,
        "chroma_hits": chroma_hits,
        "answer_strategy": answer_strategy,
        "business_id": business_id,
        "implicit_ok": neo_hits > 0 or chroma_hits > 0 or answer_strategy == "direct_llm",
    }
    try:
        path = _today_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("[ZNWD4][telemetry] execution log failed: %s", e)


def _append_record(record: Dict[str, Any]) -> None:
    path = _today_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def log_routing_clarify_prompt(
    *,
    payload: Dict[str, Any],
    question: str,
    effective_question: str = "",
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    if not telemetry_enabled():
        return
    record = {
        "type": "route_clarify_prompt",
        "ts": datetime.now(timezone.utc).isoformat(),
        "clarify_event_id": payload.get("clarifyEventId"),
        "question": question,
        "effective_question": effective_question or question,
        "user_id": user_id or "anonymous",
        "session_id": session_id or "",
        "prompt": payload.get("prompt"),
        "options": payload.get("options") or [],
        "meta": meta or {},
        "label_status": "awaiting_user",
    }
    try:
        _append_record(record)
    except Exception as e:
        logger.warning("[ZNWD4][telemetry] clarify prompt log failed: %s", e)


def answer_feedback_enabled() -> bool:
    from .answer_correction import answer_feedback_enabled as _fn

    return _fn()


def log_answer_feedback(
    *,
    turn_id: str,
    routing_event_id: str = "",
    rating: str = "negative",
    feedback_kind: str = "",
    question: str = "",
    predicted_route_id: str = "",
    predicted_slots: Optional[Dict[str, Any]] = None,
    corrected_route_id: str = "",
    corrected_slots: Optional[Dict[str, Any]] = None,
    comment: str = "",
    answer_preview: str = "",
    rerun_requested: bool = False,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[str]:
    if not telemetry_enabled() or not answer_feedback_enabled():
        return None

    feedback_id = str(uuid.uuid4())
    preview = (answer_preview or "").strip()
    if len(preview) > 500:
        preview = preview[:500]

    record: Dict[str, Any] = {
        "type": "answer_feedback",
        "feedback_id": feedback_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "turn_id": turn_id,
        "routing_event_id": routing_event_id or "",
        "rating": rating,
        "feedback_kind": feedback_kind or "",
        "question": question,
        "user_id": user_id or "anonymous",
        "session_id": session_id or "",
        "predicted_route_id": predicted_route_id or "",
        "predicted_slots": predicted_slots or {},
        "corrected_route_id": corrected_route_id or "",
        "corrected_slots": corrected_slots or {},
        "comment": (comment or "").strip(),
        "answer_preview": preview,
        "rerun_requested": bool(rerun_requested),
    }
    try:
        _append_record(record)
        return feedback_id
    except Exception as e:
        logger.warning("[ZNWD4][telemetry] answer feedback log failed: %s", e)
        return None


def log_answer_correction_rerun(
    *,
    turn_id: str,
    routing_event_id: str = "",
    new_routing_event_id: str = "",
    question: str = "",
    gold_route_id: str = "",
    gold_slots: Optional[Dict[str, Any]] = None,
    feedback_kind: str = "",
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    if not telemetry_enabled() or not answer_feedback_enabled():
        return

    ts = datetime.now(timezone.utc).isoformat()
    slots = gold_slots if isinstance(gold_slots, dict) else {}
    record: Dict[str, Any] = {
        "type": "answer_correction_rerun",
        "ts": ts,
        "turn_id": turn_id,
        "routing_event_id": routing_event_id or "",
        "new_routing_event_id": new_routing_event_id or "",
        "question": question,
        "user_id": user_id or "anonymous",
        "session_id": session_id or "",
        "feedback_kind": feedback_kind or "",
        "gold_route_id": gold_route_id or "",
        "gold_slots": slots,
        "label_status": "auto_gold",
        "label_source": "user_answer_correction",
    }
    try:
        _append_record(record)
        if routing_event_id:
            _append_record(
                {
                    "type": "routing_gold_patch",
                    "ts": ts,
                    "parent_event_id": routing_event_id,
                    "gold_route_id": gold_route_id or "",
                    "gold_slots": slots,
                    "label_status": "auto_gold",
                    "label_source": "user_answer_correction",
                    "turn_id": turn_id,
                }
            )
    except Exception as e:
        logger.warning("[ZNWD4][telemetry] answer correction rerun log failed: %s", e)


def log_result_guard_retry(
    *,
    question: str = "",
    action: str = "",
    anchor: str = "",
    before_count: int = 0,
    after_count: int = 0,
    before_rate: float = 0.0,
    after_rate: float = 0.0,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    if not telemetry_enabled():
        return
    record: Dict[str, Any] = {
        "type": "result_guard_retry",
        "ts": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "user_id": user_id or "anonymous",
        "session_id": session_id or "",
        "action": action or "",
        "anchor": anchor or "",
        "before_count": before_count,
        "after_count": after_count,
        "before_rate": before_rate,
        "after_rate": after_rate,
        "meta": meta or {},
    }
    try:
        _append_record(record)
    except Exception as e:
        logger.warning("[ZNWD4][telemetry] result guard log failed: %s", e)


def log_user_route_choice(
    *,
    clarify_event_id: str,
    gold_route_id: str,
    question: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    selected_label: str = "",
) -> None:
    if not telemetry_enabled():
        return
    record = {
        "type": "route_clarify_choice",
        "ts": datetime.now(timezone.utc).isoformat(),
        "clarify_event_id": clarify_event_id,
        "question": question,
        "user_id": user_id or "anonymous",
        "session_id": session_id or "",
        "gold_route_id": gold_route_id,
        "selected_label": selected_label,
        "label_status": "auto_gold",
        "label_source": "user_choice",
    }
    try:
        _append_record(record)
    except Exception as e:
        logger.warning("[ZNWD4][telemetry] user choice log failed: %s", e)


__all__ = [
    "log_routing_event",
    "log_execution_outcome",
    "log_routing_clarify_prompt",
    "log_user_route_choice",
    "log_answer_feedback",
    "log_answer_correction_rerun",
    "answer_feedback_enabled",
    "telemetry_enabled",
    "telemetry_dir",
]
