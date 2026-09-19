"""Unified intent-step plan: N>=1 steps, slot binding, expand to legacy RoutePlan."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CHAIN_EQUIP_PROJECT_METHOD = "equip_project_method"
CHAIN_EQUIP_INDICATOR_METHOD = "equip_indicator_method"


@dataclass
class GoalSpec:
    goal: str
    purpose: str
    route_id: str
    title: str
    anchor_slots: List[str] = field(default_factory=list)
    filter_slots: List[str] = field(default_factory=list)


GOAL_REGISTRY: Dict[str, GoalSpec] = {
    "test_projects": GoalSpec(
        goal="test_projects",
        purpose="query_equipment_test_projects",
        route_id="R-TEST_PROJECT-by_equipment",
        title="试验项目",
        anchor_slots=["equipment_keywords"],
    ),
    "indicators": GoalSpec(
        goal="indicators",
        purpose="query_equipment_indicators",
        route_id="R-INDICATOR-by_equipment",
        title="考核指标",
        anchor_slots=["equipment_keywords"],
    ),
    "methods": GoalSpec(
        goal="methods",
        purpose="query_project_methods",
        route_id="R-TEST_METHOD-by_project",
        title="试验方法",
        anchor_slots=["equipment_keywords"],
        filter_slots=["project_keywords"],
    ),
    "standards": GoalSpec(
        goal="standards",
        purpose="query_project_standards",
        route_id="R-STANDARD-for_projects",
        title="相关标准",
        filter_slots=["project_keywords"],
    ),
}


@dataclass
class IntentStep:
    goal: str
    purpose: str = ""
    route_id: str = ""
    span: str = ""
    slots: Dict[str, Any] = field(default_factory=dict)
    anchor_slots: Dict[str, Any] = field(default_factory=dict)
    filter_hints: Dict[str, Any] = field(default_factory=dict)
    chain: str = ""
    step_id: str = ""


@dataclass
class IntentPlan:
    question: str
    steps: List[IntentStep]
    binding_mode: str = ""
    source: str = "rule"


def build_intent_plan(question: str, *, driver: Any = None) -> Optional[IntentPlan]:
    """Return IntentPlan when multi-step or anchored chain; else None (delegate to legacy routing)."""
    from .slot_binding import bind_intent_plan, detect_goal_spans

    q = (question or "").strip()
    if not q:
        return None
    spans = detect_goal_spans(q)
    if not spans:
        return None
    plan = bind_intent_plan(q, spans, driver=driver)
    if not plan or not plan.steps:
        return None
    if len(plan.steps) == 1 and not plan.steps[0].chain:
        return None
    logger.info(
        "[ZNWD4][intent_plan] steps=%d binding=%s goals=%s",
        len(plan.steps),
        plan.binding_mode,
        [s.goal for s in plan.steps],
    )
    return plan


def intent_plan_to_dict(plan: IntentPlan) -> Dict[str, Any]:
    return {
        "question": plan.question,
        "binding_mode": plan.binding_mode,
        "source": plan.source,
        "steps": [
            {
                "goal": s.goal,
                "purpose": s.purpose,
                "route_id": s.route_id,
                "span": s.span,
                "slots": dict(s.slots),
                "anchor_slots": dict(s.anchor_slots),
                "filter_hints": dict(s.filter_hints),
                "chain": s.chain,
                "step_id": s.step_id,
            }
            for s in plan.steps
        ],
    }


def intent_plan_from_dict(data: Dict[str, Any]) -> IntentPlan:
    steps: List[IntentStep] = []
    for raw in data.get("steps") or []:
        if not isinstance(raw, dict):
            continue
        steps.append(
            IntentStep(
                goal=str(raw.get("goal") or ""),
                purpose=str(raw.get("purpose") or ""),
                route_id=str(raw.get("route_id") or ""),
                span=str(raw.get("span") or ""),
                slots=dict(raw.get("slots") or {}),
                anchor_slots=dict(raw.get("anchor_slots") or {}),
                filter_hints=dict(raw.get("filter_hints") or {}),
                chain=str(raw.get("chain") or ""),
                step_id=str(raw.get("step_id") or ""),
            )
        )
    return IntentPlan(
        question=str(data.get("question") or ""),
        steps=steps,
        binding_mode=str(data.get("binding_mode") or ""),
        source=str(data.get("source") or ""),
    )
