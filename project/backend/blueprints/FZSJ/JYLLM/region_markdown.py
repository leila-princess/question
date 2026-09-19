"""
JYLLM 方案区域切分与拼回：支持 selection / heading / method / chapter 四种范围。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from blueprints.FZSJ.JYLLM.skeleton_builder import METHOD_SENTINEL_PREFIX, METHOD_SENTINEL_SUFFIX

logger = logging.getLogger(__name__)

_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#*)?\s*$")
_FENCE_START_RE = re.compile(r"^(```|~~~)")
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
_HEADING_ID_IDX_RE = re.compile(r"-h-(\d+)-")
_HEADING_ID_SLUG_RE = re.compile(r"-h-\d+-(.+)$")


def _strip_duplicate_leading_chapter_heading(chapter_title: str, md: str) -> str:
    """与前端 chapterStreamDisplay.stripDuplicateLeadingChapterHeading 一致。"""
    rest = (md or "").lstrip("\ufeff")
    core = re.sub(r"^\d+\.?\s*", "", (chapter_title or "").strip()).strip()
    if not core:
        return md or ""
    for _ in range(2):
        lines = rest.split("\n")
        if not lines:
            break
        first = lines[0].strip()
        hm = re.match(r"^(#{1,6})\s+(.+)$", first)
        if not hm:
            break
        h_text = hm.group(2).strip()
        heading_core = re.sub(r"^\d+\.?\s*", "", h_text).strip()
        if heading_core == core or h_text == (chapter_title or "").strip():
            rest = "\n".join(lines[1:]).lstrip("\n")
        else:
            break
    return rest


def _resolve_heading_target(
    outline: List[Dict[str, Any]],
    heading_id: str,
    heading_text: str = "",
) -> Optional[Dict[str, Any]]:
    hid = (heading_id or "").strip()
    if hid:
        target = next((h for h in outline if h["id"] == hid), None)
        if target:
            return target
        m = _HEADING_ID_SLUG_RE.search(hid)
        if m:
            slug = m.group(1)
            for h in outline:
                if _slug_fragment(h["text"]) == slug:
                    return h
    ht = (heading_text or "").strip()
    if ht:
        target = next((h for h in outline if (h.get("text") or "").strip() == ht), None)
        if target:
            return target
        target = next((h for h in outline if ht in (h.get("text") or "")), None)
        if target:
            return target
    return None


def _slug_fragment(text: str, max_len: int = 48) -> str:
    t = (text or "").strip()[:max_len]
    t = re.sub(r"[^\w\u4e00-\u9fff-]", "-", t)
    t = re.sub(r"-+", "-", t).strip("-")
    return t or "h"


def extract_heading_outline(markdown: str, id_prefix: str) -> List[Dict[str, Any]]:
    """与前端 extractMarkdownHeadingOutline 对齐。"""
    text = (markdown or "").replace("\r\n", "\n")
    lines = text.split("\n")
    out: List[Dict[str, Any]] = []
    in_fence = False
    fence_tick = False
    idx = 0
    line_starts: List[int] = []
    pos = 0
    for line in lines:
        line_starts.append(pos)
        pos += len(line) + 1

    for i, line in enumerate(lines):
        t = line.rstrip()
        fence_start = _FENCE_START_RE.match(t.lstrip())
        if fence_start:
            tick = fence_start.group(1)[0] == "`"
            if not in_fence:
                in_fence = True
                fence_tick = tick
            elif fence_tick == tick and t.lstrip().startswith("`" if tick else "~"):
                in_fence = False
            continue
        if in_fence:
            continue
        m = _HEADING_LINE_RE.match(t.strip())
        if not m:
            continue
        level = len(m.group(1))
        raw_text = m.group(2).strip()
        hid = f"{id_prefix}-h-{idx}-{_slug_fragment(raw_text)}"
        out.append({
            "id": hid,
            "level": level,
            "text": raw_text,
            "line_index": i,
            "char_start": line_starts[i] if i < len(line_starts) else 0,
        })
        idx += 1
    return out


def _line_index_at_offset(text: str, offset: int) -> int:
    if offset <= 0:
        return 0
    upto = text[:offset]
    return upto.count("\n")


def _expand_to_table_bounds(text: str, start: int, end: int) -> Tuple[int, int]:
    """若选区落在表格行内，扩展至完整表格块。"""
    lines = text.split("\n")
    start_line = _line_index_at_offset(text, start)
    end_line = _line_index_at_offset(text, max(start, end - 1))
    lo = min(start_line, end_line)
    hi = max(start_line, end_line)

    def is_table_line(li: int) -> bool:
        if li < 0 or li >= len(lines):
            return False
        return bool(_TABLE_ROW_RE.match(lines[li]))

    if not any(is_table_line(i) for i in range(lo, hi + 1)):
        return start, end

    while lo > 0 and is_table_line(lo - 1):
        lo -= 1
    while hi + 1 < len(lines) and is_table_line(hi + 1):
        hi += 1

    char_start = sum(len(lines[i]) + 1 for i in range(lo))
    char_end = sum(len(lines[i]) + 1 for i in range(hi + 1)) - 1
    return max(0, char_start), min(len(text), char_end)


def _find_text_fuzzy(text: str, snippet: str) -> Optional[Tuple[int, int]]:
    sn = (snippet or "").strip()
    if not sn:
        return None
    idx = text.find(sn)
    if idx >= 0:
        return idx, idx + len(sn)
    sn2 = re.sub(r"\s+", " ", sn)
    norm_text = re.sub(r"[ \t]+", " ", text)
    idx2 = norm_text.find(sn2)
    if idx2 < 0:
        return None
    return idx2, idx2 + len(sn2)


@dataclass
class RegionSlice:
    before: str
    region: str
    after: str
    meta: Dict[str, Any]


def splice_region(before: str, new_region: str, after: str) -> str:
    parts = [before or "", new_region or "", after or ""]
    merged = "".join(parts)
    return merged.strip() if not before and not after else merged


def validate_gfm_tables(md: str) -> List[str]:
    warnings: List[str] = []
    lines = (md or "").split("\n")
    i = 0
    while i < len(lines):
        if not _TABLE_ROW_RE.match(lines[i]):
            i += 1
            continue
        block: List[str] = []
        while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
            block.append(lines[i])
            i += 1
        if len(block) < 2:
            warnings.append("表格块少于两行，可能不完整")
            continue
        sep = block[1].strip()
        if not re.match(r"^\|[\s\-:|]+\|\s*$", sep):
            warnings.append("表格缺少 GFM 分隔行")
            continue
        cols = block[0].count("|") - 1
        for row in block:
            c = row.count("|") - 1
            if c != cols:
                warnings.append(f"表格列数不一致: 期望 {cols}, 实际 {c}")
    return warnings


def extract_heading_block(
    chapter_md: str,
    heading_id: str,
    chapter_index: int,
    *,
    chapter_title: str = "",
    heading_text: str = "",
) -> RegionSlice:
    md = chapter_md or ""
    prefix = f"jyllm-ch-{chapter_index}"
    ht = (heading_text or "").strip()

    variants: List[str] = []
    for candidate in (md, _strip_duplicate_leading_chapter_heading(chapter_title, md)):
        if candidate and candidate not in variants:
            variants.append(candidate)

    target: Optional[Dict[str, Any]] = None
    working_md = md
    for variant in variants:
        outline = extract_heading_outline(variant, prefix)
        target = _resolve_heading_target(outline, heading_id, ht)
        if target:
            working_md = variant
            break

    if not target:
        # 目录 ID 可能基于去重后的正文：用 stripped 大纲的标题文本在 raw 正文里再定位
        stripped = _strip_duplicate_leading_chapter_heading(chapter_title, md)
        if stripped != md:
            outline_stripped = extract_heading_outline(stripped, prefix)
            ref = _resolve_heading_target(outline_stripped, heading_id, ht)
            if ref:
                ref_text = (ref.get("text") or "").strip()
                outline_raw = extract_heading_outline(md, prefix)
                target = next((h for h in outline_raw if (h.get("text") or "").strip() == ref_text), None)
                if not target and ref_text:
                    target = next((h for h in outline_raw if ref_text in (h.get("text") or "")), None)
                working_md = md

    if not target:
        raise ValueError(f"未找到标题: {heading_id}" + (f" ({ht})" if ht else ""))

    lines = working_md.split("\n")
    hi = target["line_index"]
    level = target["level"]
    heading_end = sum(len(lines[i]) + 1 for i in range(hi + 1))

    outline_working = extract_heading_outline(working_md, prefix)
    end_line = len(lines)
    for h in outline_working:
        if h["line_index"] <= hi:
            continue
        if h["level"] <= level:
            end_line = h["line_index"]
            break

    body_lines = lines[hi + 1 : end_line]
    region = "\n".join(body_lines)
    if body_lines:
        region_end = heading_end + len(region) + (1 if region else 0)
    else:
        region_end = heading_end

    before = working_md[:heading_end]
    after = working_md[region_end:] if region_end < len(working_md) else ""
    merged_slice = RegionSlice(
        before=before,
        region=region,
        after=after,
        meta={"heading_id": heading_id, "heading_text": target["text"], "heading_level": level},
    )

    if working_md is md:
        return merged_slice

    # 在 stripped 变体上切分后，映射回原始 chapter_md 存储格式
    anchor = md.find(working_md)
    if anchor > 0:
        stripped_prefix = md[:anchor]
        return RegionSlice(
            before=stripped_prefix + merged_slice.before,
            region=merged_slice.region,
            after=merged_slice.after,
            meta=merged_slice.meta,
        )
    return merged_slice


def extract_method_block(chapter_md: str, method_id: str) -> RegionSlice:
    md = chapter_md or ""
    mid = (method_id or "").strip()
    if not mid:
        raise ValueError("method_id 不能为空")

    sentinel = f"{METHOD_SENTINEL_PREFIX}{mid}{METHOD_SENTINEL_SUFFIX}"
    idx = md.find(sentinel)
    if idx >= 0:
        start = idx + len(sentinel)
        while start < len(md) and md[start] in "\r\n":
            start += 1
        next_sent = md.find(METHOD_SENTINEL_PREFIX, start)
        end = next_sent if next_sent >= 0 else len(md)
        before = md[:idx]
        region = md[start:end].strip("\n")
        after = md[end:]
        return RegionSlice(before=before + sentinel + "\n", region=region, after=after, meta={"method_id": mid})

    pattern = re.compile(
        rf"(^|\n)((?:#{{1,6}}\s+.*{re.escape(mid)}.*\n)([\s\S]*?))(?=\n#{{1,6}}\s|\Z)",
        re.MULTILINE,
    )
    m = pattern.search(md)
    if m:
        block = m.group(2)
        start_idx = m.start(2)
        end_idx = m.end(2)
        return RegionSlice(
            before=md[:start_idx],
            region=block.strip("\n"),
            after=md[end_idx:],
            meta={"method_id": mid, "fallback": "heading"},
        )
    raise ValueError(f"未找到方法块: {method_id}")


def extract_selection_block(
    chapter_md: str,
    *,
    start_offset: Optional[int] = None,
    end_offset: Optional[int] = None,
    selected_text: Optional[str] = None,
) -> RegionSlice:
    md = chapter_md or ""
    start = int(start_offset) if start_offset is not None else None
    end = int(end_offset) if end_offset is not None else None

    if start is None or end is None or start < 0 or end <= start:
        found = _find_text_fuzzy(md, selected_text or "")
        if not found:
            raise ValueError("无法在章节中定位选中文本")
        start, end = found

    start, end = _expand_to_table_bounds(md, start, end)
    start = max(0, min(start, len(md)))
    end = max(start, min(end, len(md)))

    return RegionSlice(
        before=md[:start],
        region=md[start:end],
        after=md[end:],
        meta={"start_offset": start, "end_offset": end},
    )


def extract_region(chapter_md: str, spec: Dict[str, Any]) -> RegionSlice:
    region_type = (spec.get("region_type") or "").strip()
    chapter_index = int(spec.get("chapter_index") or 1)

    if region_type == "chapter":
        return RegionSlice(before="", region=chapter_md or "", after="", meta={"region_type": "chapter"})

    if region_type == "heading":
        hid = (spec.get("heading_id") or "").strip()
        return extract_heading_block(
            chapter_md,
            hid,
            chapter_index,
            chapter_title=(spec.get("chapter_title") or "").strip(),
            heading_text=(spec.get("heading_text") or spec.get("label") or "").strip(),
        )

    if region_type == "method":
        mid = (spec.get("method_id") or "").strip()
        return extract_method_block(chapter_md, mid)

    if region_type == "selection":
        return extract_selection_block(
            chapter_md,
            start_offset=spec.get("start_offset"),
            end_offset=spec.get("end_offset"),
            selected_text=spec.get("selected_text"),
        )

    raise ValueError(f"不支持的 region_type: {region_type}")
