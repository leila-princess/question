"""
将 LLM 因果骨架 / 规划器补全计划导出为简化 STRIPS 风格 PDDL，并生成 HTML 图示。
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


def _sanitize_sym(raw: str, prefix: str = "s") -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", (raw or "").strip())
    s = s.strip("_") or prefix
    if s[0].isdigit():
        s = f"{prefix}_{s}"
    return s[:48]


def _done_pred(nid: str) -> str:
    return f"done-{_sanitize_sym(nid, 'step')}"


def _collect_graph(skeleton_or_g) -> Tuple[Dict[str, str], List[Tuple[str, str]], Dict[str, Dict]]:
    if hasattr(skeleton_or_g, "nodes"):
        g = skeleton_or_g
        nodes = {str(n): str(g.nodes[n].get("name") or n) for n in g.nodes}
        edges = [(str(u), str(v)) for u, v in g.edges]
        meta = {str(n): dict(g.nodes[n]) for n in g.nodes}
        return nodes, edges, meta

    sk = skeleton_or_g if isinstance(skeleton_or_g, dict) else {}
    nodes = {}
    meta = {}
    for n in sk.get("nodes") or []:
        nid = str(n.get("id") or n.get("name"))
        nodes[nid] = str(n.get("name") or nid)
        meta[nid] = n
    edges = []
    for e in sk.get("edges") or []:
        u, v = str(e.get("from")), str(e.get("to"))
        if u and v:
            edges.append((u, v))
    return nodes, edges, meta


def _predecessors(edges: List[Tuple[str, str]]) -> Dict[str, List[str]]:
    pred: Dict[str, List[str]] = {}
    for u, v in edges:
        pred.setdefault(v, []).append(u)
        pred.setdefault(u, pred.get(u, []))
    return pred


def build_grounded_domain(
    domain_name: str,
    nodes: Dict[str, str],
    edges: List[Tuple[str, str]],
    meta: Dict[str, Dict] | None = None,
) -> str:
    """域文件：谓词 + 每个步骤一个 grounded :action。"""
    meta = meta or {}
    pred_map = _predecessors(edges)
    preds = sorted({_done_pred(n) for n in nodes})
    extra_preds = ["env-ready", "device-ready", "baseline-recorded"]

    lines = [
        f"(define (domain {domain_name})",
        "  (:requirements :strips)",
        "  (:predicates",
    ]
    for p in preds:
        lines.append(f"    ({p})")
    for p in extra_preds:
        lines.append(f"    ({p})")
    lines.append("  )")

    for nid, label in nodes.items():
        sym = _sanitize_sym(nid, "step")
        preds_in = pred_map.get(nid, [])
        pre_parts = [_done_pred(p) for p in preds_in]
        name_lower = label.lower()

        if not pre_parts:
            precond = "()"
        elif len(pre_parts) == 1:
            precond = pre_parts[0]
        else:
            precond = f"(and {' '.join(pre_parts)})"

        effects = [_done_pred(nid)]
        if any(k in name_lower for k in ("温箱", "设定", "环境", "70", "℃")):
            effects.append("(env-ready)")
        if "试验前" in label or ("检查" in label and "试验后" not in label):
            effects.append("(baseline-recorded)")
        if "安装" in label:
            effects.append("(device-ready)")

        eff = effects[0] if len(effects) == 1 else f"(and {' '.join(effects)})"
        phase = (meta.get(nid) or {}).get("phase") or ""

        lines.append(f"  (:action exec-{sym}")
        lines.append("    :parameters ()")
        lines.append(f"    ; {label.replace(chr(34), '')}")
        if phase:
            lines.append(f"    ; phase: {phase}")
        lines.append(f"    :precondition {precond}")
        lines.append(f"    :effect {eff}")
        lines.append("  )")

    lines.append(")")
    return "\n".join(lines)


def build_problem_goal(
    problem_name: str,
    domain_name: str,
    nodes: Dict[str, str],
) -> str:
    goal_parts = [f"({_done_pred(nid)})" for nid in nodes]
    goal = goal_parts[0] if len(goal_parts) == 1 else f"(and {' '.join(goal_parts)})"
    return f"""(define (problem {problem_name})
  (:domain {domain_name})
  (:objects)
  (:init
    ;; 初始无步骤完成；根节点动作 :precondition 为 ()
  )
  (:goal
    {goal}
  )
)
"""


def build_pddl_bundle(
    bundle_id: str,
    nodes: Dict[str, str],
    edges: List[Tuple[str, str]],
    meta: Dict[str, Dict] | None = None,
) -> Tuple[str, str, str]:
    domain_name = f"test-scheme-{bundle_id}"
    problem_name = bundle_id
    domain = build_grounded_domain(domain_name, nodes, edges, meta)
    problem = build_problem_goal(problem_name, domain_name, nodes)
    combined = f";; ===== {bundle_id} =====\n\n{domain}\n\n{problem}"
    return domain, problem, combined


def export_skeleton_pddl(skeleton: Dict[str, Any], out_dir: Path) -> Dict[str, str]:
    nodes, edges, meta = _collect_graph(skeleton)
    domain, problem, combined = build_pddl_bundle("llm-skeleton", nodes, edges, meta)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "pddl_skeleton_domain": str(out_dir / "05_skeleton_domain.pddl"),
        "pddl_skeleton_problem": str(out_dir / "05_skeleton_problem.pddl"),
        "pddl_skeleton": str(out_dir / "05_skeleton.pddl"),
        "pddl_skeleton_viz": str(out_dir / "05_skeleton_pddl.html"),
    }
    Path(paths["pddl_skeleton_domain"]).write_text(domain, encoding="utf-8")
    Path(paths["pddl_skeleton_problem"]).write_text(problem, encoding="utf-8")
    Path(paths["pddl_skeleton"]).write_text(combined, encoding="utf-8")
    Path(paths["pddl_skeleton_viz"]).write_text(
        render_pddl_html_page("LLM 规划后 PDDL", domain, problem, nodes, edges),
        encoding="utf-8",
    )
    return paths


def export_full_plan_pddl(
    full_g,
    ordered_actions: List[Dict[str, Any]],
    out_dir: Path,
) -> Dict[str, str]:
    nodes, edges, meta = _collect_graph(full_g)
    for a in ordered_actions:
        gn = str(a.get("graph_node") or a.get("id") or "")
        if gn in nodes:
            tag = " [planner]" if a.get("planner_added") else ""
            nodes[gn] = f"{a.get('name') or nodes[gn]}{tag}"
            meta[gn] = {**meta.get(gn, {}), **a}

    domain, problem, combined = build_pddl_bundle("full-plan", nodes, edges, meta)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "pddl_full_domain": str(out_dir / "06_full_plan_domain.pddl"),
        "pddl_full_problem": str(out_dir / "06_full_plan_problem.pddl"),
        "pddl_full": str(out_dir / "06_full_plan.pddl"),
        "pddl_full_viz": str(out_dir / "06_full_plan_pddl.html"),
    }
    Path(paths["pddl_full_domain"]).write_text(domain, encoding="utf-8")
    Path(paths["pddl_full_problem"]).write_text(problem, encoding="utf-8")
    Path(paths["pddl_full"]).write_text(combined, encoding="utf-8")
    Path(paths["pddl_full_viz"]).write_text(
        render_pddl_html_page("规划器补全后 PDDL", domain, problem, nodes, edges),
        encoding="utf-8",
    )
    return paths


def export_all_pddl(
    out_dir: Path,
    skeleton: Dict[str, Any],
    full_g,
    ordered_actions: List[Dict[str, Any]],
) -> Dict[str, str]:
    paths = {}
    paths.update(export_skeleton_pddl(skeleton, out_dir))
    paths.update(export_full_plan_pddl(full_g, ordered_actions, out_dir))
    return paths


def _escape(s: str) -> str:
    return html.escape(str(s or ""), quote=True)


def render_pddl_html_page(
    title: str,
    domain: str,
    problem: str,
    nodes: Dict[str, str],
    edges: List[Tuple[str, str]],
) -> str:
    from .fallback_visualization import _svg_dag

    fills = {}
    for nid, label in nodes.items():
        fills[nid] = "#d9f7be" if "[planner]" in label else "#d6e4ff"

    svg = _svg_dag(f"{title} — 动作依赖", nodes, edges, fills)
    pred = _predecessors(edges)
    rows = []
    for nid, label in nodes.items():
        sym = _sanitize_sym(nid, "step")
        preds = ", ".join(f"({_done_pred(p)})" for p in pred.get(nid, [])) or "（无，可直接执行）"
        rows.append(
            f"<tr><td><code>exec-{sym}</code></td><td>{_escape(label)}</td><td>{preds}</td>"
            f"<td><code>({_done_pred(nid)})</code></td></tr>"
        )
    table = (
        "<table border='1' cellpadding='6' style='border-collapse:collapse;font-size:12px;width:100%'>"
        "<tr><th>PDDL 动作</th><th>步骤</th><th>:precondition</th><th>:effect</th></tr>"
        + "".join(rows)
        + "</table>"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/><title>{_escape(title)}</title>
<style>
body {{ font-family: Consolas, "Segoe UI", sans-serif; margin: 16px; background: #f5f5f5; }}
pre {{ background: #1e1e1e; color: #d4d4d4; padding: 12px; overflow: auto; font-size: 11px; border-radius: 6px; max-height: 360px; }}
.section {{ background: #fff; padding: 14px; margin-bottom: 14px; border-radius: 8px; box-shadow: 0 1px 6px rgba(0,0,0,.06); }}
</style></head><body>
<h2>{_escape(title)}</h2>
<p style="color:#666;font-size:13px">由 DAG 自动导出：边 A→B 即 PDDL 中 exec-B 的前置含 (done-A)。非 LLM 直接生成 PDDL 文本。</p>
<div class="section"><h3>依赖图</h3>{svg}</div>
<div class="section"><h3>动作表</h3>{table}</div>
<div class="section"><h3>domain.pddl</h3><pre>{_escape(domain)}</pre></div>
<div class="section"><h3>problem.pddl</h3><pre>{_escape(problem)}</pre></div>
</body></html>"""
