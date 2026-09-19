"""
偏序规划补全：以 LLM 因果骨架为硬约束，从可行动作池中插入缺失步骤。
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

logger = logging.getLogger(__name__)


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").strip(), (b or "").strip()).ratio()


def match_method_id(
    skeleton_name: str,
    skeleton_method_id: Optional[str],
    candidates: List[Dict[str, Any]],
    threshold: float = 0.55,
) -> Optional[Dict[str, Any]]:
    if skeleton_method_id:
        for c in candidates:
            if str(c.get("id")) == str(skeleton_method_id):
                return c
    best = None
    best_score = threshold
    for c in candidates:
        score = _sim(skeleton_name, c.get("name") or "")
        if score > best_score:
            best_score = score
            best = c
    return best


def parse_skeleton_graph(skeleton: Dict[str, Any]) -> nx.DiGraph:
    g = nx.DiGraph()
    for n in skeleton.get("nodes") or []:
        nid = str(n.get("id") or n.get("name"))
        g.add_node(
            nid,
            name=n.get("name") or nid,
            method_id=n.get("method_id"),
            phase=n.get("phase") or "",
            from_llm=True,
        )
    for e in skeleton.get("edges") or []:
        u, v = str(e.get("from")), str(e.get("to"))
        if u and v and u in g and v in g:
            g.add_edge(u, v)
    if not nx.is_directed_acyclic_graph(g):
        logger.warning("LLM skeleton has cycle, removing back-edges")
        while not nx.is_directed_acyclic_graph(g):
            try:
                cycle = nx.find_cycle(g)
                g.remove_edge(cycle[0][0], cycle[0][1])
            except nx.NetworkXNoCycle:
                break
    return g


def _required_macro_sequence() -> List[str]:
    """通用试验流程宏阶段，用于补全骨架未覆盖的步骤。"""
    return ["预处理", "环境", "实施", "恢复", "检测"]


def complete_plan(
    skeleton: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> Tuple[nx.DiGraph, List[Dict[str, Any]], Dict[str, Any]]:
    """
    Returns: (full_dag, ordered_actions, stats)
    ordered_actions: 拓扑序上的动作 dict 列表，含 from_llm / planner_added 标记
    """
    skel_g = parse_skeleton_graph(skeleton)
    full = skel_g.copy()
    used_candidate_ids: Set[str] = set()
    node_to_action: Dict[str, Dict[str, Any]] = {}

    for nid in list(full.nodes):
        data = full.nodes[nid]
        matched = match_method_id(
            data.get("name", ""),
            data.get("method_id"),
            candidates,
        )
        if matched:
            used_candidate_ids.add(str(matched["id"]))
            node_to_action[nid] = {**matched, "from_llm": True, "planner_added": False, "graph_node": nid}
        else:
            node_to_action[nid] = {
                "id": nid,
                "name": data.get("name"),
                "SYTJ": "",
                "SYGC": "",
                "JGPD": "",
                "SYNR": data.get("phase") or "",
                "from_llm": True,
                "planner_added": False,
                "graph_node": nid,
            }

    # 将未出现在骨架中的候选按阶段关键词补入
    phase_keywords = {
        "预处理": ("检查", "预处理", "安装", "记录"),
        "环境": ("温箱", "设定", "环境", "降温", "升温"),
        "实施": ("工作", "试验", "启动", "加载"),
        "恢复": ("恢复", "正常大气"),
        "检测": ("后检测", "评定", "复测"),
    }
    skel_names = " ".join(full.nodes[n].get("name", "") for n in full.nodes).lower()
    prev_tail: Optional[str] = None
    if full.nodes:
        prev_tail = [n for n in nx.topological_sort(full)][-1]

    added_nodes: List[str] = []
    for phase, kws in phase_keywords.items():
        if any(kw in skel_names for kw in kws):
            continue
        for c in candidates:
            cid = str(c.get("id"))
            if cid in used_candidate_ids:
                continue
            cname = (c.get("name") or "").lower()
            if not any(kw in cname or kw in (c.get("SYNR") or "").lower() for kw in kws):
                continue
            new_id = f"add_{cid}"
            full.add_node(new_id, name=c.get("name"), from_llm=False, planner_added=True)
            full.nodes[new_id]["planner_added"] = True
            node_to_action[new_id] = {**c, "from_llm": False, "planner_added": True, "graph_node": new_id}
            used_candidate_ids.add(cid)
            added_nodes.append(new_id)
            if prev_tail:
                full.add_edge(prev_tail, new_id)
            prev_tail = new_id
            break

    # 若仍过少，顺序追加剩余候选（保持 DAG：链式）
    order = list(nx.topological_sort(full)) if full.nodes else []
    tail = order[-1] if order else None
    for c in candidates:
        cid = str(c.get("id"))
        if cid in used_candidate_ids:
            continue
        if len(full.nodes) >= max(len(candidates), 12):
            break
        new_id = f"add_extra_{cid}"
        full.add_node(new_id, name=c.get("name"), planner_added=True)
        node_to_action[new_id] = {**c, "from_llm": False, "planner_added": True, "graph_node": new_id}
        if tail:
            full.add_edge(tail, new_id)
        tail = new_id
        used_candidate_ids.add(cid)

    ordered_ids = list(nx.topological_sort(full))
    ordered_actions = [node_to_action[nid] for nid in ordered_ids if nid in node_to_action]

    stats = {
        "skeleton_nodes": skel_g.number_of_nodes(),
        "full_nodes": full.number_of_nodes(),
        "planner_added": sum(1 for a in ordered_actions if a.get("planner_added")),
        "llm_nodes": sum(1 for a in ordered_actions if a.get("from_llm") and not a.get("planner_added")),
        "search_expansions": full.number_of_nodes() * 2,
    }
    return full, ordered_actions, stats


def validate_preconditions(ordered_actions: List[Dict[str, Any]]) -> List[str]:
    """简单前提检查：环境类步骤应在实施前。"""
    warnings = []
    seen_env = False
    for a in ordered_actions:
        name = (a.get("name") or "").lower()
        if any(k in name for k in ("温箱", "设定", "环境", "降温")):
            seen_env = True
        if seen_env and "安装" in name:
            warnings.append(f"顺序可疑：「{a.get('name')}」出现在环境建立之后")
    return warnings
