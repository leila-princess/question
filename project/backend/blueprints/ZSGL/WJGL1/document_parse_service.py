"""WJGL 统一文档解析服务 — 高保真 parse_standard_file + 归一化 + 章节 HTML。"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config import Config
from Database.minIo.main import to_minio_meta_value

from .body_content_trim import (
    find_body_start_page,
    trim_full_text_from_body_start,
    trim_html_from_body_start,
    trim_pages_from_body_start,
    trim_sections_from_body_start,
)
from .text_normalize import (
    clean_document_html,
    clean_document_text,
    garbled_score,
    has_cid_garbled,
    is_garbled_content,
    is_toc_noise_section,
    normalize_document_text,
    normalize_pages,
)
from .table_extract import (
    build_table_registry,
    is_valid_content_table,
    looks_like_table_ocr_line as _looks_like_table_ocr_line,
    parse_table_caption_from_line,
    strip_table_text_from_page_text,
    wrap_table_html as _wrap_table_html_rich,
    _pick_table_for_caption,
)

logger = logging.getLogger(__name__)

PARSE_PREFIX = "parse"


def _parse_minio_key(category: str, doc_id: str, name: str) -> str:
    return f"{category}/{doc_id}/{PARSE_PREFIX}/{name}"


def _text_to_html(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if re.search(r"<[a-z][\s\S]*>", t, re.I):
        return t
    parts = [p.strip() for p in re.split(r"\n{2,}", t) if p.strip()]
    if not parts:
        parts = [t]
    return "".join(f"<p>{p.replace(chr(10), '<br/>')}</p>" for p in parts)


def _section_anchor_id(section_no: str) -> str:
    return "wjgl-sec-" + (section_no or "").lstrip(".").replace(".", "-")


def _figure_to_html_base64(fig: Any, page_no: int = 0) -> str:
    """将提取出的图片转为内嵌 base64，避免临时路径在请求结束后失效。"""
    import base64
    import html as html_module

    if not isinstance(fig, dict):
        return ""
    caption = str(fig.get("caption") or fig.get("description") or f"图 (p{page_no})" if page_no else "图")
    b64_src = str(fig.get("base64") or fig.get("data_url") or "")
    if b64_src and not b64_src.startswith("data:"):
        b64_src = f"data:image/png;base64,{b64_src}"
    if not b64_src:
        path = fig.get("path") or fig.get("file") or ""
        if path and os.path.isfile(path):
            try:
                raw_bytes = open(path, "rb").read()
                try:
                    from PIL import Image
                    from io import BytesIO as _BytesIO

                    im = Image.open(_BytesIO(raw_bytes))
                    max_side = 960
                    if max(im.size) > max_side:
                        im.thumbnail((max_side, max_side))
                        buf = _BytesIO()
                        im.save(buf, format="PNG" if im.mode in ("RGBA", "P") else "JPEG", quality=85)
                        raw_bytes = buf.getvalue()
                        mime = "image/png" if im.mode in ("RGBA", "P") else "image/jpeg"
                    else:
                        ext = os.path.splitext(path)[1].lower()
                        mime = "image/png" if ext == ".png" else "image/jpeg"
                except Exception:
                    ext = os.path.splitext(path)[1].lower()
                    mime = "image/png" if ext == ".png" else "image/jpeg"
                raw = base64.b64encode(raw_bytes).decode()
                b64_src = f"data:{mime};base64,{raw}"
            except Exception as e:
                logger.warning("figure base64 failed %s: %s", path, e)
    if not b64_src:
        return f"<p>[图: {html_module.escape(caption)}]</p>" if caption else ""
    cap = html_module.escape(caption)
    return (
        f'<figure style="margin:12px 0;text-align:center;">'
        f'<img src="{b64_src}" alt="{cap}" style="max-width:100%;height:auto;"/>'
        f'</figure>'
    )


def _figures_html(figures: List[Any], page_no: int) -> str:
    return "".join(_figure_to_html_base64(fig, page_no) for fig in (figures or []))


def _wrap_table_html(html: str, table_id: str = "", table_no: str = "", caption: str = "") -> str:
    if table_id or table_no or caption:
        return _wrap_table_html_rich(html, table_id=table_id, table_no=table_no, caption=caption)
    return _wrap_table_html_rich(html)


def _strip_table_ocr_lines(text: str, tables_html: List[str]) -> str:
    """页内已有 HTML 表格时，去掉 OCR 挤成一行的表题/表体重复文本。"""
    if not text:
        return text
    from blueprints.FLGL.GetTestMethod.parser import _strip_table_tags

    cell_fragments: set = set()
    for th in tables_html or []:
        plain = _strip_table_tags(str(th or ""))
        for ln in plain.splitlines():
            s = ln.strip()
            if len(s) >= 4:
                cell_fragments.add(s)
        for tok in re.split(r"[\s\u3000]+", plain):
            if len(tok) >= 4:
                cell_fragments.add(tok)

    has_tables = _has_usable_table_html(tables_html)
    kept: List[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            kept.append(ln)
            continue
        if has_tables and _looks_like_table_ocr_line(s):
            continue
        if has_tables and len(s) > 45 and sum(1 for frag in cell_fragments if frag in s) >= 2:
            continue
        kept.append(ln)
    return "\n".join(kept)


def _has_usable_table_html(tables_html: List[Any]) -> bool:
    for th in tables_html or []:
        hs = str(th or "").strip()
        if hs and "<td" in hs.lower():
            return True
    return False


def _enrich_pages_with_table_map(
    pages: List[Dict[str, Any]],
    page_table_map: Dict[int, List[str]],
) -> List[Dict[str, Any]]:
    """把 page_table_map / all_tables 合并回 pages.tables_html。"""
    if not page_table_map:
        return pages
    page_by_no: Dict[int, Dict[str, Any]] = {}
    for p in pages or []:
        pn = int(p.get("page_number") or p.get("page_num") or 0)
        if pn:
            page_by_no[pn] = dict(p)
    for pno, tables in (page_table_map or {}).items():
        pn = int(pno or 0)
        if pn <= 0:
            continue
        page = page_by_no.setdefault(
            pn, {"page_number": pn, "text": "", "tables_html": [], "figures": []},
        )
        existing = list(page.get("tables_html") or [])
        for th in tables or []:
            hs = str(th or "").strip()
            if hs and hs not in existing:
                existing.append(hs)
        page["tables_html"] = existing
        page_by_no[pn] = page
    return [page_by_no[k] for k in sorted(page_by_no.keys())]


def _merge_native_pdf_assets(
    file_path: str,
    pages: List[Dict[str, Any]],
    ocr_output_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not file_path or not os.path.isfile(file_path):
        return pages
    try:
        from blueprints.FLGL.GetTestMethod.parser import _merge_pdf_pages_with_native_assets

        merged = _merge_pdf_pages_with_native_assets(
            file_path, {"pages": pages}, ocr_output_dir,
        )
        return list(merged.get("pages") or pages)
    except Exception as e:
        logger.warning("merge native pdf assets failed: %s", e)
        return pages


def _strip_figure_ocr_lines(text: str, figures: List[Any]) -> str:
    """去掉正文中与独立提取图片重复的 OCR 文字片段。"""
    if not text or not figures:
        return text
    drop: set = set()
    for fig in figures:
        if not isinstance(fig, dict):
            continue
        ot = (fig.get("ocr_text") or "").strip()
        if len(ot) >= 4:
            drop.add(ot)
            for ln in ot.splitlines():
                if ln.strip():
                    drop.add(ln.strip())
    if not drop:
        return text
    kept: List[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if s and s in drop:
            continue
        if s and len(s) >= 8 and any(len(d) >= 12 and (s in d or d in s) for d in drop):
            continue
        if figures and re.match(r"^图\s*[\dA-Z]", s, re.I) and len(s) > 30:
            continue
        kept.append(ln)
    return "\n".join(kept)


def _line_to_html_part(line: str) -> str:
    import html as html_module

    from blueprints.FLGL.GetTestMethod.parser import (
        APPENDIX_HEADING_RE,
        CHAPTER_HEADING_RE,
        DECIMAL_HEADING_RE,
        _is_heading_candidate,
        _normalize_spaces,
    )

    line = (line or "").strip()
    if not line:
        return ""
    norm = _normalize_spaces(line)
    if not _is_heading_candidate(norm):
        return f"<p>{html_module.escape(line)}</p>"

    m = DECIMAL_HEADING_RE.match(norm)
    if m:
        sec_no = m.group(1).lstrip(".")
        aid = _section_anchor_id(sec_no)
        title_part = html_module.escape((m.group(2) or "").strip())
        inner = f"{html_module.escape(sec_no)} {title_part}".strip()
        return (
            f'<p id="{aid}" class="wjgl-section-heading" '
            f'data-section-no="{html_module.escape(sec_no)}" '
            f'style="font-weight:600;margin:10px 0 6px">'
            f"<strong>{inner}</strong></p>"
        )
    if CHAPTER_HEADING_RE.match(norm) or APPENDIX_HEADING_RE.match(norm):
        aid = _section_anchor_id(norm[:12])
        return (
            f'<p id="{aid}" class="wjgl-section-heading" '
            f'data-section-no="{html_module.escape(norm[:12])}" '
            f'style="font-weight:600;margin:10px 0 6px">'
            f"<strong>{html_module.escape(norm)}</strong></p>"
        )
    return (
        f'<p class="wjgl-section-heading" style="font-weight:600;margin:10px 0 6px">'
        f"<strong>{html_module.escape(norm)}</strong></p>"
    )


def _parse_section_no(title: str) -> str:
    from blueprints.FLGL.GetTestMethod.parser import (
        CHAPTER_HEADING_RE,
        DECIMAL_HEADING_RE,
        _normalize_spaces,
    )

    t = _normalize_spaces(title or "")
    m = DECIMAL_HEADING_RE.match(t)
    if m:
        return m.group(1).lstrip(".")
    m2 = CHAPTER_HEADING_RE.match(t)
    if m2:
        return m2.group(0).strip()[:20]
    return ""


def _line_matches_section_heading(line: str, section_no: str, section_title: str) -> bool:
    from blueprints.FLGL.GetTestMethod.parser import (
        DECIMAL_HEADING_RE,
        _is_heading_candidate,
        _normalize_spaces,
    )

    if not section_no and not section_title:
        return False
    norm = _normalize_spaces(line)
    if not norm or not _is_heading_candidate(norm):
        return False
    if section_title and _normalize_spaces(section_title) == norm:
        return True
    m = DECIMAL_HEADING_RE.match(norm)
    if m and section_no and m.group(1).lstrip(".") == section_no.lstrip("."):
        return True
    return False


def parse_asset_url(category: str, doc_id: str, asset_name: str) -> str:
    from urllib.parse import quote
    return (
        f"/api/wjgl1/files/{quote(doc_id, safe='')}/parse-asset"
        f"?category={quote(category, safe='')}&name={quote(asset_name, safe='')}"
    )


def _figure_to_html_asset(fig: Any, page_no: int, category: str, doc_id: str) -> str:
    import html as html_module

    if not isinstance(fig, dict):
        return ""
    caption = str(fig.get("caption") or fig.get("description") or (f"图 (p{page_no})" if page_no else "图"))
    asset = fig.get("parse_asset") or ""
    if asset:
        src = parse_asset_url(category, doc_id, asset)
        cap = html_module.escape(caption)
        return (
            f'<figure data-figure-index="{fig.get("figure_index", 0)}" '
            f'data-page="{page_no}" style="margin:12px 0;text-align:center;">'
            f'<img src="{src}" alt="{cap}" style="max-width:100%;height:auto;"/>'
            f"</figure>"
        )
    return _figure_to_html_base64(fig, page_no)


def _persist_parse_tables_to_minio(
    category: str,
    doc_id: str,
    pages: List[Dict[str, Any]],
    body_start_page: int = 1,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """结构化表格落 MinIO parse/tables/，返回 (pages, table_registry)。"""
    if not pages:
        return pages, []
    registry = build_table_registry(pages, body_start_page=body_start_page)
    if not Config.MINIO_CLIENT or not category or not doc_id:
        return pages, registry

    bucket = Config.MINIO_BUCKET_NAME

    out_pages: List[Dict[str, Any]] = []
    for page in pages:
        p = dict(page)
        pno = int(p.get("page_number") or p.get("page_num") or 0)
        new_tables: List[Any] = []
        page_items = [i for i in registry if int(i.get("page") or 0) == pno]
        for item in page_items:
            hs = str(item.get("html") or "").strip()
            if not hs:
                continue
            tid = str(item.get("table_id") or "")
            tno = str(item.get("table_no") or "")
            caption = str(item.get("caption_text") or (f"表{tno}" if tno else f"表{item.get('table_id', '')}"))
            if not hs:
                continue
            wrapped = _wrap_table_html(hs, table_id=tid, table_no=tno, caption=caption)
            asset_name = f"tables/{tid}.html"
            obj_key = _parse_minio_key(category, doc_id, asset_name)
            payload = wrapped.encode("utf-8")
            try:
                Config.MINIO_CLIENT.put_object(
                    bucket,
                    obj_key,
                    BytesIO(payload),
                    length=len(payload),
                    content_type="text/html; charset=utf-8",
                )
                item["parse_asset"] = asset_name
                new_tables.append({
                    "parse_asset": asset_name,
                    "table_id": tid,
                    "table_no": tno,
                    "html": hs,
                })
            except Exception as e:
                logger.warning("upload parse table failed %s: %s", obj_key, e)
        p["tables_html"] = new_tables
        out_pages.append(p)

    return out_pages, registry


def _persist_parse_figures_to_minio(category: str, doc_id: str, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """上传解析出的图片到 MinIO parse/figures/，写入 parse_asset / figure_index。"""
    if not Config.MINIO_CLIENT or not category or not doc_id:
        return pages
    bucket = Config.MINIO_BUCKET_NAME
    fig_counter = 0
    out_pages: List[Dict[str, Any]] = []
    for page in pages:
        p = dict(page)
        new_figs: List[Dict[str, Any]] = []
        pno = int(p.get("page_number") or p.get("page_num") or 0)
        for fig in p.get("figures") or []:
            if not isinstance(fig, dict):
                continue
            fcopy = dict(fig)
            path = fcopy.get("path") or fcopy.get("file") or ""
            if path and os.path.isfile(path):
                fig_counter += 1
                ext = os.path.splitext(path)[1].lower() or ".png"
                asset_name = f"figures/p{pno:03d}_f{fig_counter:04d}{ext}"
                obj_key = _parse_minio_key(category, doc_id, asset_name)
                try:
                    Config.MINIO_CLIENT.fput_object(bucket, obj_key, path)
                    fcopy["parse_asset"] = asset_name
                    fcopy["figure_index"] = fig_counter
                    fcopy["page"] = pno
                except Exception as e:
                    logger.warning("upload parse figure failed %s: %s", obj_key, e)
            new_figs.append(fcopy)
        p["figures"] = new_figs
        out_pages.append(p)
    return out_pages


def _page_tables_html(page: Dict[str, Any], body_start_page: int = 1, *, filter_junk: bool = False) -> List[str]:
    from blueprints.FLGL.GetTestMethod.parser import _normalize_table_html_items

    raw = page.get("tables_html")
    if raw:
        items = _normalize_table_html_items(raw)
    else:
        items = _normalize_table_html_items(page.get("tables") or [])
    pno = int(page.get("page_number") or page.get("page_num") or 0)
    out: List[str] = []
    for item in items:
        if isinstance(item, dict):
            hs = str(item.get("html") or "").strip()
            if not hs and item.get("parse_asset"):
                continue
        else:
            hs = str(item or "").strip()
        if not hs or "<td" not in hs.lower():
            continue
        if filter_junk and is_valid_content_table(hs, page_no=pno, body_start_page=body_start_page) is False:
            continue
        out.append(hs)
    return out


def _normalize_page_structures(pages: List[Dict[str, Any]], body_start_page: int = 1) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for page in pages or []:
        p = dict(page)
        p["tables_html"] = _page_tables_html(p, body_start_page=body_start_page, filter_junk=False)
        if p.get("page_num") and not p.get("page_number"):
            p["page_number"] = p["page_num"]
        out.append(p)
    return out


def _build_page_content_html(
    page: Dict[str, Any],
    category: str = "",
    doc_id: str = "",
    embed_figures_base64: bool = False,
    skip_section_no: str = "",
    skip_section_title: str = "",
    table_registry: Optional[List[Dict[str, Any]]] = None,
    used_table_ids: Optional[Set[str]] = None,
) -> tuple:
    """单页：正文行（去表 OCR）→ 遇表题插入 HTML 表 → 图片。"""
    from blueprints.FLGL.GetTestMethod.parser import PAGE_MARKER_RE

    parts: List[str] = []
    blocks: List[Dict[str, Any]] = []
    pno = int(page.get("page_number") or page.get("page_num") or 0)
    figures = list(page.get("figures") or [])
    registry = table_registry or []
    used = used_table_ids if used_table_ids is not None else set()

    raw_text = clean_document_text(page.get("text") or "")
    text = _strip_figure_ocr_lines(raw_text, figures)
    text = strip_table_text_from_page_text(text, registry, pno)

    i = 0
    lines = text.split("\n")
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or PAGE_MARKER_RE.match(line):
            continue
        if _line_matches_section_heading(line, skip_section_no, skip_section_title):
            continue

        tno = parse_table_caption_from_line(line)
        if tno:
            tbl = _pick_table_for_caption(tno, pno, registry, used)
            if tbl:
                wrapped = _wrap_table_html(
                    tbl.get("html") or "",
                    table_id=str(tbl.get("table_id") or ""),
                    table_no=str(tbl.get("table_no") or tno),
                    caption=str(tbl.get("caption_text") or f"表{tno}"),
                )
                if wrapped:
                    parts.append(wrapped)
                    used.add(str(tbl["table_id"]))
                    blocks.append({
                        "type": "table",
                        "page": pno,
                        "table_id": tbl.get("table_id"),
                        "table_no": tbl.get("table_no") or tno,
                        "html": wrapped,
                    })
                while i < len(lines) and _looks_like_table_ocr_line(lines[i].strip(), aggressive=True):
                    i += 1
                continue
            if len(line) <= 48:
                html_part = _line_to_html_part(line)
                if html_part:
                    parts.append(html_part)
                continue

        if _looks_like_table_ocr_line(line, aggressive=True):
            continue

        html_part = _line_to_html_part(line)
        if html_part:
            parts.append(html_part)
            blocks.append({"type": "text", "page": pno, "html": html_part})

    for fig in figures:
        if embed_figures_base64 or not (category and doc_id):
            fig_html = _figure_to_html_base64(fig, pno)
        else:
            fig_html = _figure_to_html_asset(fig, pno, category, doc_id)
        if fig_html:
            parts.append(fig_html)
            blocks.append({
                "type": "figure",
                "page": pno,
                "index": fig.get("figure_index", 0),
                "asset": fig.get("parse_asset", ""),
                "html": fig_html,
            })

    return "".join(parts), blocks


def _build_full_html_from_pages(
    pages: List[Dict[str, Any]],
    category: str = "",
    doc_id: str = "",
    embed_figures_base64: bool = False,
    table_registry: Optional[List[Dict[str, Any]]] = None,
    body_start_page: int = 1,
) -> str:
    registry = table_registry or build_table_registry(pages, body_start_page=body_start_page)
    used: Set[str] = set()
    parts: List[str] = []
    for page in pages or []:
        html, _ = _build_page_content_html(
            page,
            category,
            doc_id,
            embed_figures_base64=embed_figures_base64,
            table_registry=registry,
            used_table_ids=used,
        )
        if html:
            parts.append(html)
    if parts:
        return "".join(parts)
    return ""


def _section_text_to_html(text: str, section_no: str = "", section_title: str = "") -> str:
    """章节正文 HTML（仅该节 text，不重复嵌入整页）。"""
    from blueprints.FLGL.GetTestMethod.parser import PAGE_MARKER_RE

    parts: List[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or PAGE_MARKER_RE.match(line):
            continue
        if _line_matches_section_heading(line, section_no, section_title):
            continue
        part = _line_to_html_part(line)
        if part:
            parts.append(part)
    return "".join(parts)


def _sections_should_attach_page_tables(
    pages: List[Dict[str, Any]],
    sections_raw: List[Dict[str, Any]],
) -> bool:
    """
    仅当文档具备真实分页且各章节页码范围可区分时，才按页码向章节挂载表/图。
    Word 原生等单页文档把所有表挂在 page 1，按页挂载会导致每节重复全文表格。
    """
    page_nos = [
        int(p.get("page_number") or p.get("page_num") or 0)
        for p in (pages or [])
        if int(p.get("page_number") or p.get("page_num") or 0) > 0
    ]
    if not page_nos or len(set(page_nos)) <= 1:
        return False
    ranges = {
        (
            int(sec.get("start_page") or 1),
            int(sec.get("end_page") or sec.get("start_page") or 1),
        )
        for sec in (sections_raw or [])
    }
    return len(ranges) > 1


def _page_assets_html_for_range(
    pages: List[Dict[str, Any]],
    start_page: int,
    end_page: int,
    category: str = "",
    doc_id: str = "",
    embed_figures_base64: bool = False,
) -> str:
    """章节片段用：页码范围内的表格/图片（不含正文行）。"""
    page_by_no: Dict[int, Dict[str, Any]] = {}
    for p in pages or []:
        pn = int(p.get("page_number") or p.get("page_num") or 0)
        if pn:
            page_by_no[pn] = p
    parts: List[str] = []
    for pn in range(start_page, end_page + 1):
        page = page_by_no.get(pn)
        if not page:
            continue
        for th in _page_tables_html(page):
            wrapped = _wrap_table_html(str(th))
            if wrapped:
                parts.append(wrapped)
        for fig in page.get("figures") or []:
            if embed_figures_base64 or not (category and doc_id):
                fig_html = _figure_to_html_base64(fig, pn)
            else:
                fig_html = _figure_to_html_asset(fig, pn, category, doc_id)
            if fig_html:
                parts.append(fig_html)
    return "".join(parts)


def _ensure_section_anchors_in_html(html: str, sections: List[Dict[str, Any]]) -> str:
    """为 OCR / Word 未识别为标题行的章节补上 id / data-section-no 锚点。"""
    import html as html_module

    out = html or ""
    block_tags = ("p", "h1", "h2", "h3", "h4", "h5", "h6")
    for sec in sections or []:
        title = clean_document_text(sec.get("title") or sec.get("heading") or "")
        section_no = (sec.get("section_no") or _parse_section_no(title) or "").lstrip(".")
        if not section_no or not title:
            continue
        aid = _section_anchor_id(section_no)
        if f'id="{aid}"' in out or f"id='{aid}'" in out:
            continue
        esc_no = re.escape(section_no)
        title_tail = re.escape(_normalize_section_title_tail(title, section_no))
        tag_group = "|".join(block_tags)
        m = None
        for tag in block_tags:
            pat = re.compile(
                rf'<{tag}(?![^>]*\bwjgl-section-heading\b)(?![^>]*\bid=["\']{re.escape(aid)}["\'])([^>]*)>'
                rf'\s*(?:<strong>\s*)?{esc_no}(?:\s+|\s*</strong>\s*)'
                rf'(?:<strong>\s*)?({title_tail}.*?)(?:</strong>\s*)?</{tag}>',
                re.I | re.S,
            )
            m = pat.search(out)
            if m:
                break
        if not m:
            pat2 = re.compile(
                rf'<(?:{tag_group})(?![^>]*\bwjgl-section-heading\b)([^>]*)>\s*{esc_no}\s+([^<]+?)\s*</(?:{tag_group})>',
                re.I,
            )
            m = pat2.search(out)
            if not m:
                continue
            inner = f"{section_no} {m.group(2).strip()}"
        else:
            inner = f"{section_no} {(m.group(2) or '').strip()}".strip()
        repl = (
            f'<p id="{aid}" class="wjgl-section-heading" '
            f'data-section-no="{html_module.escape(section_no)}" '
            f'style="font-weight:600;margin:10px 0 6px">'
            f"<strong>{html_module.escape(inner)}</strong></p>"
        )
        out = out[: m.start()] + repl + out[m.end() :]
    return out


def build_toc_preview_from_parse_sections(sections: list) -> tuple:
    """
    方法提取向导 UI 目录：过滤封面/目录页噪声，从「1 范围」起展示正式章节。
    04_sections 仍保留全量；提取仍按 section_id。
    """
    return build_toc_direct_from_parse_sections(sections)


def _normalize_section_title_tail(title: str, section_no: str) -> str:
    t = (title or "").strip()
    sn = (section_no or "").lstrip(".")
    if sn and t.startswith(sn):
        return t[len(sn) :].strip()
    return t


def _slim_pages_for_storage(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """MinIO 缓存页级表/图元数据，供 full_html 缺失时重建。"""
    slim: List[Dict[str, Any]] = []
    for page in pages or []:
        pn = int(page.get("page_number") or page.get("page_num") or 0)
        figs: List[Dict[str, Any]] = []
        for fig in page.get("figures") or []:
            if not isinstance(fig, dict):
                continue
            figs.append({
                k: fig.get(k)
                for k in ("parse_asset", "figure_index", "page", "path", "width", "height", "caption")
                if fig.get(k) is not None
            })
        table_items: List[Dict[str, Any]] = []
        for t in page.get("tables_html") or []:
            if isinstance(t, dict):
                table_items.append({
                    k: t.get(k)
                    for k in ("parse_asset", "table_id", "table_no", "html")
                    if t.get(k) is not None
                })
            else:
                hs = str(t or "").strip()
                if hs:
                    table_items.append({"html": hs})
        slim.append({
            "page_number": pn,
            "text": clean_document_text(page.get("text") or ""),
            "tables_html": table_items,
            "figures": figs,
        })
    return slim


def _pages_from_storage(
    raw_pages: List[Dict[str, Any]],
    category: str = "",
    doc_id: str = "",
) -> List[Dict[str, Any]]:
    """从 MinIO pages.json 恢复页结构（图片走 parse_asset URL 或 path）。"""
    out: List[Dict[str, Any]] = []
    for page in raw_pages or []:
        p = dict(page)
        p["tables_html"] = _page_tables_html(p)
        if p.get("page_num") and not p.get("page_number"):
            p["page_number"] = p["page_num"]
        out.append(p)
    return out


def _build_full_html_from_sections(sections: List[Dict[str, Any]]) -> str:
    """勿用于全文拼接（会重复）；保留供兼容。"""
    parts: List[str] = []
    for sec in sections or []:
        h = (sec.get("html") or "").strip()
        if h:
            parts.append(h)
    return trim_html_from_body_start("".join(parts))


def _build_full_document_html_linear(
    full_text: str,
    pages: List[Dict[str, Any]],
    category: str = "",
    doc_id: str = "",
    embed_figures_base64: bool = True,
    sections: Optional[List[Dict[str, Any]]] = None,
    body_start_page: int = 1,
    trim_body: bool = True,
) -> str:
    """全文只构建一次：按页顺序输出正文+表+图，不拼接章节 HTML。"""
    pages = _normalize_page_structures(pages or [], body_start_page=body_start_page)
    page_html = _build_full_html_from_pages(
        pages, category, doc_id,
        embed_figures_base64=embed_figures_base64,
        body_start_page=body_start_page,
    )
    if page_html.strip():
        html = trim_html_from_body_start(page_html) if trim_body else page_html
    else:
        text_html = _full_text_to_document_html(full_text)
        if pages:
            first = int(pages[0].get("page_number") or pages[0].get("page_num") or 1)
            last = int(pages[-1].get("page_number") or pages[-1].get("page_num") or first)
            assets = _page_assets_html_for_range(
                pages, first, last, category, doc_id, embed_figures_base64=embed_figures_base64,
            )
            combined = text_html + assets
            html = trim_html_from_body_start(combined) if trim_body else combined
        else:
            html = trim_html_from_body_start(text_html) if trim_body else text_html

    if sections:
        html = _ensure_section_anchors_in_html(html, sections)
    return html


def _rebuild_document_after_body_trim(
    pages: List[Dict[str, Any]],
    full_text: str,
    page_table_map: Dict[int, List[str]],
    category: str = "",
    doc_id: str = "",
    embed_figures_base64: bool = True,
    file_path: str = "",
    ocr_output_dir: Optional[str] = None,
    word_paragraph_map: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], str, List[Dict[str, Any]], str, int, str, str]:
    """
    按章节重建 sections 与 HTML。
    返回: pages, raw_full_text, sections, body_preview_html, start_page, raw_full_html, structure_method
    full_text 主字段使用 raw（不裁剪）；body_preview_html / raw_full_html 供预览与存储。
    """
    raw_full_text = clean_document_text(full_text or "")

    pages = _enrich_pages_with_table_map(pages, page_table_map)
    pages = _merge_native_pdf_assets(file_path, pages, ocr_output_dir)
    try:
        from blueprints.FLGL.GetTestMethod.parser import _filter_watermark_figures

        pages = _filter_watermark_figures(pages)
    except Exception as e:
        logger.warning("filter watermark figures failed: %s", e)
    pages = _normalize_page_structures(normalize_pages(pages))

    start_page = find_body_start_page(pages)

    structure_method = "structure"
    sections_raw: List[Dict[str, Any]] = []
    try:
        from .document_structure import analyze_document_structure

        structure = analyze_document_structure(
            full_text=raw_full_text,
            file_path=file_path,
            pages=pages,
            word_paragraph_map=word_paragraph_map,
        )
        sections_raw = list(structure.sections or [])
        structure_method = str(structure.method or structure_method)
        if structure.structure_fallback:
            logger.info(
                "[parse] document_structure fallback=%s sections=%d",
                structure.structure_fallback,
                len(sections_raw),
            )
    except Exception as e:
        from blueprints.FLGL.GetTestMethod.parser import split_standard_sections

        logger.warning(
            "[parse] document_structure 不可用（请同步 WJGL1/document_structure.py 等文件），"
            "回退 split_standard_sections: %s",
            e,
        )
        sections_raw = split_standard_sections(raw_full_text)
        structure_method = "split_fallback"

    attach_page_tables = _sections_should_attach_page_tables(pages, sections_raw)
    sections = _build_sections_with_blocks(
        sections_raw,
        page_table_map,
        pages,
        category,
        doc_id,
        embed_figures_base64=embed_figures_base64,
        attach_page_tables=attach_page_tables,
    )

    raw_full_html = _build_full_document_html_linear(
        raw_full_text, pages, category, doc_id,
        embed_figures_base64=embed_figures_base64,
        sections=sections,
        body_start_page=1,
        trim_body=False,
    )
    body_preview_html = trim_html_from_body_start(raw_full_html) if start_page > 1 else raw_full_html
    if not body_preview_html.strip():
        body_preview_html = raw_full_html

    return (
        pages,
        raw_full_text,
        sections,
        body_preview_html,
        start_page,
        raw_full_html,
        structure_method,
    )


def _build_sections_with_blocks(
    sections_raw: List[Dict[str, Any]],
    page_table_map: Dict[int, List[str]],
    pages: List[Dict[str, Any]],
    category: str = "",
    doc_id: str = "",
    embed_figures_base64: bool = False,
    attach_page_tables: bool = True,
) -> List[Dict[str, Any]]:
    """章节 metadata + 片段 HTML（heading + 本节 text；表/图仅在有真实分页时按页挂载）。"""
    page_by_no: Dict[int, Dict[str, Any]] = {}
    for p in pages or []:
        pn = int(p.get("page_number") or p.get("page_num") or 0)
        if pn:
            page_by_no[pn] = p

    out: List[Dict[str, Any]] = []
    for sec in sections_raw:
        sid = sec.get("section_id") or ""
        title = clean_document_text(sec.get("title") or sec.get("heading") or "章节")
        text = clean_document_text(sec.get("text") or "")
        if is_toc_noise_section(title, text):
            continue
        level = int(sec.get("level") or 0)
        start_page = int(sec.get("start_page") or 1)
        end_page = int(sec.get("end_page") or start_page)
        section_no = _parse_section_no(title)

        sec_html_parts: List[str] = []
        blocks: List[Dict[str, Any]] = []
        tables_html: List[str] = []

        if section_no:
            aid = _section_anchor_id(section_no)
            import html as html_module
            heading = (
                f'<p id="{aid}" class="wjgl-section-heading" '
                f'data-section-no="{html_module.escape(section_no)}" '
                f'style="font-weight:600;margin:10px 0 6px">'
                f"<strong>{html_module.escape(title)}</strong></p>"
            )
        else:
            heading = f'<p style="font-weight:600;margin:10px 0 6px"><strong>{title}</strong></p>'

        sec_html_parts.append(heading)
        blocks.append({"type": "heading", "section_no": section_no, "html": heading})

        body_html = _section_text_to_html(text, section_no, title)
        if body_html:
            sec_html_parts.append(body_html)
            blocks.append({"type": "text", "section_no": section_no, "html": body_html})

        if attach_page_tables:
            assets_html = _page_assets_html_for_range(
                pages, start_page, end_page, category, doc_id, embed_figures_base64=embed_figures_base64,
            )
            if assets_html:
                sec_html_parts.append(assets_html)

            for pn in range(start_page, end_page + 1):
                page = page_by_no.get(pn)
                if not page:
                    continue
                for th in _page_tables_html(page):
                    hs = str(th or "").strip()
                    if hs:
                        tables_html.append(hs)
                        blocks.append({"type": "table", "page": pn, "html": _wrap_table_html(hs)})
                for fig in page.get("figures") or []:
                    if embed_figures_base64 or not (category and doc_id):
                        fig_html = _figure_to_html_base64(fig, pn)
                    else:
                        fig_html = _figure_to_html_asset(fig, pn, category, doc_id)
                    if fig_html:
                        blocks.append({
                            "type": "figure",
                            "page": pn,
                            "index": fig.get("figure_index", 0),
                            "html": fig_html,
                        })

        entry: Dict[str, Any] = {
            "section_id": sid,
            "section_no": section_no,
            "title": title,
            "level": level,
            "start_page": start_page,
            "end_page": end_page,
            "text": text,
            "html": "".join(sec_html_parts),
            "blocks": blocks,
            "tables_html": tables_html,
        }
        if sec.get("start_offset") is not None:
            try:
                entry["start_offset"] = int(sec["start_offset"])
            except (TypeError, ValueError):
                pass
        if sec.get("end_offset") is not None:
            try:
                entry["end_offset"] = int(sec["end_offset"])
            except (TypeError, ValueError):
                pass
        if sec.get("subsections"):
            entry["subsections"] = list(sec.get("subsections") or [])
        if sec.get("heading"):
            entry["heading"] = sec.get("heading")
        out.append(entry)
    return out


def build_toc_from_sections(sections: list) -> list:
    """从 parse sections 构建目录树（毫秒级，无 OCR）。"""
    toc, _headings = build_toc_and_headings_from_sections(sections)
    return toc


def build_toc_and_headings_from_sections(sections: list) -> tuple:
    """
    从 parse sections 构建目录树与 flat headings。
    section_id 与 node_slices 完全一致，避免目录与正文切片错位。
    """
    from .body_content_trim import (
        build_toc_tree_from_candidates,
        filter_toc_from_chapter_one,
        is_toc_noise_section,
        is_formal_section_heading,
        is_toc_tree_heading,
        shorten_toc_display_title,
        strip_section_no_from_title,
    )

    candidates: List[Dict[str, Any]] = []
    flat_headings: List[Dict[str, Any]] = []
    best_by_no: Dict[str, Dict[str, Any]] = {}

    def _toc_item_score(item: dict, raw_title: str) -> tuple:
        sno = str(item.get("no") or "")
        formal = 1 if is_formal_section_heading(sno, raw_title) else 0
        text_len = int(item.get("_text_len") or 0)
        short_bonus = max(0, 40 - len(shorten_toc_display_title(raw_title)))
        return (formal, short_bonus, text_len)

    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("section_id") or "").strip()
        raw_title = str(sec.get("title") or sec.get("heading") or "").strip()
        title = raw_title
        text = str(sec.get("text") or "")
        if is_toc_noise_section(title, text):
            continue
        section_no = str(sec.get("section_no") or "").strip().lstrip(".")
        if not section_no:
            continue
        sec_level = int(sec.get("level") or 1)
        if "." not in section_no and sec_level > 1:
            continue
        if not title and not section_no:
            continue
        display_title = strip_section_no_from_title(section_no, title) if section_no else title
        display_title = shorten_toc_display_title(display_title)
        if not is_toc_tree_heading(raw_title, section_no):
            continue
        depth = section_no.count(".") + 1 if section_no else int(sec.get("level") or 1)
        item = {
            "no": section_no,
            "title": display_title or title,
            "depth": depth,
            "page": int(sec.get("start_page") or 99),
            "section_id": sid,
            "_text_len": len(text),
            "_raw_title": raw_title,
        }
        prev = best_by_no.get(section_no)
        if not prev or _toc_item_score(item, raw_title) > _toc_item_score(prev, prev.get("_raw_title", "")):
            best_by_no[section_no] = item

    from .section_number_utils import section_no_sort_key, sort_toc_tree

    for item in sorted(best_by_no.values(), key=lambda x: (x["page"], section_no_sort_key(str(x["no"] or "")))):
        candidates.append(item)
        flat_headings.append({
            "section_id": item["section_id"],
            "sectionNo": item["no"],
            "title": item["title"],
            "level": item["depth"],
            "start_page": item["page"],
            "end_page": None,
            "snippet": "",
            "source": "parse_cache",
        })

    if not candidates:
        return [], []

    toc = filter_toc_from_chapter_one(build_toc_tree_from_candidates(candidates, skip_pages=0))
    if not toc:
        from .document_structure import build_toc_from_sections_simple

        toc = filter_toc_from_chapter_one(build_toc_from_sections_simple(list(sections or [])))
    toc = sort_toc_tree(toc)

    def _attach_section_ids(nodes: list) -> None:
        by_key = {
            (str(c.get("no") or ""), str(c.get("title") or "")): str(c.get("section_id") or "")
            for c in candidates
        }
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            key = (str(node.get("sectionNo") or ""), str(node.get("title") or ""))
            sid = by_key.get(key) or ""
            if sid:
                node["section_id"] = sid
            _attach_section_ids(list(node.get("children") or []))

    _attach_section_ids(toc)
    return toc, flat_headings


def build_toc_direct_from_parse_sections(sections: list) -> tuple:
    """
    直接由 parse sections（调试 04_sections.json 同源）构建目录，不做 heading_extract。
    比 build_toc_and_headings_from_sections 过滤更宽松，保留 MinerU 切出的全部正式章节。
    """
    from .body_content_trim import (
        build_toc_tree_from_candidates,
        filter_toc_from_chapter_one,
        is_toc_noise_section,
        is_formal_section_heading,
        shorten_toc_display_title,
        strip_section_no_from_title,
    )

    candidates: List[Dict[str, Any]] = []
    flat_headings: List[Dict[str, Any]] = []
    best_by_no: Dict[str, Dict[str, Any]] = {}

    def _score(item: dict, raw_title: str) -> tuple:
        sno = str(item.get("no") or "")
        formal = 1 if is_formal_section_heading(sno, raw_title) else 0
        text_len = int(item.get("_text_len") or 0)
        short_bonus = max(0, 40 - len(shorten_toc_display_title(raw_title)))
        sid_bonus = 1 if item.get("section_id") else 0
        return (formal, sid_bonus, short_bonus, text_len)

    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("section_id") or "").strip()
        raw_title = str(sec.get("title") or sec.get("heading") or "").strip()
        text = str(sec.get("text") or "")
        if is_toc_noise_section(raw_title, text):
            continue
        section_no = str(sec.get("section_no") or "").strip().lstrip(".")
        if not section_no and not raw_title:
            continue
        if not section_no:
            continue
        display_title = strip_section_no_from_title(section_no, raw_title) if section_no else raw_title
        display_title = shorten_toc_display_title(display_title or raw_title)
        depth = section_no.count(".") + 1 if section_no else int(sec.get("level") or 1)
        item = {
            "no": section_no,
            "title": display_title or raw_title,
            "depth": depth,
            "page": int(sec.get("start_page") or sec.get("page") or 99),
            "section_id": sid,
            "_text_len": len(text),
            "_raw_title": raw_title,
        }
        prev = best_by_no.get(section_no)
        if not prev or _score(item, raw_title) > _score(prev, prev.get("_raw_title", "")):
            best_by_no[section_no] = item

    from .section_number_utils import section_no_sort_key, sort_toc_tree

    for item in sorted(best_by_no.values(), key=lambda x: (x["page"], section_no_sort_key(str(x["no"] or "")))):
        candidates.append(item)
        flat_headings.append({
            "section_id": item["section_id"],
            "sectionNo": item["no"],
            "title": item["title"],
            "level": item["depth"],
            "start_page": item["page"],
            "end_page": None,
            "snippet": "",
            "source": "parse_sections_direct",
        })

    if not candidates:
        return build_toc_and_headings_from_sections(sections)

    toc = filter_toc_from_chapter_one(build_toc_tree_from_candidates(candidates, skip_pages=0))
    if not toc:
        from .document_structure import build_toc_from_sections_simple

        toc = filter_toc_from_chapter_one(build_toc_from_sections_simple(list(sections or [])))
    toc = sort_toc_tree(toc)

    def _attach_section_ids(nodes: list) -> None:
        by_no: Dict[str, str] = {}
        for c in candidates:
            no = str(c.get("no") or "")
            sid = str(c.get("section_id") or "")
            if no and sid:
                by_no[no] = sid
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            sno = str(node.get("sectionNo") or "")
            if sno and sno in by_no:
                node["section_id"] = by_no[sno]
            _attach_section_ids(list(node.get("children") or []))

    _attach_section_ids(toc)
    return toc, flat_headings


def build_toc_exact_from_parse_sections(sections: list) -> tuple:
    """
    与 04_sections.json **同序、同条数** 构建目录（仅去掉明显噪声节）。
    不做 best_by_no 去重、不按页码重排，避免 UI 目录与调试 04 不一致。
    """
    from .body_content_trim import (
        is_toc_noise_section,
        shorten_toc_display_title,
        strip_section_no_from_title,
    )

    flat_headings: List[Dict[str, Any]] = []
    roots: List[Dict[str, Any]] = []
    stack: List[Tuple[int, Dict[str, Any]]] = []

    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("section_id") or "").strip()
        raw_title = str(sec.get("title") or sec.get("heading") or "").strip()
        text = str(sec.get("text") or "")
        section_no = str(sec.get("section_no") or "").strip().lstrip(".")
        # 04 全量展示：仅跳过无标题且无节号；有正文时才做目录页噪声判断
        if not section_no and not raw_title:
            continue
        if text.strip() and is_toc_noise_section(raw_title, text):
            continue
        display_title = (
            strip_section_no_from_title(section_no, raw_title) if section_no else raw_title
        )
        display_title = shorten_toc_display_title(display_title or raw_title)
        if section_no:
            depth = max(1, int(sec.get("level") or section_no.count(".") + 1))
        else:
            depth = max(1, int(sec.get("level") or 1))

        flat_headings.append({
            "section_id": sid,
            "sectionNo": section_no,
            "title": display_title or raw_title,
            "level": depth,
            "start_page": int(sec.get("start_page") or sec.get("page") or 0),
            "end_page": None,
            "snippet": (text or "")[:120],
            "source": "parse_sections_exact",
        })

        node = {
            "title": display_title or raw_title,
            "sectionNo": section_no,
            "level": depth,
            "section_id": sid,
            "children": [],
        }
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((depth, node))

    if not flat_headings:
        return build_toc_direct_from_parse_sections(sections)
    return roots, flat_headings


def local_parse_ready(category: str, doc_id: str) -> bool:
    if not category or not doc_id:
        return False
    try:
        from pdf_extract.mineru_wjgl_service import local_doc_dir

        d = local_doc_dir(category, doc_id)
        return any(
            (d / name).is_file()
            for name in ("document.md", "document.html", "sections.json", "toc.json")
        )
    except Exception:
        return False


def resolve_document_toc(category: str, doc_id: str, cached: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], str]:
    """
    解析用于方法提取的目录：优先 MinerU Markdown 标题，其次已保存 toc，最后 sections。
    """
    cached = cached or {}
    md = (cached.get("full_markdown") or "").strip()
    if not md and category and doc_id:
        try:
            if Config.MINIO_CLIENT:
                resp = Config.MINIO_CLIENT.get_object(
                    Config.MINIO_BUCKET_NAME,
                    _parse_minio_key(category, doc_id, "document.md"),
                )
                md = resp.read().decode("utf-8")
                resp.close()
                resp.release_conn()
        except Exception:
            md = md or ""
    if md:
        try:
            from pdf_extract.mineru_wjgl_service import build_toc_tree_from_markdown

            toc = build_toc_tree_from_markdown(md)
            if toc:
                return toc, "parse_markdown"
        except Exception as e:
            logger.debug("resolve_document_toc markdown failed: %s", e)

    toc = load_saved_toc(category, doc_id)
    if toc:
        return toc, "saved_toc"

    if cached.get("sections"):
        toc = build_toc_from_sections(cached["sections"])
        if toc:
            return toc, "parse_sections"

    return [], "none"


def save_document_toc(category: str, doc_id: str, toc: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """保存上传/审核时解析的目录，供后续审核与方法提取直接加载（无需重新解析）。"""
    if not category or not doc_id:
        return []
    items = [x for x in (toc or []) if isinstance(x, dict)]
    if not items:
        return []
    try:
        from .body_content_trim import filter_toc_from_chapter_one

        items = filter_toc_from_chapter_one(items)
    except Exception as e:
        logger.debug("filter document toc failed: %s", e)
    if not items:
        return []

    payload = json.dumps({"toc": items}, ensure_ascii=False).encode("utf-8")

    try:
        from pdf_extract.mineru_wjgl_service import local_doc_dir

        local_dir = local_doc_dir(category, doc_id)
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "toc.json").write_text(
            json.dumps({"toc": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug("save local document toc failed: %s", e)

    if Config.MINIO_CLIENT:
        try:
            Config.MINIO_CLIENT.put_object(
                Config.MINIO_BUCKET_NAME,
                _parse_minio_key(category, doc_id, "toc.json"),
                BytesIO(payload),
                length=len(payload),
                content_type="application/json",
            )
        except Exception as e:
            logger.warning("save minio document toc failed: %s", e)

    return items


def save_document_content(
    category: str,
    doc_id: str,
    *,
    html: str = "",
    markdown: str = "",
    mineru_key: str = "",
) -> Dict[str, Any]:
    """保存上传预览阶段的全文 HTML/Markdown，供审核与方法提取直接加载。"""
    if not category or not doc_id:
        return {"saved": False}

    import shutil

    from pdf_extract.mineru_wjgl_service import ensure_parse_bundle_from_markdown, local_doc_dir

    local_dir = local_doc_dir(category, doc_id)
    local_dir.mkdir(parents=True, exist_ok=True)

    mk = (mineru_key or "").strip()
    if mk:
        try:
            from pdf_extract.mineru_preview_service import preview_local_dir

            preview_dir = preview_local_dir(mk)
            if preview_dir.is_dir():
                for name in (
                    "document.html", "document.md", "toc.json", "meta.json",
                    "sections.json", "node_slices.json", "content_list.json",
                ):
                    src = preview_dir / name
                    if src.is_file():
                        shutil.copy2(src, local_dir / name)
                for sub in ("figures", "images", "assets"):
                    src_sub = preview_dir / sub
                    if src_sub.is_dir():
                        dst_sub = local_dir / sub
                        if dst_sub.exists():
                            shutil.rmtree(dst_sub, ignore_errors=True)
                        shutil.copytree(src_sub, dst_sub)
        except Exception as e:
            logger.warning("promote mineru preview failed: %s", e)

    html_text = (html or "").strip()
    md_text = (markdown or "").strip()
    html_path = local_dir / "document.html"
    md_path = local_dir / "document.md"

    if not html_text and html_path.is_file():
        html_text = html_path.read_text(encoding="utf-8", errors="replace").strip()
    if not md_text and md_path.is_file():
        md_text = md_path.read_text(encoding="utf-8", errors="replace").strip()

    if md_text and not html_text:
        rebuilt = ensure_parse_bundle_from_markdown(
            category, doc_id, markdown=md_text, persist_local=True,
        )
        if rebuilt:
            html_text = (rebuilt.get("full_html") or "").strip()
            md_text = (rebuilt.get("full_markdown") or md_text).strip()
    else:
        if html_text:
            html_path.write_text(html_text, encoding="utf-8")
        if md_text:
            md_path.write_text(md_text, encoding="utf-8")

    if not html_text and not md_text:
        return {"saved": False}

    if Config.MINIO_CLIENT:
        bucket = Config.MINIO_BUCKET_NAME
        try:
            if html_text:
                hb = html_text.encode("utf-8")
                Config.MINIO_CLIENT.put_object(
                    bucket,
                    _parse_minio_key(category, doc_id, "full_document.html"),
                    BytesIO(hb),
                    length=len(hb),
                    content_type="text/html; charset=utf-8",
                )
            if md_text:
                mb = md_text.encode("utf-8")
                Config.MINIO_CLIENT.put_object(
                    bucket,
                    _parse_minio_key(category, doc_id, "document.md"),
                    BytesIO(mb),
                    length=len(mb),
                    content_type="text/markdown; charset=utf-8",
                )
        except Exception as e:
            logger.warning("save minio document content failed: %s", e)

    return {
        "saved": True,
        "html_bytes": len(html_text.encode("utf-8")) if html_text else 0,
        "md_bytes": len(md_text.encode("utf-8")) if md_text else 0,
    }


def load_preview_document_content(category: str, doc_id: str) -> Optional[Dict[str, Any]]:
    """轻量读取预览落盘全文（无需完整 sections 解析）。"""
    try:
        from pdf_extract.mineru_wjgl_service import load_local_parse_bundle

        local = load_local_parse_bundle(category, doc_id)
        if local and ((local.get("full_html") or "").strip() or (local.get("full_markdown") or "").strip()):
            local["content_source"] = "preview"
            local["from_cache"] = True
            return local
    except Exception as e:
        logger.debug("load preview local bundle failed: %s", e)

    html = load_full_document_html(category, doc_id) or ""
    md = ""
    if Config.MINIO_CLIENT and category and doc_id:
        try:
            resp = Config.MINIO_CLIENT.get_object(
                Config.MINIO_BUCKET_NAME,
                _parse_minio_key(category, doc_id, "document.md"),
            )
            md = resp.read().decode("utf-8")
            resp.close()
            resp.release_conn()
        except Exception:
            md = ""

    if html.strip() or md.strip():
        return {
            "full_html": html,
            "full_markdown": md,
            "sections": [],
            "node_slices": {},
            "document_blocks": [],
            "from_cache": True,
            "content_source": "preview",
        }
    return None


def load_saved_toc(category: str, doc_id: str) -> List[Dict[str, Any]]:
    """优先读取入库时保存的 toc.json（本地 → MinIO）。"""
    try:
        from pdf_extract.mineru_wjgl_service import load_local_toc

        toc = load_local_toc(category, doc_id)
        if toc:
            from .body_content_trim import filter_toc_from_chapter_one

            return filter_toc_from_chapter_one(toc)
    except Exception as e:
        logger.debug("load local toc failed: %s", e)

    if not Config.MINIO_CLIENT or not category or not doc_id:
        return []
    try:
        idx = load_section_index(category, doc_id)
        if idx and idx.get("toc"):
            from .body_content_trim import filter_toc_from_chapter_one

            return filter_toc_from_chapter_one(list(idx["toc"]))
    except Exception:
        pass
    try:
        resp = Config.MINIO_CLIENT.get_object(
            Config.MINIO_BUCKET_NAME,
            _parse_minio_key(category, doc_id, "toc.json"),
        )
        raw = json.loads(resp.read().decode("utf-8"))
        resp.close()
        resp.release_conn()
        toc = list(raw.get("toc") or [])
        if toc:
            from .body_content_trim import filter_toc_from_chapter_one

            return filter_toc_from_chapter_one(toc)
    except Exception:
        pass
    return []


def _persist_section_slices_local(category: str, doc_id: str, slices: Dict[str, str], slices_md: Optional[Dict[str, str]] = None) -> None:
    if not slices and not slices_md:
        return
    try:
        from pdf_extract.mineru_wjgl_service import local_doc_dir

        base = local_doc_dir(category, doc_id) / "section_slices"
        base.mkdir(parents=True, exist_ok=True)
        for sid, html in (slices or {}).items():
            if sid and html:
                (base / f"{sid}.html").write_text(html, encoding="utf-8")
        for sid, md in (slices_md or {}).items():
            if sid and md:
                (base / f"{sid}.md").write_text(md, encoding="utf-8")
    except Exception as e:
        logger.debug("persist local section slices failed: %s", e)


def save_preview_parse_local_bundle(
    preview_cache_key: str,
    parse_cache: Optional[Dict[str, Any]],
    *,
    full_text: str = "",
    source: str = "",
    category: str = "_preview",
) -> Optional[str]:
    """
    将 FLGL 预览解析产物落盘到 data/wjgl_documents/_preview/{key}/，
    与 flgl_extract_debug 目录结构对齐，供章节提取与溯源读取。
    """
    key = str(preview_cache_key or "").strip()
    if not key or not isinstance(parse_cache, dict):
        return None
    try:
        import re
        import shutil
        from datetime import datetime, timezone
        from pdf_extract.mineru_wjgl_service import local_doc_dir

        local_dir = local_doc_dir(category, key)
        local_dir.mkdir(parents=True, exist_ok=True)

        sections = list(parse_cache.get("sections") or [])
        node_slices = dict(parse_cache.get("node_slices") or {})
        node_slices_md = dict(parse_cache.get("node_slices_md") or {})
        full_md = str(parse_cache.get("full_markdown") or "").strip()
        full_html = str(parse_cache.get("full_html") or parse_cache.get("raw_full_html") or "").strip()

        if full_md:
            (local_dir / "document.md").write_text(full_md, encoding="utf-8")
        if full_html:
            (local_dir / "document.html").write_text(full_html, encoding="utf-8")
        if sections:
            (local_dir / "sections.json").write_text(
                json.dumps({"sections": sections}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (local_dir / "04_sections.json").write_text(
                json.dumps(sections, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        def _safe_sid(sid: str) -> str:
            return re.sub(r"[^\w\-]", "_", str(sid))[:80]

        slices_md_dir = local_dir / "05_node_slices_md"
        slices_html_dir = local_dir / "05_node_slices"
        legacy_dir = local_dir / "section_slices"
        for d in (slices_md_dir, slices_html_dir, legacy_dir):
            d.mkdir(parents=True, exist_ok=True)

        for sid, md in node_slices_md.items():
            if sid and md:
                safe = _safe_sid(sid)
                (slices_md_dir / f"{safe}.md").write_text(str(md), encoding="utf-8")
                (legacy_dir / f"{sid}.md").write_text(str(md), encoding="utf-8")
        for sid, html in node_slices.items():
            if sid and html:
                safe = _safe_sid(sid)
                (slices_html_dir / f"{safe}.html").write_text(str(html), encoding="utf-8")
                (legacy_dir / f"{sid}.html").write_text(str(html), encoding="utf-8")

        if node_slices:
            (local_dir / "node_slices.json").write_text(
                json.dumps({"slices": node_slices}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        pd = parse_cache.get("parsed_document") if isinstance(parse_cache.get("parsed_document"), dict) else {}
        src_images = str(pd.get("local_dir") or pd.get("images_dir") or "").strip()
        images_dst = local_dir / "images"
        if src_images:
            src_path = Path(src_images)
            src_images_dir = src_path / "images" if src_path.is_dir() and (src_path / "images").is_dir() else src_path
            if src_images_dir.is_dir() and src_images_dir.name == "images":
                if images_dst.exists():
                    shutil.rmtree(images_dst, ignore_errors=True)
                shutil.copytree(src_images_dir, images_dst, dirs_exist_ok=True)

        meta = {
            "preview_cache_key": key,
            "category": category,
            "source": source or str(parse_cache.get("parse_mode") or ""),
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "sections_count": len(sections),
            "node_slices_count": len(node_slices),
            "node_slices_md_count": len(node_slices_md),
            "full_markdown_len": len(full_md),
            "full_html_len": len(full_html),
            "full_text_len": len(str(full_text or "")),
            "local_dir": str(local_dir),
        }
        (local_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "[preview-bundle] saved key=%s sections=%s md_slices=%s dir=%s",
            key[:12], len(sections), len(node_slices_md), local_dir,
        )
        return str(local_dir)
    except Exception as exc:
        logger.warning("[preview-bundle] save failed key=%s: %s", preview_cache_key[:12] if preview_cache_key else "", exc)
        return None


def _persist_section_slices_to_minio(
    category: str,
    doc_id: str,
    slices: Dict[str, str],
    sections: Optional[List[Dict[str, Any]]] = None,
    slices_md: Optional[Dict[str, str]] = None,
) -> None:
    if not Config.MINIO_CLIENT or (not slices and not slices_md):
        return
    bucket = Config.MINIO_BUCKET_NAME
    sec_map = {str(s.get("section_id")): s for s in (sections or []) if s.get("section_id")}
    all_sids = set((slices or {}).keys()) | set((slices_md or {}).keys())
    for sid in all_sids:
        html = clean_document_html((slices or {}).get(sid) or "")
        md = str((slices_md or {}).get(sid) or "").strip()
        if not html.strip() and not md:
            continue
        sec = sec_map.get(str(sid), {})
        section_no = str(sec.get("section_no") or "").strip().lstrip(".")
        if not section_no:
            section_no = _parse_section_no(str(sec.get("title") or "")).lstrip(".")
        title = str(sec.get("title") or "")[:120]
        meta = {
            "section-id": to_minio_meta_value(str(sid), 128),
            "section-no": to_minio_meta_value(section_no, 64),
            "section-title": to_minio_meta_value(title, 120),
            "doc-id": to_minio_meta_value(str(doc_id), 64),
            "category": to_minio_meta_value(str(category), 64),
        }
        if html.strip():
            payload = html.encode("utf-8")
            Config.MINIO_CLIENT.put_object(
                bucket,
                section_slice_minio_key(category, doc_id, sid),
                BytesIO(payload),
                length=len(payload),
                content_type="text/html; charset=utf-8",
                metadata=meta,
            )
        if md:
            md_bytes = md.encode("utf-8")
            Config.MINIO_CLIENT.put_object(
                bucket,
                section_slice_md_minio_key(category, doc_id, sid),
                BytesIO(md_bytes),
                length=len(md_bytes),
                content_type="text/markdown; charset=utf-8",
                metadata=meta,
            )
        plain = _html_to_plain_text(html) if html.strip() else md
        plain = plain or str(sec.get("text") or "").strip()
        if plain:
            txt_bytes = plain.encode("utf-8")
            Config.MINIO_CLIENT.put_object(
                bucket,
                section_text_minio_key(category, doc_id, sid),
                BytesIO(txt_bytes),
                length=len(txt_bytes),
                content_type="text/plain; charset=utf-8",
                metadata=meta,
            )


def section_slice_minio_key(category: str, doc_id: str, section_id: str) -> str:
    return _parse_minio_key(category, doc_id, f"section_slices/{section_id}.html")


def section_slice_md_minio_key(category: str, doc_id: str, section_id: str) -> str:
    return _parse_minio_key(category, doc_id, f"section_slices/{section_id}.md")


def section_text_minio_key(category: str, doc_id: str, section_id: str) -> str:
    return _parse_minio_key(category, doc_id, f"section_slices/{section_id}.txt")


def _html_to_plain_text(html: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", t).strip()


def _section_no_sort_key(section_no: str) -> tuple:
    from .section_number_utils import section_no_sort_key

    return section_no_sort_key(section_no)


def build_section_index(
    category: str,
    doc_id: str,
    sections: List[Dict[str, Any]],
    slices: Dict[str, str],
    toc: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """章节索引：目录 + 各节 MinIO 对象路径（供方法提取 / 向量库）。"""
    from .body_content_trim import strip_section_no_from_title

    sec_map = {str(s.get("section_id")): s for s in (sections or []) if s.get("section_id")}
    seen: Set[str] = set()
    entries: List[Dict[str, Any]] = []

    def _append_entry(sid: str, sec: Dict[str, Any], html: str = "") -> None:
        if not sid or sid in seen:
            return
        title = str(sec.get("title") or "").strip()
        section_no = str(sec.get("section_no") or "").strip().lstrip(".")
        if not section_no and title:
            section_no = _parse_section_no(title).lstrip(".")
        display_title = strip_section_no_from_title(section_no, title) if section_no else title
        plain = _html_to_plain_text(html) if html else str(sec.get("text") or "").strip()
        entries.append({
            "section_id": sid,
            "section_no": section_no,
            "title": display_title or title,
            "level": section_no.count(".") + 1 if section_no else int(sec.get("level") or 1),
            "html_key": section_slice_minio_key(category, doc_id, sid),
            "text_key": section_text_minio_key(category, doc_id, sid),
            "char_count": len(plain),
            "html_bytes": len(html.encode("utf-8")) if html else 0,
        })
        seen.add(sid)

    for sid, html in (slices or {}).items():
        _append_entry(str(sid), sec_map.get(str(sid), {}), html or "")
    for sec in sections or []:
        sid = str(sec.get("section_id") or "")
        if sid and sid not in seen and str(sec.get("text") or "").strip():
            _append_entry(sid, sec)

    entries.sort(key=lambda e: _section_no_sort_key(str(e.get("section_no") or "")))

    return {
        "version": 1,
        "category": category,
        "doc_id": doc_id,
        "toc_key": _parse_minio_key(category, doc_id, "toc.json"),
        "section_count": len(entries),
        "entries": entries,
        "toc": toc or [],
    }


def save_section_index_to_minio(category: str, doc_id: str, index: Dict[str, Any]) -> None:
    if not Config.MINIO_CLIENT or not index:
        return
    payload = json.dumps(index, ensure_ascii=False).encode("utf-8")
    Config.MINIO_CLIENT.put_object(
        Config.MINIO_BUCKET_NAME,
        _parse_minio_key(category, doc_id, "section_index.json"),
        BytesIO(payload),
        length=len(payload),
        content_type="application/json",
    )


def load_section_index(category: str, doc_id: str) -> Optional[Dict[str, Any]]:
    try:
        from pdf_extract.mineru_wjgl_service import local_doc_dir

        local_path = local_doc_dir(category, doc_id) / "section_index.json"
        if local_path.is_file():
            return json.loads(local_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    if not Config.MINIO_CLIENT:
        return None
    try:
        resp = Config.MINIO_CLIENT.get_object(
            Config.MINIO_BUCKET_NAME,
            _parse_minio_key(category, doc_id, "section_index.json"),
        )
        raw = json.loads(resp.read().decode("utf-8"))
        resp.close()
        resp.release_conn()
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def parse_artifacts_ready(category: str, doc_id: str) -> bool:
    if category == "_preview" and Config.MINIO_CLIENT:
        try:
            Config.MINIO_CLIENT.stat_object(
                Config.MINIO_BUCKET_NAME,
                _parse_minio_key(category, doc_id, "sections.json"),
            )
            return True
        except Exception:
            pass
    if local_parse_ready(category, doc_id):
        return True
    if load_section_index(category, doc_id):
        return True
    if load_saved_toc(category, doc_id):
        return True
    if not Config.MINIO_CLIENT:
        return False
    try:
        Config.MINIO_CLIENT.stat_object(
            Config.MINIO_BUCKET_NAME,
            _parse_minio_key(category, doc_id, "sections.json"),
        )
        return True
    except Exception:
        return False


def load_section_html_from_minio(category: str, doc_id: str, section_id: str) -> str:
    if not section_id:
        return ""
    html = ""
    key = section_slice_minio_key(category, doc_id, section_id)
    if Config.MINIO_CLIENT:
        try:
            resp = Config.MINIO_CLIENT.get_object(Config.MINIO_BUCKET_NAME, key)
            html = resp.read().decode("utf-8")
            resp.close()
            resp.release_conn()
        except Exception:
            pass
    if not html.strip():
        try:
            from pdf_extract.mineru_wjgl_service import local_doc_dir

            fp = local_doc_dir(category, doc_id) / "section_slices" / f"{section_id}.html"
            if fp.is_file():
                html = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if html.strip():
        html = clean_document_html(html)
        if not is_garbled_content(html):
            return html
    return ""


def load_section_markdown_from_minio(category: str, doc_id: str, section_id: str) -> str:
    if not section_id:
        return ""
    md = ""
    key = section_slice_md_minio_key(category, doc_id, section_id)
    if Config.MINIO_CLIENT:
        try:
            resp = Config.MINIO_CLIENT.get_object(Config.MINIO_BUCKET_NAME, key)
            md = resp.read().decode("utf-8")
            resp.close()
            resp.release_conn()
        except Exception:
            pass
    if not md.strip():
        try:
            from pdf_extract.mineru_wjgl_service import local_doc_dir

            fp = local_doc_dir(category, doc_id) / "section_slices" / f"{section_id}.md"
            if fp.is_file():
                md = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return (md or "").strip()


def _fetch_section_html_from_markdown(
    category: str,
    doc_id: str,
    section_no: str,
    *,
    section_title: str = "",
    include_children: bool = True,
) -> str:
    """MinerU Markdown 按节号切分（parse 切片缺失时的兜底）。"""
    try:
        from pdf_extract.mineru_wjgl_service import (
            ensure_parse_bundle_from_markdown,
            local_doc_dir,
        )

        local_dir = local_doc_dir(category, doc_id)
        md_path = local_dir / "document.md"
        md = ""
        if md_path.is_file():
            md = md_path.read_text(encoding="utf-8", errors="replace")
        if not md.strip() and Config.MINIO_CLIENT:
            try:
                resp = Config.MINIO_CLIENT.get_object(
                    Config.MINIO_BUCKET_NAME,
                    _parse_minio_key(category, doc_id, "document.md"),
                )
                md = resp.read().decode("utf-8")
                resp.close()
                resp.release_conn()
            except Exception:
                md = ""
        if not md.strip():
            return ""

        bundle = ensure_parse_bundle_from_markdown(
            category, doc_id, markdown=md, persist_local=True,
        )
        if not bundle:
            return ""
        sections = bundle.get("sections") or []
        slices = bundle.get("node_slices") or {}
        pno = (section_no or "").strip().lstrip(".")
        target_ids: List[str] = []
        for sec in sections:
            sid = str(sec.get("section_id") or "")
            if not sid:
                continue
            sno = str(sec.get("section_no") or "").strip().lstrip(".")
            if not sno:
                sno = _parse_section_no(str(sec.get("title") or "")).lstrip(".")
            title = str(sec.get("title") or "")
            if pno:
                if sno == pno or (include_children and sno.startswith(pno + ".")):
                    target_ids.append(sid)
            elif section_title and section_title in title:
                target_ids.append(sid)
        if target_ids:
            return merge_section_html(target_ids, sections, slices)
    except Exception as e:
        logger.warning("[section-md-fallback] failed doc=%s sec=%s: %s", doc_id, section_no, e)
    return ""


def fetch_section_content_html(
    category: str,
    doc_id: str,
    section_no: str = "",
    *,
    section_title: str = "",
    include_children: bool = True,
    sections: Optional[List[Dict[str, Any]]] = None,
    node_slices: Optional[Dict[str, str]] = None,
) -> str:
    """按章节号从 MinIO 读取已保存的正文 HTML（含子节）。"""
    pno = (section_no or "").strip().lstrip(".")
    try:
        from pdf_extract.mineru_wjgl_service import load_local_parse_bundle

        load_local_parse_bundle(category, doc_id)
    except Exception:
        pass

    index = load_section_index(category, doc_id)
    parts: List[str] = []

    if index and index.get("entries"):
        matched = []
        for entry in index["entries"]:
            sno = str(entry.get("section_no") or "").strip().lstrip(".")
            title = str(entry.get("title") or "")
            if not sno:
                sno = _parse_section_no(title).lstrip(".")
            if pno:
                if sno == pno or (include_children and sno.startswith(pno + ".")):
                    matched.append(entry)
            elif section_title and section_title in title:
                matched.append(entry)
        matched.sort(key=lambda e: _section_no_sort_key(str(e.get("section_no") or "")))
        for entry in matched:
            sid = str(entry.get("section_id") or "")
            html = load_section_html_from_minio(category, doc_id, sid)
            if html.strip():
                parts.append(html)
        if parts:
            html = clean_document_html("".join(parts))
            if html.strip() and not is_garbled_content(html):
                return html

    sections = sections or []
    slices = node_slices or {}
    if not sections and Config.MINIO_CLIENT:
        try:
            resp = Config.MINIO_CLIENT.get_object(
                Config.MINIO_BUCKET_NAME,
                _parse_minio_key(category, doc_id, "sections.json"),
            )
            sections = json.loads(resp.read().decode("utf-8")).get("sections") or []
            resp.close()
            resp.release_conn()
        except Exception:
            sections = []
    if not slices and sections:
        slices = _load_section_slices_from_minio(category, doc_id, sections)

    target_ids: List[str] = []
    for sec in sections:
        sid = str(sec.get("section_id") or "")
        if not sid:
            continue
        sno = str(sec.get("section_no") or "").strip().lstrip(".")
        if not sno:
            sno = _parse_section_no(str(sec.get("title") or "")).lstrip(".")
        title = str(sec.get("title") or "")
        if pno:
            if sno == pno or (include_children and sno.startswith(pno + ".")):
                target_ids.append(sid)
        elif section_title and section_title in title:
            target_ids.append(sid)
    if target_ids:
        html = clean_document_html(merge_section_html(target_ids, sections, slices))
        if html.strip() and not is_garbled_content(html):
            return html

    html = _fetch_section_html_from_markdown(
        category,
        doc_id,
        pno,
        section_title=section_title,
        include_children=include_children,
    )
    if html.strip():
        return clean_document_html(html)
    return ""


def _enrich_sections_with_minio_keys(
    category: str,
    doc_id: str,
    sections: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for sec in sections or []:
        item = dict(sec)
        sid = str(item.get("section_id") or "")
        if sid:
            item["content_html_key"] = section_slice_minio_key(category, doc_id, sid)
            item["content_text_key"] = section_text_minio_key(category, doc_id, sid)
        if not item.get("section_no"):
            item["section_no"] = _parse_section_no(str(item.get("title") or "")).lstrip(".")
        out.append(item)
    return out


def _load_section_slices_from_minio(category: str, doc_id: str, sections: List[Dict[str, Any]]) -> Dict[str, str]:
    if not Config.MINIO_CLIENT:
        return {}
    bucket = Config.MINIO_BUCKET_NAME
    out: Dict[str, str] = {}
    for sec in sections or []:
        sid = str(sec.get("section_id") or "")
        if not sid:
            continue
        try:
            resp = Config.MINIO_CLIENT.get_object(
                bucket,
                section_slice_minio_key(category, doc_id, sid),
            )
            html = resp.read().decode("utf-8")
            resp.close()
            resp.release_conn()
            if html.strip():
                out[sid] = html
        except Exception:
            continue
    return out


def finalize_parse_data(category: str, doc_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    上传入库前终态化：图片落 MinIO、按页序 blocks、全文 HTML。
    审查页只读此结果，不再重复 OCR。
    """
    parsed_doc = dict(data.get("parsed_document") or {})
    parse_mode = str(parsed_doc.get("parse_mode") or "")
    is_mineru = parse_mode == "pdf_mineru" or bool((data.get("full_markdown") or "").strip())

    # MinerU 已产出 MD/章节切片时，禁止用 fitz pages 覆盖（否则目录/正文会错文件）
    if is_mineru and (data.get("sections") or (data.get("full_markdown") or "").strip()):
        sections = list(data.get("sections") or [])
        full_markdown = (data.get("full_markdown") or "").strip()
        node_slices_md = dict(data.get("node_slices_md") or {})
        if not node_slices_md and full_markdown and sections:
            node_slices_md = _build_node_slices_md(sections, full_markdown=full_markdown)
        node_slices = dict(data.get("node_slices") or {})
        if not node_slices and sections:
            node_slices = _build_node_slices(
                sections,
                full_markdown=full_markdown,
                node_slices_md=node_slices_md,
                category=category,
                doc_id=doc_id,
            )
        full_html = (data.get("full_html") or "").strip()
        toc = list(data.get("toc") or [])
        if not toc and full_markdown:
            try:
                from pdf_extract.mineru_wjgl_service import build_toc_tree_from_markdown

                toc = build_toc_tree_from_markdown(full_markdown)
            except Exception:
                toc = build_toc_from_sections(sections)
        elif not toc and sections:
            toc = build_toc_from_sections(sections)

        _persist_section_slices_local(category, doc_id, node_slices, node_slices_md)

        document_blocks = [
            {
                "section_id": s.get("section_id"),
                "section_no": s.get("section_no", ""),
                "title": s.get("title", ""),
                "blocks": s.get("blocks") or [],
            }
            for s in sections
        ]
        stats = dict(data.get("stats") or {})
        stats.update({
            "section_count": len(sections),
            "block_count": sum(len(s.get("blocks") or []) for s in sections),
        })
        parsed_doc["stats"] = stats
        parsed_doc["has_full_html"] = bool(full_html)
        return {
            **data,
            "sections": sections,
            "node_slices": node_slices,
            "node_slices_md": node_slices_md,
            "full_html": full_html,
            "full_markdown": full_markdown,
            "toc": toc,
            "document_blocks": document_blocks,
            "parsed_document": parsed_doc,
            "stats": stats,
        }

    pages = list(data.get("pages") or [])
    full_text = data.get("full_text") or ""
    page_table_map = data.get("page_table_map") or {}

    if pages:
        pages = _persist_parse_figures_to_minio(category, doc_id, pages)

    pages, full_text, sections, full_html, start_page, raw_full_html, structure_method = _rebuild_document_after_body_trim(
        pages,
        full_text,
        page_table_map,
        category,
        doc_id,
        embed_figures_base64=True,
        file_path=str(data.get("file_path") or (data.get("parsed_document") or {}).get("file_path") or ""),
        ocr_output_dir=data.get("ocr_output_dir"),
    )

    if pages:
        pages, table_registry = _persist_parse_tables_to_minio(category, doc_id, pages, body_start_page=start_page)
    else:
        table_registry = []
    full_markdown = str(data.get("full_markdown") or parsed_doc.get("full_markdown") or "").strip()
    node_slices_md = _build_node_slices_md(sections, full_markdown=full_markdown)
    node_slices = _build_node_slices(
        sections,
        full_markdown=full_markdown,
        node_slices_md=node_slices_md,
        category=category,
        doc_id=doc_id,
    )

    document_blocks = [
        {
            "section_id": s.get("section_id"),
            "section_no": s.get("section_no", ""),
            "title": s.get("title", ""),
            "blocks": s.get("blocks") or [],
        }
        for s in sections
    ]

    stats = dict(data.get("stats") or {})
    stats.update({
        "block_count": sum(len(s.get("blocks") or []) for s in sections),
        "figure_count": sum(
            1 for s in sections for b in (s.get("blocks") or []) if b.get("type") == "figure"
        ),
        "table_count": len(table_registry) or sum(
            1 for s in sections for b in (s.get("blocks") or []) if b.get("type") == "table"
        ),
    })

    parsed_doc = dict(data.get("parsed_document") or {})
    parsed_doc["stats"] = stats
    parsed_doc["has_full_html"] = bool(full_html)
    parsed_doc["body_start_page"] = start_page
    parsed_doc["raw_full_text"] = full_text
    parsed_doc["raw_full_html"] = raw_full_html
    parsed_doc["structure_method"] = structure_method

    return {
        **data,
        "pages": pages,
        "sections": sections,
        "node_slices": node_slices,
        "node_slices_md": node_slices_md,
        "full_html": full_html,
        "full_markdown": full_markdown,
        "document_blocks": document_blocks,
        "parsed_document": parsed_doc,
        "stats": stats,
        "table_registry": table_registry,
    }


