"""LLM 解剖师 + 校验。"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from ..shared.line_index import LineIndex
from ..shared.schemas import SemanticModule
from .prompts import ANATOMIST_SYSTEM_PROMPT, ANATOMIST_USER_TEMPLATE

logger = logging.getLogger(__name__)

VALID_TYPES = frozenset({"METHOD", "CONDITION", "EQUIPMENT", "CRITERIA", "OTHER"})

_TITLE_BOOST = [
    (re.compile(r"试验步骤|试验方法|操作流程|测试过程"), "METHOD"),
    (re.compile(r"试验设备|仪器设备|试验装置"), "EQUIPMENT"),
    (re.compile(r"结果评判|结果评定|失效判定"), "CRITERIA"),
    (re.compile(r"试验条件|环境条件|样品状态"), "CONDITION"),
]


class AnatomistError(Exception):
    pass


def _call_llm_json(system: str, user: str, *, temperature: float = 0.1) -> Dict[str, Any]:
    from blueprints.FLGL.GetTestMethod.extractor import _get_runtime_client, _clamp_completion_tokens
    from blueprints.FLGL.GetTestMethod.extractor import _safe_json_loads

    client, model = _get_runtime_client()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    prompt_len = len(system) + len(user)
    mt = _clamp_completion_tokens(user, 4096)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=mt,
    )
    choice = resp.choices[0] if resp and resp.choices else None
    content = (choice.message.content or "").strip() if choice and choice.message else ""
    logger.info("[anatomist] LLM response len=%d prompt_len=%d", len(content), prompt_len)
    result = _safe_json_loads(content or "", {})
    if not isinstance(result, dict):
        raise AnatomistError(f"LLM 返回非 JSON 对象: {content[:200]}")
    return result


def _boost_type_from_title(title: str, line_text: str) -> Optional[str]:
    combined = f"{title} {line_text}"
    for pat, typ in _TITLE_BOOST:
        if pat.search(combined):
            return typ
    return None


def validate_modules(
    raw_sections: List[Dict[str, Any]],
    line_index: LineIndex,
    *,
    chunk_index: int = 0,
    content_start_line: int = 1,
    id_offset: int = 0,
) -> List[SemanticModule]:
    """校验并规范化 modules；仅保留 content 区内行号。"""
    out: List[SemanticModule] = []
    seen_ranges: List[tuple] = []
    mid = id_offset
    for sec in raw_sections:
        if not isinstance(sec, dict):
            continue
        try:
            sl = int(sec.get("start_line") or 0)
            el = int(sec.get("end_line") or 0)
        except (TypeError, ValueError):
            continue
        if not line_index.validate_range(sl, el):
            continue
        if el < content_start_line:
            continue
        sl = max(sl, content_start_line)
        typ = str(sec.get("type") or "OTHER").upper()
        if typ not in VALID_TYPES:
            typ = "OTHER"
        title = str(sec.get("title") or "").strip() or "片段"
        line_txt = line_index.slice_lines(sl, el)
        boost = _boost_type_from_title(title, line_txt)
        conf = float(sec.get("confidence") or 0.75)
        if boost and boost != typ:
            if conf < 0.85:
                typ = boost
                conf = max(conf, 0.8)
        if typ == "OTHER":
            continue
        text = line_txt.strip()
        if len(text) < 2:
            continue
        mid += 1
        mod: SemanticModule = {
            "module_id": f"m{mid}",
            "type": typ,
            "title": title,
            "start_line": sl,
            "end_line": el,
            "confidence": conf,
            "chunk_index": chunk_index,
            "context_only": sl < content_start_line,
        }
        ic = sec.get("inline_criteria")
        if ic:
            mod["inline_criteria"] = str(ic).strip()
        out.append(mod)
        seen_ranges.append((sl, el))
    return out


def analyze_chunk(
    line_index: LineIndex,
    numbered_text: str,
    *,
    chunk_index: int = 0,
    content_start_line: int = 1,
    id_offset: int = 0,
) -> List[SemanticModule]:
    user = ANATOMIST_USER_TEMPLATE.format(text=numbered_text)
    from ..shared.llm_budget import (
        anatomy_numbered_text_budget_chars,
        ensure_llm_context_config,
        fit_text_to_char_budget,
    )

    ensure_llm_context_config()
    numbered_budget = anatomy_numbered_text_budget_chars()
    if len(numbered_text) > numbered_budget:
        logger.warning(
            "[anatomist] 切片带行号正文 %d 字超过预算 %d，截断",
            len(numbered_text),
            numbered_budget,
        )
        numbered_text = fit_text_to_char_budget(numbered_text, numbered_budget)
        user = ANATOMIST_USER_TEMPLATE.format(text=numbered_text)

    data = _call_llm_json(ANATOMIST_SYSTEM_PROMPT, user)
    sections = data.get("sections") or []
    if not isinstance(sections, list):
        raise AnatomistError("sections 不是数组")
    modules = validate_modules(
        sections,
        line_index,
        chunk_index=chunk_index,
        content_start_line=content_start_line,
        id_offset=id_offset,
    )
    # 表格补全
    modules = _supplement_table_methods(modules, line_index, content_start_line, id_offset=len(modules))
    return modules


def _supplement_table_methods(
    modules: List[SemanticModule],
    line_index: LineIndex,
    content_start: int,
    id_offset: int,
) -> List[SemanticModule]:
    covered = set()
    for m in modules:
        for ln in range(int(m["start_line"]), int(m["end_line"]) + 1):
            covered.add(ln)
    out = list(modules)
    mid = id_offset
    for i in range(content_start, line_index.line_count + 1):
        ln = line_index.line_text(i).strip()
        if "【表格】" in ln or re.match(r"^表\s*\d+", ln):
            end = i
            while end <= line_index.line_count:
                nxt = line_index.line_text(end + 1).strip() if end < line_index.line_count else ""
                if not nxt or re.match(r"^\d+(?:\.\d+)*\s+[\u4e00-\u9fff]", nxt):
                    break
                end += 1
            if any(j in covered for j in range(i, end + 1)):
                continue
            mid += 1
            out.append({
                "module_id": f"m{mid}",
                "type": "METHOD",
                "title": ln[:20] or "表格步骤",
                "start_line": i,
                "end_line": end,
                "confidence": 0.6,
                "chunk_index": 0,
            })
    return out
