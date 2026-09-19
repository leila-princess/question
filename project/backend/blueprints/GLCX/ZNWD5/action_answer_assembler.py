"""
ZNWD5 ActionPlan 答案拼装：direct / assemble / synthesize。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from blueprints.GLCX.ZNWD4.formatters import (
    format_direct_answer,
    format_enum_nodes,
    format_node_properties,
    format_project_to_method,
    format_rel_one_hop,
)
from blueprints.GLCX.ZNWD4.executor import chroma_rows_nonempty

from .action_plan_schema import ActionPlan, ActionStep


def _rows_for_step(step_results: Optional[List[Dict[str, Any]]], step_id: str) -> List[Dict[str, Any]]:
    if not step_results:
        return []
    out: List[Dict[str, Any]] = []
    for sr in step_results:
        if not isinstance(sr, dict):
            continue
        ap = sr.get("action_planner") if isinstance(sr.get("action_planner"), dict) else {}
        logical = str(ap.get("logical_step_id") or sr.get("step_id") or "")
        if logical == step_id or str(sr.get("step_id") or "") == step_id:
            rows = sr.get("neo_rows") or sr.get("rows") or []
            if isinstance(rows, list):
                out.extend(rows)
    return out


def _collect_project_names(rows: List[Dict[str, Any]]) -> List[str]:
    from blueprints.GLCX.ZNWD4.formatters import _node_label, _node_labels

    names: List[str] = []
    seen: set = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("error"):
            continue
        for side in ("m", "n"):
            if "TestProject" in _node_labels(row, side):
                nm = _node_label(row, side, "TestProject")
                if nm and nm not in seen:
                    seen.add(nm)
                    names.append(nm)
    return names


def _collect_method_names(rows: List[Dict[str, Any]]) -> List[str]:
    from blueprints.GLCX.ZNWD4.formatters import _node_label, _node_labels

    names: List[str] = []
    seen: set = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("error"):
            continue
        for side in ("m", "n"):
            if "TestMethod" in _node_labels(row, side):
                nm = _node_label(row, side, "TestMethod")
                if nm and nm not in seen:
                    seen.add(nm)
                    names.append(nm)
    return names


def assemble_project_method(
    *,
    equip_focus: str,
    s1_rows: List[Dict[str, Any]],
    s2_rows: List[Dict[str, Any]],
) -> str:
    """拼装项目+方法：不按项目名逐条硬过滤（避免 format_projects_to_method 漏报）。"""
    sections: List[str] = []
    if equip_focus:
        sections.append(
            format_rel_one_hop(
                s1_rows,
                from_type="Equipment",
                to_type="TestProject",
                focus=equip_focus,
            )
        )
    proj_names = _collect_project_names(s1_rows)
    methods = _collect_method_names(s2_rows)
    if methods:
        lines = ["关联试验方法如下（共 {} 条）：".format(len(methods)), ""]
        for i, m in enumerate(methods, 1):
            lines.append(f"{i}. {m}")
        sections.append("\n".join(lines))
    elif s2_rows and proj_names:
        sections.append(format_project_to_method(s2_rows, proj_names[0]))
    elif not sections:
        return "未检索到相关项目或方法。"
    return "\n\n".join(s for s in sections if s).strip()


def assemble_step_group(
    steps: List[ActionStep],
    *,
    step_results: List[Dict[str, Any]],
    question: str,
) -> str:
    if not steps:
        return ""
    if len(steps) == 1:
        s = steps[0]
        rows = _rows_for_step(step_results, s.step_id)
        if s.action == "enum":
            return format_enum_nodes(rows, s.purpose_node)
        if s.action == "node_prop":
            return format_node_properties(
                rows,
                node_type=s.purpose_node,
                property_fields=s.property_fields or None,
                focus=s.focus,
            )
        if s.action == "hop_n":
            return format_rel_one_hop(
                rows,
                from_type=s.known_type or "Equipment",
                to_type=s.purpose_node,
                focus=s.focus,
            )
        return format_direct_answer(business_id="", neo_rows=rows, chroma_rows=[], question=question)

    if len(steps) >= 2:
        s1, s2 = steps[0], steps[1]
        if (
            s1.action == "hop_n"
            and s1.purpose_node == "TestProject"
            and s1.known_type == "Equipment"
            and s2.action == "hop_n"
            and s2.purpose_node == "TestMethod"
        ):
            return assemble_project_method(
                equip_focus=s1.focus,
                s1_rows=_rows_for_step(step_results, s1.step_id),
                s2_rows=_rows_for_step(step_results, s2.step_id),
            )

    parts: List[str] = []
    for s in steps:
        body = assemble_step_group([s], step_results=step_results, question=question)
        if body:
            parts.append(body)
    return "\n\n".join(parts)


def should_synthesize(
    plan: ActionPlan,
    *,
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
) -> bool:
    mode = plan.answer_mode
    if mode == "synthesize":
        return True
    if plan.sub_questions:
        return True
    if plan.total_step_count >= 2 and mode != "direct":
        return True
    if chroma_rows_nonempty(chroma_rows):
        return True
    if mode == "assemble" and plan.total_step_count >= 2:
        return False
    return False


def assemble_action_plan_answer(
    plan: ActionPlan,
    *,
    question: str,
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    step_results: Optional[List[Dict[str, Any]]] = None,
    business_id: str = "",
    params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    按 answer_mode 拼装答案；若应走 synthesize 则返回 None（由 pipeline 调 stream_synthesis）。
    """
    if should_synthesize(plan, neo_rows=neo_rows, chroma_rows=chroma_rows):
        return None

    sr = step_results or []
    sections: List[str] = []

    if plan.sub_questions:
        for sq in plan.sub_questions:
            body = assemble_step_group(sq.steps, step_results=sr, question=sq.question or question)
            if body:
                sections.append(f"### {sq.question}\n\n{body}")
        return "\n\n".join(sections).strip() or None

    if plan.answer_mode == "assemble" and len(plan.steps) >= 2:
        body = assemble_step_group(plan.steps, step_results=sr, question=question)
        return body or None

    if len(plan.steps) == 1:
        body = assemble_step_group(plan.steps, step_results=sr, question=question)
        if body:
            return body

    return format_direct_answer(
        business_id=business_id,
        neo_rows=neo_rows,
        chroma_rows=chroma_rows,
        question=question,
        params=params or {},
    )


__all__ = [
    "assemble_action_plan_answer",
    "assemble_project_method",
    "should_synthesize",
]