def _plain_text_len_in_html(html: str) -> int:
    return len(re.sub(r"<[^>]+>", "", html or "").strip())


def _full_text_to_document_html(full_text: str) -> str:
    from blueprints.FLGL.GetTestMethod.parser import PAGE_MARKER_RE

    parts: List[str] = []
    for line in (full_text or "").splitlines():
        line = line.strip()
        if not line or PAGE_MARKER_RE.match(line):
            continue
        part = _line_to_html_part(line)
        if part:
            parts.append(part)
    return "".join(parts)


def _collect_figure_html_from_pages(
    pages: List[Dict[str, Any]],
    category: str = "",
    doc_id: str = "",
    embed_figures_base64: bool = True,
) -> str:
    parts: List[str] = []
    for page in pages or []:
        for fig in page.get("figures") or []:
            pno = int(page.get("page_number") or page.get("page_num") or 0)
            if embed_figures_base64 or not (category and doc_id):
                fig_html = _figure_to_html_base64(fig, pno)
            else:
                fig_html = _figure_to_html_asset(fig, pno, category, doc_id)
            if fig_html:
                parts.append(fig_html)
    return "".join(parts)


def build_full_document_html(
    file_path: str,
    ocr_output_dir: Optional[str] = None,
    embed_figures: bool = True,
    *,
    preview: bool = False,
) -> str:
    """上传预览用：即时解析全文（不入库）。preview=True 时用 fitz 快速预览，避免 MinerU 阻塞。"""
    data = parse_local_file(
        file_path,
        ocr_output_dir=ocr_output_dir,
        fast=preview,
    )
    html = (data.get("full_html") or "").strip()
    if not html:
        html = _build_full_document_html_linear(
            data.get("full_text") or "",
            list(data.get("pages") or []),
            embed_figures_base64=bool(embed_figures),
        )
    if not embed_figures and html:
        html = re.sub(
            r"<figure[^>]*>.*?</figure>",
            '<p style="color:#888">[图]</p>',
            html,
            flags=re.S | re.I,
        )
    logger.info(
        "build_full_document_html file=%s html_bytes=%s text_chars=%s pages=%s embed_figures=%s body_start=%s",
        os.path.basename(file_path),
        len(html or ""),
        _plain_text_len_in_html(html or ""),
        len(data.get("pages") or []),
        embed_figures,
        data.get("parsed_document", {}).get("body_start_page"),
    )
    return html or ""


