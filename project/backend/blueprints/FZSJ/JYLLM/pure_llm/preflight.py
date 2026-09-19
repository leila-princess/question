"""纯 LLM 模式启动前检查：Neo4j / Chroma 必须可用，禁止静默 mock。"""
from __future__ import annotations

from config import Config


class PureLlmPreflightError(RuntimeError):
    pass


def check_pure_llm_dependencies() -> None:
    errors = []
    if not getattr(Config, "NEO4J_DRIVER", None):
        errors.append("Neo4j 未连接（检查 NEO4J_URI / 账号密码）")
    if not getattr(Config, "CHROMA_COLLECTION_OBJ", None):
        errors.append("Chroma 未初始化（检查 CHROMA_PATH / CHROMA_COLLECTION）")
    if not getattr(Config, "OPENAI_CLIENT", None):
        errors.append("LLM 未配置（检查 OPENAI_BASE_URL / 模型管理）")
    if errors:
        raise PureLlmPreflightError("；".join(errors))
