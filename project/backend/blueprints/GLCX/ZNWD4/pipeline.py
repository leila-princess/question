"""
ZNWD4 主流水线：路由 → reconcile → expand → execute → 直出或合成（SSE 事件与 v4 兼容）。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .user_biz_catalog import COMP_EQUIP_PROJECT_ENV_SETTINGS
from .business_catalog import BIZ_EQUIP_PROJECT_BY_METHOD, BIZ_METHOD_CONTENT, BIZ_NODE_PROP, BIZ_PROJECT_TO_METHOD
from .business_expand import expand_business, expand_method_content_chroma_fallback
from .playbook_resolver import expand_playbook_meta_steps
from .executor import chroma_rows_nonempty, execute_meta_steps, normalize_chroma_rows_for_client
from .answer_markdown import normalize_answer_markdown, append_env_reference_media
from .formatters import (
    equip_project_method_rows_nonempty,
    format_direct_answer,
    format_direct_doc_answer,
    format_method_content,
    method_nodes_have_content,
)
from .memory_store import get_memory_store
from .plan_reconcile import reconcile_route_plan
from . import routing_dispatcher
from .routing_dispatcher import ROUTE_CLARIFY_EVENT, ROUTE_PLAN_INTERNAL
from .routing_telemetry import (
    log_answer_correction_rerun,
    log_execution_outcome,
    log_result_guard_retry,
    log_routing_event,
)
from .znwd4_langchain_synthesis import stream_synthesis
from .znwd4_llm_errors import is_truthy
from .source_table_bridge import build_znwd4_table_keyword
from .znwd4_thinking_emit import (
    emit_milestone,
    instant_thinking_enabled,
    milestone_reconcile,
    milestone_retrieval_done,
    sanitize_thinking_for_sse,
)

logger = logging.getLogger(__name__)

_NEO_SOURCE_CAP = 120


def _enrich_done_payload(
    done_payload: Dict[str, Any],
    *,
    plan: Dict[str, Any],
    rec: Optional[Dict[str, Any]],
    question: str,
    driver: Any,
) -> Dict[str, Any]:
    from .answer_correction import answer_feedback_enabled, build_correction_hints

    out = dict(done_payload)
    if out.get("awaitingRouteClarify"):
        return out

    for src in (rec or {}, plan):
        if not isinstance(src, dict):
            continue
        for _k in (
            "routing_tier",
            "routing_path_source",
            "source",
            "zvec_used",
            "faq_slot_verified",
            "llm_routing_calls",
        ):
            if src.get(_k) not in (None, "") and _k not in out:
                out[_k] = src[_k]
        if src.get("source") and not out.get("routing_path_source"):
            out["routing_path_source"] = src["source"]

    if answer_feedback_enabled():
        hints = build_correction_hints(plan, rec, question, driver=driver)
        if hints:
            out["turnId"] = str(uuid.uuid4())
            out["routingEventId"] = str(plan.get("routing_event_id") or "")
            out["correctionHints"] = hints
    return out


def _yield_source_bundle(
    *,
    table_kw: str,
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    kw = (table_kw or "").strip()
    if kw:
        yield ("search_keywords", {"ssyqTableKeyword": kw})
    yield (
        "source_data",
        {
            "neo4jResults": list(neo_rows[:_NEO_SOURCE_CAP]),
            "chromaResults": list(chroma_rows or []),
        },
    )

_DIRECT_LLM_SYSTEM = """你是友好助手。若提供了【对话历史】，必须依据历史作答。
- 用户问「上一个/刚才/之前的问题」时，从历史中提取**最近一轮用户问句**原文回答，不要说「没有历史」。
- 不要编造未出现在历史中的内容。
输出格式：
先 <THINK>1～2句简短推理</THINK>
再 <CONTENT>完整回答</CONTENT>
不要输出 JSON 或代码围栏。"""

_THINK_BLOCK = re.compile(r"<\s*THINK\s*>([\s\S]*?)<\s*/\s*THINK\s*>", re.I)
_CONTENT_BLOCK = re.compile(r"<\s*CONTENT\s*>([\s\S]*?)<\s*/\s*CONTENT\s*>", re.I)


def _finalize_memory(
    user_id: Optional[str],
    session_id: Optional[str],
    question: str,
    answer: str,
    client: Any,
    model: str,
    plan: Optional[Dict[str, Any]] = None,
) -> None:
    if not user_id or not session_id or not (answer or "").strip():
        return
    store = get_memory_store()
    if not store:
        return
    try:
        store.add_message(user_id, session_id, "user", question)
        store.add_message(user_id, session_id, "assistant", answer)
        from .znwd4_llm_context import refresh_session_summary_async

        refresh_session_summary_async(user_id, session_id, question, answer, client, model)
        store.maybe_compress_async(user_id, session_id, client, model)
        if isinstance(plan, dict) and plan:
            from blueprints.GLCX.ZNWD.session_context import update_session_context_from_plan

            update_session_context_from_plan(user_id, session_id, question, plan)
    except Exception as e:
        logger.warning("[ZNWD4][memory] %s", e)


def _yield_instant_thinking() -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    yield ("thinking_start", {})
    yield emit_milestone("已收到您的问题，正在分析…", kind="system")


def _stream_direct_llm_think_content(
    client: Any,
    model: str,
    question: str,
    llm_limits: Optional[Dict[str, Any]],
    *,
    history_snapshot: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    from .znwd4_history_context import build_direct_llm_user_message
    from .znwd4_langchain_synthesis import _lc_kwargs

    user_msg = build_direct_llm_user_message(
        question,
        history_snapshot=history_snapshot,
        user_id=user_id,
        session_id=session_id,
    )

    llm = ChatOpenAI(**_lc_kwargs(client, model, llm_limits))
    buf = ""
    think_emitted = ""
    answer_emitted = ""
    full_answer = ""
    full_thinking = ""

    yield emit_milestone("无需查库，正在组织回答…")

    for chunk in llm.stream(
        [SystemMessage(content=_DIRECT_LLM_SYSTEM), HumanMessage(content=user_msg)]
    ):
        piece = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
        if not piece:
            continue
        buf += piece

        m_th = _THINK_BLOCK.search(buf)
        if m_th:
            inner = (m_th.group(1) or "").strip()
            if inner and len(inner) > len(think_emitted):
                delta = inner[len(think_emitted) :]
                think_emitted = inner
                full_thinking = inner
                if delta:
                    yield ("thinking", {"content": delta})

        m_co = _CONTENT_BLOCK.search(buf)
        if m_co:
            inner = (m_co.group(1) or "").strip()
            if inner and len(inner) > len(answer_emitted):
                delta = inner[len(answer_emitted) :]
                answer_emitted = inner
                full_answer = inner
                if delta:
                    yield ("answer", {"content": delta})

    if not full_answer:
        visible = _CONTENT_BLOCK.sub("", _THINK_BLOCK.sub("", buf)).strip()
        if visible:
            full_answer = visible
            yield ("answer", {"content": visible})
    if not full_thinking and buf.strip():
        m = _THINK_BLOCK.search(buf)
        if m:
            full_thinking = (m.group(1) or "").strip()
        elif not full_answer:
            yield ("thinking", {"content": "正在撰写回答…\n"})
            full_thinking = "正在撰写回答…"

    table_kw = build_znwd4_table_keyword(question)
    yield from _yield_source_bundle(table_kw=table_kw, neo_rows=[], chroma_rows=[])
    yield ("step", {"type": "generating", "status": "done", "message": "完成"})
    done_payload: Dict[str, Any] = {
        "fullAnswer": full_answer,
        "fullThinking": sanitize_thinking_for_sse(full_thinking),
    }
    if table_kw:
        done_payload["ssyqTableKeyword"] = table_kw
    yield ("done", done_payload)


def _stream_empty_retrieval_llm_fallback(
    client: Any,
    model: str,
    question: str,
    *,
    rec: Dict[str, Any],
    llm_limits: Optional[Dict[str, Any]] = None,
    business_id: str = "",
    env_settings_mode: bool = False,
    project_method_generate_mode: bool = False,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """检索未命中或无法展开步骤时，由 LLM 说明原因并引导追问（不走 error）。"""
    rq = str(rec.get("retrieval_question") or "").strip()
    synthesis_question = question
    if rq and rq != question:
        synthesis_question = f"{question}\n\n【检索聚焦】{rq}"
    yield emit_milestone("检索未命中，正在为您整理说明…", kind="synthesis")
    for ev, payload in stream_synthesis(
        client,
        model,
        synthesis_question,
        neo_rows=[],
        chroma_rows=[],
        llm_limits=llm_limits,
        method_content_mode=(business_id == BIZ_METHOD_CONTENT),
        env_settings_mode=env_settings_mode,
        project_method_generate_mode=project_method_generate_mode,
        synthesis_params=rec.get("params") if isinstance(rec.get("params"), dict) else {},
    ):
        yield ev, payload


def stream_znwd4_chat(
    client: Any,
    model: str,
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    neo4j_driver: Any = None,
    chroma_collection: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
    skip_instant_thinking: bool = False,
    route_clarify: Optional[Dict[str, Any]] = None,
    answer_correction: Optional[Dict[str, Any]] = None,
    entity_clarify: Optional[Dict[str, Any]] = None,
    initial_plan: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    q = (question or "").strip()
    if not q:
        yield ("error", {"message": "问题不能为空"})
        return

    if skip_instant_thinking:
        pass
    elif instant_thinking_enabled():
        yield from _yield_instant_thinking()
    else:
        yield ("thinking_start", {})

    yield (
        "step",
        {
            "type": "routing",
            "status": "loading",
            "message": "正在理解问题…",
            "phase": "bootstrap",
        },
    )

    logger.info("[ZNWD4][DEBUG][Pipeline-1/路由前] question=%r", q)

    plan: Dict[str, Any] = {}
    full_thinking_parts: List[str] = []
    clarify_payload: Dict[str, Any] = {}
    rec: Dict[str, Any] = {}

    entity_sel = ""
    if isinstance(entity_clarify, dict):
        entity_sel = str(
            entity_clarify.get("selectedNodeId") or entity_clarify.get("selected_node_id") or ""
        ).strip()

    if entity_sel:
        from .entity_clarify import apply_entity_selection

        orig_q = str(
            entity_clarify.get("originalQuestion")
            or entity_clarify.get("original_question")
            or q
        ).strip()
        if orig_q:
            q = orig_q
        yield emit_milestone("正在按您选择的装备类型检索…", kind="route")
        full_thinking_parts.append("正在按您选择的装备类型检索…\n")
        plan = apply_entity_selection(
            {
                "route_id": "R-EQUIPMENT-under_type",
                "user_biz": "UBIZ_EQUIPMENT",
                "scenario": "under_type",
                "match_confidence": "high",
                "params_draft": {},
                "keyword": q[:80],
                "answer_strategy": "db_direct",
                "retrieval_question": q,
            },
            node_id=entity_sel,
            parent_type=str(
                entity_clarify.get("parentType") or entity_clarify.get("parent_type") or ""
            ),
            child_name=str(
                entity_clarify.get("childName")
                or entity_clarify.get("child_name")
                or entity_clarify.get("name")
                or ""
            ),
        )
        plan["retrieval_question"] = q
    elif answer_correction:
        from .answer_correction import apply_answer_correction, validate_correction_payload

        ok, err = validate_correction_payload(answer_correction)
        if not ok:
            yield ("error", {"message": err})
            return

        orig_q = str(
            answer_correction.get("originalQuestion")
            or answer_correction.get("original_question")
            or q
        ).strip()
        if orig_q:
            q = orig_q

        yield emit_milestone("正在按您的纠正重新检索…", kind="route")
        full_thinking_parts.append("正在按您的纠正重新检索…\n")

        try:
            plan = apply_answer_correction(answer_correction, question=q, driver=neo4j_driver)
        except ValueError as exc:
            yield ("error", {"message": str(exc)})
            return
        except Exception as exc:
            logger.exception("[ZNWD4][correction] apply failed: %s", exc)
            yield ("error", {"message": f"纠错方案组装失败：{type(exc).__name__}: {exc}"})
            return

        corrected_rid = str(plan.get("route_id") or "")
        event_id = log_routing_event(
            question=q,
            effective_question=q,
            user_id=user_id,
            session_id=session_id,
            route_id=corrected_rid,
            meta={
                "user_answer_correction": True,
                "label_source": "user_answer_correction",
                "gold_route_id": corrected_rid,
                "parent_turn_id": answer_correction.get("turnId"),
                "parent_routing_event_id": answer_correction.get("routingEventId"),
            },
            plan=plan,
            slots=plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {},
        )
        if event_id:
            plan["routing_event_id"] = event_id

        if is_truthy(answer_correction.get("rerun")):
            log_answer_correction_rerun(
                turn_id=str(answer_correction.get("turnId") or ""),
                routing_event_id=str(answer_correction.get("routingEventId") or ""),
                new_routing_event_id=event_id or "",
                question=q,
                gold_route_id=corrected_rid,
                gold_slots=plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {},
                feedback_kind=str(answer_correction.get("feedbackKind") or ""),
                user_id=user_id,
                session_id=session_id,
            )
    elif isinstance(initial_plan, dict) and initial_plan:
        plan = dict(initial_plan)
        logger.info(
            "[ZNWD4][DEBUG][Pipeline-2/注入计划] source=%s route_id=%s",
            plan.get("source"),
            plan.get("route_id"),
        )
        ms = emit_milestone("已接收预置检索计划，开始执行…", kind="route")
        yield ms
        full_thinking_parts.append(str(ms[1].get("content") or ""))
    else:
        for ev, data in routing_dispatcher.stream_route_plan_auto(
            client,
            model,
            q,
            user_id=user_id,
            session_id=session_id,
            driver=neo4j_driver,
            llm_limits=llm_limits,
            route_clarify=route_clarify,
        ):
            if ev == ROUTE_PLAN_INTERNAL:
                plan = data.get("plan") or {}
                logger.info(
                    "[ZNWD4][DEBUG][Pipeline-2/路由输出] plan=%s",
                    json.dumps(plan, ensure_ascii=False, default=str)[:3000],
                )
                continue
            if ev == ROUTE_CLARIFY_EVENT:
                clarify_payload = data if isinstance(data, dict) else {}
                yield (ev, clarify_payload)
                continue
            if ev == "thinking":
                full_thinking_parts.append(str(data.get("content") or ""))
            yield (ev, data)

        if clarify_payload and not plan:
            yield (
                "done",
                {
                    "awaitingRouteClarify": True,
                    "clarifyEventId": clarify_payload.get("clarifyEventId"),
                    "fullAnswer": "",
                    "fullThinking": sanitize_thinking_for_sse("".join(full_thinking_parts)),
                },
            )
            return

    if str(plan.get("source") or "") == "action_planner":
        from blueprints.GLCX.ZNWD5.action_planner_pipeline import stream_action_planner_execution

        yield from stream_action_planner_execution(
            plan=plan,
            question=q,
            client=client,
            model=model,
            neo4j_driver=neo4j_driver,
            chroma_collection=chroma_collection,
            user_id=user_id,
            session_id=session_id,
            llm_limits=llm_limits,
            full_thinking_parts=full_thinking_parts,
        )
        return

    rec = reconcile_route_plan(
        plan,
        question=q,
        driver=neo4j_driver,
        user_id=user_id,
        session_id=session_id,
    )
    strategy = rec.get("answer_strategy") or "direct_llm"
    business_id = rec.get("business_id") or ""

    if rec.get("meta_answer"):
        text = str(rec.get("meta_answer") or "").strip()
        yield emit_milestone("正在整理答复…")
        full_thinking_parts.append("正在整理答复…\n")
        table_kw = build_znwd4_table_keyword(q, reconcile=rec, neo_rows=[])
        yield from _yield_source_bundle(table_kw=table_kw, neo_rows=[], chroma_rows=[])
        yield ("answer", {"content": text})
        full_thinking = sanitize_thinking_for_sse("".join(full_thinking_parts))
        done_payload = {"fullAnswer": text, "fullThinking": full_thinking}
        if table_kw:
            done_payload["ssyqTableKeyword"] = table_kw
        yield ("done", _enrich_done_payload(done_payload, plan=plan, rec=rec, question=q, driver=neo4j_driver))
        _finalize_memory(user_id, session_id, q, text, client, model, plan=plan)
        return

    logger.info(
        "[ZNWD4][DEBUG][Pipeline-3/对齐后] business_id=%r strategy=%r answer_mode=%r params_keys=%s playbook=%r",
        business_id,
        strategy,
        rec.get("answer_mode"),
        list(rec.get("params", {}).keys()) if isinstance(rec.get("params"), dict) else [],
        rec.get("playbook"),
    )

    ms = milestone_reconcile(rec)
    if ms:
        yield emit_milestone(ms)
        full_thinking_parts.append(ms + "\n")

    user_biz_title = str(rec.get("user_biz_title") or "").strip()
    from .znwd4_user_copy import pack_step_payload

    yield (
        "step",
        pack_step_payload(
            "routing",
            "done",
            userBizTitle=user_biz_title,
            businessId=business_id,
            userBiz=rec.get("user_biz") or "",
            scenario=rec.get("scenario") or "",
            routeId=str(plan.get("route_id") or ""),
            purpose=str(plan.get("purpose") or ""),
            paramsDraft=rec.get("params") if isinstance(rec.get("params"), dict) else (plan.get("params_draft") or {}),
            probeSnapshot=plan.get("probe_snapshot") if isinstance(plan.get("probe_snapshot"), dict) else {},
            debugMessage=f"business_id={business_id}",
        ),
    )

    from .entity_clarify import ENTITY_CLARIFY_EVENT, entity_clarify_from_plan

    entity_payload = entity_clarify_from_plan(plan, rec, question=q)
    if entity_payload:
        yield emit_milestone("需要确认具体装备类型…")
        full_thinking_parts.append("需要确认具体装备类型…\n")
        yield (ENTITY_CLARIFY_EVENT, entity_payload)
        yield (
            "done",
            {
                "awaitingEntityClarify": True,
                "fullAnswer": "",
                "fullThinking": sanitize_thinking_for_sse("".join(full_thinking_parts)),
            },
        )
        return

    if strategy == "direct_llm" or not business_id:
        hist_snap = plan.get("history_snapshot") if isinstance(plan.get("history_snapshot"), dict) else {}
        if not (hist_snap.get("rounds")) and str(plan.get("use_history")).lower() in ("true", "1"):
            from .znwd4_history_context import load_history_from_store

            hist_snap = load_history_from_store(user_id, session_id, n=5)
        yield ("step", {"type": "generating", "status": "loading", "message": "正在生成回答…"})
        for ev, data in _stream_direct_llm_think_content(
            client,
            model,
            q,
            llm_limits,
            history_snapshot=hist_snap,
            user_id=user_id,
            session_id=session_id,
        ):
            if ev == "thinking":
                full_thinking_parts.append(str(data.get("content") or ""))
            if ev == "done" and isinstance(data, dict):
                data = _enrich_done_payload(data, plan=plan, rec=rec, question=q, driver=neo4j_driver)
            yield (ev, data)
            if ev == "done":
                _finalize_memory(
                    user_id,
                    session_id,
                    q,
                    str(data.get("fullAnswer") or ""),
                    client,
                    model,
                    plan=plan,
                )
        return

    rq_exec = rec.get("retrieval_question") or q
    if rec.get("playbook"):
        steps = expand_playbook_meta_steps(rec, retrieval_question=rq_exec)
    else:
        steps = expand_business(business_id, rec.get("params") or {}, retrieval_question=rq_exec)
    if not steps:
        yield emit_milestone("未能匹配清单检索条件，正在为您整理说明…", kind="retrieval")
        full_thinking_parts.append("未能匹配清单检索条件，正在为您整理说明…\n")
        yield (
            "step",
            pack_step_payload("retrieval", "done", neoHits=0, chromaHits=0, debugMessage="no_steps"),
        )
        from .project_method_fallback import (
            attach_method_create_action,
            enrich_and_merge_for_method_draft,
            should_offer_method_draft,
        )

        _rec_params_early = rec.get("params") if isinstance(rec.get("params"), dict) else {}
        _route_id_early = str(_rec_params_early.get("route_id") or plan.get("route_id") or "")
        _user_biz_early = str(rec.get("user_biz") or "")
        _scenario_early = str(rec.get("scenario") or "")
        offer_draft_early, focus_names_early = should_offer_method_draft(
            business_id=business_id,
            user_biz=_user_biz_early,
            route_id=_route_id_early,
            scenario=_scenario_early,
            neo_rows=[],
            params=_rec_params_early,
            question=q,
            keyword=str(rec.get("keyword") or ""),
        )
        neo_rows_early: List[Dict[str, Any]] = []
        chroma_rows_early: List[Dict[str, Any]] = []
        project_method_generate_mode_early = False
        if offer_draft_early and focus_names_early:
            yield emit_milestone("图库无关联方法，正在查询装备、环境与标准文档…", kind="retrieval")
            full_thinking_parts.append("图库无关联方法，正在查询装备、环境与标准文档…\n")
            neo_rows_early, chroma_rows_early = enrich_and_merge_for_method_draft(
                neo4j_driver,
                chroma_collection,
                neo_rows=[],
                chroma_rows=[],
                focus_names=focus_names_early,
                question=q,
            )
            project_method_generate_mode_early = True
        env_settings_mode_early = str(rec.get("user_biz") or "") == COMP_EQUIP_PROJECT_ENV_SETTINGS
        full_answer = ""
        for ev, payload in _stream_empty_retrieval_llm_fallback(
            client=client,
            model=model,
            question=q,
            rec=rec,
            llm_limits=llm_limits,
            business_id=business_id,
            env_settings_mode=env_settings_mode_early,
            project_method_generate_mode=project_method_generate_mode_early,
        ):
            if ev == "thinking":
                full_thinking_parts.append(str(payload.get("content") or ""))
            yield ev, payload
            if ev == "answer":
                full_answer += str(payload.get("content") or "")
        full_answer = normalize_answer_markdown(full_answer)
        yield ("step", {"type": "generating", "status": "done", "message": "完成"})
        full_thinking = sanitize_thinking_for_sse("".join(full_thinking_parts))
        done_payload = attach_method_create_action(
            {"fullAnswer": full_answer, "fullThinking": full_thinking},
            question=q,
            plan=plan,
            rec=rec,
            neo_rows=neo_rows_early,
            full_answer=full_answer,
            project_method_generate_mode=project_method_generate_mode_early,
        )
        yield ("done", _enrich_done_payload(done_payload, plan=plan, rec=rec, question=q, driver=neo4j_driver))
        _finalize_memory(user_id, session_id, q, full_answer, client, model, plan=plan)
        return

    logger.info(
        "[ZNWD4][DEBUG][Pipeline-4/步骤展开] steps_count=%d steps=%s",
        len(steps),
        json.dumps([{k: v for k, v in s.items() if k != "params"} for s in steps], ensure_ascii=False)[:1000],
    )
    yield emit_milestone(f"已准备 {len(steps)} 步检索…", kind="route")
    full_thinking_parts.append(f"已准备 {len(steps)} 步检索…\n")

    from .znwd4_user_copy import pack_step_payload

    if business_id == BIZ_METHOD_CONTENT:
        yield emit_milestone("正在查询试验方法正文…", kind="retrieval")
        full_thinking_parts.append("正在查询试验方法正文…\n")
    else:
        yield emit_milestone("正在清单与标准库中检索…", kind="retrieval")
        full_thinking_parts.append("正在清单与标准库中检索…\n")
    yield (
        "step",
        pack_step_payload("retrieval", "loading", debugMessage="execute_meta_steps"),
    )

    import os
    from .executor import iter_meta_steps

    neo_rows: List[Dict[str, Any]] = []
    chroma_rows: List[Dict[str, Any]] = []
    step_results: List[Dict[str, Any]] = []
    sub_steps_acc: List[Dict[str, Any]] = []
    use_step_sse = (os.environ.get("ZNWD4_RETRIEVAL_STEP_SSE") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

    if use_step_sse:
        for ev in iter_meta_steps(neo4j_driver, chroma_collection, steps):
            if ev[0] == "step_start":
                data = ev[1]
                purpose = str(data.get("purpose") or "")
                sub_steps_acc.append(
                    {
                        "id": str((data.get("step") or {}).get("step_id") or ""),
                        "purpose": purpose,
                        "status": "running",
                    }
                )
                yield emit_milestone(
                    f"正在检索：{purpose}…" if purpose else "正在检索…",
                    kind="retrieval",
                )
                yield (
                    "step",
                    pack_step_payload(
                        "retrieval",
                        "loading",
                        purpose=purpose,
                        progress_current=int(data.get("current") or 0),
                        progress_total=int(data.get("total") or 0),
                        subSteps=list(sub_steps_acc),
                    ),
                )
            elif ev[0] == "step_done":
                data = ev[1]
                neo_rows = list(data.get("neo_rows") or [])
                chroma_rows = list(data.get("chroma_rows") or [])
                sid = str((data.get("step") or {}).get("step_id") or "")
                for sub in sub_steps_acc:
                    if sub.get("id") == sid:
                        sub["status"] = "done"
                if neo_rows:
                    yield (
                        "source_data",
                        {
                            "neo4jResults": list(neo_rows[:_NEO_SOURCE_CAP]),
                            "chromaResults": list(chroma_rows or []),
                            "partial": True,
                        },
                    )
            elif ev[0] == "complete":
                data = ev[1]
                neo_rows = list(data.get("neo_rows") or [])
                chroma_rows = list(data.get("chroma_rows") or [])
                step_results = list(data.get("step_results") or [])
    else:
        neo_rows, chroma_rows, step_results = execute_meta_steps(neo4j_driver, chroma_collection, steps)

    chroma_rows = normalize_chroma_rows_for_client(chroma_rows)

    from .result_guard import apply_result_guard, anchor_from_rec

    _guard_before = len([r for r in neo_rows if isinstance(r, dict) and not r.get("error")])
    if _guard_before:
        yield (
            "step",
            pack_step_payload("guard", "loading", debugMessage="result_guard"),
        )
    guard_out = apply_result_guard(
        question=q,
        rec=rec,
        steps=steps,
        neo_rows=neo_rows,
        driver=neo4j_driver,
        chroma_collection=chroma_collection,
        client=client,
        model=model,
        llm_limits=llm_limits,
    )
    if guard_out.milestone:
        from .znwd4_user_copy import sanitize_user_text

        gm = sanitize_user_text(guard_out.milestone, fallback="正在核对检索结果…")
        yield emit_milestone(gm, kind="guard")
        full_thinking_parts.append(gm + "\n")
    if guard_out.retried or guard_out.filtered:
        neo_rows = guard_out.neo_rows
        _guard_after = len([r for r in neo_rows if isinstance(r, dict) and not r.get("error")])
        log_result_guard_retry(
            question=q,
            action=guard_out.action,
            anchor=anchor_from_rec(rec),
            before_count=_guard_before,
            after_count=_guard_after,
            before_rate=float(guard_out.meta.get("before_rate") or 0),
            after_rate=float(guard_out.meta.get("match_rate") or 0),
            user_id=user_id,
            session_id=session_id,
            meta=guard_out.meta,
        )
    if _guard_before:
        yield (
            "step",
            pack_step_payload(
                "guard",
                "done",
                debugMessage=f"action={guard_out.action}",
            ),
        )

    logger.info(
        "[ZNWD4][DEBUG][Pipeline-5/执行结果] neo_rows=%d chroma_rows=%d step_results_count=%d",
        len(neo_rows),
        len(chroma_rows),
        len(step_results),
    )

    if business_id == BIZ_METHOD_CONTENT and not method_nodes_have_content(neo_rows):
        yield emit_milestone("清单中方法正文为空，正在检索标准文档…", kind="retrieval")
        full_thinking_parts.append("清单中方法正文为空，正在检索标准文档…\n")
        doc_steps = expand_method_content_chroma_fallback(rec.get("retrieval_question") or q)
        method_doc_fallback = True
        if doc_steps:
            _, chroma_extra, step_results_doc = execute_meta_steps(
                neo4j_driver, chroma_collection, doc_steps
            )
            chroma_rows = normalize_chroma_rows_for_client((chroma_rows or []) + (chroma_extra or []))
            step_results = (step_results or []) + (step_results_doc or [])
    else:
        method_doc_fallback = False

    if str(plan.get("source") or "") == "znwd6" and plan.get("znwd6_deferred_doc"):
        from blueprints.GLCX.ZNWD6.deferred_doc_bridge import enrich_doc_rows, should_run_deferred_doc

        if should_run_deferred_doc(plan, chroma_rows):
            yield emit_milestone("正在结合图检索结果检索标准文档…", kind="retrieval")
            full_thinking_parts.append("正在结合图检索结果检索标准文档…\n")
            doc_rows = enrich_doc_rows(
                rec.get("retrieval_question") or q,
                neo_rows,
                chroma_collection,
            )
            if doc_rows:
                chroma_rows = normalize_chroma_rows_for_client((chroma_rows or []) + doc_rows)

    table_kw = build_znwd4_table_keyword(q, reconcile=rec, neo_rows=neo_rows)
    from .znwd4_thinking_emit import stream_retrieval_preview

    for line in stream_retrieval_preview(
        business_id=business_id, neo_rows=neo_rows, chroma_rows=chroma_rows, max_lines=8
    ):
        yield emit_milestone(line, kind="retrieval")
        full_thinking_parts.append(line + "\n")
    yield from _yield_source_bundle(table_kw=table_kw, neo_rows=neo_rows, chroma_rows=chroma_rows)

    neo_n, chroma_n = len(neo_rows), len(chroma_rows)
    log_execution_outcome(
        session_id=session_id or "",
        question=q,
        neo_hits=neo_n,
        chroma_hits=chroma_n,
        answer_strategy=strategy,
        business_id=business_id,
    )
    ret_msg = milestone_retrieval_done(neo_n, chroma_n)
    yield emit_milestone(ret_msg, kind="retrieval")
    full_thinking_parts.append(ret_msg + "\n")

    project_method_generate_mode = False
    guided_exec = bool(plan.get("guided_execute"))
    from .project_method_fallback import (
        attach_method_create_action,
        enrich_and_merge_for_method_draft,
        should_offer_method_draft,
    )

    _rec_params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
    _route_id = str(_rec_params.get("route_id") or plan.get("route_id") or "")
    _user_biz = str(rec.get("user_biz") or "")
    _scenario = str(rec.get("scenario") or "")

    offer_draft, focus_names = should_offer_method_draft(
        business_id=business_id,
        user_biz=_user_biz,
        route_id=_route_id,
        scenario=_scenario,
        neo_rows=neo_rows,
        params=_rec_params,
        question=q,
        keyword=str(rec.get("keyword") or ""),
    )
    if guided_exec or bool(plan.get("skip_method_draft_fallback")):
        offer_draft = False
        focus_names = []
    if offer_draft and focus_names:
        yield emit_milestone("图库无关联方法，正在查询装备、环境与标准文档…", kind="retrieval")
        full_thinking_parts.append("图库无关联方法，正在查询装备、环境与标准文档…\n")
        neo_rows, chroma_rows = enrich_and_merge_for_method_draft(
            neo4j_driver,
            chroma_collection,
            neo_rows=neo_rows,
            chroma_rows=chroma_rows,
            focus_names=focus_names,
            question=q,
        )
        neo_n, chroma_n = len(neo_rows), len(chroma_rows)
        project_method_generate_mode = True

    yield (
        "step",
        pack_step_payload(
            "retrieval",
            "done",
            neoHits=neo_n,
            chromaHits=chroma_n,
            stepResults=step_results[:5],
            subSteps=sub_steps_acc,
            debugMessage=f"neo={neo_n} chroma={chroma_n}",
        ),
    )

    answer_mode = str(rec.get("answer_mode") or "")
    use_direct = strategy in ("db_direct", "db_direct_doc") or answer_mode == "database_direct"
    if rec.get("playbook") and rec.get("multi_step") and strategy not in ("db_direct", "db_direct_doc"):
        use_direct = False

    if business_id == BIZ_METHOD_CONTENT and method_nodes_have_content(neo_rows):
        use_direct = True

    _neo_ok = len([r for r in neo_rows if isinstance(r, dict) and not r.get("error")]) > 0
    if not chroma_rows_nonempty(chroma_rows) and _neo_ok:
        if business_id in (BIZ_EQUIP_PROJECT_BY_METHOD, "BIZ_REL_TWO_HOP") and equip_project_method_rows_nonempty(neo_rows):
            use_direct = True
        elif business_id == BIZ_PROJECT_TO_METHOD and project_method_generate_mode:
            use_direct = False
        elif business_id in (
            "BIZ_REL_ONE_HOP",
            "BIZ_REL_REVERSE_ONE_HOP",
            "BIZ_ENUM_NODES",
            BIZ_NODE_PROP,
        ):
            use_direct = True
        elif business_id == BIZ_PROJECT_TO_METHOD:
            use_direct = True

    # 向量/文档题 db_direct_doc：直出检索片段，不走 LLM 归纳
    if strategy == "db_direct_doc" and chroma_rows_nonempty(chroma_rows):
        use_direct = True
    elif method_doc_fallback and chroma_rows_nonempty(chroma_rows):
        use_direct = True
    elif chroma_rows_nonempty(chroma_rows) and strategy != "db_direct_doc":
        use_direct = False

    if strategy == "db_synthesize" and not guided_exec:
        use_direct = False

    if guided_exec and strategy in ("db_direct", "db_direct_doc"):
        use_direct = True
        project_method_generate_mode = False

    if project_method_generate_mode and guided_exec:
        project_method_generate_mode = False
        use_direct = True

    if project_method_generate_mode:
        use_direct = False

    if use_direct and not project_method_generate_mode:
        offer_draft_late, focus_names_late = should_offer_method_draft(
            business_id=business_id,
            user_biz=_user_biz,
            route_id=_route_id,
            scenario=_scenario,
            neo_rows=neo_rows,
            params=_rec_params,
            question=q,
            keyword=str(rec.get("keyword") or ""),
        )
        if offer_draft_late and focus_names_late:
            yield emit_milestone("图库无关联方法，正在查询装备、环境与标准文档…", kind="retrieval")
            full_thinking_parts.append("图库无关联方法，正在查询装备、环境与标准文档…\n")
            neo_rows, chroma_rows = enrich_and_merge_for_method_draft(
                neo4j_driver,
                chroma_collection,
                neo_rows=neo_rows,
                chroma_rows=chroma_rows,
                focus_names=focus_names_late,
                question=q,
            )
            project_method_generate_mode = True
            use_direct = False

    has_evidence = _neo_ok or chroma_rows_nonempty(chroma_rows)
    # 检索为空：LLM 兜底说明（有命中时模板业务仍直出）
    if not has_evidence and business_id:
        use_direct = False

    from .znwd4_experiment_config import znwd4_use_llm_answer_synthesis

    if znwd4_use_llm_answer_synthesis(
        business_id,
        strategy=strategy,
        user_biz=str(rec.get("user_biz") or ""),
    ) and has_evidence and not guided_exec:
        use_direct = False

    if business_id == BIZ_NODE_PROP and _neo_ok:
        from .env_property_utils import narrow_environment_property_rows

        p = rec.get("params") if isinstance(rec.get("params"), dict) else {}
        neo_rows = narrow_environment_property_rows(
            neo_rows,
            focus=str(p.get("value") or p.get("focus") or rec.get("keyword") or ""),
            focus_id=str(p.get("focus_id") or ""),
        )

    env_settings_mode = str(rec.get("user_biz") or "") == COMP_EQUIP_PROJECT_ENV_SETTINGS

    if use_direct:
        from .znwd4_user_copy import pack_step_payload

        yield emit_milestone("正在整理答复…")
        full_thinking_parts.append("正在整理答复…\n")
        if business_id == BIZ_METHOD_CONTENT and method_doc_fallback and chroma_rows_nonempty(chroma_rows):
            text = format_direct_doc_answer(chroma_rows)
        elif business_id == BIZ_METHOD_CONTENT:
            p = rec.get("params") or {}
            method_nm = str(p.get("method_name") or p.get("focus") or "").strip()
            project_nm = str(p.get("project_name") or "").strip()
            if method_nm and project_nm and (project_nm in method_nm or method_nm.startswith(project_nm)):
                project_nm = ""
            text, _ = format_method_content(
                neo_rows,
                project_name=project_nm,
                method_name=method_nm,
            )
        else:
            text = format_direct_answer(
                business_id,
                neo_rows=neo_rows,
                chroma_rows=chroma_rows,
                params=rec.get("params"),
                user_biz=str(rec.get("user_biz") or ""),
                step_results=step_results,
            )
        text = normalize_answer_markdown(text)
        yield ("step", pack_step_payload("generating", "loading", debugMessage="format_direct_answer"))
        yield ("answer", {"content": text})
        yield ("step", pack_step_payload("generating", "done", debugMessage="完成"))
        full_thinking = sanitize_thinking_for_sse("".join(full_thinking_parts))
        done_payload = {"fullAnswer": text, "fullThinking": full_thinking}
        if table_kw:
            done_payload["ssyqTableKeyword"] = table_kw
        yield ("done", _enrich_done_payload(done_payload, plan=plan, rec=rec, question=q, driver=neo4j_driver))
        _finalize_memory(user_id, session_id, q, text, client, model, plan=plan)
        return

    yield emit_milestone("正在整理答复…")
    full_thinking_parts.append("正在整理答复…\n")
    yield ("step", {"type": "generating", "status": "loading", "message": "正在整理答复…"})
    full_answer = ""
    rq = str(rec.get("retrieval_question") or "").strip()
    synthesis_question = q
    if rq and rq != q:
        synthesis_question = f"{q}\n\n【检索聚焦】{rq}"
    for ev, payload in stream_synthesis(
        client,
        model,
        synthesis_question,
        neo_rows=neo_rows,
        chroma_rows=chroma_rows,
        llm_limits=llm_limits,
        method_content_mode=(business_id == BIZ_METHOD_CONTENT),
        env_settings_mode=env_settings_mode,
        project_method_generate_mode=project_method_generate_mode,
        synthesis_params=rec.get("params") if isinstance(rec.get("params"), dict) else {},
    ):
        if ev == "thinking":
            full_thinking_parts.append(str(payload.get("content") or ""))
        yield (ev, payload)
        if ev == "answer":
            full_answer += payload.get("content", "")
    full_answer = normalize_answer_markdown(full_answer)
    if env_settings_mode:
        full_answer = append_env_reference_media(full_answer, neo_rows)
    yield ("step", {"type": "generating", "status": "done", "message": "完成"})
    full_thinking = sanitize_thinking_for_sse("".join(full_thinking_parts))
    done_payload = attach_method_create_action(
        {"fullAnswer": full_answer, "fullThinking": full_thinking},
        question=q,
        plan=plan,
        rec=rec,
        neo_rows=neo_rows,
        full_answer=full_answer,
        project_method_generate_mode=project_method_generate_mode,
    )
    if table_kw:
        done_payload["ssyqTableKeyword"] = table_kw
    yield ("done", _enrich_done_payload(done_payload, plan=plan, rec=rec, question=q, driver=neo4j_driver))
    _finalize_memory(user_id, session_id, q, full_answer, client, model, plan=plan)