def _build_node_slices_md(
    sections: List[Dict[str, Any]],
    *,
    full_markdown: str = "",
) -> Dict[str, str]:
    md = (full_markdown or "").strip()
    if not md or not sections:
        return {}
    try:
        from pdf_extract.mineru_wjgl_service import slice_markdown_by_sections

        return slice_markdown_by_sections(md, sections)
    except Exception as exc:
        logger.debug("[parse] node_slices_md failed: %s", exc)
        return {}


def _markdown_chunk_to_html(md: str, *, category: str = "", doc_id: str = "") -> str:
    chunk = (md or "").strip()
    if not chunk:
        return ""
    if re.search(r"<\s*table\b", chunk, re.I):
        return clean_document_html(chunk)
    try:
        from pathlib import Path
        from pdf_extract.mineru_wjgl_service import local_doc_dir, markdown_to_wjgl_html

        local_dir = local_doc_dir(category, doc_id) if category and doc_id else Path(".")
        return markdown_to_wjgl_html(
            chunk,
            doc_id=doc_id or "",
            category=category or "",
            local_dir=local_dir,
        )
    except Exception as exc:
        logger.debug("[parse] markdown_to_wjgl_html failed: %s", exc)
        return _text_to_html(chunk)


def _build_node_slices(
    sections: List[Dict[str, Any]],
    *,
    raw_full_text: str = "",
    full_markdown: str = "",
    node_slices_md: Optional[Dict[str, str]] = None,
    category: str = "",
    doc_id: str = "",
) -> Dict[str, str]:
    md_map = dict(node_slices_md or {})
    if not md_map and (full_markdown or "").strip():
        md_map = _build_node_slices_md(sections, full_markdown=full_markdown)

    slices: Dict[str, str] = {}
    for sec in sections or []:
        sid = sec.get("section_id")
        if not sid:
            continue
        sec_title = str(sec.get("title") or sec.get("heading") or "")
        md_chunk = str(md_map.get(sid) or "").strip()
        html_sec = str(sec.get("html") or "").strip()
        if md_chunk:
            try:
                from blueprints.FLGL.method_section_enrich import (
                    content_has_embedded_figure,
                    content_looks_title_only,
                )

                if content_looks_title_only(md_chunk, sec_title):
                    md_chunk = ""
                elif not content_has_embedded_figure(md_chunk) and content_has_embedded_figure(html_sec):
                    slices[sid] = clean_document_html(html_sec)
                    continue
            except Exception:
                pass
        if md_chunk:
            html_chunk = _markdown_chunk_to_html(md_chunk, category=category, doc_id=doc_id)
            if html_chunk.strip():
                slices[sid] = html_chunk.strip()
                continue
        html_chunk = str(sec.get("html") or "").strip()
        if html_chunk:
            slices[sid] = clean_document_html(html_chunk)
        else:
            slices[sid] = _text_to_html(sec.get("text") or "")
    return slices


