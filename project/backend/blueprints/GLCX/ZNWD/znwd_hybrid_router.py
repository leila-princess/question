"""
ZNWD hybrid 路由：规则 + 图谱关键词优先，低置信度时 LLM 流式兜底。
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from .intent_classifier import Intent, IntentResult, classify_intent as rule_classify_intent, plain_intent_slice
from .znwd_fast_route import relation_target_kind
from .znwd_keyword_resolver import resolve_focus_fast
from .znwd_llm_router import (
    RoutingDecision,
    llm_route_question_stream,
)
from .znwd_template_config import get_template_config

logger = logging.getLogger(__name__)


def _confidence_threshold() -> float:
    try:
        return float(os.environ.get("ZNWD_ROUTE_CONFIDENCE_THRESHOLD", "0.72"))
    except ValueError:
        return 0.72


def _llm_fallback_enabled() -> bool:
    return os.environ.get("ZNWD_ROUTE_LLM_FALLBACK", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _progress(on_progress: Optional[Callable[[str], None]], line: str) -> None:
    if on_progress and line:
        on_progress(line)


def _match_template_from_json(question: str, intent: Intent) -> Optional[str]:
    cfg = get_template_config()
    if not cfg or not cfg.is_loaded():
        return None
    tpl = cfg.match_template(question, intent.value)
    if tpl:
        return str(tpl.get("template_code") or "").strip()
    return None


def _infer_business_template(
    intent: Intent,
    question: str,
    retrieval_question: str,
    *,
    focus: str,
    from_type: str,
    target_kind: str,
) -> str:
    q = plain_intent_slice(retrieval_question) or plain_intent_slice(question) or question
    json_tpl = _match_template_from_json(q, intent)
    if json_tpl:
        return json_tpl

    if intent == Intent.ENUMERATION:
        return "BIZ_ENUM"
    if intent == Intent.STANDARD_CHAPTER:
        return "BIZ_DOCUMENT"

    if intent != Intent.RELATION_QUERY:
        return ""

    tgt = target_kind or relation_target_kind(q)
    if tgt == "method" and len(focus) >= 2:
        if re.search(r"(测试|试验).*(方法|步骤)", q) or from_type == "TestProject":
            return "BIZ_PROJECT_TO_METHOD"
    if tgt == "equipment":
        return "BIZ_EQUIP_DISCOVERY"
    if focus and from_type:
        if tgt in ("method", "indicator") and from_type in ("Equipment", "Environment"):
            return "BIZ_REL_TWO_HOP"
        return "BIZ_REL_ONE_HOP"
    return ""


def _compute_confidence(
    ir: IntentResult,
    *,
    neo_score: float,
    focus: str,
    matched_name: str,
    business_template: str,
    from_type: str,
) -> float:
    score = 0.35
    if ir.reason and "规则" in ir.reason:
        score += 0.12
    if ir.intent in (Intent.ENUMERATION, Intent.STANDARD_CHAPTER):
        score += 0.2
    if business_template:
        score += 0.15
    if neo_score >= 0.85:
        score += 0.25
    elif neo_score >= 0.6:
        score += 0.15
    elif neo_score >= 0.4:
        score += 0.08
    if from_type:
        score += 0.1
    if focus and matched_name and focus == matched_name:
        score += 0.08
    elif focus and matched_name and (focus in matched_name or matched_name in focus):
        score += 0.05
    return min(1.0, score)


def _tier1_routing(
    question: str,
    retrieval_question: str,
    driver: Any,
    *,
    on_progress: Optional[Callable[[str], None]] = None,
) -> RoutingDecision:
    _progress(on_progress, "正在根据问法与知识图谱匹配业务类型…\n")
    ir = rule_classify_intent(retrieval_question or question)
    t0 = time.perf_counter()
    best = resolve_focus_fast(driver, question, retrieval_question) if driver else {}
    neo_ms = float(best.get("neo_ms") or (time.perf_counter() - t0) * 1000)

    focus = str(best.get("focus") or "").strip()
    from_type = str(best.get("node_type") or "").strip()
    matched = str(best.get("matched_name") or focus).strip()
    neo_score = float(best.get("score") or 0.0)
    if not neo_score and best.get("resolution"):
        neo_score = float((best.get("resolution") or {}).get("score") or 0.0)

    q_slice = plain_intent_slice(retrieval_question) or plain_intent_slice(question) or question
    target_kind = relation_target_kind(q_slice or question)
    tpl = _infer_business_template(
        ir.intent,
        question,
        retrieval_question,
        focus=focus,
        from_type=from_type,
        target_kind=target_kind,
    )
    to_type = {
        "project": "TestProject",
        "indicator": "Indicator",
        "method": "TestMethod",
        "equipment": "Equipment",
    }.get(target_kind, "")

    confidence = _compute_confidence(
        ir,
        neo_score=neo_score,
        focus=focus,
        matched_name=matched,
        business_template=tpl,
        from_type=from_type,
    )

    if focus and matched:
        _progress(
            on_progress,
            f"已在图谱中定位关键词「{matched}」（{best.get('node_type_zh') or from_type or '实体'}）。\n",
        )

    logger.info(
        "[V6][Route] hybrid tier1 intent=%s template=%s focus=%r conf=%.2f neo_ms=%.1f",
        ir.intent.value,
        tpl,
        focus[:40] if focus else "",
        confidence,
        neo_ms,
    )

    return RoutingDecision(
        intent=ir.intent,
        reason=ir.reason,
        business_template=tpl,
        focus=focus or matched,
        from_type=from_type,
        to_type=to_type,
        target_kind=target_kind,
        keyword_resolution={"best": best, "neo_ms": neo_ms},
        source="hybrid_tier1",
        routing_confidence=confidence,
    )


def hybrid_route_question(
    question: str,
    retrieval_question: str,
    *,
    client: Any = None,
    model: str = "qwen-plus",
    driver: Any = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> RoutingDecision:
    """规则 + 图谱；低置信度时 LLM 流式兜底。"""
    decision: Optional[RoutingDecision] = None
    for item in iter_hybrid_route_events(
        question,
        retrieval_question,
        client=client,
        model=model,
        driver=driver,
    ):
        if isinstance(item, RoutingDecision):
            decision = item
        elif on_progress and isinstance(item, str):
            on_progress(item)
    return decision or _tier1_routing(question, retrieval_question, driver)


def iter_hybrid_route_events(
    question: str,
    retrieval_question: str,
    *,
    client: Any = None,
    model: str = "qwen-plus",
    driver: Any = None,
):
    """
    生成路由过程思考文案（str），最终以 RoutingDecision 结束。
    """
    tier1 = _tier1_routing(question, retrieval_question, driver, on_progress=None)
    for line in (
        "正在根据问法与知识图谱匹配业务类型…\n",
    ):
        yield line
    if tier1.focus:
        nt_zh = (tier1.keyword_resolution or {}).get("best", {}).get("node_type_zh") or tier1.from_type
        yield f"已在图谱中定位关键词「{tier1.focus}」（{nt_zh or '实体'}）。\n"

    threshold = _confidence_threshold()
    if tier1.routing_confidence >= threshold or not _llm_fallback_enabled() or not client:
        logger.info(
            "[V6][Route] hybrid 采用 tier1 conf=%.2f threshold=%.2f",
            tier1.routing_confidence,
            threshold,
        )
        yield tier1
        return

    yield "问法较复杂，正在用模型辅助确认检索方案…\n"
    tool_payload = tier1.keyword_resolution or {"best": {}}
    t0 = time.perf_counter()
    try:
        stream_buf: List[str] = []

        def _delta(d: str) -> None:
            if d:
                stream_buf.append(d)

        llm_dec = llm_route_question_stream(
            client,
            model,
            question,
            retrieval_question,
            driver=driver,
            keyword_tool_payload=tool_payload,
            on_delta=_delta,
        )
        for piece in stream_buf:
            yield piece
        if llm_dec.plan_user_nl:
            yield llm_dec.plan_user_nl + "\n"
        llm_ms = (time.perf_counter() - t0) * 1000
        llm_dec.routing_confidence = max(tier1.routing_confidence, llm_dec.routing_confidence or 0.75)
        llm_dec.source = "llm_hybrid"
        if not llm_dec.keyword_resolution:
            llm_dec.keyword_resolution = tool_payload
        logger.info(
            "[V6][Route] hybrid tier2 template=%s conf=%.2f llm_ms=%.1f",
            llm_dec.business_template,
            llm_dec.routing_confidence,
            llm_ms,
        )
        yield llm_dec
    except Exception as e:
        logger.warning("[V6][Route] hybrid LLM 兜底失败: %s，使用 tier1", e)
        yield tier1


__all__ = ("hybrid_route_question", "iter_hybrid_route_events")
