"""
试验方法节点 SYNR（HTML 试验内容）→ Markdown，并按 ImageUrls / MinIO「试验方法」前缀解析插图。
LLM 生成路径不使用本模块；仅规则填充（图库有实质 SYNR）时调用。
"""
from __future__ import annotations

import json
import logging
import re
from html import unescape
from typing import Any, List, Optional
from urllib.parse import quote, unquote as url_unquote

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_MD_HEADING_NUM_RE = re.compile(r"^(\s{0,3}#{1,6}\s+)(\d+(?:\.\d+)*)([\.、]?)\s+(.+)$")
_MD_PLAIN_NUM_RE = re.compile(r"^(\s*)(\d+(?:\.\d+)*)([\.、]?)\s+(.+)$")
_MD_CN_ORDINAL_RE = re.compile(r"^(\s*)[（(]([一二三四五六七八九十]{1,3})[）)]\s*(.+)$")
_MD_CN_PAREN_DIGIT_RE = re.compile(r"^(\s*)[（(](\d+)[）)]\s*(.+)$")
_MD_BOLD_NUM_RE = re.compile(r"^(\s*)\*\*(\d+(?:\.\d+)*)([\.、]?)\s+(.+?)\*\*\s*$")
_MD_BOLD_CN_ORDINAL_RE = re.compile(
    r"^(\s*)\*\*[（(]([一二三四五六七八九十]{1,3})[）)]\s*(.+?)\*\*\s*$"
)
_HTML_HEADING_NUM_RE = re.compile(
    r"(<h[1-6][^>]*>\s*)(\d+(?:\.\d+)*)([\.、]?)\s+([^<].*?</h[1-6]>)",
    re.I | re.S,
)
_HTML_STRONG_NUM_RE = re.compile(
    r"(<p[^>]*>\s*<strong>\s*)(\d+(?:\.\d+)*)([\.、]?)\s+([^<].*?</strong>\s*</p>)",
    re.I | re.S,
)
_HTML_WJGL_SECTION_NO_RE = re.compile(
    r'(data-section-no=")(\d+(?:\.\d+)*)(")',
    re.I,
)
_HTML_STRONG_INLINE_NUM_RE = re.compile(
    r"(<strong>\s*)(\d+(?:\.\d+)*)([\.、]?)\s+",
    re.I,
)


