"""
将试验方案 Markdown 转为正式 Word（.docx）。

供 JYLLM / JYGZ 等路由通过 ``markdown_to_docx_bytes`` 复用。

两种生成模式（由环境变量 ``SCHEME_DOCX_TEMPLATE`` 控制）：
- **未设置模板**：纯 Python + python-docx + OOXML，动态创建样式、多级编号、分节、目录域等。
- **已设置模板**：加载参考 ``.docx`` / ``.dotx``，保留其样式与编号定义，仅替换正文结构。

Flask 集成保持不变::

    from utils.scheme_markdown_to_docx import markdown_to_docx_bytes
    docx_bytes = markdown_to_docx_bytes(markdown_string)
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Iterator, List, Optional, Tuple

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# 正文字体
_FONT_BODY_EA = "宋体"
_FONT_BODY_ASCII = "Calibri"
_FONT_HEADING_EA = "黑体"
_FONT_HEADING_ASCII = "Arial"

# 表格表头底纹 RGB(217,217,217)
_TABLE_HEADER_FILL = "D9D9D9"

_TOC_MARKERS = ("[TOC]", "<!-- TOC -->", "<!--TOC-->")
_PAGEBREAK_MARKERS = (r"\newpage", "<!-- pagebreak -->", "<!--pagebreak-->")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_APPENDIX_RE = re.compile(r"^附录\s*([A-ZＡ-Ｚ])\s*(.*)$", re.IGNORECASE)
_STRIP_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*\.?)\s+")
# JYLLM 分章占位标题（仅用于 Markdown 分块，不写入 Word / 目录）
_CHAPTER_PLACEHOLDER_RE = re.compile(
    r"^章节\s*[\d０-９]+\s*$|^第\s*[\d０-９]+\s*章节\s*$",
    re.IGNORECASE,
)
_MAX_HEADING_LEVEL = 6  # Markdown # ~ ######
_COLOR_HEADING = RGBColor(0, 0, 0)
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_YAML_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_LATEX_BLOCK_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_LATEX_INLINE_RE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")


# ---------------------------------------------------------------------------
# 元数据
# ---------------------------------------------------------------------------


@dataclass
class SchemeMeta:
    """从 YAML Front Matter 或正文推断的文档元数据。"""

    title: str = "试验方案"
    doc_id: str = ""
    date: str = ""
    organization: str = ""
    subtitle_lines: List[str] = field(default_factory=list)
    author: str = ""
    reviewer: str = ""
    approver: str = ""
    cosigner: str = ""
    header_title: str = ""  # 页眉右侧标题，默认同 title

    @classmethod
    def from_yaml_dict(cls, d: dict[str, Any]) -> "SchemeMeta":
        def _pick(*keys: str, default: str = "") -> str:
            for k in keys:
                v = d.get(k)
                if v is not None and str(v).strip():
                    return str(v).strip()
            return default

        title = _pick("title", "文档标题", "doc_title", default="试验方案")
        return cls(
            title=title,
            doc_id=_pick("doc_id", "文档编号", "编号", "id"),
            date=_pick("date", "日期", "doc_date"),
            organization=_pick("organization", "单位", "编制单位", "org"),
            author=_pick("author", "编写", "编制"),
            reviewer=_pick("reviewer", "审核"),
            approver=_pick("approver", "批准"),
            cosigner=_pick("cosigner", "会签"),
            header_title=_pick("header_title", default=title),
        )


def _parse_simple_yaml(block: str) -> dict[str, Any]:
    """极简 YAML 解析（key: value），避免强依赖 PyYAML。"""
    result: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            result[key] = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
        else:
            result[key] = val
    return result


def _split_front_matter(content: str) -> Tuple[SchemeMeta, str]:
    m = _YAML_FM_RE.match(content)
    if not m:
        return SchemeMeta(), content
    meta = SchemeMeta.from_yaml_dict(_parse_simple_yaml(m.group(1)))
    body = content[m.end() :]
    return meta, body


# ---------------------------------------------------------------------------
# OOXML 工具
# ---------------------------------------------------------------------------


def _set_run_font(
    run,
    *,
    east_asia: str = _FONT_BODY_EA,
    ascii_font: str = _FONT_BODY_ASCII,
    size_pt: float = 12,
    bold: bool = False,
    italic: bool = False,
    color: RGBColor | None = None,
) -> None:
    run.font.name = ascii_font
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.italic = italic
    if color is not None:
        run.font.color.rgb = color
    r = run._element.get_or_add_rPr()
    rfonts = r.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), ascii_font)
    rfonts.set(qn("w:hAnsi"), ascii_font)
    rfonts.set(qn("w:eastAsia"), east_asia)
    if color is not None:
        c = OxmlElement("w:color")
        c.set(qn("w:val"), "000000")
        r.append(c)


def _set_paragraph_format(
    paragraph: Paragraph,
    *,
    align=None,
    line_spacing_15: bool = False,
    first_line_indent_cm: float = 0,
    space_before_pt: float = 0,
    space_after_pt: float = 0,
) -> None:
    pf = paragraph.paragraph_format
    if align is not None:
        pf.alignment = align
    if line_spacing_15:
        pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    if first_line_indent_cm:
        pf.first_line_indent = Cm(first_line_indent_cm)
    if space_before_pt:
        pf.space_before = Pt(space_before_pt)
    if space_after_pt:
        pf.space_after = Pt(space_after_pt)


def _insert_page_break(paragraph: Paragraph) -> None:
    run = paragraph.add_run()
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    run._r.append(br)


def _insert_section_break_next_page(paragraph: Paragraph) -> None:
    """段末插入「下一页」分节符。"""
    p = paragraph._p
    p_pr = p.get_or_add_pPr()
    sect = OxmlElement("w:sectPr")
    type_el = OxmlElement("w:type")
    type_el.set(qn("w:val"), "nextPage")
    sect.append(type_el)
    p_pr.append(sect)


def _append_field(paragraph: Paragraph, instr: str, *, placeholder: str = "1") -> None:
    """向段落插入 Word 域；各组件必须分属独立 run，否则 Word 无法识别/更新。"""
    r_begin = paragraph.add_run()
    fc_b = OxmlElement("w:fldChar")
    fc_b.set(qn("w:fldCharType"), "begin")
    r_begin._r.append(fc_b)

    r_instr = paragraph.add_run()
    instr_el = OxmlElement("w:instrText")
    instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = f" {instr.strip()} "
    r_instr._r.append(instr_el)

    r_sep = paragraph.add_run()
    fc_s = OxmlElement("w:fldChar")
    fc_s.set(qn("w:fldCharType"), "separate")
    r_sep._r.append(fc_s)

    if placeholder:
        r_ph = paragraph.add_run(placeholder)
        _set_run_font(r_ph, size_pt=9)

    r_end = paragraph.add_run()
    fc_e = OxmlElement("w:fldChar")
    fc_e.set(qn("w:fldCharType"), "end")
    r_end._r.append(fc_e)


def _enable_update_fields_on_open(doc: Document) -> None:
    """打开文档时自动更新域（目录、页码等）。"""
    settings = doc.settings.element
    if settings.find(qn("w:updateFields")) is None:
        upd = OxmlElement("w:updateFields")
        upd.set(qn("w:val"), "true")
        settings.append(upd)


def _set_outline_level(paragraph: Paragraph, heading_level: int) -> None:
    """显式设置大纲级别，供 TOC 域识别标题。"""
    p_pr = paragraph._p.get_or_add_pPr()
    if p_pr.find(qn("w:outlineLvl")) is not None:
        return
    outline = OxmlElement("w:outlineLvl")
    outline.set(qn("w:val"), str(max(0, heading_level - 1)))
    p_pr.append(outline)


def _add_paragraph_bookmark(paragraph: Paragraph, bookmark_name: str, bookmark_id: int) -> None:
    p = paragraph._p
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bookmark_id))
    start.set(qn("w:name"), bookmark_name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bookmark_id))
    p.insert(0, start)
    p.append(end)


def _should_skip_heading(raw_text: str) -> bool:
    """跳过「章节 0」「章节1」等 JYLLM 分章占位标题。"""
    t = raw_text.strip()
    if _CHAPTER_PLACEHOLDER_RE.match(t):
        return True
    display = _strip_heading_number(t)
    return bool(_CHAPTER_PLACEHOLDER_RE.match(display))


def _heading_word_level(md_level: int) -> int:
    """Markdown # 数量 → Word Heading 级别（1~9）。"""
    return max(1, min(md_level, 9))


