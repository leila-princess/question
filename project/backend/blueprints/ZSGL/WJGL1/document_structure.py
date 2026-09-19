"""
统一文档结构分析：标题识别 → 层级树 → 章节切片。

WJGL 文件管理与 FLGL 方法提取共用，替代按换行 split + GB「1 范围」硬裁切。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .text_normalize import clean_document_text

logger = logging.getLogger(__name__)

_DECIMAL_RE = re.compile(
    r"^\s*(\d+(?:\.\d+){0,5})(?:[)\.、\s]+|(?=[\u4e00-\u9fff]))(.+?)\s*$"
)
_PAREN_RE = re.compile(r"^\s*[（(](\d+)[)）][、\s]?(.+?)\s*$")
_PAREN_LETTER_RE = re.compile(r"^\s*[（(]?([a-zA-Z])[)）.、\s]+(.+?)\s*$")
_CHINESE_RE = re.compile(r"^\s*([一二三四五六七八九十百千]+)[、．.](.+?)\s*$")
_CHAPTER_RE = re.compile(r"^\s*第[一二三四五六七八九十百零0-9]+[章节部分篇]\s*(.*)$")
_APPENDIX_RE = re.compile(r"^\s*附录\s*[A-Za-z0-9一二三四五六七八九十]?\s*(.*)$")
_TRIAL_PROJECT_RE = re.compile(r"^试验项目[一二三四五六七八九十0-9]+[：:]\s*(.+)$")
_TRIAL_KEYWORDS = (
    "试验条件", "试验过程", "试验方法", "试验步骤", "结果评定", "试验目的",
    "试验项目", "试验名称", "试验要求", "试验内容",
)

_SOURCE_PRIORITY = {
    "word_paragraph_map": 6,
    "word_style": 5,
    "word_outline": 4,
    "word_body": 3,
    "word_number": 3,
    "text_decimal": 2,
    "text_chapter": 2,
    "text_paren": 1,
    "text_chinese": 2,
    "text_trial": 2,
    "fallback": 0,
}


@dataclass
class DocumentStructure:
    raw_full_text: str
    heading_tree: List[Dict[str, Any]] = field(default_factory=list)
    sections: List[Dict[str, Any]] = field(default_factory=list)
    flat_headings: List[Dict[str, Any]] = field(default_factory=list)
    toc_display: List[Dict[str, Any]] = field(default_factory=list)
    method: str = "structure"
    structure_fallback: str = ""


def _line_heading_level(line: str) -> Tuple[int, str, str, str]:
    """返回 (level, section_no, title, kind) 或 (0,'','','')。"""
    s = re.sub(r"\s+", " ", (line or "").strip())
    if not s or len(s) > 120:
        return 0, "", "", ""

    m = _DECIMAL_RE.match(s)
    if m:
        sec_no = m.group(1).strip().lstrip(".")
        title = (m.group(2) or "").strip()
        if title and not re.match(r"^[图表附注]", title):
            return len(sec_no.split(".")), sec_no, title, "decimal"

    m = _CHAPTER_RE.match(s)
    if m:
        tail = (m.group(1) or "").strip()
        return 1, "", tail or s, "chapter"

    m = _APPENDIX_RE.match(s)
    if m:
        return 1, "", s, "appendix"

    m = _CHINESE_RE.match(s)
    if m:
        title = (m.group(2) or "").strip()
        if title:
            return 1, m.group(1), title, "chinese"

    m = _PAREN_RE.match(s)
    if m:
        title = (m.group(2) or "").strip()
        if title and len(title) <= 80:
            return 0, m.group(1), title, "paren"

    m = _PAREN_LETTER_RE.match(s)
    if m:
        title = (m.group(2) or "").strip()
        if title and len(title) <= 120:
            return 0, m.group(1).lower(), title, "paren_letter"

    m = _TRIAL_PROJECT_RE.match(s)
    if m:
        return 2, "", m.group(1).strip(), "trial"

    if any(s.startswith(kw) for kw in _TRIAL_KEYWORDS) and len(s) <= 40:
        return 2, "", s, "trial"

  # 短行试验标题（无编号）
    if "试验" in s and len(s) <= 36 and not s.endswith("。"):
        if re.search(r"[\u4e00-\u9fff]", s):
            return 1, "", s, "trial"

    return 0, "", "", ""


def collect_text_heading_candidates(full_text: str) -> List[Dict[str, Any]]:
    """从纯文本按文档偏移收集标题候选。"""
    text = full_text or ""
    candidates: List[Dict[str, Any]] = []
    offset = 0
    for line in text.splitlines():
        stripped = line.strip()
        line_start = text.find(line, offset)
        if line_start < 0:
            line_start = offset
        offset = line_start + len(line) + 1

        if not stripped:
            continue

        level, sec_no, title, kind = _line_heading_level(stripped)
        if not level and kind != "paren":
            continue
        if kind == "paren":
            level = 3

        display = stripped if not title else (
            f"{sec_no} {title}".strip() if sec_no else title
        )
        candidates.append({
            "offset": line_start,
            "line_no": stripped.count("\n") if False else len(candidates),
            "level": max(1, level),
            "section_no": sec_no,
            "title": display,
            "heading": display,
            "source": f"text_{kind}" if kind else "text",
            "confidence": 0.7 if kind in ("decimal", "chapter") else 0.55,
        })
    return candidates


def collect_word_heading_candidates(
    file_path: str,
    full_text: str,
    *,
    word_paragraph_map: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Word 合并标题策略，映射到 full_text 偏移。"""
    if word_paragraph_map:
        try:
            from .word_native_parse import build_word_heading_candidates_from_map

            mapped = build_word_heading_candidates_from_map(word_paragraph_map)
            if mapped:
                return mapped
        except Exception as exc:
            logger.debug("[document_structure] paragraph_map 标题失败: %s", exc)

    if not file_path or not os.path.isfile(file_path):
        return []
    try:
        from .word_native_parse import extract_word_headings_merged, iter_word_body_enriched, _ensure_docx_path
        import docx as _docx

        docx_path, _ = _ensure_docx_path(file_path)
        doc = _docx.Document(docx_path)
        paragraph_map = iter_word_body_enriched(doc)
        from .word_native_parse import build_word_heading_candidates_from_map

        mapped = build_word_heading_candidates_from_map(paragraph_map)
        if mapped:
            return mapped

        toc, _method = extract_word_headings_merged(file_path)
    except Exception as exc:
        logger.debug("[document_structure] word headings 失败: %s", exc)
        return []

    flat: List[Dict[str, Any]] = []

    def walk(nodes: List[Dict[str, Any]], depth: int = 1) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            title = str(node.get("title") or "").strip()
            sec_no = str(node.get("sectionNo") or "").strip()
            if not title:
                walk(list(node.get("children") or []), depth + 1)
                continue
            display = title if not sec_no or title.startswith(sec_no) else f"{sec_no} {title}"
            pos = full_text.find(display) if full_text else -1
            if pos < 0:
                pos = full_text.find(title) if full_text else -1
            if pos < 0 and sec_no:
                pos = full_text.find(f"{sec_no} {title.split(sec_no, 1)[-1].strip()}".strip())
            if pos < 0:
                pos = len(flat) * 1000
            flat.append({
                "offset": pos,
                "level": int(node.get("level") or depth),
                "section_no": sec_no,
                "title": display,
                "heading": display,
                "source": "word_body",
                "confidence": 0.85,
            })
            walk(list(node.get("children") or []), depth + 1)

    walk(toc)
    flat.sort(key=lambda c: c["offset"])
    return flat