def _parse_image_urls_field(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    s = str(val).strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            j = json.loads(s)
            if isinstance(j, list):
                return [str(x).strip() for x in j if str(x).strip()]
        except (json.JSONDecodeError, TypeError):
            pass
    return [p.strip() for p in re.split(r"[,;\n|]", s) if p.strip()]


def _plain_len_from_html(html: str) -> int:
    if not (html or "").strip():
        return 0
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return len(soup.get_text(separator=" ", strip=True))
    except Exception:
        return len(re.sub(r"<[^>]+>", " ", html))


def synr_html_is_substantial(html: str, min_plain: int = 40) -> bool:
    return _plain_len_from_html(html) >= min_plain


def _minio_path_from_img_src(src: str) -> Optional[str]:
    """从 img src 提取 MinIO 对象键（试验方法/…），兼容 FLGL method-image 代理与直链。"""
    s = unescape((src or "").strip())
    if not s or s.startswith("data:"):
        return None
    if "method-image?" in s and "path=" in s:
        path = s.split("path=", 1)[1].split("&", 1)[0]
        path = url_unquote(path).replace("\\", "/").strip().lstrip("/")
        if path.startswith("试验方法/"):
            return path
    if s.startswith("试验方法/"):
        return s.replace("\\", "/").strip().lstrip("/")
    if s.startswith(("http://", "https://")):
        if "path=" in s:
            path = s.split("path=", 1)[1].split("&", 1)[0]
            path = url_unquote(path).replace("\\", "/").strip().lstrip("/")
            if path.startswith("试验方法/"):
                return path
        return None
    return None


def _file_minio_paths(files_raw: Any) -> List[str]:
    paths: List[str] = []
    if not isinstance(files_raw, list):
        return paths
    for f in files_raw:
        if not f or not isinstance(f, dict):
            continue
        for key in ("minio_id", "minIo_id", "MinIo_id", "MinIO_id", "minio_path", "path"):
            val = f.get(key)
            if val:
                p = str(val).replace("\\", "/").strip().lstrip("/")
                if p and p not in paths:
                    paths.append(p)
    return paths


def collect_method_image_object_keys(
    synr_html: str,
    method_id: str,
    image_urls_raw: Any = None,
    files_raw: Any = None,
) -> List[str]:
    """合并显式 ImageUrls、SYNR 内 img、关联 File 节点的 minIo_id。"""
    seen: set[str] = set()
    out: List[str] = []
    for p in _parse_image_urls_field(image_urls_raw):
        norm = p.replace("\\", "/").strip().lstrip("/")
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    for p in _file_minio_paths(files_raw):
        if p not in seen:
            seen.add(p)
            out.append(p)
    if synr_html:
        for src in re.findall(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", str(synr_html), flags=re.I):
            mp = _minio_path_from_img_src(src)
            if mp and mp not in seen:
                seen.add(mp)
                out.append(mp)
    return out


_TABLE_ROW_RE = re.compile(r"^\s*\|")
_TABLE_SEP_RE = re.compile(r"^\|\s*([-:\s]+)(\|\s*([-:\s]+))+\|\s*$")


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW_RE.match(line or ""))


def _is_table_separator_row(line: str) -> bool:
    t = (line or "").strip()
    if not t.startswith("|"):
        return False
    return bool(_TABLE_SEP_RE.match(t)) or all(
        re.match(r"^[-:\s]+$", c.strip()) for c in t.strip("|").split("|") if c.strip()
    )


def _table_col_count(row: str) -> int:
    return max(1, (row or "").strip().count("|") - 1)


def _ensure_gfm_table_block(block: List[str]) -> List[str]:
    if not block:
        return block
    if len(block) >= 2 and _is_table_separator_row(block[1]):
        return block
    n_cols = _table_col_count(block[0])
    sep = "| " + " | ".join(["---"] * n_cols) + " |"
    return [block[0], sep] + block[1:]


def _split_collapsed_table_line(line: str) -> List[str]:
    """一行内多行 pipe 表格：| a | b | | 1 | → 拆成多行。"""
    if line.count("|") < 4:
        return [line]
    fixed = re.sub(
        r"\|\s*\|(?=\s*(?:\d+(?:\s*\|)|序号\b))",
        "|\n|",
        line,
    )
    if "\n" not in fixed:
        return [line]
    return [ln for ln in fixed.split("\n") if ln.strip()]


def repair_markdown_tables(text: str) -> str:
    """拆开挤在一行的 GFM 表格，并补齐表头分隔行（remark-gfm 解析必需）。"""
    if not text or "|" not in text:
        return text
    raw_lines = text.splitlines()
    expanded: List[str] = []
    for line in raw_lines:
        if "|" in line and line.count("|") >= 4 and _split_collapsed_table_line(line) != [line]:
            expanded.extend(_split_collapsed_table_line(line))
        else:
            expanded.append(line)

    result: List[str] = []
    i = 0
    while i < len(expanded):
        line = expanded[i]
        if _is_table_row(line):
            block = [line]
            i += 1
            while i < len(expanded) and _is_table_row(expanded[i]):
                block.append(expanded[i])
                i += 1
            result.extend(_ensure_gfm_table_block(block))
        else:
            result.append(line)
            i += 1
    return "\n".join(result)


def _looks_like_pipe_table_plain(text: str) -> bool:
    t = (text or "").strip()
    return t.count("|") >= 4 and ("序号" in t or re.search(r"\|\s*\d+\s*\|", t))


def normalize_latex_in_text(text: str) -> str:
    """将混排文本中的 $...$ / $$...$$ 内 OCR 垃圾 LaTeX 规范化（展示用）。"""
    if not text or "$" not in text:
        return text

    def _norm_inline(m: re.Match) -> str:
        inner = m.group(1)
        return f"${_latex_normalize_segment(inner)}$"

    def _norm_block(m: re.Match) -> str:
        inner = m.group(1)
        return f"$${_latex_normalize_segment(inner)}$$"

    out = re.sub(r"\$\$([\s\S]*?)\$\$", _norm_block, text)
    out = re.sub(r"\$([^$\n]+?)\$", _norm_inline, out)
    return out


def _latex_normalize_segment(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    s = re.sub(r"\\mathrm\s*\{\s*~\s*\}", "", s, flags=re.I)
    s = re.sub(r"\\text\s*\{\s*~\s*\}", "", s, flags=re.I)
    s = re.sub(r"\{\s*~\s*\}", "", s)
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    s = re.sub(r"\^\s*\{\s*\\circ\s*\}", lambda _m: "^\\circ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _pick_object_for_src(src: str, method_id: str, image_urls: List[str]) -> Optional[str]:
    """
    将 HTML img src 解析为 MinIO 对象键（须在白名单 image_urls 内，或以 试验方法/{method_id}/ 为前缀）。
    """
    s = unescape((src or "").strip())
    if not s or s.startswith("data:"):
        return None
    minio_from_src = _minio_path_from_img_src(s)
    if minio_from_src:
        normed_urls = [u.replace("\\", "/").strip().lstrip("/") for u in image_urls if u]
        if minio_from_src in normed_urls or any(
            u.endswith(minio_from_src) or minio_from_src.endswith(u) for u in normed_urls
        ):
            return minio_from_src
        if minio_from_src.startswith("试验方法/"):
            return minio_from_src
    if s.startswith(("http://", "https://")):
        # 已是绝对 URL：不强行改写到代理（避免外链丢失）
        return None

    rel = s.split("?", 1)[0].strip()
    rel = rel.lstrip("/").replace("\\", "/")

    def norm(p: str) -> str:
        return p.replace("\\", "/").strip().lstrip("/")

    normed_urls = [norm(u) for u in image_urls if u]

    base = rel.split("/")[-1] if "/" in rel else rel
    for u in normed_urls:
        if not u:
            continue
        if rel == u or u.endswith("/" + rel) or u.endswith(rel) or base and u.endswith(base):
            return u

    prefix = f"试验方法/{method_id.strip('/')}/"
    candidate = prefix + rel.lstrip("/")
    for u in normed_urls:
        if u == candidate or u.endswith(rel):
            return u
    if candidate.replace("\\", "/").startswith("试验方法/"):
        return candidate.replace("\\", "/")
    return None


def _img_proxy_markdown(method_id: str, object_key: str) -> str:
    """相对本站代理路径，由 Flask 从 MinIO 拉取（object 为完整对象键）。"""
    key = object_key.replace("\\", "/").strip().lstrip("/")
    return f"/api/fzsj/jyllm/method_asset/{quote(str(method_id), safe='')}?object={quote(key, safe='')}"


def synr_html_to_markdown(
    html: str,
    method_id: str,
    image_urls_raw: Any = None,
    files_raw: Any = None,
) -> str:
    """
    HTML → 简易 Markdown：标题/段落/列表/表格粗转换；img 尽量换为本地代理 URL。
    """
    if not (html or "").strip():
        return ""
    image_urls = collect_method_image_object_keys(html, method_id, image_urls_raw, files_raw)
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        logger.warning("synr BeautifulSoup parse failed: %s", e)
        return re.sub(r"<[^>]+>", "\n", unescape(html)).strip()

    for tag in soup(["script", "style"]):
        tag.decompose()

    for img in soup.find_all("img"):
        src = img.get("src") or ""
        alt = (img.get("alt") or "").strip() or "图"
        obj = _pick_object_for_src(src, method_id, image_urls)
        if obj and not src.startswith(("http://", "https://")):
            url = _img_proxy_markdown(method_id, obj)
            img.replace_with(f"\n\n![{alt}]({url})\n\n")
        elif src.startswith(("http://", "https://")):
            img.replace_with(f"\n\n![{alt}]({src})\n\n")
        else:
            img.replace_with(f"\n\n（插图：{alt}，路径未在 ImageUrls 中解析：{src}）\n\n")

    for br in soup.find_all("br"):
        br.replace_with("\n")

    out_chunks: List[str] = []

    def emit_block(el) -> None:
        if not getattr(el, "name", None):
            return
        name = el.name.lower()
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(name[1])
            t = el.get_text(separator=" ", strip=True)
            if t:
                out_chunks.append("#" * min(level + 1, 6) + " " + t + "\n\n")
        elif name == "p":
            t = el.get_text(separator=" ", strip=True)
            if t:
                if _looks_like_pipe_table_plain(t):
                    out_chunks.append(repair_markdown_tables(t) + "\n\n")
                else:
                    out_chunks.append(t + "\n\n")
        elif name in ("ul", "ol"):
            for li in el.find_all("li", recursive=False):
                lt = li.get_text(separator=" ", strip=True)
                if lt:
                    out_chunks.append("- " + lt + "\n")
            out_chunks.append("\n")
        elif name == "table":
            rows = el.find_all("tr")
            if not rows:
                return
            table_rows: List[List[str]] = []
            for tr in rows[:40]:
                cells = tr.find_all(["th", "td"])
                if not cells:
                    continue
                table_rows.append([c.get_text(separator=" ", strip=True) for c in cells])
            if table_rows:
                max_cols = max(len(r) for r in table_rows)
                lines: List[str] = []
                header = table_rows[0] + [""] * (max_cols - len(table_rows[0]))
                lines.append("| " + " | ".join(header) + " |")
                lines.append("| " + " | ".join("---" for _ in range(max_cols)) + " |")
                for row in table_rows[1:]:
                    padded = row + [""] * (max_cols - len(row))
                    lines.append("| " + " | ".join(padded) + " |")
                out_chunks.append("\n".join(lines) + "\n\n")
        elif name == "div":
            for sub in el.children:
                if getattr(sub, "name", None):
                    emit_block(sub)
                elif isinstance(sub, str) and sub.strip():
                    out_chunks.append(sub.strip() + "\n\n")

    root = soup.body if soup.body else soup
    for child in root.children:
        if getattr(child, "name", None):
            emit_block(child)
        elif isinstance(child, str) and child.strip():
            out_chunks.append(child.strip() + "\n\n")

    if not out_chunks:
        return soup.get_text(separator="\n\n", strip=True)

    text = "\n".join(out_chunks)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    text = repair_markdown_tables(text)
    return normalize_latex_in_text(text)


def _cn_numeral_to_int(token: str) -> Optional[int]:
    if not token:
        return None
    single = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    if token in single:
        return single[token]
    if token.startswith("十") and len(token) == 2 and token[1] in single:
        return 10 + single[token[1]]
    if token.endswith("十") and len(token) == 2 and token[0] in single:
        return single[token[0]] * 10
    if "十" in token and len(token) == 3 and token[0] in single and token[2] in single:
        return single[token[0]] * 10 + single[token[2]]
    return None


def _prefix_number(num: str, parent_prefix: str) -> str:
    base = (parent_prefix or "").strip().strip(".")
    cur = (num or "").strip().strip(".")
    if not base or not cur:
        return cur or num
    if cur == base or cur.startswith(base + "."):
        return cur
    return f"{base}.{cur}"


def _looks_like_numbered_heading(title: str, indent_len: int = 0) -> bool:
    """区分小节标题与正文中的编号列表项。"""
    t = (title or "").strip()
    if not t:
        return False
    if indent_len > 12:
        return False
    if len(t) <= 120:
        return True
    if len(t) > 200:
        return False
    # 长句且以句号结尾，更像正文列表而非标题
    if t.endswith(("。", "；", ".", ";")):
        return False
    return len(t) <= 160


def apply_method_heading_prefix(method_body: str, parent_prefix: str) -> str:
    """
    将方法正文中的标题编号挂接到父级编号下（幂等）。
    支持 Markdown 与常见 HTML 标题格式；子级标题沿最近上级小节继续向下编号。
    """
    text = str(method_body or "")
    base = (parent_prefix or "").strip().strip(".")
    if not text.strip() or not base:
        return text

    last_section_prefix = base
    out_lines: List[str] = []

    def _emit_numbered(indent: str, full_no: str, punct: str, title: str, *, bold: bool = False) -> str:
        nonlocal last_section_prefix
        last_section_prefix = full_no.strip().strip(".")
        p = punct or ""
        sep = " " if title else ""
        body = f"{full_no}{p}{sep}{title}".rstrip()
        if bold:
            return f"{indent}**{body}**"
        return f"{indent}{body}"

    for line in text.splitlines():
        m = _MD_HEADING_NUM_RE.match(line)
        if m:
            lead, no, punct, title = m.groups()
            full = _prefix_number(no, base)
            last_section_prefix = full
            out_lines.append(f"{lead}{full}{punct} {title}")
            continue
        m = _MD_BOLD_NUM_RE.match(line)
        if m:
            indent, no, punct, title = m.groups()
            out_lines.append(_emit_numbered(indent, _prefix_number(no, base), punct, title, bold=True))
            continue
        m = _MD_BOLD_CN_ORDINAL_RE.match(line)
        if m:
            indent, cn, title = m.groups()
            idx = _cn_numeral_to_int(cn)
            if idx is not None:
                out_lines.append(_emit_numbered(indent, f"{base}.{idx}", "", title, bold=True))
                continue
        m = _MD_CN_ORDINAL_RE.match(line)
        if m:
            indent, cn, title = m.groups()
            idx = _cn_numeral_to_int(cn)
            if idx is not None:
                out_lines.append(_emit_numbered(indent, f"{base}.{idx}", "", title))
                continue
        m = _MD_CN_PAREN_DIGIT_RE.match(line)
        if m:
            indent, digit, title = m.groups()
            if _looks_like_numbered_heading(title, len(indent)):
                parent = last_section_prefix or base
                out_lines.append(_emit_numbered(indent, f"{parent}.{digit}", "", title))
                continue
        m = _MD_PLAIN_NUM_RE.match(line)
        if m:
            indent, no, punct, title = m.groups()
            if _looks_like_numbered_heading(title, len(indent)):
                out_lines.append(_emit_numbered(indent, _prefix_number(no, base), punct, title))
                continue
        out_lines.append(line)
    out = "\n".join(out_lines)

    def _html_num_repl(m: re.Match) -> str:
        return f"{m.group(1)}{_prefix_number(m.group(2), base)}{m.group(3)} {m.group(4)}"

    def _html_wjgl_section_repl(m: re.Match) -> str:
        return f'{m.group(1)}{_prefix_number(m.group(2), base)}{m.group(3)}'

    def _html_strong_inline_repl(m: re.Match) -> str:
        return f"{m.group(1)}{_prefix_number(m.group(2), base)}{m.group(3)} "

    out = _HTML_WJGL_SECTION_NO_RE.sub(_html_wjgl_section_repl, out)
    out = _HTML_HEADING_NUM_RE.sub(_html_num_repl, out)
    out = _HTML_STRONG_NUM_RE.sub(_html_num_repl, out)
    out = _HTML_STRONG_INLINE_NUM_RE.sub(_html_strong_inline_repl, out)
    return out


def strip_redundant_method_name_heading(method_body: str, method_name: str) -> str:
    """去掉方法正文开头与方法节点同名的重复标题行（方案中不再单独展示方法名）。"""
    text = str(method_body or "")
    name = str(method_name or "").strip()
    if not text.strip() or not name:
        return text
    lines = text.splitlines()
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return text
    first = lines[idx].strip()
    plain = re.sub(r"^#{1,6}\s+", "", first)
    plain = re.sub(r"^\*\*|\*\*$", "", plain).strip()
    plain = re.sub(r"^\d+(?:\.\d+)*[\.、]?\s+", "", plain).strip()
    plain = re.sub(r"^[（(][一二三四五六七八九十\d]+[）)]\s*", "", plain).strip()
    if plain == name or plain.rstrip("试验方法").strip() == name.rstrip("试验方法").strip():
        del lines[idx]
        while idx < len(lines) and not lines[idx].strip():
            del lines[idx]
        return "\n".join(lines)
    return text
