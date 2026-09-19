"""
将方法抽取中的「试验条件 / 试验过程 / 结果评定」结构化块转为 HTML/Markdown。
结构：{ "text", "tables_html": [], "formulas_latex": [] } 与 extractor._normalize_text_block 一致。
"""
from __future__ import annotations

import html as html_mod
import re
from typing import Any, List, Optional, Tuple

__all__ = ["text_block_to_markdown", "text_block_to_html"]

_NUM_LIST_RE = re.compile(r"^(\d+)[\.、\)）]\s+")
_SUB_METHOD_MD_RE = re.compile(
    r"^\*\*[（(]?(\d+)[）)]?\s*(.+?)\*\*\s*$",
)
_PIPE_TABLE_ROW_RE = re.compile(r"^\s*\|(.+\|.+)\s*$|^\s*(.+?\|.+?\|.+)\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


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


def _apply_inline_markdown(text: str) -> str:
    """**粗体** → strong；保留已有 HTML。"""
    s = str(text or "")
    if "<" in s and ">" in s:
        return s
    escaped = html_mod.escape(s, quote=False)
    return _BOLD_RE.sub(r"<strong>\1</strong>", escaped)


def _split_pipe_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|") if c.strip() or s.count("|") >= 1]


def _is_pipe_table_row(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if s.count("|") >= 2:
        return True
    if _PIPE_TABLE_ROW_RE.match(s):
        return True
    return False


def _is_table_separator_row(cells: List[str]) -> bool:
    return bool(cells) and all(re.match(r"^:?-{2,}:?$", c.strip()) for c in cells if c.strip())


def _pipe_table_to_html(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    if len(rows) == 1:
        body_rows = rows
        header = None
    else:
        if _is_table_separator_row(rows[1]):
            header = rows[0]
            body_rows = rows[2:]
        else:
            header = rows[0]
            body_rows = rows[1:]

    parts = ['<div class="doc-table"><table border="1" cellpadding="4" cellspacing="0">']
    if header:
        parts.append("<thead><tr>")
        for cell in header:
            parts.append(f"<th>{_apply_inline_markdown(cell)}</th>")
        parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in body_rows:
        if not row or _is_table_separator_row(row):
            continue
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<td>{_apply_inline_markdown(cell)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _list_item_html(line: str) -> str:
    m = _NUM_LIST_RE.match(line.strip())
    if not m:
        return f"<li>{_apply_inline_markdown(line.strip())}</li>"
    body = line.strip()[m.end():].strip()
    return f"<li>{_apply_inline_markdown(body)}</li>"


def _plain_text_to_html(text: str) -> str:
    """
    将 LLM/Word 提取的纯文本或 Markdown 片段转为 WangEditor 可渲染的 HTML。
    统一委托 method_section_enrich._plain_text_to_method_html，避免 br/表格 处理分叉。
    """
    from blueprints.FLGL.method_section_enrich import _plain_text_to_method_html

    s = str(text or "").strip()
    if not s or s == "文档未明确说明":
        return ""
    if re.search(r"<\s*(p|div|table|ul|ol|li|figure|img|h[1-6]|strong)\b", s, re.I):
        if re.search(r"<\s*br\s*/?>", s, re.I) and not re.search(r"<\s*(p|div|table)\b", s, re.I):
            return _plain_text_to_method_html(s)
        return s
    return _plain_text_to_method_html(s)


def _extract_tables_from_html(html: str) -> List[str]:
    return [t.strip() for t in re.findall(r"<table[\s\S]*?</table>", html or "", re.I) if t.strip()]


def _dedupe_block_tables(text: str, tables: List[str]) -> List[str]:
    """去掉正文中已内嵌的表格，避免重复渲染。"""
    if not text or not tables:
        return list(tables or [])
    embedded = _extract_tables_from_html(text)
    if not embedded:
        return list(tables)
    out: List[str] = []
    for raw in tables:
        h = str(raw or "").strip()
        if not h:
            continue
        if h in text or any(h in e or e in h for e in embedded):
            continue
        out.append(h)
    return out


def text_block_to_html(block: Any) -> str:
    """将结构化块转为 HTML，保留 tables_html 原始表格结构。"""
    if block is None:
        return ""
    if isinstance(block, str):
        return _plain_text_to_html(block)
    if not isinstance(block, dict):
        return _plain_text_to_html(str(block))

    text = str(block.get("text") or "").strip()
    tables: List[str] = block.get("tables_html") or []
    formulas: List[str] = block.get("formulas_latex") or []
    if not isinstance(tables, list):
        tables = []
    if not isinstance(formulas, list):
        formulas = []

    tables = _dedupe_block_tables(text, tables)

    parts: List[str] = []
    if text:
        parts.append(_plain_text_to_html(text))
    for i, raw in enumerate(tables, 1):
        h = str(raw or "").strip()
        if not h:
            continue
        if "<table" in h.lower():
            wrapped = h if 'class="doc-table"' in h else f'<div class="doc-table">{h}</div>'
            parts.append(wrapped)
        else:
            parts.append(f"<p><strong>表格 {i}</strong></p>{_plain_text_to_html(_strip_html_to_plain(h))}")
    for i, raw in enumerate(formulas, 1):
        t = str(raw or "").strip()
        if t:
            parts.append(f"<pre><code>{html_mod.escape(t)}</code></pre>")
    return "".join(parts).strip()


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

    tables = _dedupe_block_tables(text, tables)

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
        # 保留原始 HTML 表格，主流 Markdown 渲染器均支持嵌入式 HTML 表格
        if "<table" in h.lower():
            parts.append(h)
        else:
            plain = _strip_html_to_plain(h)
            if plain:
                parts.append(f"**表格 {i}**（自 HTML 提取为文本）\n\n{plain}")
            else:
                parts.append(f"**表格 {i}**（无可用文本，原 HTML 见下）\n\n```html\n{h[:12000]}{'…' if len(h) > 12000 else ''}\n```")

    return "\n\n".join(parts).strip()