def parse_local_file(
    file_path: str,
    ocr_output_dir: Optional[str] = None,
    fast: bool = False,
) -> Dict[str, Any]:
    """解析本地文件，返回 sections / parsed_document / node_slices。"""
    from blueprints.FLGL.GetTestMethod.parser import parse_standard_file, split_standard_sections

    ext = os.path.splitext(file_path)[1].lower()
    if fast and ext == ".pdf":
        return _parse_local_fitz_fast(file_path)

    ocr_dir = ocr_output_dir or os.path.join(tempfile.gettempdir(), "wjgl_ocr_cache")
    os.makedirs(ocr_dir, exist_ok=True)

    if ext in (".docx", ".doc"):
        try:
            parsed = parse_standard_file(file_path, ocr_output_dir=ocr_dir)
        except Exception as e:
            logger.warning("Word 原生 parse_standard_file 失败，回退通用解析: %s", e)
            parsed = None
        if parsed is not None:
            full_text = clean_document_text(parsed.get("full_text") or "")
            pages = _normalize_page_structures(normalize_pages(list(parsed.get("pages") or [])))
            native_html = (parsed.get("full_html") or "").strip()
            page_table_map = parsed.get("page_table_map") or {}
            pages, full_text, sections, full_html, start_page, raw_full_html, structure_method = _rebuild_document_after_body_trim(
                pages,
                full_text,
                page_table_map,
                embed_figures_base64=True,
                file_path=file_path,
                ocr_output_dir=ocr_dir,
                word_paragraph_map=parsed.get("word_paragraph_map"),
            )
            if native_html:
                raw_full_html = native_html
                full_html = trim_html_from_body_start(native_html)
            full_markdown = str(parsed.get("full_markdown") or "").strip()
            node_slices_md = _build_node_slices_md(sections, full_markdown=full_markdown)
            node_slices = _build_node_slices(
                sections,
                full_markdown=full_markdown,
                node_slices_md=node_slices_md,
            )
            try:
                from .document_structure import build_section_catalogue

                section_catalogue = build_section_catalogue(sections)
            except Exception:
                section_catalogue = []
            parsed_doc = {
                "file_name": parsed.get("file_name"),
                "file_path": os.path.abspath(file_path),
                "parse_mode": parsed.get("parse_mode") or "docx_native",
                "ocr_used": False,
                "full_text": full_text,
                "raw_full_text": full_text,
                "raw_full_html": raw_full_html,
                "full_markdown": full_markdown,
                "structure_method": structure_method,
                "word_paragraph_map": parsed.get("word_paragraph_map") or [],
                "stats": parsed.get("stats") or {},
                "garbled_score": garbled_score(full_text),
                "page_count": len(pages),
                "section_catalogue": section_catalogue,
                "body_start_page": start_page,
            }
            return {
                "sections": sections,
                "full_text": full_text,
                "full_html": full_html,
                "raw_full_html": raw_full_html,
                "full_markdown": full_markdown,
                "node_slices_md": node_slices_md,
                "pages": pages,
                "file_path": os.path.abspath(file_path),
                "page_table_map": page_table_map,
                "parsed_document": parsed_doc,
                "node_slices": node_slices,
                "section_catalogue": section_catalogue,
                "stats": parsed_doc.get("stats") or {},
            }

    if ext == ".pdf":
        try:
            from pdf_extract.mineru_wjgl_service import parse_pdf_for_wjgl_local, use_mineru_for_wjgl_pdf

            if use_mineru_for_wjgl_pdf():
                return parse_pdf_for_wjgl_local(file_path, ocr_output_dir=ocr_dir)
        except Exception as e:
            logger.warning("parse_local_file MinerU 失败，回退 fitz+OCR: %s", e)

    try:
        parsed = parse_standard_file(file_path, ocr_output_dir=ocr_dir)
    except Exception as e:
        logger.warning("parse_standard_file failed, fallback fitz: %s", e)
        if ext == ".pdf":
            return _parse_local_fitz_fast(file_path)
        raise

    full_text = clean_document_text(parsed.get("full_text") or "")
    pages = _normalize_page_structures(normalize_pages(list(parsed.get("pages") or [])))

    if (has_cid_garbled(full_text) or garbled_score(full_text) >= 0.35) and not parsed.get("ocr_used"):
        logger.info("CID garbled detected, retry with OCR forced")
        os.environ["FLGL_FORCE_OCR"] = "1"
        try:
            parsed = parse_standard_file(file_path, ocr_output_dir=ocr_dir)
            full_text = clean_document_text(parsed.get("full_text") or "")
            pages = _normalize_page_structures(normalize_pages(list(parsed.get("pages") or [])))
        except Exception as e2:
            logger.warning("OCR retry failed: %s", e2)
        finally:
            os.environ.pop("FLGL_FORCE_OCR", None)

    page_table_map = parsed.get("page_table_map") or {}
    pages, full_text, sections, full_html, start_page, raw_full_html, structure_method = _rebuild_document_after_body_trim(
        pages,
        full_text,
        page_table_map,
        embed_figures_base64=True,
        file_path=file_path,
        ocr_output_dir=ocr_dir,
    )
    full_markdown = str(parsed.get("full_markdown") or "").strip()
    node_slices_md = _build_node_slices_md(sections, full_markdown=full_markdown)
    node_slices = _build_node_slices(
        sections,
        full_markdown=full_markdown,
        node_slices_md=node_slices_md,
    )
    from .document_structure import build_section_catalogue

    section_catalogue = build_section_catalogue(sections)

    parsed_doc = {
        "file_name": parsed.get("file_name"),
        "file_path": os.path.abspath(file_path),
        "parse_mode": parsed.get("parse_mode"),
        "ocr_used": bool(parsed.get("ocr_used")),
        "full_text": full_text,
        "raw_full_text": full_text,
        "raw_full_html": raw_full_html,
        "full_markdown": full_markdown,
        "full_markdown": full_markdown,
        "structure_method": structure_method,
        "stats": parsed.get("stats") or {},
        "garbled_score": garbled_score(full_text),
        "page_count": len(pages),
        "section_catalogue": section_catalogue,
        "body_start_page": start_page,
    }

    return {
        "sections": sections,
        "full_text": full_text,
        "full_html": full_html,
        "raw_full_html": raw_full_html,
        "pages": pages,
        "file_path": os.path.abspath(file_path),
        "page_table_map": page_table_map,
        "parsed_document": parsed_doc,
        "node_slices": node_slices,
        "section_catalogue": section_catalogue,
        "stats": parsed_doc.get("stats") or {},
    }