def _set_heading_flush_left(paragraph: Paragraph) -> None:
    """标题顶格：清除左缩进与列表悬挂缩进。"""
    pf = paragraph.paragraph_format
    pf.left_indent = Pt(0)
    pf.first_line_indent = Pt(0)
    p_pr = paragraph._p.get_or_add_pPr()
    for tag in ("w:ind",):
        el = p_pr.find(qn(tag))
        if el is not None:
            p_pr.remove(el)
    ind = OxmlElement("w:ind")
    ind.set(qn("w:left"), "0")
    ind.set(qn("w:firstLine"), "0")
    ind.set(qn("w:hanging"), "0")
    p_pr.append(ind)


# (level, plain_title, bookmark, number_label) — number_label 如 1 / 1.1 / 附录A
_TocEntry = Tuple[int, str, str, str]


def _heading_number_label(
    level: int,
    counters: List[int],
    *,
    is_appendix: bool,
    appendix_letter: str = "",
) -> str:
    """按标题层级生成序号文本（与正文 Word 多级编号格式一致）。"""
    if is_appendix:
        letter = (appendix_letter or "A").strip().upper()
        return f"附录{letter}"
    lvl = max(1, min(level, _MAX_HEADING_LEVEL))
    counters[lvl - 1] += 1
    for i in range(lvl, _MAX_HEADING_LEVEL):
        counters[i] = 0
    # 动态查找首个非零计数器，跳过未出现的父级（避免 "0.1" 或多余层级）
    start_idx = next((i for i in range(lvl) if counters[i] != 0), 0)
    return ".".join(str(counters[i]) for i in range(start_idx, lvl))


