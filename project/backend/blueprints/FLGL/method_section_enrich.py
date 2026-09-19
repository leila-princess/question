"""按章节提取方法：LLM 整合、标题重编号、见XXX 溯源、图节点推荐。"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SECTION_LINE_RE = re.compile(r"^(\d+(?:\.\d+){0,5})\s+(.+)$")
_SEE_TABLE_RE = re.compile(r"见\s*表\s*([A-Za-z]?\.\d+(?:\.\d+)?|\d+)", re.I)
_SAME_TABLE_RE = re.compile(r"同\s*表\s*([A-Za-z]?\.\d+(?:\.\d+)?|\d+)", re.I)
_SEE_SECTION_RE = re.compile(r"见\s*(?:本章|本标准|附录)?\s*(\d+(?:\.\d+){1,5}|附录\s*[A-Z])", re.I)
_SEE_APPENDIX_RE = re.compile(r"见\s*附录\s*([A-ZＡ-Ｚ])", re.I)
_SAME_SECTION_RE = re.compile(r"同\s*(\d+(?:\.\d+){1,5})\s*(?:节|条|款|项|规定|要求|方法|内容)?", re.I)
_SAME_CONTEXT_RE = re.compile(r"同\s*(?:上|前|本)(?:一)?(?:条|款|节|表|项|规定|要求|方法|内容)?", re.I)
_SAME_CHAPTER_RE = re.compile(r"同\s*(?:本章|本标准|上文)", re.I)
_BY_SECTION_RE = re.compile(r"按\s*(\d+(?:\.\d+){1,5})", re.I)
_ZHAO_SECTION_RE = re.compile(r"照\s*(\d+(?:\.\d+){1,5})", re.I)
_NORM_SECTION_REF_RE = re.compile(r"(?<=[,，;；\s])(\d+(?:\.\d+){1,5})规定", re.I)
_TO_SECTION_REF_RE = re.compile(r"至\s*(\d+(?:\.\d+)+)(?:规定|的)", re.I)
_EXPLICIT_SEC_REF_RE = re.compile(
    r"(?:见|按|照|同)\s*(?:本章|本标准|附录)?\s*(\d+(?:\.\d+){1,5})",
    re.I,
)
_EXPLICIT_TABLE_REF_RE = re.compile(
    r"(?:见|同)\s*表\s*([A-Za-z]?\.\d+(?:\.\d+)?|\d+)",
    re.I,
)
_SEE_FIGURE_RE = re.compile(r"见\s*图\s*(\d+)", re.I)
_AS_FIGURE_RE = re.compile(r"如\s*图\s*(\d+)", re.I)
_PLAIN_FIGURE_REF_RE = re.compile(r"(?<![表附])图\s*(\d+)", re.I)
_FIGURE_CAPTION_LINE_RE = re.compile(
    r"^图\s*(\d+)\s+[\u4e00-\u9fffA-Za-z（）()、，\-\s]{2,}",
    re.I,
)
_MD_FIGURE_IMAGE_RE = re.compile(
    r"!\[(?:图\s*)?(\d+)[^\]]*\]\(([^)]+)\)",
    re.I,
)
_FIGURE_CAPTION_HTML_RE = re.compile(
    r'<p[^>]*>\s*(图\s*\d+[^<]*)</p>\s*(<figure[\s\S]*?</figure>)',
    re.I | re.S,
)
_TABLE_CAPTION_RE = re.compile(
    r"<p[^>]*>\s*(表\s*\d+[^<]*)</p>\s*(?:<div\s+class=\"doc-table\"[^>]*>\s*)?(<table[\s\S]*?</table>)",
    re.I,
)

MAIN_SECTION_TITLES = ("1. 试验条件", "2. 试验过程", "3. 结果评定")

_WJGL_HEADING_RE = re.compile(
    r'<p[^>]*class="wjgl-section-heading"[^>]*data-section-no="([^"]+)"[^>]*>.*?</p>',
    re.I | re.S,
)
_PLAIN_HEADING_RE = re.compile(
    r'<p[^>]*>\s*<strong>\s*(\d+(?:\.\d+)*)\s+([^<]+)</strong>\s*</p>',
    re.I | re.S,
)


def _top_chapter_no(section_no: str) -> int:
    s = (section_no or "").strip().lstrip(".")
    if not s:
        return 0
    head = s.split(".")[0]
    return int(head) if head.isdigit() else 0


def split_gb_standard_method_html(html: str) -> Dict[str, str]:
    """
    按国标章节号切分 HTML：
    - 通用 GB：4/5→试验条件，6→试验过程，7/8/9→结果评定
    - GB/T 38924 分则：3→试验条件，5→试验过程，6/7→结果评定
    保留原章节号与 <table> 结构，不做 1.2.3 重编号。
    """
    h = (html or "").strip()
    if not h:
        return {"sytj": "", "sygc": "", "jgpd": ""}

    markers: List[Tuple[int, str, str]] = []
    for m in _WJGL_HEADING_RE.finditer(h):
        markers.append((m.start(), m.group(1).strip().lstrip("."), m.group(0)))
    if not markers:
        for m in _PLAIN_HEADING_RE.finditer(h):
            markers.append((m.start(), m.group(1).strip().lstrip("."), m.group(0)))
    if not markers:
        return {"sytj": "", "sygc": h, "jgpd": ""}

    markers.sort(key=lambda x: x[0])
    tops = {_top_chapter_no(sn) for _, sn, _ in markers}

    def _bucket_key(top: int) -> Optional[str]:
        # GB/T 38924 环境试验分则：3=条件 4=设备仪表 5=过程 6/7=中断与评定
        if 4 in tops and 5 in tops and 3 in tops:
            if top in (3, 4):
                return "sytj"
            if top == 5:
                return "sygc"
            if top in (6, 7):
                return "jgpd"
            return None
        if 4 in tops and 5 in tops and 7 in tops and 3 not in tops:
            if top == 4:
                return "sytj"
            if top == 5:
                return "sygc"
            if top == 7:
                return "jgpd"
            if top == 6:
                return "jgpd"
            return None
        if 3 in tops and 5 in tops and 4 not in tops:
            if top == 3:
                return "sytj"
            if top == 5:
                return "sygc"
            if top in (6, 7):
                return "jgpd"
            return None
        if top in (4, 5):
            return "sytj"
        if top == 6:
            return "sygc"
        if top in (7, 8, 9):
            return "jgpd"
        return None

    buckets: Dict[str, List[str]] = {"sytj": [], "sygc": [], "jgpd": []}

    for i, (pos, sec_no, heading_html) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(h)
        body = h[pos:end]
        top = _top_chapter_no(sec_no)
        key = _bucket_key(top)
        if not key:
            continue
        buckets[key].append(body)

    return {k: "".join(v).strip() for k, v in buckets.items()}


def _html_blocks_from_split(split: Dict[str, str]) -> Dict[str, Any]:
    return {
        "sytj": {"text": split.get("sytj") or "", "tables_html": [], "formulas_latex": []},
        "sygc": {"text": split.get("sygc") or "", "tables_html": [], "formulas_latex": []},
        "jgpd": {"text": split.get("jgpd") or "", "tables_html": [], "formulas_latex": []},
    }


_LOOSE_HEADING_RE = re.compile(
    r'<p[^>]*>\s*(?:<strong>\s*)?(\d+(?:\.\d+)*)\s+'
    r'([\u4e00-\u9fff（）()、，\sA-Za-z0-9.\-]{2,64})'
    r'(?:\s*</strong>)?\s*</p>',
    re.I | re.S,
)


def _title_from_heading_html(sec_no: str, heading_html: str) -> str:
    inner = re.sub(r"<[^>]+>", " ", heading_html or "")
    inner = re.sub(r"\s+", " ", inner).strip()
    inner = re.sub(r"^\d+(?:\.\d+)*\s*", "", inner).strip()
    return inner or sec_no


def _bucket_from_chapter_top(top: int, tops: set) -> Optional[str]:
    """按国标章号粗分块（38924 分则 / 经典 GB）。"""
    if not top:
        return None
    if 4 in tops and 5 in tops and 3 in tops:
        if top in (3, 4):
            return "sytj"
        if top == 5:
            return "sygc"
        if top in (6, 7):
            return "jgpd"
        return None
    if 4 in tops and 5 in tops and 7 in tops and 3 not in tops:
        if top == 4:
            return "sytj"
        if top == 5:
            return "sygc"
        if top in (6, 7):
            return "jgpd"
        return None
    if 3 in tops and 5 in tops and 4 not in tops:
        if top == 3:
            return "sytj"
        if top == 5:
            return "sygc"
        if top in (6, 7):
            return "jgpd"
        return None
    if top in (4, 5):
        return "sytj"
    if top == 6:
        return "sygc"
    if top in (7, 8, 9):
        return "jgpd"
    return None


def classify_section_to_bucket(sec_no: str, title: str, tops: set) -> Optional[str]:
    """将单节划入试验条件/过程/评定；优先标题语义，其次章号。"""
    from blueprints.FLGL.GetTestMethod.section_extract_config import (
        exclude_title_keywords,
        prefer_title_name_over_chapter_number,
        title_keywords,
    )

    title = (title or "").strip()
    for ex in exclude_title_keywords():
        if ex and ex in title and len(title) <= len(ex) + 8:
            return None

    if prefer_title_name_over_chapter_number() and title:
        for bucket in ("jgpd", "sygc", "sytj"):
            for kw in sorted(title_keywords(bucket), key=len, reverse=True):
                if kw and kw in title:
                    return bucket

    top = _top_chapter_no(sec_no)
    by_chapter = _bucket_from_chapter_top(top, tops)
    if by_chapter:
        return by_chapter

    if title:
        for bucket in ("jgpd", "sygc", "sytj"):
            for kw in sorted(title_keywords(bucket), key=len, reverse=True):
                if kw and kw in title:
                    return bucket
    return None


def _collect_all_headings_in_order(html: str) -> List[Tuple[int, str, str]]:
    """wjgl / strong / 松散段落标题，按文档顺序去重。"""
    markers: List[Tuple[int, str, str]] = []
    seen_at: Dict[str, int] = {}

    def _add(pos: int, sno: str, frag: str) -> None:
        sno = (sno or "").strip().lstrip(".")
        if not sno:
            return
        if sno in seen_at and abs(seen_at[sno] - pos) < 8:
            return
        seen_at[sno] = pos
        markers.append((pos, sno, frag))

    for m in _WJGL_HEADING_RE.finditer(html or ""):
        _add(m.start(), m.group(1), m.group(0))
    for m in _PLAIN_HEADING_RE.finditer(html or ""):
        _add(m.start(), m.group(1), m.group(0))
    for m in _LOOSE_HEADING_RE.finditer(html or ""):
        _add(m.start(), m.group(1), m.group(0))

    markers.sort(key=lambda x: x[0])
    return markers


def split_method_html_ordered(html: str) -> Dict[str, str]:
    """
    按文档顺序切分三块，语义/章号归类，**不改变节内顺序**。
    用于非 LLM 方法生成：仅规则排版与重编号。
    """
    h = (html or "").strip()
    if not h:
        return {"sytj": "", "sygc": "", "jgpd": ""}

    markers = _collect_all_headings_in_order(h)
    if not markers:
        return {"sytj": "", "sygc": h, "jgpd": ""}

    tops = {_top_chapter_no(sn) for _, sn, _ in markers}
    buckets: Dict[str, List[str]] = {"sytj": [], "sygc": [], "jgpd": []}
    last_bucket = "sygc"
    preamble = h[: markers[0][0]].strip()
    if preamble:
        buckets[last_bucket].append(preamble)

    for i, (pos, sec_no, heading_html) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(h)
        body = h[pos:end]
        title = _title_from_heading_html(sec_no, heading_html)
        bucket = classify_section_to_bucket(sec_no, title, tops)
        if not bucket:
            bucket = _bucket_from_chapter_top(_top_chapter_no(sec_no), tops) or last_bucket
        else:
            last_bucket = bucket
        buckets[bucket].append(body)

    return {k: "".join(v).strip() for k, v in buckets.items()}


def _strip_scheme_h4_titles(html: str) -> str:
    """去掉正文中与 SYNR 大标题重复 h4。"""
    out = html or ""
    for title in MAIN_SECTION_TITLES:
        pat = re.compile(
            rf"<h4[^>]*>\s*{re.escape(title)}\s*</h4>",
            re.I,
        )
        out = pat.sub("", out, count=1)
    return out.strip()


def _normalize_heading_paragraphs(html: str) -> str:
    """统一小节标题为 wjgl-section-heading + strong。"""
    h = html or ""

    def _loose_to_wjgl(m: re.Match) -> str:
        sno = m.group(1).strip().lstrip(".")
        title = (m.group(2) or "").strip()
        aid = "wjgl-sec-" + sno.replace(".", "-")
        return (
            f'<p id="{aid}" class="wjgl-section-heading" data-section-no="{sno}" '
            f'style="font-weight:600;margin:10px 0 6px"><strong>{sno} {title}</strong></p>'
        )

    h = _LOOSE_HEADING_RE.sub(_loose_to_wjgl, h)
    return h


def format_method_blocks_rule_based(blocks: Dict[str, Any]) -> Dict[str, Any]:
    """
    LLM 块后处理：去重复章标题、按 1/2/3 方案根重编号。
    """
    out: Dict[str, Any] = {}

    for key in ("sytj", "sygc", "jgpd"):
        blk = dict(blocks.get(key) or {})
        raw = _strip_scheme_h4_titles(str(blk.get("text") or ""))
        raw = _normalize_heading_paragraphs(raw)
        raw = _strip_redundant_chapter_heading(raw, key)
        scheme_root = {"sytj": "1", "sygc": "2", "jgpd": "3"}[key]
        renumbered, partial = _renumber_block_under_scheme_root(raw, scheme_root)
        text = _apply_section_ref_map_safe(renumbered, partial)
        text = _wrap_doc_tables(text)
        blk["text"] = text
        blk["tables_html"] = []
        out[key] = blk

    return out


def renumber_document_sequential(html: str) -> Tuple[str, Dict[str, str]]:
    """按文档顺序将标题重编号为 1 / 1.1 / 2 / 2.1 …，不删节、不改顺序。"""
    h = (html or "").strip()
    if not h:
        return h, {}

    markers = _collect_all_headings_in_order(h)
    if not markers:
        return h, {}

    min_depth = min(len(old_no.split(".")) for _, old_no, _ in markers)
    no_map: Dict[str, str] = {}
    counters = [0, 0, 0, 0, 0, 0]

    for _, old_no, _ in markers:
        parts = old_no.split(".")
        depth = len(parts) - min_depth + 1
        if depth < 1:
            depth = 1
        for i in range(depth, len(counters)):
            counters[i] = 0
        counters[depth - 1] += 1
        new_parts = [str(counters[i]) for i in range(depth)]
        no_map[old_no] = ".".join(new_parts)

    def _wjgl_repl(m: re.Match) -> str:
        old_no = m.group(1).strip().lstrip(".")
        new_no = no_map.get(old_no, old_no)
        inner = re.sub(r"<[^>]+>", "", m.group(0))
        title_text = re.sub(r"^\d+(?:\.\d+)*\s*", "", inner).strip()
        return _heading_html_with_no(m.group(0), old_no, new_no, title_text)

    def _plain_repl(m: re.Match) -> str:
        old_no = m.group(1).strip().lstrip(".")
        new_no = no_map.get(old_no, old_no)
        title_text = (m.group(2) or "").strip() if m.lastindex and m.lastindex >= 2 else ""
        if not title_text:
            inner = re.sub(r"<[^>]+>", "", m.group(0))
            title_text = re.sub(r"^\d+(?:\.\d+)*\s*", "", inner).strip()
        return _heading_html_with_no(m.group(0), old_no, new_no, title_text)

    out = _WJGL_HEADING_RE.sub(_wjgl_repl, h)
    out = _PLAIN_HEADING_RE.sub(_plain_repl, out)
    out = _LOOSE_HEADING_RE.sub(_plain_repl, out)
    partial = {k: v for k, v in no_map.items() if v}
    return out.strip(), partial


def format_selected_content_rule_based(html: str) -> str:
    """
    用户自选章节（非 LLM）：不切分三块，保持内容与先后顺序。
    输出 GFM Markdown：标题重编号、段落换行、a)/(1) 序号连续化。
    """
    raw = (html or "").strip()
    if re.search(r"<\s*(p|div|table|h[1-6]|ul|ol|li|strong)\b", raw, re.I):
        return finalize_section_extract_synr(raw)
    return finalize_section_extract_markdown(raw)


def _normalize_html_headings_for_method(html: str) -> str:
    """将 h1–h6、br 等 HTML 统一为 wjgl-section-heading 段落，便于重编号。"""
    import html as html_module

    h = (html or "").strip()
    if not h:
        return ""

    h_tag_re = re.compile(r"<h([1-6])([^>]*)>([\s\S]*?)</h\1>", re.I)

    def _h_repl(m: re.Match) -> str:
        attrs = m.group(2) or ""
        inner = re.sub(r"<[^>]+>", "", m.group(3) or "").strip()
        inner = html_module.unescape(inner)
        sno_m = re.search(r'data-section-no=["\']([^"\']+)["\']', attrs, re.I)
        sno = (sno_m.group(1) if sno_m else "").strip().lstrip(".")
        if not sno:
            hm = re.match(r"^(\d+(?:\.\d+)*)\s*(.+)$", inner)
            if hm:
                sno, inner = hm.group(1).strip(), hm.group(2).strip()
        display = inner
        if sno and not re.match(rf"^{re.escape(sno)}(?:\s|[\.．、]|$)", inner):
            display = f"{sno} {inner}".strip()
        aid = f' id="wjgl-sec-{sno.replace(".", "-")}"' if sno else ""
        data = f' data-section-no="{html_module.escape(sno)}"' if sno else ""
        return (
            f'<p{aid} class="wjgl-section-heading"{data} '
            f'style="font-weight:600;margin:10px 0 6px">'
            f"<strong>{html_module.escape(display)}</strong></p>"
        )

    h = h_tag_re.sub(_h_repl, h)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    return h


def finalize_section_extract_synr(html: str) -> str:
    """按章提取终态 SYNR：排版、换行、标题从 1 重编号，供 WangEditor 展示。"""
    raw = (html or "").strip()
    if not raw:
        return ""

    raw = _normalize_html_headings_for_method(raw)
    if not re.search(r"<\s*(p|div|table|h[1-6]|ul|ol|li|figure|img|strong)\b", raw, re.I):
        raw = _plain_text_to_method_html(raw)
    else:
        raw = _normalize_heading_paragraphs(_strip_scheme_h4_titles(raw))

    renumbered, ref_map = renumber_document_sequential(raw)
    text = _apply_section_ref_map_safe(renumbered, ref_map)
    text = _wrap_doc_tables(text)

    if (
        text
        and not re.search(r"<\s*p\b", text, re.I)
        and not re.search(r"<\s*(figure|img)\b", text, re.I)
    ):
        plain = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        plain = re.sub(r"<[^>]+>", "\n", plain)
        plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
        if plain:
            text = _plain_text_to_method_html(plain)
            renumbered, ref_map = renumber_document_sequential(text)
            text = _apply_section_ref_map_safe(renumbered, ref_map)
            text = _wrap_doc_tables(text)
    return text


def _finalize_structured_synr_html(html: str) -> str:
    """已成型的章节+表格 HTML：仅规范化标题与表包裹，避免重编号打乱顺序。"""
    raw = (html or "").strip()
    if not raw:
        return ""
    raw = _normalize_html_headings_for_method(raw)
    raw = _normalize_heading_paragraphs(_strip_scheme_h4_titles(raw))
    return _wrap_doc_tables(raw)


def ensure_method_synr_html(content: str) -> str:
    """提取结果写入 synr 前的统一入口（富文本 + 标题重编号）。"""
    return finalize_section_extract_synr(content)


def _is_structured_synr_html(html: str) -> bool:
    """已是带章节标题的富文本；含五段标题时视为成形，不宜整段剥成纯文本。"""
    h = (html or "").strip()
    if not h:
        return False
    has_heading = bool(re.search(r"wjgl-section-heading|data-section-no=", h, re.I))
    has_blocks = bool(re.search(r"<\s*(?:p|div|h[1-6])\b", h, re.I))
    if has_heading and has_blocks:
        return True
    if not re.search(r"<\s*table\b", h, re.I):
        return False
    return has_blocks


def _synr_content_needs_restructure(html: str) -> bool:
    """LLM/拼接产物常见：大段 <p>+<br>、未拆（1）子标题、残留国标节号。"""
    h = (html or "").strip()
    if not h:
        return False
    if re.search(r"8\.\d+\.\d+", h) or re.search(r'data-section-no=["\']8\.', h, re.I):
        return True
    if _is_structured_synr_html(h) and not re.search(r"^#{1,6}\s", h, re.M):
        return False
    if _is_br_only_pseudo_html(h):
        return True
    br_count = len(re.findall(r"<\s*br\s*/?>", h, re.I))
    if br_count >= 3:
        return True
    if re.search(r"<\s*h[1-6]\b", h, re.I) and br_count >= 2:
        return True
    if re.search(r"[（(]\d+[）)]", h) and br_count >= 1:
        return True
    if re.search(r"^#{1,6}\s", h, re.M):
        return True
    if re.search(r"^(?:附)?表\s*[\dA-Za-z]+", h, re.M):
        return True
    if not re.search(r'class=["\'][^"\']*wjgl-section-heading', h, re.I):
        if re.search(r"^\d+(?:\.\d+)*\s+[\u4e00-\u9fff]", h, re.M):
            return True
    return False


def _strip_outline_duplicate_headings_html(html: str) -> str:
    """去掉 8.2.1 等与五段大标题重复的大纲节标题行。"""
    h = str(html or "").strip()
    if not h:
        return ""
    h = re.sub(
        r'<(?:p|h[1-6])[^>]*class="[^"]*wjgl-section-heading[^"]*"[^>]*'
        r'data-section-no="\d+\.\d+(?:\.\d+)+"[^>]*>.*?</(?:p|h[1-6])>',
        "",
        h,
        flags=re.I | re.S,
    )
    h = re.sub(
        r'<(?:p|h[1-6])[^>]*data-section-no="\d+\.\d+(?:\.\d+)+"[^>]*>.*?</(?:p|h[1-6])>',
        "",
        h,
        flags=re.I | re.S,
    )
    h = re.sub(
        r'<p[^>]*>\s*<strong>\s*\d+(?:\.\d+)+\s+[^<]*?</strong>\s*</p>',
        "",
        h,
        flags=re.I | re.S,
    )
    return h


_TOP_FIVE_SYNR_HEADING_RE = re.compile(
    r'(<(?:p|h[1-6])[^>]*class="[^"]*wjgl-section-heading[^"]*"[^>]*'
    r'data-section-no="([1-5])"[^>]*>.*?</(?:p|h[1-6])>)',
    re.I | re.S,
)

_GB_OUTLINE_SECTION_LINE_RE = re.compile(
    r"^(\d+(?:\.\d+)+)\s*"
    r"(试验目的|试验条件|试验方法|试验过程|试验数据处理|结果评[定判])"
    r"(.*)$"
)
_SYNR_TOP_SECTION_NUM = {
    "试验目的": "1",
    "试验条件": "2",
    "试验方法": "3",
    "试验过程": "3",
    "试验数据处理": "4",
    "结果评定": "5",
    "结果评判": "5",
    "结果判定": "5",
}


def _map_gb_outline_headings_to_top_five(text: str) -> str:
    """8.2.1 试验目的 → 1 试验目的（五段大标题归一）。"""
    lines = (text or "").splitlines()
    out: List[str] = []
    for ln in lines:
        stripped = ln.strip()
        m = _GB_OUTLINE_SECTION_LINE_RE.match(stripped)
        if m:
            title = m.group(2)
            rest = (m.group(3) or "").strip()
            sec_no = _SYNR_TOP_SECTION_NUM.get(title, "")
            if sec_no:
                out.append(f"{sec_no} {title}{rest}")
                continue
        out.append(ln)
    return "\n".join(out)


def _renumber_global_paren_items_in_text(text: str) -> str:
    """各五段大标题段内「（1）…」按出现顺序重排为（1）（2）…。"""
    lines = (text or "").splitlines()
    out: List[str] = []
    paren_idx = 0
    paren_re = re.compile(r"^[（(](\d+)[）)]\s*(.+)$")
    for ln in lines:
        stripped = ln.strip()
        if _TOP_SECTION_RE.match(stripped):
            paren_idx = 0
            out.append(ln)
            continue
        m = paren_re.match(stripped)
        if m and m.group(2).strip():
            paren_idx += 1
            out.append(f"（{paren_idx}）{m.group(2).strip()}")
        else:
            out.append(ln)
    return "\n".join(out)


def polish_native_outline_synr(html: str) -> str:
    """
    大纲类原生 HTML SYNR：保留五段 1–5 大标题，去掉 8.x.x 重复节标题，
    段内（1）/1）/1. 列表重排，表格保留。
    """
    h = str(html or "").strip()
    if not h:
        return ""

    markers = list(_TOP_FIVE_SYNR_HEADING_RE.finditer(h))
    if len(markers) < 2:
        return polish_method_synr_structure(h)

    parts: List[str] = []
    for i, m in enumerate(markers):
        heading = m.group(1)
        body_end = markers[i + 1].start() if i + 1 < len(markers) else len(h)
        body = h[m.end():body_end].strip()
        body = _strip_outline_duplicate_headings_html(body)
        if body:
            plain = html_to_section_plain(body)
            plain = renumber_paren_num_items_in_text(plain)
            plain = renumber_cn_paren_num_items_in_text(plain)
            plain = renumber_dot_num_items_in_text(plain)
            plain = renumber_letter_items_in_text(plain)
            plain = _renumber_global_paren_items_in_text(plain)
            plain = _normalize_text_paragraph_breaks(plain)
            body = _plain_text_to_method_html(plain) if plain else ""
        parts.append(heading)
        if body:
            parts.append(body)
    merged = _finalize_structured_synr_html("".join(parts))
    return _strip_outline_duplicate_headings_html(merged)


def renumber_dot_num_items_in_text(text: str) -> str:
    """连续 1. / 2. 条目重排为 1..N（含 1.试验箱 无空格写法）。"""
    lines = (text or "").splitlines()
    out: List[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\d+)[\.．、]\s*(.*)$", lines[i].strip())
        if m and m.group(2):
            # 8.2.1 等多级章节号勿当作 1. 2. 列表重排
            if re.match(r"^\d+(?:\.\d+){2,}\s+\S", lines[i].strip()):
                out.append(lines[i])
                i += 1
                continue
            bodies: List[str] = []
            while i < len(lines):
                lm = re.match(r"^(\d+)[\.．、]\s*(.*)$", lines[i].strip())
                if not lm or not lm.group(2):
                    break
                bodies.append(lm.group(2).strip())
                i += 1
            for idx, body in enumerate(bodies, start=1):
                out.append(f"{idx}.{body}")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def renumber_cn_paren_num_items_in_text(text: str) -> str:
    """连续 1）2）条目重排。"""
    lines = (text or "").splitlines()
    out: List[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\d+)[）)]\s*(.+)$", lines[i].strip())
        if m:
            bodies: List[str] = []
            while i < len(lines):
                lm = re.match(r"^(\d+)[）)]\s*(.+)$", lines[i].strip())
                if not lm:
                    break
                bodies.append(lm.group(2).strip())
                i += 1
            for idx, body in enumerate(bodies, start=1):
                out.append(f"{idx}）{body}")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _normalize_latex_segment(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    s = re.sub(r"\\mathrm\s*\{\s*~\s*\}", "", s, flags=re.I)
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    s = re.sub(r"\^\s*\{\s*\\circ\s*\}", r"^\\circ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def format_latex_in_html_content(html: str) -> str:
    """将 $...$ / $$...$$ / ```latex 转为可渲染 LaTeX 文本（前端 KaTeX 识别）。"""
    import html as html_module

    h = str(html or "")
    if not h.strip():
        return h

    def _fence_repl(m: re.Match) -> str:
        inner = _normalize_latex_segment(html_module.unescape(m.group(1) or ""))
        return f"<p>$${inner}$$</p>" if inner else ""

    h = re.sub(
        r"<pre[^>]*>\s*<code[^>]*class=[\"'][^\"']*language-(?:latex|tex|math)[\"'][^>]*>([\s\S]*?)</code>\s*</pre>",
        _fence_repl,
        h,
        flags=re.I,
    )
    h = re.sub(
        r"```(?:latex|tex|math)\s*\n([\s\S]*?)```",
        lambda m: f"\n$${_normalize_latex_segment(m.group(1))}$$\n" if m.group(1).strip() else "",
        h,
        flags=re.I,
    )

    def _inline_latex_repl(m: re.Match) -> str:
        inner = _normalize_latex_segment(m.group(1) or "")
        return f"${inner}$" if inner else m.group(0)

    # 不破坏 HTML 标签：仅处理标签外文本
    parts = re.split(r"(<[^>]+>)", h)
    for i, part in enumerate(parts):
        if part.startswith("<"):
            continue
        part = re.sub(r"\$\$([\s\S]+?)\$\$", lambda m: f"$${_normalize_latex_segment(m.group(1))}$$", part)
        part = re.sub(r"\$([^$\n]+?)\$", _inline_latex_repl, part)
        parts[i] = part
    return "".join(parts)


def _has_top_five_synr_headings_html(html: str) -> bool:
    """是否已是 1–5 五段大标题结构（不宜再全文 renumber_document_sequential）。"""
    h = str(html or "")
    nums = {m.group(1) for m in re.finditer(r'data-section-no="([1-5])"', h, re.I)}
    if len(nums) >= 2:
        return True
    return bool(
        re.search(
            r"<strong>\s*[1-5]\s+(?:试验目的|试验条件|试验方法|试验过程|试验数据处理|结果评)",
            h,
            re.I,
        )
    )


def polish_method_synr_structure(html: str) -> str:
    """标题重编号、（1）/1. 序号连续化、段落结构化（不含溯源）。"""
    h = (html or "").strip()
    if not h:
        return h

    has_outline_nums = bool(
        re.search(r"8\.\d+\.\d+", h) or re.search(r'data-section-no=["\']8\.', h, re.I)
    )
    if has_outline_nums and _TOP_FIVE_SYNR_HEADING_RE.search(h):
        return polish_native_outline_synr(h)

    if _is_structured_synr_html(h) and not re.search(r"^#{1,6}\s", h, re.M):
        if has_outline_nums:
            pass
        else:
            return _finalize_structured_synr_html(h)

    if _synr_content_needs_restructure(h) or _has_markdown_artifacts(h) or has_outline_nums:
        plain = html_to_section_plain(h) if re.search(r"<", h) else h
        plain = _map_gb_outline_headings_to_top_five(plain)
        plain = renumber_outline_text(plain)
        plain = renumber_letter_items_in_text(plain)
        plain = renumber_paren_num_items_in_text(plain)
        plain = renumber_cn_paren_num_items_in_text(plain)
        plain = renumber_dot_num_items_in_text(plain)
        plain = _renumber_global_paren_items_in_text(plain)
        plain = _normalize_text_paragraph_breaks(plain)
        h = _plain_text_to_method_html(plain)

    if _has_top_five_synr_headings_html(h):
        return _finalize_structured_synr_html(_strip_outline_duplicate_headings_html(h))
    return finalize_section_extract_synr(h)


def polish_method_synr_for_editor(
    html: str,
    *,
    full_text: str = "",
    all_tables: Optional[List[Dict[str, Any]]] = None,
    all_figures: Optional[List[Dict[str, Any]]] = None,
    force_restructure: bool = False,
) -> str:
    """详情抽屉终态：结构抛光 + 见/按/照溯源 + LaTeX 规范化。"""
    h = (html or "").strip()
    if not h:
        return h
    if force_restructure or _synr_content_needs_restructure(h):
        h = polish_method_synr_structure(h)
    elif _is_structured_synr_html(h):
        h = _finalize_structured_synr_html(h)
    else:
        h = finalize_section_extract_synr(h)
    if (full_text or "").strip():
        try:
            h = apply_document_trace_to_html(
                h,
                full_text=full_text,
                all_tables=list(all_tables or []),
                all_figures=list(all_figures or []),
            )
        except Exception as exc:
            logger.warning("polish_method_synr trace failed (skipped): %s", exc)
    return format_latex_in_html_content(h)


def expand_gb_method_section_nos(parent_no: str, parent_title: str = "") -> List[str]:
    """勾选「试验条件」等章节时，自动扩展抓取完整方法相关章节（仅经典 GB 结构）。"""
    pno = (parent_no or "").strip().lstrip(".")
    title = (parent_title or "").strip()
    top = pno.split(".")[0] if pno else ""
    # GB/T 38924 分则：第4章为「试验设备和仪表」，勿按经典 GB 扩到 5/6/7/8
    if top == "4" and ("设备" in title or "仪表" in title):
        return [pno] if pno else []
    if top == "4" or "试验条件" in title:
        return ["4", "5", "6", "7", "8"]
    if top == "6" or "试验过程" in title:
        return ["6", "7", "8"]
    if top in ("7", "8") or "结果评定" in title or "试验中断" in title:
        return [top] if top else ([pno] if pno else [])
    return [pno] if pno else []


def _strip_redundant_chapter_heading(html: str, block: str) -> str:
    """去掉与方案大标题重复的国标章标题（4 试验条件 / 6 试验过程 等）。"""
    drop_roots = {
        "sytj": ("3", "4", "5"),
        "sygc": ("5", "6"),
        "jgpd": ("6", "7", "8"),
    }
    roots = drop_roots.get(block) or ()
    out = html or ""
    for root in roots:
        pat = re.compile(
            rf'<p[^>]*class="wjgl-section-heading"[^>]*data-section-no="{re.escape(root)}"[^>]*>.*?</p>',
            re.I | re.S,
        )
        out = pat.sub("", out, count=1)
        plain_pat = re.compile(
            rf'<p[^>]*>\s*<strong>\s*{re.escape(root)}\s+[^<]+</strong>\s*</p>',
            re.I | re.S,
        )
        out = plain_pat.sub("", out, count=1)
        loose_pat = re.compile(
            rf'<p[^>]*>\s*{re.escape(root)}\s+[\u4e00-\u9fff（）()、，\s]{{2,48}}\s*</p>',
            re.I | re.S,
        )
        out = loose_pat.sub("", out, count=1)
    return out.strip()


def _gb_old_to_new_section_no(old_no: str, block: str, *, max_sub_under_4: int = 3) -> str:
    """国标章节号 → 方案编号：4/5→1.x，6→2.x，7/8→3.x。"""
    s = (old_no or "").strip().lstrip(".")
    if not s:
        return s
    parts = s.split(".")
    top = parts[0]
    rest = parts[1:]

    if block == "sytj":
        if top == "4":
            return ".".join(["1"] + rest) if rest else "1"
        if top == "5":
            base = f"1.{max_sub_under_4 + 1}"
            return ".".join([base] + rest) if rest else base
    if block == "sygc" and top == "6":
        return ".".join(["2"] + rest) if rest else "2"
    if block == "jgpd":
        if top == "7":
            tail = ["3", "1"] + rest
            return ".".join(tail)
        if top == "8":
            tail = ["3", "2"] + rest
            return ".".join(tail)
    return s


def _collect_heading_numbers(html: str) -> List[str]:
    nums: List[str] = []
    seen: set = set()
    for m in _WJGL_HEADING_RE.finditer(html or ""):
        sno = m.group(1).strip().lstrip(".")
        if sno and sno not in seen:
            seen.add(sno)
            nums.append(sno)
    if nums:
        return nums
    for m in _PLAIN_HEADING_RE.finditer(html or ""):
        sno = m.group(1).strip().lstrip(".")
        if sno and sno not in seen:
            seen.add(sno)
            nums.append(sno)
    return nums


def _collect_headings_in_order(html: str) -> List[Tuple[int, str, str]]:
    """按文档顺序收集章节标题 (pos, section_no, full_html)。"""
    markers: List[Tuple[int, str, str]] = []
    seen_at: Dict[str, int] = {}
    for m in _WJGL_HEADING_RE.finditer(html or ""):
        sno = m.group(1).strip().lstrip(".")
        markers.append((m.start(), sno, m.group(0)))
        seen_at[sno] = m.start()
    for m in _PLAIN_HEADING_RE.finditer(html or ""):
        sno = m.group(1).strip().lstrip(".")
        if sno in seen_at and abs(seen_at[sno] - m.start()) < 8:
            continue
        markers.append((m.start(), sno, m.group(0)))
    markers.sort(key=lambda x: x[0])
    return markers


def _heading_html_with_no(frag: str, old_no: str, new_no: str, title_text: str) -> str:
    strong_body = f"{new_no} {title_text}".strip()
    out = frag
    if 'data-section-no="' in out:
        out = re.sub(
            rf'data-section-no="{re.escape(old_no)}"',
            f'data-section-no="{new_no}"',
            out,
            count=1,
            flags=re.I,
        )
    return re.sub(
        r">.*?</p>\s*$",
        f"><strong>{strong_body}</strong></p>",
        out,
        count=1,
        flags=re.S,
    )


def _renumber_block_under_scheme_root(html: str, scheme_root: str) -> Tuple[str, Dict[str, str]]:
    """
    GB/T 38924 分则：块内 3.1/4.1/5.1 → scheme_root 下 1.1/1.2/2.1；
    去掉与 h4 大标题重复的章级标题（3/4/5/7 单节）。
    """
    h = (html or "").strip()
    if not h:
        return h, {}

    markers = _collect_headings_in_order(h)
    no_map: Dict[str, str] = {}
    counters = [0, 0, 0, 0, 0, 0]
    depth_1_nos = [sno for _, sno, _ in markers if sno and len(sno.split(".")) == 1]
    single_chapter_banner = len(depth_1_nos) == 1

    for _, old_no, _ in markers:
        parts = old_no.split(".")
        depth = len(parts)
        if depth == 1:
            if single_chapter_banner:
                no_map[old_no] = f"{scheme_root}.1"
            else:
                no_map[old_no] = ""
            continue
        rel = depth - 1
        for i in range(rel, len(counters)):
            counters[i] = 0
        counters[rel - 1] += 1
        new_parts = [scheme_root] + [str(counters[i]) for i in range(rel)]
        no_map[old_no] = ".".join(new_parts)

    def _wjgl_repl(m: re.Match) -> str:
        old_no = m.group(1).strip().lstrip(".")
        new_no = no_map.get(old_no, old_no)
        if new_no == "":
            return ""
        inner = re.sub(r"<[^>]+>", "", m.group(0))
        title_text = re.sub(r"^\d+(?:\.\d+)*\s*", "", inner).strip()
        return _heading_html_with_no(m.group(0), old_no, new_no, title_text)

    def _plain_repl(m: re.Match) -> str:
        old_no = m.group(1).strip().lstrip(".")
        new_no = no_map.get(old_no, old_no)
        if new_no == "":
            return ""
        title_text = (m.group(2) or "").strip()
        return _heading_html_with_no(m.group(0), old_no, new_no, title_text)

    out = _WJGL_HEADING_RE.sub(_wjgl_repl, h)
    out = _PLAIN_HEADING_RE.sub(_plain_repl, out)
    partial = {k: v for k, v in no_map.items() if v}
    return out.strip(), partial


def _looks_like_gb38924_block(html: str) -> bool:
    tops = {_top_chapter_no(n) for n in _collect_heading_numbers(html)}
    if not tops:
        return False
    if tops & {3, 5}:
        return True
    if tops & {4, 5, 7} and not tops & {8, 9}:
        return True
    return False


def _max_subsection_index(section_nums: List[str], root: str) -> int:
    mx = 0
    prefix = root + "."
    for sno in section_nums:
        if sno.startswith(prefix):
            tail = sno[len(prefix):]
            head = tail.split(".")[0]
            if head.isdigit():
                mx = max(mx, int(head))
    return mx


def renumber_gb_method_block_html(html: str, block: str, *, prefer_scheme_sequential: bool = False) -> Tuple[str, Dict[str, str]]:
    """将块内国标标题改为方案编号，并返回 old→new 映射。"""
    h = (html or "").strip()
    if not h:
        return h, {}

    scheme_root = {"sytj": "1", "sygc": "2", "jgpd": "3"}.get(block, "1")
    if prefer_scheme_sequential or _looks_like_gb38924_block(h):
        return _renumber_block_under_scheme_root(h, scheme_root)

    heading_nums = _collect_heading_numbers(h)
    max_sub_4 = _max_subsection_index(heading_nums, "4")
    no_map: Dict[str, str] = {}
    for old in heading_nums:
        no_map[old] = _gb_old_to_new_section_no(old, block, max_sub_under_4=max_sub_4)

    def _heading_repl(m: re.Match) -> str:
        old_no = m.group(1).strip().lstrip(".")
        new_no = no_map.get(old_no, old_no)
        frag = m.group(0)
        frag = re.sub(
            rf'data-section-no="{re.escape(old_no)}"',
            f'data-section-no="{new_no}"',
            frag,
            count=1,
            flags=re.I,
        )
        inner = re.sub(r"<[^>]+>", "", frag)
        title_text = re.sub(r"^\d+(?:\.\d+)*\s*", "", inner).strip()
        strong_body = f"{new_no} {title_text}".strip()
        return re.sub(
            r">.*?</p>\s*$",
            f"><strong>{strong_body}</strong></p>",
            frag,
            count=1,
            flags=re.S,
        )

    out = _WJGL_HEADING_RE.sub(_heading_repl, h)
    out = _PLAIN_HEADING_RE.sub(_heading_repl, out)
    return out, no_map


def _apply_section_ref_map_safe(html: str, no_map: Dict[str, str]) -> str:
    """仅在引用语境中替换章节号，避免误改 1.5kg、3 000m~5 000 m 等数值。"""
    if not html or not no_map:
        return html or ""
    out = html
    for old, new in sorted(no_map.items(), key=lambda x: -len(x[0])):
        if not old or old == new:
            continue
        esc = re.escape(old)

        def _ref_prefix(m: re.Match, _new: str = new) -> str:
            return f"{m.group(1)}{_new}"

        out = re.sub(rf"(同|按|至|见)\s*{esc}(?!\d)", _ref_prefix, out)
        out = out.replace(f"{old}规定", f"{new}规定")
        out = out.replace(f"{old}的要求", f"{new}的要求")
        out = re.sub(rf"(GB/T[\d\.]+-[\d]+中){esc}(?!\d)", rf"\g<1>{new}", out, flags=re.I)
        out = re.sub(rf"(?<![\d\.]){esc}(\s+[\u4e00-\u9fff])", rf"{new}\g<1>", out)
        out = re.sub(rf"(<strong>){esc}(\s+)", rf"\g<1>{new}\g<2>", out, flags=re.I)
    return out


def _should_inline_trace(sec: str) -> bool:
    """仅对具体小节（至少两级编号）做内联溯源，避免「1.1 / 1.2」整章复制。"""
    parts = (sec or "").strip().lstrip(".").split(".")
    return len(parts) >= 2


def _chunk_to_rich_html(chunk: str) -> str:
    """Markdown/纯文本块转 HTML，保留内嵌 <table>。"""
    import html as html_module

    chunk = (chunk or "").strip()
    if not chunk:
        return ""
    if re.search(r"<\s*(p|div|table)\b", chunk, re.I):
        return _wrap_doc_tables(chunk)
    parts: List[str] = []
    pos = 0
    for tbl_m in re.finditer(r"<table[\s\S]*?</table>", chunk, re.I):
        before = chunk[pos:tbl_m.start()].strip()
        if before:
            for para in re.split(r"\n\s*\n+", before):
                p = para.strip()
                if p:
                    parts.append(f"<p>{html_module.escape(p)}</p>")
        parts.append(_wrap_single_table(tbl_m.group(0)))
        pos = tbl_m.end()
    tail = chunk[pos:].strip()
    if tail:
        for para in re.split(r"\n\s*\n+", tail):
            p = para.strip()
            if p:
                parts.append(f"<p>{html_module.escape(p)}</p>")
    return "".join(parts) if parts else _plain_to_html(chunk)


def _repair_table_captions(html: str, tables_by_no: Dict[str, str]) -> str:
    """表N 标题后若无 <table>，从索引补全表格 HTML。"""
    if not html or not tables_by_no:
        return html or ""

    def _inject_cap(m: re.Match) -> str:
        cap_p = m.group(0)
        tno = m.group(1)
        tbl = tables_by_no.get(tno) or tables_by_no.get(str(int(tno)) if tno.isdigit() else tno)
        if not tbl:
            return cap_p
        return f"{cap_p}{_wrap_single_table(tbl)}"

    pat = re.compile(
        r"<p[^>]*>\s*表\s*(\d+)[^<]*</p>(?!\s*<div[^>]*doc-table)",
        re.I,
    )
    out = pat.sub(_inject_cap, html)

    def _fix_runon(m: re.Match) -> str:
        tno = m.group(1)
        rest = (m.group(2) or "").strip()
        tbl = tables_by_no.get(tno) or tables_by_no.get(str(int(tno)) if tno.isdigit() else tno)
        if not tbl or len(rest) < 60:
            return m.group(0)
        cap_m = re.match(r"^([\u4e00-\u9fff（）()、，\sA-Za-z0-9.\-]{2,48})", rest)
        cap_title = cap_m.group(1).strip() if cap_m else rest[:24]
        return f"<p>表{tno} {cap_title}</p>{_wrap_single_table(tbl)}"

    runon_pat = re.compile(
        r"<p[^>]*>\s*表\s*(\d+)\s+([^<]{60,})</p>(?!\s*<div[^>]*doc-table)",
        re.I,
    )
    return runon_pat.sub(_fix_runon, out)


def _merge_tables_index(*indexes: Optional[Dict[str, str]]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for idx in indexes:
        for k, v in (idx or {}).items():
            if v and k not in merged:
                merged[k] = v
    return merged


def _load_document_tables_index(category: str, doc_id: str) -> Dict[str, str]:
    """从整篇 document.html / full_html 建立表号 → 表格 HTML 索引。"""
    if not category or not doc_id:
        return {}
    html_sources: List[str] = []
    try:
        from pdf_extract.mineru_wjgl_service import local_doc_dir

        html_path = local_doc_dir(category, doc_id) / "document.html"
        if html_path.is_file():
            html_sources.append(html_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import load_parse_artifacts

        cached = load_parse_artifacts(category, doc_id) or {}
        fh = str(cached.get("full_html") or "").strip()
        if fh:
            html_sources.append(fh)
    except Exception:
        pass
    out: Dict[str, str] = {}
    for html in html_sources:
        out = _merge_tables_index(out, _index_tables_from_html(html))
        for item in re.finditer(r"<table[\s\S]*?</table>", html or "", re.I):
            tbl = item.group(0).strip()
            plain = re.sub(r"<[^>]+>", " ", tbl)
            tm = re.search(r"表\s*(\d+)", plain, re.I)
            if tm:
                out.setdefault(tm.group(1), tbl)
    return out


def _inject_missing_subsections(html: str, full_text: str, *, roots: Tuple[str, ...] = ("4", "5", "6", "7", "8")) -> str:
    """HTML 切片缺子节标题时，从 full_text 补入（含表格）。"""
    if not full_text.strip():
        return html or ""
    present = set(_collect_heading_numbers(html))
    try:
        from blueprints.FLGL.GetTestMethod.parser import preprocess_merged_section_lines
    except Exception:
        preprocess_merged_section_lines = lambda t: t  # type: ignore

    text = preprocess_merged_section_lines(full_text)
    inserts: List[str] = []
    for m in re.finditer(r"(?m)^(\d+(?:\.\d+){0,5})\s+(\S.+)$", text):
        sno = m.group(1).strip().lstrip(".")
        if sno in present:
            continue
        if sno.split(".")[0] not in roots:
            continue
        chunk = _extract_section_text(text, sno)
        if not chunk:
            continue
        title_line = m.group(0).strip()
        title = re.sub(r"^\d+(?:\.\d+)*\s*", "", title_line).strip()
        aid = "wjgl-sec-" + sno.replace(".", "-")
        inserts.append(
            f'<p id="{aid}" class="wjgl-section-heading" data-section-no="{sno}" '
            f'style="font-weight:600;margin:10px 0 6px"><strong>{sno} {title}</strong></p>'
            f'{_chunk_to_rich_html(chunk)}'
        )
        present.add(sno)
    if not inserts:
        return html
    return (html or "") + "".join(inserts)



def _build_section_text_index(full_text: str) -> Dict[str, str]:
    """从 full_text / Markdown 建立章节正文索引（补齐 HTML 切片缺失的子节）。"""
    if not (full_text or "").strip():
        return {}
    try:
        from blueprints.FLGL.GetTestMethod.parser import preprocess_merged_section_lines
    except Exception:
        preprocess_merged_section_lines = lambda t: t  # type: ignore

    text = preprocess_merged_section_lines(full_text)
    nums: List[str] = []
    seen: set = set()
    for m in re.finditer(r"(?m)^(\d+(?:\.\d+){0,5})\s+\S", text):
        sno = m.group(1).strip().lstrip(".")
        if sno and sno not in seen:
            seen.add(sno)
            nums.append(sno)
    out: Dict[str, str] = {}
    for sno in nums:
        chunk = _extract_section_text(text, sno)
        if not chunk:
            continue
        if re.search(r"<\s*table\b", chunk, re.I):
            out[sno] = _chunk_to_rich_html(chunk)
        else:
            out[sno] = _chunk_to_rich_html(chunk)
    return out


def _merge_section_index(*indexes: Optional[Dict[str, str]]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for idx in indexes:
        for k, v in (idx or {}).items():
            if v and k not in merged:
                merged[k] = v
    return merged


def _build_section_html_index(html: str) -> Dict[str, str]:
    """从完整 HTML 建立 section_no → 正文片段索引。"""
    h = html or ""
    markers: List[Tuple[int, str, int]] = []
    for m in _WJGL_HEADING_RE.finditer(h):
        markers.append((m.start(), m.group(1).strip().lstrip("."), m.end()))
    if not markers:
        return {}
    markers.sort(key=lambda x: x[0])
    out: Dict[str, str] = {}
    for i, (_pos, sec_no, end) in enumerate(markers):
        body_end = markers[i + 1][0] if i + 1 < len(markers) else len(h)
        body = h[end:body_end].strip()
        if body:
            out[sec_no] = body
    return out


def _index_tables_from_html(html: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _TABLE_CAPTION_RE.finditer(html or ""):
        cap = m.group(1)
        tbl = m.group(2).strip()
        tm = re.search(r"表\s*(\d+)", cap, re.I)
        if tm and tbl:
            out[tm.group(1)] = tbl
    for item in re.finditer(r"<table[\s\S]*?</table>", html or "", re.I):
        tbl = item.group(0).strip()
        plain = re.sub(r"<[^>]+>", " ", tbl)
        for tm in re.finditer(r"表\s*(\d+)", plain, re.I):
            out.setdefault(tm.group(1), tbl)
    return out


def _wrap_single_table(html: str) -> str:
    h = (html or "").strip()
    if not h:
        return ""
    if "<table" not in h.lower():
        return h
    if 'class="doc-table"' in h:
        return h
    return f'<div class="doc-table">{h}</div>'


def _lookup_table_html(tables_by_no: Dict[str, str], tno: str) -> str:
    key = (tno or "").replace(" ", "").replace("．", ".")
    return (
        tables_by_no.get(key)
        or tables_by_no.get(key.lstrip("0"))
        or tables_by_no.get(str(int(key)) if key.isdigit() else key)
        or ""
    )


def _normalize_figure_no(raw: str) -> str:
    s = str(raw or "").strip().replace("．", ".").lstrip("0")
    return s or "0"


def _figure_dict_to_html(fig: Any, page_no: int = 0) -> str:
    if not isinstance(fig, dict):
        return ""
    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import _figure_to_html_base64

        return str(_figure_to_html_base64(fig, page_no=page_no) or "").strip()
    except Exception as exc:
        logger.debug("figure to html failed: %s", exc)
        return ""


def _lookup_figure_html(figures_by_no: Dict[str, str], fno: str) -> str:
    key = _normalize_figure_no(fno)
    if not key:
        return ""
    return (
        figures_by_no.get(key)
        or figures_by_no.get(str(int(key)) if key.isdigit() else key)
        or figures_by_no.get(key.zfill(2))
        or ""
    )


def _figure_no_from_caption(text: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", str(text or ""))
    plain = re.sub(r"\s+", " ", plain).strip()
    m = re.search(r"图\s*(\d+)", plain, re.I)
    return m.group(1) if m else ""


def _index_figures_from_html(html: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    h = html or ""
    for m in _FIGURE_CAPTION_HTML_RE.finditer(h):
        cap, fig = m.group(1), m.group(2).strip()
        fno = _figure_no_from_caption(cap)
        if fno and fig:
            out.setdefault(fno, fig)
    for m in re.finditer(r"<figure[\s\S]*?</figure>", h, re.I):
        block = m.group(0).strip()
        if not block:
            continue
        fno = ""
        idx_m = re.search(r'data-figure-index=["\'](\d+)["\']', block, re.I)
        if idx_m:
            fno = idx_m.group(1)
        if not fno:
            alt_m = re.search(r'alt=["\']([^"\']+)["\']', block, re.I)
            if alt_m:
                fno = _figure_no_from_caption(alt_m.group(1))
        if not fno:
            prev = h[max(0, m.start() - 120):m.start()]
            cap_m = re.search(r"图\s*(\d+)", prev, re.I)
            if cap_m:
                fno = cap_m.group(1)
        if fno:
            out.setdefault(fno, block)
    for m in _MD_FIGURE_IMAGE_RE.finditer(h):
        fno = m.group(1)
        url = (m.group(2) or "").strip()
        if not fno or not url:
            continue
        import html as html_module

        cap = html_module.escape(f"图{fno}")
        out.setdefault(
            fno,
            f'<figure style="margin:12px 0;text-align:center;">'
            f'<img src="{url}" alt="{cap}" style="max-width:100%;height:auto;"/>'
            f"</figure>",
        )
    return out


def gather_figures_for_method_trace(parse_cache: Optional[dict]) -> List[Dict[str, Any]]:
    """
    从 parse_cache 全文 HTML / 章节 HTML 建立图号 → HTML 索引，供「见图N」溯源。
    Word 内嵌图 + PDF MinerU 图均可覆盖。
    """
    if not isinstance(parse_cache, dict):
        return []
    html_parts: List[str] = [
        str(parse_cache.get("full_html") or ""),
        str(parse_cache.get("full_markdown") or ""),
    ]
    for sec in parse_cache.get("sections") or []:
        if isinstance(sec, dict):
            html_parts.append(str(sec.get("html") or ""))
    for _sid, slice_html in (parse_cache.get("node_slices") or {}).items():
        html_parts.append(str(slice_html or ""))
    combined = "\n".join(p for p in html_parts if p.strip())
    if not combined.strip():
        return []

    fig_map = _index_figures_from_html(combined)
    seq = 0
    for m in re.finditer(r"<figure[\s\S]*?</figure>", combined, re.I):
        block = m.group(0).strip()
        if not block:
            continue
        fno = _figure_no_from_caption(block)
        if not fno:
            idx_m = re.search(r'data-figure-index=["\'](\d+)["\']', block, re.I)
            if idx_m:
                fno = idx_m.group(1)
        if not fno:
            alt_m = re.search(r'alt=["\']([^"\']+)["\']', block, re.I)
            if alt_m:
                fno = _figure_no_from_caption(alt_m.group(1))
        if not fno:
            seq += 1
            fno = str(seq)
        fno = _normalize_figure_no(fno)
        if fno and fno not in fig_map:
            fig_map[fno] = block

    out: List[Dict[str, Any]] = []
    for fno in sorted(fig_map.keys(), key=lambda x: (len(str(x)), str(x))):
        html = fig_map[fno]
        out.append({
            "figure_index": fno,
            "number": fno,
            "html": html,
            "caption": f"图{fno}",
        })
    return out


def inline_local_asset_images(html: str, *, category: str = "", doc_id: str = "") -> str:
    """将 local-asset / parse-asset / 相对路径图片转为 data URI，编辑框内可显示。"""
    h = str(html or "")
    if not h.strip():
        return h
    if not re.search(r"parse-asset|local-asset|<\s*img\b", h, re.I):
        return h
    try:
        from config import Config
        from pdf_extract.mineru_wjgl_service import resolve_local_parse_asset
        from urllib.parse import parse_qs, unquote, urlparse
        import base64
        import html as html_module
        import mimetypes

        def _file_to_data_uri(fpath) -> str:
            ext = fpath.suffix.lower()
            mime = mimetypes.guess_type(str(fpath))[0] or (
                "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
            )
            raw = fpath.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"

        def _bytes_to_data_uri(raw: bytes, name: str = "") -> str:
            ext = os.path.splitext(name)[1].lower()
            mime = mimetypes.guess_type(name)[0] or (
                "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
            )
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"

        def _rel_from_src(src: str) -> str:
            s = html_module.unescape(str(src or "").strip())
            if not s or s.startswith(("data:", "http://", "https://")):
                return ""
            if "local-asset" in s and ("path=" in s or "amp;path=" in s):
                q = parse_qs(urlparse(s).query)
                rel = unquote((q.get("path") or [""])[0])
                if rel:
                    return rel.replace("\\", "/").lstrip("./")
            if not s.startswith("/"):
                return s.replace("\\", "/").lstrip("./")
            return ""

        def _load_parse_asset_bytes(src: str) -> Optional[bytes]:
            s = html_module.unescape(str(src or "").strip())
            if "parse-asset" not in s:
                return None
            parsed = urlparse(s)
            q = parse_qs(parsed.query)
            cat = unquote((q.get("category") or [category or ""])[0])
            name = unquote((q.get("name") or [""])[0]).replace("\\", "/")
            url_doc = doc_id or ""
            if "/files/" in parsed.path:
                tail = parsed.path.split("/files/", 1)[1]
                url_doc = unquote(tail.split("/parse-asset", 1)[0])
            if not cat or not url_doc or not name or not Config.MINIO_CLIENT:
                return None
            try:
                from blueprints.ZSGL.WJGL1.document_parse_service import _parse_minio_key

                obj_key = _parse_minio_key(cat, url_doc, name)
                resp = Config.MINIO_CLIENT.get_object(Config.MINIO_BUCKET_NAME, obj_key)
                raw = resp.read()
                resp.close()
                resp.release_conn()
                return raw if raw else None
            except Exception as exc:
                logger.debug(
                    "[method-extract] parse-asset inline miss cat=%s doc=%s name=%s: %s",
                    cat, (url_doc or "")[:16], name[:40], exc,
                )
                return None

        def _repl_img(m: re.Match) -> str:
            src = (m.group(2) or "").strip()
            blob = _load_parse_asset_bytes(src)
            if blob:
                try:
                    data_uri = _bytes_to_data_uri(blob, src)
                    return m.group(0).replace(src, data_uri, 1)
                except OSError:
                    pass
            rel = _rel_from_src(src)
            if not rel:
                return m.group(0)
            use_cat, use_doc = category, doc_id
            if not use_cat or not use_doc:
                return m.group(0)
            fpath = resolve_local_parse_asset(use_cat, use_doc, rel)
            if not fpath or not fpath.is_file():
                logger.debug(
                    "[method-extract] inline image miss category=%s doc_id=%s rel=%s",
                    use_cat, (use_doc or "")[:16], rel,
                )
                return m.group(0)
            try:
                data_uri = _file_to_data_uri(fpath)
                return m.group(0).replace(src, data_uri, 1)
            except OSError:
                return m.group(0)

        return re.sub(r'src=(["\'])([^"\']+)\1', _repl_img, h, flags=re.I)
    except Exception as exc:
        logger.debug("[method-extract] inline_local_asset_images skipped: %s", exc)
        return h


def _index_figures_from_text(text: str) -> Dict[str, str]:
    return _index_figures_from_html(text or "")


def _index_figures_from_list(all_figures: List[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for idx, fig in enumerate(all_figures or [], start=1):
        if not isinstance(fig, dict):
            continue
        page_no = int(fig.get("page") or fig.get("page_number") or 0)
        html = str(fig.get("html") or "").strip()
        if not html:
            html = _figure_dict_to_html(fig, page_no=page_no)
        if not html:
            continue
        fno = _figure_no_from_caption(
            str(fig.get("caption") or fig.get("description") or fig.get("alt") or ""),
        )
        if not fno:
            fno = str(fig.get("figure_index") or fig.get("number") or idx)
        fno = _normalize_figure_no(fno)
        if fno:
            out.setdefault(fno, html)
    return out


def _merge_figures_index(*indexes: Optional[Dict[str, str]]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for idx in indexes:
        for k, v in (idx or {}).items():
            if v and k not in merged:
                merged[k] = v
    return merged


def _index_figures(
    all_figures: Optional[List[Dict[str, Any]]] = None,
    *,
    html: str = "",
    full_text: str = "",
) -> Dict[str, str]:
    out: Dict[str, str] = {}
    out = _merge_figures_index(
        out,
        _index_figures_from_list(list(all_figures or [])),
        _index_figures_from_html(html),
        _index_figures_from_text(full_text),
    )
    return out


def _load_document_figures_index(category: str, doc_id: str) -> Dict[str, str]:
    if not category or not doc_id:
        return {}
    html_sources: List[str] = []
    try:
        from pdf_extract.mineru_wjgl_service import resolve_local_doc_dir

        local_dir = resolve_local_doc_dir(category, doc_id)
        html_path = local_dir / "document.html"
        if html_path.is_file():
            html_sources.append(html_path.read_text(encoding="utf-8", errors="replace"))
        md_path = local_dir / "document.md"
        if md_path.is_file():
            html_sources.append(md_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import load_parse_artifacts

        cached = load_parse_artifacts(category, doc_id) or {}
        fh = str(cached.get("full_html") or "").strip()
        fm = str(cached.get("full_markdown") or "").strip()
        if fh:
            html_sources.append(fh)
        if fm:
            html_sources.append(fm)
        page_figs: List[Dict[str, Any]] = []
        for page in cached.get("pages") or []:
            if isinstance(page, dict):
                page_figs.extend(list(page.get("figures") or []))
        if page_figs:
            return _merge_figures_index(
                _index_figures_from_list(page_figs),
                *(_index_figures_from_html(src) for src in html_sources),
            )
    except Exception:
        pass
    out: Dict[str, str] = {}
    for src in html_sources:
        out = _merge_figures_index(out, _index_figures_from_html(src))
    return out


def _collect_figure_refs_in_plain(text: str) -> List[str]:
    """从一句正文中提取需溯源的图号（去重保序）。"""
    inner = str(text or "")
    if not inner.strip():
        return []
    if _FIGURE_CAPTION_LINE_RE.match(inner.strip()):
        return []
    found: List[str] = []
    seen: set = set()
    for pat in (_SEE_FIGURE_RE, _AS_FIGURE_RE):
        for m in pat.finditer(inner):
            fno = _normalize_figure_no(m.group(1))
            if fno and fno not in seen:
                seen.add(fno)
                found.append(fno)
    for m in _PLAIN_FIGURE_REF_RE.finditer(inner):
        start = m.start()
        if start >= 1 and inner[start - 1] in "见如":
            continue
        fno = _normalize_figure_no(m.group(1))
        if fno and fno not in seen:
            seen.add(fno)
            found.append(fno)
    return found


def _lookup_section_chunk(
    sec: str,
    *,
    section_index: Dict[str, str],
    full_text: str,
) -> str:
    s = (sec or "").strip().replace("附录", "").lstrip(".")
    if not s or not _should_inline_trace(s):
        return ""
    chunk = section_index.get(s)
    if not chunk and full_text.strip():
        plain_chunk = _extract_section_text(full_text, s)
        chunk = _chunk_to_rich_html(plain_chunk) if plain_chunk else ""
    return chunk or ""


def resolve_see_references_html(
    html: str,
    *,
    section_index: Optional[Dict[str, str]] = None,
    tables_by_no: Optional[Dict[str, str]] = None,
    figures_by_no: Optional[Dict[str, str]] = None,
    full_text: str = "",
) -> str:
    """在 HTML 段落内联溯源：见/同表N、见/同/按/照 4.x、见图N/如图N/图N。"""
    h = html or ""
    if not h.strip():
        return h

    section_index = dict(section_index or {})
    tables_by_no = dict(tables_by_no or {})
    figures_by_no = dict(figures_by_no or {})
    if full_text.strip() and not section_index:
        plain_idx = {}
        for m in re.finditer(r"^(\d+(?:\.\d+){0,5})\s+(.+)$", full_text, re.M):
            sno = m.group(1).strip().lstrip(".")
            chunk = _extract_section_text(full_text, sno)
            if chunk:
                plain_idx[sno] = _plain_to_html(chunk)
        section_index.update(plain_idx)

    def _append_inline(para_html: str, label: str, body_html: str) -> str:
        body = (body_html or "").strip()
        if not body:
            return para_html
        wrapped = _wrap_single_table(body) if "<table" in body.lower() else body
        return (
            f'{para_html}'
            f'<div class="see-ref-inline"><p><em>【溯源：{label}】</em></p>{wrapped}</div>'
        )

    last_sec_ref = ""
    last_table_ref = ""

    def _process_paragraph(m: re.Match) -> str:
        nonlocal last_sec_ref, last_table_ref
        para = m.group(0)
        inner_plain = re.sub(r"<[^>]+>", " ", m.group(2))
        out = para
        seen: set = set()

        for tm in _SEE_TABLE_RE.finditer(inner_plain):
            tno = tm.group(1).replace(" ", "").replace("．", ".")
            if re.match(r"^[A-Za-z]", tno):
                continue
            key = f"table:{tno}"
            if key in seen:
                continue
            seen.add(key)
            th = _lookup_table_html(tables_by_no, tno)
            if th:
                last_table_ref = tno
                out = _append_inline(out, f"表{tno}", th)

        for tm in _SAME_TABLE_RE.finditer(inner_plain):
            tno = tm.group(1).replace(" ", "").replace("．", ".")
            if re.match(r"^[A-Za-z]", tno):
                continue
            key = f"same-table:{tno}"
            if key in seen:
                continue
            seen.add(key)
            th = _lookup_table_html(tables_by_no, tno)
            if th:
                last_table_ref = tno
                out = _append_inline(out, f"同表{tno}", th)

        for fno in _collect_figure_refs_in_plain(inner_plain):
            key = f"figure:{fno}"
            if key in seen:
                continue
            seen.add(key)
            fh = _lookup_figure_html(figures_by_no, fno)
            if fh:
                out = _append_inline(out, f"图{fno}", fh)

        for pat, label_prefix in (
            (_SEE_SECTION_RE, "见"),
            (_SAME_SECTION_RE, "同"),
            (_BY_SECTION_RE, "按"),
            (_ZHAO_SECTION_RE, "照"),
            (_TO_SECTION_REF_RE, "至"),
        ):
            for sm in pat.finditer(inner_plain):
                sec = sm.group(1).strip().replace("附录", "").lstrip(".")
                if not _should_inline_trace(sec):
                    continue
                key = f"sec:{sec}"
                if key in seen:
                    continue
                seen.add(key)
                chunk = _lookup_section_chunk(sec, section_index=section_index, full_text=full_text)
                if chunk:
                    last_sec_ref = sec
                    label = f"{label_prefix}{sec}" if label_prefix else sec
                    out = _append_inline(out, label, chunk)

        if _SAME_CONTEXT_RE.search(inner_plain):
            key = "same-context"
            if key not in seen:
                seen.add(key)
                if last_sec_ref:
                    chunk = _lookup_section_chunk(
                        last_sec_ref, section_index=section_index, full_text=full_text,
                    )
                    if chunk:
                        out = _append_inline(out, f"同前（{last_sec_ref}）", chunk)
                elif last_table_ref:
                    th = _lookup_table_html(tables_by_no, last_table_ref)
                    if th:
                        out = _append_inline(out, f"同前（表{last_table_ref}）", th)

        if _SAME_CHAPTER_RE.search(inner_plain) and last_sec_ref:
            key = "same-chapter"
            if key not in seen:
                seen.add(key)
                chunk = _lookup_section_chunk(
                    last_sec_ref, section_index=section_index, full_text=full_text,
                )
                if chunk:
                    out = _append_inline(out, f"同本章（{last_sec_ref}）", chunk)

        for am in _SEE_APPENDIX_RE.finditer(inner_plain):
            ap = am.group(1).upper()
            key = f"ap:{ap}"
            if key in seen:
                continue
            seen.add(key)
            chunk = _extract_section_text(full_text, f"附录{ap}") or _extract_section_text(full_text, "", f"附录 {ap}")
            if chunk:
                out = _append_inline(out, f"见附录{ap}", _plain_to_html(chunk))

        for em in _EXPLICIT_SEC_REF_RE.finditer(inner_plain):
            last_sec_ref = em.group(1).strip().lstrip(".")
        for etm in _EXPLICIT_TABLE_REF_RE.finditer(inner_plain):
            last_table_ref = etm.group(1).replace(" ", "").replace("．", ".")

        return out

    return re.sub(r"(<p[^>]*>)([\s\S]*?)(</p>)", _process_paragraph, h, flags=re.I)


def apply_document_trace_to_html(
    html: str,
    *,
    full_text: str = "",
    all_tables: Optional[List[Dict[str, Any]]] = None,
    all_figures: Optional[List[Dict[str, Any]]] = None,
    category: str = "",
    doc_id: str = "",
    inline_section_refs: bool = True,
) -> str:
    """
    对方法正文 HTML 做全文级溯源（见/按/照/同/至 + 见表N + 见图N/如图N/图N）。
    LLM 抽取与手动选章拼接均可复用。
    """
    h = str(html or "").strip()
    if not h:
        return h
    tables_map = _index_tables(all_tables or [], h)
    figures_map = _index_figures(all_figures or [], html=h, full_text=full_text)
    if category and doc_id:
        tables_map = _merge_tables_index(tables_map, _load_document_tables_index(category, doc_id))
        figures_map = _merge_figures_index(figures_map, _load_document_figures_index(category, doc_id))
    section_index: Dict[str, str] = {}
    trace_full_text = ""
    if inline_section_refs and (full_text or "").strip():
        section_index = _build_section_text_index(full_text)
        trace_full_text = full_text
    traced = resolve_see_references_html(
        h,
        section_index=section_index,
        tables_by_no=tables_map,
        figures_by_no=figures_map,
        full_text=trace_full_text,
    )
    return _repair_table_captions(traced, tables_map)


def finalize_method_blocks(
    blocks: Dict[str, Any],
    *,
    renumber: bool = True,
    prefer_scheme_sequential: bool = False,
) -> Dict[str, Any]:
    """重编号三块内容，并统一替换交叉引用章节号。"""
    global_map: Dict[str, str] = {}
    renumbered: Dict[str, str] = {}

    for key in ("sytj", "sygc", "jgpd"):
        blk = blocks.get(key) or {}
        raw = str(blk.get("text") or "")
        cleaned = _strip_redundant_chapter_heading(raw, key) if renumber else raw
        if renumber:
            new_html, partial = renumber_gb_method_block_html(
                cleaned, key, prefer_scheme_sequential=prefer_scheme_sequential,
            )
            renumbered[key] = new_html
            global_map.update(partial)
        else:
            renumbered[key] = cleaned

    out: Dict[str, Any] = {}
    for key in ("sytj", "sygc", "jgpd"):
        blk = dict(blocks.get(key) or {})
        text = _apply_section_ref_map_safe(renumbered.get(key) or "", global_map) if renumber else (renumbered.get(key) or "")
        blk["text"] = _wrap_doc_tables(text)
        blk["tables_html"] = []
        out[key] = blk
    return out


def _wrap_doc_tables(html: str) -> str:
    """为裸 table 包 doc-table，便于前端样式一致。"""
    out = html or ""

    def _wrap(m: re.Match) -> str:
        tbl = m.group(0)
        if 'class="doc-table"' in out[max(0, m.start() - 40):m.start()]:
            return tbl
        return f'<div class="doc-table">{tbl}</div>'

    return re.sub(r"<table[\s\S]*?</table>", _wrap, out, flags=re.I)


def _dedupe_table_paragraphs_in_html(html: str) -> str:
    """表格已结构化时，去掉与其重复的整段段落文本（常见于 OCR 双份输出）。"""
    h = html or ""
    if not re.search(r"<\s*table\b", h, re.I):
        return h
    try:
        from blueprints.FLGL.GetTestMethod.parser import _strip_table_tags
        from blueprints.ZSGL.WJGL1.table_extract import looks_like_table_ocr_line
    except Exception:
        return h

    tables_html = re.findall(r"<table[\s\S]*?</table>", h, re.I)
    cell_frags: set = set()
    for th in tables_html:
        plain_tbl = _strip_table_tags(th)
        for tok in re.split(r"[\s\u3000|]+", plain_tbl):
            t = tok.strip()
            if len(t) >= 4:
                cell_frags.add(t)

    def _para_repl(m: re.Match) -> str:
        inner = m.group(2)
        if re.search(r"<\s*table\b", inner, re.I):
            return m.group(0)
        plain = re.sub(r"<[^>]+>", " ", inner)
        plain = re.sub(r"\s+", " ", plain).strip()
        if not plain or len(plain) < 24:
            return m.group(0)
        if looks_like_table_ocr_line(plain, aggressive=True):
            return ""
        if cell_frags and sum(1 for frag in cell_frags if frag in plain) >= 3:
            return ""
        return m.group(0)

    return re.sub(r"(<p[^>]*>)([\s\S]*?)(</p>)", _para_repl, h, flags=re.I)


def renumber_outline_text(text: str) -> str:
    """将正文中的数字标题行重排为 1 / 1.1 / 2. …（与 renumber_document_sequential 一致）。"""
    lines = (text or "").splitlines()
    if not lines:
        return text or ""

    heading_lines: List[Tuple[int, str, str]] = []
    for i, ln in enumerate(lines):
        m = _SECTION_LINE_RE.match(ln.strip())
        if m:
            heading_lines.append((i, m.group(1), m.group(2)))

    if not heading_lines:
        return text or ""

    min_depth = min(len(no.split(".")) for _, no, _ in heading_lines)
    counters = [0, 0, 0, 0, 0, 0]
    renumber_map: Dict[int, str] = {}

    for line_i, old_no, title in heading_lines:
        parts = old_no.split(".")
        depth = len(parts) - min_depth + 1
        if depth < 1:
            depth = 1
        for j in range(depth, len(counters)):
            counters[j] = 0
        counters[depth - 1] += 1
        new_no = ".".join(str(counters[j]) for j in range(depth))
        renumber_map[line_i] = f"{new_no} {title.strip()}"

    out: List[str] = []
    for i, ln in enumerate(lines):
        out.append(renumber_map.get(i, ln))
    return "\n".join(out)


def renumber_outline_html(html: str) -> str:
    """HTML 内段落标题重编号。"""
    h = str(html or "")
    if not h.strip():
        return h

    def repl(m: re.Match) -> str:
        title = m.group(2).strip()
        return f"<p><strong>{m.group(1)} {title}</strong></p>"

    pat = re.compile(
        r"<p>\s*<strong>\s*(\d+(?:\.\d+){0,5})\s+([^<]+?)\s*</strong>\s*</p>",
        re.I | re.S,
    )
    plain_parts = re.findall(r"<p[^>]*>([^<]+)</p>", h, re.I)
    if plain_parts:
        joined = "\n".join(re.sub(r"<[^>]+>", "", p) for p in plain_parts)
        renumbered = renumber_outline_text(joined)
        if renumbered != joined:
            blocks = [f"<p>{ln}</p>" if ln.strip() else "" for ln in renumbered.splitlines()]
            return "".join(b for b in blocks if b)
    return pat.sub(lambda m: repl(m), h)


_LETTER_ITEM_LINE_RE = re.compile(
    r"^(?:[（(])?([a-zA-Zａ-ｚＡ-Ｚ])[）)]?[\.、\)）]\s*(.+)$"
)
_PAREN_NUM_ITEM_LINE_RE = re.compile(r"^[（(](\d+)[）)]\s*(.+)$")
_SECTION_EXTRACT_SOURCES = frozenset({
    "sections_selected",
    "sections",
    "sections_file",
    "sections_llm",
    "sections_llm_structure",
    "sections_file_llm",
    "sections_whole_chapter",
})


def renumber_letter_items_in_text(text: str) -> str:
    """连续字母条目 a) b) c) 重排为连续序号，修复原文缺 b) 等跳号。"""
    lines = (text or "").splitlines()
    out: List[str] = []
    i = 0
    while i < len(lines):
        m = _LETTER_ITEM_LINE_RE.match(lines[i].strip())
        if m:
            bodies: List[str] = []
            while i < len(lines):
                lm = _LETTER_ITEM_LINE_RE.match(lines[i].strip())
                if not lm:
                    break
                bodies.append(lm.group(2).strip())
                i += 1
            for idx, body in enumerate(bodies):
                letter = chr(ord("a") + idx)
                out.append(f"{letter}) {body}")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def renumber_paren_num_items_in_text(text: str) -> str:
    """连续（1）（2）条目重排为 1..N，修复跳号。"""
    lines = (text or "").splitlines()
    out: List[str] = []
    i = 0
    while i < len(lines):
        m = _PAREN_NUM_ITEM_LINE_RE.match(lines[i].strip())
        if m:
            bodies: List[str] = []
            while i < len(lines):
                lm = _PAREN_NUM_ITEM_LINE_RE.match(lines[i].strip())
                if not lm:
                    break
                bodies.append(lm.group(2).strip())
                i += 1
            for idx, body in enumerate(bodies, start=1):
                out.append(f"（{idx}）{body}")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _html_fragment_to_plain(fragment: str) -> str:
    import html as html_module

    s = fragment or ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p>\s*", "\n\n", s, flags=re.I)
    s = re.sub(r"</h[1-6]>\s*", "\n\n", s, flags=re.I)
    s = re.sub(r"</li>\s*", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_module.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def content_has_embedded_figure(body: str) -> bool:
    """正文是否含内嵌图（HTML figure/img、Markdown data URI 或 MinIO parse-asset）。"""
    s = str(body or "")
    return bool(
        re.search(
            r"<\s*figure\b|<\s*img[\s/>]|!\[[^\]]*\]\(\s*data:image|/parse-asset\?[^)\s\"']*figures/",
            s,
            re.I,
        )
    )


def html_to_section_plain(html: str) -> str:
    """HTML → 纯文本；表格保留为 HTML 块，避免内容在标签剥离时丢失。"""
    h = (html or "").strip()
    if not h:
        return ""
    if not re.search(r"<\s*(p|div|table|h[1-6]|ul|ol|li|br|strong)\b", h, re.I):
        return h

    parts: List[str] = []
    pos = 0
    block_re = re.compile(
        r"(?:<table[\s\S]*?</table>|<figure[\s\S]*?</figure>|"
        r"<\s*img[\s\S]*?/?>|"
        r'<div[^>]*class="[^"]*see-ref-inline[^"]*"[^>]*>[\s\S]*?</div>)',
        re.I,
    )
    for m in block_re.finditer(h):
        before = h[pos:m.start()]
        plain_before = _html_fragment_to_plain(before)
        if plain_before:
            parts.append(plain_before)
        parts.append(m.group(0).strip())
        pos = m.end()
    tail = h[pos:]
    plain_tail = _html_fragment_to_plain(tail)
    if plain_tail:
        parts.append(plain_tail)
    return "\n\n".join(p for p in parts if p.strip())


def _normalize_text_paragraph_breaks(text: str) -> str:
    """标题、字母条目、括号编号前补空行，正文段落用空行分隔。"""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: List[str] = []
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue
        is_break_before = bool(
            out
            and out[-1] != ""
            and (
                _SECTION_LINE_RE.match(stripped)
                or _LETTER_ITEM_LINE_RE.match(stripped)
                or _PAREN_NUM_ITEM_LINE_RE.match(stripped)
                or re.match(r"^[（(][一二三四五六七八九十百零\d]+[）)]", stripped)
            )
        )
        if is_break_before:
            out.append("")
        out.append(stripped)
    merged = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", merged).strip()


def _renumber_markdown_headings(md: str) -> str:
    """Markdown # 标题行重编号（保留 # 层级）。"""
    lines = (md or "").splitlines()
    heading_rows: List[Tuple[int, str, str, str]] = []
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(\d+(?:\.\d+)*)\s+(.+)$", ln.strip())
        if m:
            heading_rows.append((i, m.group(1), m.group(2), m.group(3).strip()))
    if not heading_rows:
        return md
    min_depth = min(len(old_no.split(".")) for _, _, old_no, _ in heading_rows)
    counters = [0, 0, 0, 0, 0, 0]
    no_map: Dict[str, str] = {}
    for _, _, old_no, _ in heading_rows:
        parts = old_no.split(".")
        depth = len(parts) - min_depth + 1
        if depth < 1:
            depth = 1
        for j in range(depth, len(counters)):
            counters[j] = 0
        counters[depth - 1] += 1
        no_map[old_no] = ".".join(str(counters[j]) for j in range(depth))
    out = list(lines)
    for i, hashes, old_no, title in heading_rows:
        new_no = no_map.get(old_no, old_no)
        out[i] = f"{hashes} {new_no} {title}"
    return "\n".join(out)


_METHOD_SUBSECTION_KEYWORDS: Tuple[str, ...] = (
    "试验环境和条件要求",
    "试验环境及条件要求",
    "试验环境条件",
    "数据处理方法",
    "试验数据处理",
    "试验结果评定",
    "试验结果评判",
    "试验结果判定",
    "试验目的",
    "试验条件",
    "试验方法",
    "试验过程",
    "试验步骤",
    "试验要求",
    "试验设备",
    "试验仪器",
    "结果评定",
    "结果评判",
    "结果判定",
    "试验环境",
)

_KEYWORDS_ALT_PATTERN = "|".join(re.escape(k) for k in _METHOD_SUBSECTION_KEYWORDS)

_GLUED_KEYWORD_HEADING_RE = re.compile(
    rf"^(?:#{{1,6}}\s+)?"
    rf"(?:(\d+(?:\.\d+)*)\s*)?"
    rf"({_KEYWORDS_ALT_PATTERN})"
    rf"(?:[：:\s、\-—]+(.*)|(.+))?$",
    re.I,
)

_KEYWORD_ONLY_LINE_RE = re.compile(
    rf"^(?:#{{1,6}}\s+)?(?:(\d+(?:\.\d+)*)\s*)?({_KEYWORDS_ALT_PATTERN})\s*$",
    re.I,
)

_CN_UPPER_DIGITS = "零一二三四五六七八九"


def _int_to_cn_upper_numeral(num: int) -> str:
    if num <= 0:
        return str(num)
    if num < 10:
        return _CN_UPPER_DIGITS[num]
    if num == 10:
        return "十"
    if num < 20:
        tail = _CN_UPPER_DIGITS[num % 10] if num % 10 else ""
        return f"十{tail}"
    if num < 100:
        tens, ones = divmod(num, 10)
        tens_part = f"{_CN_UPPER_DIGITS[tens]}十"
        ones_part = _CN_UPPER_DIGITS[ones] if ones else ""
        return f"{tens_part}{ones_part}"
    return str(num)


def _cn_paren_heading(idx: int, title: str) -> str:
    return f"（{_int_to_cn_upper_numeral(idx)}）{title.strip()}"


def _canonical_subsection_keyword(text: str) -> str:
    t = re.sub(r"\s+", "", str(text or ""))
    for kw in _METHOD_SUBSECTION_KEYWORDS:
        if t == kw or t.endswith(kw):
            return kw
    return str(text or "").strip()


def _split_glued_keyword_subheading_line(line: str) -> List[str]:
    stripped = (line or "").strip()
    if not stripped:
        return []

    only = _KEYWORD_ONLY_LINE_RE.match(stripped)
    if only:
        return [only.group(2).strip()]

    m = _GLUED_KEYWORD_HEADING_RE.match(stripped)
    if not m:
        return [stripped]

    keyword = (m.group(2) or "").strip()
    rest = (m.group(3) or m.group(4) or "").strip()
    rest = re.sub(r"^[：:\s、\-—]+", "", rest)

    if not rest:
        return [keyword]

    # 正文以关键词开头且紧跟标点/空格时视为粘连标题
    if rest.startswith(keyword):
        body = rest[len(keyword):].lstrip("：:、-— \t")
        if body:
            return [keyword, body]
        return [keyword]

    if len(rest) <= 2 and rest in "：:":
        return [keyword]

    return [keyword, rest]


def split_glued_method_subsection_lines(text: str) -> str:
    """将小标题与正文粘连的行拆成独立行。"""
    out: List[str] = []
    for ln in (text or "").replace("\r\n", "\n").split("\n"):
        if not ln.strip():
            if out and out[-1] != "":
                out.append("")
            continue
        stripped = re.sub(r"^#{1,6}\s+", "", ln.strip())
        parts: List[str] = []
        hash_m = re.match(r"^(#{1,6}\s+\d+(?:\.\d+)*\s+)(.+)$", ln.strip())
        if hash_m:
            prefix, rest = hash_m.group(1), hash_m.group(2).strip()
            for kw in _METHOD_SUBSECTION_KEYWORDS:
                if rest.startswith(kw):
                    tail = rest[len(kw):].lstrip("：: \t")
                    parts = [f"{prefix}{kw}".strip()]
                    if tail:
                        parts.append(tail)
                    break
            if not parts:
                tm = re.match(r"^([\u4e00-\u9fff]{2,12}试验)(.+)$", rest)
                if tm and len(tm.group(2).strip()) >= 4:
                    parts = [f"{prefix}{tm.group(1)}".strip(), tm.group(2).strip()]
        if not parts:
            parts = _split_glued_keyword_subheading_line(stripped)
            if len(parts) == 1 and parts[0] != stripped and not ln.strip().startswith("#"):
                parts = [stripped]
        out.extend(parts)
    return "\n".join(out)


def _line_is_subsection_heading_candidate(line: str) -> Optional[str]:
    stripped = (line or "").strip()
    if not stripped:
        return None
    stripped = re.sub(r"^#{1,6}\s+", "", stripped)
    stripped = re.sub(r"^[（(][一二三四五六七八九十百零\d]+[）)]\s*", "", stripped)
    stripped = re.sub(r"^\d+(?:\.\d+)*\s*", "", stripped).strip()
    only = _KEYWORD_ONLY_LINE_RE.match(stripped)
    if only:
        return only.group(2).strip()
    for kw in _METHOD_SUBSECTION_KEYWORDS:
        if stripped == kw:
            return kw
    m = _GLUED_KEYWORD_HEADING_RE.match(stripped)
    if m and not (m.group(3) or m.group(4) or "").strip():
        return m.group(2).strip()
    return None


def normalize_method_subsection_headings(text: str) -> str:
    """
    识别试验目的/试验条件/试验方法等关键词为小标题，
    重排为（一）（二）…，并避免正文中重复关键词被误拆。
    """
    raw = split_glued_method_subsection_lines(text or "")
    lines = raw.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    heading_idx = 0
    seen_heading_titles: set = set()

    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue

        keyword = _line_is_subsection_heading_candidate(stripped)
        if keyword:
            canon = _canonical_subsection_keyword(keyword)
            glued_parts = _split_glued_keyword_subheading_line(stripped)
            body_tail = ""
            if len(glued_parts) >= 2 and glued_parts[0] == keyword:
                body_tail = "\n".join(glued_parts[1:]).strip()

            if canon in seen_heading_titles and not body_tail:
                continue

            heading_idx += 1
            seen_heading_titles.add(canon)
            out.append(_cn_paren_heading(heading_idx, canon))
            if body_tail:
                out.append(body_tail)
            continue

        # 数字大纲节（8.2.1 试验目的）→ 仅保留关键词作小标题
        gb_m = _GB_OUTLINE_SECTION_LINE_RE.match(stripped)
        if gb_m:
            title = gb_m.group(2).strip()
            rest = (gb_m.group(3) or "").strip()
            rest = re.sub(r"^[：:\s、\-—]+", "", rest)
            canon = _canonical_subsection_keyword(title)
            if canon in _SYNR_TOP_SECTION_NUM or canon in _METHOD_SUBSECTION_KEYWORDS:
                if canon not in seen_heading_titles:
                    heading_idx += 1
                    seen_heading_titles.add(canon)
                    out.append(_cn_paren_heading(heading_idx, canon))
                if rest:
                    out.append(rest)
                continue

        gm = re.match(
            r"^(?:#{1,6}\s+)?(\d+(?:\.\d+)*)\s+([\u4e00-\u9fff（）()、，\sA-Za-z0-9.\-]{2,80})\s*$",
            stripped,
        )
        if gm:
            heading_idx += 1
            out.append(_cn_paren_heading(heading_idx, gm.group(2).strip()))
            continue

        out.append(stripped)

    return "\n".join(out)


def renumber_remaining_headings_to_cn_paren(text: str) -> str:
    """将其余 ## / 数字节标题 重排为（一）（二）…（关键词标题已由 normalize_method_subsection_headings 处理）。"""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: List[str] = []
    idx = 0
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue
        if re.match(r"^[（(][一二三四五六七八九十百零\d]+[）)]", stripped):
            out.append(stripped)
            continue
        if _line_is_subsection_heading_candidate(stripped):
            out.append(stripped)
            continue
        m = re.match(
            r"^(?:#{1,6}\s+)?(\d+(?:\.\d+)*)\s+([\u4e00-\u9fff（）()、，\sA-Za-z0-9.\-]{2,80})\s*$",
            stripped,
        )
        if m:
            idx += 1
            out.append(_cn_paren_heading(idx, m.group(2).strip()))
            continue
        out.append(stripped)
    return "\n".join(out)


def _normalize_for_dedupe_compare(text: str) -> str:
    s = re.sub(r"<[^>]+>", "", text or "")
    s = re.sub(r"\s+", "", s)
    return s.lower()


def dedupe_repeated_document_text(text: str) -> str:
    """去掉整段正文前后重复粘贴（常见于章节切片重复合并）。"""
    s = (text or "").strip()
    if len(s) < 120:
        return s

    norm = _normalize_for_dedupe_compare(s)
    half = len(norm) // 2
    if half >= 60 and norm[:half] == norm[half:half * 2]:
        # 精确前半重复
        raw_half = len(s) // 2
        split_at = s.rfind("\n\n", 0, raw_half + 80)
        if split_at <= 0:
            split_at = raw_half
        return s[:split_at].strip()

    # 模糊：较长段落在文中出现两次
    probe = max(80, min(400, len(s) // 3))
    head = _normalize_for_dedupe_compare(s[:probe])
    if head and norm.count(head) >= 2:
        pos = norm.find(head, len(head))
        if pos > 0 and pos >= len(norm) // 2 - 40:
            raw_pos = s.find(s[: min(probe, len(s))].strip()[:40])
            if raw_pos > 40:
                return s[:raw_pos].strip()

    return s


def finalize_section_extract_markdown(md: str) -> str:
    """按章提取终态 SYNR（GFM Markdown）：重编号、序号连续化，保留表格块。"""
    from blueprints.ZSGL.WJGL1.text_normalize import clean_document_text

    raw = clean_document_text(md or "")
    if not raw.strip():
        return ""
    if re.search(r"<\s*(p|div|table|h[1-6]|ul|ol|li|br|strong)\b", raw, re.I):
        raw = html_to_section_plain(raw)
    raw = _renumber_markdown_headings(raw)
    raw = renumber_letter_items_in_text(raw)
    raw = renumber_paren_num_items_in_text(raw)
    return _normalize_text_paragraph_breaks(raw)


def apply_section_extract_trace_markdown(
    md: str,
    *,
    full_text: str = "",
    all_tables: Optional[List[Dict[str, Any]]] = None,
    all_figures: Optional[List[Dict[str, Any]]] = None,
    category: str = "",
    doc_id: str = "",
) -> str:
    """按章提取：在 Markdown 正文上内联溯源，保留 see-ref-inline HTML 块。"""
    base = finalize_section_extract_markdown(md)
    if not base.strip():
        return base
    if not (full_text or "").strip() and not (all_tables or all_figures) and not (category and doc_id):
        return base
    html = _plain_text_to_method_html(base)
    html = apply_document_trace_to_html(
        html,
        full_text=full_text,
        all_tables=list(all_tables or []),
        all_figures=list(all_figures or []),
        category=category,
        doc_id=doc_id,
    )
    return _normalize_text_paragraph_breaks(html_to_section_plain(html))


def apply_section_extract_editor_synr(
    synr: str,
    *,
    full_text: str = "",
    all_tables: Optional[List[Dict[str, Any]]] = None,
    all_figures: Optional[List[Dict[str, Any]]] = None,
    category: str = "",
    doc_id: str = "",
    document_html: str = "",
) -> str:
    """编辑框展示：保持 HTML 章节结构，仅重编号与可选溯源（不去重、不 plain 化）。"""
    s = (synr or "").strip()
    if not s:
        return s
    is_html = bool(re.search(r"<\s*(p|div|table|h[1-6]|ul|ol|li|figure|img|strong)\b", s, re.I))
    if is_html:
        work = s
        doc_html = str(document_html or "").strip()
        if (full_text or "").strip() or all_tables or all_figures or doc_html or (category and doc_id):
            try:
                figures_for_trace = list(all_figures or [])
                if doc_html:
                    figures_for_trace = figures_for_trace + gather_figures_for_method_trace(
                        {"full_html": doc_html}
                    )
                work = apply_document_trace_to_html(
                    work,
                    full_text=full_text,
                    all_tables=list(all_tables or []),
                    all_figures=figures_for_trace,
                    category=category,
                    doc_id=doc_id,
                    inline_section_refs=False,
                )
            except Exception as exc:
                logger.warning("apply_section_extract_editor_synr trace skipped: %s", exc)
        work = inline_local_asset_images(work, category=category, doc_id=doc_id)
        return finalize_section_extract_synr(work)
    return finalize_section_extract_markdown(s)


def finalize_section_extract_text(text: str) -> str:
    """
    按章提取终态 SYNR（纯文本）：
    保留原文顺序与用词，仅做换行、数字标题与 a)/(1) 序号重排。
    展示时由前端 synrMarkdownToHtml 转 HTML。
    """
    from blueprints.ZSGL.WJGL1.text_normalize import clean_document_text

    raw = clean_document_text(text or "")
    if not raw.strip():
        return ""
    if re.search(r"<\s*(p|div|table|h[1-6]|ul|ol|li|br|strong)\b", raw, re.I):
        raw = html_to_section_plain(raw)
    raw = re.sub(r"(?m)^#{1,6}\s+", "", raw)
    raw = renumber_outline_text(raw)
    raw = renumber_letter_items_in_text(raw)
    raw = renumber_paren_num_items_in_text(raw)
    return _normalize_text_paragraph_breaks(raw)


def is_section_extract_source(extract_source: str) -> bool:
    return str(extract_source or "").strip() in _SECTION_EXTRACT_SOURCES


def content_looks_title_only(content: str, title: str = "") -> bool:
    """判断章节正文是否仅有标题、无实质内容（Markdown 或 HTML）。"""
    raw = (content or "").strip()
    if not raw:
        return True

    lines: List[str] = []
    for line in raw.splitlines():
        s = re.sub(r"^#{1,6}\s*", "", line.strip())
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        if s:
            lines.append(s)

    h = re.sub(r"\s+", " ", " ".join(lines)).strip()
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not h:
        return True
    if t and h == t:
        return True

    if t:
        idx = h.find(t)
        if idx >= 0:
            rest = h[idx + len(t):].strip(" .:：-—\t")
            if len(rest) > 8:
                return False

    if len(lines) >= 2:
        body = [ln for ln in lines if not t or (ln != t and not ln.startswith(f"{t} "))]
        if body and max(len(x) for x in body) > 6:
            return False

    if re.search(r"[（(]\d+[)）]|[a-zA-Z][).、]", raw):
        return False

    if t and len(h) <= len(t) + 8:
        return True
    return len(h) < 20


def _extract_section_text(full_text: str, section_no: str, section_title: str = "") -> str:
    if not full_text:
        return ""
    lines = full_text.splitlines()
    start = -1
    heading_body = ""
    for i, ln in enumerate(lines):
        s = ln.strip()
        m = _SECTION_LINE_RE.match(s)
        if section_no and m and m.group(1) == section_no:
            start = i + 1
            heading_body = (m.group(2) or "").strip()
            break
        if section_no and (s.startswith(section_no + " ") or s.startswith(section_no + ".") or s == section_no):
            start = i + 1
            heading_body = s[len(section_no):].strip(" .")
            break
        if section_title and section_title in s:
            start = i + 1
            break
    if start < 0:
        return ""
    end = len(lines)
    for j in range(start, len(lines)):
        s = lines[j].strip()
        m = _SECTION_LINE_RE.match(s)
        if m and section_no:
            found = m.group(1)
            if found != section_no and not found.startswith(section_no + "."):
                end = j
                break
    body = "\n".join(lines[start:end]).strip()
    if heading_body:
        return f"{heading_body}\n{body}".strip() if body else heading_body
    return body


def resolve_see_references(
    text: str,
    *,
    full_text: str = "",
    tables_by_no: Optional[Dict[str, str]] = None,
    figures_by_no: Optional[Dict[str, str]] = None,
) -> str:
    """解析「见表N / 见节 / 见图N / 如图N / 图N」并在引用句后追加溯源内容。"""
    if not text:
        return text
    tables_by_no = tables_by_no or {}
    figures_by_no = figures_by_no or {}

    traced_lines: List[str] = []
    fig_seen: set = set()
    for ln in (text or "").replace("\r\n", "\n").split("\n"):
        traced_lines.append(ln)
        for fno in _collect_figure_refs_in_plain(ln):
            key = f"fig:{fno}"
            if key in fig_seen:
                continue
            fh = _lookup_figure_html(figures_by_no, fno)
            if fh:
                fig_seen.add(key)
                traced_lines.append(f"【溯源：图{fno}】")
                traced_lines.append(fh)
    body = "\n".join(traced_lines)

    extras: List[str] = []
    seen: set = set()

    for m in _SEE_TABLE_RE.finditer(body):
        tno = m.group(1).replace(" ", "").upper().replace("．", ".")
        key = f"表{tno}"
        if key in seen:
            continue
        seen.add(key)
        th = _lookup_table_html(tables_by_no, tno)
        if th:
            extras.append(f"\n\n【溯源：{key}】\n{th}")

    for m in _SEE_SECTION_RE.finditer(body):
        sec = m.group(1).strip()
        if sec in seen:
            continue
        seen.add(sec)
        chunk = _extract_section_text(full_text, sec.replace("附录", "").strip())
        if chunk:
            extras.append(f"\n\n【溯源：见{sec}】\n{chunk}")

    for m in _SEE_APPENDIX_RE.finditer(body):
        ap = m.group(1).upper()
        key = f"appendix_{ap}"
        if key in seen:
            continue
        seen.add(key)
        chunk = _extract_section_text(full_text, f"附录{ap}") or _extract_section_text(full_text, "", f"附录 {ap}")
        if chunk:
            extras.append(f"\n\n【溯源：见附录{ap}】\n{chunk}")

    if not extras:
        return body
    return body + "".join(extras)


def _index_tables(all_tables: List[Dict[str, Any]], html: str = "") -> Dict[str, str]:
    out: Dict[str, str] = _index_tables_from_html(html)
    for item in all_tables or []:
        htm = str(item.get("html") or "").strip()
        if not htm:
            continue
        plain = re.sub(r"<[^>]+>", " ", htm)
        for m in re.finditer(r"表\s*([A-Za-z]?\.\d+(?:\.\d+)?|\d+)", plain, re.I):
            out[m.group(1).upper().replace("．", ".")] = htm
        tm = re.search(r"表\s*(\d+)", plain, re.I)
        if tm:
            out.setdefault(tm.group(1), htm)
    return out


def _call_llm_json(prompt: str, max_tokens: int = 6000) -> Optional[Dict[str, Any]]:
    try:
        from blueprints.FLGL.GetTestMethod.extractor import _call_llm_json as _llm
        return _llm(prompt, max_tokens=max_tokens, temperature=0.1)
    except Exception as e:
        logger.warning("section LLM call failed: %s", e)
        return None


def prepare_section_content_html(
    raw_html: str,
    *,
    full_text: str = "",
    all_tables: Optional[List[Dict[str, Any]]] = None,
    category: str = "",
    doc_id: str = "",
    section_extract_mode: bool = True,
) -> Dict[str, Any]:
    """章节勾选正文：清洗、块内溯源、表格修复。供规则分块或 LLM 结构分析使用。"""
    from blueprints.ZSGL.WJGL1.text_normalize import clean_document_html, clean_document_text

    if not section_extract_mode and not (full_text or "").strip() and category and doc_id:
        try:
            from pdf_extract.mineru_wjgl_service import _plain_from_markdown, local_doc_dir

            md_path = local_doc_dir(category, doc_id) / "document.md"
            if md_path.is_file():
                full_text = clean_document_text(_plain_from_markdown(md_path.read_text(encoding="utf-8")))
        except Exception as e:
            logger.debug("load full_text from local md failed: %s", e)

    raw = raw_html or ""
    is_html = bool(re.search(r"<\s*(p|div|table|h[1-6]|ul|ol|li|figure|img|strong)\b", raw, re.I))
    if section_extract_mode:
        if is_html and content_has_embedded_figure(raw):
            raw_html = clean_document_html(raw)
            plain = re.sub(r"<[^>]+>", "\n", raw_html or "")
            plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
            tables_html: List[str] = []
            for th in re.findall(r"<table[\s\S]*?</table>", raw_html or "", re.I):
                th = th.strip()
                if th and th not in tables_html:
                    tables_html.append(th)
            return {
                "content_html": raw_html,
                "content_markdown": html_to_section_plain(raw_html),
                "plain": plain,
                "tables_html": tables_html,
                "is_html": True,
                "is_markdown": False,
            }
        if is_html:
            content_md = html_to_section_plain(clean_document_html(raw))
        else:
            content_md = clean_document_text(raw)
        plain = re.sub(r"<[^>]+>", "\n", content_md or "")
        plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
        tables_html: List[str] = []
        for th in re.findall(r"<table[\s\S]*?</table>", content_md or "", re.I):
            th = th.strip()
            if th and th not in tables_html:
                tables_html.append(th)
        return {
            "content_html": content_md,
            "content_markdown": content_md,
            "plain": plain,
            "tables_html": tables_html,
            "is_html": False,
            "is_markdown": True,
        }

    if is_html:
        raw_html = clean_document_html(raw)
    else:
        raw_html = _plain_to_html(clean_document_text(raw))

    tables_map = _index_tables(all_tables or [], raw_html)
    if category and doc_id:
        tables_map = _merge_tables_index(tables_map, _load_document_tables_index(category, doc_id))
    section_index = _merge_section_index(
        _build_section_html_index(raw_html),
        _build_section_text_index(full_text) if not section_extract_mode else {},
    )
    if not section_extract_mode:
        raw_html = _inject_missing_subsections(raw_html, full_text)
    raw_html = _repair_table_captions(raw_html, tables_map)
    figures_map = _index_figures([], html=raw_html, full_text=full_text if not section_extract_mode else "")
    if category and doc_id:
        figures_map = _merge_figures_index(figures_map, _load_document_figures_index(category, doc_id))
    content_html = resolve_see_references_html(
        raw_html,
        section_index=section_index,
        tables_by_no=tables_map,
        figures_by_no=figures_map,
        full_text="" if section_extract_mode else full_text,
    )
    content_html = _repair_table_captions(content_html, tables_map)
    content_html = _dedupe_table_paragraphs_in_html(content_html)
    content_html = _wrap_doc_tables(content_html)

    plain = re.sub(r"<[^>]+>", "\n", content_html or "")
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()

    tables_html: List[str] = []
    for item in all_tables or []:
        h = str(item.get("html") or "").strip()
        if h and h in (content_html or ""):
            tables_html.append(h)
    for th in re.findall(r"<table[\s\S]*?</table>", content_html or "", re.I):
        th = th.strip()
        if th and th not in tables_html:
            tables_html.append(th)

    return {
        "content_html": content_html,
        "plain": plain,
        "tables_html": tables_html,
        "is_html": is_html,
    }


def _normalize_llm_method_block(value: Any) -> Dict[str, Any]:
    block = value if isinstance(value, dict) else {}
    if isinstance(value, str):
        block = {"text": value, "tables_html": []}
    txt = _strip_scheme_h4_titles(str(block.get("text") or ""))
    txt = _normalize_heading_paragraphs(txt)
    ths = block.get("tables_html") or []
    if isinstance(ths, str):
        ths = [ths]
    embedded = _extract_tables_from_block_text(txt)
    merged_tables = list(dict.fromkeys([str(t) for t in ths if t] + embedded))
    return {
        "text": txt,
        "tables_html": merged_tables,
        "formulas_latex": [],
    }


def _blocks_have_core_text(blocks: Dict[str, Any]) -> bool:
    return any(
        str((blocks.get(k) or {}).get("text") or "").strip()
        for k in ("sytj", "sygc", "jgpd")
    )


def _blocks_from_llm_method_item(item: Dict[str, Any]) -> Dict[str, Any]:
    symd = _normalize_llm_method_block(item.get("symd"))
    sytj = _normalize_llm_method_block(item.get("sytj"))
    sygc = _normalize_llm_method_block(item.get("sygc") or item.get("syff"))
    jgpd = _normalize_llm_method_block(item.get("jgpd"))
    if symd.get("text"):
        symd_txt = symd["text"]
        sytj_txt = str(sytj.get("text") or "").strip()
        if symd_txt not in sytj_txt:
            sytj["text"] = f"{symd_txt}\n{sytj_txt}".strip() if sytj_txt else symd_txt
    raw_blocks = {"sytj": sytj, "sygc": sygc, "jgpd": jgpd}
    return format_method_blocks_rule_based(raw_blocks)


_METHOD_FIELD_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "symd": ("试验目的", "测试目的"),
    "sytj": ("试验条件", "测试条件", "环境条件", "工况", "样品要求"),
    "sygc": ("试验过程", "试验方法", "试验步骤", "测试过程", "测试方法", "操作步骤"),
    "sjsjcl": ("试验数据处理", "数据处理"),
    "jgpd": ("结果评定", "结果判定", "结果评判", "试验结果评定"),
}


def _norm_title_for_field_match(title: str) -> str:
    t = re.sub(r"^\d+(?:\.\d+)*[\s)）.、\-]*", "", str(title or "").strip())
    return re.sub(r"\s+", "", t)


def _field_from_section_title(title: str) -> Optional[str]:
    norm = _norm_title_for_field_match(title)
    raw = str(title or "").strip()
    try:
        from blueprints.FLGL.GetTestMethod.section_extract_config import exclude_title_keywords

        for ex in exclude_title_keywords() or []:
            if ex and ex in norm and len(norm) <= len(ex) + 6:
                return None
    except Exception:
        pass
    best_field = None
    best_len = 0
    for field, keywords in _METHOD_FIELD_KEYWORDS.items():
        for kw in keywords:
            if (kw in norm or kw in raw) and len(kw) > best_len:
                best_field = field
                best_len = len(kw)
    return best_field


def _whole_section_html(sec: dict, sections: list, node_slices: dict) -> str:
    sid = str(sec.get("section_id") or "").strip()
    if sid and node_slices:
        try:
            from blueprints.ZSGL.WJGL1.document_parse_service import merge_section_html

            html = merge_section_html([sid], sections, node_slices).strip()
            if html:
                return html
        except Exception as exc:
            logger.debug("merge_section_html single section failed: %s", exc)
    for key in ("html", "text"):
        body = str(sec.get(key) or "").strip()
        if body:
            return body
    return ""


def _section_in_method_scope(
    sec: dict,
    *,
    root_no: str,
    scope_ids: set,
) -> bool:
    sid = str(sec.get("section_id") or "").strip()
    if sid and sid in scope_ids:
        return True
    sno = str(sec.get("section_no") or "").strip().lstrip(".")
    root = str(root_no or "").strip().lstrip(".")
    if not root:
        return True
    if not sno:
        return False
    return sno == root or sno.startswith(root + ".")


def build_method_blocks_from_parse_sections(
    parse_cache: dict,
    group: dict,
) -> Optional[Dict[str, Any]]:
    """
    从 parse sections（04 同源）按「一整章对应一小节」映射 symd/sytj/sygc/jgpd。
    每个字段只取标题最匹配的一节全文，禁止跨章拼接。
    """
    if not isinstance(parse_cache, dict):
        return None
    sections = list(parse_cache.get("sections") or [])
    if not sections:
        return None
    node_slices = parse_cache.get("node_slices") if isinstance(parse_cache.get("node_slices"), dict) else {}

    parent = group.get("parent") if isinstance(group.get("parent"), dict) else {}
    root_no = str(parent.get("sectionNo") or parent.get("section_no") or "").strip().lstrip(".")
    explicit_items = []
    try:
        from blueprints.FLGL.method_routes import _explicit_section_items_from_group

        explicit_items = _explicit_section_items_from_group(group)
    except Exception:
        explicit_items = []

    scope_ids = {
        str(x.get("section_id") or "").strip()
        for x in explicit_items
        if str(x.get("section_id") or "").strip()
    }

    blocks: Dict[str, Any] = {
        "symd": {"text": "", "tables_html": [], "formulas_latex": []},
        "sytj": {"text": "", "tables_html": [], "formulas_latex": []},
        "sygc": {"text": "", "tables_html": [], "formulas_latex": []},
        "sjsjcl": {"text": "", "tables_html": [], "formulas_latex": []},
        "jgpd": {"text": "", "tables_html": [], "formulas_latex": []},
    }
    assigned_fields: set = set()

    for item in explicit_items:
        sid = str(item.get("section_id") or "").strip()
        title = str(item.get("title") or "").strip()
        field = _field_from_section_title(title)
        if not field or field in assigned_fields:
            continue
        sec = None
        if sid:
            sec = next((s for s in sections if str(s.get("section_id") or "") == sid), None)
        if not sec and item.get("sectionNo"):
            from blueprints.FLGL.method_routes import _resolve_parse_cache_section

            sec = _resolve_parse_cache_section(
                parse_cache,
                section_no=str(item.get("sectionNo") or ""),
                title=title,
                section_id=sid,
            )
        if not sec:
            continue
        body = _whole_section_html(sec, sections, node_slices)
        if not body.strip():
            continue
        blocks[field]["text"] = body
        assigned_fields.add(field)

    for field, keywords in _METHOD_FIELD_KEYWORDS.items():
        if field in assigned_fields:
            continue
        candidates: List[tuple] = []
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            if not _section_in_method_scope(sec, root_no=root_no, scope_ids=scope_ids):
                continue
            raw_title = str(sec.get("title") or sec.get("heading") or "")
            matched = _field_from_section_title(raw_title)
            if matched != field:
                continue
            sno = str(sec.get("section_no") or "").strip().lstrip(".")
            depth = sno.count(".") if sno else 99
            norm = _norm_title_for_field_match(raw_title)
            kw = next((k for k in sorted(keywords, key=len, reverse=True) if k in norm or k in raw_title), "")
            exact = 1 if kw and (norm == kw or norm.endswith(kw)) else 0
            text_len = len(str(sec.get("text") or ""))
            candidates.append((exact, -depth, text_len, sec))
        if not candidates:
            continue
        candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        body = _whole_section_html(candidates[0][3], sections, node_slices)
        if body.strip():
            blocks[field]["text"] = body
            assigned_fields.add(field)

    core = blocks.get("sytj", {}).get("text") or blocks.get("sygc", {}).get("text") or blocks.get("jgpd", {}).get("text")
    if not str(core or "").strip():
        return None
    return blocks


def llm_structure_section_methods(
    content_html: str,
    method_name_hint: str,
    *,
    tables_html: Optional[List[str]] = None,
    figures_html: str = "",
) -> List[Dict[str, Any]]:
    """
    LLM 理解勾选章节语义：判断合并为 1 个还是拆成多个试验方法，
    并将分散的试验目的/条件/过程/评定归入对应块（仅原文，不编造）。
    返回 [{name, blocks, split_mode, reason}, ...]
    """
    tables_html = tables_html or []
    tbl_block = "\n\n".join(tables_html[:12])[:16000]
    fig_block = (figures_html or "").strip()[:8000]
    hint = (method_name_hint or "试验方法").strip()

    prompt = f"""你是试验文档结构化专家。用户从标准/方案中勾选了若干章节，正文已合并。
请**理解语义**（不要机械按「每节下必须有条件/过程/评定」），判断输出 **1 个** 还是 **多个** 独立试验方法。

【合并为 1 个方法（methods 长度=1）】
- 整体结构为：试验目的 → 试验方法/过程（内含多个子项）→ 结果评定
- 条件、方法、评定分散在不同小节，但属于同一试验任务
- 子项如「（1）××」「1.1 ××」属于同一试验下的步骤/子方法，不是独立试验
- 用户本次只定义了 1 个待提取方法

【拆成多个方法（methods 长度>1）】
- 多个可独立执行的并列试验，各有自己的条件/过程/评定（或明显独立单元）
- 如「最大起飞重量试验」「风载试验」等并列章节，各自成体系
- 每个 methods 元素须能独立成文

【字段映射 — 每一字段只对应标准中一整章，禁止跨章拼接】
- symd：试验目的（可选，无则 ""）→ **仅**取标题含「试验目的」的**单一章节**全文
- sytj：试验条件 → **仅**取标题含「试验条件」的**单一章节**全文
- sygc：试验过程 / 试验方法 / 试验步骤 → **仅**取标题含「试验过程/方法/步骤」的**单一章节**全文
- jgpd：结果评定 → **仅**取标题含「结果评定/判定」的**单一章节**全文
- **禁止**把多个章节的段落合并进同一字段；**禁止**从 A 章取条件、B 章取过程再拼在一起
- 国标/军用标准通常每个字段各有独立章（如 8.2.1 目的、8.2.2 条件、8.2.3 方法、8.2.4 评定），须按章一一对应
- 若某字段在正文中无对应整章，该字段 text 留空 ""，不要从其他章摘录拼凑

【硬性要求】
1. 只输出 JSON，不要 markdown
2. 所有 text 必须来自【章节正文】，禁止编造、总结、扩写；找不到则 ""
3. 保留原文中的 <table>、<img> 等 HTML
4. name 为方法短名，不要加「-试验方法」后缀

【输出格式】
{{
  "split_mode": "single|multiple",
  "structure_note": "简要说明为何合并或拆分",
  "methods": [
    {{
      "name": "方法短名",
      "symd": {{"text": ""}},
      "sytj": {{"text": ""}},
      "sygc": {{"text": ""}},
      "jgpd": {{"text": ""}}
    }}
  ]
}}

【方法名称提示】{hint}

【章节正文 HTML】
{content_html[:20000]}

【已有表格 HTML】
{tbl_block[:10000] or "（无）"}

【已有图片 HTML】
{fig_block or "（无）"}
"""
    logger.info("[section-llm] 结构分析 prompt=%d 字 hint=%s", len(prompt), hint[:80])
    result = _call_llm_json(prompt, max_tokens=12000)
    out: List[Dict[str, Any]] = []
    if isinstance(result, dict):
        methods_raw = result.get("methods")
        split_mode = str(result.get("split_mode") or "single").strip()
        note = str(result.get("structure_note") or "").strip()
        if isinstance(methods_raw, list):
            for item in methods_raw:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or hint).strip() or hint
                blocks = _blocks_from_llm_method_item(item)
                if not _blocks_have_core_text(blocks):
                    continue
                out.append({
                    "name": name,
                    "blocks": blocks,
                    "split_mode": split_mode,
                    "reason": note,
                })
        if out:
            logger.info(
                "[section-llm] 结构分析完成 mode=%s methods=%d note=%s",
                split_mode,
                len(out),
                note[:120],
            )
            return out

    logger.warning("[section-llm] 结构分析无有效结果，回退单方法整合")
    blocks = llm_integrate_method(
        content_html,
        hint,
        tables_html=tables_html,
        figures_html=figures_html,
    )
    if not _blocks_have_core_text(blocks):
        split = split_method_html_ordered(content_html)
        blocks = _html_blocks_from_split(split)
    blocks = format_method_blocks_rule_based(blocks)
    return [{"name": hint, "blocks": blocks, "split_mode": "single", "reason": "LLM 回退"}]


def llm_integrate_method(
    raw_content: str,
    method_name: str,
    *,
    tables_html: Optional[List[str]] = None,
    figures_html: str = "",
) -> Dict[str, Any]:
    """
    LLM 将章节正文整合为试验条件 / 试验过程 / 结果评定 三块。
    返回 { sytj, sygc, jgpd } 每项含 text / tables_html。
    """
    tables_html = tables_html or []
    tbl_block = "\n\n".join(tables_html[:12])[:16000]
    fig_block = (figures_html or "").strip()[:8000]
    prompt = f"""你是标准试验方法编写专家。根据以下从标准文档截取的章节内容，整理为一个完整试验方法文档。
方法名：{method_name}

要求：
1. 输出 JSON，且仅输出 JSON：
{{
  "sytj": {{ "text": "试验条件正文 HTML", "tables_html": [] }},
  "sygc": {{ "text": "试验过程正文 HTML", "tables_html": [] }},
  "jgpd": {{ "text": "结果评定正文 HTML", "tables_html": [] }}
}}
2. 将原文按语义归入三块：试验条件(sytj)、试验过程(sygc)、结果评定(jgpd)；**保持各块内原文顺序**，不要打乱段落先后。
2b. **每一小块只对应标准中的一个完整章节**：试验目的/条件/过程/评定各找**唯一**匹配章，取该章**全文**，禁止从多个章摘录拼成一块。
3. 小节标题统一改为方案编号：试验条件用 1.1/1.2…，试验过程用 2.1/2.2…，结果评定用 3.1/3.2…；**删除**原标准章标题（如「3 试验条件」「5 试验过程」「7 试验结果评定」），**不要**输出「1. 试验条件」「2. 试验过程」「3. 结果评定」等大块标题（系统会自动添加）。
4. 小节标题格式：<p class="wjgl-section-heading" data-section-no="1.1"><strong>1.1 标题</strong></p>
5. **必须保留全部表格与图片**：原文中的 <table>...</table>、<img> 必须完整保留在对应块的 text 中；不得把表格改成纯文字。
6. 「见表N」处应保留表格或把已有表格 HTML 嵌入对应位置；不得编造标准中不存在的内容。
7. 仅做归类、分段与编号整理，**不改写**原文句子。

章节原文（HTML）：
{raw_content[:18000]}

已有表格 HTML（嵌入对应块 text 或 tables_html）：
{tbl_block[:10000] or "（无）"}

已有图片 HTML（嵌入对应块 text，通常放在试验条件或试验过程）：
{fig_block or "（无）"}
"""
    result = _call_llm_json(prompt, max_tokens=8000)
    if not isinstance(result, dict):
        return _fallback_blocks(raw_content, tables_html)

    out: Dict[str, Any] = {}
    for key in ("sytj", "sygc", "jgpd"):
        block = result.get(key) or {}
        if isinstance(block, str):
            block = {"text": block, "tables_html": []}
        txt = _strip_scheme_h4_titles(str(block.get("text") or ""))
        txt = _normalize_heading_paragraphs(txt)
        ths = block.get("tables_html") or []
        if isinstance(ths, str):
            ths = [ths]
        embedded = _extract_tables_from_block_text(txt)
        merged_tables = list(dict.fromkeys([str(t) for t in ths if t] + embedded))
        out[key] = {
            "text": txt,
            "tables_html": merged_tables,
            "formulas_latex": [],
        }
    return out


def _extract_tables_from_block_text(text: str) -> List[str]:
    return [t.strip() for t in re.findall(r"<table[\s\S]*?</table>", text or "", re.I) if t.strip()]


def _fallback_blocks(raw_content: str, tables_html: List[str]) -> Dict[str, Any]:
    if "<" in (raw_content or "") and re.search(r"<\s*(p|table|div)\b", raw_content or "", re.I):
        split = split_gb_standard_method_html(raw_content)
        if any(split.values()):
            return _html_blocks_from_split(split)
    text = raw_content or ""
    mid = len(text) // 2 if text else 0
    return {
        "sytj": {"text": text[: max(mid, 0)] or text, "tables_html": tables_html[:1], "formulas_latex": []},
        "sygc": {"text": text[max(mid, 0):] or "", "tables_html": tables_html[1:3], "formulas_latex": []},
        "jgpd": {"text": "", "tables_html": tables_html[3:4], "formulas_latex": []},
    }


def build_equipment_scoped_recommendations(
    method_text: str,
    *,
    neo4j_driver=None,
    std_name: str = "",
    std_code: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    返回 (recommended_nodes, recommended_relations, equipment_recommendations)。
    先推荐装备，再对每个装备推荐项目、指标及关联度。
    """
    from blueprints.ZSGL.WJGL1.graph_link_helper import build_graph_link_candidates

    doc = {
        "std_name": std_name,
        "std_code": std_code,
        "abstract": method_text[:2000],
        "keywords": "",
        "title": std_name,
    }
    raw = build_graph_link_candidates(doc, neo4j_driver, top_k_per_slot=3) if neo4j_driver else {}
    if isinstance(raw, dict):
        candidates = list(raw.get("candidates") or [])
    elif isinstance(raw, list):
        candidates = raw
    else:
        candidates = []

    by_slot: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        if not isinstance(c, dict):
            continue
        slot = c.get("slot") or ""
        by_slot.setdefault(slot, []).append(c)

    equip_cands = sorted(by_slot.get("equipment", []), key=lambda x: -(x.get("score") or 0))[:5]
    if not equip_cands:
        equip_cands = sorted(by_slot.get("equipment_type", []), key=lambda x: -(x.get("score") or 0))[:3]

    recommended_nodes: List[Dict[str, Any]] = []
    recommended_relations: List[Dict[str, Any]] = []
    equipment_recommendations: List[Dict[str, Any]] = []

    projects = sorted(by_slot.get("test_project", []), key=lambda x: -(x.get("score") or 0))[:24]
    indicators = sorted(by_slot.get("indicator", []), key=lambda x: -(x.get("score") or 0))[:24]
    per_equipment_top = 3

    for eq in equip_cands:
        eid = str(eq.get("node_id") or "")
        ename = str(eq.get("node_name") or eid)
        escore = float(eq.get("score") or 0)
        recommended_nodes.append({
            "node_type": eq.get("node_type") or "Equipment",
            "node_id": eid,
            "node_name": ename,
            "score": escore,
        })
        recommended_relations.append({
            "target_type": "Equipment",
            "target_id": eid,
            "node_name": ename,
            "rel_type": "CON",
            "rate": round(min(1.0, escore), 3),
            "equipment_id": eid,
        })
        eq_projects, eq_indicators = _load_equipment_related_from_graph(
            neo4j_driver, eid, top_n=per_equipment_top,
        )
        eq_projects = _fill_equipment_slot_candidates(
            eq_projects, projects, equipment_name=ename, top_n=per_equipment_top,
        )
        eq_indicators = _fill_equipment_slot_candidates(
            eq_indicators, indicators, equipment_name=ename, top_n=per_equipment_top,
        )
        for p in eq_projects:
            pid = str(p.get("node_id") or "")
            recommended_relations.append({
                "target_type": "TestProject",
                "target_id": pid,
                "node_name": p.get("node_name") or pid,
                "rel_type": "CON",
                "rate": round(min(1.0, float(p.get("score") or 0) * 0.9 + escore * 0.1), 3),
                "equipment_id": eid,
            })
        for ind in eq_indicators:
            iid = str(ind.get("node_id") or "")
            recommended_relations.append({
                "target_type": "Indicator",
                "target_id": iid,
                "node_name": ind.get("node_name") or iid,
                "rel_type": "CON",
                "rate": round(min(1.0, float(ind.get("score") or 0) * 0.9 + escore * 0.1), 3),
                "equipment_id": eid,
            })
        equipment_recommendations.append({
            "equipment_id": eid,
            "equipment_name": ename,
            "score": escore,
            "projects": eq_projects,
            "indicators": eq_indicators,
        })

    if not equip_cands:
        for p in projects[:3]:
            recommended_relations.append({
                "target_type": "TestProject",
                "target_id": p.get("node_id"),
                "node_name": p.get("node_name"),
                "rel_type": "CON",
                "rate": round(min(1.0, float(p.get("score") or 0)), 3),
            })
        for ind in indicators[:3]:
            recommended_relations.append({
                "target_type": "Indicator",
                "target_id": ind.get("node_id"),
                "node_name": ind.get("node_name"),
                "rel_type": "CON",
                "rate": round(min(1.0, float(ind.get("score") or 0)), 3),
            })

    return recommended_nodes, recommended_relations, equipment_recommendations


def _load_equipment_related_from_graph(
    neo4j_driver,
    equipment_id: str,
    *,
    top_n: int = 3,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """从图库读取某装备已关联的项目、指标（各最多 top_n 个）。"""
    projects: List[Dict[str, Any]] = []
    indicators: List[Dict[str, Any]] = []
    if not neo4j_driver or not equipment_id:
        return projects, indicators
    try:
        with neo4j_driver.session() as session:
            for row in session.run(
                """
                MATCH (eq:Equipment {Id: $eqId})-[:CON]->(p:TestProject)
                RETURN COALESCE(p.Id, '') AS node_id, COALESCE(p.Name, '') AS node_name
                ORDER BY p.Name
                LIMIT $lim
                """,
                {"eqId": equipment_id, "lim": top_n},
            ):
                nid = str(row.get("node_id") or "").strip()
                if nid:
                    projects.append({
                        "node_id": nid,
                        "node_name": str(row.get("node_name") or nid),
                        "score": 0.85,
                    })
            for row in session.run(
                """
                MATCH (eq:Equipment {Id: $eqId})-[:CON]->(i:Indicator)
                RETURN COALESCE(i.Id, '') AS node_id, COALESCE(i.Name, '') AS node_name
                ORDER BY i.Name
                LIMIT $lim
                """,
                {"eqId": equipment_id, "lim": top_n},
            ):
                nid = str(row.get("node_id") or "").strip()
                if nid:
                    indicators.append({
                        "node_id": nid,
                        "node_name": str(row.get("node_name") or nid),
                        "score": 0.85,
                    })
    except Exception as e:
        logger.debug("load equipment related failed eq=%s: %s", equipment_id, e)
    return projects, indicators


def _fill_equipment_slot_candidates(
    existing: List[Dict[str, Any]],
    pool: List[Dict[str, Any]],
    *,
    equipment_name: str = "",
    top_n: int = 3,
) -> List[Dict[str, Any]]:
    """在图库关联不足时，用全局候选按装备名匹配补齐。"""
    out = list(existing)
    seen = {str(x.get("node_id") or "") for x in out}
    en = re.sub(r"\s+", "", (equipment_name or "").lower())

    def _score_item(item: Dict[str, Any]) -> float:
        base = float(item.get("score") or 0)
        eqn = re.sub(r"\s+", "", str(item.get("equipment_name") or "").lower())
        if en and eqn and (en in eqn or eqn in en):
            return base + 0.15
        return base

    ranked = sorted(pool, key=lambda x: -_score_item(x))
    for item in ranked:
        if len(out) >= top_n:
            break
        nid = str(item.get("node_id") or "").strip()
        if not nid or nid in seen:
            continue
        seen.add(nid)
        out.append({
            "node_id": nid,
            "node_name": item.get("node_name") or nid,
            "score": round(min(1.0, _score_item(item)), 3),
        })
    return out[:top_n]


def enrich_section_method(
    raw_html: str,
    method_name: str,
    *,
    use_llm_merge: bool = False,
    full_text: str = "",
    all_tables: Optional[List[Dict[str, Any]]] = None,
    all_figures: Optional[List[Dict[str, Any]]] = None,
    std_name: str = "",
    std_code: str = "",
    neo4j_driver=None,
    gallery_html: str = "",
    category: str = "",
    doc_id: str = "",
    section_extract_mode: bool = False,
) -> Dict[str, Any]:
    """单方法：溯源 → 可选 LLM 整合 → 重编号 → 图推荐。"""
    prepared = prepare_section_content_html(
        raw_html,
        full_text=full_text,
        all_tables=all_tables,
        category=category,
        doc_id=doc_id,
        section_extract_mode=section_extract_mode,
    )
    content_html = prepared["content_html"]
    plain = prepared["plain"]
    tables_html = prepared["tables_html"]
    is_html = prepared["is_html"]

    if use_llm_merge:
        llm_input = content_html if is_html else plain
        blocks = llm_integrate_method(
            llm_input,
            method_name,
            tables_html=tables_html,
            figures_html=gallery_html,
        )
        if not _blocks_have_core_text(blocks):
            split = split_method_html_ordered(content_html)
            blocks = _html_blocks_from_split(split)
        blocks = format_method_blocks_rule_based(blocks)
        formatted_single = ""
        extract_source = "sections_llm"
    elif section_extract_mode:
        source_for_fmt = str(prepared.get("content_html") or plain or content_html or raw_html or "")
        if not content_has_embedded_figure(source_for_fmt) and content_has_embedded_figure(raw_html):
            source_for_fmt = str(raw_html or "").strip()
        if re.search(r"!\[[^\]]*\]\(\s*(?:data:image|/api/[^)]*parse-asset)", source_for_fmt, re.I):
            try:
                from blueprints.ZSGL.WJGL1.document_parse_service import _markdown_chunk_to_html

                converted = _markdown_chunk_to_html(
                    source_for_fmt, category=category, doc_id=doc_id,
                ).strip()
                if converted:
                    source_for_fmt = converted
            except Exception as exc:
                logger.debug("[method-extract] md data-uri to html skipped: %s", exc)
        if re.search(r"<\s*(p|div|table|h[1-6]|ul|ol|li|figure|strong|img)\b", source_for_fmt, re.I):
            formatted_single = finalize_section_extract_synr(source_for_fmt)
        else:
            formatted_single = finalize_section_extract_markdown(
                str(prepared.get("content_markdown") or plain or source_for_fmt or "")
            )
        if (full_text or "").strip() or all_tables or all_figures:
            tables_map = _index_tables(all_tables or [], content_html)
            figures_map = _index_figures(
                list(all_figures or []),
                html=content_html,
                full_text=full_text,
            )
            traced = resolve_see_references(
                formatted_single,
                full_text=full_text,
                tables_by_no=tables_map,
                figures_by_no=figures_map,
            )
            # 已追加溯源块时不再二次 finalize，避免 figure/table HTML 被 plain 化
            if traced.strip() != formatted_single.strip():
                formatted_single = traced
            elif re.search(r"<\s*(figure|img|table)\b", formatted_single, re.I):
                formatted_single = finalize_section_extract_synr(formatted_single)
            else:
                formatted_single = finalize_section_extract_markdown(traced)
        blocks = {
            "sytj": {"text": "", "tables_html": [], "formulas_latex": []},
            "sygc": {"text": "", "tables_html": [], "formulas_latex": []},
            "jgpd": {"text": "", "tables_html": [], "formulas_latex": []},
        }
        extract_source = "sections_selected"
    else:
        split = split_method_html_ordered(content_html)
        if any(str(split.get(k) or "").strip() for k in ("sytj", "sygc", "jgpd")):
            blocks = format_method_blocks_rule_based(_html_blocks_from_split(split))
            formatted_single = ""
        else:
            formatted_single = format_selected_content_rule_based(content_html)
            blocks = {
                "sytj": {"text": "", "tables_html": [], "formulas_latex": []},
                "sygc": {"text": "", "tables_html": [], "formulas_latex": []},
                "jgpd": {"text": "", "tables_html": [], "formulas_latex": []},
            }
        extract_source = "sections_rule"

    rec_nodes, rec_rels, eq_recs = build_equipment_scoped_recommendations(
        f"{method_name}\n{plain[:3000]}",
        neo4j_driver=neo4j_driver,
        std_name=std_name,
        std_code=std_code,
    )

    return {
        "blocks": blocks,
        "content_html": content_html,
        "formatted_single": formatted_single,
        "gallery_html": gallery_html,
        "recommended_nodes": rec_nodes,
        "recommended_relations": rec_rels,
        "equipment_recommendations": eq_recs,
        "extract_source": extract_source,
        "use_llm_merge": use_llm_merge,
    }


_PLAIN_HEADING_LINE_RE = re.compile(
    r"^(\d+(?:\.\d+)*)\s*([\u4e00-\u9fff（）()、，\sA-Za-z0-9.\-]{2,80})\s*$"
)
_PLAIN_HEADING_PREFIX_RE = re.compile(
    r"^(\d+(?:\.\d+)*)\s*"
    r"([\u4e00-\u9fff（）()、，\sA-Za-z0-9.\-]{2,48})"
    r"(.+)$"
)
_PLAIN_PAREN_SUBHEAD_RE = re.compile(r"^[（(](\d+)[）)]\s*(.+)$")
_PLAIN_BOLD_LINE_RE = re.compile(r"^\*\*(.+?)\*\*\s*$")
_PLAIN_NUM_LIST_RE = re.compile(r"^(\d+)[\.、\)）]\s*")
_CN_NUM_LIST_RE = re.compile(r"^(\d+)[）)]\s*(.+)$")
_TABLE_MARKER_RE = re.compile(r"^【表格】\s*$")
_KNOWN_TABLE_COLS = (
    "温度/高度条件", "参数名称", "功能说明", "数据记录", "岗位角色", "工作内容",
    "设备名称", "文件标识", "文件名称", "样机编号", "机身编号", "持续时间",
    "序号", "步骤", "姓名", "参数", "参数值", "来源", "操作", "配置",
    "试验类型", "机型", "颜色", "温度（℃）", "气压（kPa）", "对应高度",
    "喷淋角度", "水压", "喷水量", "水温控制",
)
_MD_HASH_PAREN_RE = re.compile(r"^#{1,6}\s*[（(](\d+)[）)]\s*(.+)$")
_MD_HASH_PLAIN_RE = re.compile(r"^#{1,6}\s+(.+)$")
_TABLE_CAPTION_LINE_RE = re.compile(r"^(?:附)?表\s*(\S+)\s*(.*)$")
_TOP_SECTION_RE = re.compile(
    r"^(\d+)\s+(试验条件|试验方法|试验过程|试验数据处理|结果评定|试验目的|试验组织[\u4e00-\u9fff]*)$"
)


def _has_markdown_artifacts(text: str) -> bool:
    s = _preprocess_plain_synr_text(text or "")
    if not s:
        return False
    if _is_structured_synr_html(text or ""):
        return bool(
            re.search(r"^#{1,6}\s", s, re.M)
            or "【表格】" in s
        )
    return bool(
        re.search(r"^#{1,6}\s", s, re.M)
        or re.search(r"^(?:附)?表\s*[\dA-Za-z]+", s, re.M)
        or "【表格】" in s
    )


def _preprocess_plain_synr_text(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(
        r"(\d+(?:\.\d+)*\s+[^\n/]+)\n/评定\s*",
        r"\1/评定\n",
        s,
    )
    return s


def _split_glued_table_header(line: str) -> List[str]:
    s = (line or "").strip()
    if "|" in s:
        return [c.strip() for c in s.split("|") if c.strip() or s.count("|") >= 1]
    cols: List[str] = []
    pos = 0
    ordered = sorted(_KNOWN_TABLE_COLS, key=len, reverse=True)
    while pos < len(s):
        tok = next((k for k in ordered if s.startswith(k, pos)), None)
        if not tok:
            break
        cols.append(tok)
        pos += len(tok)
    return cols if len(cols) >= 2 else [s]


def _split_glued_table_row(line: str, col_count: int) -> List[str]:
    s = (line or "").strip()
    if not s:
        return []
    if s.startswith("|"):
        s = s[1:]
    if "|" in s:
        return [c.strip() for c in s.split("|")]
    m = re.match(r"^([\u4e00-\u9fffA-Za-z（）()、，\-\s]+?)([\d≥≤±].+)$", s)
    if m:
        if col_count >= 3:
            return ["", m.group(1).strip(), m.group(2).strip()]
        return [m.group(1).strip(), m.group(2).strip()]
    return [s]


def _table_block_should_stop(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return True
    if _TABLE_MARKER_RE.match(s):
        return True
    if re.match(r"^#{1,6}\s", s):
        return True
    if _PLAIN_HEADING_LINE_RE.match(s) or _PLAIN_HEADING_PREFIX_RE.match(s):
        return True
    if _TOP_SECTION_RE.match(s):
        return True
    if _PLAIN_PAREN_SUBHEAD_RE.match(s):
        return True
    if re.match(r"^\d+(?:\.\d+)*\s*[\u4e00-\u9fff]", s):
        return True
    return False


def _parse_caption_table_block(lines: List[str], start: int) -> Tuple[str, int]:
    """解析「表 6 标题」+ 粘连表头/表体。"""
    stripped = lines[start].strip()
    cap_m = _TABLE_CAPTION_LINE_RE.match(stripped)
    if not cap_m:
        return "", start
    caption = f"表 {cap_m.group(1)} {cap_m.group(2).strip()}".strip()
    i = start + 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and re.match(r"^<table\b", lines[i].strip(), re.I):
        return (
            f"<p><strong>{_inline_markdown_to_html(caption)}</strong></p>",
            i,
        )
    rows: List[List[str]] = []
    if i < len(lines) and lines[i].strip() and not _table_block_should_stop(lines[i].strip()):
        hdr = lines[i].strip()
        if "|" not in hdr:
            rows.append(_split_glued_table_header(hdr))
            i += 1
    while i < len(lines):
        row_line = lines[i].strip()
        if not row_line:
            i += 1
            if len(rows) > 1:
                break
            continue
        if _table_block_should_stop(row_line):
            break
        if "|" in row_line:
            rows.append([c.strip() for c in row_line.strip("|").split("|")])
        else:
            rows.append(_split_glued_table_row(row_line, len(rows[0]) if rows else 3))
        i += 1
    parts = [f"<p><strong>{_inline_markdown_to_html(caption)}</strong></p>"]
    tbl = _rows_to_table_html(rows)
    if tbl:
        parts.append(tbl)
    return "".join(parts), i


def _rows_to_table_html(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    head, *body = rows
    parts = ['<div class="doc-table"><table border="1" cellpadding="4" cellspacing="0">']
    parts.append("<thead><tr>")
    for cell in head:
        parts.append(f"<th>{_inline_markdown_to_html(cell)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in body:
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<td>{_inline_markdown_to_html(cell)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _parse_table_marker_block(lines: List[str], start: int) -> Tuple[str, int]:
    i = start
    stripped = lines[i].strip()
    if not (_TABLE_MARKER_RE.match(stripped) or stripped.startswith("【表格】")):
        return "", start
    rows: List[List[str]] = []
    if stripped.startswith("【表格】") and not _TABLE_MARKER_RE.match(stripped):
        rest = stripped.replace("【表格】", "", 1).strip()
        if rest:
            rows.append(_split_glued_table_header(rest))
    i += 1
    while i < len(lines):
        row_line = lines[i].strip()
        if _table_block_should_stop(row_line):
            if not row_line:
                i += 1
            break
        if "|" in row_line or row_line.startswith("|"):
            row = [c.strip() for c in row_line.strip("|").split("|")]
            rows.append(row)
        elif not rows:
            rows.append(_split_glued_table_header(row_line))
        else:
            rows.append(_split_glued_table_row(row_line, len(rows[0]) if rows else 2))
        i += 1
    return _rows_to_table_html(rows), i


def _is_br_only_pseudo_html(text: str) -> bool:
    s = (text or "").strip()
    if not s or not re.search(r"<\s*br\s*/?>", s, re.I):
        return False
    return not re.search(r"<\s*(p|div|table|ul|ol|li|h[1-6]|strong)\b", s, re.I)


def _inline_markdown_to_html(text: str) -> str:
    import html as html_module

    s = html_module.escape(str(text or ""))
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


def _plain_row_is_table(line: str) -> bool:
    return line.count("|") >= 2


_CN_LIST_RESTORED_LINE_RE = re.compile(r"^(\d+)\s+([\u4e00-\u9fff（）()、，\sA-Za-z0-9.\-：:].+)$")
_CN_LIST_TOP_SECTION_PREFIXES = (
    "试验目的", "试验条件", "试验方法", "试验过程", "试验数据处理", "结果评定", "结果评判", "结果判定",
)


def _plain_text_to_method_html(text: str) -> str:
    """纯文本/Markdown 片段 → 带章节标题的 HTML，供重编号与 WangEditor 使用。"""
    from blueprints.ZSGL.WJGL1.text_normalize import clean_document_text

    s = _preprocess_plain_synr_text(clean_document_text(text or ""))
    if not _is_br_only_pseudo_html(text or ""):
        s = split_glued_method_subsection_lines(s)
    if not s.strip():
        return ""
    if _is_br_only_pseudo_html(text or ""):
        pass
    elif re.search(r"<\s*(p|div|table|h[1-6]|ul|ol|li|strong)\b", s, re.I):
        if _has_markdown_artifacts(text or s):
            s = html_to_section_plain(s)
        else:
            return _normalize_heading_paragraphs(s)

    lines = s.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        cap_html, next_i = _parse_caption_table_block(lines, i)
        if cap_html:
            out.append(cap_html)
            i = next_i
            continue

        tbl_html, next_i = _parse_table_marker_block(lines, i)
        if tbl_html:
            out.append(tbl_html)
            i = next_i
            continue

        if _plain_row_is_table(stripped):
            rows: List[List[str]] = []
            while i < len(lines) and _plain_row_is_table(lines[i].strip()):
                row = lines[i].strip()
                if row.startswith("|"):
                    row = row[1:]
                if row.endswith("|"):
                    row = row[:-1]
                rows.append([c.strip() for c in row.split("|")])
                i += 1
            if rows:
                head, *body = rows
                out.append('<div class="doc-table"><table border="1" cellpadding="4" cellspacing="0">')
                out.append("<thead><tr>")
                for cell in head:
                    out.append(f"<th>{_inline_markdown_to_html(cell)}</th>")
                out.append("</tr></thead><tbody>")
                for row in body:
                    if all(re.fullmatch(r"[-:\s|]+", c or "") for c in row):
                        continue
                    out.append("<tr>")
                    for cell in row:
                        out.append(f"<td>{_inline_markdown_to_html(cell)}</td>")
                    out.append("</tr>")
                out.append("</tbody></table></div>")
            continue

        ts = _TOP_SECTION_RE.match(stripped)
        if ts:
            out.append(
                f'<p class="wjgl-section-heading" data-section-no="{ts.group(1)}" '
                f'style="font-weight:600;margin:10px 0 6px">'
                f"<strong>{ts.group(1)} {_inline_markdown_to_html(ts.group(2))}</strong></p>"
            )
            i += 1
            continue

        mhp = _MD_HASH_PAREN_RE.match(stripped)
        if mhp:
            out.append(f"<p><strong>（{mhp.group(1)}）{_inline_markdown_to_html(mhp.group(2))}</strong></p>")
            i += 1
            continue

        mhp2 = _MD_HASH_PLAIN_RE.match(stripped)
        if mhp2:
            body = mhp2.group(1).strip()
            inner_paren = _PLAIN_PAREN_SUBHEAD_RE.match(body)
            if inner_paren:
                out.append(
                    f"<p><strong>（{inner_paren.group(1)}）"
                    f"{_inline_markdown_to_html(inner_paren.group(2))}</strong></p>"
                )
            elif re.match(r"^\d+\s+[\u4e00-\u9fff]", body):
                sm = re.match(r"^(\d+)\s+(.+)$", body)
                sno = sm.group(1) if sm else ""
                out.append(
                    f'<p class="wjgl-section-heading" data-section-no="{sno}" '
                    f'style="font-weight:600;margin:10px 0 6px">'
                    f"<strong>{_inline_markdown_to_html(body)}</strong></p>"
                )
            else:
                out.append(f"<p><strong>{_inline_markdown_to_html(body)}</strong></p>")
            i += 1
            continue

        restored = _CN_LIST_RESTORED_LINE_RE.match(stripped)
        if restored and not any(
            stripped.startswith(f"{restored.group(1)} {prefix}")
            for prefix in _CN_LIST_TOP_SECTION_PREFIXES
        ):
            out.append("<ol>")
            while i < len(lines):
                rm = _CN_LIST_RESTORED_LINE_RE.match(lines[i].strip())
                if not rm or any(
                    lines[i].strip().startswith(f"{rm.group(1)} {prefix}")
                    for prefix in _CN_LIST_TOP_SECTION_PREFIXES
                ):
                    break
                out.append(f"<li>{_inline_markdown_to_html(rm.group(2))}</li>")
                i += 1
            out.append("</ol>")
            continue

        hm = _PLAIN_HEADING_LINE_RE.match(stripped)
        if hm:
            sno, title = hm.group(1), hm.group(2)
            out.append(
                f'<p class="wjgl-section-heading" data-section-no="{sno}" '
                f'style="font-weight:600;margin:10px 0 6px">'
                f"<strong>{sno} {_inline_markdown_to_html(title)}</strong></p>"
            )
            i += 1
            continue

        if _PLAIN_NUM_LIST_RE.match(stripped) and not _PLAIN_HEADING_LINE_RE.match(stripped):
            out.append("<ol>")
            while i < len(lines) and _PLAIN_NUM_LIST_RE.match(lines[i].strip()) and not _PLAIN_HEADING_LINE_RE.match(lines[i].strip()):
                body = _PLAIN_NUM_LIST_RE.sub("", lines[i].strip())
                out.append(f"<li>{_inline_markdown_to_html(body)}</li>")
                i += 1
            out.append("</ol>")
            continue

        cn = _CN_NUM_LIST_RE.match(stripped)
        if cn and cn.group(2):
            out.append("<ol>")
            while i < len(lines):
                m = _CN_NUM_LIST_RE.match(lines[i].strip())
                if not m or not m.group(2):
                    break
                out.append(f"<li>{_inline_markdown_to_html(m.group(2))}</li>")
                i += 1
            out.append("</ol>")
            continue

        hp = _PLAIN_HEADING_PREFIX_RE.match(stripped)
        if hp and hp.group(3).strip():
            title = hp.group(2).strip()
            if not title.startswith(("）", ")")):
                sno, body = hp.group(1), hp.group(3).strip()
                out.append(
                    f'<p class="wjgl-section-heading" data-section-no="{sno}" '
                    f'style="font-weight:600;margin:10px 0 6px">'
                    f"<strong>{sno} {_inline_markdown_to_html(title)}</strong></p>"
                )
                out.append(f"<p>{_inline_markdown_to_html(body)}</p>")
                i += 1
                continue

        pm = _PLAIN_PAREN_SUBHEAD_RE.match(stripped)
        if pm:
            out.append(
                f"<p><strong>（{pm.group(1)}）{_inline_markdown_to_html(pm.group(2))}</strong></p>"
            )
            i += 1
            continue

        bm = _PLAIN_BOLD_LINE_RE.match(stripped)
        if bm:
            out.append(f"<p><strong>{_inline_markdown_to_html(bm.group(1))}</strong></p>")
            i += 1
            continue

        para = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (
                not nxt
                or _plain_row_is_table(nxt)
                or _PLAIN_HEADING_LINE_RE.match(nxt)
                or _PLAIN_PAREN_SUBHEAD_RE.match(nxt)
                or _PLAIN_BOLD_LINE_RE.match(nxt)
                or _PLAIN_NUM_LIST_RE.match(nxt)
                or _CN_NUM_LIST_RE.match(nxt)
                or _TABLE_MARKER_RE.match(nxt)
                or nxt.startswith("【表格】")
            ):
                break
            para.append(nxt)
            i += 1
        joined = "<br>".join(_inline_markdown_to_html(p) for p in para)
        out.append(f"<p>{joined}</p>")

    return _normalize_heading_paragraphs("".join(out))


def _plain_to_html(text: str) -> str:
    return _plain_text_to_method_html(text)


def label_from_source_filename(source_name: str) -> str:
    """方法标识：来源文件名（去路径、去扩展名）。"""
    import os

    base = os.path.basename(str(source_name or "").strip())
    if not base:
        return ""
    stem, _ext = os.path.splitext(base)
    return (stem or base).strip()


def _html_to_plain_excerpt(html: str, *, max_len: int = 3500) -> str:
    plain = re.sub(r"<[^>]+>", "\n", html or "")
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    plain = re.sub(r"[ \t\r\f\v]+", " ", plain).strip()
    return plain[:max_len]


def _fallback_method_description(method_name: str, plain: str) -> str:
    name = str(method_name or "").strip()
    if name:
        return f"{name}的试验方法说明。"
    excerpt = str(plain or "").strip()
    if len(excerpt) > 160:
        return excerpt[:160].rstrip() + "…"
    return excerpt


def generate_method_description_llm(
    method_name: str,
    *,
    content_html: str = "",
    content_plain: str = "",
    source_name: str = "",
    std_code: str = "",
) -> str:
    """根据方法名称与正文摘录，LLM 生成 80–200 字方法描述（摘要）。"""
    plain = (content_plain or "").strip() or _html_to_plain_excerpt(content_html)
    if not plain and not str(method_name or "").strip():
        return ""

    prompt = f"""你是试验标准文档专家。根据以下试验方法信息，写一段简洁的方法描述（摘要）。

要求：
1. 80–200 个汉字，一段话；
2. 概括试验对象、主要试验步骤/条件与结果评定要点；
3. 不编造原文没有的内容；
4. 只输出描述正文，不要标题、不要 JSON、不要列表编号。

方法名称：{method_name or "（未命名）"}
来源文件：{source_name or "未知"}
标准编号：{std_code or "未知"}

方法正文摘录：
{plain[:3200]}
"""
    try:
        from blueprints.FLGL.GetTestMethod.extractor import _clamp_completion_tokens, _get_runtime_client

        client, model = _get_runtime_client()
        mt = _clamp_completion_tokens(prompt, 320)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=mt,
        )
        choice = resp.choices[0] if resp and resp.choices else None
        content = (choice.message.content or "").strip() if choice and choice.message else ""
        content = re.sub(r"^[\"'`「『【\[]+|[\"'`」』】\]]+$", "", content).strip()
        if content:
            return content[:500]
    except Exception as e:
        logger.warning("generate_method_description_llm failed: %s", e)
    return _fallback_method_description(method_name, plain)