def _parse_local_fitz_fast(file_path: str) -> Dict[str, Any]:
    import fitz
    from blueprints.FLGL.GetTestMethod.parser import split_standard_sections

    doc = fitz.open(file_path)
    parts: List[str] = []
    for page in doc:
        t = page.get_text("text") or ""
        if t.strip():
            parts.append(t)
    doc.close()
    full_text = clean_document_text("\n\n".join(parts))
    sections_raw = split_standard_sections(full_text)
    pages_raw = [{"page_number": i + 1, "text": p, "tables_html": [], "figures": []} for i, p in enumerate(parts)]
    pages, full_text, sections, full_html, _, raw_full_html, structure_method = _rebuild_document_after_body_trim(
        pages_raw, full_text, {}, embed_figures_base64=True, file_path=file_path,
    )
    return {
        "sections": sections,
        "full_text": full_text,
        "full_html": full_html,
        "raw_full_html": raw_full_html,
        "pages": pages,
        "parsed_document": {"parse_mode": "fitz_fast", "ocr_used": False, "full_text": full_text},
        "node_slices": _build_node_slices(sections, full_markdown=""),
        "section_catalogue": [],
        "stats": {"section_count": len(sections), "text_length": len(full_text)},
    }


_HTML_DATA_URI_RE = re.compile(
    r'(src\s*=\s*["\'])(data:image/([^;"\']+);base64,([^"\']+))(["\'])',
    re.I,
)
_MD_DATA_URI_RE = re.compile(
    r'(!\[[^\]]*\]\()(data:image/([^;]+);base64,([^)]+))(\))',
    re.I,
)


