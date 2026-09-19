"""
WJGL 文件管理：MinerU 解析 PDF → 本地 Markdown + 章节 HTML。

本地目录（项目内）:
  data/wjgl_documents/{category}/{doc_id}/
    document.md
    document.html
    sections.json
    node_slices.json
    meta.json
    images/
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SECTION_HEADING_MD = re.compile(
    r"^(#{1,6})\s+(\d+(?:[\.．]\d+)*)(?:[\s\.、．]+)?(.+)$",
    re.M,
)
_SECTION_HEADING_PLAIN = re.compile(
    r"^(\d+(?:[\.．]\d+)*)[\s\.、．]+(.+)$",
    re.M,
)


def project_root() -> Path:
    env = (os.environ.get("WJGL_PROJECT_ROOT") or "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def _wjgl_documents_roots() -> List[Path]:
    """可能的 wjgl_documents 根目录（兼容 backend/data 与项目根 data）。"""
    roots: List[Path] = []
    seen: set = set()

    def _add(p: Path) -> None:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            return
        seen.add(key)
        roots.append(p)

    env = (os.environ.get("WJGL_LOCAL_PARSE_ROOT") or "").strip()
    if env:
        p = Path(env)
        if not p.is_absolute():
            p = project_root() / p
        _add(p)
    else:
        _add(project_root() / "data" / "wjgl_documents")
    backend_dir = Path(__file__).resolve().parents[1]
    _add(backend_dir / "data" / "wjgl_documents")
    _add(project_root() / "backend" / "data" / "wjgl_documents")
    return roots


def local_doc_dir(category: str, doc_id: str) -> Path:
    base = (os.environ.get("WJGL_LOCAL_PARSE_ROOT") or "").strip()
    if not base:
        base_path = project_root() / "data" / "wjgl_documents"
    else:
        base_path = Path(base)
        if not base_path.is_absolute():
            base_path = project_root() / base_path
    return base_path / category / doc_id


def iter_local_doc_dir_candidates(category: str, doc_id: str) -> List[Path]:
    """读取本地 parse 目录时依次尝试多个根路径。"""
    out: List[Path] = []
    seen: set = set()
    for root in _wjgl_documents_roots():
        d = (root / category / doc_id).resolve()
        key = str(d)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    canonical = local_doc_dir(category, doc_id).resolve()
    if str(canonical) not in seen:
        out.insert(0, canonical)
    return out


def resolve_local_doc_dir(category: str, doc_id: str) -> Path:
    """定位已有 parse 产物的本地目录（images/document.html）。"""
    for d in iter_local_doc_dir_candidates(category, doc_id):
        if d.is_dir() and (
            (d / "document.html").is_file()
            or (d / "document.md").is_file()
            or (d / "images").is_dir()
        ):
            return d
    return local_doc_dir(category, doc_id)


def use_mineru_for_wjgl_pdf() -> bool:
    mode = (os.environ.get("WJGL_PDF_PARSE") or "mineru").strip().lower()
    return mode in ("mineru", "1", "true", "yes", "on")


def wjgl_upload_preview_mode() -> str:
    """上传弹窗正文预览：fitz=秒级草稿+后台MinerU；mineru=仅MinerU（需 MINERU_API_URL 加速）。"""
    mode = (os.environ.get("WJGL_UPLOAD_PREVIEW") or "fitz").strip().lower()
    if mode in ("mineru", "mineru_only", "mineru-only", "1", "true", "yes", "on"):
        return "mineru"
    return "fitz"


def _normalize_section_no(sec: str) -> str:
    return (sec or "").replace("．", ".").replace(" ", "").strip(".")


def _build_toc_tree(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    toc: List[Dict[str, Any]] = []
    stack: List[tuple] = []
    for item in items:
        level = int(item.get("level") or 1)
        node = {
            "title": item.get("title") or "",
            "sectionNo": item.get("sectionNo") or "",
            "level": level,
            "children": [],
        }
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            toc.append(node)
        stack.append((level, node))
    return toc


def trim_mineru_markdown_body(md: str) -> str:
    """跳过国标封面/目次/前言乱码，从正文「## 1 范围」起保留。"""
    lines = (md or "").splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not re.match(r"^#{1,6}\s+1[\s．.、]", stripped):
            continue
        hm = _SECTION_HEADING_MD.match(stripped)
        if not hm or _normalize_section_no(hm.group(2)).split(".")[0] != "1":
            continue
        body = "\n".join(lines[i:]).strip()
        if body:
            return body
    return (md or "").strip()


