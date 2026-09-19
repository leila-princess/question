"""
纯 LLM 检索工具层：复用 ZNWD V6 显式 Neo/Chroma 工具，禁止 mock。
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

from blueprints.GLCX.ZNWD.znwd_v6_tools import (
    _EXPLICIT_RETRIEVAL_TOOLS_OPENAI,
    build_znwd_v6_tool_dispatch,
    explicit_tool_chroma_vector_search,
    explicit_tool_neo_list_nodes,
    explicit_tool_neo_node_name_search,
    explicit_tool_neo_relation_by_ids,
    explicit_tool_neo_relation_name_search,
    resolve_znwd_v6_tool_call_parser,
)

# 试验方案规划场景：改写 description 前缀
_PURE_LLM_TOOL_NAMES = {
    "TOOL_neo_relation_name_search",
    "TOOL_neo_relation_by_ids",
    "TOOL_neo_node_name_search",
    "TOOL_neo_list_nodes",
    "TOOL_chroma_vector_search",
}


def build_pure_llm_tool_definitions() -> List[Dict[str, Any]]:
    out = []
    for spec in _EXPLICIT_RETRIEVAL_TOOLS_OPENAI:
        fn = (spec.get("function") or {})
        if fn.get("name") not in _PURE_LLM_TOOL_NAMES:
            continue
        s = copy.deepcopy(spec)
        desc = str((s.get("function") or {}).get("description") or "")
        s["function"]["description"] = (
            "【试验方案规划】"
            + desc
            + " 返回的 Id 必须用于后续 PDDL 动作命名 method-<Id>，禁止编造节点。"
        )
        out.append(s)
    return out


def build_pure_llm_tool_dispatch(
    driver: Any,
    collection: Any,
    user_interest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    full = build_znwd_v6_tool_dispatch(driver, collection, user_interest, None, None)
    return {k: full[k] for k in _PURE_LLM_TOOL_NAMES if k in full}


def run_pure_llm_tool(name: str, args: Dict[str, Any], dispatch: Dict[str, Callable]) -> Dict[str, Any]:
    fn = dispatch.get(name)
    if not fn:
        return {"error": f"unknown tool {name}", "neo4jResults": [], "chromaResults": []}
    try:
        return fn(args or {})
    except Exception as e:
        return {"error": str(e), "neo4jResults": [], "chromaResults": []}


__all__ = [
    "build_pure_llm_tool_definitions",
    "build_pure_llm_tool_dispatch",
    "run_pure_llm_tool",
    "resolve_znwd_v6_tool_call_parser",
]
