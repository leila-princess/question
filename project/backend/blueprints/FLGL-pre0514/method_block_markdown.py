"""
将方法抽取中的「试验条件 / 试验过程 / 结果评定」结构化块转为单段 Markdown，供前端直接渲染，避免传整段 JSON。
结构：{ "text", "tables_html": [], "formulas_latex": [] } 与 extractor._normalize_text_block 一致。
"""
from __future__ import annotations

import html as html_mod
import re
from typing import Any, List

__all__ = ["text_block_to_markdown"]


def _strip_html_to_plain(html: str) -> str:
    if not (html and str(html).strip()):
        return ""
    s = str(html)
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</\s*(tr|h[1-6]|p|div|li|th|td)\s*>", " | ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\s*\|\s*", " | ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def text_block_to_markdown(block: Any) -> str:
    if block is None:
        return ""
    if isinstance(block, str):
        return block.strip()
    if not isinstance(block, dict):
        return str(block).strip()

    text = str(block.get("text") or "").strip()
    tables: List[str] = block.get("tables_html") or []
    formulas: List[str] = block.get("formulas_latex") or []
    if not isinstance(tables, list):
        tables = []
    if not isinstance(formulas, list):
        formulas = []

    parts: List[str] = []
    if text:
        parts.append(text)

    for i, raw in enumerate(formulas, 1):
        t = str(raw or "").strip()
        if not t:
            continue
        parts.append(f"**公式 {i}**（LaTeX）\n\n```latex\n{t}\n```")

    for i, raw in enumerate(tables, 1):
        h = str(raw or "").strip()
        if not h:
            continue
        plain = _strip_html_to_plain(h)
        if plain:
            parts.append(f"**表格 {i}**（自 HTML 提取为文本）\n\n{plain}")
        else:
            parts.append(f"**表格 {i}**（无可用文本，原 HTML 见下）\n\n```html\n{h[:12000]}{'…' if len(h) > 12000 else ''}\n```")

    return "\n\n".join(parts).strip()