def build_toc_tree_from_markdown(md: str) -> List[Dict[str, Any]]:
    """从 MinerU Markdown 标题构建目录树（支持 ## 标题与 plain 1 范围 行）。"""
    try:
        from blueprints.ZSGL.WJGL1.body_content_trim import (
            filter_toc_from_chapter_one,
            is_toc_tree_heading,
            normalize_gb_standard_toc_title,
            shorten_toc_display_title,
            strip_section_no_from_title,
        )
    except Exception:
        is_toc_tree_heading = None  # type: ignore
        shorten_toc_display_title = lambda t, max_len=40: (t or "")[:max_len]  # type: ignore
        strip_section_no_from_title = lambda _sn, t: t  # type: ignore
        filter_toc_from_chapter_one = lambda x: _build_toc_tree(x) if x else []  # type: ignore

    items: List[Dict[str, Any]] = []
    lines = (md or "").splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        sec_no = ""
        raw_title = ""
        hm = _SECTION_HEADING_MD.match(stripped)
        if hm:
            sec_no = _normalize_section_no(hm.group(2))
            raw_title = (hm.group(3) or "").strip()
        else:
            pm = _SECTION_HEADING_PLAIN.match(stripped)
            if pm and len(stripped) < 120:
                sec_no = _normalize_section_no(pm.group(1))
                raw_title = (pm.group(2) or "").strip()
        if not sec_no or not re.match(r"^\d", sec_no):
            continue
        if not raw_title:
            for j in range(i + 1, min(i + 4, len(lines))):
                nxt = lines[j].strip()
                if not nxt or nxt.startswith("#"):
                    break
                if _SECTION_HEADING_MD.match(nxt) or _SECTION_HEADING_PLAIN.match(nxt):
                    break
                if re.search(r"[\u4e00-\u9fff]", nxt):
                    raw_title = nxt[:56]
                    break
            if not raw_title:
                raw_title = sec_no
        display = normalize_gb_standard_toc_title(
            sec_no,
            shorten_toc_display_title(strip_section_no_from_title(sec_no, raw_title)),
        )
        if is_toc_tree_heading and not is_toc_tree_heading(display or raw_title, sec_no):
            continue
        items.append({
            "sectionNo": sec_no,
            "title": display or raw_title or sec_no,
            "level": sec_no.count(".") + 1,
        })
    if not items:
        return []
    return filter_toc_from_chapter_one(_build_toc_tree(items))


def load_local_toc(category: str, doc_id: str) -> List[Dict[str, Any]]:
    path = local_doc_dir(category, doc_id) / "toc.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("toc") or [])
    except Exception:
        return []


def _safe_resolve_asset(local_dir: Path, rel_path: str) -> Optional[Path]:
    rel = (rel_path or "").replace("\\", "/").lstrip("./")
    if not rel or ".." in rel.split("/"):
        return None
    full = (local_dir / rel).resolve()
    try:
        full.relative_to(local_dir.resolve())
    except ValueError:
        return None
    return full if full.is_file() else None


def resolve_local_parse_asset(category: str, doc_id: str, rel_path: str) -> Optional[Path]:
    """跨多个 wjgl_documents 根目录解析 MinerU 图片等资源。"""
    rel = (rel_path or "").replace("\\", "/").lstrip("./")
    if not rel:
        return None
    for base in iter_local_doc_dir_candidates(category, doc_id):
        fpath = _safe_resolve_asset(base, rel)
        if fpath:
            return fpath
    return None


