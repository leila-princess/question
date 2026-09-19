"""
ZNWD LLM 业务路由：选模板、填槽位、判断是否需历史/是否跳过检索。

规则路由保留在 znwd_business_templates；ZNWD_INTENT_MODE=llm 时全量 LLM；
hybrid 默认见 znwd_hybrid_router。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .intent_classifier import Intent, IntentResult, classify_intent as rule_classify_intent
from .znwd_intent_mode import is_hybrid_routing_enabled, is_llm_routing_enabled
from .znwd_keyword_resolver import extract_focus_candidates, resolve_best_focus, resolve_keyword_in_graph

logger = logging.getLogger(__name__)

LLM_ROUTE_SYSTEM = "你是试验知识图谱智能问答的路由助手。只输出一个 JSON 对象，不要 markdown。"

LLM_ROUTE_USER_TEMPLATE = """## 用户当前问题
{question}

## 检索用语（含历史/承前主体）
{retrieval_question}

## 图谱关键词解析（系统已查库，请优先采用 matched_name 与 node_type）
{keyword_tool_json}

## 可选业务模板
| template | 含义 |
| BIZ_ENUM | 列举某类全部（设备类型、全部环境等） |
| BIZ_REL_ONE_HOP | 查某对象关联的项目/指标/设备等（单边） |
| BIZ_REL_TWO_HOP | 设备或环境 → 项目 → 方法（两边） |
| BIZ_PROJECT_TO_METHOD | 主体是测试项目名称，查其方法 |
| BIZ_EQUIP_DISCOVERY | 按项目关键词查有哪些设备 |
| BIZ_DOCUMENT | 标准/章节文档 |
| BIZ_AGENT | 无法结构化检索或需综合推理 |
| BIZ_DIRECT_ANSWER | 闲聊/常识，不查图谱 |

## 节点类型（from_type / node_type 填英文标签）
Equipment, EquipmentType, Environment, TestProject, Indicator, TestMethod

