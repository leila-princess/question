"""
ZNWD3 确认器（方案 B）。
程序预分类生成 2-3 个候选，LLM 确认选择。
单次调用，提示词约 400 token。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .entity_probe import probe_entities
from .pre_classifier import Candidate, get_top_candidates
from .prompts.confirmation import _CONFIRMATION_SYSTEM_PROMPT, build_confirmation_human
from .slot_extractor import extract_slots

logger = logging.getLogger(__name__)

_CONFIRMATION_TIMEOUT = 30.0


def _lc_kwargs(client: Any, model: str, llm_limits: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    kw: Dict[str, Any] = {"model": model, "temperature": 0}
    if client:
        bu = getattr(client, "base_url", None)
        if bu is not None:
            kw["base_url"] = str(bu).rstrip("/")
        ak = getattr(client, "api_key", None)
        if ak:
            kw["api_key"] = ak
    if llm_limits:
        mo = llm_limits.get("max_output_tokens")
        if mo:
            try:
                kw["max_tokens"] = min(int(mo), 256)
            except (TypeError, ValueError):
                pass
    kw["timeout"] = _CONFIRMATION_TIMEOUT
    kw["max_retries"] = 0
    return kw


def _build_candidates_text(candidates: List[Candidate]) -> str:
    labels = ["A", "B", "C", "D"]
    lines = []
    for i, c in enumerate(candidates):
        if i >= 3:
            break
        lines.append(f"{labels[i]}) {c.user_biz} / {c.scenario}（{c.title}）")
    lines.append(f"D) 都不对（走 fallback 路径）")
    return "\n".join(lines)


def _parse_confirmation_json(raw: str) -> Dict[str, Any]:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```\s*$", "", s)
        s = s.strip()
    if not s:
        return {"choice": "D", "confidence": "low"}
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                return {"choice": "D", "confidence": "low"}
        else:
            return {"choice": "D", "confidence": "low"}
    if not isinstance(obj, dict):
        return {"choice": "D", "confidence": "low"}
    return {
        "choice": str(obj.get("choice") or "D").strip().upper(),
        "confidence": str(obj.get("confidence") or "low").strip().lower(),
    }


def stream_confirm_route(
    client: Any,
    model: str,
    question: str,
    *,
    driver: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """
    方案 B 路由：程序预分类 + LLM 确认。
    
    流程：
    1. 程序槽位提取 + probe 验证
    2. 程序预分类生成 2-3 个候选
    3. LLM 确认选择（1 次调用）
    4. 返回确认结果
    """
    q = (question or "").strip()
    if not q:
        yield ("error", {"message": "问题不能为空"})
        return

    # Step 1: 程序槽位提取 + probe 验证
    slots = extract_slots(q)
    keywords = []
    for slot_key in ["equipment", "environment", "project", "indicator", "standard"]:
        for kw in slots.get(slot_key, []):
            if kw not in keywords:
                keywords.append(kw)

    probe_results = {}
    if keywords and driver:
        probe_results = probe_entities(driver, keywords)

    # Step 2: 程序预分类
    candidates = get_top_candidates(slots, probe_results, max_count=3)
    if not candidates:
        # 无候选，直接走 fallback
        yield ("route_result", {
            "mode": "confirm",
            "choice": "D",
            "confidence": "low",
            "candidates": [],
            "slots": slots,
            "probe_results": probe_results,
        })
        return

    candidates_text = _build_candidates_text(candidates)

    yield ("thinking", {"content": "正在理解您的问题…", "kind": "route"})

    # Step 3: LLM 确认
    llm = ChatOpenAI(**_lc_kwargs(client, model, llm_limits))
    messages = [
        SystemMessage(content=_CONFIRMATION_SYSTEM_PROMPT),
        HumanMessage(content=build_confirmation_human(q, candidates_text)),
    ]

    raw_output = ""
    try:
        for chunk in llm.stream(messages):
            piece = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
            if piece:
                raw_output += piece
    except Exception as e:
        logger.warning("[ZNWD3][confirm] LLM failed: %s", e)
        # Fallback: 选第一个候选
        yield ("confirm_fallback", {"reason": str(e)})
        yield ("route_result", {
            "mode": "confirm",
            "choice": "A",
            "confidence": "medium",
            "candidates": [{"user_biz": c.user_biz, "scenario": c.scenario, "title": c.title} for c in candidates],
            "slots": slots,
            "probe_results": probe_results,
        })
        return

    # Step 4: 解析确认结果
    confirmation = _parse_confirmation_json(raw_output)
    choice = confirmation.get("choice", "D")
    confidence = confirmation.get("confidence", "low")

    # 映射选择到候选
    choice_map = {"A": 0, "B": 1, "C": 2, "D": -1}
    idx = choice_map.get(choice, -1)
    selected = candidates[idx] if 0 <= idx < len(candidates) else None

    logger.info(
        "[ZNWD3][confirm] question=%r choice=%r confidence=%r selected=%s",
        q, choice, confidence, selected,
    )

    yield ("route_result", {
        "mode": "confirm",
        "choice": choice,
        "confidence": confidence,
        "selected": {
            "user_biz": selected.user_biz if selected else "",
            "scenario": selected.scenario if selected else "",
            "title": selected.title if selected else "",
        },
        "candidates": [{"user_biz": c.user_biz, "scenario": c.scenario, "title": c.title} for c in candidates],
        "slots": slots,
        "probe_results": probe_results,
        "llm_raw": raw_output[:300],
    })