def _pipe_table_to_html(rows: List[str]) -> str:
    """Markdown pipe 表格 → HTML table。"""
    import html as html_module

    if len(rows) < 2:
        return ""
    def split_row(line: str) -> List[str]:
        inner = line.strip().strip("|")
        return [c.strip() for c in inner.split("|")]

    header = split_row(rows[0])
    body_rows = rows[2:] if len(rows) >= 2 and re.match(r"^[\|\s:\-]+$", rows[1]) else rows[1:]
    parts = ['<table class="doc-table" border="1" cellspacing="0" cellpadding="4">']
    if header:
        parts.append("<thead><tr>")
        for cell in header:
            parts.append(f"<th>{html_module.escape(cell)}</th>")
        parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in body_rows:
        cells = split_row(row)
        if not any(cells):
            continue
        parts.append("<tr>")
        for cell in cells:
            parts.append(f"<td>{html_module.escape(cell)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def markdown_to_wjgl_html(
    md: str,
    *,
    doc_id: str,
    category: str,
    local_dir: Path,
) -> str:
    """Markdown → 带章节锚点与本地图片 URL 的 HTML。"""
    import html as html_module

    asset_prefix = f"/api/wjgl1/files/{doc_id}/local-asset?category={category}&path="
    out: List[str] = []
    in_table = False
    table_buf: List[str] = []
    in_pipe_table = False
    pipe_buf: List[str] = []

    def flush_pipe_table() -> None:
        nonlocal in_pipe_table, pipe_buf
        if pipe_buf:
            html = _pipe_table_to_html(pipe_buf)
            if html:
                out.append(f'<div class="doc-table">{html}</div>')
            else:
                import html as html_module
                out.append(f"<pre>{html_module.escape(chr(10).join(pipe_buf))}</pre>")
        pipe_buf = []
        in_pipe_table = False

    def flush_table() -> None:
        nonlocal in_table, table_buf
        if table_buf:
            block = "\n".join(table_buf)
            if "<table" in block.lower():
                out.append(f'<div class="doc-table">{block}</div>')
            else:
                out.append(f"<pre>{html_module.escape(block)}</pre>")
        table_buf = []
        in_table = False

    try:
        from blueprints.FLGL.GetTestMethod.parser import split_merged_decimal_heading_line
    except Exception:
        split_merged_decimal_heading_line = None  # type: ignore

    for raw_line in (md or "").splitlines():
        line = raw_line.rstrip()
        merged_tail = None
        if split_merged_decimal_heading_line:
            line, merged_tail = split_merged_decimal_heading_line(line)
        stripped = line.strip()

        if stripped.startswith("|") and stripped.endswith("|"):
            if in_pipe_table:
                pipe_buf.append(stripped)
            else:
                flush_table()
                in_pipe_table = True
                pipe_buf = [stripped]
            continue
        if in_pipe_table:
            flush_pipe_table()

        if stripped.startswith("<table") or (in_table and not stripped.startswith("</table>")):
            in_table = True
            table_buf.append(line)
            if "</table>" in stripped.lower():
                flush_table()
            continue
        if in_table:
            flush_table()

        hm = _SECTION_HEADING_MD.match(stripped)
        if hm:
            section_no = _norm_section_no(hm.group(2))
            title_tail = (hm.group(3) or "").strip()
            if not section_no or not re.match(r"^\d", section_no):
                pass
            else:
                try:
                    from blueprints.ZSGL.WJGL1.body_content_trim import is_toc_heading_noise

                    if is_toc_heading_noise(title_tail or section_no, section_no):
                        continue
                except Exception:
                    pass
                title = f"{section_no} {title_tail}".strip()
                aid = "wjgl-sec-" + section_no.replace(".", "-")
                out.append(
                    f'<p id="{aid}" class="wjgl-section-heading" data-section-no="{html_module.escape(section_no)}" '
                    f'style="font-weight:600;margin:10px 0 6px">'
                    f"<strong>{html_module.escape(title)}</strong></p>"
                )
                if merged_tail:
                    out.append(f"<p>{html_module.escape(merged_tail)}</p>")
                    merged_tail = None
                continue

        pm = _SECTION_HEADING_PLAIN.match(stripped)
        if pm and len(stripped) < 80:
            section_no = _norm_section_no(pm.group(1))
            if section_no and re.match(r"^\d", section_no):
                try:
                    from blueprints.ZSGL.WJGL1.body_content_trim import is_toc_heading_noise

                    if is_toc_heading_noise(stripped, section_no):
                        continue
                except Exception:
                    pass
                title = stripped
                aid = "wjgl-sec-" + section_no.replace(".", "-")
                out.append(
                    f'<p id="{aid}" class="wjgl-section-heading" data-section-no="{html_module.escape(section_no)}" '
                    f'style="font-weight:600;margin:10px 0 6px">'
                    f"<strong>{html_module.escape(title)}</strong></p>"
                )
                if merged_tail:
                    out.append(f"<p>{html_module.escape(merged_tail)}</p>")
                    merged_tail = None
                continue

        if merged_tail:
            out.append(f"<p>{html_module.escape(merged_tail)}</p>")
            merged_tail = None
            continue

        if stripped.startswith("/") and re.search(r"[\u7280-\u72ff]", stripped):
            continue

        img_m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
        if img_m:
            alt = (img_m.group(1) or "").strip()
            src = img_m.group(2).strip()
            if src.startswith("data:image"):
                alt_esc = html_module.escape(alt or "图")
                out.append(
                    f'<figure class="word-native-figure">'
                    f'<img src="{src}" alt="{alt_esc}" style="max-width:100%;height:auto;"/>'
                    f"{f'<figcaption>{alt_esc}</figcaption>' if alt else ''}"
                    f"</figure>"
                )
                continue
            if not src.startswith(("http://", "https://", "/api/")):
                rel = src.replace("\\", "/").lstrip("./")
                if (local_dir / rel).is_file() or rel.startswith("images/"):
                    src = asset_prefix + rel
            alt_esc = html_module.escape(alt or "")
            out.append(
                f'<figure class="word-native-figure">'
                f'<img src="{html_module.escape(src)}" alt="{alt_esc}" style="max-width:100%;height:auto;"/>'
                f"</figure>"
            )
            continue

        if not stripped:
            continue
        out.append(f"<p>{html_module.escape(stripped)}</p>")

    flush_pipe_table()
    flush_table()
    html = "\n".join(out)
    try:
        from blueprints.ZSGL.WJGL1.text_normalize import clean_document_html

        return clean_document_html(html)
    except Exception:
        return html