## 输出 JSON 字段
{{
  "intent": "ENUMERATION|RELATION_QUERY|STANDARD_CHAPTER|EXPLAIN|OTHER",
  "business_template": "模板代号",
  "focus": "完整焦点名称（必须用解析结果中的 matched_name，如无人机A型）",
  "from_type": "起点类型，可空",
  "to_type": "目标类型 project→TestProject indicator→Indicator method→TestMethod",
  "target_kind": "project|indicator|method|equipment|unknown",
  "need_history": false,
  "skip_retrieval": false,
  "skip_retrieval_reason": "",
  "plan_user_nl": "一句话检索意图（中文，≤40字；禁止 BIZ_/箭头/op_code；禁止写结果或未命中）",
  "reason": "路由理由"
}}
"""


@dataclass
class RoutingDecision:
    intent: Intent
    reason: str = ""
    business_template: str = ""
    focus: str = ""
    from_type: str = ""
    to_type: str = ""
    target_kind: str = "unknown"
    need_history: bool = False
    skip_retrieval: bool = False
    skip_retrieval_reason: str = ""
    plan_user_nl: str = ""
    keyword_resolution: Optional[Dict[str, Any]] = None
    source: str = "rule"
    routing_confidence: float = 0.0

    def to_intent_result(self) -> IntentResult:
        return IntentResult(self.intent, self.reason)


def _build_keyword_tool_payload(
    driver: Any,
    question: str,
    retrieval_question: str,
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if existing and existing.get("best"):
        return existing
    best = resolve_best_focus(driver, question, retrieval_question)
    cands = best.get("candidates_checked") or extract_focus_candidates(
        question, retrieval_question
    )
    per_candidate: List[Dict[str, Any]] = []
    for c in (cands or [])[:2]:
        per_candidate.append(resolve_keyword_in_graph(driver, c))
    return {
        "best": best,
        "per_candidate": per_candidate,
    }


def _parse_route_json(content: str) -> Optional[Dict[str, Any]]:
    if not content:
        return None
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    start = s.find("{")
    end = s.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(s[start:end])
    except json.JSONDecodeError:
        return None


def _routing_from_llm_json(
    data: Dict[str, Any],
    tool_payload: Dict[str, Any],
    *,
    source: str = "llm",
) -> RoutingDecision:
    intent_s = str(data.get("intent") or "OTHER").strip()
    try:
        intent = Intent(intent_s)
    except ValueError:
        intent = Intent.OTHER

    focus = str(data.get("focus") or "").strip()
    if not focus:
        focus = str((tool_payload.get("best") or {}).get("focus") or "")

    from_type = str(data.get("from_type") or "").strip()
    if not from_type:
        from_type = str((tool_payload.get("best") or {}).get("node_type") or "")

    to_type = str(data.get("to_type") or "").strip()
    if not to_type:
        to_type = _target_kind_to_to_type(str(data.get("target_kind") or ""))

    tpl = str(data.get("business_template") or "").strip()
    if tpl == "BIZ_DIRECT_ANSWER":
        return RoutingDecision(
            intent=intent,
            reason=str(data.get("reason") or ""),
            business_template="BIZ_DIRECT_ANSWER",
            skip_retrieval=True,
            skip_retrieval_reason=str(data.get("skip_retrieval_reason") or "LLM 判定无需检索"),
            plan_user_nl=str(data.get("plan_user_nl") or ""),
            keyword_resolution=tool_payload,
            source=source,
            routing_confidence=0.85,
        )

    return RoutingDecision(
        intent=intent,
        reason=str(data.get("reason") or ""),
        business_template=tpl,
        focus=focus,
        from_type=from_type,
        to_type=to_type,
        target_kind=str(data.get("target_kind") or "unknown"),
        need_history=bool(data.get("need_history")),
        skip_retrieval=bool(data.get("skip_retrieval")),
        skip_retrieval_reason=str(data.get("skip_retrieval_reason") or ""),
        plan_user_nl=str(data.get("plan_user_nl") or ""),
        keyword_resolution=tool_payload,
        source=source,
        routing_confidence=0.8,
    )


def _target_kind_to_to_type(target_kind: str) -> str:
    return {
        "project": "TestProject",
        "indicator": "Indicator",
        "method": "TestMethod",
        "equipment": "Equipment",
    }.get((target_kind or "").strip(), "")


def _stream_delta_for_thinking(delta: str, buf: str) -> str:
    """过滤流式片段：JSON 主体不写入思考区，仅输出 plan 相关自然语言前缀。"""
    if not delta:
        return ""
    combined = buf + delta
    if "{" in combined and '"' in combined:
        return ""
    if delta.strip().startswith("{") or delta.strip().startswith("```"):
        return ""
    return delta


def llm_route_question_stream(
    client: Any,
    model: str,
    question: str,
    retrieval_question: str,
    driver: Any = None,
    *,
    keyword_tool_payload: Optional[Dict[str, Any]] = None,
    on_delta: Optional[Callable[[str], None]] = None,
) -> RoutingDecision:
    """流式 LLM 路由；on_delta 接收可展示的思考片段。"""
    tool_payload = _build_keyword_tool_payload(
        driver,
        question,
        retrieval_question,
        existing=keyword_tool_payload,
    )
    user_msg = LLM_ROUTE_USER_TEMPLATE.format(
        question=(question or "")[:800],
        retrieval_question=(retrieval_question or "")[:1200],
        keyword_tool_json=json.dumps(tool_payload, ensure_ascii=False, default=str)[:6000],
    )
    raw_buf = ""
    display_buf = ""
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": LLM_ROUTE_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=120,
            stream=True,
        )
        for chunk in stream:
            delta = ""
            if chunk.choices:
                delta = chunk.choices[0].delta.content or ""
            if not delta:
                continue
            raw_buf += delta
            piece = _stream_delta_for_thinking(delta, display_buf)
            if piece and on_delta:
                on_delta(piece)
            display_buf += piece

        data = _parse_route_json(raw_buf)
        if not data:
            logger.warning("[ZNWD][LLM-Route] stream JSON 解析失败，回退 tier1/规则")
            return rule_routing_decision(question, retrieval_question, driver)

        dec = _routing_from_llm_json(data, tool_payload, source="llm_stream")
        if dec.plan_user_nl and on_delta:
            on_delta(dec.plan_user_nl + "\n")
        logger.info(
            "[ZNWD][LLM-Route] stream template=%s focus=%r",
            dec.business_template,
            (dec.focus or "")[:40],
        )
        return dec
    except Exception as e:
        logger.error("[ZNWD][LLM-Route] stream 失败: %s", e)
        return rule_routing_decision(question, retrieval_question, driver)


def llm_route_question(
    client: Any,
    model: str,
    question: str,
    retrieval_question: str,
    driver: Any = None,
    *,
    keyword_tool_payload: Optional[Dict[str, Any]] = None,
) -> RoutingDecision:
    """LLM 路由 + 关键词工具结果注入（非流式，兼容 llm 模式）。"""
    tool_payload = _build_keyword_tool_payload(
        driver,
        question,
        retrieval_question,
        existing=keyword_tool_payload,
    )
    user_msg = LLM_ROUTE_USER_TEMPLATE.format(
        question=(question or "")[:800],
        retrieval_question=(retrieval_question or "")[:1200],
        keyword_tool_json=json.dumps(tool_payload, ensure_ascii=False, default=str)[:6000],
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": LLM_ROUTE_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _parse_route_json(raw)
        if not data:
            logger.warning("[ZNWD][LLM-Route] JSON 解析失败，回退规则")
            return rule_routing_decision(question, retrieval_question, driver)
        return _routing_from_llm_json(data, tool_payload, source="llm")
    except Exception as e:
        logger.error("[ZNWD][LLM-Route] 失败: %s，回退规则", e)
        return rule_routing_decision(question, retrieval_question, driver)


def rule_routing_decision(
    question: str,
    retrieval_question: str,
    driver: Any = None,
) -> RoutingDecision:
    """规则意图 + 关键词解析，供回退或 rule 模式。"""
    ir = rule_classify_intent(retrieval_question or question)
    best = resolve_best_focus(driver, question, retrieval_question) if driver else {}
    focus = str(best.get("focus") or "").strip()
    from_type = str(best.get("node_type") or "").strip()
    neo_score = float(best.get("score") or 0.0)
    return RoutingDecision(
        intent=ir.intent,
        reason=ir.reason,
        focus=focus,
        from_type=from_type,
        keyword_resolution={"best": best} if best else None,
        source="rule",
        routing_confidence=min(1.0, 0.5 + neo_score * 0.4),
    )


def iter_route_events(
    question: str,
    retrieval_question: str,
    *,
    client: Any = None,
    model: str = "qwen-plus",
    driver: Any = None,
):
    """yield 思考片段 str，最终以 RoutingDecision 结束。"""
    if is_hybrid_routing_enabled():
        from .znwd_hybrid_router import iter_hybrid_route_events

        yield from iter_hybrid_route_events(
            question,
            retrieval_question,
            client=client,
            model=model,
            driver=driver,
        )
        return
    if is_llm_routing_enabled() and client:
        yield "正在用模型分析问法与检索方案…\n"
        yield llm_route_question(
            client,
            model,
            question,
            retrieval_question,
            driver,
        )
        return
    yield rule_routing_decision(question, retrieval_question, driver)


def route_question(
    question: str,
    retrieval_question: str,
    *,
    client: Any = None,
    model: str = "qwen-plus",
    driver: Any = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> RoutingDecision:
    decision: Optional[RoutingDecision] = None
    for item in iter_route_events(
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
    if decision is None:
        return rule_routing_decision(question, retrieval_question, driver)
    return decision


__all__ = (
    "RoutingDecision",
    "llm_route_question",
    "llm_route_question_stream",
    "route_question",
    "iter_route_events",
    "rule_routing_decision",
)
