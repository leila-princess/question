"""
无 matplotlib 依赖的过程可视化：Graphviz DOT + 内嵌 SVG 的 HTML（仅标准库）。
"""
from __future__ import annotations

import html
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .partial_order_planner import parse_skeleton_graph


def _escape(s: str) -> str:
    return html.escape(str(s or ""), quote=True)


def _layer_layout(node_ids: List[str], edges: List[Tuple[str, str]]) -> Dict[str, Tuple[float, float]]:
    ids = list(node_ids)
    if not ids:
        return {}
    indegree: Dict[str, int] = {n: 0 for n in ids}
    adj: Dict[str, List[str]] = defaultdict(list)
    for u, v in edges:
        indegree.setdefault(u, 0)
        indegree.setdefault(v, 0)
        adj[u].append(v)
        indegree[v] += 1

    q = deque([n for n, d in indegree.items() if d == 0])
    if not q:
        q = deque(ids[:1])
    order: List[str] = []
    while q:
        u = q.popleft()
        if u in order:
            continue
        order.append(u)
        for v in adj.get(u, []):
            indegree[v] -= 1
            if indegree[v] <= 0:
                q.append(v)
    for n in ids:
        if n not in order:
            order.append(n)

    layer: Dict[str, int] = {n: 0 for n in order}
    for u in order:
        for v in adj.get(u, []):
            layer[v] = max(layer.get(v, 0), layer.get(u, 0) + 1)

    by_layer: Dict[int, List[str]] = defaultdict(list)
    for n in order:
        by_layer[layer.get(n, 0)].append(n)

    pos: Dict[str, Tuple[float, float]] = {}
    for ly, nodes in sorted(by_layer.items()):
        for i, n in enumerate(nodes):
            pos[n] = (100.0 + i * 160.0, 70.0 + ly * 100.0)
    return pos


def _graph_to_dot(title: str, nodes: Dict[str, str], edges: List[Tuple[str, str]], fills: Dict[str, str] | None = None) -> str:
    fills = fills or {}
    lines = [f'digraph "{title}" {{', "  rankdir=TB;", "  node [shape=box];"]
    for nid, label in nodes.items():
        color = fills.get(nid, "#e8f4ff")
        lines.append(f'  "{nid}" [label="{label.replace(chr(34), "")}", fillcolor="{color}", style="filled"];')
    for u, v in edges:
        lines.append(f'  "{u}" -> "{v}";')
    lines.append("}")
    return "\n".join(lines)


def _svg_dag(title: str, nodes: Dict[str, str], edges: List[Tuple[str, str]], fills: Dict[str, str] | None = None) -> str:
    fills = fills or {}
    pos = _layer_layout(list(nodes.keys()), edges)
    if not pos:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="80"><text x="12" y="40">{_escape(title)}（空）</text></svg>'

    max_x = max(p[0] for p in pos.values()) + 120
    max_y = max(p[1] for p in pos.values()) + 80
    w, h = int(max(400, max_x + 40)), int(max(180, max_y + 40))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">',
        f'<text x="12" y="20" font-size="13" font-weight="bold">{_escape(title)}</text>',
        '<defs><marker id="arr" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 Z" fill="#666"/></marker></defs>',
    ]
    for u, v in edges:
        if u in pos and v in pos:
            x1, y1 = pos[u]
            x2, y2 = pos[v]
            parts.append(
                f'<line x1="{x1}" y1="{y1+16}" x2="{x2}" y2="{y2-16}" stroke="#888" stroke-width="1.5" marker-end="url(#arr)"/>'
            )
    for nid, (x, y) in pos.items():
        label = _escape((nodes.get(nid) or nid)[:18])
        fill = fills.get(nid, "#e8f4ff")
        parts.append(f'<rect x="{x-52}" y="{y-20}" width="104" height="40" rx="5" fill="{fill}" stroke="#444"/>')
        parts.append(f'<text x="{x}" y="{y+4}" text-anchor="middle" font-size="10">{label}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _stats_svg(stats: Dict[str, Any]) -> str:
    rows = [
        ("骨架节点", stats.get("skeleton_nodes", 0), "#4a90d9"),
        ("补全后节点", stats.get("full_nodes", 0), "#52c41a"),
        ("搜索扩展(估)", stats.get("search_expansions", 0), "#faad14"),
    ]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="180">',
        '<text x="12" y="22" font-size="13" font-weight="bold">规划器搜索示意</text>',
    ]
    for i, (lab, val, color) in enumerate(rows):
        bw = min(220, int(val) * 28 + 16)
        y = 45 + i * 42
        parts.append(f'<text x="16" y="{y+14}" font-size="11">{_escape(lab)}: {val}</text>')
        parts.append(f'<rect x="150" y="{y}" width="{bw}" height="22" fill="{color}"/>')
    parts.append("</svg>")
    return "".join(parts)


