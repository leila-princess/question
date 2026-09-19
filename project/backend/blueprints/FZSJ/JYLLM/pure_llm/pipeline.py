"""
纯 LLM 主流水线：工具检索 → PDDL → 规划求解 → 补全重规划 → 可视化 → 方案撰写（全程 SSE 流式）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from blueprints.FZSJ.JYLLM.multi_agent_engine import emit_event

from .config import task_output_dir
from .pddl_enricher import replan_enriched
from .pddl_generator import generate_pddl_from_llm, iter_generate_pddl_from_llm
from .pddl_visualization import export_all_visualizations
from .preflight import PureLlmPreflightError, check_pure_llm_dependencies
from .pure_llm_agent import iter_retrieval_agent, run_retrieval_agent
from .pyperplan_runner import solve_pddl
from .retrieval_bundle import RetrievalBundle, RetrievalError
from .scheme_writer import iter_write_scheme_from_plan, split_scheme_markdown, write_scheme_from_plan

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, Dict[str, Any]], None]
SubEvent = Tuple[str, Dict[str, Any]]


def _yield_subgen(
    subgen: Generator[SubEvent, None, Any],
) -> Generator[str, None, Any]:
    """将 (event_type, data) 子生成器转为 SSE 字符串；return 子生成器返回值。"""
    try:
        while True:
            ev, data = next(subgen)
            yield emit_event(ev, data)
    except StopIteration as si:
        return si.value


def _emit_plan_steps(plan_names: List[str], *, phase: str) -> Generator[str, None, None]:
    for i, name in enumerate(plan_names, 1):
        yield emit_event("plan_step", {"index": i, "action": name, "phase": phase})


def _plan_trace_actions(plan_names: List[str], bundle: RetrievalBundle, source: str) -> List[Dict[str, Any]]:
    out = []
    method_by_key = {f"method-{m.get('id')}": m for m in bundle.neo_methods}
    for n in plan_names:
        m = method_by_key.get(n) or {}
        out.append(
            {
                "name": m.get("name") or n,
                "source": source,
                "action": n,
                "SYTJ": m.get("SYTJ", ""),
                "SYGC": m.get("SYGC", ""),
                "JGPD": m.get("JGPD", ""),
            }
        )
    return out


def run_pure_llm_pipeline(
    user_description: str,
    task_id: str,
    *,
    llm_model_key: str = "",
    on_progress: Optional[ProgressFn] = None,
) -> Dict[str, Any]:
    """同步执行（调试 / run-sync）。"""
    progress = on_progress or (lambda _p, _d: None)
    out_dir = task_output_dir(task_id)
    check_pure_llm_dependencies()
    progress("agent_retrieve", {"message": "检索…"})
    bundle = run_retrieval_agent(user_description, llm_model_key=llm_model_key)
    (out_dir / "retrieval_trace.json").write_text(bundle.to_json(), encoding="utf-8")
    pddl = generate_pddl_from_llm(user_description, bundle, llm_model_key=llm_model_key)
    (out_dir / "05_skeleton_domain.pddl").write_text(pddl.domain, encoding="utf-8")
    (out_dir / "05_skeleton_problem.pddl").write_text(pddl.problem, encoding="utf-8")
    plan_llm = solve_pddl(pddl.domain, pddl.problem, out_dir)
    enriched_dom, enriched_prob, plan_final, added = replan_enriched(
        pddl.domain, pddl.problem, bundle, out_dir
    )
    figure_paths = export_all_visualizations(
        out_dir, bundle, pddl.domain, pddl.problem, plan_llm, plan_final, enriched_dom
    )
    trace_final = _plan_trace_actions(
        plan_final.plan if plan_final.goal_reached else plan_llm.plan, bundle, "final"
    )
    chroma_text = "\n".join(
        f"- {c.get('title','')}: {str(c.get('content',''))[:300]}" for c in bundle.chroma_chunks[:12]
    )
    branch_notes = pddl.rationale + (f"\n补全新增动作: {', '.join(added)}" if added else "")
    scheme_md = write_scheme_from_plan(
        user_description, trace_final, branch_notes, chroma_text, llm_model_key=llm_model_key
    )
    (out_dir / "scheme.md").write_text(scheme_md, encoding="utf-8")
    return {
        "task_id": task_id,
        "bundle": bundle,
        "pddl": pddl,
        "plan_llm": plan_llm,
        "plan_final": plan_final,
        "trace_final": trace_final,
        "figure_paths": figure_paths,
        "scheme_markdown": scheme_md,
    }


def iter_pure_llm_sse_events(
    user_description: str,
    task_id: str,
    llm_model_key: str = "",
) -> Generator[str, None, None]:
    out_dir = task_output_dir(task_id)
    try:
        yield emit_event("start", {"task_id": task_id, "message": "纯 LLM 模式：开始"})
        check_pure_llm_dependencies()

        yield emit_event("agent_retrieve", {"message": "LLM 调用数据库工具检索…"})
        bundle: RetrievalBundle = yield from _yield_subgen(
            iter_retrieval_agent(user_description, llm_model_key=llm_model_key)
        )
        (out_dir / "retrieval_trace.json").write_text(bundle.to_json(), encoding="utf-8")
        yield emit_event(
            "retrieve_done",
            {
                "method_count": len(bundle.neo_methods),
                "chroma_count": len(bundle.chroma_chunks),
                "tool_rounds": len(bundle.tool_trace),
            },
        )

        pddl = yield from _yield_subgen(
            iter_generate_pddl_from_llm(user_description, bundle, llm_model_key=llm_model_key)
        )
        (out_dir / "05_skeleton_domain.pddl").write_text(pddl.domain, encoding="utf-8")
        (out_dir / "05_skeleton_problem.pddl").write_text(pddl.problem, encoding="utf-8")

        yield emit_event("plan_solve", {"message": "经典规划器求解…"})
        plan_llm = solve_pddl(pddl.domain, pddl.problem, out_dir)
        yield from _emit_plan_steps(plan_llm.plan, phase="llm_domain")
        yield emit_event(
            "plan_solved",
            {
                "solver": plan_llm.solver,
                "plan": plan_llm.plan,
                "goal_reached": plan_llm.goal_reached,
            },
        )

        yield emit_event("pddl_enrich", {"message": "注入图谱方法并重规划…"})
        enriched_dom, enriched_prob, plan_final, added = replan_enriched(
            pddl.domain, pddl.problem, bundle, out_dir
        )
        if plan_final.plan:
            yield from _emit_plan_steps(plan_final.plan, phase="enriched")
        yield emit_event("enrich_done", {"added": added, "plan": plan_final.plan})

        yield emit_event("visualize", {"message": "生成可视化…"})
        figure_paths = export_all_visualizations(
            out_dir, bundle, pddl.domain, pddl.problem, plan_llm, plan_final, enriched_dom
        )
        rel = {k: f"/api/fzsj/jyllm/pure-llm/asset/{task_id}/{Path(v).name}" for k, v in figure_paths.items()}
        yield emit_event("visualize_done", {"figures": rel})

        trace_final = _plan_trace_actions(
            plan_final.plan if plan_final.goal_reached else plan_llm.plan,
            bundle,
            "final",
        )
        chroma_text = "\n".join(
            f"- {c.get('title','')}: {str(c.get('content',''))[:300]}" for c in bundle.chroma_chunks[:12]
        )
        branch_notes = pddl.rationale + (f"\n补全新增动作: {', '.join(added)}" if added else "")

        yield emit_event("write", {"message": "流式撰写试验方案…"})
        scheme_md: str = yield from _yield_subgen(
            iter_write_scheme_from_plan(
                user_description,
                trace_final,
                branch_notes,
                chroma_text,
                llm_model_key=llm_model_key,
            )
        )
        (out_dir / "scheme.md").write_text(scheme_md, encoding="utf-8")

        chapters = _chapters_from_md(scheme_md)
        summary = _build_summary(trace_final, plan_llm, plan_final, added)
        yield emit_event(
            "complete",
            {
                "task_id": task_id,
                "scheme_markdown": scheme_md,
                "chapters": chapters,
                "figures": rel,
                "ordered_actions": trace_final,
                "summary": summary,
            },
        )
    except (RetrievalError, PureLlmPreflightError, ValueError) as e:
        logger.exception("pure_llm failed")
        yield emit_event("error", {"message": str(e)})
    except Exception as e:
        logger.exception("pure_llm failed")
        yield emit_event("error", {"message": str(e)})


def _chapters_from_md(md: str) -> List[Dict[str, Any]]:
    return split_scheme_markdown(md) or [{"index": 1, "title": "试验方案", "content": md}]


def _build_summary(trace, plan_llm, plan_final, added) -> str:
    lines = [f"规划器（LLM 域）: {plan_llm.solver}, {len(plan_llm.plan)} 步"]
    lines.append(f"重规划（补全后）: {plan_final.solver}, {len(plan_final.plan)} 步")
    if added:
        lines.append(f"图谱注入动作: {', '.join(added[:8])}")
    lines.append("解序列:")
    for i, a in enumerate(trace,  1):
        lines.append(f"  {i}. [{a.get('source')}] {a.get('name')}")
    return "\n".join(lines)
