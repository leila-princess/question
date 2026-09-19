"""ZNWD2 SSE 主流水线。"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Generator, Optional, Tuple

from .config import default_query_limit, intent_model, intent_temperature
from .executor import QueryExecutor, format_result_preview
from .intent_parser import IntentParseError, parse_intent
from .path_planner import plan
from .result_formatter import format_answer, summarize_intent
from .znwd2_thinking_emit import emit_milestone, instant_thinking_enabled

logger = logging.getLogger(__name__)


def stream_znwd2_chat(
    client: Any,
    model: str,
    question: str,
    *,
    neo4j_driver: Any,
    skip_instant_thinking: bool = False,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """
    ZNWD2 流式问答：thinking → answer → done。

    Yields:
        (event_type, data) 元组，与 ZNWD1 SSE 协议兼容。
    """
    full_answer = ""
    thinking_parts: list[str] = []

    def _think(text: str, *, kind: str = "system") -> Generator[Tuple[str, Dict[str, Any]], None, None]:
        thinking_parts.append(text)
        yield emit_milestone(text, kind=kind)

    if not skip_instant_thinking and instant_thinking_enabled():
        yield ("thinking_start", {})
        yield from _think("已收到您的问题，正在分析…", kind="system")

    if neo4j_driver is None:
        msg = "Neo4j 未连接，无法查询知识图谱。"
        yield ("answer", {"content": msg})
        yield ("done", {"fullAnswer": msg, "fullThinking": "\n".join(thinking_parts)})
        return

    if client is None:
        msg = "LLM 未配置，无法解析意图。"
        yield ("answer", {"content": msg})
        yield ("done", {"fullAnswer": msg, "fullThinking": "\n".join(thinking_parts)})
        return

    resolved_model = intent_model(model) or model

    try:
        yield from _think("正在解析问题意图…", kind="route")
        intent_json = parse_intent(
            question,
            client=client,
            model=resolved_model,
            temperature=intent_temperature(),
        )
        intent_summary = summarize_intent(intent_json)
        yield from _think(f"意图解析：{intent_summary}", kind="route")
        yield from _think(f"结构化意图：{json.dumps(dict(intent_json), ensure_ascii=False)}", kind="route")

        execution_plan = plan(intent_json)
        if execution_plan.get("mode") == "macro":
            yield from _think(
                f"命中动作宏「{execution_plan.get('macro')}」，正在检索图谱…",
                kind="route",
            )
        else:
            steps = execution_plan.get("steps") or []
            yield from _think(f"动态路径规划：共 {len(steps)} 步动作", kind="route")

        executor = QueryExecutor(neo4j_driver, limit=default_query_limit())
        result = executor.execute(execution_plan)

        preview = format_result_preview(result.rows)
        if preview:
            yield from _think("【检索结果】", kind="retrieval")
            for line in preview:
                yield from _think(line, kind="retrieval")
        else:
            yield from _think("图谱检索未命中记录。", kind="retrieval")

        full_answer = format_answer(question, intent_json, result)
        yield ("answer", {"content": full_answer})
        yield ("done", {"fullAnswer": full_answer, "fullThinking": "\n".join(thinking_parts)})

    except IntentParseError as e:
        msg = f"意图解析失败：{e}"
        logger.warning("[ZNWD2] %s", msg)
        yield from _think(msg, kind="system")
        yield ("answer", {"content": msg})
        yield ("done", {"fullAnswer": msg, "fullThinking": "\n".join(thinking_parts)})
    except Exception as e:
        logger.exception("[ZNWD2] pipeline failed")
        msg = f"问答流水线异常：{type(e).__name__}: {e}"
        yield ("error", {"message": msg})
        yield ("done", {"fullAnswer": "", "fullThinking": "\n".join(thinking_parts)})
