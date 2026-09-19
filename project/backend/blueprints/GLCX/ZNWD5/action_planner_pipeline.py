"""
ZNWD5 ActionPlanner pipeline 分支：expand → execute → assemble/synthesize。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Generator, List, Optional, Tuple

from blueprints.GLCX.ZNWD4.answer_markdown import normalize_answer_markdown
from blueprints.GLCX.ZNWD4.executor import chroma_rows_nonempty, iter_meta_steps
from blueprints.GLCX.ZNWD4.routing_telemetry import log_execution_outcome
from blueprints.GLCX.ZNWD4.source_table_bridge import build_znwd4_table_keyword
from blueprints.GLCX.ZNWD4.znwd4_langchain_synthesis import stream_synthesis
from blueprints.GLCX.ZNWD4.znwd4_thinking_emit import (
    emit_milestone,
    milestone_retrieval_done,
    sanitize_thinking_for_sse,
    stream_retrieval_preview,
)

from .action_answer_assembler import assemble_action_plan_answer, should_synthesize
from .action_plan_expand import action_plan_to_reconcile_rec, expand_action_plan
from .action_plan_schema import ActionPlan, action_plan_from_dict

logger = logging.getLogger(__name__)


def _yield_source_bundle(
    *,
    table_kw: str,
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    from blueprints.GLCX.ZNWD4.pipeline import _NEO_SOURCE_CAP, _yield_source_bundle as _base

    yield from _base(table_kw=table_kw, neo_rows=neo_rows, chroma_rows=chroma_rows)


def stream_action_planner_execution(
    *,
    plan: Dict[str, Any],
    question: str,
    client: Any,
    model: str,
    neo4j_driver: Any,
    chroma_collection: Any,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    llm_limits: Optional[Dict[str, Any]] = None,
    full_thinking_parts: Optional[List[str]] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """action_planner 计划执行（跳过 reconcile / Playbook）。"""
    from blueprints.GLCX.ZNWD4.pipeline import _enrich_done_payload, _finalize_memory
    from blueprints.GLCX.ZNWD4.znwd4_user_copy import pack_step_payload

    q = (question or "").strip()
    parts = full_thinking_parts if full_thinking_parts is not None else []
    ap_raw = plan.get("action_plan") if isinstance(plan.get("action_plan"), dict) else {}
    action_plan = action_plan_from_dict(ap_raw)
    grounding = plan.get("grounding") if isinstance(plan.get("grounding"), dict) else {}

    rec = action_plan_to_reconcile_rec(action_plan, question=q, grounding=grounding)
    business_id = str(rec.get("business_id") or "")

    yield (
        "step",
        pack_step_payload(
            "routing",
            "done",
            userBizTitle="动作规划器",
            businessId=business_id,
            routeId="R-ACTION-PLANNER",
            purpose=str(plan.get("purpose_node") or ""),
            paramsDraft=rec.get("params") if isinstance(rec.get("params"), dict) else {},
            probeSnapshot=plan.get("probe_snapshot") if isinstance(plan.get("probe_snapshot"), dict) else {},
            debugMessage=f"action_planner steps={action_plan.total_step_count}",
        ),
    )

    steps, groups = expand_action_plan(action_plan, question=q)
    rec["action_plan_groups"] = groups
    if not steps:
        yield emit_milestone("未能展开动作计划…", kind="retrieval")
        parts.append("未能展开动作计划…\n")
        yield ("error", {"message": "action_plan_expand 未产生检索步骤"})
        return

    logger.info(
        "[ZNWD5][action_planner] expand steps=%d groups=%d plan=%s",
        len(steps),
        len(groups),
        json.dumps(action_plan.to_dict(), ensure_ascii=False)[:1500],
    )
    yield emit_milestone(f"已准备 {len(steps)} 步检索…", kind="route")
    parts.append(f"已准备 {len(steps)} 步检索…\n")
    yield emit_milestone("正在清单与标准库中检索…", kind="retrieval")
    parts.append("正在清单与标准库中检索…\n")
    yield ("step", pack_step_payload("retrieval", "loading", debugMessage="execute_meta_steps"))

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
                sub_steps_acc.append({"purpose": purpose, "status": "loading"})
            elif ev[0] == "step_done":
                data = ev[1]
                sid = str(data.get("step_id") or "")
                rows = data.get("neo_rows") or data.get("rows") or []
                sr = {
                    "step_id": sid,
                    "rows": rows,
                    "neo_rows": rows,
                    "action_planner": next(
                        (s.get("action_planner") for s in steps if str(s.get("step_id")) == sid),
                        {},
                    ),
                }
                step_results.append(sr)
                neo_rows.extend(rows if isinstance(rows, list) else [])
                cr = data.get("chroma_rows") or []
                if isinstance(cr, list):
                    chroma_rows.extend(cr)
                if sub_steps_acc:
                    sub_steps_acc[-1]["status"] = "done"
            elif ev[0] == "done":
                data = ev[1]
                neo_rows = list(data.get("neo_rows") or neo_rows)
                chroma_rows = list(data.get("chroma_rows") or chroma_rows)
                step_results = list(data.get("step_results") or step_results)
    else:
        from blueprints.GLCX.ZNWD5.executor import execute_meta_steps

        neo_rows, chroma_rows, step_results = execute_meta_steps(
            neo4j_driver, chroma_collection, steps
        )

    table_kw = build_znwd4_table_keyword(q, reconcile=rec, neo_rows=neo_rows)
    for line in stream_retrieval_preview(
        business_id=business_id, neo_rows=neo_rows, chroma_rows=chroma_rows, max_lines=8
    ):
        yield emit_milestone(line, kind="retrieval")
        parts.append(line + "\n")
    yield from _yield_source_bundle(table_kw=table_kw, neo_rows=neo_rows, chroma_rows=chroma_rows)

    neo_n, chroma_n = len(neo_rows), len(chroma_rows)
    log_execution_outcome(
        session_id=session_id or "",
        question=q,
        neo_hits=neo_n,
        chroma_hits=chroma_n,
        answer_strategy=str(rec.get("answer_strategy") or ""),
        business_id=business_id,
    )
    ret_msg = milestone_retrieval_done(neo_n, chroma_n)
    yield emit_milestone(ret_msg, kind="retrieval")
    parts.append(ret_msg + "\n")
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

    direct_text = assemble_action_plan_answer(
        action_plan,
        question=q,
        neo_rows=neo_rows,
        chroma_rows=chroma_rows,
        step_results=step_results,
        business_id=business_id,
        params=rec.get("params") if isinstance(rec.get("params"), dict) else {},
    )

    if direct_text and not should_synthesize(
        action_plan, neo_rows=neo_rows, chroma_rows=chroma_rows
    ):
        yield emit_milestone("正在整理答复…")
        parts.append("正在整理答复…\n")
        text = normalize_answer_markdown(direct_text)
        yield ("step", pack_step_payload("generating", "loading", debugMessage="action_assembler"))
        yield ("answer", {"content": text})
        yield ("step", pack_step_payload("generating", "done", debugMessage="完成"))
        full_thinking = sanitize_thinking_for_sse("".join(parts))
        done_payload = {"fullAnswer": text, "fullThinking": full_thinking}
        if table_kw:
            done_payload["ssyqTableKeyword"] = table_kw
        yield ("done", _enrich_done_payload(done_payload, plan=plan, rec=rec, question=q, driver=neo4j_driver))
        _finalize_memory(user_id, session_id, q, text, client, model)
        return

    yield emit_milestone("正在综合检索结果生成回答…")
    parts.append("正在综合检索结果生成回答…\n")
    yield ("step", pack_step_payload("generating", "loading", debugMessage="stream_synthesis"))
    full_answer = ""
    for ev, data in stream_synthesis(
        client,
        model,
        q,
        neo_rows=neo_rows,
        chroma_rows=chroma_rows,
        llm_limits=llm_limits,
        synthesis_params=rec.get("params") if isinstance(rec.get("params"), dict) else {},
    ):
        if ev == "thinking":
            parts.append(str(data.get("content") or ""))
        if ev == "answer":
            full_answer += str(data.get("content") or "")
        yield ev, data

    full_answer = normalize_answer_markdown(full_answer)
    yield ("step", pack_step_payload("generating", "done", debugMessage="完成"))
    full_thinking = sanitize_thinking_for_sse("".join(parts))
    done_payload = {"fullAnswer": full_answer, "fullThinking": full_thinking}
    if table_kw:
        done_payload["ssyqTableKeyword"] = table_kw
    yield ("done", _enrich_done_payload(done_payload, plan=plan, rec=rec, question=q, driver=neo4j_driver))
    _finalize_memory(user_id, session_id, q, full_answer, client, model)


__all__ = ["stream_action_planner_execution"]
