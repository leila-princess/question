"""
ZNWD3 主流水线：语义提取/确认 → 组装 RoutePlan → reconcile → expand → execute → 直出/合成。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .business_catalog import BIZ_EQUIP_PROJECT_BY_METHOD, BIZ_METHOD_CONTENT
from .business_expand import expand_business, expand_method_content_chroma_fallback
from .confirmator import stream_confirm_route
from .entity_probe import probe_entities
from .executor import chroma_rows_nonempty, execute_meta_steps, normalize_chroma_rows_for_client
from .formatters import (
    equip_project_method_rows_nonempty,
    format_direct_answer,
    format_method_content,
    method_nodes_have_content,
)
from .graph_path_finder import find_path_from_probe
from .memory_store import get_memory_store
from .plan_reconcile import reconcile_route_plan
from .playbook_resolver import expand_playbook_meta_steps
from .route_assembler import assemble_route_plan
from .semantic_extractor import stream_semantic_extract
from .slot_extractor import extract_slots, merge_slots, detect_intent
from .source_table_bridge import build_znwd1_table_keyword
from .znwd1_langchain_synthesis import stream_synthesis
from .znwd1_thinking_emit import (
    emit_milestone,
    milestone_reconcile,
    milestone_retrieval_done,
    sanitize_thinking_for_sse,
)

logger = logging.getLogger(__name__)

_NEO_SOURCE_CAP = 120
_DIRECT_LLM_SYSTEM = """你是友好助手。若提供了【对话历史】，必须依据历史作答。
- 用户问「上一个/刚才/之前的问题」时，从历史中提取**最近一轮用户问句**原文回答，不要说「没有历史」。
- 不要编造未出现在历史中的内容。
输出格式：
先 <THINK>1～2句简短推理</THINK>
再 <CONTENT>完整回答</CONTENT>
不要输出 JSON 或代码围栏。"""

_THINK_BLOCK = re.compile(r"<\s*THINK\s*>([\s\S]*?)<\s*/\s*THINK\s*>", re.I)
_CONTENT_BLOCK = re.compile(r"<\s*CONTENT\s*>([\s\S]*?)<\s*/\s*CONTENT\s*>", re.I)


def _get_mode() -> str:
    raw = os.environ.get("ZNWD3_MODE", "extract").strip().lower()
    return raw if raw in ("extract", "confirm") else "extract"


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


def _finalize_memory(
    user_id: Optional[str],
    session_id: Optional[str],
    question: str,
    answer: str,
    client: Any,
    model: str,
) -> None:
    if not user_id or not session_id or not (answer or "").strip():
        return
    store = get_memory_store()
    if not store:
        return
    try:
        store.add_message(user_id, session_id, "user", question)
        store.add_message(user_id, session_id, "assistant", answer)
        store.maybe_compress_async(user_id, session_id, client, model)
    except Exception as e:
        logger.warning("[ZNWD3][memory] %s", e)


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
    from .znwd1_history_context import build_direct_llm_user_message
    from .znwd1_langchain_synthesis import _lc_kwargs

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
                delta = inner[len(think_emitted):]
                think_emitted = inner
                full_thinking = inner
                if delta:
                    yield ("thinking", {"content": delta})

        m_co = _CONTENT_BLOCK.search(buf)
        if m_co:
            inner = (m_co.group(1) or "").strip()
            if inner and len(inner) > len(answer_emitted):
                delta = inner[len(answer_emitted):]
                answer_emitted = inner
                full_answer = inner
                if delta:
                    yield ("answer", {"content": delta})

    if not full_answer:
        visible = _CONTENT_BLOCK.sub("", _THINK_BLOCK.sub("", buf)).strip()
        if visible:
            full_answer = visible
            yield ("answer", {"content": visible})

    table_kw = build_znwd1_table_keyword(question)
    yield from _yield_source_bundle(table_kw=table_kw, neo_rows=[], chroma_rows=[])
    yield ("step", {"type": "generating", "status": "done", "message": "完成"})
    done_payload: Dict[str, Any] = {
        "fullAnswer": full_answer,
        "fullThinking": sanitize_thinking_for_sse(full_thinking),
    }
    if table_kw:
        done_payload["ssyqTableKeyword"] = table_kw
    yield ("done", done_payload)


def stream_znwd3_chat(
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
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    q = (question or "").strip()
    if not q:
        yield ("error", {"message": "问题不能为空"})
        return

    if not skip_instant_thinking:
        yield ("thinking_start", {})
        yield emit_milestone("已收到您的问题，正在分析…", kind="system")

    yield (
        "step",
        {
            "type": "routing",
            "status": "loading",
            "message": "正在理解问题…",
            "phase": "bootstrap",
        },
    )

    mode = _get_mode()
    logger.info("[ZNWD3][pipeline] mode=%s question=%r", mode, q)

    # Step 1: 路由（语义提取或确认器）
    route_result: Dict[str, Any] = {}
    full_thinking_parts: List[str] = []

    if mode == "extract":
        for ev, data in stream_semantic_extract(
            client, model, q, driver=neo4j_driver, llm_limits=llm_limits,
        ):
            if ev == "route_result":
                route_result = data
            elif ev == "thinking":
                full_thinking_parts.append(str(data.get("content") or ""))
            yield (ev, data)
    else:
        for ev, data in stream_confirm_route(
            client, model, q, driver=neo4j_driver, llm_limits=llm_limits,
        ):
            if ev == "route_result":
                route_result = data
            elif ev == "thinking":
                full_thinking_parts.append(str(data.get("content") or ""))
            yield (ev, data)

    # Step 2: 组装 RoutePlan
    slots = route_result.get("slots", extract_slots(q))
    probe_results = route_result.get("probe_results", {})
    
    plan = assemble_route_plan(
        mode=mode,
        question=q,
        slots=slots,
        probe_results=probe_results,
        extraction_result=route_result if mode == "extract" else None,
        confirm_result=route_result if mode == "confirm" else None,
    )

    logger.info(
        "[ZNWD3][pipeline] plan assembled: user_biz=%s scenario=%s confidence=%s",
        plan.get("user_biz"), plan.get("scenario"), plan.get("match_confidence"),
    )

    # Step 3: Reconcile
    rec = reconcile_route_plan(
        plan,
        question=q,
        driver=neo4j_driver,
        user_id=user_id,
        session_id=session_id,
    )
    strategy = rec.get("answer_strategy") or "direct_llm"
    business_id = rec.get("business_id") or ""

    ms = milestone_reconcile(rec)
    if ms:
        yield emit_milestone(ms)
        full_thinking_parts.append(ms + "\n")

    user_biz_title = str(rec.get("user_biz_title") or "").strip()
    yield (
        "step",
        {
            "type": "routing",
            "status": "done",
            "message": f"方案就绪：{user_biz_title or '知识库检索'}",
            "businessId": business_id,
            "userBiz": rec.get("user_biz") or "",
            "scenario": rec.get("scenario") or "",
            "userBizTitle": user_biz_title,
        },
    )

    # Step 4: 直答或检索
    if strategy == "direct_llm" or not business_id:
        hist_snap = plan.get("history_snapshot") if isinstance(plan.get("history_snapshot"), dict) else {}
        if not (hist_snap.get("rounds")) and str(plan.get("use_history")).lower() in ("true", "1"):
            from .znwd1_history_context import load_history_from_store
            hist_snap = load_history_from_store(user_id, session_id, n=5)
        yield ("step", {"type": "generating", "status": "loading", "message": "正在生成回答…"})
        for ev, data in _stream_direct_llm_think_content(
            client, model, q, llm_limits,
            history_snapshot=hist_snap,
            user_id=user_id,
            session_id=session_id,
        ):
            if ev == "thinking":
                full_thinking_parts.append(str(data.get("content") or ""))
            yield (ev, data)
            if ev == "done":
                _finalize_memory(user_id, session_id, q, str(data.get("fullAnswer") or ""), client, model)
        return

    # Step 5: 展开步骤
    rq_exec = rec.get("retrieval_question") or q
    
    # 如果 reconcile 没有生成 business_id，尝试图谱路径查找
    if not business_id:
        intent = plan.get("intent", "")
        path_steps = find_path_from_probe(probe_results, intent=intent)
        if path_steps:
            logger.info("[ZNWD3][pipeline] graph_path_finder generated %d steps", len(path_steps))
            steps = path_steps
        else:
            yield ("error", {"message": "无法确定检索路径"})
            return
    elif rec.get("playbook"):
        steps = expand_playbook_meta_steps(rec, retrieval_question=rq_exec)
    else:
        steps = expand_business(business_id, rec.get("params") or {}, retrieval_question=rq_exec)

    if not steps:
        yield ("error", {"message": "无法展开检索步骤"})
        return

    # Step 6: 执行
    if business_id == BIZ_METHOD_CONTENT:
        yield emit_milestone("正在查询方法节点属性（优先 SYNR）…")
    else:
        yield emit_milestone("正在清单库中检索…")
    yield ("step", {"type": "retrieval", "status": "loading", "message": "正在检索知识库…"})
    
    neo_rows, chroma_rows, step_results = execute_meta_steps(neo4j_driver, chroma_collection, steps)
    chroma_rows = normalize_chroma_rows_for_client(chroma_rows)

    if business_id == BIZ_METHOD_CONTENT and not method_nodes_have_content(neo_rows):
        yield emit_milestone("图库方法字段为空，正在检索标准文档…")
        doc_steps = expand_method_content_chroma_fallback(rec.get("retrieval_question") or q)
        if doc_steps:
            _, chroma_extra, step_results_doc = execute_meta_steps(neo4j_driver, chroma_collection, doc_steps)
            chroma_rows = normalize_chroma_rows_for_client((chroma_rows or []) + (chroma_extra or []))
            step_results = (step_results or []) + (step_results_doc or [])

    table_kw = build_znwd1_table_keyword(q, reconcile=rec, neo_rows=neo_rows)
    yield from _yield_source_bundle(table_kw=table_kw, neo_rows=neo_rows, chroma_rows=chroma_rows)

    neo_n, chroma_n = len(neo_rows), len(chroma_rows)
    ret_msg = milestone_retrieval_done(neo_n, chroma_n)
    yield emit_milestone(ret_msg, kind="retrieval")
    full_thinking_parts.append(ret_msg + "\n")
    yield (
        "step",
        {
            "type": "retrieval",
            "status": "done",
            "message": f"检索完成 Neo4j={neo_n} Chroma={chroma_n}",
            "neoHits": neo_n,
            "chromaHits": chroma_n,
            "stepResults": step_results[:5],
        },
    )

    # Step 7: 决定直出还是合成
    answer_mode = str(rec.get("answer_mode") or "")
    use_direct = strategy == "db_direct" or answer_mode == "database_direct"
    if rec.get("playbook") and rec.get("multi_step"):
        use_direct = False
    if business_id == BIZ_METHOD_CONTENT and method_nodes_have_content(neo_rows):
        use_direct = True

    _neo_ok = len([r for r in neo_rows if isinstance(r, dict) and not r.get("error")]) > 0
    if not chroma_rows_nonempty(chroma_rows) and _neo_ok:
        if business_id in (BIZ_EQUIP_PROJECT_BY_METHOD, "BIZ_REL_TWO_HOP") and equip_project_method_rows_nonempty(neo_rows):
            use_direct = True
        elif business_id in ("BIZ_REL_ONE_HOP", "BIZ_REL_REVERSE_ONE_HOP", "BIZ_ENUM_NODES", "BIZ_PROJECT_TO_METHOD"):
            use_direct = True

    if chroma_rows_nonempty(chroma_rows):
        use_direct = False

    # Step 8: 输出
    if use_direct:
        yield emit_milestone("正在整理答复…")
        full_thinking_parts.append("正在整理答复…\n")
        if business_id == BIZ_METHOD_CONTENT:
            p = rec.get("params") or {}
            text, _ = format_method_content(
                neo_rows,
                project_name=str(p.get("project_name") or ""),
                method_name=str(p.get("method_name") or ""),
            )
        else:
            text = format_direct_answer(
                business_id,
                neo_rows=neo_rows,
                chroma_rows=chroma_rows,
                params=rec.get("params"),
            )
        yield ("answer", {"content": text})
        full_thinking = sanitize_thinking_for_sse("".join(full_thinking_parts))
        done_payload = {"fullAnswer": text, "fullThinking": full_thinking}
        if table_kw:
            done_payload["ssyqTableKeyword"] = table_kw
        yield ("done", done_payload)
        _finalize_memory(user_id, session_id, q, text, client, model)
        return

    yield emit_milestone("正在整理答复…")
    full_thinking_parts.append("正在整理答复…\n")
    yield ("step", {"type": "generating", "status": "loading", "message": "正在整理答复…"})
    full_answer = ""
    for ev, payload in stream_synthesis(
        client, model, rec.get("retrieval_question") or q,
        neo_rows=neo_rows, chroma_rows=chroma_rows, llm_limits=llm_limits,
        method_content_mode=(business_id == BIZ_METHOD_CONTENT),
    ):
        if ev == "thinking":
            full_thinking_parts.append(str(payload.get("content") or ""))
        yield (ev, payload)
        if ev == "answer":
            full_answer += payload.get("content", "")
    yield ("step", {"type": "generating", "status": "done", "message": "完成"})
    full_thinking = sanitize_thinking_for_sse("".join(full_thinking_parts))
    done_payload = {"fullAnswer": full_answer, "fullThinking": full_thinking}
    if table_kw:
        done_payload["ssyqTableKeyword"] = table_kw
    yield ("done", done_payload)
    _finalize_memory(user_id, session_id, q, full_answer, client, model)
