"""
ZNWD1 路由：LangChain ChatOpenAI + bind_tools（≤2 轮）→ RoutePlan JSON；流式下发 thinking。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from .prompts.routing_prompt_builder import build_routing_human, build_routing_system_prompt
from .route_plan_schema import parse_route_plan_json
from .znwd1_thinking_emit import (
    emit_milestone,
    humanize_tool_milestone,
    milestone_route_plan,
    route_stream_thinking_enabled,
    sanitize_route_stream_buffer,
    summarize_tool_result,
)
from .znwd1_tools import (
    build_znwd1_openai_tool_definitions,
    build_znwd1_tool_dispatch,
    canonical_tool_name,
    resolve_znwd1_tool_call_parser,
    znwd1_llm_request_timeout,
)

logger = logging.getLogger(__name__)

ROUTE_PLAN_INTERNAL = "route_plan_internal"


def _lc_text_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(content)


def _lc_kwargs_from_openai_client(
    client: Any,
    model: str,
    llm_limits: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    kw: Dict[str, Any] = {"model": model, "temperature": 0}
    if client:
        bu = getattr(client, "base_url", None)
        if bu is not None:
            kw["base_url"] = str(bu).rstrip("/")
        ak = getattr(client, "api_key", None)
        if ak:
            kw["api_key"] = ak
    if llm_limits:
        mo = llm_limits.get("max_output_tokens")
        if mo:
            try:
                kw["max_tokens"] = min(int(mo), 1024)
            except (TypeError, ValueError):
                pass
    tcp = resolve_znwd1_tool_call_parser()
    if tcp:
        kw["extra_body"] = {"tool_call_parser": tcp}
    kw["timeout"] = znwd1_llm_request_timeout()
    kw["max_retries"] = 0
    return kw


def _normalize_tool_call_args(tc: Any) -> Dict[str, Any]:
    if isinstance(tc, dict):
        raw = tc.get("args")
        if raw is None:
            raw = tc.get("arguments")
        tid = tc.get("id") or ""
        name = tc.get("name") or ""
    else:
        raw = getattr(tc, "args", None) or getattr(tc, "arguments", {})
        tid = getattr(tc, "id", "") or ""
        name = getattr(tc, "name", "") or ""
    if isinstance(raw, str):
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            args = {}
    else:
        args = dict(raw) if isinstance(raw, dict) else {}
    return {"id": tid, "name": canonical_tool_name(name), "args": args}


def _max_tool_rounds() -> int:
    try:
        return max(1, min(int(os.environ.get("ZNWD1_ROUTE_MAX_TOOL_ROUNDS", "2")), 3))
    except ValueError:
        return 2


def stream_route_plan(
    client: Any,
    model: str,
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    driver: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    # 承上省略短句自动补全（不依赖 LLM 调工具）
    # 如「指标呢」→ 读取历史 → 「无人机有什么考核指标？」
    from .znwd1_history_context import enrich_short_follow_up

    effective_question, history_snapshot = enrich_short_follow_up(
        question, user_id=user_id, session_id=session_id
    )
    if effective_question != question:
        logger.info(
            "[ZNWD1][DEBUG][承上补全] original=%r -> enriched=%r",
            question,
            effective_question,
        )
        yield emit_milestone(f"结合上下文，理解为「{effective_question}」…", kind="route")

    tool_defs = build_znwd1_openai_tool_definitions()
    dispatch = build_znwd1_tool_dispatch(
        user_id=user_id, session_id=session_id, driver=driver
    )
    llm = ChatOpenAI(**_lc_kwargs_from_openai_client(client, model, llm_limits))
    llm_tools = llm.bind_tools(tool_defs)

    session_hint = f"(session_id={session_id[:8]}…)" if session_id else ""
    human = build_routing_human((effective_question or "").strip(), session_hint=session_hint)

    messages: List[Any] = [
        SystemMessage(content=build_routing_system_prompt()),
        HumanMessage(content=human),
    ]
    max_rounds = _max_tool_rounds()
    probe_snapshot: Dict[str, Any] = {}
    # history_snapshot 可能已由 enrich_short_follow_up 预填（承上短句补全）
    if not history_snapshot:
        history_snapshot: Dict[str, Any] = {}

    yield emit_milestone("正在理解您的问题并规划检索方案…", kind="route")

    for tr in range(max_rounds + 1):
        is_last = tr >= max_rounds
        tool_think_raw = ""
        tool_think_emitted = 0
        ai: Optional[AIMessage] = None
        # 默认不流式输出路由 LLM 原始推理（多为英文/JSON）；仅输出里程碑与工具摘要
        stream_thinking = route_stream_thinking_enabled() and tr < max_rounds

        try:
            gathered: Any = None
            for chunk in llm_tools.stream(messages):
                if gathered is None:
                    gathered = chunk
                else:
                    gathered = gathered + chunk
                piece = _lc_text_content(getattr(chunk, "content", None))
                if piece:
                    tool_think_raw += piece
                    if (
                        '"answer_strategy"' in tool_think_raw
                        or '"intended_business_id"' in tool_think_raw
                        or '"probe_snapshot"' in tool_think_raw
                    ):
                        stream_thinking = False
                    if stream_thinking:
                        delta, tool_think_emitted = sanitize_route_stream_buffer(
                            tool_think_raw, tool_think_emitted
                        )
                        if delta.strip():
                            # 流式推理逐字输出，勿带 kind=route（前端会对每个 chunk 误加【路由】）
                            yield ("thinking", {"content": delta})
            if gathered is not None:
                ai = gathered  # type: ignore[assignment]
        except Exception as e:
            logger.warning("[ZNWD1][route] stream failed round=%s: %s", tr, e)
            yield emit_milestone(f"路由模型调用异常，正在重试（{type(e).__name__}）…")
            try:
                ai = llm_tools.invoke(messages)
            except Exception as e2:
                logger.error("[ZNWD1][route] invoke failed round=%s: %s", tr, e2)
                yield (
                    "error",
                    {
                        "message": (
                            f"路由模型无响应或调用失败（{type(e2).__name__}）。"
                            "请检查本地 LLM 是否支持 tool_calls，以及 ZNWD1_TOOL_CALL_PARSER 是否与 vLLM 的 "
                            "--tool-call-parser 一致（如 qwen3 / hermes）；也可临时设 ZNWD1_TOOL_CALL_PARSER=none。"
                        )
                    },
                )
                return

        if ai is None:
            break

        messages.append(ai)
        tool_calls = getattr(ai, "tool_calls", None) or []

        if tool_calls and not is_last:
            for tc in tool_calls:
                norm = _normalize_tool_call_args(tc)
                name = norm["name"]
                args = norm["args"]
                yield emit_milestone(humanize_tool_milestone(name, args))
                fn = dispatch.get(name)
                if fn:
                    try:
                        result = fn(**args)
                    except TypeError:
                        result = fn()
                else:
                    result = json.dumps({"error": f"unknown_tool:{name}"}, ensure_ascii=False)
                summary = summarize_tool_result(name, result)
                if summary:
                    yield emit_milestone(summary)
                if name == "znwd1_probe_keyword":
                    try:
                        probe_data = json.loads(result)
                        if isinstance(probe_data, dict) and probe_data.get("best"):
                            probe_snapshot = {"best": probe_data["best"], "hits": probe_data.get("hits", [])}
                    except (json.JSONDecodeError, TypeError):
                        pass
                if name == "znwd1_history_recent_qa":
                    try:
                        hist_data = json.loads(result)
                        if isinstance(hist_data, dict):
                            history_snapshot = hist_data
                    except (json.JSONDecodeError, TypeError):
                        pass
                messages.append(
                    ToolMessage(content=result, tool_call_id=norm["id"] or name)
                )
            continue

        text = _lc_text_content(ai.content)
        plan = parse_route_plan_json(text)
        if not plan.get("retrieval_question"):
            plan["retrieval_question"] = question
        if not plan.get("keyword"):
            plan["keyword"] = question[:80]
        if probe_snapshot and not plan.get("probe_snapshot"):
            plan["probe_snapshot"] = probe_snapshot
        if history_snapshot and not plan.get("history_snapshot"):
            plan["history_snapshot"] = history_snapshot
        route_summary = milestone_route_plan(plan)
        if route_summary:
            yield emit_milestone(route_summary, kind="route")
        yield emit_milestone("检索方案已确定。", kind="route")
        yield (ROUTE_PLAN_INTERNAL, {"plan": plan})
        return

    text = _lc_text_content(
        messages[-1].content if isinstance(messages[-1], AIMessage) else ""
    )
    plan = parse_route_plan_json(text)
    plan.setdefault("retrieval_question", question)
    plan.setdefault("keyword", question[:80])
    if probe_snapshot:
        plan.setdefault("probe_snapshot", probe_snapshot)
    if history_snapshot:
        plan.setdefault("history_snapshot", history_snapshot)
    yield (ROUTE_PLAN_INTERNAL, {"plan": plan})


def run_route_plan(
    client: Any,
    model: str,
    question: str,
    **kwargs: Any,
) -> Tuple[Dict[str, Any], List[str]]:
    plan: Dict[str, Any] = {}
    notes: List[str] = []
    for ev, data in stream_route_plan(client, model, question, **kwargs):
        if ev == ROUTE_PLAN_INTERNAL:
            plan = data.get("plan") or {}
        elif ev == "thinking":
            notes.append(str(data.get("content") or ""))
    return plan, notes