def _norm_section_no(s: str) -> str:
    return (s or "").replace("．", ".").replace(" ", "").strip().lstrip(".")


def _section_no_depth(sno: str) -> int:
    """节号层级深度：8 -> 1, 8.2 -> 2, 8.2.3 -> 3。"""
    sno = _norm_section_no(sno)
    if not sno or not re.match(r"^\d", sno):
        return 99
    return len([p for p in sno.split(".") if p])


def _flat_sections_from_markdown(md: str) -> List[Dict[str, Any]]:
    """从 MinerU Markdown 标题构建扁平章节列表（含 section_no），避免 plain 文本切分错乱。"""
    try:
        from blueprints.ZSGL.WJGL1.body_content_trim import (
            is_toc_tree_heading,
            normalize_gb_standard_toc_title,
            shorten_toc_display_title,
            strip_section_no_from_title,
        )
    except Exception:
        is_toc_tree_heading = None  # type: ignore
        shorten_toc_display_title = lambda t, max_len=40: (t or "")[:max_len]  # type: ignore
        strip_section_no_from_title = lambda _sn, t: t  # type: ignore
        normalize_gb_standard_toc_title = lambda _sn, t: t  # type: ignore

    body_md = trim_mineru_markdown_body(md)
    sections: List[Dict[str, Any]] = []
    idx = 1
    lines = (body_md or "").splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        sec_no = ""
        raw_title = ""
        hm = _SECTION_HEADING_MD.match(stripped)
        if hm:
            sec_no = _normalize_section_no(hm.group(2))
            raw_title = (hm.group(3) or "").strip()
        else:
            pm = _SECTION_HEADING_PLAIN.match(stripped)
            if pm and len(stripped) < 120:
                sec_no = _normalize_section_no(pm.group(1))
                raw_title = (pm.group(2) or "").strip()
        if not sec_no or not re.match(r"^\d", sec_no):
            continue
        if not raw_title:
            for j in range(i + 1, min(i + 4, len(lines))):
                nxt = lines[j].strip()
                if not nxt or nxt.startswith("#"):
                    break
                if _SECTION_HEADING_MD.match(nxt) or _SECTION_HEADING_PLAIN.match(nxt):
                    break
                if re.search(r"[\u4e00-\u9fff]", nxt):
                    raw_title = nxt[:80]
                    break
        display = normalize_gb_standard_toc_title(
            sec_no,
            shorten_toc_display_title(strip_section_no_from_title(sec_no, raw_title or sec_no)),
        )
        if is_toc_tree_heading and not is_toc_tree_heading(display or raw_title, sec_no):
            continue
        title = f"{sec_no} {display}".strip() if display else sec_no
        sections.append({
            "section_id": f"S{idx:03d}",
            "section_no": sec_no,
            "title": title,
            "heading": title,
            "level": sec_no.count(".") + 1,
        })
        idx += 1
    return sections


def slice_markdown_by_sections(md: str, sections: List[Dict[str, Any]]) -> Dict[str, str]:
    """按 Markdown 标题节号切分正文，返回 section_id -> markdown chunk。"""
    return _slice_markdown_by_sections(md, sections)


