"""
ZNWD 快速路径共享 API（V5 / V6 Preflight 共用）。

实现仍位于 services_v5；本模块提供稳定对外符号，避免 V6 直接依赖 services_v5 私有函数名。
后续可将实现逐步迁入本文件。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .znwd_llm_intent_classifier import Intent, IntentResult, classify_intent, relation_target_kind


def build_retrieval_question(messages: List[Dict[str, Any]], question: str) -> str:
    from .services_v5 import _build_v5_retrieval_question

    return _build_v5_retrieval_question(messages, question)


def try_build_fast_path_program(
    intent_res: IntentResult,
    question: str,
    retrieval_question: str,
    messages: List[Dict[str, Any]],
    driver=None,
    routing=None,
) -> Optional[Dict[str, Any]]:
    from .znwd_business_templates import build_fast_program_from_template

    return build_fast_program_from_template(
        intent_res,
        question,
        retrieval_question,
        messages,
        driver=driver,
        routing=routing,
    )


def fast_program_normalize_ok(fast_in: Dict[str, Any], normalized: Dict[str, Any]) -> bool:
    from .services_v5 import _fast_program_normalize_ok

    return _fast_program_normalize_ok(fast_in, normalized)


def normalize_program(
    program: Optional[Dict[str, Any]],
    retrieval_question: str,
    user_question: str = "",
) -> Dict[str, Any]:
    from .services_v5 import _normalize_program

    return _normalize_program(program, retrieval_question, user_question)


def plan_thinking_display_text(program: Dict[str, Any], retrieval_question: str) -> str:
    from .services_v5 import _plan_thinking_display_text

    return _plan_thinking_display_text(program, retrieval_question)


def format_fast_direct_body(
    intent: Intent,
    question: str,
    retrieval_question: str,
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    program: Dict[str, Any],
) -> str:
    from .services_v5 import _format_fast_direct_body

    return _format_fast_direct_body(
        intent, question, retrieval_question, neo_rows, chroma_rows, program
    )


def intent_for_sufficiency(program: Dict[str, Any], retrieval_question: str) -> Intent:
    from .services_v5 import _intent_for_sufficiency

    return _intent_for_sufficiency(program, retrieval_question)


def is_retrieval_sufficient_for_intent(
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    question: str,
    intent: Intent,
) -> bool:
    from .services_v5 import is_retrieval_sufficient

    return is_retrieval_sufficient(neo_rows, chroma_rows, question, intent)


def template_display_name(program: Dict[str, Any]) -> str:
    from .znwd_business_templates import template_display_name as _name

    return _name(program)


__all__ = (
    "Intent",
    "IntentResult",
    "build_retrieval_question",
    "classify_intent",
    "fast_program_normalize_ok",
    "format_fast_direct_body",
    "intent_for_sufficiency",
    "is_retrieval_sufficient_for_intent",
    "normalize_program",
    "plan_thinking_display_text",
    "relation_target_kind",
    "template_display_name",
    "try_build_fast_path_program",
)