def _html_dashboard(title: str, sections: List[Tuple[str, str]]) -> str:
    blocks = "".join(
        f'<div class="section"><h3>{_escape(st)}</h3>{frag}</div>' for st, frag in sections
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/><title>{_escape(title)}</title>
<style>
body {{ font-family: "Segoe UI", sans-serif; margin: 16px; background: #f5f5f5; }}
h2 {{ margin-top: 0; }}
.section {{ background: #fff; padding: 14px; margin-bottom: 14px; border-radius: 8px;
  box-shadow: 0 1px 6px rgba(0,0,0,.06); overflow-x: auto; }}
</style></head><body><h2>{_escape(title)}</h2>{blocks}</body></html>"""


def _kg_graph(kg: Dict[str, Any]) -> Tuple[Dict[str, str], List[Tuple[str, str]], Dict[str, str]]:
    nodes: Dict[str, str] = {}
    fills: Dict[str, str] = {}
    colors = {
        "EquipmentType": "#8dd3c7",
        "Equipment": "#80b1d3",
        "Environment": "#bebada",
        "TestProject": "#fb8072",
        "Indicator": "#fdb462",
        "TestMethod": "#b3de69",
    }
    for n in kg.get("nodes") or []:
        nid = str(n.get("id"))
        nodes[nid] = str(n.get("name") or nid)
        fills[nid] = colors.get(n.get("label"), "#ddd")
    edges = []
    for e in kg.get("edges") or []:
        if len(e) >= 2:
            edges.append((str(e[0]), str(e[1])))
    return nodes, edges, fills


def _nx_to_graph(g) -> Tuple[Dict[str, str], List[Tuple[str, str]], Dict[str, str]]:
    nodes = {str(n): str(g.nodes[n].get("name") or n)[:20] for n in g.nodes}
    edges = [(str(u), str(v)) for u, v in g.edges]
    fills = {}
    for n in g.nodes:
        if g.nodes[n].get("planner_added"):
            fills[str(n)] = "#d9f7be"
        elif g.nodes[n].get("from_llm", True):
            fills[str(n)] = "#d6e4ff"
        else:
            fills[str(n)] = "#e8f4ff"
    return nodes, edges, fills


def render_fallback_figures(
    output_dir: Path,
    kg: Dict[str, Any],
    skeleton: Dict[str, Any],
    full_g,
    stats: Dict[str, Any],
) -> Dict[str, str]:
    """生成 HTML + DOT，返回 API 可访问的相对路径键。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    skel_g = parse_skeleton_graph(skeleton)
    kg_nodes, kg_edges, kg_fills = _kg_graph(kg)
    sk_nodes, sk_edges, sk_fills = _nx_to_graph(skel_g)
    full_nodes, full_edges, full_fills = _nx_to_graph(full_g)

    paths = {
        "dashboard": str(output_dir / "process_dashboard.html"),
        "kg_subgraph": str(output_dir / "01_kg_subgraph.html"),
        "llm_skeleton": str(output_dir / "02_llm_skeleton.html"),
        "full_plan": str(output_dir / "03_full_plan.html"),
        "planner_search": str(output_dir / "04_planner_search.html"),
    }

    (output_dir / "01_kg_subgraph.dot").write_text(
        _graph_to_dot("kg", kg_nodes, kg_edges, kg_fills), encoding="utf-8"
    )
    (output_dir / "02_llm_skeleton.dot").write_text(
        _graph_to_dot("skeleton", sk_nodes, sk_edges, sk_fills), encoding="utf-8"
    )
    (output_dir / "03_full_plan.dot").write_text(
        _graph_to_dot("full_plan", full_nodes, full_edges, full_fills), encoding="utf-8"
    )

    pddl_links = ""
    for name in (
        "05_skeleton_pddl.html",
        "06_full_plan_pddl.html",
        "05_skeleton.pddl",
        "06_full_plan.pddl",
    ):
        p = output_dir / name
        if p.is_file():
            pddl_links += f'<li><a href="{name}">{name}</a></li>'

    sections = [
        ("1. 知识图谱子图", _svg_dag("知识图谱子图", kg_nodes, kg_edges, kg_fills)),
        ("2. LLM 因果骨架", _svg_dag("LLM 因果骨架（蓝）", sk_nodes, sk_edges, sk_fills)),
        ("3. 完整计划", _svg_dag("完整计划（蓝=LLM，绿=补全）", full_nodes, full_edges, full_fills)),
        ("4. 规划器搜索", _stats_svg(stats)),
    ]
    if pddl_links:
        sections.append(
            (
                "5. PDDL（LLM 骨架 / 补全后）",
                f"<ul>{pddl_links}</ul><p>打开 HTML 可查看依赖图、动作表与 domain/problem 原文。</p>",
            )
        )
    Path(paths["dashboard"]).write_text(
        _html_dashboard("纯 LLM 过程可视化", sections), encoding="utf-8"
    )
    for key, (nodes, edges, fills, title) in [
        ("kg_subgraph", (kg_nodes, kg_edges, kg_fills, "知识图谱子图")),
        ("llm_skeleton", (sk_nodes, sk_edges, sk_fills, "LLM 因果骨架")),
        ("full_plan", (full_nodes, full_edges, full_fills, "完整计划")),
    ]:
        frag = _svg_dag(title, nodes, edges, fills)
        Path(paths[key]).write_text(
            _html_dashboard(title, [(title, frag)]), encoding="utf-8"
        )
    Path(paths["planner_search"]).write_text(
        _html_dashboard("规划器搜索", [("统计", _stats_svg(stats))]), encoding="utf-8"
    )
    return paths
