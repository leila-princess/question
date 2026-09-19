"""章节号自然排序工具（避免 1, 10, 11, 2 字符串序）。"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def section_no_sort_key(section_no: str) -> Tuple:
    parts = (section_no or "").strip().lstrip(".").split(".")
    out: List[str] = []
    for p in parts:
        if not p:
            continue
        if p.isdigit():
            out.append(f"{int(p):08d}")
        else:
            out.append(f"z{p.lower()}")
    return tuple(out) if out else ("",)


def sort_by_section_no(items: List[Dict[str, Any]], key_field: str = "no") -> List[Dict[str, Any]]:
    return sorted(items, key=lambda x: section_no_sort_key(str(x.get(key_field) or x.get("sectionNo") or "")))


def sort_toc_tree(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """递归按 sectionNo 排序目录树兄弟节点。"""
    if not nodes:
        return nodes
    sorted_nodes = sorted(
        nodes,
        key=lambda n: section_no_sort_key(str(n.get("sectionNo") or n.get("no") or "")),
    )
    for node in sorted_nodes:
        children = node.get("children")
        if isinstance(children, list) and children:
            node["children"] = sort_toc_tree(children)
    return sorted_nodes
