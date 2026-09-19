"""文档文本归一化 — WJGL 与 FLGL 解析共用。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# 康熙部首/符号字体误映射常见区段（犌犅／犜 等）
_CID_RADICAL_RE = re.compile(r"[\u7280-\u72ff]{2,}")
_GB_GARBLED_RE = re.compile(r"犌犅|犐犆犛|狑狊狊|犜\s*[\d０-９]")
_SYMBOL_HEADING_RE = re.compile(r"^[０-９\d\s．.]*[#$%&*]{1,}")


def normalize_fullwidth(text: str) -> str:
    if not text:
        return ""
    result: List[str] = []
    for c in text:
        cp = ord(c)
        if 0xFF10 <= cp <= 0xFF19:
            result.append(chr(cp - 0xFEE0))
        elif cp in (0xFF0E, 0xFF0C):
            result.append("." if cp == 0xFF0E else ",")
        elif cp in (0xFF0F, 0xFF3C):
            result.append("/")
        elif cp in (0x2014, 0x2013, 0xFF0D):
            result.append("-")
        elif cp == 0x3000:
            result.append(" ")
        elif cp == 0xFF1A:
            result.append(":")
        elif cp == 0xFF1B:
            result.append(";")
        else:
            result.append(c)
    return "".join(result)


def collapse_cjk_spaces(text: str) -> str:
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text or "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def normalize_document_text(text: str) -> str:
    return collapse_cjk_spaces(normalize_fullwidth(text or ""))


def has_cid_garbled(text: str) -> bool:
    """检测 PDF 自定义字体 CID / Symbol 字体误映射。"""
    if not text:
        return False
    if _GB_GARBLED_RE.search(text):
        return True
    if _CID_RADICAL_RE.search(text[:2000]):
        return True
    sample = text[:4000]
    radical_count = len(_CID_RADICAL_RE.findall(sample))
    if radical_count >= 2:
        return True
    return False


def count_symbol_font_headings(text: str) -> int:
    count = 0
    for line in (text or "").splitlines()[:200]:
        line = line.strip()
        if len(line) < 2:
            continue
        if _SYMBOL_HEADING_RE.match(line):
            count += 1
        if re.match(r"^[#$%&*]{2,}$", line):
            count += 1
    return count


def garbled_score(text: str) -> float:
    """0=正常, 1=严重乱码。"""
    if not text:
        return 1.0
    score = 0.0
    if has_cid_garbled(text):
        score += 0.5
    if has_symbol_font_garbled(text):
        score += 0.4
    sym = count_symbol_font_headings(text)
    if sym >= 3:
        score += 0.35
    elif sym >= 1:
        score += 0.15
    return min(1.0, score)


_TOC_LEADER_RE = re.compile(r"[\.…·．]{4,}")
_PAGE_ONLY_RE = re.compile(r"^[0-9０-９]{1,3}$")
_ROMAN_ONLY_RE = re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹ]+$")
_GARBLED_SHORT_RE = re.compile(r"犌犅|犐犆犛|狑狊狊")


def is_toc_noise_line(line: str) -> bool:
    """目录页噪声：点线引导符、孤立页码、罗马页脚、短乱码页眉。"""
    s = normalize_document_text(line or "").strip()
    if not s:
        return False
    if _PAGE_ONLY_RE.match(s):
        return True
    if _ROMAN_ONLY_RE.match(s):
        return True
    if _GARBLED_SHORT_RE.search(s) and len(s) < 60:
        return True
    if _TOC_LEADER_RE.search(s):
        stripped = _TOC_LEADER_RE.sub("", s).strip()
        if not stripped or _PAGE_ONLY_RE.match(stripped) or len(stripped) <= 12:
            return True
    if re.fullmatch(r"[\.．·…\s\d０-９]+", s):
        return True
    return False


_SYMBOL_FONT_INLINE_RE = re.compile(
    r"[\s\u3000]+[\u7680-\u76ff]{2,}(?:[a-zA-Z\u7680-\u76ff\s]*)"
)
_SYMBOL_FONT_LATIN_RE = re.compile(
    r"[\s\u3000]+[\u7280-\u72ff]{2,}(?:[a-zA-Z\u7280-\u72ff\s]*)"
)

# PDF Symbol 字体：中文被映射为拉丁字母 / Latin-1（38924 等标准常见）
_LATIN1_GARBAGE_CHAR = re.compile(
    r"[\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u00FF"
    r"ÊËÌÍÎÏÐÑÒÓÔÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ]"
)
_PAREN_CAP_RUN = re.compile(r"[A-Z]\([A-Z]{2,}[A-Z\(\)\u00C0-\u00FF]*")
_DIGIT_SYMBOL_JUNK = re.compile(
    r"(?:"
    r"[\$#%&]{2,}|"
    r"\d{2,}[\\\*]|"
    r"012\*|0178|01;<|"
    r"sra\]|ra\]|"
    r"e[0-9]{1,3}e?üý|"
    r"üý[0-9]|"
    r"34-5401|"
    r"[`´]{2,}"
    r")",
    re.I,
)
_CAP_GARBAGE_RUN = re.compile(r"(?<![A-Z/])(?<![a-z0-9])[A-Z]{5,}(?![a-z/])")
_GB_STD_INLINE_RE = re.compile(r"GB/?T\s*\d", re.I)
_CID_GB_SLASH_RE = re.compile(r"/[\u7280-\u72ff](?=\s*[\d０-９])")
_CID_GB_PREFIX_RE = re.compile(r"[\u7280-\u72ff](?=[\d０-９]{4,})")


def fix_gb_standard_garbled(text: str) -> str:
    """将 /犜38924、犜38924 等 CID 误映射修正为 GB/T38924。"""
    s = text or ""
    s = _CID_GB_SLASH_RE.sub("GB/T", s)
    s = _CID_GB_PREFIX_RE.sub("GB/T", s)
    s = re.sub(r"GB/T\s*/\s*T", "GB/T", s)
    return s


def is_garbled_content(text: str, threshold: float = 0.32) -> bool:
    """判断正文是否仍含大量 Symbol/CID 乱码。"""
    sample = re.sub(r"<[^>]+>", " ", text or "")
    sample = re.sub(r"\s+", " ", sample).strip()
    if not sample:
        return True
    return garbled_score(sample) >= threshold


def has_symbol_font_garbled(text: str) -> bool:
    """检测 Symbol 字体 Latin-1 误映射（P(QRSTUV、ÊËÌÍ 等）。"""
    if not text:
        return False
    sample = text[:4000]
    if len(_LATIN1_GARBAGE_CHAR.findall(sample)) >= 8:
        return True
    if len(_PAREN_CAP_RUN.findall(sample)) >= 2:
        return True
    if len(_DIGIT_SYMBOL_JUNK.findall(sample)) >= 4:
        return True
    return False


_MD_HEADING_PREFIX_RE = re.compile(r"^(#{1,6}\s+)")


def strip_symbol_font_line(line: str) -> str:
    """剔除单行内 Symbol 字体误映射字符。"""
    s = fix_gb_standard_garbled(line or "")
    md_prefix = ""
    md_m = _MD_HEADING_PREFIX_RE.match(s)
    if md_m:
        md_prefix = md_m.group(1)
        s = s[len(md_prefix):]
    if _GB_STD_INLINE_RE.search(s):
        # 保留 GB/T 行，仅删明显 junk
        s = _PAREN_CAP_RUN.sub("", s)
        s = _LATIN1_GARBAGE_CHAR.sub("", s)
    else:
        s = _PAREN_CAP_RUN.sub("", s)
        s = _DIGIT_SYMBOL_JUNK.sub("", s)
        s = _CAP_GARBAGE_RUN.sub("", s)
        s = _LATIN1_GARBAGE_CHAR.sub("", s)
    s = _SYMBOL_FONT_INLINE_RE.sub("", s)
    s = _SYMBOL_FONT_LATIN_RE.sub("", s)
    s = _CID_RADICAL_RE.sub("", s)
    s = re.sub(r"(?<=[\u4e00-\u9fff])\s+[A-Za-z]{1,4}\s+(?=[\u4e00-\u9fff])", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return f"{md_prefix}{s}".strip()


def line_quality_score(line: str) -> float:
    """越高越像正常国标正文（0~1）。"""
    s = (line or "").strip()
    if not s:
        return 1.0
    if re.match(r"^#{1,6}\s+\d", s):
        return 0.85
    if "|" in s and s.count("|") >= 2:
        return 0.8
    total = len(s)
    cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    if cjk >= 6 and cjk / max(total, 1) >= 0.12:
        return 0.75
    good = cjk * 2.0
    for ch in s:
        if ch in "，。、《》？！：；,.!?;:()[]{}-_/\\'\"%％+℃°≤≥±·—":
            good += 1.0
        elif ch.isdigit() or ch.isspace():
            good += 0.8
    good -= len(_LATIN1_GARBAGE_CHAR.findall(s)) * 2.5
    good -= len(_CAP_GARBAGE_RUN.findall(s)) * 4.0
    good -= len(_PAREN_CAP_RUN.findall(s)) * 4.0
    return max(0.0, min(1.0, good / max(total * 1.6, 1)))


def filter_garbled_lines(text: str, min_score: float = 0.2) -> str:
    """丢弃明显乱码行，保留含足够中文或表格的行。"""
    out: List[str] = []
    for line in (text or "").splitlines():
        cleaned = strip_symbol_font_line(line)
        if not cleaned:
            continue
        if line_quality_score(cleaned) >= min_score:
            out.append(cleaned)
            continue
        if len(re.findall(r"[\u4e00-\u9fff]", cleaned)) >= 4:
            out.append(cleaned)
    return "\n".join(out)


def clean_document_html(html: str) -> str:
    """清洗 HTML 正文中的 Symbol 字体乱码，保留表格结构。"""
    import html as html_module

    h = html or ""
    if not h.strip():
        return ""

    def _clean_inner(inner: str) -> str:
        plain = re.sub(r"<[^>]+>", " ", inner)
        return clean_document_text(html_module.unescape(plain))

    h = re.sub(
        r"(<t[dh][^>]*>)(.*?)(</t[dh]>)",
        lambda m: m.group(1) + _clean_inner(m.group(2)) + m.group(3),
        h,
        flags=re.I | re.S,
    )
    h = re.sub(
        r"<(p|h[1-6]|strong|span|li|td|th)([^>]*)>(.*?)</\1>",
        lambda m: (
            f"<{m.group(1)}{m.group(2)}>{_clean_inner(m.group(3))}</{m.group(1)}>"
            if _clean_inner(m.group(3))
            else ""
        ),
        h,
        flags=re.I | re.S,
    )
    return h


def strip_inline_symbol_garbled(text: str) -> str:
    """剔除术语行内符号字体英文乱码（如 狊狋狅狉犪犵犲…）。"""
    if not text:
        return ""
    out: List[str] = []
    for line in text.splitlines():
        s = strip_symbol_font_line(line)
        if s:
            out.append(s)
    return "\n".join(out)


def clean_document_text(text: str) -> str:
    """归一化并剔除目录噪声行，保留有效正文。"""
    text = fix_gb_standard_garbled(text or "")
    text = strip_inline_symbol_garbled(normalize_document_text(text))
    out: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            if out and out[-1] != "":
                out.append("")
            continue
        if is_toc_noise_line(s):
            if _TOC_LEADER_RE.search(s):
                head = re.split(r"[\.…·．]{3,}", s)[0].strip()
                if head and len(head) > 1 and not _PAGE_ONLY_RE.match(head):
                    if not out or out[-1] != head:
                        out.append(head)
            continue
        out.append(s)
    collapsed: List[str] = []
    prev_blank = False
    for line in out:
        if line == "":
            if not prev_blank:
                collapsed.append("")
            prev_blank = True
        else:
            collapsed.append(line)
            prev_blank = False
    result = "\n".join(collapsed).strip()
    return filter_garbled_lines(result)


def is_toc_noise_section(title: str, text: str) -> bool:
    """判断章节是否主要为目录/页码噪声。"""
    body_lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not body_lines:
        t = (title or "").strip()
        return bool(t and _TOC_LEADER_RE.search(t))
    noise = sum(1 for l in body_lines if is_toc_noise_line(l))
    if noise / max(len(body_lines), 1) >= 0.55:
        return True
    meaningful = [l for l in body_lines if not is_toc_noise_line(l) and len(l) > 8]
    return len(meaningful) == 0 and _TOC_LEADER_RE.search(text or "")


def normalize_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in pages or []:
        item = dict(p)
        item["text"] = clean_document_text(p.get("text") or "")
        out.append(item)
    return out
