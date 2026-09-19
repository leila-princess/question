"""根据规划器解路径撰写试验方案 Markdown（支持流式 SSE）。"""
from __future__ import annotations

import re
from typing import Any, Dict, Generator, List, Tuple

from blueprints.FZSJ.JYLLM.multi_agent_engine import call_llm, call_llm_stream

from .prompts import SCHEME_WRITER_SYSTEM, build_scheme_writer_prompt

SchemeEvent = Tuple[str, Dict[str, Any]]


def iter_write_scheme_from_plan(
    user_description: str,
    plan_trace: List[Dict[str, Any]],
    branch_notes: str,
    chroma_context: str,
    *,
    llm_model_key: str = "",
) -> Generator[SchemeEvent, None, str]:
    plan_summary = branch_notes or "规划器已根据 PDDL 前提/效果自动排序。"
    messages = [
        {"role": "system", "content": SCHEME_WRITER_SYSTEM},
        {
            "role": "user",
            "content": build_scheme_writer_prompt(
                user_description, plan_summary, plan_trace, branch_notes, chroma_context
            ),
        },
    ]

    yield (
        "scheme_start",
        {
            "chapters": [
                {"index": 1, "title": "1. 试验依据"},
                {"index": 2, "title": "2. 试验内容及方法"},
            ],
        },
    )

    parts: list[str] = []
    for piece in call_llm_stream(
        messages,
        temperature=0.45,
        max_tokens=8192,
        llm_model_key=llm_model_key,
    ):
        if isinstance(piece, str) and piece:
            parts.append(piece)
            yield ("scheme_delta", {"text": piece})

    md = "".join(parts).strip()
    if not md:
        md = (
            call_llm(
                messages,
                temperature=0.45,
                max_tokens=8192,
                llm_model_key=llm_model_key,
            )
            or ""
        ).strip()

    yield ("scheme_done", {"content": md, "chars": len(md)})
    return md


def write_scheme_from_plan(
    user_description: str,
    plan_trace: List[Dict[str, Any]],
    branch_notes: str,
    chroma_context: str,
    *,
    llm_model_key: str = "",
) -> str:
    gen = iter_write_scheme_from_plan(
        user_description,
        plan_trace,
        branch_notes,
        chroma_context,
        llm_model_key=llm_model_key,
    )
    md = ""
    try:
        while True:
            next(gen)
    except StopIteration as si:
        md = si.value or ""
    return md.strip()


def split_scheme_markdown(md: str) -> List[Dict[str, Any]]:
    """将流式累积的 Markdown 拆为章节（供前端增量更新）。"""
    chs: List[Dict[str, Any]] = []
    for num in (1, 2):
        m = re.search(rf"(##\s*{num}\.[\s\S]*?)(?=##\s*{num + 1}\.|$)", md)
        if m:
            chs.append({"index": num, "title": f"{num}.", "content": m.group(1).strip()})
    if not chs and md.strip():
        chs = [{"index": 1, "title": "试验方案", "content": md.strip()}]
    return chs