def _format_numbered_heading(num_label: str, title: str) -> str:
    t = (title or "").strip()
    n = (num_label or "").strip()
    if n and t:
        return f"{n} {t}"
    return n or t


def _extract_headings_for_toc(md_body: str) -> List[_TocEntry]:
    """预扫描全部标题（至 6 级），生成目录项（含层级序号）；跳过章节占位。"""
    entries: List[_TocEntry] = []
    counters = [0] * _MAX_HEADING_LEVEL
    appendix_counter = 0
    bid = 0
    for line in md_body.splitlines():
        stripped = line.strip()
        hm = _HEADING_RE.match(stripped)
        if not hm:
            continue
        raw = hm.group(2).strip()
        if _should_skip_heading(raw):
            continue
        level = min(len(hm.group(1)), _MAX_HEADING_LEVEL)
        text = _strip_heading_number(raw)
        app_m = _APPENDIX_RE.match(raw.strip())
        is_appendix = bool(app_m)
        if is_appendix:
            letter = (app_m.group(1) if app_m else "").upper() or chr(ord("A") + appendix_counter)
            appendix_counter += 1
        else:
            letter = ""
        num_label = _heading_number_label(
            level, counters, is_appendix=is_appendix, appendix_letter=letter
        )
        entries.append((level, text, f"_Toc{bid}", num_label))
        bid += 1
    return entries


def _add_toc_entry_line(
    doc: Document,
    level: int,
    title: str,
    bookmark_name: str,
    num_label: str = "",
) -> None:
    """目录行：序号 + 标题 + 点线 + PAGEREF 页码（打开 Word 后自动更新）。"""
    p = doc.add_paragraph()
    # 按标题层级缩进，最多 2.5cm
    p.paragraph_format.left_indent = Cm(min(0.5 * (level - 1), 2.5))
    tab_stops = p.paragraph_format.tab_stops
    tab_stops.add_tab_stop(Inches(6.2), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)

    display = _format_numbered_heading(num_label, title)
    r_title = p.add_run(display)
    _set_run_font(r_title, size_pt=12, color=_COLOR_HEADING)
    p.add_run("\t")
    _append_field(p, f"PAGEREF {bookmark_name} \\h", placeholder="1")


def _add_toc_section(doc: Document, entries: List[_TocEntry]) -> None:
    """目录页：标题 + 逐条目录（不依赖空 TOC 域）。"""
    tp = doc.add_paragraph()
    tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rt = tp.add_run("目  录")
    _set_run_font(rt, east_asia=_FONT_HEADING_EA, size_pt=16, bold=True)
    doc.add_paragraph("")
    for level, text, bm, num_label in entries:
        _add_toc_entry_line(doc, level, text, bm, num_label)


def _set_cell_shading(cell, fill_hex: str) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    tc_pr.append(shd)


def _set_table_borders(table: Table) -> None:
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        tbl.insert(0, tbl_pr)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "000000")
        borders.append(el)
    tbl_pr.append(borders)


def _set_table_width_pct(table: Table, pct: int = 5000) -> None:
    """pct 为 Word 的 50.00% 单位（5000 = 100%）。"""
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        tbl.insert(0, tbl_pr)
    tbl_w = OxmlElement("w:tblW")
    tbl_w.set(qn("w:type"), "pct")
    tbl_w.set(qn("w:w"), str(pct))
    tbl_pr.append(tbl_w)


def _add_num_pr(paragraph: Paragraph, num_id: int, ilvl: int) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    ilvl_el = OxmlElement("w:ilvl")
    ilvl_el.set(qn("w:val"), str(ilvl))
    num_id_el = OxmlElement("w:numId")
    num_id_el.set(qn("w:val"), str(num_id))
    num_pr.append(ilvl_el)
    num_pr.append(num_id_el)
    p_pr.append(num_pr)


