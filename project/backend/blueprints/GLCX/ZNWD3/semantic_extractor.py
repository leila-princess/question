"""
ZNWD3 语义提取器（方案 A）。
LLM 只做一件事：从问题中提取实体和意图。
零工具轮次，单次调用，提示词约 300 token。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .entity_probe import probe_entities
from .prompts.extraction import _EXTRACTION_SYSTEM_PROMPT, build_extraction_human
from .slot_extractor import extract_slots, has_slot

logger = logging.getLogger(__name__)

_EXTRACTION_TIMEOUT = 30.0


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
                kw["max_tokens"] = min(int(mo), 512)
            except (TypeError, ValueError):
                pass
    kw["timeout"] = _EXTRACTION_TIMEOUT
    kw["max_retries"] = 0
    return kw


def _parse_extraction_json(raw: str) -> Dict[str, Any]:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```\s*$", "", s)
        s = s.strip()
    if not s:
        return {"entities": [], "intent": "enum", "needs_history": False}
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                return {"entities": [], "intent": "enum", "needs_history": False}
        else:
            return {"entities": [], "intent": "enum", "needs_history": False}
    if not isinstance(obj, dict):
        return {"entities": [], "intent": "enum", "needs_history": False}
    return {
        "entities": obj.get("entities") if isinstance(obj.get("entities"), list) else [],
        "intent": str(obj.get("intent") or "enum").strip().lower(),
        "needs_history": bool(obj.get("needs_history")),
    }


def stream_semantic_extract(
    client: Any,
    model: str,
    question: str,
    *,
    driver: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """
    方案 A 路由：LLM 语义提取 + 程序 probe 验证。
    
    流程：
    1. LLM 提取实体和意图（1 次调用）
    2. 程序 probe 验证每个提取的实体
    3. 返回提取结果 + probe 验证结果
    """
    q = (question or "").strip()
    if not q:
        yield ("error", {"message": "问题不能为空"})
        return

    # Step 1: LLM 语义提取
    llm = ChatOpenAI(**_lc_kwargs(client, model, llm_limits))
    messages = [
        SystemMessage(content=_EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=build_extraction_human(q)),
    ]

    raw_output = ""
    yield ("thinking", {"content": "正在理解您的问题…", "kind": "route"})

    try:
        for chunk in llm.stream(messages):
            piece = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
            if piece:
                raw_output += piece
    except Exception as e:
        logger.warning("[ZNWD3][extract] LLM failed: %s", e)
        # Fallback: 用程序槽位提取
        slots = extract_slots(q)
        intent = "enum"
        yield ("extraction_fallback", {"reason": str(e)})
        yield ("route_result", {
            "entities": [],
            "intent": intent,
            "probe_results": {},
            "slots": slots,
        })
        return

    # Step 2: 解析 LLM 输出
    extraction = _parse_extraction_json(raw_output)
    entities = extraction.get("entities", [])
    intent = extraction.get("intent", "enum")

    # Step 3: 程序 probe 验证实体
    keywords = [e.get("text", "") for e in entities if isinstance(e, dict) and e.get("text")]
    # 同时用程序槽位提取补充关键词
    slots = extract_slots(q)
    for slot_key in ["equipment", "environment", "project", "indicator", "standard"]:
        for kw in slots.get(slot_key, []):
            if kw not in keywords:
                keywords.append(kw)

    probe_results = {}
    if keywords and driver:
        label_hints = [e.get("type") for e in entities if isinstance(e, dict) and e.get("type")]
        label_hints = [lt for lt in label_hints if lt] or None
        probe_results = probe_entities(driver, keywords, label_hints=label_hints)

    logger.info(
        "[ZNWD3][extract] question=%r intent=%r entities=%r probe_hits=%d",
        q, intent, keywords, len(probe_results),
    )

    yield ("route_result", {
        "entities": entities,
        "intent": intent,
        "probe_results": probe_results,
        "slots": slots,
        "llm_raw": raw_output[:500],
    })
