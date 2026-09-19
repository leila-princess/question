"""
ZNWD 意图/路由模式开关。

ZNWD_INTENT_MODE=hybrid  规则+图谱优先，低置信度 LLM 兜底（默认）
ZNWD_INTENT_MODE=rule    规则意图 + 模板（与历史行为一致）
ZNWD_INTENT_MODE=llm     LLM 选业务模板与槽位，可调用关键词→节点类型工具

兼容旧变量 ZNWD_USE_LLM_INTENT=1（等价于 llm，但仅影响 classify_intent，建议改用 INTENT_MODE）。
"""

from __future__ import annotations

import os

_VALID = frozenset({"rule", "llm", "hybrid"})


def get_intent_mode() -> str:
    mode = (os.environ.get("ZNWD_INTENT_MODE") or "").strip().lower()
    if mode in _VALID:
        return mode
    legacy = os.environ.get("ZNWD_USE_LLM_INTENT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    return "llm" if legacy else "hybrid"


def is_llm_routing_enabled() -> bool:
    return get_intent_mode() == "llm"


def is_rule_routing_enabled() -> bool:
    return get_intent_mode() == "rule"


def is_hybrid_routing_enabled() -> bool:
    return get_intent_mode() == "hybrid"
