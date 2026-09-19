"""
纯 LLM 检索 Agent：多轮调用 Neo4j / Chroma 工具（OpenAI tools API），禁止 mock。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from config import Config

from blueprints.FZSJ.JYLLM.multi_agent_engine import _resolve_jyllm_client_and_model
from blueprints.FZSJ.JYLLM.pure_llm.prompts import PURE_LLM_RETRIEVAL_SYSTEM, build_retrieval_user_prompt
from blueprints.FZSJ.JYLLM.pure_llm.pure_llm_tools import (
    build_pure_llm_tool_definitions,
    build_pure_llm_tool_dispatch,
    resolve_znwd_v6_tool_call_parser,
    run_pure_llm_tool,
)
from blueprints.FZSJ.JYLLM.pure_llm.retrieval_bundle import (
    RetrievalBundle,
    RetrievalError,
    ingest_tool_result,
    validate_bundle,
)

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

RetrievalEvent = Tuple[str, Dict[str, Any]]


def _parse_tool_calls(message) -> List[Dict[str, Any]]:
    out = []
    for tc in getattr(message, "tool_calls", None) or []:
        fn = tc.function
        raw = fn.arguments or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            args = {}
        out.append({"id": tc.id, "name": fn.name, "args": args})
    return out


def iter_retrieval_agent(
    user_description: str,
    *,
    llm_model_key: str = "",
) -> Generator[RetrievalEvent, None, RetrievalBundle]:
    """流式产出 tool_round；结束时 return RetrievalBundle。"""
    check_preflight()
    client, model_name, _ = _resolve_jyllm_client_and_model(None, llm_model_key)
    if not client:
        raise RetrievalError("LLM 客户端未初始化")

    driver = Config.NEO4J_DRIVER
    collection = Config.CHROMA_COLLECTION_OBJ
    dispatch = build_pure_llm_tool_dispatch(driver, collection)
    tools = build_pure_llm_tool_definitions()

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": PURE_LLM_RETRIEVAL_SYSTEM},
        {"role": "user", "content": build_retrieval_user_prompt(user_description)},
    ]

    bundle = RetrievalBundle(user_description=user_description)

    for round_i in range(MAX_TOOL_ROUNDS):
        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.2,
            "tools": tools,
            "tool_choice": "auto",
        }
        tcp = resolve_znwd_v6_tool_call_parser()
        if tcp:
            kwargs["extra_body"] = {"tool_call_parser": tcp}

        # 改为流式调用，实时产出 LLM 思考/回复文本
        stream_resp = client.chat.completions.create(**kwargs, stream=True)
        content_parts: List[str] = []
        tc_accumulator: List[Dict[str, Any]] = []

        for chunk in stream_resp:
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                content_parts.append(delta.content)
                yield ("retrieve_delta", {"text": delta.content})
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    tc_id = tc_delta.id
                    fn = tc_delta.function
                    fn_name = fn.name if fn else None
                    fn_args = fn.arguments if fn else None

                    if idx >= len(tc_accumulator):
                        tc_accumulator.append(
                            {"id": tc_id, "name": fn_name or "", "args": fn_args or ""}
                        )
                    else:
                        if fn_name:
                            tc_accumulator[idx]["name"] = fn_name
                        if fn_args:
                            tc_accumulator[idx]["args"] += fn_args

        msg_content = "".join(content_parts)
        tool_calls = []
        for tc in tc_accumulator:
            try:
                args = json.loads(tc["args"]) if tc["args"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": tc["id"], "name": tc["name"], "args": args})

        if not tool_calls:
            break

        messages.append(
            {
                "role": "assistant",
                "content": msg_content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"], ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            name = tc["name"]
            args = tc["args"]
            yield ("tool_round", {"tool": name, "args": args, "round": round_i + 1})
            result = run_pure_llm_tool(name, args, dispatch)
            ingest_tool_result(bundle, name, args, result)
            neo_n = len(result.get("neo4jResults") or [])
            chroma_n = len(result.get("chromaResults") or [])
            yield (
                "tool_result",
                {
                    "tool": name,
                    "neo_count": neo_n,
                    "chroma_count": chroma_n,
                    "error": result.get("error"),
                },
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": _compress_tool_result_for_llm(result),
                }
            )

    validate_bundle(bundle)
    return bundle


def run_retrieval_agent(
    user_description: str,
    *,
    llm_model_key: str = "",
    on_tool_round: Optional[Callable[[str, Dict], None]] = None,
) -> RetrievalBundle:
    """同步封装（兼容旧调用）。"""
    gen = iter_retrieval_agent(user_description, llm_model_key=llm_model_key)
    bundle: Optional[RetrievalBundle] = None
    try:
        while True:
            ev, data = next(gen)
            if ev == "tool_round" and on_tool_round:
                on_tool_round(data.get("tool", ""), data.get("args") or {})
    except StopIteration as si:
        bundle = si.value
    if bundle is None:
        raise RetrievalError("检索 Agent 未返回结果")
    return bundle


def check_preflight() -> None:
    from .preflight import check_pure_llm_dependencies

    check_pure_llm_dependencies()


def _compress_tool_result_for_llm(result: dict) -> str:
    lines = []
    if result.get("error"):
        lines.append(f"error: {result['error']}")
    neo = result.get("neo4jResults") or []
    if neo:
        lines.append(f"[Neo4j] {len(neo)} 条")
        for i, row in enumerate(neo[:15]):
            if isinstance(row, dict) and not row.get("error"):
                n = row.get("n") or {}
                m = row.get("m") or {}
                lines.append(f"  {i}: {_short_node(n)} -[{_rel(row)}]-> {_short_node(m)}")
    chroma = result.get("chromaResults") or []
    if chroma:
        lines.append(f"[Chroma] {len(chroma)} 条")
        for j, c in enumerate(chroma[:8]):
            if isinstance(c, dict):
                lines.append(f"  {j}: {str(c.get('title',''))[:40]} {str(c.get('content',''))[:120]}")
    return "\n".join(lines) or "(empty)"


def _short_node(node: Any) -> str:
    if not isinstance(node, dict):
        return "?"
    p = node.get("properties") if isinstance(node.get("properties"), dict) else node
    lab = (node.get("labels") or ["?"])[0] if node.get("labels") else "?"
    return f"{lab}:{p.get('Name', p.get('Id', '?'))}"


def _rel(row: dict) -> str:
    r = row.get("r")
    if isinstance(r, dict):
        return str(r.get("type") or "CON")
    return "CON"
