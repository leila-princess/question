"""LLM 生成 PDDL domain/problem（支持流式 SSE）。"""
from __future__ import annotations

import logging
import os
from typing import Dict, Generator, Tuple

from blueprints.FZSJ.JYLLM.multi_agent_engine import call_llm, call_llm_stream

from .pddl_validator import PddlBundle, extract_pddl_blocks, validate_pddl_quality
from .prompts import SKELETON_SYSTEM, build_skeleton_user_prompt
from .retrieval_bundle import RetrievalBundle

logger = logging.getLogger(__name__)

PddlEvent = Tuple[str, Dict]

BRANCH_TEMPLATE_DOMAIN = """(define (domain uav-test-template)
  (:requirements :strips)
  (:predicates (inspection-ok) (damage-detected) (spare-available) (using-spare)
    (env-ready) (baseline-done) (low-test-done) (high-test-done))
  (:action inspect
    :parameters () :precondition () :effect (inspection-ok))
  (:action setup-env
    :parameters () :precondition (inspection-ok)
    :effect (and (env-ready) (baseline-done)))
  (:action run-low-temp
    :parameters () :precondition (and (env-ready) (not damage-detected))
    :effect (low-test-done))
  (:action use-spare
    :parameters () :precondition (and (damage-detected) (spare-available))
    :effect (and (using-spare) (inspection-ok) (not damage-detected)))
  (:action repair
    :parameters () :precondition (damage-detected)
    :effect (and (inspection-ok) (not damage-detected)))
  (:action run-high-temp
    :parameters () :precondition (and (low-test-done) (env-ready))
    :effect (high-test-done))
)
"""

BRANCH_TEMPLATE_PROBLEM = """(define (problem template-prob)
  (:domain uav-test-template)
  (:init (and (spare-available) (damage-detected)))
  (:goal (and (low-test-done) (high-test-done) (baseline-done)))
)
"""


def _stream_llm_to_raw(messages, *, llm_model_key: str, temperature: float, max_tokens: int) -> Generator[PddlEvent, None, str]:
    parts: list[str] = []
    for piece in call_llm_stream(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        llm_model_key=llm_model_key,
    ):
        if isinstance(piece, str) and piece:
            parts.append(piece)
            yield ("pddl_delta", {"text": piece})
    raw = "".join(parts)
    if not raw.strip():
        raw = call_llm(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            llm_model_key=llm_model_key,
        )
    return raw


def iter_generate_pddl_from_llm(
    user_description: str,
    bundle: RetrievalBundle,
    *,
    llm_model_key: str = "",
) -> Generator[PddlEvent, None, PddlBundle]:
    retrieval_text = bundle.to_prompt_text()
    method_ids = [str(m.get("id")) for m in bundle.neo_methods]
    user_prompt = build_skeleton_user_prompt(user_description, retrieval_text)
    messages = [
        {"role": "system", "content": SKELETON_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    yield ("pddl_llm", {"message": "正在流式生成 PDDL…"})
    gen = _stream_llm_to_raw(messages, llm_model_key=llm_model_key, temperature=0.35, max_tokens=8192)
    raw = ""
    try:
        while True:
            ev, data = next(gen)
            yield (ev, data)
    except StopIteration as si:
        raw = si.value or ""

    pb = extract_pddl_blocks(raw)
    errs = validate_pddl_quality(pb.domain, pb.problem, method_ids)
    if not errs:
        yield ("pddl_llm_done", {"rationale": pb.rationale[:200], "streaming": True})
        return pb

    # 自动修复：如果是未声明谓词错误，直接补全 domain 谓词声明
    from .pddl_strips import ensure_predicates_in_domain, list_domain_predicates
    undeclared_preds = []
    for e in errs:
        m = re.search(r"使用未声明谓词 \(([\w-]+)\)", e)
        if m:
            undeclared_preds.append(m.group(1))
    
    if undeclared_preds:
        yield ("pddl_repair", {"message": "PDDL 校验未通过，正在自动补全谓词声明…", "errors": errs[:5]})
        pb.domain = ensure_predicates_in_domain(pb.domain, undeclared_preds)
        errs2 = validate_pddl_quality(pb.domain, pb.problem, method_ids)
        if not errs2:
            yield ("pddl_llm_done", {"rationale": pb.rationale[:200], "streaming": True, "auto_fixed": True})
            return pb

    logger.warning("[pure_llm] PDDL validation: %s; attempting LLM repair", errs)
    yield ("pddl_repair", {"message": "PDDL 校验未通过，正在请求 LLM 修正…", "errors": errs[:5]})

    repair_messages = messages + [
        {"role": "assistant", "content": raw or ""},
        {
            "role": "user",
            "content": "PDDL 校验失败："
            + "; ".join(errs)
                + "。请修正：仅 :strips、:parameters ()、零元谓词、无 ? 与 =；:init/:goal 用 (and ...)。",
        },
    ]
    repair_gen = _stream_llm_to_raw(
        repair_messages, llm_model_key=llm_model_key, temperature=0.2, max_tokens=8192
    )
    repair_raw = ""
    try:
        while True:
            ev, data = next(repair_gen)
            yield (ev, data)
    except StopIteration as si:
        repair_raw = si.value or ""

    pb2 = extract_pddl_blocks(repair_raw)
    errs2 = validate_pddl_quality(pb2.domain, pb2.problem, method_ids)
    if not errs2:
        yield ("pddl_llm_done", {"rationale": pb2.rationale[:200], "streaming": True, "repaired": True})
        return pb2

    # 最终回退：使用分支模板
    logger.warning("[pure_llm] using branch template fallback after repair failure")
    pb_tpl = PddlBundle(
        domain=BRANCH_TEMPLATE_DOMAIN,
        problem=BRANCH_TEMPLATE_PROBLEM,
        rationale="使用内置分支模板（LLM 修复失败后回退）。",
        raw=repair_raw or raw or "",
    )
    yield ("pddl_llm_done", {"rationale": pb_tpl.rationale, "fallback": True})
    return pb_tpl
    raise ValueError("PDDL 生成/校验失败: " + "; ".join(errs2))


def generate_pddl_from_llm(
    user_description: str,
    bundle: RetrievalBundle,
    *,
    llm_model_key: str = "",
) -> PddlBundle:
    gen = iter_generate_pddl_from_llm(user_description, bundle, llm_model_key=llm_model_key)
    result: PddlBundle | None = None
    try:
        while True:
            next(gen)
    except StopIteration as si:
        result = si.value
    if result is None:
        raise ValueError("PDDL 生成失败")
    return result