def _slice_markdown_by_sections(md: str, sections: List[Dict[str, Any]]) -> Dict[str, str]:
    """按 Markdown 标题节号切分正文，返回 section_id -> markdown chunk。

    父节（如 8.2）切到下一同级标题（8.3），包含全部 8.2.x 子节；
    子节（如 8.2.3）切到下一同级兄弟（8.2.4）。
    """
    md = md or ""
    heads: List[Tuple[int, str, int]] = []
    for m in _SECTION_HEADING_MD.finditer(md):
        sno = _norm_section_no(m.group(2))
        if sno:
            heads.append((m.start(), sno, _section_no_depth(sno)))
    if not heads:
        for m in _SECTION_HEADING_PLAIN.finditer(md):
            sno = _norm_section_no(m.group(1))
            line = m.group(0)
            if sno and len(line) < 80:
                heads.append((m.start(), sno, _section_no_depth(sno)))

    if not heads:
        return {}

    positions_by_no: Dict[str, List[int]] = {}
    for pos, sno, _depth in heads:
        positions_by_no.setdefault(sno, []).append(pos)

    out: Dict[str, str] = {}
    for sec in sections or []:
        sid = str(sec.get("section_id") or "")
        if not sid:
            continue
        sno = _norm_section_no(str(sec.get("section_no") or ""))
        if not sno:
            tm = _SECTION_HEADING_PLAIN.match(str(sec.get("title") or "").strip())
            if tm:
                sno = _norm_section_no(tm.group(1))
        if not sno:
            continue

        sec_depth = _section_no_depth(sno)
        try:
            level = int(sec.get("level") or 0)
            if level > 0:
                sec_depth = level
        except (TypeError, ValueError):
            pass

        start_candidates = positions_by_no.get(sno) or []
        if not start_candidates:
            continue
        start_pos = start_candidates[0]
        if len(start_candidates) > 1:
            try:
                target_off = int(sec.get("start_offset") or 0)
            except (TypeError, ValueError):
                target_off = 0
            if target_off > 0 and len(md) > 0:
                ratio = target_off / max(len(md), 1)
                start_pos = min(
                    start_candidates,
                    key=lambda p: abs(p / max(len(md), 1) - ratio),
                )

        end_pos = len(md)
        for pos, hsno, hdepth in heads:
            if pos <= start_pos:
                continue
            if hdepth <= sec_depth:
                end_pos = pos
                break

        chunk = md[start_pos:end_pos].strip()
        if chunk:
            out[sid] = chunk
    return out


def _plain_from_markdown(md: str) -> str:
    try:
        from blueprints.FLGL.GetTestMethod.parser import preprocess_merged_section_lines

        md = preprocess_merged_section_lines(md or "")
    except Exception:
        md = md or ""
    s = md or ""
    s = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"\$\$[^$]+\$\$", " ", s)
    s = re.sub(r"\$[^$]+\$", " ", s)
    return s.strip()


