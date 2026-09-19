"""LLM 从 τ 候选中判别式选择 binding σ（禁止 KB 外 entity）。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .ontology_loader import list_template_ids, slot_key_for_node_type

logger = logging.getLogger(__name__)


def _loads(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def _format_tau_for_prompt(tau: Dict[str, Any]) -> str:
    lines: List[str] = []
    for sp in tau.get("spans") or []:
        span = str(sp.get("span") or "")
        lines.append(f"【mention】{span}")
        for c in sp.get("candidates") or []:
            lines.append(
                f"  - id={c.get('entity_id')} type={c.get('node_type')} "
                f"name={c.get('name')} score={c.get('score')} source={c.get('source')}"
            )
        if not sp.get("candidates"):
            lines.append("  - （无候选）")
    return "\n".join(lines) if lines else "（无实体候选）"


def _validate_binding(payload: Dict[str, Any], tau: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    allowed = {
        str(c.get("entity_id") or ""): c
        for c in (tau.get("flat_candidates") or [])
        if c.get("entity_id")
    }
    bindings = payload.get("bindings") if isinstance(payload.get("bindings"), list) else []
    clean: List[Dict[str, Any]] = []
    for b in bindings:
        if not isinstance(b, dict):
            continue
        eid = str(b.get("entity_id") or "").strip()
        if eid and eid not in allowed:
            errors.append(f"entity_id not in tau: {eid!r}")
            continue
        row = allowed.get(eid, {})
        clean.append(
            {
                "span": str(b.get("span") or ""),
                "entity_id": eid,
                "node_type": str(b.get("node_type") or row.get("node_type") or ""),
                "name": str(b.get("name") or row.get("name") or ""),
                "slot_key": slot_key_for_node_type(str(b.get("node_type") or row.get("node_type") or "")),
            }
        )
    template_id = str(payload.get("template_id") or "").strip()
    if template_id and template_id not in list_template_ids():
        errors.append(f"unknown template_id: {template_id!r}")
    return {
        "bindings": clean,
        "template_id": template_id,
        "intent": str(payload.get("intent") or "").strip(),
        "doc_hints": [str(x) for x in (payload.get("doc_hints") or []) if str(x).strip()],
    }, errors


def select_binding_and_template(
    client: Any,
    model: str,
    question: str,
    tau: Dict[str, Any],
    *,
    suggested_template_id: str = "",
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """一次 LLM：从 τ 选 entity + 选 template_id。"""
    templates = list_template_ids()
    sys_msg = (
        "你是装备试验领域问答系统的实体消歧与意图路由助手。\n"
        "规则：\n"
        "1. entity_id 必须来自下方候选列表，禁止编造。\n"
        "2. template_id 必须从给定列表中选择。\n"
        "3. 只输出 JSON："
        '{"bindings":[{"span":"...","entity_id":"...","node_type":"...","name":"..."}],'
        '"template_id":"...","intent":"...","doc_hints":[]}\n'
        f"可选 template_id：{', '.join(templates)}"
    )
    human = (
        f"问句：{question}\n\n"
        f"实体候选 τ：\n{_format_tau_for_prompt(tau)}\n\n"
        f"程序建议 template_id：{suggested_template_id or '（无）'}"
    )
    kw: Dict[str, Any] = {"model": model, "temperature": 0}
    if client:
        bu = getattr(client, "base_url", None)
        if bu:
            kw["base_url"] = str(bu)
        key = getattr(client, "api_key", None)
        kw["api_key"] = key or "EMPTY"
    if llm_limits and llm_limits.get("max_tokens"):
        kw["max_tokens"] = int(llm_limits["max_tokens"])
    llm = ChatOpenAI(**kw)
    resp = llm.invoke([SystemMessage(content=sys_msg), HumanMessage(content=human)])
    content = resp.content
    if isinstance(content, list):
        content = "".join(str(x) for x in content)
    try:
        payload = _loads(str(content or "{}"))
    except Exception as exc:
        logger.warning("[ZNWD6][binding] JSON parse failed: %s", exc)
        return {"bindings": [], "template_id": suggested_template_id, "intent": "", "doc_hints": []}, [
            f"json_parse: {exc}"
        ]
    return _validate_binding(payload if isinstance(payload, dict) else {}, tau)
