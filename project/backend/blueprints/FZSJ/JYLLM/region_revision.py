"""
JYLLM 区域增量修订：表格结构化编辑、校验、方案锚点。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|\s*$")
_CHAPTER_SKELETON_RE = re.compile(
    r"(?:^|\n)\s*(?:#{1,6}\s+)?1\.[123]\s*(?:设备信息|环境条件|试验方法)",
    re.MULTILINE,
)
_TABLE_INTENT_RE = re.compile(
    r"表格|表头|列|单元格|持续时间|时长|时间|数值|单位|行|改成|改为|修改|填写|填入|具体",
    re.IGNORECASE,
)


@dataclass
class GfmTable:
    start_line: int
    end_line: int
    lines: List[str]
    headers: List[str]

    @property
    def markdown(self) -> str:
        return "\n".join(self.lines)


@dataclass
class TableEditIntent:
    is_table_edit: bool
    target_column: str = ""
    reason: str = ""


@dataclass
class ValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    similarity: float = 1.0


def detect_table_edit_intent(comment: str, region_md: str) -> TableEditIntent:
    c = (comment or "").strip()
    md = region_md or ""
    if not c or not _TABLE_ROW_RE.search(md):
        return TableEditIntent(False, reason="no_table_in_region")

    if not _TABLE_INTENT_RE.search(c):
        return TableEditIntent(False, reason="no_table_intent_in_comment")

    col = ""
    m = re.search(r"[「『\"']?([^「『\"'」』\n，,。]+?)[」』\"']?\s*(?:列|栏)", c)
    if m:
        col = m.group(1).strip()
    if not col:
        for kw in ("持续时间", "时长", "试验时间", "时间", "数值", "单位", "参数", "指标"):
            if kw in c:
                col = kw
                break

    return TableEditIntent(True, target_column=col, reason="table_column_edit")


def resolve_validation_original(original_region: str, region_full: str) -> str:
    """前端 preview 可能是标题摘要，校验须以切分出的真实 region 为准。"""
    full = (region_full or "").strip()
    orig = (original_region or "").strip()
    if not full:
        return orig
    if not orig:
        return full
    if len(orig) < max(40, int(len(full) * 0.25)):
        return full
    if orig not in full and full not in orig:
        return full
    return orig


def count_table_cell_changes(original: str, revised: str) -> int:
    ot = parse_gfm_tables(original)
    rt = parse_gfm_tables(revised)
    if not ot or not rt:
        return 0
    changed = 0
    pairs = min(len(ot), len(rt))
    for i in range(pairs):
        o_rows = ot[i].lines[2:]
        r_rows = rt[i].lines[2:]
        for j in range(min(len(o_rows), len(r_rows))):
            if o_rows[j] != r_rows[j]:
                changed += 1
    return changed


def parse_gfm_tables(md: str) -> List[GfmTable]:
    lines = (md or "").split("\n")
    tables: List[GfmTable] = []
    i = 0
    while i < len(lines):
        if not _TABLE_ROW_RE.match(lines[i]):
            i += 1
            continue
        start = i
        block: List[str] = []
        while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
            block.append(lines[i])
            i += 1
        if len(block) < 2 or not _TABLE_SEP_RE.match(block[1].strip()):
            continue
        headers = _split_table_row(block[0])
        tables.append(GfmTable(start_line=start, end_line=i - 1, lines=block, headers=headers))
    return tables


def _split_table_row(row: str) -> List[str]:
    inner = row.strip().strip("|")
    return [c.strip() for c in inner.split("|")]


def _join_table_row(cells: List[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def find_column_index(headers: List[str], target: str) -> Optional[int]:
    t = (target or "").strip()
    if not t:
        return None
    for i, h in enumerate(headers):
        if h == t or t in h or h in t:
            return i
    return None


def apply_table_column_patch(
    region_md: str,
    table: GfmTable,
    column_name: str,
    updates: List[Dict[str, Any]],
) -> Tuple[str, bool]:
    """按行号更新目标列，其余列字节级保留。"""
    col_idx = find_column_index(table.headers, column_name)
    if col_idx is None:
        return region_md, False

    lines = region_md.split("\n")
    new_lines = list(table.lines)
    data_start = 2
    changed = False

    for upd in updates:
        row_idx = int(upd.get("row", upd.get("row_index", -1)))
        value = str(upd.get("value", upd.get("new_value", ""))).strip()
        if row_idx < 0:
            continue
        line_idx = data_start + row_idx
        if line_idx >= len(new_lines):
            continue
        cells = _split_table_row(new_lines[line_idx])
        while len(cells) < len(table.headers):
            cells.append("")
        if col_idx < len(cells):
            cells[col_idx] = value
            new_lines[line_idx] = _join_table_row(cells)
            changed = True

    if not changed:
        return region_md, False

    rebuilt = lines[: table.start_line] + new_lines + lines[table.end_line + 1 :]
    return "\n".join(rebuilt), True


def apply_table_patch_to_region(
    region_md: str,
    patch: Dict[str, Any],
) -> Tuple[str, bool]:
    column = str(patch.get("column") or patch.get("target_column") or "").strip()
    updates = patch.get("updates") or patch.get("cells") or []
    if not column or not isinstance(updates, list):
        return region_md, False

    tables = parse_gfm_tables(region_md)
    if not tables:
        return region_md, False

    out = region_md
    any_changed = False
    for tbl in tables:
        patched, ok = apply_table_column_patch(out, tbl, column, updates)
        if ok:
            out = patched
            any_changed = True
            tables = parse_gfm_tables(out)
    return out, any_changed


def parse_table_patch_json(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


def text_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _has_chapter_skeleton(text: str) -> bool:
    return bool(_CHAPTER_SKELETON_RE.search(text or ""))


def validate_region_revision(
    original: str,
    revised: str,
    *,
    region_type: str = "heading",
    table_edit_mode: bool = False,
    min_similarity: float = 0.55,
) -> ValidationResult:
    orig = original or ""
    rev = revised or ""
    errors: List[str] = []
    warnings: List[str] = []
    sim = text_similarity(orig, rev)

    if not rev.strip():
        errors.append("修订结果为空")
        return ValidationResult(False, errors, warnings, sim)

    if _has_chapter_skeleton(rev) and not _has_chapter_skeleton(orig):
        errors.append("修订结果出现了原文不含的章骨架（1.1/1.2/1.3），疑似重新生成")

    if table_edit_mode:
        orig_tables = parse_gfm_tables(orig)
        rev_tables = parse_gfm_tables(rev)
        if not rev_tables:
            errors.append("表格编辑后未保留有效 GFM 表格")
        elif orig_tables and rev_tables:
            oh, rh = orig_tables[0].headers, rev_tables[0].headers
            if oh != rh:
                errors.append("表格列编辑模式下表头发生变化")
            if len(oh) != len(rh):
                errors.append("表格列数发生变化")
        cell_changes = count_table_cell_changes(orig, rev)
        if cell_changes == 0 and orig.strip() == rev.strip():
            warnings.append("表格内容未见变更，请核对是否改动了目标单元格")
        # 表格/单元格编辑：不做全文相似度硬拦截（大段正文+改一格会使字符相似度失真）
        ok = len(errors) == 0
        return ValidationResult(ok, errors, warnings, sim)

    if region_type != "chapter":
        # 行级相似度：更适合「只改少量句子/单元格」场景
        o_lines = orig.split("\n")
        r_lines = rev.split("\n")
        line_sim = SequenceMatcher(None, o_lines, r_lines).ratio()
        effective_sim = max(sim, line_sim)
        if effective_sim < min_similarity:
            errors.append(
                f"修订后与原文相似度过低（字符 {sim:.2f} / 行 {line_sim:.2f} < {min_similarity}）"
            )

        olen, rlen = len(orig), len(rev)
        if olen > 80:
            ratio = rlen / max(olen, 1)
            if ratio < 0.3:
                warnings.append(f"修订后长度仅为原文 {ratio:.0%}，请仔细核对 diff")
            elif ratio > 2.0:
                warnings.append(f"修订后长度为原文 {ratio:.0%}，变更范围较大")

    ok = len(errors) == 0
    return ValidationResult(ok, errors, warnings, sim)


def build_scheme_anchor(
    *,
    device_display_name: str = "",
    environment_conditions: str = "",
    chapter_index: int = 1,
    chapter_title: str = "",
    heading_breadcrumb: str = "",
    full_scheme_excerpt: str = "",
    chapter_content: str = "",
    heading_text: str = "",
) -> str:
    parts: List[str] = []
    if device_display_name:
        parts.append(f"设备：{device_display_name[:120]}")
    if environment_conditions:
        parts.append(f"环境：{str(environment_conditions)[:400]}")
    if heading_breadcrumb:
        parts.append(f"位置：{heading_breadcrumb[:300]}")
    elif heading_text:
        parts.append(f"当前小节：{heading_text[:200]}")
    if chapter_title:
        parts.append(f"章：{chapter_title[:120]}")

    excerpt = (full_scheme_excerpt or "").strip()
    if excerpt:
        parts.append("方案摘要：\n" + excerpt[:1200])
    elif chapter_content:
        parts.append("章内摘要：\n" + chapter_content[:800])

    return "\n".join(parts)[:1800]


def diff_stats(original: str, revised: str) -> Dict[str, Any]:
    o_lines = (original or "").split("\n")
    r_lines = (revised or "").split("\n")
    matcher = SequenceMatcher(None, o_lines, r_lines)
    added = removed = changed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            changed += max(i2 - i1, j2 - j1)
    return {
        "similarity": round(text_similarity(original, revised), 4),
        "lines_added": added,
        "lines_removed": removed,
        "lines_changed": changed,
    }
