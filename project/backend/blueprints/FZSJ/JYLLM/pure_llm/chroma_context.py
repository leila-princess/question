"""Chroma 检索 — 主路径由 LLM Agent 调用工具，此模块仅供非 Agent 场景。"""
from __future__ import annotations

from blueprints.FZSJ.JYLLM.search_tool import get_search_tool


def retrieve_chroma_context(query: str, n_results: int = 8):
    tool = get_search_tool()
    hits = tool.search_by_keyword(query, n_results=n_results)
    if not hits:
        raise RuntimeError("Chroma 检索无结果，请检查向量库是否已入库")
    return hits


def format_chroma_for_prompt(hits, max_chars: int = 6000) -> str:
    parts = []
    total = 0
    for i, h in enumerate(hits, 1):
        block = f"[{i}] {h.get('content', '')}\n"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)