def _resolve_document_toc(md: str, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Markdown 标题目录优先，空则回退 sections 构建。"""
    toc = build_toc_tree_from_markdown(md)
    if toc:
        return toc
    if not sections:
        return []
    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import build_toc_from_sections

        return build_toc_from_sections(sections) or []
    except Exception as e:
        logger.warning("[MinerU-WJGL] build toc from sections failed: %s", e)
        return []


def _build_sections_and_slices(
    md: str,
    full_html: str,
    *,
    doc_id: str,
    category: str,
    local_dir: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    from blueprints.FLGL.GetTestMethod.parser import preprocess_merged_section_lines
    from blueprints.ZSGL.WJGL1.text_normalize import clean_document_text

    md = preprocess_merged_section_lines(md or "")
    md_body = trim_mineru_markdown_body(md)
    sections = _flat_sections_from_markdown(md)
    if not sections:
        plain = clean_document_text(_plain_from_markdown(md_body))
        from blueprints.FLGL.GetTestMethod.parser import split_standard_sections
        sections = split_standard_sections(plain)
        for sec in sections:
            if not sec.get("section_no"):
                t = str(sec.get("title") or "")
                pm = _SECTION_HEADING_PLAIN.match(t.strip())
                if pm:
                    sec["section_no"] = _normalize_section_no(pm.group(1))

    chunk_map = _slice_markdown_by_sections(md_body, sections)

    node_slices: Dict[str, str] = {}
    for sec in sections:
        sid = str(sec.get("section_id") or "")
        if not sid:
            continue
        chunk = chunk_map.get(sid, "").strip()
        if chunk:
            node_slices[sid] = markdown_to_wjgl_html(
                clean_document_text(chunk),
                doc_id=doc_id,
                category=category,
                local_dir=local_dir,
            )

    return sections, node_slices


def _copy_mineru_assets(mineru_out: Path, local_dir: Path) -> Path:
    images_dir = local_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("images", "figures", "img"):
        for p in mineru_out.rglob(sub):
            if p.is_dir():
                for f in p.iterdir():
                    if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                        shutil.copy2(f, images_dir / f.name)
    for f in mineru_out.rglob("*"):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            if f.parent.name.lower() in ("images", "figures", "img"):
                continue
            dest = images_dir / f.name
            if not dest.exists():
                try:
                    shutil.copy2(f, dest)
                except Exception:
                    pass
    return images_dir


def _mineru_pdf_to_wjgl_bundle(
    pdf_path: str,
    ocr_output_dir: str,
    *,
    local_dir: Path,
    doc_id: str,
    category: str,
    fast: bool = False,
) -> Dict[str, Any]:
    """MinerU 解析 PDF 并组装 WJGL 所需结构（不写 MinIO）。fast=True 时跳过重型 HTML 重建。"""
    from pdf_extract.mineru_adapter import finalize_mineru_parse, is_mineru_available, parse_pdf_with_mineru

    if not is_mineru_available():
        raise RuntimeError("MinerU 未安装或未配置")

    mineru_work = os.path.join(ocr_output_dir, f"mineru_{doc_id}_{uuid.uuid4().hex[:8]}")
    raw = parse_pdf_with_mineru(pdf_path, mineru_work)
    raw = finalize_mineru_parse(raw)

    artifacts = raw.get("mineru_artifacts") or {}
    md_path = artifacts.get("markdown")
    md_text = ""
    if md_path and os.path.isfile(md_path):
        md_text = Path(md_path).read_text(encoding="utf-8", errors="replace")
    if not md_text.strip():
        md_text = _plain_from_markdown(raw.get("full_text") or "")

    raw_md_bytes = len(md_text.encode("utf-8"))
    md_text = trim_mineru_markdown_body(md_text)
    from blueprints.ZSGL.WJGL1.text_normalize import clean_document_text

    try:
        md_text = clean_document_text(md_text)
    except Exception as e:
        logger.warning("[MinerU-WJGL] clean markdown failed: %s", e)

    local_dir.mkdir(parents=True, exist_ok=True)
    _copy_mineru_assets(Path(mineru_work), local_dir)

    cl_path = artifacts.get("content_list")
    if cl_path and os.path.isfile(cl_path):
        try:
            shutil.copy2(cl_path, local_dir / "content_list.json")
        except Exception as e:
            logger.warning("[MinerU-WJGL] copy content_list failed: %s", e)

    pages = list(raw.get("pages") or [])
    plain_text = clean_document_text(_plain_from_markdown(md_text)) if md_text.strip() else ""
    start_page = 1

    # MinerU Markdown 为正文来源；禁止 fitz 文本层重建（Symbol 字体会产生 P(QRSTUV 类乱码）
    full_html = markdown_to_wjgl_html(md_text, doc_id=doc_id, category=category, local_dir=local_dir)
    sections, node_slices = _build_sections_and_slices(
        md_text, full_html, doc_id=doc_id, category=category, local_dir=local_dir,
    )
    toc_tree = _resolve_document_toc(md_text, sections)
    if not plain_text:
        plain_text = _plain_from_markdown(md_text) or raw.get("full_text") or ""

    (local_dir / "document.md").write_text(md_text, encoding="utf-8")
    (local_dir / "toc.json").write_text(
        json.dumps({"toc": toc_tree}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (local_dir / "document.html").write_text(full_html, encoding="utf-8")

    for s in sections:
        if not s.get("html"):
            s["html"] = node_slices.get(str(s.get("section_id")), "")

    (local_dir / "node_slices.json").write_text(
        json.dumps({"slices": node_slices}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    slices_dir = local_dir / "section_slices"
    slices_dir.mkdir(parents=True, exist_ok=True)
    for sid, html in node_slices.items():
        if sid and html:
            (slices_dir / f"{sid}.html").write_text(html, encoding="utf-8")

    stats = dict(raw.get("stats") or {})
    stats["section_count"] = len(sections)
    stats["text_length"] = len(plain_text)

    meta = {
        "parse_mode": "pdf_mineru",
        "ocr_used": True,
        "mineru_elapsed_ms": raw.get("mineru_elapsed_ms"),
        "local_dir": str(local_dir),
        "local_md_path": str(local_dir / "document.md"),
        "local_html_path": str(local_dir / "document.html"),
        "local_toc_path": str(local_dir / "toc.json"),
        "md_bytes": len(md_text.encode("utf-8")),
        "raw_md_bytes": raw_md_bytes,
        "html_bytes": len(full_html.encode("utf-8")),
        "body_start_page": start_page,
        "toc_count": len(toc_tree),
    }
    (local_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (local_dir / "sections.json").write_text(
        json.dumps({"sections": sections}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (local_dir / "node_slices.json").write_text(
        json.dumps({"slices": node_slices}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "[MinerU-WJGL] parsed doc_id=%s md=%s html=%s sections=%s dir=%s",
        doc_id, len(md_text), len(full_html), len(sections), local_dir,
    )

    return {
        "sections": sections,
        "full_text": plain_text,
        "full_html": full_html,
        "full_markdown": md_text,
        "pages": pages,
        "parsed_document": {
            "parse_mode": "pdf_mineru",
            "ocr_used": True,
            "full_text": plain_text,
            "local_md_path": str(local_dir / "document.md"),
            **meta,
        },
        "node_slices": node_slices,
        "toc": toc_tree,
        "document_blocks": [],
        "section_catalogue": raw.get("section_catalogue") or [],
        "stats": stats,
        "table_registry": [],
        "page_table_map": raw.get("page_table_map") or {},
    }


def parse_pdf_for_wjgl_local(
    pdf_path: str,
    ocr_output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    MinerU 解析 PDF（预览/即时解析，不入库 MinIO）。
    图片写入 ocr_output_dir 下的临时目录，供 upload 预览 HTML 引用。
    """
    ocr_output_dir = ocr_output_dir or os.path.join(os.getcwd(), "mineru_tmp")
    os.makedirs(ocr_output_dir, exist_ok=True)
    preview_id = f"preview_{uuid.uuid4().hex[:8]}"
    preview_dir = Path(ocr_output_dir) / preview_id
    return _mineru_pdf_to_wjgl_bundle(
        pdf_path,
        ocr_output_dir,
        local_dir=preview_dir,
        doc_id=preview_id,
        category="_preview",
    )


def parse_pdf_save_local_and_minio(
    pdf_path: str,
    category: str,
    doc_id: str,
    ocr_output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    MinerU 解析 PDF，保存 MD/HTML 到本地项目目录，并返回 save_parse_artifacts 所需结构。
    MinerU 不可用时抛出异常，由调用方回退旧管线。
    """
    ocr_output_dir = ocr_output_dir or os.path.join(os.getcwd(), "mineru_tmp")
    local_dir = local_doc_dir(category, doc_id)
    return _mineru_pdf_to_wjgl_bundle(
        pdf_path,
        ocr_output_dir,
        local_dir=local_dir,
        doc_id=doc_id,
        category=category,
    )


def ensure_parse_bundle_from_markdown(
    category: str,
    doc_id: str,
    *,
    markdown: str = "",
    persist_local: bool = True,
) -> Optional[Dict[str, Any]]:
    """从 document.md 重建 sections / slices / html（修复缺失切片或 fitz 乱码 HTML）。"""
    local_dir = local_doc_dir(category, doc_id)
    md_path = local_dir / "document.md"
    md = (markdown or "").strip()
    if not md and md_path.is_file():
        md = md_path.read_text(encoding="utf-8", errors="replace").strip()
    if not md:
        return None

    md = trim_mineru_markdown_body(md)
    try:
        from blueprints.ZSGL.WJGL1.text_normalize import clean_document_text

        md = clean_document_text(md)
    except Exception:
        pass

    full_html = markdown_to_wjgl_html(md, doc_id=doc_id, category=category, local_dir=local_dir)
    sections, node_slices = _build_sections_and_slices(
        md, full_html, doc_id=doc_id, category=category, local_dir=local_dir,
    )
    toc = _resolve_document_toc(md, sections)

    if persist_local:
        local_dir.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding="utf-8")
        (local_dir / "document.html").write_text(full_html, encoding="utf-8")
        (local_dir / "sections.json").write_text(
            json.dumps({"sections": sections}, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (local_dir / "node_slices.json").write_text(
            json.dumps({"slices": node_slices}, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (local_dir / "toc.json").write_text(
            json.dumps({"toc": toc}, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        slices_dir = local_dir / "section_slices"
        slices_dir.mkdir(parents=True, exist_ok=True)
        for sid, html in node_slices.items():
            if sid and html:
                (slices_dir / f"{sid}.html").write_text(html, encoding="utf-8")

    return {
        "sections": sections,
        "node_slices": node_slices,
        "full_html": full_html,
        "full_markdown": md,
        "toc": toc,
    }


def load_local_parse_bundle(category: str, doc_id: str) -> Optional[Dict[str, Any]]:
    """读取本地 MinerU 解析产物（优先于 MinIO）。"""
    local_dir = local_doc_dir(category, doc_id)
    html_path = local_dir / "document.html"
    md_path = local_dir / "document.md"
    if not html_path.is_file() and not md_path.is_file():
        return None

    html = html_path.read_text(encoding="utf-8", errors="replace") if html_path.is_file() else ""
    markdown = md_path.read_text(encoding="utf-8", errors="replace") if md_path.is_file() else ""

    try:
        from blueprints.ZSGL.WJGL1.text_normalize import is_garbled_content
    except Exception:
        is_garbled_content = None  # type: ignore

    need_rebuild = bool(markdown.strip()) and (
        not html.strip()
        or (is_garbled_content and is_garbled_content(html))
    )
    if need_rebuild:
        rebuilt = ensure_parse_bundle_from_markdown(
            category, doc_id, markdown=markdown, persist_local=True,
        )
        if rebuilt:
            html = rebuilt.get("full_html") or html
            markdown = rebuilt.get("full_markdown") or markdown
            sections = rebuilt.get("sections") or []
            slices = rebuilt.get("node_slices") or {}
            toc = rebuilt.get("toc") or load_local_toc(category, doc_id)
            meta: Dict[str, Any] = {}
            try:
                meta = json.loads((local_dir / "meta.json").read_text(encoding="utf-8"))
            except Exception:
                meta = {"parse_mode": "pdf_mineru"}
            return {
                "sections": sections,
                "node_slices": slices,
                "document_blocks": [],
                "full_html": html,
                "full_markdown": markdown,
                "toc": toc,
                "from_local": True,
                "local_dir": str(local_dir),
                "parsed_document": meta,
            }

    if not html.strip() and markdown.strip():
        html = markdown_to_wjgl_html(markdown, doc_id=doc_id, category=category, local_dir=local_dir)

    sections: List[Dict[str, Any]] = []
    slices: Dict[str, str] = {}
    try:
        sections = json.loads((local_dir / "sections.json").read_text(encoding="utf-8")).get("sections") or []
    except Exception:
        pass
    try:
        slices = json.loads((local_dir / "node_slices.json").read_text(encoding="utf-8")).get("slices") or {}
    except Exception:
        pass
    if not slices:
        slices_dir = local_dir / "section_slices"
        if slices_dir.is_dir():
            for fp in slices_dir.glob("*.html"):
                sid = fp.stem
                if sid:
                    try:
                        slices[sid] = fp.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass

    if markdown.strip() and (not slices or (is_garbled_content and is_garbled_content(" ".join(slices.values())[:8000]))):
        rebuilt = ensure_parse_bundle_from_markdown(
            category, doc_id, markdown=markdown, persist_local=True,
        )
        if rebuilt:
            sections = rebuilt.get("sections") or sections
            slices = rebuilt.get("node_slices") or slices
            html = rebuilt.get("full_html") or html
            markdown = rebuilt.get("full_markdown") or markdown

    toc: List[Dict[str, Any]] = load_local_toc(category, doc_id)

    if markdown.strip() and not toc:
        toc = _resolve_document_toc(markdown, sections)
        if toc:
            try:
                (local_dir / "toc.json").write_text(
                    json.dumps({"toc": toc}, ensure_ascii=False, indent=2), encoding="utf-8",
                )
            except Exception:
                pass

    meta: Dict[str, Any] = {}
    try:
        meta = json.loads((local_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        meta = {"parse_mode": "pdf_mineru"}

    return {
        "sections": sections,
        "node_slices": slices,
        "document_blocks": [],
        "full_html": html,
        "full_markdown": markdown,
        "toc": toc,
        "from_local": True,
        "local_dir": str(local_dir),
        "parsed_document": meta,
    }
