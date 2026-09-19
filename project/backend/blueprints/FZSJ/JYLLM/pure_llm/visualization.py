"""
过程可视化：优先 matplotlib PNG；失败则 HTML/SVG 回退（无 numpy2 冲突）。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

NODE_COLORS = {
    "EquipmentType": "#8dd3c7",
    "Equipment": "#80b1d3",
    "Environment": "#bebada",
    "TestProject": "#fb8072",
    "Indicator": "#fdb462",
    "TestMethod": "#b3de69",
    "File": "#fccde5",
    "default": "#cccccc",
}

_plt = None
_nx = None
_mpl_ready: Optional[bool] = None


def _use_matplotlib() -> bool:
    """默认关闭 matplotlib，避免 numpy1/2 与 Roaming 包冲突；需 PNG 时设 JYLLM_PURE_LLM_USE_MATPLOTLIB=1。"""
    return os.environ.get("JYLLM_PURE_LLM_USE_MATPLOTLIB", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _ensure_mpl():
    global _plt, _nx, _mpl_ready
    if _mpl_ready is True:
        return _plt, _nx
    if _mpl_ready is False:
        raise ImportError("matplotlib 不可用")
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import networkx as nx

        _plt = plt
        _nx = nx
        _mpl_ready = True
        return plt, nx
    except Exception as e:
        _mpl_ready = False
        raise ImportError(str(e)) from None


def _render_png(
    output_dir: Path,
    kg: Dict[str, Any],
    skeleton: Dict[str, Any],
    full_g,
    stats: Dict[str, Any],
) -> Dict[str, str]:
    from .partial_order_planner import parse_skeleton_graph

    plt, nx = _ensure_mpl()
    paths = {
        "kg_subgraph": str(output_dir / "01_kg_subgraph.png"),
        "llm_skeleton": str(output_dir / "02_llm_skeleton.png"),
        "full_plan": str(output_dir / "03_full_plan.png"),
        "planner_search": str(output_dir / "04_planner_search.png"),
    }

    def draw(g, out_path: Path, title: str, color_fn=None, critical=False):
        plt.figure(figsize=(12, 8))
        if not g.nodes:
            plt.savefig(out_path, dpi=120, bbox_inches="tight")
            plt.close()
            return
        pos = nx.spring_layout(g, seed=42, k=1.2)
        colors = [color_fn(n, g.nodes[n]) if color_fn else "#4a90d9" for n in g.nodes]
        labels = {n: (g.nodes[n].get("name") or str(n))[:16] for n in g.nodes}
        nx.draw_networkx_nodes(g, pos, node_color=colors, node_size=1200, alpha=0.9)
        nx.draw_networkx_labels(g, pos, labels=labels, font_size=8)
        nx.draw_networkx_edges(g, pos, edge_color="#666", arrows=True, arrowsize=16)
        if critical:
            try:
                cp = nx.dag_longest_path(g)
                nx.draw_networkx_edges(g, pos, edgelist=list(zip(cp, cp[1:])), edge_color="red", width=2)
            except Exception:
                pass
        plt.title(title)
        plt.axis("off")
        plt.savefig(out_path, dpi=120, bbox_inches="tight")
        plt.close()

    g_kg = nx.DiGraph()
    for n in kg.get("nodes") or []:
        g_kg.add_node(n.get("id"), name=n.get("name"), label=n.get("label"))
    for e in kg.get("edges") or []:
        if len(e) >= 2:
            g_kg.add_edge(e[0], e[1])

    draw(
        g_kg,
        Path(paths["kg_subgraph"]),
        "知识图谱子图",
        lambda n, d: NODE_COLORS.get(d.get("label"), NODE_COLORS["default"]),
    )

    sk = parse_skeleton_graph(skeleton)
    draw(sk, Path(paths["llm_skeleton"]), "LLM 因果骨架", lambda n, d: "#4a90d9")

    draw(
        full_g,
        Path(paths["full_plan"]),
        "完整计划",
        lambda n, d: "#52c41a" if d.get("planner_added") else "#4a90d9",
        critical=True,
    )

    plt.figure(figsize=(8, 5))
    labels = ["骨架节点", "补全后节点", "搜索扩展(估)"]
    vals = [stats.get("skeleton_nodes", 0), stats.get("full_nodes", 0), stats.get("search_expansions", 0)]
    plt.bar(labels, vals, color=["#4a90d9", "#52c41a", "#faad14"])
    plt.title("规划器搜索过程示意")
    plt.savefig(paths["planner_search"], dpi=120, bbox_inches="tight")
    plt.close()
    return paths


def render_all_figures(
    output_dir: Path,
    kg: Dict[str, Any],
    skeleton: Dict[str, Any],
    full_g,
    stats: Dict[str, Any],
) -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    if _use_matplotlib():
        try:
            return _render_png(output_dir, kg, skeleton, full_g, stats)
        except ImportError as e:
            logger.info("[pure_llm] matplotlib PNG 不可用，改用 HTML 可视化: %s", e)
        except Exception as e:
            logger.warning("[pure_llm] matplotlib 绘图失败，改用 HTML: %s", e)

    from .fallback_visualization import render_fallback_figures

    paths = render_fallback_figures(output_dir, kg, skeleton, full_g, stats)
    logger.info("[pure_llm] 已生成 HTML 过程图（dashboard + 分图）")
    return paths
