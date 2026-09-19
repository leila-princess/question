"""Optional LLM fallback when deterministic result guard cannot fix retrieval."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .executor import execute_meta_steps
from .result_guard import (
    ResultGuardOutcome,
    compute_match_rate,
)

logger = logging.getLogger(__name__)

_SYSTEM = """你是清单库检索结果校验助手。根据用户问题、槽位关键词与 Neo4j 检索摘要，判断结果是否回答了问题。
仅输出一行 JSON，不要 markdown：
{"action":"accept"|"filter"|"retry_keyword","filter_names":["二级装备类型名"],"retry_keyword":"来自问句或槽位的词"}
规则：
- action=accept：结果基本正确
- action=filter：结果混杂无关装备类型，filter_names 列出应保留的 EquipmentType.Name（必须来自摘要）
- action=retry_keyword：应换更精确关键词重查，retry_keyword 必须来自问句或槽位子串
禁止编造节点 id 或摘要中不存在的名称。"""


def llm_guard_enabled() -> bool:
    raw = (os.environ.get("ZNWD4_RESULT_GUARD_LLM") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip()).lower()


def _row_summary(neo_rows: List[Dict[str, Any]], limit: int = 20) -> List[str]:
    lines: List[str] = []
    for row in neo_rows[:limit]:
        if not isinstance(row, dict) or row.get("error"):
            continue
        et = ""
        eq = ""
        for side in ("n", "m"):
            v = row.get(side)
            if not isinstance(v, dict):
                continue
            labs = v.get("labels") if isinstance(v.get("labels"), list) else []
            props = v.get("properties") if isinstance(v.get("properties"), dict) else {}
            name = str(props.get("Name") or props.get("name") or "").strip()
            if "EquipmentType" in labs and not et:
                et = name
            if "Equipment" in labs and not eq:
                eq = name
        if et or eq:
            lines.append(f"EquipmentType={et or '?'} | Equipment={eq or '?'}")
    return lines


def _parse_llm_json(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return {}
    return {}


def _allowed_retry_keyword(keyword: str, question: str, anchor: str) -> bool:
    kw = _norm(keyword)
    if len(kw) < 2:
        return False
    for src in (question, anchor):
        if kw in _norm(src):
            return True
    return False


def apply_llm_result_guard(
    *,
    question: str,
    anchor: str,
    rec: Dict[str, Any],
    neo_rows: List[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    driver: Any,
    chroma_collection: Any,
    client: Any,
    model: str,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Optional[ResultGuardOutcome]:
    from .znwd4_langchain_synthesis import _lc_kwargs

    summaries = _row_summary(neo_rows)
    if not summaries:
        return None

    params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
    user = (
        f"问句：{question}\n"
        f"槽位关键词：{anchor}\n"
        f"params：{json.dumps(params, ensure_ascii=False)}\n"
        f"检索摘要（前{len(summaries)}条）：\n"
        + "\n".join(f"- {x}" for x in summaries)
    )

    try:
        llm = ChatOpenAI(**_lc_kwargs(client, model, llm_limits))
        resp = llm.invoke([SystemMessage(content=_SYSTEM), HumanMessage(content=user)])
        text = resp.content if isinstance(resp.content, str) else str(resp.content or "")
        data = _parse_llm_json(text)
    except Exception as exc:
        logger.warning("[ZNWD4][result_guard_llm] invoke failed: %s", exc)
        return None

    action = str(data.get("action") or "").strip().lower()
    if action == "accept":
        return ResultGuardOutcome(
            neo_rows=neo_rows,
            action="llm_accept",
            meta={"llm": data},
        )

    if action == "filter":
        names = [str(x).strip() for x in (data.get("filter_names") or []) if str(x).strip()]
        if not names:
            return None
        filtered = [
            r
            for r in neo_rows
            if isinstance(r, dict)
            and not r.get("error")
            and any(
                _norm(n) in _norm(_equipment_type_from_row(r)) or _norm(_equipment_type_from_row(r)) in _norm(n)
                for n in names
            )
        ]
        if filtered:
            return ResultGuardOutcome(
                neo_rows=filtered,
                filtered=True,
                action="llm_filter",
                meta={"llm": data, "match_rate": compute_match_rate(filtered, anchor)},
            )
        return None

    if action == "retry_keyword":
        retry_kw = str(data.get("retry_keyword") or "").strip()
        if not _allowed_retry_keyword(retry_kw, question, anchor):
            return None
        from .result_guard import _patch_under_type_steps

        retry_steps = _patch_under_type_steps(steps, keyword=retry_kw)
        new_neo, _, _ = execute_meta_steps(driver, chroma_collection, retry_steps)
        new_valid = [r for r in new_neo if isinstance(r, dict) and not r.get("error")]
        if new_valid:
            return ResultGuardOutcome(
                neo_rows=new_neo,
                retried=True,
                milestone="检索结果与关键词不一致，正在按 LLM 建议精确重查…",
                action="llm_retry_keyword",
                meta={"llm": data, "match_rate": compute_match_rate(new_valid, retry_kw)},
            )
        return None

    return None


def _equipment_type_from_row(row: Dict[str, Any]) -> str:
    for side in ("n", "m"):
        v = row.get(side)
        if not isinstance(v, dict):
            continue
        labs = v.get("labels") if isinstance(v.get("labels"), list) else []
        if "EquipmentType" not in labs:
            continue
        props = v.get("properties") if isinstance(v.get("properties"), dict) else {}
        return str(props.get("Name") or props.get("name") or "").strip()
    return ""


__all__ = ["apply_llm_result_guard", "llm_guard_enabled"]