def merge_heading_candidates(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 offset 合并去重，高优先级 source 胜出。"""
    merged: Dict[int, Dict[str, Any]] = {}
    for group in groups:
        for c in group or []:
            off = int(c.get("offset") or 0)
            key = off // 20
            existing = merged.get(key)
            if not existing:
                merged[key] = dict(c)
                continue
            p_new = _SOURCE_PRIORITY.get(str(c.get("source") or ""), 1)
            p_old = _SOURCE_PRIORITY.get(str(existing.get("source") or ""), 1)
            if p_new > p_old or (
                p_new == p_old and float(c.get("confidence") or 0) > float(existing.get("confidence") or 0)
            ):
                merged[key] = dict(c)
    out = sorted(merged.values(), key=lambda x: int(x.get("offset") or 0))
    return out


def build_structure_tree(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """栈算法构建嵌套目录树。"""
    if not candidates:
        return []

    from .body_content_trim import is_toc_heading_noise

    roots: List[Dict[str, Any]] = []
    stack: List[Tuple[int, Dict[str, Any]]] = []

    for c in candidates:
        title = str(c.get("title") or "").strip()
        sec_no = str(c.get("section_no") or c.get("sectionNo") or "").strip()
        if is_toc_heading_noise(title, sec_no):
            continue
        level = max(1, int(c.get("level") or 1))
        node = {
            "title": title,
            "sectionNo": sec_no,
            "level": level,
            "offset": int(c.get("offset") or 0),
            "children": [],
        }
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots


def _flatten_tree_preorder(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def walk(ns: List[Dict[str, Any]]) -> None:
        for n in ns or []:
            if not isinstance(n, dict):
                continue
            out.append(n)
            walk(list(n.get("children") or []))

    walk(nodes)
    return out


def _flat_sibling_end_offset(
    flat: List[Dict[str, Any]],
    idx: int,
    text_len: int,
) -> int:
    """当前标题节正文结束偏移：到下一个同级或更高级标题之前（含全部子节如（1）（a））。"""
    if idx < 0 or idx >= len(flat):
        return text_len
    level = int(flat[idx].get("level") or 1)
    try:
        start_off = int(flat[idx].get("offset") or 0)
    except (TypeError, ValueError):
        start_off = 0
    for j in range(idx + 1, len(flat)):
        other_level = int(flat[j].get("level") or 1)
        try:
            other_off = int(flat[j].get("offset") or 0)
        except (TypeError, ValueError):
            other_off = 0
        if other_off > start_off and other_level <= level:
            return other_off
    return text_len


def scan_nested_headings(section_text: str, parent_level: int = 1) -> List[Dict[str, Any]]:
    """在 section 正文内扫描子标题（（1）、1.1 等）。"""
    children: List[Dict[str, Any]] = []
    for line in (section_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        level, sec_no, title, kind = _line_heading_level(stripped)
        if kind == "paren":
            children.append({
                "section_no": sec_no,
                "title": stripped,
                "level": parent_level + 1,
                "kind": "paren",
            })
        elif kind == "paren_letter":
            children.append({
                "section_no": sec_no,
                "title": stripped,
                "level": parent_level + 1,
                "kind": "paren_letter",
            })
        elif level > parent_level and sec_no:
            children.append({
                "section_no": sec_no,
                "title": stripped,
                "level": level,
                "kind": kind,
            })
    return children


def _slice_body_between_offsets(
    full_text: str,
    start_off: int,
    end_off: int,
    title: str,
) -> str:
    """按字符偏移切章节正文，避免行号/标题匹配失败导致空节。"""
    text = full_text or ""
    if end_off <= start_off:
        return ""
    chunk = text[start_off:end_off].strip()
    if not chunk:
        return ""
    title_s = (title or "").strip()
    if title_s:
        if chunk == title_s:
            return ""
        if chunk.startswith(title_s):
            rest = chunk[len(title_s):].lstrip(" \t:：.-—")
            if rest:
                return rest.strip()
        first_line = chunk.splitlines()[0].strip() if chunk else ""
        if first_line == title_s and "\n" in chunk:
            return "\n".join(chunk.splitlines()[1:]).strip()
    return chunk


def split_sections_from_structure(
    full_text: str,
    heading_tree: List[Dict[str, Any]],
    *,
    start_page: int = 1,
) -> List[Dict[str, Any]]:
    """由标题树切片 sections（基于完整原文，不裁剪封面）。"""
    text = full_text or ""
    flat = _flatten_tree_preorder(heading_tree)
    if not flat:
        return []

    lines = text.splitlines()
    line_offsets: List[int] = []
    pos = 0
    for line in lines:
        line_offsets.append(pos)
        pos += len(line) + 1

    def offset_to_line(off: int) -> int:
        for i, lo in enumerate(line_offsets):
            if lo >= off:
                return i
        return max(0, len(lines) - 1)

    sections: List[Dict[str, Any]] = []
    for idx, node in enumerate(flat):
        title = str(node.get("title") or "").strip()
        sec_no = str(node.get("sectionNo") or "").strip()
        level = int(node.get("level") or 1)
        off = int(node.get("offset") or 0)
        start_line = offset_to_line(off)
        end_off = _flat_sibling_end_offset(flat, idx, len(text))
        end_line = offset_to_line(end_off)

        body_lines = []
        for li in range(start_line, end_line):
            if li == start_line and lines[li].strip() == title.strip():
                continue
            if li == start_line and title in lines[li]:
                remainder = lines[li].replace(title, "", 1).strip()
                if remainder:
                    body_lines.append(remainder)
                continue
            body_lines.append(lines[li])

        body = "\n".join(body_lines).strip()
        if len(body) <= len(title) + 5:
            by_offset = _slice_body_between_offsets(
                text,
                off,
                end_off,
                title,
            )
            if by_offset:
                body = by_offset

        if not body:
            body = ""

        subsections = scan_nested_headings(body, parent_level=level)
        sid = f"S{len(sections) + 1:03d}"
        sections.append({
            "section_id": sid,
            "section_no": sec_no,
            "title": title,
            "heading": title,
            "level": level,
            "start_page": start_page,
            "end_page": start_page,
            "start_offset": off,
            "end_offset": end_off,
            "text": body,
            "subsections": subsections,
        })
    return sections


def _sections_from_split_fallback(full_text: str) -> List[Dict[str, Any]]:
    from blueprints.FLGL.GetTestMethod.parser import split_standard_sections

    raw = split_standard_sections(full_text)
    out: List[Dict[str, Any]] = []
    for sec in raw or []:
        if not isinstance(sec, dict):
            continue
        out.append({
            "section_id": sec.get("section_id"),
            "section_no": "",
            "title": sec.get("title") or sec.get("heading") or "",
            "heading": sec.get("heading") or sec.get("title") or "",
            "level": int(sec.get("level") or 1),
            "start_page": int(sec.get("start_page") or 1),
            "end_page": int(sec.get("end_page") or 1),
            "text": sec.get("text") or "",
            "subsections": [],
        })
        title = out[-1]["title"]
        m = re.match(r"^(\d+(?:\.\d+)*)", str(title))
        if m:
            out[-1]["section_no"] = m.group(1)
    return out


def _tree_to_flat_headings(tree: List[Dict[str, Any]], source: str = "structure") -> List[Dict[str, Any]]:
    from .document_heading_extract import flatten_toc_to_headings

    return flatten_toc_to_headings(tree, source=source)


def analyze_document_structure(
    *,
    full_text: str,
    file_path: Optional[str] = None,
    pages: Optional[List[Dict[str, Any]]] = None,
    parse_mode: str = "",
    word_paragraph_map: Optional[List[Dict[str, Any]]] = None,
) -> DocumentStructure:
    """
    统一结构分析入口。
    sections 基于完整 raw_full_text，不做 trim_sections_from_body_start。
    toc_display 仅用于 UI 展示过滤。
    """
    raw = clean_document_text(full_text or "")
    if not raw.strip():
        return DocumentStructure(raw_full_text=raw)

    text_cands = collect_text_heading_candidates(raw)
    word_cands: List[Dict[str, Any]] = []
    ext = os.path.splitext(file_path or "")[1].lower()
    if file_path and ext in (".docx", ".doc") and os.path.isfile(file_path):
        word_cands = collect_word_heading_candidates(
            file_path, raw, word_paragraph_map=word_paragraph_map,
        )

    candidates = merge_heading_candidates(word_cands, text_cands)
    method = "structure"
    fallback = ""

    if len(candidates) < 2:
        sections = _sections_from_split_fallback(raw)
        method = "fallback_split"
        fallback = "split_standard_sections"
        logger.info(
            "[document_structure] 候选标题不足(%d)，回退 split_standard_sections",
            len(candidates),
        )
        tree = build_structure_tree([
            {
                "offset": i * 100,
                "level": int(s.get("level") or 1),
                "section_no": s.get("section_no") or "",
                "title": s.get("title") or "",
                "source": "fallback",
            }
            for i, s in enumerate(sections)
        ])
    else:
        tree = build_structure_tree(candidates)
        sections = split_sections_from_structure(raw, tree)

    flat = _tree_to_flat_headings(tree)
    for i, sec in enumerate(sections):
        if i < len(flat) and flat[i].get("section_id"):
            sec["section_id"] = flat[i]["section_id"]
        elif not sec.get("section_id"):
            sec["section_id"] = f"S{i + 1:03d}"

    from .body_content_trim import filter_toc_from_chapter_one

    toc_display = filter_toc_from_chapter_one(tree) if tree else []

    return DocumentStructure(
        raw_full_text=raw,
        heading_tree=tree,
        sections=sections,
        flat_headings=flat,
        toc_display=toc_display,
        method=method,
        structure_fallback=fallback,
    )


def nested_toc_from_flat_headings(flat_headings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将 flat_headings 嵌套为前端 document-toc 兼容树（与 LLM section_catalogue 同源）。"""
    from .section_number_utils import section_no_sort_key, sort_toc_tree

    ordered = sorted(
        [h for h in (flat_headings or []) if isinstance(h, dict)],
        key=lambda h: section_no_sort_key(str(h.get("sectionNo") or h.get("section_no") or "")),
    )
    roots: List[Dict[str, Any]] = []
    stack: List[Tuple[int, Dict[str, Any]]] = []
    for h in ordered:
        title = str(h.get("title") or "").strip()
        if not title:
            continue
        level = max(1, int(h.get("level") or 1))
        node = {
            "title": title,
            "sectionNo": str(h.get("sectionNo") or h.get("section_no") or "").strip(),
            "section_id": str(h.get("section_id") or "").strip(),
            "level": level,
            "children": [],
        }
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return sort_toc_tree(roots)


def build_section_catalogue(sections: List[Dict[str, Any]], snippet_chars: int = 180) -> List[Dict[str, Any]]:
    result = []
    for sec in sections or []:
        text = (sec.get("text") or "").strip().replace("\n", " ")
        result.append({
            "section_id": sec.get("section_id"),
            "section_no": sec.get("section_no") or "",
            "title": sec.get("title"),
            "start_page": sec.get("start_page"),
            "end_page": sec.get("end_page"),
            "snippet": text[:snippet_chars],
        })
    return result


def build_toc_from_sections_simple(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """无数字章节号时按 level 字段建树（试验方案体例）。"""
    roots: List[Dict[str, Any]] = []
    stack: List[Tuple[int, Dict[str, Any]]] = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or sec.get("heading") or "").strip()
        if not title:
            continue
        level = max(1, int(sec.get("level") or 1))
        node = {
            "title": title,
            "sectionNo": str(sec.get("section_no") or "").strip(),
            "level": level,
            "section_id": sec.get("section_id"),
            "children": [],
        }
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots


def heading_outline_text(structure: DocumentStructure, limit: int = 120) -> str:
    lines: List[str] = []
    for h in (structure.flat_headings or [])[:limit]:
        sno = str(h.get("sectionNo") or h.get("section_no") or "").strip()
        title = str(h.get("title") or "").strip()
        if title:
            lines.append(f"{sno} {title}".strip())
    return "\n".join(lines) if lines else "（无目录）"
