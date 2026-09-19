"""从任务目录已有 .dot 重新生成 PDDL（一次性工具）。"""
import re
import sys
from pathlib import Path

import networkx as nx

from blueprints.FZSJ.JYLLM.pure_llm.pddl_export import export_full_plan_pddl, export_skeleton_pddl


def parse_dot(path: Path):
    text = path.read_text(encoding="utf-8")
    nodes, edges = {}, []
    for m in re.finditer(r'"([^"]+)"\s*\[label="([^"]*)"', text):
        nodes[m.group(1)] = m.group(2)
    for m in re.finditer(r'"([^"]+)"\s*->\s*"([^"]+)"', text):
        edges.append((m.group(1), m.group(2)))
    return nodes, edges


def main(task_dir: str):
    out = Path(task_dir)
    sk_nodes, sk_edges = parse_dot(out / "02_llm_skeleton.dot")
    full_nodes, full_edges = parse_dot(out / "03_full_plan.dot")
    sk = {
        "nodes": [{"id": k, "name": v, "phase": ""} for k, v in sk_nodes.items()],
        "edges": [{"from": u, "to": v} for u, v in sk_edges],
    }
    g = nx.DiGraph()
    for k, v in full_nodes.items():
        g.add_node(k, name=v, planner_added="[planner]" in v)
    for u, v in full_edges:
        g.add_edge(u, v)
    ordered = [
        {
            "graph_node": nid,
            "name": full_nodes[nid],
            "planner_added": "[planner]" in full_nodes[nid],
        }
        for nid in nx.topological_sort(g)
    ]
    export_skeleton_pddl(sk, out)
    export_full_plan_pddl(g, ordered, out)
    print("PDDL regenerated for", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "schemes/pure_llm/pure_1779172935085")
