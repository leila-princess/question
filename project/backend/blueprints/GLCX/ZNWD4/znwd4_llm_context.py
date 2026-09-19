"""
LLM 承上启下：用对话摘要 + 最近 3 轮补全省略问句；每轮问答后滚动更新摘要。
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from .memory_store import get_memory_store
from .znwd4_history_context import format_history_rounds, load_history_from_store

logger = logging.getLogger(__name__)


def llm_context_enrich_enabled() -> bool:
    """Multi-turn context enrich; off by default for routing-only benchmarks."""
    return (os.environ.get("ZNWD4_LLM_CONTEXT_ENRICH") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


_SUMMARY_GUARD: Dict[str, float] = {}
_SUMMARY_GUARD_LOCK = threading.Lock()

_RESOLVE_SYSTEM = """你是装备试验知识库多轮对话的上下文理解助手。
根据【对话摘要】与【最近对话】，判断【当前问题】是否省略了上文实体或查询对象。
规则：
1. 若当前问题已可独立理解（含完整主语与查询目标），原样输出，不要改写。
2. 若省略主语/对象/装备名/环境（如「有哪些考核指标」「那它的方法呢」「复杂地理呢」），补全为一句完整中文问句。
3. 只补全省略信息，不改变用户意图；不要添加用户未暗示的过滤条件。
4. 只输出一行问句，不要解释、不要 Markdown、不要 JSON。

示例：
- 上问「无人机A型有哪些试验项目」→ 当前「有哪些考核指标」→ 无人机A型有哪些考核指标
- 上问「无人机有哪些试验项目」→ 当前「坦克呢」→ 坦克有哪些试验项目"""


def build_routing_memory_block(
    user_id: Optional[str],
    session_id: Optional[str],
    *,
    max_rounds: int = 3,
) -> str:
    """路由 Step1/2 注入：滚动摘要 + 最近 N 轮对话。"""
    if not user_id or not session_id:
        return ""
    snapshot = load_history_from_store(user_id, session_id, n=max_rounds)
    rounds = snapshot.get("rounds") if isinstance(snapshot.get("rounds"), list) else []
    store = get_memory_store()
    summary = store.get_summary(user_id, session_id) if store else ""
    parts: List[str] = []
    if (summary or "").strip():
        parts.append(f"【对话摘要】\n{summary.strip()}")
    hist = format_history_rounds(rounds, max_rounds=max_rounds)
    if hist:
        parts.append(f"【最近对话（最多{max_rounds}轮）】\n{hist}")
    return "\n\n".join(parts)
    raw = (os.environ.get("ZNWD4_LLM_CONTEXT_ENRICH") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _chat_complete(client: Any, model: str, messages: List[Dict[str, str]], *, max_tokens: int = 256) -> str:
    if not client or not model:
        return ""
    try:
        from .routing_context import record_llm_call

        record_llm_call("context_enrich")
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("[ZNWD4][llm_context] LLM call failed: %s", e)
        return ""


def _clean_resolved_question(raw: str, fallback: str) -> str:
    s = (raw or "").strip()
    if not s:
        return fallback
    s = re.sub(r"^[\"'「『]|[\"'」』]$", "", s).strip()
    s = re.sub(r"^(输出|问句|补全后)[:：]\s*", "", s, flags=re.I).strip()
    first = s.split("\n", 1)[0].strip()
    if len(first) < 2 or len(first) > 120:
        return fallback
    return first


def resolve_effective_question_with_llm(
    question: str,
    *,
    summary: str,
    rounds: List[Dict[str, str]],
    client: Any,
    model: str,
) -> str:
    q = (question or "").strip()
    if not q or not rounds:
        return q

    rounds_text = format_history_rounds(rounds, max_rounds=3)
    user_content = (
        f"【对话摘要】\n{(summary or '（无）').strip()}\n\n"
        f"【最近对话】\n{rounds_text}\n\n"
        f"【当前问题】\n{q}"
    )
    out = _chat_complete(
        client,
        model,
        [
            {"role": "system", "content": _RESOLVE_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        max_tokens=120,
    )
    resolved = _clean_resolved_question(out, q)
    if resolved != q:
        logger.info("[ZNWD4][llm_context] enrich %r -> %r", q, resolved)
    return resolved


def enrich_question_with_history(
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    client: Any = None,
    model: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    用 LLM + 摘要 + 最近 3 轮对话补全省略问句。
    返回 (effective_question, history_snapshot)。
    """
    q = (question or "").strip()
    snapshot = load_history_from_store(user_id, session_id, n=3)
    rounds = snapshot.get("rounds") if isinstance(snapshot.get("rounds"), list) else []

    if not llm_context_enrich_enabled() or not rounds or not client or not model:
        return q, snapshot

    store = get_memory_store()
    summary = store.get_summary(user_id, session_id) if store and user_id and session_id else ""

    effective = resolve_effective_question_with_llm(
        q,
        summary=summary,
        rounds=rounds,
        client=client,
        model=model,
    )
    snapshot = dict(snapshot)
    snapshot["summary"] = summary
    snapshot["context_enrich"] = "llm"
    return effective, snapshot


def _merge_summary_prompt(old_summary: str, user_q: str, assistant_a: str) -> str:
    return (
        "请将「旧摘要」与「最新一轮问答」合并为一段不超过200字的中文摘要。"
        "须保留：讨论主题、装备/环境/项目/指标等实体名、用户关注点、已查到的要点。"
        "不要 Markdown，不要 JSON，只输出摘要正文。\n\n"
        f"旧摘要：{(old_summary or '（无）').strip()}\n\n"
        f"最新一轮：\n用户：{user_q[:800]}\n助手：{assistant_a[:800]}"
    )


def refresh_session_summary(
    user_id: str,
    session_id: str,
    user_question: str,
    assistant_answer: str,
    client: Any,
    model: str,
) -> None:
    """同步更新滚动摘要（供后台线程调用）。"""
    store = get_memory_store()
    if not store or not client or not model:
        return

    guard_key = f"{user_id}:{session_id}"
    import time

    now = time.time()
    with _SUMMARY_GUARD_LOCK:
        last = _SUMMARY_GUARD.get(guard_key, 0)
        if now - last < 1.0:
            return
        _SUMMARY_GUARD[guard_key] = now

    old = store.get_summary(user_id, session_id)
    prompt = _merge_summary_prompt(old, user_question, assistant_answer)
    new_summary = _chat_complete(
        client,
        model,
        [{"role": "user", "content": prompt}],
        max_tokens=400,
    )
    new_summary = (new_summary or old or "").strip()[:400]
    if not new_summary:
        new_summary = old[:200]

    store.update_merge(
        user_id,
        session_id,
        new_summary,
        user_question,
        assistant_answer,
    )
    logger.info(
        "[ZNWD4][llm_context] summary updated session=%s len=%d",
        session_id[:12] if session_id else "",
        len(new_summary),
    )


def refresh_session_summary_async(
    user_id: Optional[str],
    session_id: Optional[str],
    user_question: str,
    assistant_answer: str,
    client: Any,
    model: str,
) -> None:
    if not user_id or not session_id or not (assistant_answer or "").strip():
        return
    if not llm_context_enrich_enabled():
        return
    t = threading.Thread(
        target=refresh_session_summary,
        args=(user_id, session_id, user_question, assistant_answer, client, model),
        daemon=True,
    )
    t.start()


__all__ = [
    "llm_context_enrich_enabled",
    "build_routing_memory_block",
    "enrich_question_with_history",
    "resolve_effective_question_with_llm",
    "refresh_session_summary",
    "refresh_session_summary_async",
]
