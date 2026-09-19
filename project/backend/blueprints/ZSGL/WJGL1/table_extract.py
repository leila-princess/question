"""WJGL 表格结构化提取、索引与正文定位插入。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from .text_normalize import clean_document_text

TABLE_CAPTION_LINE_RE = re.compile(
    r"^表\s*(?P<no>(?:A\.|[Ａａ]\.)?[0-9０-９]+(?:\.[0-9０-９]+)?)\s*(?P<title>.*)?$",
    re.I,
)
TABLE_REF_INLINE_RE = re.compile(r"表\s*(?P<no>(?:A\.|[Ａａ]\.)?\d+(?:\.\d+)?)", re.I)
TABLE_CAPTION_HTML_RE = re.compile(
    r"<caption[^>]*>\s*表\s*(?P<no>(?:A\.)?\d+(?:\.\d+)?)",
    re.I,
)
TOC_LEADER_RE = re.compile(r"\.{4,}|…{2,}|·{4,}")
SECTION_NUM_CELL_RE = re.compile(r"^\d+(?:\.\d+)*\s*[\u4e00-\u9fff]")
STANDARD_TABLE_KW_RE = re.compile(
    r"设备类别|动力形式|振动|试验量值|试验方式|频率|功率谱|振幅|坐标轴|起飞重量|扫频|"
    r"犵|ｏｃｔ|ｍｍ|随机|正弦|Ｈｚ|Hz|ｋｇ|kg",
    re.I,
)


def normalize_table_no(raw: str) -> str:
    s = (raw or "").strip()
    fw = "０１２３４５６７８９ＡＢＣＤＥＦ．。"
    asc = "0123456789ABCDEF.."
    s = s.translate(str.maketrans(fw, asc)).upper().replace(" ", "")
    s = re.sub(r"^表", "", s, flags=re.I)
    return s


def is_plausible_table_no(raw: str) -> bool:
    n = normalize_table_no(raw)
    if not n:
        return False
    if n.startswith("A."):
        try:
            return 1 <= int(n.split(".", 1)[1]) <= 15
        except ValueError:
            return False
    if re.match(r"^\d+$", n):
        return 1 <= int(n) <= 5
    return False


def parse_table_caption_from_line(line: str) -> Optional[str]:
    s = clean_document_text(line or "").strip()
    if not s:
        return None
    m = TABLE_CAPTION_LINE_RE.match(s)
    if not m:
        return None
    if len(s) > 72 and not re.search(r"^表\s*(?:A\.)?\d", s, re.I):
        return None
    tno = normalize_table_no(m.group("no"))
    if not is_plausible_table_no(tno):
        return None
    return tno


def looks_like_table_ocr_line(line: str, *, aggressive: bool = False) -> bool:
    s = clean_document_text(line or "").strip()
    if not s:
        return False
    if parse_table_caption_from_line(s):
        return len(s) > 18
    if len(s) > 30 and s.count("表") >= 2:
        return True
    if re.search(r"[０-９]{4,}", s) and re.search(r"[／\.]{1,}|功率谱|振幅|频率|犵|ｏｃｔ|g²|g2", s, re.I):
        return True
    if re.search(r"(Hz|ｏｃｔ|功率谱|谱密度|扫频速率|设备类别|动力形式|振动类别|试验量值|坐标轴)", s, re.I) and len(s) > 28:
        return True
    if aggressive:
        tokens = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", s)
        if len(s) > 50 and len(tokens) >= 8 and not re.search(r"[。；！？]$", s):
            return True
    return False


def _table_plain_signature(html: str) -> str:
    from blueprints.FLGL.GetTestMethod.parser import _strip_table_tags

    return re.sub(r"\s+", "", _strip_table_tags(str(html or "")))[:240]


def _table_cell_count(html: str) -> int:
    return str(html or "").lower().count("<td")


def _table_matrix_from_html(html: str) -> List[List[str]]:
    from blueprints.FLGL.GetTestMethod.parser import _strip_table_tags

    plain = _strip_table_tags(str(html or ""))
    rows: List[List[str]] = []
    for ln in plain.splitlines():
        cells = [c.strip() for c in re.split(r"[\t|│|｜]+|\s{2,}", ln.strip()) if c.strip()]
        if not cells:
            continue
        if len(cells) == 1 and len(cells[0]) > 100:
            rows.append([cells[0]])
        else:
            rows.append(cells)
    if rows:
        return rows
    text = re.sub(r"<[^>]+>", " ", html or "")
    return [[c.strip() for c in re.split(r"\s{2,}", ln.strip()) if c.strip()] for ln in text.splitlines() if ln.strip()]


def _table_rows_from_html(html: str) -> List[List[str]]:
    rows: List[List[str]] = []
    for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", str(html or ""), re.I | re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr.group(1), re.I | re.S)
        cleaned = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in cells]
        cleaned = [c for c in cleaned if c]
        if cleaned:
            rows.append(cleaned)
    return rows


def is_junk_table(html: str, *, page_no: int = 0, body_start_page: int = 1) -> bool:
    """仅识别目录/前言/正文块等假表格，真表格默认保留。"""
    hs = str(html or "").strip()
    if not hs or "<td" not in hs.lower():
        return True
    if _table_cell_count(hs) < 2:
        return True
    if page_no and body_start_page and page_no < body_start_page:
        return True

    rows = _table_rows_from_html(hs)
    if not rows:
        rows = _table_matrix_from_html(hs)
    if len(rows) < 2:
        return True

    cols = max(len(r) for r in rows)
    plain = _table_plain_signature(hs)

    if cols < 2:
        return True

    joined_rows = [" ".join(r) for r in rows]
    dot_rows = sum(1 for row in joined_rows if TOC_LEADER_RE.search(row))
    if dot_rows >= 2 or (dot_rows >= 1 and len(rows) >= 5):
        return True

    section_rows = sum(
        1 for row in rows
        if row and SECTION_NUM_CELL_RE.match(str(row[0]).strip())
    )
    if section_rows >= 3:
        return True

    if re.search(r"目\s*次", plain) and not STANDARD_TABLE_KW_RE.search(plain):
        return True

    if re.search(r"第[一二三四五六七八九十\d]+部分", plain) and not STANDARD_TABLE_KW_RE.search(plain):
        return True

    total_cells = sum(len(r) for r in rows)
    long_cells = sum(1 for row in rows for c in row if len(str(c).strip()) > 120)
    if total_cells and long_cells / total_cells > 0.5:
        return True

    return False


def is_valid_content_table(html: str, *, page_no: int = 0, body_start_page: int = 1) -> bool:
    return not is_junk_table(html, page_no=page_no, body_start_page=body_start_page)


def _caption_from_html(html: str) -> Optional[str]:
    m = TABLE_CAPTION_HTML_RE.search(str(html or ""))
    if m:
        return normalize_table_no(m.group("no"))
    return None


def _page_table_html_strings(page: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for item in page.get("tables_html") or page.get("tables") or []:
        if isinstance(item, dict):
            hs = str(item.get("html") or "").strip()
        else:
            hs = str(item or "").strip()
        if hs and "<td" in hs.lower():
            out.append(hs)
    return out


def _score_table_item(item: Dict[str, Any]) -> int:
    html = str(item.get("html") or "")
    score = _table_cell_count(html)
    if STANDARD_TABLE_KW_RE.search(_table_plain_signature(html)):
        score += 20
    if item.get("table_no"):
        score += 10
    return score


def _dedupe_registry(registry: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_sig: Dict[str, Dict[str, Any]] = {}
    by_no: Dict[str, Dict[str, Any]] = {}
    ordered: List[Dict[str, Any]] = []

    for item in registry:
        sig = item.get("plain_sig") or ""
        if sig and sig in by_sig:
            if _score_table_item(item) > _score_table_item(by_sig[sig]):
                by_sig[sig] = item
            continue
        if sig:
            by_sig[sig] = item

        tno = normalize_table_no(item.get("table_no") or "")
        if tno:
            prev = by_no.get(tno)
            if prev and _score_table_item(prev) >= _score_table_item(item):
                continue
            by_no[tno] = item

        ordered.append(item)

    seen_ids: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for item in ordered:
        sig = item.get("plain_sig") or ""
        if sig and by_sig.get(sig) is not item:
            continue
        tno = normalize_table_no(item.get("table_no") or "")
        if tno and by_no.get(tno) is not item:
            continue
        tid = item.get("table_id")
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        out.append(item)
    return out


def build_table_registry(
    pages: List[Dict[str, Any]],
    body_start_page: int = 1,
) -> List[Dict[str, Any]]:
    """汇总全文档有效表格；表号仅来自页内「表N」标题行，不猜测。"""
    captions_by_page: Dict[int, List[str]] = {}
    for page in pages or []:
        pno = int(page.get("page_number") or page.get("page_num") or 0)
        caps: List[str] = []
        for ln in (page.get("text") or "").splitlines():
            tno = parse_table_caption_from_line(ln)
            if tno:
                caps.append(tno)
        if caps:
            captions_by_page[pno] = caps

    raw_items: List[Dict[str, Any]] = []
    seq = 0
    for page in pages or []:
        pno = int(page.get("page_number") or page.get("page_num") or 0)
        tables = _page_table_html_strings(page)
        cap_queue = list(captions_by_page.get(pno) or [])
        cap_used = 0
        valid_tables = [hs for hs in tables if not is_junk_table(hs, page_no=pno, body_start_page=body_start_page)]
        for idx, hs in enumerate(tables):
            if hs not in valid_tables:
                continue
            seq += 1
            tno = _caption_from_html(hs)
            if not tno and cap_used < len(cap_queue):
                tno = cap_queue[cap_used]
                cap_used += 1
            raw_items.append({
                "table_id": f"T{seq:03d}",
                "table_no": tno or "",
                "page": pno,
                "index_on_page": idx,
                "html": hs,
                "plain_sig": _table_plain_signature(hs),
                "caption_text": f"表{tno}" if tno else "",
            })

    return _dedupe_registry(raw_items)


def _pick_table_for_caption(
    table_no: str,
    page_no: int,
    registry: List[Dict[str, Any]],
    used_ids: Set[str],
) -> Optional[Dict[str, Any]]:
    if not table_no:
        return None
    norm = normalize_table_no(table_no)
    candidates: List[Dict[str, Any]] = []
    for item in registry:
        if item["table_id"] in used_ids:
            continue
        item_no = normalize_table_no(item.get("table_no") or "")
        if item_no and item_no == norm:
            candidates.append(item)
    if not candidates:
        for item in registry:
            if item["table_id"] in used_ids:
                continue
            p = int(item.get("page") or 0)
            if p < page_no - 1 or p > page_no + 2:
                continue
            candidates.append(item)
    if not candidates:
        return None

    def sort_key(it: Dict[str, Any]) -> tuple:
        p = int(it.get("page") or 0)
        item_no = normalize_table_no(it.get("table_no") or "")
        no_match = 0 if item_no == norm else 1
        dist = abs(p - page_no)
        on_page = 0 if p == page_no else 1
        return (no_match, on_page, dist, int(it.get("index_on_page") or 0), -_score_table_item(it))

    candidates.sort(key=sort_key)
    picked = dict(candidates[0])
    if not picked.get("table_no"):
        picked["table_no"] = norm
        picked["caption_text"] = f"表{norm}"
    return picked


def strip_table_text_from_page_text(text: str, registry: List[Dict[str, Any]], page_no: int) -> str:
    """去掉页内表格 OCR 正文，保留表题引导行。"""
    page_table_sigs: Set[str] = set()
    for item in registry:
        if int(item.get("page") or 0) == page_no:
            sig = item.get("plain_sig") or ""
            if sig:
                page_table_sigs.add(sig)

    kept: List[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            kept.append(ln)
            continue
        if parse_table_caption_from_line(s):
            kept.append(ln)
            continue
        if looks_like_table_ocr_line(s, aggressive=True):
            continue
        if page_table_sigs and len(s) > 40:
            if any(sig and sig[:24] in re.sub(r"\s+", "", s) for sig in page_table_sigs):
                continue
        kept.append(ln)
    return "\n".join(kept)


def wrap_table_html(html: str, table_id: str = "", table_no: str = "", caption: str = "") -> str:
    import html as html_module

    h = (html or "").strip()
    if not h:
        return ""
    if 'class="doc-table"' in h[:300]:
        return h
    if not h.lower().startswith("<table"):
        h = f"<table>{h}</table>"
    cap = caption or (f"表 {table_no}" if table_no else "")
    if cap and "<caption" not in h.lower()[:400]:
        cap_html = f"<caption>{html_module.escape(cap)}</caption>"
        h = re.sub(r"(<table[^>]*>)", rf"\1{cap_html}", h, count=1, flags=re.I)
    if "border" not in h.lower()[:300]:
        h = h.replace(
            "<table",
            '<table border="1" cellpadding="4" cellspacing="0" '
            'style="border-collapse:collapse;width:100%;"',
            1,
        )
    attrs = ""
    if table_id:
        attrs += f' id="{html_module.escape(table_id)}"'
    if table_no:
        attrs += f' data-table-no="{html_module.escape(table_no)}"'
    if attrs and "data-table-no" not in h[:400]:
        h = re.sub(
            r"(<table[^>]*)(>)",
            rf'\1 class="wjgl-doc-table"{attrs}\2',
            h,
            count=1,
            flags=re.I,
        )
    return f'<div class="doc-table" style="overflow-x:auto;margin:12px 0;">{h}</div>'
