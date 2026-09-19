"""
V6 前置判别（Preflight）：在 Agent 之前尝试快速答完。

流程概要：retrieval_question → V4 _quick_plan → route_question → 业务模板 program
→ execute_meta_steps → 直出或交 Agent。见 FLOW.md。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Generator, List, Optional, Tuple

from .znwd_llm_intent_classifier import Intent
from .znwd_intent_mode import get_intent_mode
from .znwd_llm_router import RoutingDecision, iter_route_events
from .memory_store import get_memory_store
from .prompts_v4 import DIRECT_ANSWER_PROMPT
from .services_v4 import (
    ZNWD_CHROMA_UI_MAX,
    _quick_plan,
    _should_database_direct_answer,
    _yield_database_direct_answer_stream,
    collect_v4_retrieval_results,
    _stream_single_llm as _stream_single_llm_v4,
)
from .services_v5 import (
    _apply_primary_focus_to_equipment_relation_steps,
    _build_ssyq_table_keyword_v5,
    _last_user_question,
    _normalize_chroma_rows_for_client,
    _send_step,
    _v5_finalize_memory,
    _v5_rows_non_empty,
    _v5_chroma_nonempty,
)
from .znwd_fast_route import (
    build_retrieval_question as _build_retrieval_question_base,
    fast_program_normalize_ok,
    format_fast_direct_body,
    intent_for_sufficiency,
    is_retrieval_sufficient_for_intent,
    normalize_program,
    plan_thinking_display_text,
    template_display_name,
    try_build_fast_path_program,
)
from .znwd_v6_executor import (
    ZNWD_CHROMA_UI_MAX as V6_CHROMA_UI_MAX,
    chroma_rows_nonempty,
    execute_meta_steps,
    normalize_chroma_rows_for_client,
    rerank_neo_by_interest,
)

logger = logging.getLogger(__name__)

_PREFLIGHT_ENABLED = os.environ.get("ZNWD_V6_PREFLIGHT", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
_V4_SUPPLEMENT = os.environ.get("ZNWD_V6_PREFLIGHT_V4_SUPPLEMENT", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
_EARLY_THINKING_HTTP = os.environ.get("ZNWD_PREFLIGHT_EARLY_THINKING", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)


def http_early_thinking_enabled() -> bool:
    return _EARLY_THINKING_HTTP


def _send_bootstrap_step(
    msg: str,
    tp: str = "intent_judge",
    status: str = "loading",
) -> Tuple[str, dict]:
    """Preflight/路由进度：仅 step，前端在「处理中」区展示，不写入思考面板。"""
    return _send_step(msg, tp=tp, status=status, phase="bootstrap")


def preflight_enabled() -> bool:
    return _PREFLIGHT_ENABLED


def build_retrieval_question(
    messages: List[Dict[str, Any]],
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """V6 多轮拼接 + 可选 Redis 摘要前缀（与 ZNWD6 session_context 对齐）。"""
    from .session_context import build_retrieval_question_v6

    return build_retrieval_question_v6(
        messages,
        question,
        user_id=user_id,
        session_id=session_id,
    )


def _stream_direct_answer_no_retrieval(
    client: Any,
    model: str,
    messages: List[Dict[str, Any]],
    question: str,
    driver: Any,
    collection: Any,
    llm_limits: Optional[Dict[str, Any]],
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Generator[Tuple[str, dict], None, None]:
    """strategy=none：单次 DIRECT_ANSWER，不进 Agent。"""
    yield _send_step("判定为无需数据库检索，直接由 LLM 回答", tp="intent_judge", status="done")
    yield _send_step("正在生成直接回答", tp="synthesis", status="loading")
    compact: List[Dict[str, Any]] = []
    for m in messages[-4:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            compact.append({"role": m["role"], "content": str(m.get("content") or "")[:800]})
    direct_messages = (
        [{"role": "system", "content": DIRECT_ANSWER_PROMPT}]
        + compact[:-1]
        + [{"role": "user", "content": question}]
    )
    full_answer = ""
    full_thinking = ""
    for et, data in _stream_single_llm_v4(
        client,
        model,
        direct_messages,
        driver,
        collection,
        tools_disabled=True,
        llm_limits=llm_limits,
    ):
        if et == "done":
            continue
        yield (et, data)
        if et == "answer":
            full_answer += str(data.get("content") or "")
        elif et == "thinking":
            full_thinking += str(data.get("content") or "")
    yield _send_step("直接回答完成", tp="synthesis", status="done")
    _v5_finalize_memory(user_id, session_id, question, full_answer, client, model)
    yield ("done", {"fullAnswer": full_answer, "fullThinking": full_thinking, "primaryChromaSource": None})


def stream_v6_preflight(
    client: Any,
    model: str,
    messages: List[Dict[str, Any]],
    driver: Any,
    collection: Any,
    llm_limits: Optional[Dict[str, Any]] = None,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_interest: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, dict], None, None]:
    """
    执行前置判别与快速检索直出。若生成器正常结束则已处理完毕；
    调用方不应再进 Agent（本函数始终 yield 到 done 或 error，不返回 None）。
    未启用 preflight 时调用方应直接走 Agent（由 llm_stream_single_v6 判断）。
    """
    t_request = time.perf_counter()
    work_messages = [dict(m) if isinstance(m, dict) else m for m in messages]
    question = _last_user_question(work_messages)

    yield _send_bootstrap_step("正在理解您的问题…")
    logger.info("[V6][Preflight] ttft_ms=%.1f", (time.perf_counter() - t_request) * 1000)

    yield _send_bootstrap_step("正在结合对话上下文整理检索用语…")
    retrieval_question = build_retrieval_question(
        work_messages, question, user_id=user_id, session_id=session_id
    )
    logger.info("[V6][Preflight] retrieval_question=%r", retrieval_question[:400])

    plan_gate = _quick_plan(retrieval_question)
    strategy = str(plan_gate.get("strategy") or "fuzzy").strip().lower()
    answer_mode_gate = str(plan_gate.get("answerMode") or "llm_synthesize").strip().lower()

    if strategy == "none":
        logger.info("[V6][Preflight] V4 strategy=none → DIRECT_ANSWER")
        yield from _stream_direct_answer_no_retrieval(
            client,
            model,
            work_messages,
            question,
            driver,
            collection,
            llm_limits,
            user_id=user_id,
            session_id=session_id,
        )
        return

    mode = get_intent_mode()
    yield _send_bootstrap_step(
        f"正在进行意图与业务路由判别（模式={mode}）…",
        tp="intent_judge",
        status="loading",
    )

    t_route = time.perf_counter()
    routing = None
    for item in iter_route_events(
        question,
        retrieval_question,
        client=client,
        model=model,
        driver=driver,
    ):
        if isinstance(item, RoutingDecision):
            routing = item
        elif isinstance(item, str) and item.strip():
            line = item.strip()
            for sub in (x.strip() for x in line.split("\n") if x.strip()):
                yield _send_bootstrap_step(sub)
    if routing is None:
        from .znwd_llm_router import rule_routing_decision

        routing = rule_routing_decision(question, retrieval_question, driver)

    intent_res = routing.to_intent_result()
    conf = getattr(routing, "routing_confidence", 0.0)
    kw_res = getattr(routing, "keyword_resolution", None) or {}
    neo_ms = float(kw_res.get("neo_ms") or 0.0)
    route_ms = (time.perf_counter() - t_route) * 1000
    logger.info(
        "[V6][Preflight] intent_mode=%s intent=%s reason=%s template=%s focus=%r "
        "strategy=%s conf=%.2f route_ms=%.1f neo_ms=%.1f source=%s",
        mode,
        intent_res.intent.value,
        intent_res.reason,
        getattr(routing, "business_template", ""),
        getattr(routing, "focus", ""),
        strategy,
        conf,
        route_ms,
        neo_ms,
        getattr(routing, "source", ""),
    )

    if getattr(routing, "skip_retrieval", False):
        logger.info("[V6][Preflight] 路由判定跳过检索 → DIRECT_ANSWER")
        yield _send_step("判定为无需查库，直接回答", tp="intent_judge", status="done")
        if getattr(routing, "plan_user_nl", ""):
            yield ("thinking", {"content": routing.plan_user_nl + "\n\n"})
        yield from _stream_direct_answer_no_retrieval(
            client,
            model,
            work_messages,
            question,
            driver,
            collection,
            llm_limits,
            user_id=user_id,
            session_id=session_id,
        )
        return

    fast_raw = try_build_fast_path_program(
        intent_res,
        question,
        retrieval_question,
        work_messages,
        driver=driver,
        routing=routing,
    )
    if fast_raw:
        logger.info(
            "[V6][Preflight] business_template=%s slots=%s",
            template_display_name(fast_raw),
            fast_raw.get("_business_slots"),
        )
    if not fast_raw:
        logger.info("[V6][Preflight] 无快速 program → 交由 V6 Agent")
        yield _send_step(
            "当前问题需智能体多步检索与推理，进入 V6 Agent。",
            tp="intent_judge",
            status="done",
        )
        return

    program = normalize_program(fast_raw, retrieval_question, question)
    if not fast_program_normalize_ok(fast_raw, program) or program.get("fallback_v4"):
        logger.info("[V6][Preflight] 快速 program 归一化失败 → Agent")
        yield _send_step("快速路径不可用，进入 V6 Agent。", tp="intent_judge", status="done")
        return

    _apply_primary_focus_to_equipment_relation_steps(program, retrieval_question)
    table_kw = _build_ssyq_table_keyword_v5(
        work_messages, retrieval_question, program, question
    )
    yield ("search_keywords", {"ssyqTableKeyword": table_kw})

    # 发送检索规划（一次性）— 此处才进入思考面板
    fb = plan_thinking_display_text(program, retrieval_question)
    if fb.strip():
        yield ("thinking_start", {})
        yield ("thinking", {"content": fb + "\n\n"})

    tpl = template_display_name(program)
    yield _send_step(
        f"快速检索 [{tpl}]：{program.get('intent_summary') or intent_res.intent.value}",
        tp="intent_judge",
        status="done",
    )
    
    # 分步骤执行并流式返回
    steps = program.get("steps") or []
    neo_rows_accum: List[Dict[str, Any]] = []
    chroma_rows_accum: List[Dict[str, Any]] = []
    
    for i, step in enumerate(steps):
        step_id = step.get("step_id", f"s{i+1}")
        purpose = step.get("purpose", "")
        display_text = step.get("display_text", purpose)
        op_code = step.get("op_code", "")
        
        # 发送步骤开始（语义化描述）
        yield _send_step(
            f"第{i+1}步：{display_text}",
            tp="retrieve",
            status="loading",
        )
        
        # 执行单步
        neo_rows, chroma_rows, step_results = execute_meta_steps(
            driver, collection, [step]
        )
        
        # 累积结果
        neo_rows_accum.extend(neo_rows)
        chroma_rows_accum.extend(chroma_rows)
        
        # 发送步骤完成
        neo_count = len(neo_rows)
        chroma_count = len(chroma_rows)
        if neo_count > 0 or chroma_count > 0:
            yield _send_step(
                f"第{i+1}步完成：找到 {neo_count} 条图谱数据，{chroma_count} 条文档数据",
                tp="retrieve",
                status="done",
            )
        else:
            yield _send_step(
                f"第{i+1}步完成：未找到匹配数据",
                tp="retrieve",
                status="done",
            )
    
    # 处理最终结果
    neo_rows = rerank_neo_by_interest(neo_rows_accum, user_interest)
    chroma_rows = normalize_chroma_rows_for_client(chroma_rows_accum)
    chroma_display = (chroma_rows or [])[:V6_CHROMA_UI_MAX]
    yield ("source_data", {"neo4jResults": neo_rows, "chromaResults": chroma_display})
    yield _send_step(
        f"程序化检索完成：Neo={len(neo_rows)}，Chroma={len(chroma_rows)}",
        tp="retrieve",
        status="done",
    )

    if _v5_chroma_nonempty(chroma_rows):
        program["answer_mode"] = "llm_synthesize"
        logger.info("[V6][Preflight] Chroma 命中 → 进入 Agent 合成")
        yield _send_step("文档库有命中，进入 V6 Agent 整合。", tp="synthesis", status="done")
        return

    if not _v5_rows_non_empty(neo_rows, chroma_rows):
        if _V4_SUPPLEMENT and client:
            yield _send_step("快速路径无命中，尝试 V4 补充检索...", tp="retrieve", status="loading")
            try:
                pack = collect_v4_retrieval_results(
                    client, model, work_messages, driver, collection, llm_limits=llm_limits
                )
                v4_neo = list(pack.get("neo_res") or [])
                v4_chroma = list(pack.get("chroma_res") or [])
                plan_norm = pack.get("plan_norm") or {}
                neo_rows = v4_neo
                chroma_rows = _normalize_chroma_rows_for_client(v4_chroma)
                chroma_display = (chroma_rows or [])[:ZNWD_CHROMA_UI_MAX]
                ssyq = str(pack.get("ssyq_kw") or "").strip()
                if ssyq:
                    yield ("search_keywords", {"ssyqTableKeyword": ssyq})
                yield ("source_data", {"neo4jResults": neo_rows, "chromaResults": chroma_display})
                yield _send_step(
                    f"V4 补充检索：Neo={len(neo_rows)} Chroma={len(chroma_rows)}",
                    tp="retrieve",
                    status="done",
                )
                if _should_database_direct_answer(
                    plan_norm,
                    neo_rows,
                    chroma_rows,
                    str(pack.get("retrieval_question") or retrieval_question),
                ):
                    yield _send_step(
                        "V4 结构化项目清单直出",
                        tp="synthesis",
                        status="loading",
                    )
                    full_answer = ""
                    for ev in _yield_database_direct_answer_stream(question, neo_rows):
                        yield ev
                        if ev[0] == "answer":
                            full_answer += str(ev[1].get("content") or "")
                    yield _send_step("直出完成", tp="synthesis", status="done")
                    _v5_finalize_memory(user_id, session_id, question, full_answer, client, model)
                    yield (
                        "done",
                        {
                            "fullAnswer": full_answer,
                            "fullThinking": "数据库直出（V4 补充）",
                            "primaryChromaSource": None,
                            "ssyqTableKeyword": ssyq or table_kw,
                        },
                    )
                    return
            except Exception as ex:
                logger.warning("[V6][Preflight] V4 supplement failed: %s", ex)

        # 方法类问句：无结构化「项目→方法」时，按测试项目名补查（避免误走 equipment_via 或仅模糊节点）
        if driver:
            from .services_v4 import (
                _extract_ssyq_keyword_from_question,
                question_asks_method_focus,
                query_neo4j,
                resolve_neo_pattern_from_question,
            )
            from .znwd_business_templates import _extract_test_project_keyword_for_method_question

            rq = str(retrieval_question or question)
            if question_asks_method_focus(rq):
                has_project_method = any(
                    isinstance(r, dict)
                    and not r.get("error")
                    and r.get("rowKind") == "TestProjectToTestMethod"
                    for r in (neo_rows or [])
                )
                if not has_project_method:
                    pk = _extract_test_project_keyword_for_method_question(
                        question, rq
                    ) or _extract_ssyq_keyword_from_question(rq)
                    if len(pk) >= 2 and resolve_neo_pattern_from_question(rq) == "testproject_con_testmethod":
                        extra = query_neo4j(
                            driver,
                            pk,
                            40,
                            contextual_question=rq,
                            neo_pattern="testproject_con_testmethod",
                        )
                        if extra and not all(
                            isinstance(x, dict) and x.get("error") for x in extra
                        ):
                            neo_rows = extra
                            yield (
                                "source_data",
                                {"neo4jResults": neo_rows, "chromaResults": chroma_display},
                            )
                            yield _send_step(
                                f"项目→方法补查：Neo={len(neo_rows)}",
                                tp="retrieve",
                                status="done",
                            )

        if not _v5_rows_non_empty(neo_rows, chroma_rows):
            logger.info("[V6][Preflight] 检索无命中 → DIRECT_ANSWER")
        yield from _stream_direct_answer_no_retrieval(
            client,
            model,
            work_messages,
            question,
            driver,
            collection,
            llm_limits,
            user_id=user_id,
            session_id=session_id,
        )
        return

    intent_eff = intent_for_sufficiency(program, retrieval_question)
    
    # 优先使用 program 中的 answer_mode（来自业务模板），而不是 _quick_plan 的 answerMode
    effective_answer_mode = program.get("answer_mode") or answer_mode_gate
    
    if (
        effective_answer_mode == "database_direct"
        and is_retrieval_sufficient_for_intent(neo_rows, chroma_rows, question, intent_eff)
        and not chroma_rows_nonempty(chroma_rows)
    ):
        if intent_eff == Intent.STANDARD_CHAPTER:
            think_msg = "检索结果已覆盖标准/章节类问题，直接整理文档要点作答。"
            full_th = "标准章节直出"
        else:
            think_msg = "将检索到的关联整理为列表。"
            full_th = "数据库直出（Preflight）"
        yield ("thinking", {"content": think_msg + "\n"})
        body = format_fast_direct_body(
            intent_eff, question, retrieval_question, neo_rows, chroma_rows, program
        )
        for i in range(0, len(body), 600):
            yield ("answer", {"content": body[i : i + 600]})
        yield _send_step("数据库直出完成", tp="synthesis", status="done")
        _v5_finalize_memory(user_id, session_id, question, body, client, model)
        yield (
            "done",
            {
                "fullAnswer": body,
                "fullThinking": full_th,
                "primaryChromaSource": None,
                "ssyqTableKeyword": table_kw,
            },
        )
        return

    logger.info(
        "[V6][Preflight] 有命中但需合成或 answer_mode=%s → Agent",
        program.get("answer_mode"),
    )
    yield _send_step("检索结果需智能体整合，进入 V6 Agent。", tp="retrieve", status="done")


def run_preflight_or_agent(
    client: Any,
    model: str,
    messages: List[Dict[str, Any]],
    driver: Any,
    collection: Any,
    llm_limits: Optional[Dict[str, Any]] = None,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_interest: Optional[Dict[str, Any]] = None,
    agent_stream: Any,
) -> Generator[Tuple[str, dict], None, None]:
    """
    若启用 preflight：先尝试快速路径；preflight 内 return 表示应交 Agent。
    agent_stream 为 _v6_agent_stream 生成器工厂（callable returning generator）。
    """
    if not preflight_enabled():
        yield from agent_stream()
        return

    preflight_done = False
    for event in stream_v6_preflight(
        client,
        model,
        messages,
        driver,
        collection,
        llm_limits,
        user_id=user_id,
        session_id=session_id,
        user_interest=user_interest,
    ):
        et = event[0]
        if et == "done":
            preflight_done = True
        yield event
        if et == "error":
            return

    if preflight_done:
        return

    logger.info("[V6][Preflight] handoff → LangChain Agent")
    yield from agent_stream()
