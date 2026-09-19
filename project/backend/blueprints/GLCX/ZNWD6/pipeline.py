"""
ZNWD6 主流水线：τ 探测 → LLM 判别 binding → 模板 plan → 复用 ZNWD4 执行/合成。

与 ZNWD5 不同：不 monkey-patch ZNWD4；通过 initial_plan 注入路由结果。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Generator, Optional, Tuple

from blueprints.GLCX.ZNWD4.znwd4_thinking_emit import emit_milestone

from .binding_heuristics import try_rule_binding
from .binding_selector import select_binding_and_template
from .intent_router import refine_template_by_binding, suggest_template
from .plan_builder import build_route_plan
from .plan_verify import verify_plan
from .probe import build_tau

logger = logging.getLogger(__name__)


def _tier0_chitchat(question: str) -> bool:
    import re

    return bool(re.search(r"^(你好|您好|谢谢|你是谁|再见)[？?！!。]*$", (question or "").strip()))


def stream_znwd6_chat(
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
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    q = (question or "").strip()
    if not q:
        yield ("error", {"message": "问题不能为空"})
        return

    use_llm_binding = os.environ.get("ZNWD6_LLM_BINDING", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

    # 纠错 / 实体澄清 / 路由澄清：仍走 ZNWD4 完整链路
    if answer_correction or entity_clarify or route_clarify:
        from blueprints.GLCX.ZNWD4.pipeline import stream_znwd4_chat

        yield from stream_znwd4_chat(
            client,
            model,
            q,
            user_id=user_id,
            session_id=session_id,
            neo4j_driver=neo4j_driver,
            chroma_collection=chroma_collection,
            llm_limits=llm_limits,
            skip_instant_thinking=skip_instant_thinking,
            route_clarify=route_clarify,
            answer_correction=answer_correction,
            entity_clarify=entity_clarify,
        )
        return

    yield (
        "step",
        {"type": "routing", "status": "loading", "message": "正在探测知识库…", "phase": "znwd6_probe"},
    )
    yield emit_milestone("正在从图库与向量索引召回实体候选…", kind="route")

    from blueprints.GLCX.ZNWD.session_context import resolve_question_context

    from .intent_router import enforce_binding_template, infer_template_hint_from_question, _resolve_effective_template_hint

    resolved = resolve_question_context(
        q,
        user_id=user_id,
        session_id=session_id,
        client=client,
        model=model,
    )
    q_eff = resolved.resolved_question
    if resolved.is_follow_up and q_eff != q:
        logger.info(
            "[ZNWD6] follow-up resolved raw=%r -> %r layer=%s",
            q[:80],
            q_eff[:120],
            (resolved.debug or {}).get("layer"),
        )
        yield (
            "context_resolved",
            {
                "rawQuestion": q,
                "resolvedQuestion": q_eff,
                "isFollowUp": True,
                "templateHint": resolved.template_hint,
                "debug": resolved.debug,
            },
        )
        yield emit_milestone(f"理解为：{q_eff}", kind="route")

    route_t0 = time.perf_counter()
    tau_t0 = time.perf_counter()
    tau = build_tau(q_eff, driver=neo4j_driver)
    tau_ms = round((time.perf_counter() - tau_t0) * 1000)
    yield ("timing", {"phase": "tau_ms", "ms": tau_ms})
    suggested_tid, reason = suggest_template(q_eff, tau, template_hint=resolved.template_hint)
    logger.info(
        "[ZNWD6] tau spans=%d candidates=%d suggest=%s (%s)",
        len(tau.get("spans") or []),
        len(tau.get("flat_candidates") or []),
        suggested_tid,
        reason,
    )

    binding: Dict[str, Any] = {"bindings": [], "template_id": suggested_tid, "intent": reason, "doc_hints": []}
    bind_errors: list = []

    if _tier0_chitchat(q_eff):
        binding["template_id"] = "chitchat"
        yield ("timing", {"phase": "binding_ms", "ms": 0})
    else:
        bind_t0 = time.perf_counter()
        rule_binding = try_rule_binding(q_eff, tau, suggested_tid)
        if rule_binding:
            binding = rule_binding
            logger.info(
                "[ZNWD6] rule binding: %s",
                [b.get("name") for b in binding.get("bindings") or []],
            )
        elif resolved.carry_bindings:
            carry_tid = resolved.template_hint or suggested_tid
            binding = {
                "bindings": list(resolved.carry_bindings),
                "template_id": carry_tid,
                "intent": reason,
                "doc_hints": [],
            }
            logger.info(
                "[ZNWD6] carry binding from session: %s",
                [b.get("name") for b in binding.get("bindings") or []],
            )
        elif use_llm_binding and client and model:
            yield emit_milestone("正在结合问句从候选实体中消歧…", kind="route")
            binding, bind_errors = select_binding_and_template(
                client,
                model,
                q_eff,
                tau,
                suggested_template_id=suggested_tid,
                llm_limits=llm_limits,
            )
            if bind_errors:
                logger.info("[ZNWD6] binding warnings: %s", bind_errors[:5])
            if not binding.get("template_id"):
                binding["template_id"] = suggested_tid
        else:
            from .ontology_loader import slot_key_for_node_type

            auto: list = []
            for sp in tau.get("spans") or []:
                cands = sp.get("candidates") or []
                if not cands:
                    continue
                c0 = cands[0]
                nt = str(c0.get("node_type") or "")
                auto.append(
                    {
                        "span": sp.get("span"),
                        "entity_id": c0.get("entity_id"),
                        "node_type": nt,
                        "name": c0.get("name"),
                        "slot_key": slot_key_for_node_type(nt),
                    }
                )
            binding = {
                "bindings": auto,
                "template_id": suggested_tid,
                "intent": reason,
                "doc_hints": [],
            }
        binding_ms = round((time.perf_counter() - bind_t0) * 1000)
        yield ("timing", {"phase": "binding_ms", "ms": binding_ms})

    effective_hint = _resolve_effective_template_hint(q_eff, tau, resolved.template_hint)
    binding = enforce_binding_template(
        binding,
        suggested_tid=suggested_tid,
        suggest_reason=reason,
        template_hint=effective_hint,
        question=q_eff,
        tau=tau,
    )

    template_id = refine_template_by_binding(
        str(binding.get("template_id") or suggested_tid),
        binding,
        template_hint=effective_hint,
        question=q_eff,
        tau=tau,
    )
    plan = build_route_plan(q_eff, tau, binding, template_id=template_id, driver=neo4j_driver)
    verr, plan = verify_plan(plan, tau)
    if verr:
        logger.warning("[ZNWD6] plan verify: %s", verr)

    yield emit_milestone(f"已选定检索模板：{template_id}", kind="route")
    route_ms = round((time.perf_counter() - route_t0) * 1000)
    yield ("timing", {"phase": "route_ms", "ms": route_ms})
    yield (
        "step",
        {
            "type": "routing",
            "status": "done",
            "message": f"ZNWD6 路由完成（{template_id}）",
            "phase": "znwd6_plan",
            "templateId": template_id,
            "candidateCount": len(tau.get("flat_candidates") or []),
            "bindingCount": len(binding.get("bindings") or []),
            "zvecUsed": bool(tau.get("zvec_used")),
        },
    )

    from blueprints.GLCX.ZNWD4.pipeline import stream_znwd4_chat

    yield from stream_znwd4_chat(
        client,
        model,
        q_eff,
        user_id=user_id,
        session_id=session_id,
        neo4j_driver=neo4j_driver,
        chroma_collection=chroma_collection,
        llm_limits=llm_limits,
        skip_instant_thinking=True,
        initial_plan=plan,
    )
