"""Neo4j 检索 — 主路径由 LLM Agent 工具完成；直接调用时禁止 mock。"""
from __future__ import annotations

from config import Config


def retrieve_kg_and_actions(user_description: str, *, method_limit: int = 40):
    if not getattr(Config, "NEO4J_DRIVER", None):
        raise RuntimeError("Neo4j 未连接，请配置 NEO4J_URI 后重试")
    raise RuntimeError(
        "请使用 pure_llm_agent.run_retrieval_agent 通过 LLM 工具检索，不再支持自动 keyword 检索或 mock"
    )