def recommend_standard_method_name_llm(
    *,
    file_name: str = "",
    full_text: str = "",
    heading_outline: str = "",
    candidate_name: str = "",
    std_code: str = "",
) -> str:
    """
    标准 PDF/Word：根据封面标题、目录与正文开头，LLM 推荐规范方法名称。
    失败时回退 candidate_name 或文件名去扩展名。
    """
    fallback = str(candidate_name or "").strip()
    if not fallback and file_name:
        import os
        fallback = os.path.splitext(os.path.basename(file_name))[0]
    excerpt = (full_text or "").strip()[:4000]
    outline = (heading_outline or "").strip()[:2000]
    if not excerpt and not outline:
        return fallback

    prompt = f"""你是国家标准/行业标准文档专家。请根据文档信息推荐**一条**规范的试验方法名称。

要求：
1. 优先使用标准封面/标题中的完整名称（含标准号、部分号、试验名称）；
2. 不要编造文档中不存在的编号或试验名；
3. 只输出方法名称一行纯文本，不要 JSON、不要解释；
4. 若文档为「第N部分」分则标准，名称应含部分信息（如「…第7部分：湿热试验」）。

来源文件：{file_name or "未知"}
标准编号提示：{std_code or "未知"}
LLM 初提取名称：{candidate_name or "（无）"}

目录大纲：
{outline or "（无）"}

正文开头摘录：
{excerpt[:3000]}
"""
    try:
        from blueprints.FLGL.GetTestMethod.extractor import _clamp_completion_tokens, _get_runtime_client

        client, model = _get_runtime_client()
        mt = _clamp_completion_tokens(prompt, 200)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=mt,
        )
        choice = resp.choices[0] if resp and resp.choices else None
        content = (choice.message.content or "").strip() if choice and choice.message else ""
        content = re.sub(r"^[\"'`「『【\[]+|[\"'`」』】\]]+$", "", content).strip()
        content = content.splitlines()[0].strip() if content else ""
        if content and len(content) >= 4:
            return content[:200]
    except Exception as e:
        logger.warning("recommend_standard_method_name_llm failed: %s", e)
    return fallback
