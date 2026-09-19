"""Span segmentation, entity extraction, and keyword–intent alignment (1:N / 1:1 / N:1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .intent_plan import CHAIN_EQUIP_PROJECT_METHOD, GOAL_REGISTRY, IntentPlan, IntentStep
from .question_route_lock import extract_project_keywords_for_methods
from .slot_focus import extract_equipment_focus, strip_query_prefix


@dataclass
class GoalSpan:
    goal: str
    span: str
    start: int = 0
    end: int = 0


_GOAL_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("test_projects", re.compile(r"(试验项目|测试项目)", re.I)),
    ("indicators", re.compile(r"(考核指标|性能指标|试验指标)", re.I)),
    ("methods", re.compile(r"(试验方法|测试方法|用什么方法|的方法是什么|的方法)", re.I)),
    ("standards", re.compile(r"(相关标准|依据标准|适用标准|标准文件)", re.I)),
]

_INDICATOR_FALLBACK = re.compile(r"(?<![试验测试])指标", re.I)
_SPLIT_SEG = re.compile(r"[，；;]|(?<=[？?])(?=[^？?])")
_CONNECTOR_SPLIT = re.compile(r"(?:和|以及|及|、)")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def _goal_in_text(text: str) -> List[str]:
    found: List[str] = []
    for goal, pat in _GOAL_PATTERNS:
        if pat.search(text):
            found.append(goal)
    if "indicators" not in found and _INDICATOR_FALLBACK.search(text):
        if "test_projects" in found or "methods" in found:
            found.append("indicators")
    return found


def _extract_project_hint(span: str, equipment: str = "") -> str:
    sn = _norm(span)
    for pat in (
        r"的([\u4e00-\u9fffA-Za-z0-9·\-（）()≥≤%,./\-]{2,40}?)(?:试验|测试)?项目(?:的)?(?:试验|测试)?方法",
        r"([\u4e00-\u9fffA-Za-z0-9·\-（）()≥≤%,./\-]{2,40}?)的(?:试验|测试)?方法",
        r"([\u4e00-\u9fffA-Za-z0-9·\-（）()≥≤%,./\-]{2,40}?)(?:试验|测试)?项目",
    ):
        m = re.search(pat, sn)
        if m:
            hint = _norm(m.group(1) or "")
            if hint and hint not in ("设备", "装备") and hint != equipment:
                if equipment and hint.startswith(equipment):
                    hint = hint[len(equipment) :].lstrip("的")
                return hint
    kws = extract_project_keywords_for_methods(span)
    if kws:
        return str(kws[0]).strip()
    return ""


def _normalize_equip_name(name: str) -> str:
    raw = strip_query_prefix(name or "")
    if not raw:
        return ""
    focus = extract_equipment_focus(raw)
    if focus and focus not in ("试验项目", "考核指标", "试验方法", "测试方法"):
        return focus
    m = re.search(r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)的", _norm(raw))
    if m:
        cand = (m.group(1) or "").strip()
        if cand and cand not in ("哪些", "什么", "全部", "试验", "测试"):
            return cand
    return raw


def _extract_equipment_in_span(span: str, *, question: str = "") -> str:
    sn = _norm(strip_query_prefix(span))
    qn = _norm(strip_query_prefix(question)) if question else ""
    if (
        len(sn) <= 10
        and not re.search(r"设备|装备|有哪些", sn)
        and qn
        and sn != qn
    ):
        inherited = _extract_equipment_in_span(question, question=question)
        if inherited:
            return inherited

    focus = extract_equipment_focus(sn)
    if focus and focus not in ("试验项目", "考核指标", "试验方法", "测试方法"):
        return focus

    m = re.match(r"^(.+?)有哪些", sn)
    if m:
        name = (m.group(1) or "").strip()
        if (
            len(name) >= 2
            and "哪" not in name
            and "些" not in name
            and name not in ("设备", "装备", "试验", "测试")
        ):
            return name

    m = re.search(r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)的", sn)
    if m:
        name = (m.group(1) or "").strip()
        if len(name) >= 2 and name not in ("哪些", "什么", "全部", "试验", "测试"):
            return name

    focus = extract_equipment_focus(span)
    if focus:
        focus = (focus or "").strip()
        if "哪些" in focus:
            m = re.match(r"^(.+?)有哪些", _norm(focus))
            if m:
                cand = (m.group(1) or "").strip()
                if cand and "哪" not in cand:
                    return cand
        if "的" in focus and len(focus) > len(span) // 2:
            m = re.match(r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)的", _norm(focus))
            if m:
                return (m.group(1) or "").strip()
        if focus not in ("试验项目", "考核指标", "试验方法", "测试方法"):
            return focus

    if question and sn != qn:
        return _extract_equipment_in_span(question, question=question)
    return ""


def detect_goal_spans(question: str) -> List[GoalSpan]:
    """Locate goal mentions; may yield multiple spans for one question."""
    q = (question or "").strip()
    if not q:
        return []

    if re.search(r"(?:试验|测试)?项目.{0,16}(?:的)?(?:试验|测试)?方法", q):
        return [GoalSpan(goal="methods", span=q)]

    goals_all = _goal_in_text(q)
    if not goals_all:
        return []

    if len(goals_all) >= 2 and _CONNECTOR_SPLIT.search(q):
        parts = _CONNECTOR_SPLIT.split(q)
        chunks = [p.strip() for p in parts if p.strip()]
        if len(chunks) >= 2:
            out: List[GoalSpan] = []
            for chunk in chunks:
                gs = _goal_in_text(chunk)
                if not gs:
                    continue
                for g in gs:
                    out.append(GoalSpan(goal=g, span=chunk))
            if len(out) >= 2:
                return out

    seg_out: List[GoalSpan] = []
    for seg in _SPLIT_SEG.split(q):
        seg = seg.strip()
        if not seg:
            continue
        gs = _goal_in_text(seg)
        for g in gs:
            seg_out.append(GoalSpan(goal=g, span=seg))
    if len(seg_out) >= 2:
        return seg_out
    if len(seg_out) == 1:
        return seg_out

    if len(goals_all) >= 2:
        return [GoalSpan(goal=g, span=q) for g in goals_all]

    return [GoalSpan(goal=goals_all[0], span=q)]


def _merge_slots(anchor: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(anchor)
    for k, v in extra.items():
        if v is not None and v != "" and v != []:
            out[k] = v
    return out


def _slots_from_equipment(equip: str) -> Dict[str, Any]:
    if not equip:
        return {}
    return {
        "equipment_keywords": [equip],
        "focus": equip,
    }


def _apply_goal_spec(step: IntentStep) -> IntentStep:
    spec = GOAL_REGISTRY.get(step.goal)
    if spec:
        step.purpose = spec.purpose
        step.route_id = spec.route_id
    return step


def bind_intent_plan(
    question: str,
    spans: List[GoalSpan],
    *,
    driver: Any = None,
) -> Optional[IntentPlan]:
    if not spans:
        return None

    unique_goals = {s.goal for s in spans}
    global_equip = ""
    for gs in spans:
        if gs.goal == "test_projects":
            global_equip = _normalize_equip_name(
                _extract_equipment_in_span(gs.span, question=question)
            )
            if global_equip:
                break
    if not global_equip:
        global_equip = _normalize_equip_name(
            _extract_equipment_in_span(question, question=question)
        )

    if len(unique_goals) == 1 and len(spans) == 1:
        gs = spans[0]
        if gs.goal != "methods":
            return None
        equip = _normalize_equip_name(_extract_equipment_in_span(gs.span, question=question) or global_equip)
        proj_hint = _extract_project_hint(gs.span, equip)
        if equip and proj_hint:
            slots = _merge_slots(_slots_from_equipment(equip), {"project_keywords": [proj_hint]})
            step = IntentStep(
                goal="methods",
                span=gs.span,
                slots=slots,
                anchor_slots=_slots_from_equipment(equip),
                filter_hints={"project_keywords": [proj_hint]},
                chain=CHAIN_EQUIP_PROJECT_METHOD,
            )
            return IntentPlan(
                question=question,
                steps=[_apply_goal_spec(step)],
                binding_mode="merged",
                source="rule",
            )
        return None

    steps: List[IntentStep] = []
    equipments: List[str] = []

    for i, gs in enumerate(spans):
        if gs.goal not in GOAL_REGISTRY:
            continue
        equip = _normalize_equip_name(
            _extract_equipment_in_span(gs.span, question=question) or global_equip
        )
        if equip:
            equipments.append(equip)
        slots = _slots_from_equipment(equip)
        step = IntentStep(
            goal=gs.goal,
            span=gs.span,
            slots=slots,
            anchor_slots=_slots_from_equipment(equip),
            step_id=f"intent{i + 1}",
        )
        steps.append(_apply_goal_spec(step))

    if len(steps) < 2:
        return None

    unique_equip = list(dict.fromkeys(e for e in equipments if e))
    if len(unique_equip) <= 1:
        binding_mode = "shared"
        if unique_equip:
            shared = _slots_from_equipment(unique_equip[0])
            for st in steps:
                st.slots = _merge_slots(shared, st.slots)
                st.anchor_slots = dict(shared)
    else:
        binding_mode = "split"

    return IntentPlan(question=question, steps=steps, binding_mode=binding_mode, source="rule")