def _ensure_numbering_definitions(doc: Document) -> Tuple[int, int]:
    """
    在 numbering.xml 中创建：
    - numId=1：正文多级标题 1 / 1.1 / 1.1.1 / 1.1.1.1
    - numId=2：附录 A / B …
    返回 (heading_num_id, appendix_num_id)。
    """
    try:
        numbering_part = doc.part.numbering_part
    except Exception:
        # 无 numbering 部件时新建
        from docx.parts.numbering import NumberingPart

        numbering_part = NumberingPart.new()
        doc.part.relate_to(numbering_part, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering")

    numbering_el = numbering_part.element

    def _abstract_num(aid: int, levels: List[dict]) -> None:
        abs_num = OxmlElement("w:abstractNum")
        abs_num.set(qn("w:abstractNumId"), str(aid))
        mlt = OxmlElement("w:multiLevelType")
        mlt.set(qn("w:val"), "multilevel")
        abs_num.append(mlt)
        for lvl_def in levels:
            ilvl = lvl_def["ilvl"]
            lvl = OxmlElement("w:lvl")
            lvl.set(qn("w:ilvl"), str(ilvl))
            start = OxmlElement("w:start")
            start.set(qn("w:val"), "1")
            num_fmt = OxmlElement("w:numFmt")
            num_fmt.set(qn("w:val"), lvl_def.get("fmt", "decimal"))
            lvl_text = OxmlElement("w:lvlText")
            lvl_text.set(qn("w:val"), lvl_def["text"])
            lvl_jc = OxmlElement("w:lvlJc")
            lvl_jc.set(qn("w:val"), "left")
            lvl.append(start)
            lvl.append(num_fmt)
            lvl.append(lvl_text)
            lvl.append(lvl_jc)
            p_pr = OxmlElement("w:pPr")
            ind = OxmlElement("w:ind")
            # 编号顶格：不额外左缩进
            ind.set(qn("w:left"), "0")
            ind.set(qn("w:hanging"), "360")
            p_pr.append(ind)
            lvl.append(p_pr)
            abs_num.append(lvl)
        numbering_el.append(abs_num)

    def _num(num_id: int, abstract_id: int) -> None:
        num = OxmlElement("w:num")
        num.set(qn("w:numId"), str(num_id))
        ref = OxmlElement("w:abstractNumId")
        ref.set(qn("w:val"), str(abstract_id))
        num.append(ref)
        numbering_el.append(num)

    def _multilevel_text(ilvl: int) -> str:
        return ".".join([f"%{i + 1}" for i in range(ilvl + 1)])

    # 正文标题：1 ~ 1.1.1.1.1.1（最多 6 级）
    heading_levels = [
        {"ilvl": i, "text": _multilevel_text(i), "fmt": "decimal"}
        for i in range(_MAX_HEADING_LEVEL)
    ]
    appendix_levels = [
        {"ilvl": 0, "text": "附录%1", "fmt": "upperLetter"},
    ]
    _abstract_num(1, heading_levels)
    _abstract_num(2, appendix_levels)
    _num(1, 1)
    _num(2, 2)
    return 1, 2


# ---------------------------------------------------------------------------
# 样式
# ---------------------------------------------------------------------------


def _configure_document_styles(doc: Document) -> None:
    """配置 Normal / Heading1-4 字体与段落格式。"""
    normal = doc.styles["Normal"]
    normal.font.name = _FONT_BODY_ASCII
    normal.font.size = Pt(12)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), _FONT_BODY_EA)

    heading_specs = [
        ("Heading 1", 16, 18, 18),
        ("Heading 2", 14, 12, 6),
        ("Heading 3", 13, 6, 3),
        ("Heading 4", 12, 3, 3),
        ("Heading 5", 12, 3, 3),
        ("Heading 6", 12, 3, 3),
    ]
    for style_name, size, sb, sa in heading_specs:
        try:
            st = doc.styles[style_name]
        except KeyError:
            continue
        st.font.name = _FONT_HEADING_ASCII
        st.font.bold = True
        st.font.size = Pt(size)
        st.font.color.rgb = _COLOR_HEADING
        st.element.rPr.rFonts.set(qn("w:eastAsia"), _FONT_HEADING_EA)
        st.paragraph_format.space_before = Pt(sb)
        st.paragraph_format.space_after = Pt(sa)
        st.paragraph_format.keep_with_next = True
        st.paragraph_format.left_indent = Pt(0)
        st.paragraph_format.first_line_indent = Pt(0)


# ---------------------------------------------------------------------------
# 页眉页脚 / 分节
# ---------------------------------------------------------------------------


def _set_section_page_number_start(section, start: int = 1) -> None:
    sect_pr = section._sectPr
    pg_num = sect_pr.find(qn("w:pgNumType"))
    if pg_num is None:
        pg_num = OxmlElement("w:pgNumType")
        sect_pr.append(pg_num)
    pg_num.set(qn("w:start"), str(start))