def _upload_parse_figure_bytes(
    category: str,
    doc_id: str,
    raw_bytes: bytes,
    mime: str,
    counter: List[int],
) -> Tuple[str, str]:
    counter[0] += 1
    ext = ".png"
    ml = (mime or "").lower()
    if "jpeg" in ml or "jpg" in ml:
        ext = ".jpg"
    elif "gif" in ml:
        ext = ".gif"
    elif "webp" in ml:
        ext = ".webp"
    asset_name = f"figures/fig_{counter[0]:04d}{ext}"
    bucket = Config.MINIO_BUCKET_NAME
    obj_key = _parse_minio_key(category, doc_id, asset_name)
    ct = mime if str(mime or "").startswith("image/") else f"image/{ext.lstrip('.')}"
    Config.MINIO_CLIENT.put_object(
        bucket,
        obj_key,
        BytesIO(raw_bytes),
        length=len(raw_bytes),
        content_type=ct,
    )
    return parse_asset_url(category, doc_id, asset_name), asset_name


def rewrite_data_uri_images_in_text(
    text: str,
    category: str,
    doc_id: str,
    *,
    seen: Optional[Dict[str, str]] = None,
    counter: Optional[List[int]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """将 HTML/Markdown 内 data:image base64 上传 MinIO 并替换为 parse-asset URL。"""
    if not text or not Config.MINIO_CLIENT or not category or not doc_id:
        return text, []
    import base64

    seen_map = seen if seen is not None else {}
    fig_counter = counter if counter is not None else [0]
    manifest: List[Dict[str, Any]] = []

    def _repl_html(m: re.Match) -> str:
        full = m.group(2)
        if full in seen_map:
            return m.group(1) + seen_map[full] + m.group(5)
        mime = m.group(3).strip()
        b64 = m.group(4)
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return m.group(0)
        url, asset = _upload_parse_figure_bytes(category, doc_id, raw, f"image/{mime}", fig_counter)
        seen_map[full] = url
        manifest.append({"parse_asset": asset, "figure_index": fig_counter[0], "content_type": f"image/{mime}"})
        return m.group(1) + url + m.group(5)

    def _repl_md(m: re.Match) -> str:
        full = m.group(2)
        if full in seen_map:
            return m.group(1) + seen_map[full] + m.group(5)
        mime = m.group(3).strip()
        b64 = m.group(4)
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return m.group(0)
        url, asset = _upload_parse_figure_bytes(category, doc_id, raw, f"image/{mime}", fig_counter)
        seen_map[full] = url
        manifest.append({"parse_asset": asset, "figure_index": fig_counter[0], "content_type": f"image/{mime}"})
        return m.group(1) + url + m.group(5)

    out = _HTML_DATA_URI_RE.sub(_repl_html, str(text))
    out = _MD_DATA_URI_RE.sub(_repl_md, out)
    return out, manifest


def rewrite_parse_cache_images_to_minio(
    parse_cache: Dict[str, Any],
    category: str,
    doc_id: str,
) -> Dict[str, Any]:
    if not isinstance(parse_cache, dict) or not Config.MINIO_CLIENT:
        return parse_cache
    pc = dict(parse_cache)
    seen: Dict[str, str] = {}
    counter = [0]

    def _rw(val: Any) -> Any:
        if not val:
            return val
        new_val, _ = rewrite_data_uri_images_in_text(str(val), category, doc_id, seen=seen, counter=counter)
        return new_val

    for key in ("full_html", "raw_full_html", "full_markdown"):
        if pc.get(key):
            pc[key] = _rw(pc[key])

    pc["node_slices"] = {k: _rw(v) for k, v in dict(pc.get("node_slices") or {}).items()}
    pc["node_slices_md"] = {k: _rw(v) for k, v in dict(pc.get("node_slices_md") or {}).items()}

    sections: List[Dict[str, Any]] = []
    for sec in pc.get("sections") or []:
        s = dict(sec) if isinstance(sec, dict) else {}
        if s.get("html"):
            s["html"] = _rw(s["html"])
        sections.append(s)
    pc["sections"] = sections
    pc["_figure_count_minio"] = counter[0]
    return pc


def save_preview_parse_to_minio(
    preview_key: str,
    parse_cache: Dict[str, Any],
    ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """FLGL 预览解析：图片与全文落 MinIO，作为唯一数据源。"""
    if not preview_key or not isinstance(parse_cache, dict):
        return parse_cache
    category = "_preview"
    doc_id = preview_key
    if not Config.MINIO_CLIENT:
        logger.warning("[preview-minio] MinIO 未配置，跳过 key=%s", preview_key[:12])
        return parse_cache

    ctx = ctx or {}
    pc = rewrite_parse_cache_images_to_minio(dict(parse_cache), category, doc_id)

    pages = list(ctx.get("pages") or pc.get("pages") or [])
    if pages:
        pages = _persist_parse_figures_to_minio(category, doc_id, pages)

    pd = dict(pc.get("parsed_document") or {})
    if not pd.get("images_dir"):
        for base in (pd.get("local_dir"), pd.get("ocr_output_dir"), ctx.get("ocr_output_dir")):
            base = str(base or "").strip()
            if not base:
                continue
            images_path = os.path.join(base, "images")
            if os.path.isdir(images_path):
                pd["images_dir"] = images_path
                break

    data: Dict[str, Any] = {
        "sections": pc.get("sections") or [],
        "node_slices": pc.get("node_slices") or {},
        "node_slices_md": pc.get("node_slices_md") or {},
        "full_html": pc.get("full_html") or "",
        "full_markdown": pc.get("full_markdown") or "",
        "parsed_document": {**pd, "parse_mode": pd.get("parse_mode") or "preview"},
        "pages": pages,
        "full_text": str(ctx.get("full_text") or ""),
        "toc": pc.get("toc") or pc.get("toc_display") or [],
    }
    try:
        save_parse_artifacts(category, doc_id, data)
        pc["_storage"] = "minio"
        pc["_minio_category"] = category
        logger.info(
            "[preview-minio] saved key=%s sections=%s figures=%s html_len=%s",
            preview_key[:12],
            len(data["sections"]),
            pc.get("_figure_count_minio", 0),
            len(data["full_html"]),
        )
    except Exception as exc:
        logger.warning("[preview-minio] save failed key=%s: %s", preview_key[:12], exc)
    return pc


def _load_section_md_slices_from_minio(
    category: str,
    doc_id: str,
    sections: List[Dict[str, Any]],
) -> Dict[str, str]:
    if not Config.MINIO_CLIENT:
        return {}
    bucket = Config.MINIO_BUCKET_NAME
    out: Dict[str, str] = {}
    for sec in sections or []:
        sid = str(sec.get("section_id") or "")
        if not sid:
            continue
        try:
            resp = Config.MINIO_CLIENT.get_object(
                bucket,
                section_slice_md_minio_key(category, doc_id, sid),
            )
            md = resp.read().decode("utf-8")
            resp.close()
            resp.release_conn()
            if md.strip():
                out[sid] = md
        except Exception:
            continue
    return out


def _load_parse_artifacts_from_minio(category: str, doc_id: str) -> Optional[Dict[str, Any]]:
    if not Config.MINIO_CLIENT:
        return None
    bucket = Config.MINIO_BUCKET_NAME
    key = _parse_minio_key(category, doc_id, "sections.json")
    try:
        resp = Config.MINIO_CLIENT.get_object(bucket, key)
        raw = json.loads(resp.read().decode("utf-8"))
        resp.close()
        resp.release_conn()
        sections = raw.get("sections") or []
        slices: Dict[str, str] = {}
        blocks: List[Dict[str, Any]] = []
        full_html = load_full_document_html(category, doc_id) or ""
        try:
            r2 = Config.MINIO_CLIENT.get_object(bucket, _parse_minio_key(category, doc_id, "node_slices.json"))
            sraw = json.loads(r2.read().decode("utf-8"))
            r2.close()
            r2.release_conn()
            slices = sraw.get("slices") or {}
        except Exception:
            slices = {s["section_id"]: s.get("html", "") for s in sections if s.get("section_id")}
        if not slices and sections:
            slices = _load_section_slices_from_minio(category, doc_id, sections)
        try:
            r3 = Config.MINIO_CLIENT.get_object(bucket, _parse_minio_key(category, doc_id, "document_blocks.json"))
            braw = json.loads(r3.read().decode("utf-8"))
            r3.close()
            r3.release_conn()
            blocks = braw.get("blocks") or []
        except Exception:
            blocks = []
        stored_pages: List[Dict[str, Any]] = []
        full_text = ""
        try:
            r4 = Config.MINIO_CLIENT.get_object(bucket, _parse_minio_key(category, doc_id, "pages.json"))
            praw = json.loads(r4.read().decode("utf-8"))
            r4.close()
            r4.release_conn()
            stored_pages = _pages_from_storage(praw.get("pages") or [], category, doc_id)
            full_text = "\n\n".join(
                clean_document_text(p.get("text") or "") for p in stored_pages if (p.get("text") or "").strip()
            )
        except Exception:
            stored_pages = []
        if not full_html:
            if stored_pages:
                full_html = _build_full_document_html_linear(
                    full_text,
                    stored_pages,
                    category,
                    doc_id,
                    embed_figures_base64=False,
                    sections=sections,
                )
            else:
                parts: List[str] = []
                for s in sections:
                    title = clean_document_text(s.get("title") or "")
                    text = clean_document_text(s.get("text") or "")
                    if is_toc_noise_section(title, text):
                        continue
                    section_no = _parse_section_no(title)
                    if section_no:
                        aid = _section_anchor_id(section_no)
                        import html as html_module
                        parts.append(
                            f'<p id="{aid}" class="wjgl-section-heading" '
                            f'data-section-no="{html_module.escape(section_no)}" '
                            f'style="font-weight:600;margin:10px 0 6px">'
                            f"<strong>{html_module.escape(title)}</strong></p>"
                        )
                    else:
                        parts.append(f'<p style="font-weight:600;margin:10px 0 6px"><strong>{title}</strong></p>')
                    body = _section_text_to_html(text, section_no, title)
                    if body:
                        parts.append(body)
                    for th in s.get("tables_html") or []:
                        wrapped = _wrap_table_html(str(th))
                        if wrapped:
                            parts.append(wrapped)
                full_html = trim_html_from_body_start("".join(parts))
                full_html = _ensure_section_anchors_in_html(full_html, sections)
        full_markdown = ""
        try:
            rmd = Config.MINIO_CLIENT.get_object(bucket, _parse_minio_key(category, doc_id, "document.md"))
            full_markdown = rmd.read().decode("utf-8")
            rmd.close()
            rmd.release_conn()
        except Exception:
            full_markdown = ""
        node_slices_md = _load_section_md_slices_from_minio(category, doc_id, sections)
        if not node_slices_md and full_markdown and sections:
            node_slices_md = _build_node_slices_md(sections, full_markdown=full_markdown)
        toc = load_saved_toc(category, doc_id)
        section_index = load_section_index(category, doc_id)
        return {
            "sections": sections,
            "node_slices": slices,
            "node_slices_md": node_slices_md,
            "document_blocks": blocks,
            "full_html": full_html,
            "full_markdown": full_markdown,
            "toc": toc,
            "section_index": section_index,
            "pages": stored_pages,
            "full_text": full_text,
            "_from_minio": True,
        }
    except Exception:
        return None


def save_parse_artifacts(category: str, doc_id: str, data: Dict[str, Any]) -> None:
    if not Config.MINIO_CLIENT or not category or not doc_id:
        return
    data = finalize_parse_data(category, doc_id, data)
    bucket = Config.MINIO_BUCKET_NAME
    sections = _enrich_sections_with_minio_keys(category, doc_id, list(data.get("sections") or []))
    data["sections"] = sections

    payload_sections = json.dumps({"sections": sections}, ensure_ascii=False).encode("utf-8")
    Config.MINIO_CLIENT.put_object(
        bucket, _parse_minio_key(category, doc_id, "sections.json"),
        BytesIO(payload_sections), length=len(payload_sections), content_type="application/json",
    )
    pd = data.get("parsed_document") or {}
    payload_doc = json.dumps(pd, ensure_ascii=False).encode("utf-8")
    Config.MINIO_CLIENT.put_object(
        bucket, _parse_minio_key(category, doc_id, "parsed_document.json"),
        BytesIO(payload_doc), length=len(payload_doc), content_type="application/json",
    )
    slices = data.get("node_slices") or {}
    payload_slices = json.dumps({"slices": slices}, ensure_ascii=False).encode("utf-8")
    Config.MINIO_CLIENT.put_object(
        bucket, _parse_minio_key(category, doc_id, "node_slices.json"),
        BytesIO(payload_slices), length=len(payload_slices), content_type="application/json",
    )
    blocks = data.get("document_blocks") or []
    payload_blocks = json.dumps({"blocks": blocks, "version": 1}, ensure_ascii=False).encode("utf-8")
    Config.MINIO_CLIENT.put_object(
        bucket, _parse_minio_key(category, doc_id, "document_blocks.json"),
        BytesIO(payload_blocks), length=len(payload_blocks), content_type="application/json",
    )
    slim_pages = _slim_pages_for_storage(data.get("pages") or [])
    payload_pages = json.dumps({"pages": slim_pages}, ensure_ascii=False).encode("utf-8")
    Config.MINIO_CLIENT.put_object(
        bucket, _parse_minio_key(category, doc_id, "pages.json"),
        BytesIO(payload_pages), length=len(payload_pages), content_type="application/json",
    )
    table_registry = data.get("table_registry") or []
    if table_registry:
        slim_tables = [
            {
                "table_id": t.get("table_id"),
                "table_no": t.get("table_no"),
                "page": t.get("page"),
                "parse_asset": t.get("parse_asset"),
                "caption_text": t.get("caption_text"),
            }
            for t in table_registry
        ]
        payload_tables = json.dumps({"tables": slim_tables, "count": len(slim_tables)}, ensure_ascii=False).encode("utf-8")
        Config.MINIO_CLIENT.put_object(
            bucket, _parse_minio_key(category, doc_id, "tables.json"),
            BytesIO(payload_tables), length=len(payload_tables), content_type="application/json",
        )
    full_html = (data.get("full_html") or "").encode("utf-8")
    if full_html:
        Config.MINIO_CLIENT.put_object(
            bucket, _parse_minio_key(category, doc_id, "full_document.html"),
            BytesIO(full_html), length=len(full_html), content_type="text/html; charset=utf-8",
        )
    full_md = (data.get("full_markdown") or "").strip()
    if full_md:
        md_bytes = full_md.encode("utf-8")
        Config.MINIO_CLIENT.put_object(
            bucket, _parse_minio_key(category, doc_id, "document.md"),
            BytesIO(md_bytes), length=len(md_bytes), content_type="text/markdown; charset=utf-8",
        )
    toc = list(data.get("toc") or [])
    if not toc and full_md:
        try:
            from pdf_extract.mineru_wjgl_service import build_toc_tree_from_markdown

            toc = build_toc_tree_from_markdown(full_md)
        except Exception:
            toc = []
    elif not toc and data.get("sections"):
        toc = build_toc_from_sections(data.get("sections") or [])
    if toc:
        payload_toc = json.dumps({"toc": toc}, ensure_ascii=False).encode("utf-8")
        Config.MINIO_CLIENT.put_object(
            bucket, _parse_minio_key(category, doc_id, "toc.json"),
            BytesIO(payload_toc), length=len(payload_toc), content_type="application/json",
        )
    _persist_section_slices_to_minio(category, doc_id, slices, sections, data.get("node_slices_md") or {})
    _persist_section_slices_local(category, doc_id, slices, data.get("node_slices_md") or {})

    section_index = build_section_index(category, doc_id, sections, slices, toc)
    save_section_index_to_minio(category, doc_id, section_index)
    try:
        from pdf_extract.mineru_wjgl_service import local_doc_dir

        idx_path = local_doc_dir(category, doc_id) / "section_index.json"
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        idx_path.write_text(json.dumps(section_index, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("persist local section_index failed: %s", e)

    logger.info(
        "parse artifacts saved doc_id=%s sections=%s toc=%s index=%s slices=%s blocks=%s tables=%s html_bytes=%s",
        doc_id, len(sections), len(toc), len(section_index.get("entries") or []), len(slices), len(blocks),
        len(data.get("table_registry") or []), len(full_html),
    )


def load_full_document_html(category: str, doc_id: str) -> Optional[str]:
    if not Config.MINIO_CLIENT or not category or not doc_id:
        return None
    key = _parse_minio_key(category, doc_id, "full_document.html")
    try:
        resp = Config.MINIO_CLIENT.get_object(Config.MINIO_BUCKET_NAME, key)
        html = resp.read().decode("utf-8")
        resp.close()
        resp.release_conn()
        return html if html.strip() else None
    except Exception:
        return None


def load_parse_artifacts(category: str, doc_id: str) -> Optional[Dict[str, Any]]:
    if category == "_preview":
        loaded = _load_parse_artifacts_from_minio(category, doc_id)
        if loaded:
            return loaded
    try:
        from pdf_extract.mineru_wjgl_service import load_local_parse_bundle

        local = load_local_parse_bundle(category, doc_id)
        if local and ((local.get("full_html") or "").strip() or (local.get("full_markdown") or "").strip()):
            return local
    except Exception as e:
        logger.debug("load local parse bundle failed: %s", e)

    return _load_parse_artifacts_from_minio(category, doc_id)


def merge_section_html(section_ids: List[str], sections: List[Dict[str, Any]], node_slices: Dict[str, str]) -> str:
    parts: List[str] = []
    sec_map = {s.get("section_id"): s for s in sections}
    for sid in section_ids or []:
        sec = sec_map.get(sid) or {}
        title = (sec.get("title") or "").strip()
        text = sec.get("text") or ""
        if is_toc_noise_section(title, text):
            continue
        if sid in node_slices and node_slices[sid]:
            parts.append(clean_document_html(node_slices[sid]))
        elif sid in sec_map:
            s = sec_map[sid]
            parts.append(s.get("html") or _text_to_html(clean_document_text(s.get("text") or "")))
    return clean_document_html("".join(parts))
