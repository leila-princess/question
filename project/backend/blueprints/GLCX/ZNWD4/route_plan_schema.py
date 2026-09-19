"""
RoutePlan JSON：路由 Agent 最终输出结构（合并 Gate / Focus / Planner 意图）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_ROUTE_PLAN_KEYS = (
    "answer_strategy",
    "use_history",
    "retrieval_question",
    "keyword",
    "guessed_node_types",
    "user_goal",
    "intended_business_id",
    "answer_mode",
    "params_draft",
    "probe_snapshot",
    "purpose_node",
    "biz_type",
    "known_type",
    "hop_direction",
    "biz_key",
    "history_snapshot",
    "user_biz",
    "scenario",
    "match_confidence",
)


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def parse_route_plan_json(raw: str) -> Dict[str, Any]:
    """从路由 Agent 最后一轮 assistant 文本解析 RoutePlan。"""
    s = _strip_json_fence(raw)
    if not s:
        return _default_plan()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if not m:
            return _default_plan()
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return _default_plan()
    if not isinstance(obj, dict):
        return _default_plan()
    out: Dict[str, Any] = _default_plan()
    for k in _ROUTE_PLAN_KEYS:
        if k in obj:
            out[k] = obj[k]
    if not isinstance(out.get("guessed_node_types"), list):
        out["guessed_node_types"] = []
    if not isinstance(out.get("params_draft"), dict):
        out["params_draft"] = {}
    if not isinstance(out.get("probe_snapshot"), dict):
        out["probe_snapshot"] = {}
    if not isinstance(out.get("history_snapshot"), dict):
        out["history_snapshot"] = {}
    strat = str(out.get("answer_strategy") or "").strip().lower()
    if strat not in ("db_direct", "db_synthesize", "direct_llm", "db_direct_doc"):
        if str(out.get("intended_business_id") or "").strip():
            out["answer_strategy"] = "db_synthesize"
        else:
            out["answer_strategy"] = "direct_llm"
    return out


def _default_plan() -> Dict[str, Any]:
    return {
        "answer_strategy": "direct_llm",
        "use_history": False,
        "retrieval_question": "",
        "keyword": "",
        "guessed_node_types": [],
        "user_goal": "",
        "intended_business_id": "",
        "answer_mode": "",
        "params_draft": {},
        "probe_snapshot": {},
        "user_biz": "",
        "scenario": "",
        "match_confidence": "",
    }


def plan_retrieval_question(plan: Dict[str, Any], fallback: str) -> str:
    rq = str(plan.get("retrieval_question") or "").strip()
    return rq or (fallback or "").strip()


def plan_keyword(plan: Dict[str, Any], fallback: str) -> str:
    kw = str(plan.get("keyword") or "").strip()
    if kw:
        return kw
    draft = plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {}
    eq_arr = draft.get("equipment_keywords")
    if isinstance(eq_arr, list) and eq_arr:
        v = str(eq_arr[0] or "").strip()
        if v:
            return v
    for key in ("project_name", "focus", "keyword", "name"):
        v = str(draft.get(key) or "").strip()
        if v:
            return v
    for arr_key in (
        "equipment_keywords",
        "environment_keywords",
        "project_keywords",
        "indicator_keywords",
        "standard_keywords",
        "equipment_type_keywords",
    ):
        arr = draft.get(arr_key)
        if isinstance(arr, list) and arr:
            v = str(arr[0] or "").strip()
            if v:
                return v
        if isinstance(arr, str) and arr.strip():
            return arr.strip()
    return (fallback or "").strip()
