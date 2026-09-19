"""
将图库/Word 导入遗留的 HTML 片段转为 Markdown，便于前端 ReactMarkdown 渲染。
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List

from urllib.parse import quote, unquote

ENV_PROPERTY_LABELS = {
    "Type": "分类",
    "Class": "分级",
    "Level": "分级",
    "Combination": "典型组合",
    "Requirement": "要求",
    "Request": "要求",
    "Constraint": "约束",
    "Limit": "约束",
    "Name": "名称",
}

# 环境五要素：图库为 HTML，答复中保留 HTML 供前端格式化展示（非转 Markdown）
ENV_HTML_PROPERTY_KEYS = frozenset({"Type", "Class", "Combination", "Limit", "Request"})


def normalize_minio_image_url(src: str) -> str:
    """将各类 MinIO/代理图片地址统一为 /api/GLCX/JQCX/minio-image?path=…"""
    s = (src or "").strip()
    if not s:
        return s
    if s.startswith("/api/GLCX/JQCX/minio-image?"):
        return s
    malformed = re.search(r"path=(?:/minio-image&)?path=([^&\"']+)", s)
    if malformed:
        obj_path = unquote(malformed.group(1))
        return f"/api/GLCX/JQCX/minio-image?path={quote(obj_path, safe='')}"
    faxm = re.search(r"/faxm/([^?\"']+)", s)
    if faxm:
        obj_path = unquote(faxm.group(1))
        return f"/api/GLCX/JQCX/minio-image?path={quote(obj_path, safe='')}"
    if re.match(r"^环境/", s) or s.startswith("%E7%8E%AF%E5%A2%83"):
        obj_path = unquote(s.split("?")[0])
        return f"/api/GLCX/JQCX/minio-image?path={quote(obj_path, safe='')}"
    return s


def property_field_label(key: str) -> str:
    return ENV_PROPERTY_LABELS.get(key, key)


class _TableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self._row: List[str] = []
        self._cell_parts: List[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        if t in ("td", "th"):
            self._in_cell = True
            self._cell_parts = []
        elif t == "tr":
            self._row = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("td", "th"):
            self._in_cell = False
            text = re.sub(r"\s+", " ", "".join(self._cell_parts)).strip()
            self._row.append(text)
        elif t == "tr" and self._row:
            self.rows.append(self._row)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)


def _table_html_to_markdown(html: str) -> str:
    parser = _TableHtmlParser()
    try:
        parser.feed(html)
    except Exception:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()
    rows = parser.rows
    if not rows:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()
    ncol = max(len(r) for r in rows)
    norm = [list(r) + [""] * (ncol - len(r)) for r in rows]

    def esc(cell: str) -> str:
        return cell.replace("|", "\\|").replace("\n", " ").strip()

    header = norm[0]
    sep = ["---"] * ncol
    lines = [
        "| " + " | ".join(esc(c) for c in header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in norm[1:]:
        lines.append("| " + " | ".join(esc(c) for c in row) + " |")
    return "\n".join(lines)


def html_fragment_to_markdown(text: str) -> str:
    """HTML 图片/表格/路径 div → Markdown（GFM 表格与图片语法）。"""
    if not text or "<" not in text:
        return (text or "").strip()

    s = text

    def table_repl(match: re.Match) -> str:
        md = _table_html_to_markdown(match.group(0))
        return f"\n\n{md}\n\n" if md else ""

    s = re.sub(
        r"<table\b[^>]*>.*?</table>",
        table_repl,
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )

    img_block = re.compile(
        r"<div>\s*<img\b[^>]*?\bsrc=(['\"])([^'\"]+)\1[^>]*?"
        r"(?:\balt=(['\"])([^'\"]*?)\3)?[^>]*/?\s*>\s*</div>"
        r"(?:\s*<div[^>]*>\s*路径[:：]\s*([^<]+)\s*</div>)?",
        re.IGNORECASE | re.DOTALL,
    )

    def img_repl(match: re.Match) -> str:
        src = normalize_minio_image_url(match.group(2))
        alt = unescape(match.group(4) or "环境图片")
        path = unescape((match.group(5) or "").strip())
        lines = [f"![{alt}]({src})", "", f"[下载图片]({src})"]
        if path:
            lines.insert(1, f"> 存储路径：`{path}`")
        return "\n\n" + "\n".join(lines) + "\n\n"

    s = img_block.sub(img_repl, s)

    s = re.sub(
        r"<img\b[^>]*?\bsrc=(['\"])([^'\"]+)\1[^>]*?"
        r"(?:\balt=(['\"])([^'\"]*?)\3)?[^>]*/?\s*>",
        lambda m: (
            f"\n\n![{unescape(m.group(4) or '环境图片')}]({normalize_minio_image_url(m.group(2))})\n\n"
            f"[下载图片]({normalize_minio_image_url(m.group(2))})\n\n"
        ),
        s,
        flags=re.IGNORECASE,
    )

    s = re.sub(
        r"<div[^>]*>\s*路径[:：]\s*([^<]+)\s*</div>",
        lambda m: f"\n\n> 路径：`{unescape(m.group(1).strip())}`\n\n",
        s,
        flags=re.IGNORECASE,
    )

    s = re.sub(r"</?div\b[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def preserve_env_html_fragment(text: str) -> str:
    """保留环境五要素 HTML 结构，仅规范化图片地址。"""
    if not text:
        return ""
    s = unescape((text or "").strip())
    if "<" not in s:
        return s

    def _img_src_repl(m: re.Match) -> str:
        src = normalize_minio_image_url(m.group(3))
        return f"{m.group(1)}{m.group(2)}{src}{m.group(2)}"

    s = re.sub(
        r'(<img\b[^>]*\bsrc=)(["\'])([^"\']+)(\2)',
        _img_src_repl,
        s,
        flags=re.IGNORECASE,
    )
    return s.strip()


def looks_like_environment_properties_answer(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.search(r"环境[「『\"\"].+?[」』\"\"]\s*属性如下", t):
        return True
    if re.search(r"环境[「『\"\"].+?[」』\"\"]\s*的(?:分类|分级|典型组合|约束|要求)", t):
        return True
    if re.search(r"^###\s+", t, re.MULTILINE) and re.search(
        r"(?:分类|分级|典型组合|约束|要求)[：:]", t
    ):
        return True
    labels = "|".join(re.escape(v) for v in ENV_PROPERTY_LABELS.values() if v != "名称")
    if labels and len(re.findall(rf"(?:{labels})[：:]", t)) >= 2:
        return True
    return False


def normalize_env_properties_answer(text: str) -> str:
    """环境属性答复：保留 HTML，不做 html→markdown 剥离。"""
    t = (text or "").strip()
    if not t:
        return ""
    if "<" in t:
        return preserve_env_html_fragment(t)
    return t


def field_value_for_environment_property(field_key: str, raw: str) -> str:
    """五要素字段保留 HTML；名称/描述等仍转 Markdown。"""
    val = (raw or "").strip()
    if not val:
        return ""
    if field_key in ENV_HTML_PROPERTY_KEYS:
        if "<" in val:
            return preserve_env_html_fragment(val)
        return val
    return html_fragment_to_markdown(val) if "<" in val else val


def normalize_answer_markdown(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if "<" not in t:
        return t
    if looks_like_environment_properties_answer(t):
        return normalize_env_properties_answer(t)
    return html_fragment_to_markdown(t)


def append_env_reference_media(answer: str, neo_rows: List[Dict[str, Any]]) -> str:
    """若 LLM 未保留环境示意图，从图谱属性 HTML 补回 Markdown 图片。"""
    if not neo_rows or "![环境图片]" in (answer or ""):
        return (answer or "").strip()

    def _props_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
        for side in ("n", "m"):
            node = row.get(side)
            if not isinstance(node, dict):
                continue
            labs = node.get("labels") if isinstance(node.get("labels"), list) else []
            if "Environment" in labs:
                props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
                return props if props else dict(node)
        node = row.get("n")
        if isinstance(node, dict):
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            return props if props else dict(node)
        return {}

    blocks: List[str] = []
    seen: set = set()
    for row in neo_rows:
        if not isinstance(row, dict):
            continue
        props = _props_from_row(row)
        for fk in ("Type", "Class", "Combination", "Limit", "Request"):
            raw = str(props.get(fk) or "")
            if "<img" not in raw:
                continue
            md = html_fragment_to_markdown(raw)
            if md and md not in seen:
                seen.add(md)
                blocks.append(md)
    if not blocks:
        return (answer or "").strip()
    return (answer or "").rstrip() + "\n\n### 环境参考资料\n\n" + "\n\n".join(blocks[:3])
