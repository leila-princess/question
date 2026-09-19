"""LLM 输入预处理：目录净化、base64 过滤、按节边界分片。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 图片 / 嵌入二进制
_DATA_URL_B64_RE = re.compile(r"data:[^;>\s]+;base64,[A-Za-z0-9+/=\s]{80,}", re.I)
_MD_BASE64_IMG_RE = re.compile(r"!\[[^\]]*\]\(data:[^)]+\)", re.I)
_LONG_B64_RUN_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{256,}={0,2}")

# 目录行：节号 + 中文标题
_HEADING_LINE_RE = re.compile(
    r"^(\d+(?:\.\d+){0,5})\s+([\u4e00-\u9fff（）()、，\sA-Za-z0-9.\-℃°%±≤≥]{2,80})\s*$"
)
# 正文中的节标题（用于分片）
_SECTION_BREAK_RE = re.compile(
    r"(?:^|\n)(\d+(?:\.\d+){0,5})\s+([\u4e00-\u9fff][^\n]{1,80})"
)

_TABLE_CAPTION_RE = re.compile(r"^(?:附)?表\s*[\dA-Za-z]", re.I)
_LIST_ONLY_RE = re.compile(r"^[（(]?\d+[）).、]\s*")
_NOISE_TITLE_RE = re.compile(
    r"(水压|喷水量|kPa|℃|试验人员\d|序号参数|岗位角色|对应高度)",
    re.I,
)


def _replace_long_b64_run(match: re.Match) -> str:
    s = match.group(0)
    if "+" in s or "/" in s or s.rstrip().endswith("="):
        return "[编码数据已省略]"
    if len(set(s.rstrip("="))) < 12:
        return s
    return "[编码数据已省略]"


def sanitize_text_for_llm(text: str) -> str:
    """移除 base64 图片与长编码串，保留纯文本供 LLM。"""
    s = str(text or "")
    if not s:
        return ""
    s = _DATA_URL_B64_RE.sub("[图片]", s)
    s = _MD_BASE64_IMG_RE.sub("[图片]", s)
    s = _LONG_B64_RUN_RE.sub(_replace_long_b64_run, s)
    s = re.sub(r"<img[^>]+src=[\"']data:[^\"']+[\"'][^>]*>", "[图片]", s, flags=re.I)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _section_no_depth(section_no: str) -> int:
    return len([p for p in str(section_no or "").strip().split(".") if p])


def _normalize_outline_pair(section_no: str, title: str) -> Tuple[str, str]:
    sno = str(section_no or "").strip().lstrip(".")
    t = str(title or "").strip()
    if not t:
        return sno, t
    # 去掉标题前重复的节号：1 + "1 试验条件" → "试验条件"
    if sno:
        t = re.sub(rf"^{re.escape(sno)}[\s　.、]+", "", t)
    # "1 1 试验条件" 合并
    m = re.match(r"^(\d+(?:\.\d+)*)\s+(.+)$", t)
    if m and sno and m.group(1) == sno:
        t = m.group(2).strip()
    return sno, t


def is_valid_toc_heading(section_no: str, title: str) -> bool:
    """过滤表题、列表项、参数行等非目录标题。"""
    sno, t = _normalize_outline_pair(section_no, title)
    if not t or len(t) > 80:
        return False
    if _TABLE_CAPTION_RE.match(t) or _TABLE_CAPTION_RE.match(f"{sno} {t}"):
        return False
    if _LIST_ONLY_RE.match(t) and not _HEADING_LINE_RE.match(f"{sno} {t}".strip()):
        return False
    if _NOISE_TITLE_RE.search(t) and len(t) < 25:
        return False
    if re.search(r"^\d+[）).、]", t):
        return False
    # 须像章节标题（含中文主题词）
    if not re.search(r"[\u4e00-\u9fff]{2,}", t):
        return False
    if sno and not re.match(r"^\d", sno):
        return False
    return True


def build_llm_heading_outline(
    parsed_doc: Dict[str, Any],
    *,
    limit: int = 80,
) -> str:
    """
    仅输出目录结构（节号 + 标题），去重节号、过滤非标题行。
    不附带 section snippet / 表体 / 正文。
    """
    lines: List[str] = []
    seen: set = set()

    def _append(sno: str, title: str) -> None:
        sno, title = _normalize_outline_pair(sno, title)
        if not title or not is_valid_toc_heading(sno, title):
            return
        key = f"{sno}|{title}"
        if key in seen:
            return
        seen.add(key)
        line = f"{sno} {title}".strip() if sno else title
        lines.append(line)

    flat = list(parsed_doc.get("flat_headings") or [])
    for h in flat[: limit * 2]:
        if not isinstance(h, dict):
            continue
        _append(
            str(h.get("sectionNo") or h.get("section_no") or ""),
            str(h.get("title") or ""),
        )
        if len(lines) >= limit:
            break

    if len(lines) < 5:
        for item in list(parsed_doc.get("section_catalogue") or [])[: limit * 2]:
            if not isinstance(item, dict):
                continue
            _append(
                str(item.get("section_no") or item.get("sectionNo") or ""),
                str(item.get("title") or item.get("heading") or ""),
            )
            if len(lines) >= limit:
                break

    if len(lines) < 5:
        for sec in list(parsed_doc.get("sections") or [])[: limit * 2]:
            if not isinstance(sec, dict):
                continue
            _append(
                str(sec.get("section_no") or ""),
                str(sec.get("title") or sec.get("heading") or ""),
            )
            if len(lines) >= limit:
                break

    return "\n".join(lines[:limit]) if lines else "（无目录）"


def _fixed_split_text_chunks(
    text: str,
    chunk_chars: int,
    overlap_chars: int = 300,
) -> List[str]:
    """按段落贪心分包（节内 fallback）。"""
    raw = (text or "").strip()
    if not raw:
        return []
    if len(raw) <= chunk_chars:
        return [raw]

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", raw) if p.strip()]
    if not paragraphs:
        paragraphs = [raw[i : i + chunk_chars] for i in range(0, len(raw), chunk_chars)]

    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf))
        buf, buf_len = [], 0

    for para in paragraphs:
        plen = len(para) + (2 if buf else 0)
        if buf and buf_len + plen > chunk_chars:
            flush()
        if len(para) > chunk_chars:
            if buf:
                flush()
            for i in range(0, len(para), chunk_chars):
                chunks.append(para[i : i + chunk_chars])
            continue
        buf.append(para)
        buf_len += plen
    flush()

    if overlap_chars <= 0 or len(chunks) <= 1:
        return chunks
    overlapped: List[str] = [chunks[0]]
    for i in range(1, len(chunks)):
        prev = chunks[i - 1]
        tail = prev[-overlap_chars:] if len(prev) > overlap_chars else prev
        overlapped.append(f"{tail}\n\n{chunks[i]}".strip())
    return overlapped


def _section_spans(text: str, max_depth: int = 2) -> List[Tuple[int, int, str, str]]:
    """返回 [(start, end, section_no, title), ...] 按节边界。"""
    raw = str(text or "")
    if not raw.strip():
        return []
    matches = list(_SECTION_BREAK_RE.finditer(raw))
    if not matches:
        return [(0, len(raw), "", "")]
    spans: List[Tuple[int, int, str, str]] = []
    filtered = [
        m for m in matches
        if _section_no_depth(m.group(1)) <= max_depth
        and is_valid_toc_heading(m.group(1), m.group(2).split("\n")[0][:40])
    ]
    if not filtered:
        filtered = matches[:1]
    for i, m in enumerate(filtered):
        start = m.start(1) if m.start(1) > 0 or raw[m.start():m.start() + 1] != "\n" else m.start(2)
        if m.start() > 0 and raw[m.start()] == "\n":
            start = m.start(1)
        end = filtered[i + 1].start() if i + 1 < len(filtered) else len(raw)
        spans.append((start, end, m.group(1), m.group(2).strip()))
    if spans and spans[0][0] > 0:
        spans.insert(0, (0, spans[0][0], "", "前言"))
    return spans


def split_text_at_section_boundaries(
    text: str,
    chunk_chars: int,
    *,
    max_section_depth: int = 2,
    overlap_chars: int = 300,
) -> List[str]:
    """
    按一级/二级节标题组批，避免 8.3 节被机械切断。
    单节超长时再在节内按段落硬切。
    """
    raw = sanitize_text_for_llm(text)
    if not raw:
        return []
    if len(raw) <= chunk_chars:
        return [raw]

    spans = _section_spans(raw, max_depth=max_section_depth)
    if len(spans) <= 1:
        return _fixed_split_text_chunks(raw, chunk_chars=chunk_chars, overlap_chars=overlap_chars)

    blocks: List[str] = []
    for start, end, sno, title in spans:
        block = raw[start:end].strip()
        if block:
            header = f"=== {sno} {title} ===".strip() if sno else ""
            blocks.append(f"{header}\n{block}".strip() if header else block)

    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf))
        buf, buf_len = [], 0

    for block in blocks:
        blen = len(block)
        if blen > chunk_chars:
            flush()
            chunks.extend(
                _fixed_split_text_chunks(
                    block,
                    chunk_chars=chunk_chars,
                    overlap_chars=overlap_chars,
                )
            )
            continue
        if buf and buf_len + blen + 2 > chunk_chars:
            flush()
        buf.append(block)
        buf_len += blen + (2 if buf_len else 0)
    flush()

    if not chunks:
        return _fixed_split_text_chunks(raw, chunk_chars=chunk_chars, overlap_chars=overlap_chars)

    if overlap_chars <= 0 or len(chunks) <= 1:
        return chunks
    overlapped = [chunks[0]]
    for i in range(1, len(chunks)):
        prev = chunks[i - 1]
        tail = prev[-overlap_chars:] if len(prev) > overlap_chars else prev
        overlapped.append(f"{tail}\n\n{chunks[i]}".strip())
    return overlapped
