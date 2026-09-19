"""
ZNWD1 路由工具：history×2 + probe_keyword（OpenAI schema + dispatch）。
"""

from __future__ import annotations

import json
import logging
import os
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional

from .keyword_probe import compress_probe_for_tool, probe_keyword
from .memory_store import get_memory_store

logger = logging.getLogger(__name__)


def znwd1_llm_request_timeout() -> float:
    """LangChain / OpenAI 兼容网关请求超时（秒），避免本地 LLM 无响应时 SSE 长期无字节。"""
    import os

    raw = (os.environ.get("ZNWD1_LLM_TIMEOUT") or os.environ.get("OPENAI_TIMEOUT") or "180").strip()
    try:
        return max(30.0, min(float(raw), 600.0))
    except ValueError:
        return 180.0


def resolve_znwd1_tool_call_parser() -> Optional[str]:
    raw = (
        os.environ.get("ZNWD1_TOOL_CALL_PARSER")
        or os.environ.get("ZNWD_V6_TOOL_CALL_PARSER")
        or "hermes"
    ).strip().lower()
    if raw in ("", "none", "off", "disabled", "false", "0", "no"):
        return None
    if raw in ("hermes", "qwen3"):
        return raw
    return "hermes"


def _pair_messages_to_rounds(msgs: List[dict]) -> List[Dict[str, str]]:
    rounds: List[Dict[str, str]] = []
    pending_u: Optional[str] = None
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        content = str(m.get("content") or "")
        if role == "user":
            pending_u = content
        elif role == "assistant" and pending_u is not None:
            rounds.append({"user": pending_u[:8000], "assistant": content[:8000]})
            pending_u = None
    return rounds


def history_recent_qa(user_id: Optional[str], session_id: Optional[str], n: int = 3) -> Dict[str, Any]:
    store = get_memory_store()
    if not store or not user_id or not session_id:
        return {"error": "memory_unavailable", "rounds": []}
    try:
        n = max(1, min(int(n), 10))
    except (TypeError, ValueError):
        n = 3
    msgs = store.get_messages(user_id, session_id)
    rounds = _pair_messages_to_rounds(msgs)
    return {"rounds": rounds[-n:] if rounds else [], "total_rounds_in_store": len(rounds)}


def history_similar_questions(
    user_id: Optional[str],
    session_id: Optional[str],
    query: str,
    top_k: int = 3,
) -> Dict[str, Any]:
    store = get_memory_store()
    if not store or not user_id or not session_id:
        return {"error": "memory_unavailable", "matches": []}
    q = (query or "").strip()
    if not q:
        return {"matches": []}
    try:
        top_k = max(1, min(int(top_k), 10))
    except (TypeError, ValueError):
        top_k = 3
    msgs = store.get_messages(user_id, session_id)
    rounds = _pair_messages_to_rounds(msgs)
    scored: List[tuple] = []
    for r in rounds:
        u = (r.get("user") or "").strip()
        if not u:
            continue
        score = SequenceMatcher(None, q, u).ratio()
        scored.append((score, u, (r.get("assistant") or "")[:600]))
    scored.sort(key=lambda x: x[0], reverse=True)
    matches = [
        {"score": round(s, 4), "user_question": u[:2000], "assistant_excerpt": a}
        for s, u, a in scored[:top_k]
    ]
    return {"matches": matches, "query": q}


def build_znwd1_openai_tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "znwd1_history_recent_qa",
                "description": (
                    "读取本会话最近 n 轮 user+assistant。指代不明（它/上述/继续）时先调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer", "description": "轮数，默认 3", "default": 3},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "znwd1_history_similar_questions",
                "description": "在历史用户句中找与 query 最相似的 top_k 条及助手摘要。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "比对用问句"},
                        "top_k": {"type": "integer", "default": 3},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "znwd1_probe_keyword",
                "description": (
                    "在 Neo4j 实测 keyword 命中的节点类型、Id、名称。"
                    "有明确实体名时建议调用；结果写入 RoutePlan.probe_snapshot。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "待探测关键词"},
                        "label_hints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "优先节点类型，如 TestProject",
                        },
                    },
                    "required": ["keyword"],
                },
            },
        },
    ]


def canonical_tool_name(raw: str) -> str:
    n = (raw or "").strip()
    if n.lower().startswith("znwd1_"):
        return n
    return n


def build_znwd1_tool_dispatch(
    *,
    user_id: Optional[str],
    session_id: Optional[str],
    driver: Any,
) -> Dict[str, Callable[..., str]]:
    def _recent(n: int = 3) -> str:
        return json.dumps(history_recent_qa(user_id, session_id, n), ensure_ascii=False)

    def _similar(query: str, top_k: int = 3) -> str:
        return json.dumps(
            history_similar_questions(user_id, session_id, query, top_k),
            ensure_ascii=False,
        )

    def _probe(keyword: str, label_hints: Optional[List[str]] = None) -> str:
        res = probe_keyword(driver, keyword, label_hints=label_hints or [])
        return json.dumps(compress_probe_for_tool(res), ensure_ascii=False)

    return {
        "znwd1_history_recent_qa": lambda **kw: _recent(kw.get("n", 3)),
        "znwd1_history_similar_questions": lambda **kw: _similar(
            kw.get("query", ""), kw.get("top_k", 3)
        ),
        "znwd1_probe_keyword": lambda **kw: _probe(
            kw.get("keyword", ""), kw.get("label_hints")
        ),
    }
