"""
ZNWD5 ActionPlan 校验：JSON 结构、7 动作/7 节点白名单、hop 关系、use_prev 链。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from blueprints.GLCX.ZNWD4.business_catalog_v2 import resolve_business_from_plan
from blueprints.GLCX.ZNWD4.meta_ops import NODE_LABELS, RELATION_SCHEMA

from .action_plan_schema import (
    ACTION_TYPES,
    ANSWER_MODES,
    ActionPlan,
    ActionStep,
    HOP_DIRECTIONS,
    USE_PREV_FIELDS,
    USE_PREV_SIDES,
    normalize_answer_mode,
)
from .zvec_grounding import best_focus_for_step, focus_in_question_or_grounding

_VALID_NODES = frozenset(NODE_LABELS)

_FATAL_ERROR_PREFIXES = (
    "duplicate step_id",
    "plan must have",
    "too many steps",
    "invalid action",
    "invalid purpose_node",
    "invalid answer_mode",
    "illegal hop",
    "use_prev references unknown",
    "use_prev must reference earlier",
    "cannot combine enum+hop",
)


def is_fatal_validation_error(message: str) -> bool:
    m = (message or "").strip().lower()
    return any(m.startswith(p) or p in m for p in _FATAL_ERROR_PREFIXES)


def validation_errors_fatal(errors: List[str]) -> bool:
    return any(is_fatal_validation_error(e) for e in (errors or []))


def _pick_focus_from_grounding(
    step: ActionStep,
    candidates: List[Dict[str, Any]],
) -> Tuple[str, str]:
    prefer = [step.known_type, step.purpose_node]
    for label in prefer:
        if not label:
            continue
        for c in candidates:
            if str(c.get("node_type") or "") != label:
                continue
            name = str(c.get("name") or "").strip()
            if name:
                return name, str(c.get("id") or "").strip()
    if candidates:
        c0 = candidates[0]
        return str(c0.get("name") or "").strip(), str(c0.get("id") or "").strip()
    return "", ""


def _autofill_step_focus(
    step: ActionStep,
    *,
    question: str,
    candidates: List[Dict[str, Any]],
) -> None:
    if step.use_prev or step.action in ("enum", "document"):
        return
    if not step.focus and not step.focus_id:
        name, eid = _pick_focus_from_grounding(step, candidates)
        if name:
            step.focus = name
            if eid:
                step.focus_id = eid
    if step.focus:
        patched = best_focus_for_step(
            step.focus,
            purpose_node=step.known_type or step.purpose_node,
            question=question,
            candidates=candidates,
        )
        if patched:
            step.focus = patched


def action_max_steps() -> int:
    raw = (os.environ.get("ZNWD5_ACTION_MAX_STEPS") or "6").strip()
    try:
        return max(1, min(14, int(raw)))
    except ValueError:
        return 6


def _hop_relation_allowed(known_type: str, purpose_node: str, hop_direction: str) -> bool:
    if not known_type or not purpose_node:
        return False
    if hop_direction == "reverse":
        fr, to = purpose_node, known_type
    else:
        fr, to = known_type, purpose_node
    for rel, pairs in RELATION_SCHEMA.items():
        if (fr, to) in pairs:
            return True
    return False


def _validate_step(step: ActionStep, *, question: str, candidates: List[Dict[str, Any]]) -> List[str]:
    errs: List[str] = []
    sid = step.step_id

    if step.action not in ACTION_TYPES:
        errs.append(f"{sid}: invalid action {step.action!r}")

    if step.purpose_node not in _VALID_NODES:
        errs.append(f"{sid}: invalid purpose_node {step.purpose_node!r}")

    if step.known_type and step.known_type not in _VALID_NODES:
        errs.append(f"{sid}: invalid known_type {step.known_type!r}")

    if step.hop_direction not in HOP_DIRECTIONS:
        errs.append(f"{sid}: invalid hop_direction {step.hop_direction!r}")

    if step.action == "hop_n":
        if not step.known_type:
            errs.append(f"{sid}: hop_n requires known_type")
        elif not _hop_relation_allowed(step.known_type, step.purpose_node, step.hop_direction):
            errs.append(
                f"{sid}: illegal hop {step.known_type}-[{step.hop_direction}]->{step.purpose_node}"
            )
        draft = {
            "purpose_node": step.purpose_node,
            "biz_type": "hop_n",
            "known_type": step.known_type,
            "hop_direction": step.hop_direction,
            "params_draft": {"focus": step.focus, "focus_id": step.focus_id},
        }
        bid, _ = resolve_business_from_plan(draft, question=question)
        if not bid and not step.use_prev:
            errs.append(f"{sid}: hop_n cannot resolve business_id")

    if step.action == "document" and not (step.query or question):
        errs.append(f"{sid}: document requires query")

    if step.use_prev:
        ref = step.use_prev
        if ref.side not in USE_PREV_SIDES:
            errs.append(f"{sid}: use_prev.side must be from|to")
        if ref.field not in USE_PREV_FIELDS:
            errs.append(f"{sid}: use_prev.field must be Name|Id|Type")

    if step.action != "enum" and not step.use_prev:
        if not step.focus and not step.focus_id:
            if step.action not in ("document",):
                errs.append(f"{sid}: requires focus or focus_id")

    if step.focus and not focus_in_question_or_grounding(step.focus, question, candidates):
        alt = best_focus_for_step(
            step.focus,
            purpose_node=step.known_type or step.purpose_node,
            question=question,
            candidates=candidates,
        )
        if alt and alt != step.focus:
            step.focus = alt
        elif not focus_in_question_or_grounding(step.focus, question, candidates):
            name, eid = _pick_focus_from_grounding(step, candidates)
            if name:
                step.focus = name
                if eid and not step.focus_id:
                    step.focus_id = eid
            # 有 grounding 候选时不因 focus 字面未出现在问句而阻断规划
            if not candidates and step.action not in ("document", "enum"):
                errs.append(f"{sid}: focus {step.focus!r} not in question or grounding")

    return errs


def _collect_step_ids(plan: ActionPlan) -> List[str]:
    ids: List[str] = []
    for s in plan.steps:
        ids.append(s.step_id)
    for sq in plan.sub_questions:
        for s in sq.steps:
            ids.append(s.step_id)
    return ids


def _validate_use_prev_chain(plan: ActionPlan) -> List[str]:
    errs: List[str] = []
    all_ids = _collect_step_ids(plan)
    id_set = set(all_ids)
    if len(all_ids) != len(id_set):
        errs.append("duplicate step_id in plan")

    def check_steps(steps: List[ActionStep]) -> None:
        for i, s in enumerate(steps):
            if not s.use_prev:
                continue
            ref_sid = s.use_prev.step_id
            if ref_sid not in id_set:
                errs.append(f"{s.step_id}: use_prev references unknown step {ref_sid!r}")
            ref_idx = all_ids.index(ref_sid) if ref_sid in id_set else -1
            cur_idx = all_ids.index(s.step_id) if s.step_id in id_set else -1
            if ref_idx >= 0 and cur_idx >= 0 and ref_idx >= cur_idx:
                errs.append(f"{s.step_id}: use_prev must reference earlier step")

    check_steps(plan.steps)
    for sq in plan.sub_questions:
        check_steps(sq.steps)
    return errs


def validate_action_plan(
    plan: ActionPlan,
    *,
    question: str,
    candidates: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[str], ActionPlan]:
    """
    校验 ActionPlan；返回 (errors, patched_plan)。
    patched_plan 会对非法 focus 做 Top1 降级。
    """
    errs: List[str] = []
    cands = list(candidates or [])
    q = (question or plan.question or "").strip()

    if not plan.steps and not plan.sub_questions:
        errs.append("plan must have steps or sub_questions")

    total = plan.total_step_count
    if total > action_max_steps():
        errs.append(f"too many steps: {total} > {action_max_steps()}")

    if plan.answer_mode not in ANSWER_MODES:
        errs.append(f"invalid answer_mode {plan.answer_mode!r}")

    for s in plan.steps:
        _autofill_step_focus(s, question=q, candidates=cands)
        errs.extend(_validate_step(s, question=q, candidates=cands))

    for sq in plan.sub_questions:
        if not sq.question.strip():
            errs.append("sub_question missing question text")
        for s in sq.steps:
            _autofill_step_focus(s, question=sq.question or q, candidates=cands)
            errs.extend(_validate_step(s, question=sq.question or q, candidates=cands))

    errs.extend(_validate_use_prev_chain(plan))

    for sq in plan.sub_questions:
        for s in sq.steps:
            if s.action == "enum" and any(x.action == "hop_n" for x in sq.steps if x.step_id == s.step_id):
                pass
        actions = [s.action for s in sq.steps]
        for i, s in enumerate(sq.steps):
            if s.action == "enum" and i + 1 < len(sq.steps) and sq.steps[i + 1].action == "hop_n":
                if s.step_id == sq.steps[i + 1].step_id:
                    errs.append(f"{s.step_id}: cannot combine enum+hop in one step")

    plan.answer_mode = normalize_answer_mode(plan)
    return errs, plan


__all__ = [
    "action_max_steps",
    "is_fatal_validation_error",
    "validate_action_plan",
    "validation_errors_fatal",
]
