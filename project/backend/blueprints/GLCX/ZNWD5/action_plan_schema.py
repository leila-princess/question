"""
ZNWD5 ActionPlan JSON Schema：LLM 动作规划唯一输出结构。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from blueprints.GLCX.ZNWD4.meta_ops import NODE_LABELS

ACTION_TYPES = frozenset(
    {"node_kw", "node_prop", "node_prop_rev", "enum", "hop_n", "document"}
)
ANSWER_MODES = frozenset({"direct", "assemble", "synthesize"})
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})
HOP_DIRECTIONS = frozenset({"forward", "reverse"})
USE_PREV_SIDES = frozenset({"from", "to"})
USE_PREV_FIELDS = frozenset({"Name", "Id", "Type"})

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I | re.M)


@dataclass
class UsePrevRef:
    step_id: str
    side: str = "to"
    field: str = "Name"

    def to_dict(self) -> Dict[str, Any]:
        return {"step_id": self.step_id, "side": self.side, "field": self.field}


@dataclass
class ActionStep:
    step_id: str
    action: str
    purpose_node: str
    known_type: str = ""
    hop_direction: str = "forward"
    focus: str = ""
    focus_id: str = ""
    use_prev: Optional[UsePrevRef] = None
    note: str = ""
    query: str = ""
    property_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "step_id": self.step_id,
            "action": self.action,
            "purpose_node": self.purpose_node,
        }
        if self.known_type:
            out["known_type"] = self.known_type
        if self.hop_direction and self.hop_direction != "forward":
            out["hop_direction"] = self.hop_direction
        if self.focus:
            out["focus"] = self.focus
        if self.focus_id:
            out["focus_id"] = self.focus_id
        if self.use_prev:
            out["use_prev"] = self.use_prev.to_dict()
        if self.note:
            out["note"] = self.note
        if self.query:
            out["query"] = self.query
        if self.property_fields:
            out["property_fields"] = list(self.property_fields)
        return out


@dataclass
class SubQuestion:
    question: str
    steps: List[ActionStep] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass
class ActionPlan:
    question: str
    steps: List[ActionStep] = field(default_factory=list)
    sub_questions: List[SubQuestion] = field(default_factory=list)
    answer_mode: str = "synthesize"
    confidence: str = "medium"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "sub_questions": [sq.to_dict() for sq in self.sub_questions],
            "steps": [s.to_dict() for s in self.steps],
            "answer_mode": self.answer_mode,
            "confidence": self.confidence,
        }

    @property
    def total_step_count(self) -> int:
        n = len(self.steps)
        for sq in self.sub_questions:
            n += len(sq.steps)
        return n


def _strip_json(raw: str) -> str:
    s = (raw or "").strip()
    s = _JSON_FENCE.sub("", s).strip()
    m = re.search(r"\{[\s\S]*\}", s)
    return m.group(0) if m else s


def _parse_use_prev(raw: Any) -> Optional[UsePrevRef]:
    if not isinstance(raw, dict):
        return None
    sid = str(raw.get("step_id") or "").strip()
    if not sid:
        return None
    side = str(raw.get("side") or "to").strip().lower()
    fld = str(raw.get("field") or "Name").strip()
    return UsePrevRef(step_id=sid, side=side, field=fld)


def _parse_step(raw: Any) -> Optional[ActionStep]:
    if not isinstance(raw, dict):
        return None
    sid = str(raw.get("step_id") or "").strip()
    action = str(raw.get("action") or "").strip().lower()
    purpose = str(raw.get("purpose_node") or "").strip()
    if not sid or not action or not purpose:
        return None
    pf = raw.get("property_fields")
    if not isinstance(pf, list):
        pf = []
    return ActionStep(
        step_id=sid,
        action=action,
        purpose_node=purpose,
        known_type=str(raw.get("known_type") or "").strip(),
        hop_direction=str(raw.get("hop_direction") or "forward").strip().lower(),
        focus=str(raw.get("focus") or "").strip(),
        focus_id=str(raw.get("focus_id") or "").strip(),
        use_prev=_parse_use_prev(raw.get("use_prev")),
        note=str(raw.get("note") or "").strip(),
        query=str(raw.get("query") or "").strip(),
        property_fields=[str(x).strip() for x in pf if str(x).strip()],
    )


def parse_action_plan_json(raw: str) -> ActionPlan:
    """从 LLM 原始文本解析 ActionPlan。"""
    obj = json.loads(_strip_json(raw))
    if not isinstance(obj, dict):
        raise ValueError("ActionPlan 根对象必须为 JSON 对象")

    sub_qs: List[SubQuestion] = []
    for item in obj.get("sub_questions") or []:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question") or "").strip()
        steps = [_parse_step(s) for s in (item.get("steps") or [])]
        steps = [s for s in steps if s]
        if q and steps:
            sub_qs.append(SubQuestion(question=q, steps=steps))

    steps = [_parse_step(s) for s in (obj.get("steps") or [])]
    steps = [s for s in steps if s]

    mode = str(obj.get("answer_mode") or "synthesize").strip().lower()
    conf = str(obj.get("confidence") or "medium").strip().lower()

    return ActionPlan(
        question=str(obj.get("question") or "").strip(),
        steps=steps,
        sub_questions=sub_qs,
        answer_mode=mode if mode in ANSWER_MODES else "synthesize",
        confidence=conf if conf in CONFIDENCE_LEVELS else "medium",
    )


def action_plan_from_dict(obj: Dict[str, Any]) -> ActionPlan:
    return parse_action_plan_json(json.dumps(obj, ensure_ascii=False))


def normalize_answer_mode(plan: ActionPlan) -> str:
    """多步/多问默认 synthesize；单步 enum 可 direct。"""
    if plan.sub_questions:
        if plan.answer_mode == "direct":
            all_enum = all(
                s.action == "enum" for sq in plan.sub_questions for s in sq.steps
            )
            if not all_enum:
                return "synthesize"
        return plan.answer_mode if plan.answer_mode in ANSWER_MODES else "synthesize"

    if len(plan.steps) >= 2:
        if plan.answer_mode == "direct":
            if all(s.action == "enum" for s in plan.steps):
                return "direct"
            return "synthesize"
        return plan.answer_mode if plan.answer_mode in ANSWER_MODES else "synthesize"

    if len(plan.steps) == 1 and plan.steps[0].action == "enum":
        return plan.answer_mode if plan.answer_mode in ("direct", "assemble") else "direct"
    return plan.answer_mode if plan.answer_mode in ANSWER_MODES else "direct"


def plan_purpose_node(plan: ActionPlan) -> str:
    if plan.steps:
        return plan.steps[0].purpose_node
    if plan.sub_questions and plan.sub_questions[0].steps:
        return plan.sub_questions[0].steps[0].purpose_node
    return ""


def plan_primary_action(plan: ActionPlan) -> str:
    if plan.steps:
        return plan.steps[0].action
    if plan.sub_questions and plan.sub_questions[0].steps:
        return plan.sub_questions[0].steps[0].action
    return ""


__all__ = [
    "ACTION_TYPES",
    "ANSWER_MODES",
    "NODE_LABELS",
    "ActionPlan",
    "ActionStep",
    "SubQuestion",
    "UsePrevRef",
    "action_plan_from_dict",
    "normalize_answer_mode",
    "parse_action_plan_json",
    "plan_primary_action",
    "plan_purpose_node",
]