def _set_section_different_first_page(section, enabled: bool = True) -> None:
    sect_pr = section._sectPr
    title_pg = sect_pr.find(qn("w:titlePg"))
    if enabled:
        if title_pg is None:
            title_pg = OxmlElement("w:titlePg")
            sect_pr.append(title_pg)
    elif title_pg is not None:
        sect_pr.remove(title_pg)


def _apply_header_footer(
    section,
    *,
    doc_id: str,
    header_title: str,
    show: bool = True,
) -> None:
    if not show:
        section.header.is_linked_to_previous = True
        section.footer.is_linked_to_previous = True
        return

    header = section.header
    header.is_linked_to_previous = False
    hp = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    hp.clear()
    # 左：文档标识
    r1 = hp.add_run(f"文档标识：{doc_id or '——'}")
    _set_run_font(r1, size_pt=9)
    # 右：标题（制表位）
    tab_stops = hp.paragraph_format.tab_stops
    tab_stops.add_tab_stop(Inches(6.0), alignment=WD_ALIGN_PARAGRAPH.RIGHT)
    r2 = hp.add_run("\t" + (header_title or "试验方案"))
    _set_run_font(r2, size_pt=9)

    footer = section.footer
    footer.is_linked_to_previous = False
    fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fp.clear()
    r = fp.add_run("第 ")
    _set_run_font(r, size_pt=9)
    _append_field(fp, "PAGE", placeholder="1")
    r2 = fp.add_run(" 页 共 ")
    _set_run_font(r2, size_pt=9)
    _append_field(fp, "NUMPAGES", placeholder="1")
    r3 = fp.add_run(" 页")
    _set_run_font(r3, size_pt=9)


# ---------------------------------------------------------------------------
# Markdown 解析
# ---------------------------------------------------------------------------


@dataclass
class _Block:
    kind: str
    payload: Any = None


def _is_table_separator(line: str) -> bool:
    s = line.strip()
    if not s.startswith("|"):
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    return bool(cells) and all(re.match(r"^:?-+:?$", c) for c in cells if c)


