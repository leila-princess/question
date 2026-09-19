"""ZNWD2 主控制器：同步问答编排。"""

from __future__ import annotations

from typing import Any, Optional

from .config import default_query_limit, intent_model, intent_temperature
from .executor import QueryExecutor
from .intent_parser import parse_intent
from .path_planner import plan
from .result_formatter import format_answer


def answer(
    user_query: str,
    *,
    driver: Any,
    client: Any,
    model: Optional[str] = None,
) -> str:
    """
    同步问答入口：意图解析 → 路径规划 → 执行 → 格式化。

    Args:
        user_query: 用户问题
        driver: Neo4j driver
        client: OpenAI 兼容客户端
        model: LLM 模型名（空则用 client 默认）

    Returns:
        自然语言答案
    """
    if driver is None:
        return "Neo4j 未连接，无法查询知识图谱。"
    if client is None:
        return "LLM 未配置，无法解析意图。"

    resolved_model = intent_model(model or "")
    if not resolved_model:
        resolved_model = model or "gpt-4o-mini"

    intent_json = parse_intent(
        user_query,
        client=client,
        model=resolved_model,
        temperature=intent_temperature(),
    )
    execution_plan = plan(intent_json)
    executor = QueryExecutor(driver, limit=default_query_limit())
    result = executor.execute(execution_plan)
    return format_answer(user_query, intent_json, result)
