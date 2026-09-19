"""
ZNWD4 实验开关（意图锁等）。

环境变量：
  ZNWD4_ROUTE_LOCK_MODE — rules | llm | hybrid（默认 rules）
    rules  — 仅程序规则锁（现网默认）
    llm    — 单次 LLM 识别 purpose/route_id/keywords
    hybrid — 规则未命中时 LLM 兜底
  ZNWD4_ROUTE_LOCK_LOG_COMPARE — 1/true 时在 hybrid 下打 rules vs llm 对比日志
    （规则已命中时不额外调 LLM，仅记 llm=skipped_rules_hit）
  ZNWD4_ROUTE_LOCK_LLM_MIN_CONFIDENCE — LLM 锁最低置信度，默认 0.5
  ZNWD4_ANSWER_SYNTHESIS — llm | template（默认 template）
    template — 图谱/清单类业务模板直出（format_direct_answer）；仅 db_synthesize/文档检索走 LLM
    llm       — 拼装/向量库/环境属性等可走 LLM 整合（清单类仍模板直出）
"""

from __future__ import annotations

import os

from .business_catalog import BIZ_DOCUMENT, BIZ_NODE_PROP
from .user_biz_catalog import (
    COMP_EQUIP_PROJECT_ENV_SETTINGS,
    COMP_INDICATOR_THEN_METHOD,
    COMP_INTENT_PLAN,
    COMP_PROJECT_ENV_METHOD,
    COMP_PROJECT_THEN_METHOD,
    UBIZ_ENVIRONMENT,
)

# 开启 ZNWD4_ANSWER_SYNTHESIS=llm 时，这些 user_biz 优先走 LLM 合成（非清单模板）
_LLM_SYNTHESIS_USER_BIZ = frozenset(
    {
        COMP_PROJECT_THEN_METHOD,
        COMP_INDICATOR_THEN_METHOD,
        COMP_EQUIP_PROJECT_ENV_SETTINGS,
        COMP_PROJECT_ENV_METHOD,
        COMP_INTENT_PLAN,
    }
)


def znwd4_route_lock_mode() -> str:
    raw = (os.environ.get("ZNWD4_ROUTE_LOCK_MODE") or "rules").strip().lower()
    if raw in ("llm", "hybrid", "rules"):
        return raw
    if raw in ("rule", "regex", "program"):
        return "rules"
    return "rules"


def route_lock_log_compare() -> bool:
    raw = (os.environ.get("ZNWD4_ROUTE_LOCK_LOG_COMPARE") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def route_lock_llm_min_confidence() -> float:
    raw = (os.environ.get("ZNWD4_ROUTE_LOCK_LLM_MIN_CONFIDENCE") or "0.5").strip()
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.5


def znwd4_answer_synthesis_mode() -> str:
    raw = (os.environ.get("ZNWD4_ANSWER_SYNTHESIS") or "template").strip().lower()
    if raw in ("1", "true", "yes", "on", "llm"):
        return "llm"
    return "template"


# 图谱清单模板直出业务（有检索命中时不走 LLM 总结）
_TEMPLATE_BUSINESS_IDS = frozenset(
    {
        "BIZ_REL_ONE_HOP",
        "BIZ_REL_REVERSE_ONE_HOP",
        "BIZ_REL_TWO_HOP",
        "BIZ_ENUM_NODES",
        "BIZ_NODE_PROP",
        "BIZ_NODE_PROP_REV",
        "BIZ_NODE_KW",
        "BIZ_EQUIP_PROJECT_BY_METHOD",
        "BIZ_PROJECT_TO_METHOD",
        "BIZ_METHOD_CONTENT",
    }
)


def is_template_business(business_id: str) -> bool:
    return (business_id or "").strip() in _TEMPLATE_BUSINESS_IDS


def znwd4_use_llm_answer_synthesis(
    business_id: str = "",
    *,
    strategy: str = "",
    user_biz: str = "",
) -> bool:
    """llm 模式下：拼装/向量库/环境属性可走合成；单跳清单类仍模板直出。"""
    if znwd4_answer_synthesis_mode() != "llm":
        return False
    ub = (user_biz or "").strip()
    if ub in _LLM_SYNTHESIS_USER_BIZ:
        return True
    bid = (business_id or "").strip()
    if bid == BIZ_DOCUMENT:
        return False
    if strategy == "db_direct_doc":
        return False
    if bid == BIZ_NODE_PROP and ub == UBIZ_ENVIRONMENT:
        return True
    if is_template_business(bid):
        return False
    return False
