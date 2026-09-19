"""分片：优先卡在一级节标题；重叠仅作上下文；单片适配 16384 上下文。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..config import ANATOMY_CONTEXT_OVERLAP_LINES, get_anatomy_chunk_max_chars
from ..shared.line_index import LineIndex
from ..shared.llm_budget import anatomy_numbered_text_budget_chars


@dataclass
class AnatomyChunk:
    chunk_index: int
    content_start_line: int  # 本片参与合并的起始行（不含重叠上下文）
    content_end_line: int
    context_start_line: int  # LLM 输入起始行（含上下文）
    input_end_line: int
    hard_split: bool = False


def _span_raw_chars(line_index: LineIndex, start: int, end: int) -> int:
    return len(line_index.slice_lines(start, end))


def plan_anatomy_chunks(
    line_index: LineIndex,
    max_chars: int | None = None,
) -> List[AnatomyChunk]:
    """按一级节标题切分；单片过长时在行边界硬切。max_chars 为纯文本预算。"""
    budget = max_chars if max_chars is not None else get_anatomy_chunk_max_chars()
    n = line_index.line_count
    if n == 0:
        return []

    breaks = [1] + [ln for ln, _ in line_index.find_section_break_lines() if ln > 1]
    if breaks[-1] != n + 1:
        breaks.append(n + 1)

    raw_spans: List[Tuple[int, int]] = []
    for i in range(len(breaks) - 1):
        raw_spans.append((breaks[i], breaks[i + 1] - 1))

    merged: List[Tuple[int, int]] = []
    for s, e in raw_spans:
        if _span_raw_chars(line_index, s, e) <= budget:
            merged.append((s, e))
            continue
        pos = s
        while pos <= e:
            sub_end = pos
            while sub_end <= e:
                if _span_raw_chars(line_index, pos, sub_end) > budget and sub_end > pos:
                    sub_end -= 1
                    break
                if sub_end >= e:
                    break
                sub_end += 1
            merged.append((pos, min(sub_end, e)))
            pos = sub_end + 1

    chunks: List[AnatomyChunk] = []
    overlap = ANATOMY_CONTEXT_OVERLAP_LINES
    for idx, (cs, ce) in enumerate(merged):
        ctx_start = max(1, cs - overlap) if idx > 0 else cs
        chunks.append(
            AnatomyChunk(
                chunk_index=idx,
                content_start_line=cs,
                content_end_line=ce,
                context_start_line=ctx_start,
                input_end_line=ce,
                hard_split=ctx_start < cs,
            )
        )
    return chunks


def format_chunk_input(line_index: LineIndex, chunk: AnatomyChunk) -> str:
    """生成带行号输入；若含上下文后超长则逐步收缩上下文行。"""
    numbered_budget = anatomy_numbered_text_budget_chars()
    ctx_start = chunk.context_start_line
    content_start = chunk.content_start_line

    while ctx_start <= chunk.input_end_line:
        prefix = ""
        if ctx_start < content_start:
            prefix = (
                f"[CONTEXT 以下行仅供理解，勿输出 module 行号范围在 "
                f"{ctx_start}-{content_start - 1} 内]\n"
            )
        numbered = line_index.format_line_numbered(
            ctx_start,
            chunk.input_end_line,
            prefix_context=prefix,
        )
        if len(numbered) <= numbered_budget or ctx_start >= content_start:
            return numbered
        ctx_start += 1

    numbered = line_index.format_line_numbered(content_start, chunk.input_end_line)
    if len(numbered) > numbered_budget:
        return numbered[:numbered_budget]
    return numbered