def _parse_table_row(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _strip_heading_number(text: str) -> str:
    t = text.strip()
    m = _APPENDIX_RE.match(t)
    if m:
        rest = (m.group(2) or "").strip()
        return rest or f"附录{m.group(1)}"
    return _STRIP_NUM_RE.sub("", t).strip() or t


def _iter_markdown_blocks(lines: List[str]) -> Iterator[_Block]:
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = raw.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            i += 1
            yield _Block("blank")
            continue

        if stripped in _TOC_MARKERS:
            i += 1
            yield _Block("toc")
            continue

        if stripped in _PAGEBREAK_MARKERS:
            i += 1
            yield _Block("pagebreak")
            continue

        hm = _HEADING_RE.match(stripped)
        if hm:
            text = hm.group(2).strip()
            if not _should_skip_heading(text):
                level = len(hm.group(1))
                appendix = bool(_APPENDIX_RE.match(text))
                yield _Block("heading", (level, text, appendix))
            i += 1
            continue

        if stripped.startswith("|") and "|" in stripped[1:]:
            rows: List[List[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                if _is_table_separator(lines[i]):
                    i += 1
                    continue
                rows.append(_parse_table_row(lines[i]))
                i += 1
            if rows:
                yield _Block("table", rows)
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            items = []
            while i < n:
                s = lines[i].strip()
                if s.startswith("- ") or s.startswith("* "):
                    items.append(s[2:].strip())
                    i += 1
                else:
                    break
            yield _Block("ul", items)
            continue

        if re.match(r"^\d+\.\s", stripped):
            items = []
            while i < n and re.match(r"^\d+\.\s", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s*", "", lines[i].strip()))
                i += 1
            yield _Block("ol", items)
            continue

        if stripped.startswith("!["):
            m = _IMG_RE.match(stripped)
            if m:
                i += 1
                yield _Block("image", (m.group(1), m.group(2)))
                continue

        # 普通段落：合并到空行
        para_lines = [stripped]
        i += 1
        while i < n and lines[i].strip() and not _HEADING_RE.match(lines[i].strip()):
            s = lines[i].strip()
            if s in _TOC_MARKERS or s in _PAGEBREAK_MARKERS:
                break
            if s.startswith("|") or s.startswith("#"):
                break
            para_lines.append(s)
            i += 1
        yield _Block("para", " ".join(para_lines))


def _normalize_latex(text: str) -> str:
    text = _LATEX_BLOCK_RE.sub(lambda m: f"[公式: {m.group(1).strip()[:80]}]", text)
    text = _LATEX_INLINE_RE.sub(lambda m: f"[{m.group(1).strip()}]", text)
    return text


def _add_inline_markdown(paragraph: Paragraph, text: str, *, body: bool = True) -> None:
    text = _normalize_latex(text)
    parts = re.split(r"(\*\*.+?\*\*|\*.+?\*|`.+?`)", text)
    for part in parts:
        if not part:
            continue
        bold_m = re.match(r"\*\*(.+?)\*\*", part)
        italic_m = re.match(r"\*(.+?)\*", part)
        code_m = re.match(r"`(.+?)`", part)
        if bold_m:
            run = paragraph.add_run(bold_m.group(1))
            _set_run_font(run, bold=True, size_pt=12 if body else 12)
        elif italic_m:
            run = paragraph.add_run(italic_m.group(1))
            _set_run_font(run, italic=True)
        elif code_m:
            run = paragraph.add_run(code_m.group(1))
            _set_run_font(run, ascii_font="Consolas", east_asia="宋体")
        else:
            run = paragraph.add_run(part)
            _set_run_font(run)


# ---------------------------------------------------------------------------
# 封面 / 签署
# ---------------------------------------------------------------------------


def _build_cover_and_signature(doc: Document, meta: SchemeMeta) -> None:
    """封面 + 签署页（无页眉页脚，由后续分节处理）。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(f"文档标识：{meta.doc_id or '——'}")
    _set_run_font(r, size_pt=12)

    for _ in range(4):
        doc.add_paragraph("")

    # 标题（支持多行 subtitle_lines + 主标题拆分）
    title_lines = meta.subtitle_lines or []
    if not title_lines and meta.title:
        # 尝试按换行或「型」拆分副标题
        if "\n" in meta.title:
            title_lines = [x.strip() for x in meta.title.split("\n") if x.strip()]
        else:
            title_lines = [meta.title]

    for tl in title_lines[:3]:
        tp = doc.add_paragraph()
        tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rt = tp.add_run(tl)
        _set_run_font(rt, east_asia=_FONT_HEADING_EA, ascii_font=_FONT_HEADING_ASCII, size_pt=22, bold=True)

    for _ in range(6):
        doc.add_paragraph("")

    if meta.organization:
        op = doc.add_paragraph()
        op.alignment = WD_ALIGN_PARAGRAPH.CENTER
        ro = op.add_run(meta.organization)
        _set_run_font(ro, size_pt=14)
    if meta.date:
        dp = doc.add_paragraph()
        dp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rd = dp.add_run(meta.date)
        _set_run_font(rd, size_pt=14)

    # 分页 → 签署页
    pb = doc.add_paragraph()
    _insert_page_break(pb)

    sp = doc.add_paragraph()
    sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rs = sp.add_run("签  署  页")
    _set_run_font(rs, east_asia=_FONT_HEADING_EA, size_pt=16, bold=True)

    doc.add_paragraph("")
    for tl in title_lines[:2]:
        tp2 = doc.add_paragraph()
        tp2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rt2 = tp2.add_run(tl)
        _set_run_font(rt2, size_pt=16, bold=True)

    for _ in range(4):
        doc.add_paragraph("")

    for label, val in (
        ("编写：", meta.author),
        ("审核：", meta.reviewer),
        ("会签：", meta.cosigner),
        ("批准：", meta.approver),
    ):
        lp = doc.add_paragraph()
        lp.paragraph_format.left_indent = Cm(3)
        _add_inline_markdown(lp, f"{label}{val or ''}")


# ---------------------------------------------------------------------------
# 表格
# ---------------------------------------------------------------------------


def _add_gfm_table(doc: Document, rows: List[List[str]]) -> None:
    if not rows:
        return
    col_count = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=col_count)
    table.style = "Table Grid"
    _set_table_borders(table)
    _set_table_width_pct(table, 5000)

    for ri, row_cells in enumerate(rows):
        for ci in range(col_count):
            text = row_cells[ci] if ci < len(row_cells) else ""
            cell = table.rows[ri].cells[ci]
            cell.text = ""
            cp = cell.paragraphs[0]
            cp.alignment = WD_ALIGN_PARAGRAPH.CENTER if ri == 0 else WD_ALIGN_PARAGRAPH.LEFT
            _add_inline_markdown(cp, text, body=True)
            for run in cp.runs:
                if ri == 0:
                    run.bold = True
            if ri == 0:
                _set_cell_shading(cell, _TABLE_HEADER_FILL)
            # 垂直居中
            tc = cell._tc
            tc_pr = tc.get_or_add_tcPr()
            v_align = OxmlElement("w:vAlign")
            v_align.set(qn("w:val"), "center")
            tc_pr.append(v_align)

# ---------------------------------------------------------------------------
# 正文渲染
# ---------------------------------------------------------------------------


class _DocxBuilder:
    def __init__(
        self,
        doc: Document,
        meta: SchemeMeta,
        *,
        heading_num_id: int = 1,
        appendix_num_id: int = 2,
        auto_cover: bool = True,
        auto_toc: bool = True,
        link_heading_numbering: bool = True,
    ) -> None:
        self.doc = doc
        self.meta = meta
        self.heading_num_id = heading_num_id
        self.appendix_num_id = appendix_num_id
        self.auto_cover = auto_cover
        self.auto_toc = auto_toc
        self.link_heading_numbering = link_heading_numbering
        self._toc_inserted = False
        self._in_body = False
        self._toc_entries: List[_TocEntry] = []
        self._toc_pointer = 0
        self.use_manual_heading_numbers = True

    def build(self, md_body: str) -> None:
        _configure_document_styles(self.doc)
        _enable_update_fields_on_open(self.doc)
        self._toc_entries = _extract_headings_for_toc(md_body)
        self._toc_pointer = 0

        if self.auto_cover:
            _build_cover_and_signature(self.doc, self.meta)
            # 分节：封面区 → 目录区
            sec_p = self.doc.add_paragraph()
            _insert_section_break_next_page(sec_p)

        # 目录节：预扫描标题生成可见目录行（含 PAGEREF 页码域）
        if self.auto_toc and self._toc_entries:
            _add_toc_section(self.doc, self._toc_entries)
            self._toc_inserted = True
            pb = self.doc.add_paragraph()
            _insert_page_break(pb)

        # 分节：正文（页码从 1 开始，启用页眉页脚）
        body_sec_p = self.doc.add_paragraph()
        _insert_section_break_next_page(body_sec_p)
        self._in_body = True

        lines = md_body.splitlines()
        for block in _iter_markdown_blocks(lines):
            self._render_block(block)

        # 应用分节页眉页脚
        self._finalize_sections()

    def _render_block(self, block: _Block) -> None:
        doc = self.doc
        if block.kind == "blank":
            # Markdown 空行仅用于分隔块，正文不再额外插入空段（避免「一行变两行」）
            if not self._in_body:
                doc.add_paragraph("")
            return
        elif block.kind == "pagebreak":
            p = doc.add_paragraph()
            _insert_page_break(p)
        elif block.kind == "toc" and not self._toc_inserted:
            if self._toc_entries:
                _add_toc_section(doc, self._toc_entries)
            self._toc_inserted = True
        elif block.kind == "heading":
            md_level, raw_text, is_appendix = block.payload
            display = _strip_heading_number(raw_text)
            word_level = _heading_word_level(md_level)
            num_label = ""
            if self._toc_pointer < len(self._toc_entries):
                _, toc_plain, bm_name, num_label = self._toc_entries[self._toc_pointer]
                if toc_plain:
                    display = toc_plain
            heading_text = (
                _format_numbered_heading(num_label, display)
                if self.use_manual_heading_numbers and num_label
                else display
            )
            p = doc.add_heading(heading_text, level=word_level)
            for run in p.runs:
                _set_run_font(
                    run,
                    east_asia=_FONT_HEADING_EA,
                    ascii_font=_FONT_HEADING_ASCII,
                    size_pt={1: 16, 2: 14, 3: 13, 4: 12, 5: 12, 6: 12}.get(word_level, 12),
                    bold=True,
                    color=_COLOR_HEADING,
                )
            _set_heading_flush_left(p)
            _set_outline_level(p, word_level)
            if self._toc_pointer < len(self._toc_entries):
                _, _, bm_name, _ = self._toc_entries[self._toc_pointer]
                _add_paragraph_bookmark(p, bm_name, self._toc_pointer)
                self._toc_pointer += 1
            if self.link_heading_numbering and not (
                self.use_manual_heading_numbers and num_label
            ):
                num_id = self.appendix_num_id if is_appendix else self.heading_num_id
                ilvl = 0 if is_appendix else max(0, word_level - 1)
                _add_num_pr(p, num_id, ilvl)
                _set_heading_flush_left(p)
        elif block.kind == "table":
            _add_gfm_table(doc, block.payload)
        elif block.kind == "ul":
            for item in block.payload:
                p = doc.add_paragraph(style="List Bullet")
                _add_inline_markdown(p, item)
        elif block.kind == "ol":
            for item in block.payload:
                p = doc.add_paragraph(style="List Number")
                _add_inline_markdown(p, item)
        elif block.kind == "image":
            alt, src = block.payload
            p = doc.add_paragraph()
            _add_inline_markdown(p, f"[图片: {alt or src}]", body=True)
        elif block.kind == "para":
            p = doc.add_paragraph()
            _add_inline_markdown(p, block.payload)
            _set_paragraph_format(p, line_spacing_15=True, first_line_indent_cm=0.75)

    def _finalize_sections(self) -> None:
        sections = self.doc.sections
        if not sections:
            return
        # 第一节：封面 — 无页眉页脚
        _set_section_different_first_page(sections[0], True)
        _apply_header_footer(sections[0], doc_id=self.meta.doc_id, header_title=self.meta.header_title, show=False)

        if len(sections) >= 2:
            # 目录节
            _apply_header_footer(sections[1], doc_id=self.meta.doc_id, header_title=self.meta.header_title, show=False)

        if len(sections) >= 3:
            body_sec = sections[2]
            _set_section_page_number_start(body_sec, 1)
            _apply_header_footer(
                body_sec,
                doc_id=self.meta.doc_id,
                header_title=self.meta.header_title,
                show=True,
            )
        elif len(sections) == 1:
            _set_section_page_number_start(sections[0], 1)
            _apply_header_footer(
                sections[0],
                doc_id=self.meta.doc_id,
                header_title=self.meta.header_title,
                show=True,
            )


# ---------------------------------------------------------------------------
# 方案 A：纯 Python 构建
# ---------------------------------------------------------------------------


def _build_docx_programmatic(md_content: str) -> bytes:
    meta, body = _split_front_matter(md_content)
    # 若 Markdown 以 # 标题开头且无 YAML，用首行 H1 作标题
    if meta.title == "试验方案":
        m = re.match(r"^#\s+(.+)$", body.strip(), re.MULTILINE)
        if m:
            meta.title = m.group(1).strip()
            meta.header_title = meta.title

    doc = Document()
    # 页面边距（A4 常用）
    for sec in doc.sections:
        sec.top_margin = Cm(2.54)
        sec.bottom_margin = Cm(2.54)
        sec.left_margin = Cm(3.17)
        sec.right_margin = Cm(3.17)

    heading_num_id, appendix_num_id = _ensure_numbering_definitions(doc)
    builder = _DocxBuilder(
        doc,
        meta,
        heading_num_id=heading_num_id,
        appendix_num_id=appendix_num_id,
        auto_cover=True,
        auto_toc=True,
        link_heading_numbering=False,
    )
    builder.use_manual_heading_numbers = True
    builder.build(body)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 方案 B：基于模板 .docx / .dotx
# ---------------------------------------------------------------------------


def _clear_document_body(doc: Document) -> None:
    """删除正文段落与表格，保留 sectPr（节属性）。"""
    body = doc.element.body
    for child in list(body):
        tag = child.tag.split("}")[-1]
        if tag == "sectPr":
            continue
        body.remove(child)


def _build_docx_from_template(md_content: str, template_path: str) -> bytes:
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Word 模板不存在: {template_path}")

    meta, body = _split_front_matter(md_content)
    doc = Document(template_path)
    _clear_document_body(doc)

    # 模板已含样式/编号时不再重写 numbering
    builder = _DocxBuilder(
        doc,
        meta,
        heading_num_id=1,
        appendix_num_id=2,
        auto_cover=True,
        auto_toc=True,
        link_heading_numbering=False,  # 沿用模板 Heading 样式内建编号
    )
    builder.use_manual_heading_numbers = True
    builder.build(body)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def markdown_to_docx_bytes(content: str) -> bytes:
    """
    将 Markdown 试验方案转为 Word 文档字节流。

    :param content: 含可选 YAML Front Matter 的 Markdown 全文
    :returns: .docx 文件二进制
    :raises ValueError: 输入为空
    :raises FileNotFoundError: 指定模板路径不存在
    """
    if content is None:
        raise ValueError("Markdown 内容不能为 None")
    text = str(content)
    if not text.strip():
        raise ValueError("Markdown 内容为空，无法生成 Word")

    template = (
        os.environ.get("SCHEME_DOCX_TEMPLATE", "").strip()
        or os.environ.get("SCHEME_DOCX_TEMPLATE_PATH", "").strip()
    )
    if template:
        return _build_docx_from_template(text, template)
    return _build_docx_programmatic(text)


def markdown_to_docx_bytes_programmatic(content: str) -> bytes:
    """强制使用纯 Python 构建（忽略模板环境变量）。"""
    if not str(content or "").strip():
        raise ValueError("Markdown 内容为空")
    return _build_docx_programmatic(str(content))


def markdown_to_docx_bytes_from_template(content: str, template_path: str) -> bytes:
    """强制使用指定模板路径构建。"""
    return _build_docx_from_template(str(content), template_path)


def markdown_to_docx_stream(content: str) -> BinaryIO:
    buf = io.BytesIO(markdown_to_docx_bytes(content))
    buf.seek(0)
    return buf
