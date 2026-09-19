"""
ZNWD4 分步路由：Step1 purpose → Step2 route_id → 程序 probe + 组装 RoutePlan。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from .prompts.routing_prompt_builder import (
    build_routing_human,
    build_step1_system_prompt,
    build_step2_candidate_message,
    build_step2_system_prompt,
)
from .route_plan_v2 import merge_slots_from_keywords, parse_step1_json, parse_step2_json
from .route_validator import assemble_legacy_plan
from .routing_catalog import PURPOSE_CHAT, filter_routes_by_purpose, get_route, guess_purpose_from_question
from .route_lock_resolver import safe_resolve_route_lock
from .question_route_lock import (
    apply_route_lock_to_step1,
    build_locked_step2,
    hybrid_routing_complete_lock,
    inject_locked_route,
    skip_llm_for_lock,
)
from .pcsr_assembly import finalize_locked_plan
from .znwd4_thinking_emit import (
    emit_milestone,
    emit_route_progress,
    humanize_tool_milestone,
    milestone_route_step1,
    milestone_route_step2,
    summarize_tool_result,
)
from .routing_context import record_llm_call
from .znwd4_tools import (
    build_znwd4_openai_tool_definitions,
    build_znwd4_tool_dispatch,
    canonical_tool_name,
    resolve_znwd4_tool_call_parser,
    znwd4_llm_request_timeout,
)

logger = logging.getLogger(__name__)

ROUTE_PLAN_INTERNAL = "route_plan_internal"
_TOOL_ROUND_RESULT = "_tool_round_result"


def _purpose_zh_for_lock(lock: Any) -> str:
    from .routing_catalog import _purpose_zh

    return _purpose_zh(lock.purpose)


def _lc_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(b.get("text") or b) if isinstance(b, dict) else str(b)
            for b in content
            if b
        )
    return str(content)


def _lc_kwargs(client: Any, model: str, llm_limits: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    kw: Dict[str, Any] = {"model": model, "temperature": 0}
    if client:
        bu = getattr(client, "base_url", None)
        if bu:
            kw["base_url"] = str(bu).rstrip("/")
        ak = getattr(client, "api_key", None)
        if ak:
            kw["api_key"] = ak
    if llm_limits:
        mo = llm_limits.get("max_output_tokens")
        if mo:
            try:
                kw["max_tokens"] = min(int(mo), 1024)
            except (TypeError, ValueError):
                pass
    tcp = resolve_znwd4_tool_call_parser()
    if tcp:
        kw["extra_body"] = {"tool_call_parser": tcp}
    kw["timeout"] = znwd4_llm_request_timeout()
    kw["max_retries"] = 0
    base = str(kw.get("base_url") or os.environ.get("OPENAI_BASE_URL") or "")
    if any(x in base for x in ("192.168.", "127.0.0.1", "localhost")):
        try:
            import httpx

            timeout = float(kw["timeout"])
            kw["http_client"] = httpx.Client(trust_env=False, timeout=timeout)
            kw["http_async_client"] = httpx.AsyncClient(trust_env=False, timeout=timeout)
        except Exception:
            pass
    return kw


def _normalize_tc(tc: Any) -> Dict[str, Any]:
    if isinstance(tc, dict):
        raw = tc.get("args") or tc.get("arguments") or {}
        tid = tc.get("id") or ""
        name = tc.get("name") or ""
    else:
        raw = getattr(tc, "args", None) or getattr(tc, "arguments", {}) or {}
        tid = getattr(tc, "id", "") or ""
        name = getattr(tc, "name", "") or ""
    if isinstance(raw, str):
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            args = {}
    else:
        args = dict(raw) if isinstance(raw, dict) else {}
    return {"id": tid, "name": canonical_tool_name(name), "args": args}


def _run_tool_round(
    llm_tools: Any,
    messages: List[Any],
    dispatch: Dict[str, Any],
    *,
    max_rounds: int = 2,
    stage: str = "routing",
) -> Tuple[AIMessage, Dict[str, Any], Dict[str, Any]]:
    probe_snapshot: Dict[str, Any] = {}
    history_snapshot: Dict[str, Any] = {}
    ai: Optional[AIMessage] = None
    for tr in range(max_rounds + 1):
        is_last = tr >= max_rounds
        record_llm_call(f"routing_{stage}")
        ai = llm_tools.invoke(messages)
        messages.append(ai)
        tool_calls = getattr(ai, "tool_calls", None) or []
        if tool_calls and not is_last:
            for tc in tool_calls:
                norm = _normalize_tc(tc)
                name = norm["name"]
                fn = dispatch.get(name)
                result = fn(**norm["args"]) if fn else json.dumps({"error": name}, ensure_ascii=False)
                if name == "znwd4_probe_keyword":
                    try:
                        pd = json.loads(result)
                        if isinstance(pd, dict) and pd.get("best"):
                            probe_snapshot = {"best": pd["best"], "hits": pd.get("hits", [])}
                    except (json.JSONDecodeError, TypeError):
                        pass
                if name == "znwd4_history_recent_qa":
                    try:
                        hd = json.loads(result)
                        if isinstance(hd, dict):
                            history_snapshot = hd
                    except (json.JSONDecodeError, TypeError):
                        pass
                messages.append(ToolMessage(content=result, tool_call_id=norm["id"] or name))
            continue
        break
    assert ai is not None
    return ai, probe_snapshot, history_snapshot


def _stream_tool_round(
    llm_tools: Any,
    messages: List[Any],
    dispatch: Dict[str, Any],
    *,
    max_rounds: int = 2,
    stage: str = "step1",
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """工具轮 SSE：每轮 invoke 与 tool call 发里程碑 / route_progress。"""
    probe_snapshot: Dict[str, Any] = {}
    history_snapshot: Dict[str, Any] = {}
    ai: Optional[AIMessage] = None
    stage_label = "问题目的" if stage == "step1" else "业务方案"
    yield emit_route_progress(stage=stage, status="loading", message=f"正在分析{stage_label}…")
    for tr in range(max_rounds + 1):
        is_last = tr >= max_rounds
        if tr == 0:
            yield emit_milestone(f"正在判断{stage_label}…", kind="route")
        elif not is_last:
            yield emit_milestone(f"正在根据图谱结果继续分析{stage_label}…", kind="route")
        record_llm_call(f"routing_{stage}")
        ai = llm_tools.invoke(messages)
        messages.append(ai)
        tool_calls = getattr(ai, "tool_calls", None) or []
        if tool_calls and not is_last:
            for tc in tool_calls:
                norm = _normalize_tc(tc)
                name = norm["name"]
                ms_tool = humanize_tool_milestone(name, norm.get("args"))
                if ms_tool:
                    yield emit_milestone(ms_tool, kind="route")
                fn = dispatch.get(name)
                result = fn(**norm["args"]) if fn else json.dumps({"error": name}, ensure_ascii=False)
                summary = summarize_tool_result(name, result)
                if summary:
                    yield emit_milestone(summary, kind="route")
                if name == "znwd4_probe_keyword":
                    try:
                        pd = json.loads(result)
                        if isinstance(pd, dict) and pd.get("best"):
                            probe_snapshot = {"best": pd["best"], "hits": pd.get("hits", [])}
                    except (json.JSONDecodeError, TypeError):
                        pass
                if name == "znwd4_history_recent_qa":
                    try:
                        hd = json.loads(result)
                        if isinstance(hd, dict):
                            history_snapshot = hd
                    except (json.JSONDecodeError, TypeError):
                        pass
                messages.append(ToolMessage(content=result, tool_call_id=norm["id"] or name))
            continue
        break
    assert ai is not None
    yield emit_route_progress(stage=stage, status="done", message=f"{stage_label}分析完成")
    yield (_TOOL_ROUND_RESULT, {"ai": ai, "probe": probe_snapshot, "history": history_snapshot})


def _route_lock_progress_events(
    lock: Any,
    *,
    status: str = "done",
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    from .routing_catalog import _purpose_zh

    if status == "loading":
        yield emit_milestone("正在识别业务意图与关键词…", kind="route")
        yield emit_route_progress(stage="route_lock", status="loading", message="正在识别业务意图与关键词…")
        return
    if not lock:
        return
    purpose_zh = _purpose_zh(lock.purpose) if lock else ""
    kw = list(getattr(lock, "keywords", None) or [])
    msg = f"已识别：{purpose_zh or lock.purpose}"
    if kw:
        msg += f"，关键词：{'、'.join(kw[:6])}"
    yield emit_milestone(msg + "。", kind="route")
    yield emit_route_progress(
        stage="route_lock",
        status="done",
        message=msg,
        purpose=str(lock.purpose or ""),
        purpose_zh=purpose_zh,
        route_id=str(lock.route_id or ""),
        keywords=kw,
    )


def _yield_plan_from_complete_lock(
    *,
    effective_question: str,
    question: str,
    route_lock: Any,
    driver: Any,
    history_snapshot_pre: Dict[str, Any],
    user_id: Optional[str],
    session_id: Optional[str],
    user_route_id: Optional[str],
    probe_source: str = "lock_complete",
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    yield emit_milestone(
        f"程序意图锁：{_purpose_zh_for_lock(route_lock)}（{route_lock.route_id}）",
        kind="route",
    )
    yield emit_milestone("意图与关键词已明确，跳过 LLM 分步路由…", kind="route")
    step1 = apply_route_lock_to_step1({}, route_lock, effective_question)
    step2 = build_locked_step2(route_lock)
    ms1 = milestone_route_step1(step1.get("purpose_zh") or step1.get("purpose", ""))
    if ms1:
        yield emit_milestone(ms1, kind="route")
        yield emit_route_progress(
            stage="step1",
            status="done",
            message=ms1,
            purpose=str(step1.get("purpose") or ""),
            purpose_zh=str(step1.get("purpose_zh") or ""),
            keywords=list(step1.get("keywords") or []),
        )
    ms2 = milestone_route_step2(step2.get("route_id", ""), step2.get("match_reason", ""))
    if ms2:
        yield emit_milestone(ms2, kind="route")
        yield emit_route_progress(
            stage="step2",
            status="done",
            message=ms2,
            route_id=str(step2.get("route_id") or ""),
            keywords=list(step2.get("keywords") or step1.get("keywords") or []),
        )
    yield emit_milestone("【Step3】正在验证关键词并组装检索方案…", kind="route")
    plan = assemble_legacy_plan(
        question=effective_question,
        step1=step1,
        step2=step2,
        probe_snapshot={},
        probe_source=probe_source,
        driver=driver,
        history_snapshot=history_snapshot_pre,
    )
    from .routing_telemetry import log_routing_event

    rid = str(plan.get("route_id") or step2.get("route_id") or "")
    event_id = log_routing_event(
        question=question,
        effective_question=effective_question,
        user_id=user_id,
        session_id=session_id,
        route_id=rid,
        meta={
            "system": "langchain",
            "user_route_choice": bool(user_route_id),
            "gold_route_id": user_route_id if user_route_id else None,
            "label_source": "user_choice" if user_route_id else None,
            "lock_skip_hybrid": True,
        },
        step1=step1,
        step2=step2,
        plan=plan,
        slots=step2.get("params_draft") if isinstance(step2.get("params_draft"), dict) else {},
    )
    if event_id:
        plan["routing_event_id"] = event_id
    yield emit_milestone("检索方案已确定。", kind="route")
    yield (ROUTE_PLAN_INTERNAL, {"plan": plan})


def stream_route_plan(
    client: Any,
    model: str,
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    driver: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
    route_clarify: Optional[Dict[str, Any]] = None,
    _skip_enrich: bool = False,
    _effective_question: Optional[str] = None,
    _history_snapshot: Optional[Dict[str, Any]] = None,
    _route_lock: Optional[Any] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    from .route_clarify import (
        ROUTE_CLARIFY_EVENT,
        build_route_clarify_payload_from_llm,
        needs_llm_route_clarify,
        route_clarify_enabled,
    )
    from .routing_telemetry import log_routing_clarify_prompt, log_user_route_choice
    from .znwd4_history_context import enrich_short_follow_up
    from .memory_store import get_memory_store
    from .znwd4_llm_context import build_routing_memory_block, llm_context_enrich_enabled

    user_route_id: Optional[str] = None
    clarify_event_id: Optional[str] = None
    if route_clarify and isinstance(route_clarify, dict):
        user_route_id = str(
            route_clarify.get("selectedRouteId") or route_clarify.get("selected_route_id") or ""
        ).strip() or None
        clarify_event_id = str(
            route_clarify.get("clarifyEventId") or route_clarify.get("clarify_event_id") or ""
        ).strip() or None
        orig = str(route_clarify.get("originalQuestion") or route_clarify.get("original_question") or "").strip()
        if orig:
            question = orig

    memory_block = build_routing_memory_block(user_id, session_id, max_rounds=3) if user_id and session_id else ""

    if _skip_enrich and _effective_question:
        effective_question = str(_effective_question or question or "").strip()
        history_snapshot_pre = _history_snapshot or {}
    else:
        if user_id and session_id and llm_context_enrich_enabled():
            _store = get_memory_store()
            if _store and _store.get_message_count(user_id, session_id) > 0:
                yield emit_milestone("【上下文】正在结合对话历史与摘要理解问题…", kind="route")

        effective_question, history_snapshot_pre = enrich_short_follow_up(
            question,
            user_id=user_id,
            session_id=session_id,
            client=client,
            model=model,
        )
        effective_question = str(effective_question or question or "").strip()
        if effective_question != question:
            yield emit_milestone(f"结合上下文，理解为「{effective_question}」…", kind="route")

    if _route_lock is not None:
        route_lock = _route_lock
        yield from _route_lock_progress_events(route_lock, status="done")
    else:
        yield from _route_lock_progress_events(None, status="loading")
        route_lock = safe_resolve_route_lock(effective_question)
        if route_lock:
            yield from _route_lock_progress_events(route_lock, status="done")

    if route_lock and skip_llm_for_lock(route_lock):
        yield emit_milestone(
            f"程序意图锁：{_purpose_zh_for_lock(route_lock)}（{route_lock.route_id}）",
            kind="route",
        )
        yield emit_milestone("程序锁直出：无需 LLM 路由…", kind="route")
        plan = finalize_locked_plan(
            question=effective_question,
            lock=route_lock,
            driver=driver,
            history_snapshot=history_snapshot_pre,
            probe_source="lock_slotless",
        )
        yield emit_milestone("检索方案已确定。", kind="route")
        yield (ROUTE_PLAN_INTERNAL, {"plan": plan})
        return

    if route_lock and hybrid_routing_complete_lock(route_lock):
        yield from _yield_plan_from_complete_lock(
            effective_question=effective_question,
            question=question,
            route_lock=route_lock,
            driver=driver,
            history_snapshot_pre=history_snapshot_pre,
            user_id=user_id,
            session_id=session_id,
            user_route_id=user_route_id,
            probe_source="lock_complete",
        )
        return

    tool_defs = build_znwd4_openai_tool_definitions()
    dispatch = build_znwd4_tool_dispatch(user_id=user_id, session_id=session_id, driver=driver)
    llm = ChatOpenAI(**_lc_kwargs(client, model, llm_limits))
    llm_tools = llm.bind_tools(tool_defs)
    session_hint = f"(session_id={session_id[:8]}…)" if session_id else ""

    yield emit_milestone("【Step1】正在判断问题目的…", kind="route")

    # --- Step1: purpose ---
    step1_messages: List[Any] = [
        SystemMessage(content=build_step1_system_prompt()),
        HumanMessage(content=build_routing_human(effective_question, session_hint=session_hint, memory_block=memory_block)),
    ]
    ai1: Optional[AIMessage] = None
    hist1: Dict[str, Any] = {}
    for ev in _stream_tool_round(llm_tools, step1_messages, dispatch, max_rounds=2, stage="step1"):
        if ev[0] == _TOOL_ROUND_RESULT:
            ai1 = ev[1]["ai"]
            hist1 = ev[1].get("history") or {}
        else:
            yield ev
    assert ai1 is not None
    if hist1 and not history_snapshot_pre:
        history_snapshot_pre = hist1
    step1 = parse_step1_json(_lc_text(ai1.content))
    if not step1.get("purpose"):
        step1["purpose"] = guess_purpose_from_question(effective_question)

    route_lock = safe_resolve_route_lock(effective_question)
    if route_lock:
        step1 = apply_route_lock_to_step1(step1, route_lock, effective_question)
        step2 = build_locked_step2(route_lock)
        yield emit_milestone(
            f"程序意图锁：{_purpose_zh_for_lock(route_lock)}（{route_lock.route_id}）",
            kind="route",
        )
    else:
        route_lock = None

    ms1 = milestone_route_step1(step1.get("purpose_zh") or step1.get("purpose", ""))
    if ms1:
        yield emit_milestone(ms1, kind="route")
        yield emit_route_progress(
            stage="step1",
            status="done",
            message=ms1,
            purpose=str(step1.get("purpose") or ""),
            purpose_zh=str(step1.get("purpose_zh") or ""),
            keywords=list(step1.get("keywords") or []),
        )

    if step1.get("purpose") == PURPOSE_CHAT:
        plan = assemble_legacy_plan(
            question=effective_question,
            step1=step1,
            step2={"route_id": "R-CHAT-direct", "params_draft": {}, "keywords": []},
            probe_snapshot={},
            driver=driver,
            history_snapshot=history_snapshot_pre,
        )
        yield emit_milestone("检索方案已确定：闲聊直答。", kind="route")
        yield (ROUTE_PLAN_INTERNAL, {"plan": plan})
        return

    # --- Step2: route_id ---
    candidates = filter_routes_by_purpose(str(step1.get("purpose") or ""))
    if not candidates:
        candidates = filter_routes_by_purpose(guess_purpose_from_question(effective_question))
    if route_lock:
        candidates = inject_locked_route(candidates, route_lock)
    if not candidates:
        from .routing_catalog import list_routes

        candidates = list_routes()[:12]

    probe_tool: Dict[str, Any] = {}
    if user_route_id:
        yield emit_milestone("已收到您的选择，正在按您确认的业务意图继续检索…", kind="route")
        if clarify_event_id:
            log_user_route_choice(
                clarify_event_id=clarify_event_id,
                gold_route_id=user_route_id,
                question=question,
                user_id=user_id,
                session_id=session_id,
                selected_label=str(
                    route_clarify.get("selectedLabel") or route_clarify.get("selected_label") or ""
                ),
            )
        entry = get_route(user_route_id)
        step2 = {
            "route_id": user_route_id,
            "params_draft": {},
            "keywords": list(step1.get("keywords") or []),
            "focus": str(step1.get("keyword") or ""),
            "match_reason": "用户确认业务意图",
            "confidence": 1.0,
            "route_candidates": [{"route_id": user_route_id, "confidence": 1.0, "reason": "user_choice"}],
        }
        if entry:
            step2["params_draft"] = merge_slots_from_keywords(step1, step2, user_route_id)
            if driver:
                from .answer_correction import reslot_for_route
                from .pcsr_assembly import build_evidence_for_question
                ulock = safe_resolve_route_lock(effective_question)
                uevidence = build_evidence_for_question(
                    effective_question, driver=driver, lock=ulock
                )
                step2["params_draft"] = reslot_for_route(
                    effective_question,
                    user_route_id,
                    existing_slots=step2["params_draft"],
                    user_edited_slots=False,
                    driver=driver,
                    lock=ulock,
                    evidence=uevidence,
                )
        ms2 = milestone_route_step2(user_route_id, step2.get("match_reason", ""))
        if ms2:
            yield emit_milestone(ms2, kind="route")
    elif route_lock:
        step2 = build_locked_step2(route_lock)
        ms2 = milestone_route_step2(step2.get("route_id", ""), step2.get("match_reason", ""))
        if ms2:
            yield emit_milestone(ms2, kind="route")
    else:
        yield emit_milestone("【Step2】正在匹配业务方案…", kind="route")
        candidate_msg = build_step2_candidate_message(candidates)
        step2_messages: List[Any] = [
            SystemMessage(content=build_step2_system_prompt(candidate_msg)),
            HumanMessage(
                content=build_routing_human(
                    effective_question,
                    session_hint=session_hint,
                    memory_block=memory_block,
                    extra=f"Step1 purpose={step1.get('purpose')} keywords={step1.get('keywords')}",
                )
            ),
        ]
        ai2: Optional[AIMessage] = None
        probe_tool: Dict[str, Any] = {}
        hist2: Dict[str, Any] = {}
        for ev in _stream_tool_round(llm_tools, step2_messages, dispatch, max_rounds=2, stage="step2"):
            if ev[0] == _TOOL_ROUND_RESULT:
                ai2 = ev[1]["ai"]
                probe_tool = ev[1].get("probe") or {}
                hist2 = ev[1].get("history") or {}
            else:
                yield ev
        assert ai2 is not None
        if hist2:
            history_snapshot_pre = hist2
        step2 = parse_step2_json(_lc_text(ai2.content))
        if not step2.get("route_id") and candidates:
            step2["route_id"] = candidates[0].route_id
        conf = step2.get("confidence")
        conf_txt = f"（置信度 {conf:.0%}）" if isinstance(conf, (int, float)) and conf > 0 else ""
        ms2 = milestone_route_step2(step2.get("route_id", ""), step2.get("match_reason", ""))
        if ms2:
            yield emit_milestone(ms2 + conf_txt, kind="route")

        if route_clarify_enabled() and needs_llm_route_clarify(step2):
            payload = build_route_clarify_payload_from_llm(
                effective_question,
                step2,
                driver=driver,
            )
            if payload.get("options"):
                log_routing_clarify_prompt(
                    payload=payload,
                    question=question,
                    effective_question=effective_question,
                    user_id=user_id,
                    session_id=session_id,
                    meta={
                        "llm_topk": payload.get("llmTop"),
                        "step2_route": step2.get("route_id"),
                        "step2_confidence": step2.get("confidence"),
                    },
                )
                yield emit_milestone("需要您帮忙确认一下具体想了解哪类信息…", kind="route")
                yield (ROUTE_CLARIFY_EVENT, payload)
                return

    # --- Step3: program assemble ---
    yield emit_milestone("【Step3】正在验证关键词并组装检索方案…", kind="route")
    probe_source = "tool" if probe_tool.get("best") else "auto"
    plan = assemble_legacy_plan(
        question=effective_question,
        step1=step1,
        step2=step2,
        probe_snapshot=probe_tool,
        probe_source=probe_source,
        driver=driver,
        history_snapshot=history_snapshot_pre,
    )
    from .routing_telemetry import log_routing_event

    rid = str(plan.get("route_id") or step2.get("route_id") or "")
    event_id = log_routing_event(
        question=question,
        effective_question=effective_question,
        user_id=user_id,
        session_id=session_id,
        route_id=rid,
        meta={
            "system": "langchain",
            "user_route_choice": bool(user_route_id),
            "gold_route_id": user_route_id if user_route_id else None,
            "label_source": "user_choice" if user_route_id else None,
        },
        step1=step1,
        step2=step2,
        plan=plan,
        slots=step2.get("params_draft") if isinstance(step2.get("params_draft"), dict) else {},
    )
    if event_id:
        plan["routing_event_id"] = event_id
    yield emit_milestone("检索方案已确定。", kind="route")
    yield (ROUTE_PLAN_INTERNAL, {"plan": plan})


def run_route_plan(client: Any, model: str, question: str, **kwargs: Any) -> Tuple[Dict[str, Any], List[str]]:
    plan: Dict[str, Any] = {}
    notes: List[str] = []
    for ev, data in stream_route_plan(client, model, question, **kwargs):
        if ev == ROUTE_PLAN_INTERNAL:
            plan = data.get("plan") or {}
        elif ev == "thinking":
            notes.append(str(data.get("content") or ""))
    return plan, notes


def generate_znwd4_plan(
    client: Any,
    model: str,
    question: str,
    *,
    driver: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
    skip_route_lock: bool = True,
) -> Dict[str, Any]:
    """
    Step1 + Step2 only (no Step3 assemble). For AF matrix fair comparison.

    When skip_route_lock=True, L0 route locks do not override LLM routing.
    """
    from .route_plan_v2 import merge_slots_from_keywords
    from .znwd4_llm_context import build_routing_memory_block

    # Single-turn benchmark: skip multi-turn enrich (no session).
    effective_question = (question or "").strip()
    _history_snapshot_pre: Dict[str, Any] = {}

    tool_defs = build_znwd4_openai_tool_definitions()
    dispatch = build_znwd4_tool_dispatch(user_id=None, session_id=None, driver=driver)
    llm = ChatOpenAI(**_lc_kwargs(client, model, llm_limits))
    llm_tools = llm.bind_tools(tool_defs)
    session_hint = ""
    memory_block = ""
    llm_calls = 0

    route_lock = None if skip_route_lock else safe_resolve_route_lock(effective_question)
    if route_lock and skip_llm_for_lock(route_lock):
        step1 = apply_route_lock_to_step1({}, route_lock, effective_question)
        step2 = build_locked_step2(route_lock)
        params = merge_slots_from_keywords(step1, step2, route_lock.route_id)
        return {
            "question": effective_question,
            "step1": step1,
            "step2": step2,
            "route_id": route_lock.route_id,
            "purpose": route_lock.purpose,
            "params_draft": params,
            "llm_calls": 0,
            "candidates": [],
            "probe_tool": {},
        }

    step1_messages: List[Any] = [
        SystemMessage(content=build_step1_system_prompt()),
        HumanMessage(
            content=build_routing_human(
                effective_question, session_hint=session_hint, memory_block=memory_block
            )
        ),
    ]
    ai1, _, _ = _run_tool_round(llm_tools, step1_messages, dispatch, max_rounds=2)
    llm_calls += 1
    step1 = parse_step1_json(_lc_text(ai1.content))
    if not step1.get("purpose"):
        step1["purpose"] = guess_purpose_from_question(effective_question)

    if not skip_route_lock:
        route_lock = safe_resolve_route_lock(effective_question)
        if route_lock:
            step1 = apply_route_lock_to_step1(step1, route_lock, effective_question)
            step2 = build_locked_step2(route_lock)
            params = merge_slots_from_keywords(step1, step2, route_lock.route_id)
            return {
                "question": effective_question,
                "step1": step1,
                "step2": step2,
                "route_id": route_lock.route_id,
                "purpose": route_lock.purpose,
                "params_draft": params,
                "llm_calls": llm_calls,
                "candidates": [],
                "probe_tool": {},
            }

    if step1.get("purpose") == PURPOSE_CHAT:
        return {
            "question": effective_question,
            "step1": step1,
            "step2": {"route_id": "R-CHAT-direct", "params_draft": {}, "keywords": []},
            "route_id": "R-CHAT-direct",
            "purpose": PURPOSE_CHAT,
            "params_draft": {},
            "llm_calls": llm_calls,
            "candidates": [],
            "probe_tool": {},
        }

    candidates = filter_routes_by_purpose(str(step1.get("purpose") or ""))
    if not candidates:
        candidates = filter_routes_by_purpose(guess_purpose_from_question(effective_question))
    if not candidates:
        from .routing_catalog import list_routes

        candidates = list_routes()[:12]

    candidate_msg = build_step2_candidate_message(candidates)
    step2_messages: List[Any] = [
        SystemMessage(content=build_step2_system_prompt(candidate_msg)),
        HumanMessage(
            content=build_routing_human(
                effective_question,
                session_hint=session_hint,
                memory_block=memory_block,
                extra=f"Step1 purpose={step1.get('purpose')} keywords={step1.get('keywords')}",
            )
        ),
    ]
    ai2, probe_tool, _ = _run_tool_round(llm_tools, step2_messages, dispatch, max_rounds=2)
    llm_calls += 1
    step2 = parse_step2_json(_lc_text(ai2.content))
    if not step2.get("route_id") and candidates:
        step2["route_id"] = candidates[0].route_id

    route_id = str(step2.get("route_id") or "").strip()
    entry = get_route(route_id)
    purpose = entry.purpose if entry else str(step1.get("purpose") or "")
    params = merge_slots_from_keywords(step1, step2, route_id)

    return {
        "question": effective_question,
        "step1": step1,
        "step2": step2,
        "route_id": route_id,
        "purpose": purpose,
        "params_draft": params,
        "llm_calls": llm_calls,
        "candidates": candidates,
        "probe_tool": probe_tool if isinstance(probe_tool, dict) else {},
    }
