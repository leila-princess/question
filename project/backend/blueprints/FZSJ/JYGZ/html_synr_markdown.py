"""方法 SYNR 字段：HTML → Markdown（供 JYGZ 方案模板嵌入）。"""
from __future__ import annotations

import html as html_mod
import re
from typing import List

_HTML_TAG_RE = re.compile(r"<[a-z][a-z0-9]*\b", re.I)


def _looks_like_html(s: str) -> bool:
    return bool(_HTML_TAG_RE.search(str(s or "")))


def _inline_children(node) -> str:
    from bs4 import NavigableString, Tag

    parts: List[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            text = html_mod.unescape(str(child))
            text = text.replace("\xa0", " ")
            if text:
                parts.append(text)
        elif isinstance(child, Tag):
            parts.append(_tag_to_md(child, in_table=False))
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _table_to_md(table) -> str:
    rows: List[List[str]] = []
    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"]):
            cells.append(_inline_children(cell).replace("|", "\\|").replace("\n", " "))
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    max_cols = max(len(r) for r in rows)
    lines = []
    header = rows[0] + [""] * (max_cols - len(rows[0]))
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in range(max_cols)) + " |")
    for row in rows[1:]:
        padded = row + [""] * (max_cols - len(row))
        lines.append("| " + " | ".join(padded) + " |")
    return "\n" + "\n".join(lines) + "\n\n"


def _tag_to_md(tag, *, in_table: bool = False) -> str:
    name = (tag.name or "").lower()
    if name == "br":
        return "\n"
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(name[1])
        inner = _inline_children(tag)
        return f"\n{'#' * (level + 2)} {inner}\n\n" if inner else ""
    if name == "p":
        inner = _inline_children(tag)
        return f"{inner}\n\n" if inner else ""
    if name == "li":
        inner = _inline_children(tag)
        return f"- {inner}\n" if inner else ""
    if name in ("ul", "ol"):
        return "".join(_tag_to_md(li, in_table=in_table) for li in tag.find_all("li", recursive=False))
    if name == "table" and not in_table:
        return _table_to_md(tag)
    if name == "img":
        src = (tag.get("src") or "").strip()
        alt = (tag.get("alt") or "图").strip()
        return f"\n![{alt}]({src})\n\n" if src else ""
    if name in ("strong", "b"):
        inner = _inline_children(tag)
        return f"**{inner}**" if inner else ""
    if name in ("em", "i"):
        inner = _inline_children(tag)
        return f"*{inner}*" if inner else ""
    if name in ("body", "div", "section", "article", "span"):
        parts: List[str] = []
        for child in tag.children:
            if getattr(child, "name", None):
                parts.append(_tag_to_md(child, in_table=in_table))
            else:
                text = html_mod.unescape(str(child)).strip()
                if text:
                    parts.append(text)
        return "".join(parts)
    return _inline_children(tag)


def synr_html_to_markdown(html: str) -> str:
    """将方法库中的 SYNR HTML 转为 Markdown；已是纯文本/Markdown 时原样返回。"""
    raw = str(html or "").strip()
    if not raw:
        return ""
    if not _looks_like_html(raw):
        return raw

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _fallback_strip_html(raw)

    soup = BeautifulSoup(raw, "html.parser")
    body = soup.body if soup.body else soup
    chunks: List[str] = []
    for child in body.children:
        if not getattr(child, "name", None):
            text = html_mod.unescape(str(child)).strip()
            if text:
                chunks.append(text)
            continue
        chunks.append(_tag_to_md(child))
    out = "".join(chunks)
    out = html_mod.unescape(out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


def _fallback_strip_html(html: str) -> str:
    s = str(html or "")
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</\s*p\s*>", "\n\n", s)
    s = re.sub(r"(?i)<\s*p[^>]*>", "", s)
    s = re.sub(r"(?i)</\s*h[1-6][^>]*>", "\n\n", s)
    s = re.sub(r"(?i)<\s*h([1-6])[^>]*>", lambda m: "\n" + "#" * (int(m.group(1)) + 2) + " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()
