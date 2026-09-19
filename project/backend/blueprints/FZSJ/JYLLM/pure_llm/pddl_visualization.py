"""
PDDL 规划可视化：领域结构图、规划空间图、检索轨迹。
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .fallback_visualization import _html_dashboard, _svg_dag
from .pddl_parser import PddlModel, parse_pddl_bundle, _expr_atoms
from .retrieval_bundle import RetrievalBundle
from .strips_planner import PlanResult


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


def render_domain_structure_html(model: PddlModel, title: str) -> str:
    nodes: Dict[str, str] = {}
    edges: List[tuple] = []
    fills: Dict[str, str] = {}
    for i, act in enumerate(model.actions):
        nid = f"a{i}"
        label = act.name[:18]
        if isinstance(act.precondition, tuple) and act.precondition and act.precondition[0] == "or":
            fills[nid] = "#fff3cd"
            label += " [OR]"
        else:
            fills[nid] = "#d6e4ff"
        nodes[nid] = label
        pres = _expr_atoms(act.precondition)
        for p in list(pres)[:2]:
            pid = f"p_{p}"[:20]
            if pid not in nodes:
                nodes[pid] = p[:12]
                fills[pid] = "#f0f0f0"
            edges.append((pid, nid))
    svg = _svg_dag(title, nodes, edges, fills) if nodes else "<p>（无动作）</p>"
    return _html_dashboard(title, [(title, svg)])


def render_plan_space_html(
    model: PddlModel,
    plan_result: PlanResult,
    title: str,
    *,
    highlight_actions: Optional[Set[str]] = None,
) -> str:
    """有限深度状态扩展：解路径实线，分支点虚线备选。"""
    highlight_actions = highlight_actions or set()
    start = set(model.init)
    goal_atoms = _expr_atoms(model.goal)

    from .strips_planner import _holds, _apply_effect

    layers: List[Dict] = []
    state = start
    plan = plan_result.plan or []
    path_states = [frozenset(state)]
    for an in plan:
        act = next((a for a in model.actions if a.name == an), None)
        if act:
            state = _apply_effect(act.effect, state)
        path_states.append(frozenset(state))

    nodes: Dict[str, str] = {}
    edges: List[tuple] = []
    fills: Dict[str, str] = {}

    def sid(st):
        return "s_" + str(abs(hash(frozenset(st))))[:8]

    for i, st in enumerate(path_states):
        nid = sid(st)
        nodes[nid] = f"状态{i}"
        fills[nid] = "#d6e4ff"
        if i > 0:
            edges.append((sid(path_states[i - 1]), nid))

    # 分支：第一个状态的可选动作（未选虚线 — 用额外节点表示）
    if path_states:
        from .strips_planner import _holds as holds

        st0 = set(path_states[0])
        alts = plan_result.alternatives.get(str(sorted(st0)), [])
        for j, alt in enumerate(alts[:3]):
            if plan and alt == plan[0]:
                continue
            aid = f"alt_{j}"
            nodes[aid] = alt[:14] + "?"
            fills[aid] = "#fffbe6"
            edges.append((sid(path_states[0]), aid))

    svg = _svg_dag(title + "（蓝=解路径，黄=未选分支）", nodes, edges, fills)
    plan_txt = "<ol>" + "".join(f"<li>{_esc(a)}</li>" for a in plan) + "</ol>"
    body = f"<p>求解器: {_esc(plan_result.solver)}，扩展: {plan_result.expanded}，目标: {'达成' if plan_result.goal_reached else '未达成'}</p>{plan_txt}{svg}"
    return _html_dashboard(title, [(title, body)])


def render_retrieval_trace_html(bundle: RetrievalBundle) -> str:
    rows = []
    for t in bundle.tool_trace:
        rows.append(
            f"<tr><td>{_esc(t.get('tool'))}</td><td>{t.get('neo_count',0)}</td>"
            f"<td>{t.get('chroma_count',0)}</td><td>{_esc(t.get('error') or '')}</td></tr>"
        )
    table = (
        "<table border='1' cellpadding='4'><tr><th>工具</th><th>Neo</th><th>Chroma</th><th>错误</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    return _html_dashboard("检索工具轨迹", [("调用记录", table)])


def export_all_visualizations(
    out_dir: Path,
    bundle: RetrievalBundle,
    domain_text: str,
    problem_text: str,
    plan_llm: PlanResult,
    plan_final: Optional[PlanResult],
    enriched_domain: Optional[str] = None,
) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, str] = {}
    model = parse_pddl_bundle(domain_text, problem_text)

    (out_dir / "05_skeleton.pddl").write_text(
        f";; LLM PDDL\n\n{domain_text}\n\n{problem_text}", encoding="utf-8"
    )
    paths["pddl_skeleton"] = str(out_dir / "05_skeleton.pddl")

    dom_html = render_domain_structure_html(model, "LLM 领域结构")
    p1 = out_dir / "07_domain_structure.html"
    p1.write_text(dom_html, encoding="utf-8")
    paths["domain_structure"] = str(p1)

    ps1 = out_dir / "08_plan_space_llm.html"
    ps1.write_text(render_plan_space_html(model, plan_llm, "LLM 规划空间"), encoding="utf-8")
    paths["plan_space_llm"] = str(ps1)

    if enriched_domain and plan_final:
        (out_dir / "06_full_plan.pddl").write_text(enriched_domain, encoding="utf-8")
        paths["pddl_full"] = str(out_dir / "06_full_plan.pddl")
        em = parse_pddl_bundle(enriched_domain, problem_text)
        ps2 = out_dir / "09_plan_space_enriched.html"
        ps2.write_text(
            render_plan_space_html(em, plan_final, "补全后规划空间"),
            encoding="utf-8",
        )
        paths["plan_space_enriched"] = str(ps2)

    rt = out_dir / "10_retrieval_trace.html"
    rt.write_text(render_retrieval_trace_html(bundle), encoding="utf-8")
    paths["retrieval_trace"] = str(rt)

    bundle.to_json()
    (out_dir / "retrieval_trace.json").write_text(bundle.to_json(), encoding="utf-8")

    # dashboard
    links = "".join(
        f'<li><a href="{Path(v).name}">{k}</a></li>' for k, v in paths.items() if str(v).endswith(".html")
    )
    dash = _html_dashboard(
        "纯 LLM 规划总览",
        [
            ("链接", f"<ul>{links}</ul>"),
            (
                "说明",
                "<p>领域图展示 OR/分支动作；规划空间图展示求解路径与未选分支。</p>",
            ),
        ],
    )
    db = out_dir / "process_dashboard.html"
    db.write_text(dash, encoding="utf-8")
    paths["dashboard"] = str(db)
    return paths
