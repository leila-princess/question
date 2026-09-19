"""自选章节提取：全文保留 + 标题重编号 + offset 连续 span。"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.FLGL.method_section_enrich import enrich_section_method


def test_section_span_fetch_includes_paren_items():
    from blueprints.FLGL.method_routes import _section_html_from_parse_cache

    text = "2.1 某试验\n\n说明段落。\n\n（1）步骤一\n内容A\n\n（2）步骤二\n内容B\n\n2.2 下一试验\n其他"
    sec = {
        "section_id": "S001",
        "section_no": "2.1",
        "title": "2.1 某试验",
        "level": 2,
        "start_offset": 0,
        "end_offset": text.index("2.2"),
        "text": "说明段落。",
    }
    parse_cache = {
        "sections": [sec, {
            "section_id": "S002",
            "section_no": "2.2",
            "title": "2.2 下一试验",
            "level": 2,
            "start_offset": text.index("2.2"),
            "end_offset": len(text),
            "text": "其他",
        }],
        "node_slices": {"S001": "<p>2.1 某试验</p>"},
    }
    path_out = []
    html = _section_html_from_parse_cache(
        parse_cache,
        section_id="S001",
        resolved=sec,
        full_text=text,
        path_out=path_out,
    )
    assert path_out == ["offset_span"]
    plain = re.sub(r"<[^>]+>", "\n", html)
    assert "（1）" in plain or "步骤一" in plain
    assert "内容B" in plain
    assert "下一试验" not in plain


def test_offset_span_not_blocked_by_short_title_guard():
    from blueprints.FLGL.method_routes import _section_html_from_parse_cache

    text = "短标题\n\n正文一段。\n\na) 子项内容\n\n下一节"
    sec = {
        "section_id": "S001",
        "title": "短标题",
        "level": 2,
        "start_offset": 0,
        "end_offset": text.index("下一节"),
        "text": "正文一段。",
    }
    parse_cache = {
        "sections": [sec],
        "node_slices": {"S001": "<p>短标题</p>"},
    }
    html = _section_html_from_parse_cache(
        parse_cache, section_id="S001", resolved=sec, full_text=text,
    )
    plain = re.sub(r"<[^>]+>", "\n", html)
    assert "子项内容" in plain
    assert "正文一段" in plain


def test_fetch_trace_uses_offset_span():
    from blueprints.FLGL.method_routes import _fetch_one_explicit_section_content

    text = "2.1 试验\n\n（1）步骤\n内容\n\n2.2 其他"
    sec = {
        "section_id": "S001",
        "section_no": "2.1",
        "title": "2.1 试验",
        "level": 2,
        "start_offset": 0,
        "end_offset": text.index("2.2"),
        "text": "短",
    }
    parse_cache = {
        "sections": [sec],
        "node_slices_md": {
            "S001": "## 2.1 试验\n\n（1）步骤\n内容",
        },
        "node_slices": {"S001": "<p>2.1 试验</p>"},
        "parsed_document": {"raw_full_text": text},
    }
    html, trace = _fetch_one_explicit_section_content(
        {"sectionNo": "2.1", "title": "2.1 试验", "section_id": "S001"},
        parse_cache=parse_cache,
        category="",
        doc_id="",
        temp_path="",
        ext=".docx",
        all_tables=[],
        all_figures=[],
        tmp_dir="",
        pages=None,
        full_text=text,
    )
    assert trace["fetch_path"] == "node_slices_md"
    assert "步骤" in html
    assert trace["chunk_len"] >= trace["expected_span_len"]


def test_build_sections_with_blocks_preserves_offset():
    from blueprints.ZSGL.WJGL1.document_parse_service import _build_sections_with_blocks

    sections_raw = [{
        "section_id": "S001",
        "title": "2.1 某试验",
        "level": 2,
        "start_page": 1,
        "end_page": 1,
        "text": "正文",
        "start_offset": 100,
        "end_offset": 500,
        "subsections": [{"title": "（1）步骤"}],
    }]
    built = _build_sections_with_blocks(sections_raw, {}, [], attach_page_tables=False)
    assert built[0]["start_offset"] == 100
    assert built[0]["end_offset"] == 500
    assert built[0]["subsections"]


def test_section_extract_mode_keeps_full_body_and_renumbers():
    raw = """## 3.1 高温试验

环境温度应满足要求。

## 3.2 试验过程

（1）升温至规定温度。

## 3.3 结果评定

按规范评定。"""
    out = enrich_section_method(
        raw,
        "高温试验-试验方法",
        section_extract_mode=True,
    )
    synr = str(out.get("formatted_single") or "")
    assert "环境温度" in synr
    assert "升温至规定温度" in synr
    assert "按规范评定" in synr
    assert "（一）" not in synr
    blocks = out.get("blocks") or {}
    assert not str((blocks.get("sytj") or {}).get("text") or "").strip()
    assert out.get("extract_source") == "sections_selected"


def test_filter_items_covered_by_ancestor():
    from blueprints.FLGL.method_routes import _filter_items_covered_by_ancestor

    parse_cache = {
        "sections": [
            {
                "section_id": "S001",
                "section_no": "2.1",
                "title": "某试验",
                "level": 2,
                "start_offset": 0,
                "end_offset": 200,
                "text": "parent section body with enough content for filter test",
            },
            {
                "section_id": "S002",
                "section_no": "",
                "title": "（1）步骤",
                "level": 3,
                "start_offset": 50,
                "end_offset": 100,
                "text": "child",
            },
        ],
    }
    items = [
        {"sectionNo": "2.1", "title": "某试验", "section_id": "S001"},
        {"sectionNo": "", "title": "（1）步骤", "section_id": "S002"},
    ]
    kept = _filter_items_covered_by_ancestor(items, parse_cache, full_text="x" * 300)
    assert len(kept) == 1
    assert kept[0]["section_id"] == "S001"


def test_scan_nested_headings_paren_letter():
    from blueprints.ZSGL.WJGL1.document_structure import scan_nested_headings

    body = "说明\n\na) 第一项\n内容A\n\nb) 第二项\n内容B"
    subs = scan_nested_headings(body, parent_level=2)
    kinds = [s.get("kind") for s in subs]
    assert "paren_letter" in kinds


def test_build_node_slices_uses_markdown_slice():
    from blueprints.ZSGL.WJGL1.document_parse_service import _build_node_slices

    md = "## 2.1 标题\n\n（1）步骤\n长内容\n"
    sections = [{
        "section_id": "S001",
        "section_no": "2.1",
        "title": "2.1 标题",
        "text": "短",
        "html": "<p>2.1 标题</p>",
        "start_offset": 0,
        "end_offset": 100,
    }]
    slices = _build_node_slices(sections, full_markdown=md)
    plain = re.sub(r"<[^>]+>", "\n", slices["S001"])
    assert "长内容" in plain
    assert "（1）" in plain or "步骤" in plain


def test_word_numbering_resolver_lower_letter():
    from blueprints.ZSGL.WJGL1.word_native_parse import _format_number_value

    assert _format_number_value("lowerLetter", 1) == "a"
    assert _format_number_value("lowerLetter", 2) == "b"


def test_build_pipeline_report_html():
    import tempfile
    from blueprints.FLGL.section_extract_debug import (
        build_pipeline_report_html,
        save_section_extract_debug_bundle,
    )

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["FLGL_SECTION_EXTRACT_DEBUG"] = "1"
        session = "test-session-pipeline"
        root = os.path.join(
            os.path.dirname(__file__), "..", "output_json", "flgl_extract_debug", session,
        )
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "04_sections.json"), "w", encoding="utf-8") as f:
            f.write('[{"section_id":"S001","text":"abc","start_offset":0,"end_offset":10}]')

        save_section_extract_debug_bundle(
            session,
            group={"parent": {"title": "t"}},
            fetch_traces=[{
                "section_id": "S001",
                "title": "t",
                "fetch_path": "offset_span",
                "expected_span": "x" * 100,
                "expected_span_len": 100,
                "chunk_len": 95,
            }],
            merged_content="merged",
            formatted_synr="formatted",
        )
        report = build_pipeline_report_html(session)
        assert os.path.isfile(report)
        with open(report, encoding="utf-8") as f:
            html = f.read()
        assert "offset_span" in html or "章节提取" in html


def test_guided_anchor_prefers_specific_section_no():
    from blueprints.FLGL.guided_method_extract import _resolve_sections_for_anchors, _candidate_to_group

    sections = [
        {"section_id": "S035", "section_no": "8", "title": "8 试验内容", "level": 1},
        {"section_id": "S049", "section_no": "8.2.3", "title": "8.2.3 试验方法", "level": 3, "text": "x" * 200},
    ]
    resolved = _resolve_sections_for_anchors(
        sections,
        ["S035"],
        ["8.2.3"],
        hint="低温低气压",
    )
    assert len(resolved) == 1
    assert resolved[0]["section_id"] == "S049"

    group = _candidate_to_group({
        "anchor_section_ids": ["S035"],
        "anchor_section_nos": ["8.2.3"],
        "project_name": "温度与高度试验",
        "method_short_name": "低温低气压工作测试",
    }, sections)
    assert group["parent"]["section_id"] == "S049"


def test_decimal_section_span_reaches_next_sibling():
    from blueprints.FLGL.method_routes import _compute_section_span_end_offset

    sections = [
        {"section_id": "S049", "section_no": "8.2.3", "title": "8.2.3 试验方法", "level": 3, "start_offset": 100},
        {"section_id": "S050", "section_no": "3", "title": "3 高温", "level": 3, "start_offset": 200},
        {"section_id": "S055", "section_no": "8.2.4", "title": "8.2.4 数据处理", "level": 3, "start_offset": 500},
    ]
    end = _compute_section_span_end_offset(sections[0], sections, full_text_len=1000)
    assert end == 500


def test_top_level_chapter_span_skips_duplicate_section_no():
    from blueprints.FLGL.method_routes import _compute_section_span_end_offset

    sections = [
        {"section_id": "S035", "section_no": "8", "title": "8 章", "level": 1, "start_offset": 100},
        {"section_id": "S038", "section_no": "8", "title": "8 章重复", "level": 1, "start_offset": 200},
        {"section_id": "S148", "section_no": "9", "title": "9 下一章", "level": 1, "start_offset": 900},
    ]
    end = _compute_section_span_end_offset(sections[0], sections, full_text_len=1000)
    assert end == 900


def test_span_start_ignores_section_no_in_body_text():
    from blueprints.FLGL.method_routes import _span_start_for_decimal_section

    sections = [
        {
            "section_id": "S133",
            "section_no": "",
            "title": "2）传感器标定:",
            "start_offset": 9467,
            "text": "正文提及 8.5.3 最大水平飞行速度试验 字样",
        },
        {
            "section_id": "S140",
            "section_no": "8.5.3",
            "title": "8.5.3 最大水平飞行速度试验",
            "start_offset": 10276,
            "text": "正式方法正文",
        },
    ]
    start = _span_start_for_decimal_section(sections[1], sections)
    assert start == 10276


def test_table_fragment_candidate_maps_to_body_keyword_span():
    from blueprints.FLGL.guided_method_extract import (
        _find_paren_list_item_span,
        _remap_table_fragment_candidate,
    )

    full_text = (
        "7.1 设备\n表5参数\n"
        "8.2.3 试验方法\n"
        "（1）低温试验（非工作状态）\n降温至-20℃;\n保温6小时。\n"
        "（2）低温低气压工作测试\n降压至41kPa;\n"
    )
    sections = [
        {"section_id": "S026", "section_no": "", "title": "| 低温工作试验 | -30℃ | 2h", "start_offset": 10},
        {"section_id": "S035", "section_no": "8", "title": "8 试验内容", "start_offset": 30},
    ]
    cand = {
        "candidate_id": "C001",
        "method_short_name": "低温工作试验",
        "memory_excerpt": "| 低温工作试验 | -30℃ | 2h",
        "anchor_section_ids": ["S026"],
        "anchor_section_nos": ["7.1"],
    }
    remapped = _remap_table_fragment_candidate(cand, sections, full_text)
    span = remapped.get("_keyword_span")
    assert span
    chunk = full_text[span["start"]:span["end"]]
    assert "低温试验（非工作状态）" in chunk
    assert "保温6小时" in chunk
    assert "低温低气压" not in chunk


def test_resolve_sections_skips_table_fragment_and_wrong_chapter_no():
    from blueprints.FLGL.guided_method_extract import _resolve_sections_for_anchors

    sections = [
        {"section_id": "S021", "section_no": "7.1", "title": "7.1 设备", "level": 2},
        {"section_id": "S026", "section_no": "", "title": "| 低温工作试验 | -30℃ | 2h", "level": 1},
        {"section_id": "S049", "section_no": "8.2.3", "title": "8.2.3 试验方法", "level": 3},
    ]
    cand = {
        "method_short_name": "低温工作试验",
        "memory_excerpt": "| 低温工作试验 | -30℃ | 2h",
        "anchor_section_ids": ["S026"],
        "anchor_section_nos": ["7.1"],
    }
    resolved = _resolve_sections_for_anchors(
        sections, ["S026"], ["7.1"], hint="低温", candidate=cand,
    )
    assert resolved == []


def test_dedupe_methods_by_project_prefers_full_project():
    from blueprints.FLGL.guided_method_extract import _dedupe_methods_by_project

    methods = [
        {
            "project_name": "温度和高度试验",
            "method_short_name": "低温低气压",
            "method_scope": "single_method",
            "boundary_start": {"section_nos": ["8.2.3"]},
            "anchor_section_nos": ["8.2.3"],
            "confidence": 0.95,
        },
        {
            "project_name": "温度和高度试验",
            "method_short_name": "",
            "method_scope": "full_project",
            "boundary_start": {"section_nos": ["8.2"]},
            "confidence": 0.9,
        },
    ]
    out = _dedupe_methods_by_project(methods)
    assert len(out) == 2


def test_dedupe_same_anchor_keeps_higher_confidence():
    from blueprints.FLGL.guided_method_extract import _dedupe_methods_by_project

    methods = [
        {
            "project_name": "砂尘试验",
            "boundary_start": {"section_nos": ["8.4"]},
            "confidence": 0.7,
        },
        {
            "project_name": "砂尘试验",
            "boundary_start": {"section_nos": ["8.4"]},
            "confidence": 0.95,
            "reasoning": "更完整",
        },
    ]
    out = _dedupe_methods_by_project(methods)
    assert len(out) == 1
    assert out[0].get("confidence") == 0.95


def test_dedupe_keeps_distinct_anchors_same_project_name():
    from blueprints.FLGL.guided_method_extract import _dedupe_methods_by_project

    methods = [
        {
            "project_name": "试验项目A",
            "boundary_start": {"section_nos": ["4.1"]},
            "confidence": 0.9,
        },
        {
            "project_name": "试验项目A",
            "boundary_start": {"section_nos": ["4.2"]},
            "confidence": 0.88,
        },
    ]
    out = _dedupe_methods_by_project(methods)
    assert len(out) == 2


def test_dedupe_keeps_indicator_methods_with_same_project_name():
    from blueprints.FLGL.guided_method_extract import _dedupe_methods_by_project

    methods = [
        {
            "project_name": "边界性能试验",
            "method_short_name": "最大起飞重量试验",
            "method_scope": "sub_project",
            "boundary_start": {"section_nos": ["8.5.1"]},
            "confidence": 0.93,
        },
        {
            "project_name": "边界性能试验",
            "method_short_name": "最大旋转角速度试验",
            "method_scope": "sub_project",
            "boundary_start": {"section_nos": ["8.5.2"]},
            "confidence": 0.94,
        },
        {
            "project_name": "边界性能试验",
            "method_short_name": "最大水平飞行速度试验",
            "method_scope": "sub_project",
            "boundary_start": {"section_nos": ["8.5.3"]},
            "confidence": 0.93,
        },
    ]
    out = _dedupe_methods_by_project(methods)
    assert len(out) == 3


def test_normalize_candidate_filters_chapter_root_from_included_ids():
    from blueprints.FLGL.guided_method_extract import _normalize_candidate

    sections = [
        {"section_id": "S035", "section_no": "8", "title": "8 章", "start_offset": 3294},
        {"section_id": "S085", "section_no": "8.4", "title": "8.4 砂尘试验", "start_offset": 6121},
        {"section_id": "S086", "section_no": "8.4.1", "title": "8.4.1 试验目的", "start_offset": 6130},
    ]
    raw = {
        "project_name": "砂尘试验",
        "method_scope": "full_project",
        "boundary_start": {"section_nos": ["8.4"]},
        "boundary_end": {"section_nos": ["8.5"]},
        "included_section_ids": ["S035", "S085", "S086"],
        "anchor_section_ids": ["S035"],
        "anchor_section_nos": ["8.4"],
    }
    cand = _normalize_candidate(raw, 3, sections=sections)
    assert "S035" not in cand["included_section_ids"]
    assert "S085" in cand["included_section_ids"]
    assert cand["anchor_section_ids"] == ["S085"]


def test_resolve_boundary_span_8_4_excludes_chapter_eight():
    from blueprints.FLGL.guided_method_extract import _resolve_method_boundary_span

    full_text = (
        "8 章总述\n"
        "8.2 温度和高度\n8.2.1 目的\n"
        "8.3 防水性\n8.3.1 目的\n"
        "8.4 砂尘试验\n8.4.1 试验目的\n砂尘正文。\n"
        "8.5 边界性能\n8.5.1 最大起飞重量\n"
    )
    sections = [
        {"section_id": "S035", "section_no": "8", "title": "8 章", "start_offset": full_text.find("8 章")},
        {"section_id": "S085", "section_no": "8.4", "title": "8.4 砂尘试验",
         "start_offset": full_text.find("8.4 砂尘"), "level": 2},
        {"section_id": "S111", "section_no": "8.5", "title": "8.5 边界性能",
         "start_offset": full_text.find("8.5 边界"), "level": 2},
    ]
    cand = {
        "project_name": "砂尘试验",
        "method_scope": "full_project",
        "boundary_start": {"section_nos": ["8.4"]},
        "boundary_end": {"section_nos": ["8.5"]},
        "included_section_ids": ["S035", "S085"],
        "anchor_section_nos": ["8.4"],
    }
    span = _resolve_method_boundary_span(cand, sections, full_text)
    assert span
    chunk = full_text[span["start"]:span["end"]]
    assert span["start"] == full_text.find("8.4 砂尘")
    assert "8.2 温度" not in chunk
    assert "砂尘正文" in chunk
    assert "8.5 边界" not in chunk


def test_indicator_boundary_span_uses_first_heading_in_full_text():
    from blueprints.FLGL.guided_method_extract import _resolve_indicator_boundary_span

    full_text = (
        "8.5 边界性能考核\n"
        "8.5.1 最大起飞重量试验\n"
        "8.5.1.1 试验目的\n"
        "表11内容\n"
        "（1）阶段1:静态载荷\n"
        "阶段1正文。\n"
        "（2）阶段2:动态离地\n"
        "表12内容\n"
        "（3）阶段3:带载起降\n"
        "阶段3正文。\n"
        "8.5.2 最大旋转角速度试验\n"
        "8.5.2.1 试验条件\n"
        "表13内容\n"
        "1）设备安装\n"
        "光学校准正文。\n"
        "2）传感器标定\n"
        "标定正文。\n"
        "8.5.3 最大水平飞行速度试验\n"
        "8.5.3正文。\n"
    )
    sections = [
        {"section_id": "S121", "section_no": "8.5.1", "title": "8.5.1 最大起飞重量试验",
         "start_offset": full_text.find("（2）阶段2")},
        {"section_id": "S127", "section_no": "8.5.2", "title": "8.5.2 最大旋转角速度试验",
         "start_offset": full_text.find("2）传感器")},
        {"section_id": "S135", "section_no": "8.5.3", "title": "8.5.3 最大水平飞行速度试验",
         "start_offset": full_text.find("8.5.3")},
    ]
    cand851 = {
        "method_scope": "sub_project",
        "display_name": "最大起飞重量试验",
        "boundary_start": {"section_nos": ["8.5.1"], "title_hint": "8.5.1 最大起飞重量试验"},
        "boundary_end": {"section_nos": ["8.5.2"], "title_hint": "8.5.2 最大旋转角速度试验"},
        "anchor_section_nos": ["8.5.1"],
    }
    span851 = _resolve_indicator_boundary_span(cand851, sections, full_text, hint="最大起飞")
    assert span851
    chunk851 = full_text[span851["start"]:span851["end"]]
    assert chunk851.startswith("8.5.1")
    assert "阶段1" in chunk851
    assert "表12" in chunk851
    assert "8.5.2" not in chunk851

    cand852 = {
        "method_scope": "sub_project",
        "display_name": "最大旋转角速度试验",
        "boundary_start": {"section_nos": ["8.5.2"], "title_hint": "8.5.2 最大旋转角速度试验"},
        "boundary_end": {"section_nos": ["8.5.3"], "title_hint": "8.5.3 最大水平飞行速度试验"},
        "anchor_section_nos": ["8.5.2"],
    }
    span852 = _resolve_indicator_boundary_span(cand852, sections, full_text, hint="旋转")
    assert span852
    chunk852 = full_text[span852["start"]:span852["end"]]
    assert chunk852.startswith("8.5.2")
    assert "表13" in chunk852
    assert "光学校准" in chunk852
    assert "2）传感器" in chunk852
    assert "8.5.3" not in chunk852


def test_plain_text_to_method_html_detects_decimal_headings():
    from blueprints.FLGL.method_section_enrich import _plain_text_to_method_html

    plain = (
        "8.4 砂尘试验\n"
        "8.4.1 试验目的\n"
        "考核密封性。\n"
        "（1）试验设备\n"
        "设备列表。\n"
    )
    html = _plain_text_to_method_html(plain)
    assert 'data-section-no="8.4"' in html or "8.4 砂尘" in html
    assert "试验目的" in html
    assert "（1）" in html or "试验设备" in html


def test_plain_text_to_method_html_expands_br_and_table_marker():
    from blueprints.FLGL.method_section_enrich import _plain_text_to_method_html

    plain = (
        "8.3.1 试验目的<br>"
        "（1）试验设备与仪器<br>"
        "1）淋雨试验箱:需满足以下条件;<br>喷水角度:±60°可调摆管;<br>"
        "表 6 喷淋试验参数<br>【表格】<br>"
        "序号参数参数值<br>"
        "水压100kPa<br>"
        "喷水量10L/min"
    )
    html = _plain_text_to_method_html(plain)
    assert "wjgl-section-heading" in html
    assert "8.3.1" in html
    assert "<table" in html
    assert "水压" in html
    assert "100kPa" in html
    assert "&lt;br&gt;" not in html


def test_format_selected_content_renumbers_headings():
    from blueprints.FLGL.method_section_enrich import format_selected_content_rule_based

    md = (
        "## 8.4 砂尘试验\n\n"
        "## 8.4.1 试验目的\n\n"
        "正文。\n"
    )
    out = format_selected_content_rule_based(md)
    assert "8.4 砂尘" not in out
    assert (
        "wjgl-section-heading" in out
        or re.search(r"^#+\s*1(?:\s|\.)", out, re.M)
        or re.search(r"^1(?:\s|\.)", out, re.M)
    )
    assert "（一）" not in out
    assert "正文" in out


def test_build_node_slices_md_from_markdown():
    from blueprints.ZSGL.WJGL1.document_parse_service import _build_node_slices_md

    md = (
        "## 8.4 砂尘试验\n\n"
        "概述。\n\n"
        "## 8.4.1 试验目的\n\n"
        "考核密封性。\n"
    )
    sections = [
        {"section_id": "S1", "section_no": "8.4", "title": "砂尘试验"},
        {"section_id": "S2", "section_no": "8.4.1", "title": "试验目的"},
    ]
    slices = _build_node_slices_md(sections, full_markdown=md)
    assert "S1" in slices
    assert "考核密封性" in slices.get("S2", "")
    assert "概述" in slices.get("S1", "")


def test_slice_markdown_parent_includes_children():
    from pdf_extract.mineru_wjgl_service import slice_markdown_by_sections

    md = (
        "### 8.2 温度和高度试验\n\n"
        "#### 8.2.1 试验目的\n\n"
        "目的正文。\n\n"
        "#### 8.2.3 试验方法\n\n"
        "低温试验步骤。\n\n"
        "### 8.3 防水性试验\n\n"
        "下一章。\n"
    )
    sections = [
        {"section_id": "S040", "section_no": "8.2", "title": "8.2 温度和高度试验", "level": 2},
        {"section_id": "S049", "section_no": "8.2.3", "title": "8.2.3 试验方法", "level": 3},
    ]
    slices = slice_markdown_by_sections(md, sections)
    parent = slices.get("S040", "")
    child = slices.get("S049", "")
    assert "低温试验步骤" in parent
    assert "8.2.1" in parent or "目的正文" in parent
    assert "低温试验步骤" in child
    assert "8.3 防水性" not in child


def test_fetch_parent_section_not_title_only():
    from blueprints.FLGL.method_routes import _fetch_one_explicit_section_content

    full_text = (
        "8.2 温度和高度试验\n"
        "8.2.1 试验目的\n目的正文。\n"
        "8.2.3 试验方法\n低温试验步骤。\n"
        "8.3 防水性试验\n"
    )
    sec = {
        "section_id": "S040",
        "section_no": "8.2",
        "title": "8.2 温度和高度试验",
        "level": 2,
        "start_offset": 0,
        "end_offset": full_text.index("8.3"),
        "text": "低温试验步骤。",
        "html": "<p>8.2 温度和高度试验</p><p>低温试验步骤。</p>",
    }
    parse_cache = {
        "sections": [sec],
        "node_slices_md": {"S040": "### 8.2 温度和高度试验"},
        "node_slices": {"S040": "<p>8.2 温度和高度试验</p><p>低温试验步骤。</p>"},
        "parsed_document": {"raw_full_text": full_text},
    }
    content, trace = _fetch_one_explicit_section_content(
        {"sectionNo": "8.2", "title": "8.2 温度和高度试验", "section_id": "S040"},
        parse_cache=parse_cache,
        category="",
        doc_id="",
        temp_path="",
        ext=".docx",
        all_tables=[],
        all_figures=[],
        tmp_dir="",
        pages=None,
        full_text=full_text,
    )
    assert "低温试验步骤" in content or "目的正文" in content
    assert trace.get("fetch_path") != "node_slices_md" or len(content) > 40


def test_filter_ancestor_keeps_children_when_parent_empty():
    from blueprints.FLGL.method_routes import (
        _filter_items_covered_by_ancestor,
        _merge_explicit_selected_section_content,
    )

    full_text = (
        "8.2 温度和高度试验\n"
        "8.2.3 试验方法\n低温试验步骤。\n"
        "8.3 防水性试验\n"
    )
    parent_sec = {
        "section_id": "S040",
        "section_no": "8.2",
        "title": "8.2 温度和高度试验",
        "level": 2,
        "start_offset": 0,
        "end_offset": full_text.index("8.3"),
        "html": "<p>8.2 温度和高度试验</p>",
    }
    child_sec = {
        "section_id": "S049",
        "section_no": "8.2.3",
        "title": "8.2.3 试验方法",
        "level": 3,
        "start_offset": full_text.index("8.2.3"),
        "end_offset": full_text.index("8.3"),
        "text": "低温试验步骤。",
        "html": "<p>8.2.3 试验方法</p><p>低温试验步骤。</p>",
    }
    parse_cache = {
        "sections": [parent_sec, child_sec],
        "node_slices_md": {
            "S040": "### 8.2 温度和高度试验",
            "S049": "#### 8.2.3 试验方法\n\n低温试验步骤。",
        },
        "node_slices": {
            "S040": "<p>8.2 温度和高度试验</p>",
            "S049": "<p>8.2.3 试验方法</p><p>低温试验步骤。</p>",
        },
        "parsed_document": {"raw_full_text": full_text},
    }
    group = {
        "parent": {
            "sectionNo": "8.2",
            "title": "温度和高度试验",
            "section_id": "S040",
        },
        "children": [
            {
                "sectionNo": "8.2.3",
                "title": "试验方法",
                "section_id": "S049",
            },
        ],
    }
    items = [
        {"sectionNo": "8.2", "title": "温度和高度试验", "section_id": "S040"},
        {"sectionNo": "8.2.3", "title": "试验方法", "section_id": "S049"},
    ]
    kept = _filter_items_covered_by_ancestor(items, parse_cache, full_text=full_text)
    kept_ids = {i.get("section_id") for i in kept}
    assert "S049" in kept_ids

    merged = _merge_explicit_selected_section_content(
        group,
        parse_cache=parse_cache,
        category="",
        doc_id="",
        temp_path="",
        ext=".docx",
        all_tables=[],
        all_figures=[],
        tmp_dir="",
        pages=None,
        full_text=full_text,
    )
    assert "低温试验步骤" in merged


def test_build_node_slices_skips_title_only_md():
    from blueprints.ZSGL.WJGL1.document_parse_service import _build_node_slices

    sections = [{
        "section_id": "S040",
        "section_no": "8.2",
        "title": "8.2 温度和高度试验",
        "html": "<p>8.2 温度和高度试验</p><p>低温试验步骤。</p>",
    }]
    slices = _build_node_slices(
        sections,
        node_slices_md={"S040": "### 8.2 温度和高度试验"},
    )
    plain = re.sub(r"<[^>]+>", "\n", slices.get("S040", ""))
    assert "低温试验步骤" in plain


def test_finalize_section_extract_text_renumbers_letter_items():
    from blueprints.FLGL.method_section_enrich import finalize_section_extract_markdown

    plain = (
        "## 8.4.1 试验步骤\n"
        "a) 第一步操作。\n"
        "c) 第三步操作。\n"
        "d) 第四步操作。\n"
    )
    out = finalize_section_extract_markdown(plain)
    assert "a) 第一步" in out
    assert "b) 第三步" in out
    assert "c) 第四步" in out
    assert "c) 第三步" not in out


def test_finalize_section_extract_markdown_preserves_body():
    from blueprints.FLGL.method_section_enrich import finalize_section_extract_markdown

    plain = "## 3.1 标题\n\n正文段落一。\n\n正文段落二。"
    out = finalize_section_extract_markdown(plain)
    assert "正文段落一" in out
    assert "正文段落二" in out


def test_word_table_to_gfm():
    from blueprints.ZSGL.WJGL1.word_native_parse import _table_to_gfm

    class _Cell:
        def __init__(self, text):
            self.text = text

    class _Row:
        def __init__(self, cells):
            self.cells = cells

    class _Table:
        def __init__(self, rows):
            self.rows = rows

    tbl = _Table([
        _Row([_Cell("序号"), _Cell("名称")]),
        _Row([_Cell("1"), _Cell("试验")]),
    ])
    gfm = _table_to_gfm(tbl)
    assert "| 序号 | 名称 |" in gfm
    assert "| 1 | 试验 |" in gfm


def test_resolve_method_boundary_span_project_block():
    from blueprints.FLGL.guided_method_extract import _resolve_method_boundary_span

    full_text = (
        "前言\n"
        "8.2 温度和高度试验\n"
        "8.2.1 试验目的\n目的正文。\n"
        "8.2.2 试验条件\n条件正文。\n"
        "8.2.3 试验方法\n（1）低温试验\n步骤A。\n"
        "8.2.4 试验数据处理\n处理。\n"
        "8.2.5 试验结果评判\n评定。\n"
        "8.3 防水性试验\n"
        "8.3.1 试验目的\n"
    )
    sections = [
        {"section_id": "S1", "section_no": "8.2", "title": "8.2 温度和高度试验",
         "start_offset": full_text.find("8.2 温度"), "level": 2},
        {"section_id": "S2", "section_no": "8.2.1", "title": "8.2.1 试验目的",
         "start_offset": full_text.find("8.2.1"), "level": 3},
        {"section_id": "S3", "section_no": "8.2.3", "title": "8.2.3 试验方法",
         "start_offset": full_text.find("8.2.3"), "level": 3},
        {"section_id": "S4", "section_no": "8.2.5", "title": "8.2.5 试验结果评判",
         "start_offset": full_text.find("8.2.5"), "level": 3},
        {"section_id": "S5", "section_no": "8.3", "title": "8.3 防水性试验",
         "start_offset": full_text.find("8.3 防水"), "level": 2},
    ]
    cand = {
        "project_name": "温度和高度试验",
        "method_scope": "full_project",
        "boundary_start": {"section_nos": ["8.2"], "text_anchor": "8.2.1 试验目的"},
        "boundary_end": {"section_nos": ["8.3"]},
        "anchor_section_nos": ["8.2"],
    }
    span = _resolve_method_boundary_span(cand, sections, full_text)
    assert span
    chunk = full_text[span["start"]:span["end"]]
    assert "8.2.1" in chunk or "试验目的" in chunk
    assert "8.2.5" in chunk or "评定" in chunk
    assert "8.3 防水性" not in chunk
    assert span["end"] - span["start"] > 80


def test_catalogue_for_methods_includes_hierarchy_and_non_8_sections():
    from blueprints.FLGL.guided_method_extract import _catalogue_for_methods

    sections = [
        {"section_id": "S1", "section_no": "4", "title": "4 试验项目1", "level": 1},
        {"section_id": "S2", "section_no": "4.1", "title": "4.1 方法甲", "level": 2},
        {"section_id": "S3", "section_no": "4.2", "title": "4.2 方法乙", "level": 2},
        {"section_id": "S4", "section_no": "", "title": "| 表格行 |", "level": 1},
    ]
    cat = _catalogue_for_methods(sections)
    nos = [c.get("section_no") for c in cat]
    assert "4" in nos
    assert "4.1" in nos
    assert "4.2" in nos
    assert not any(str(c.get("title") or "").startswith("|") for c in cat)
    item_4 = next(c for c in cat if c.get("section_no") == "4")
    assert "4.1" in (item_4.get("child_section_nos") or [])
    assert item_4.get("parent_section_no") == ""


def test_filter_granular_drops_table_but_keeps_depth3_indicator():
    from blueprints.FLGL.guided_method_extract import _filter_granular_method_candidates

    methods = [
        {
            "display_name": "（1）低温步骤",
            "memory_excerpt": "短",
        },
        {
            "display_name": "表格碎片",
            "memory_excerpt": "| 参数 | 值 |",
            "boundary_start": {"section_nos": ["7.1"]},
        },
        {
            "display_name": "最大起飞重量试验",
            "method_scope": "indicator_method",
            "boundary_start": {"section_nos": ["8.5.1"]},
            "anchor_section_nos": ["8.5.1"],
            "confidence": 0.9,
        },
    ]
    out = _filter_granular_method_candidates(methods)
    assert len(out) == 1
    assert out[0].get("boundary_start", {}).get("section_nos") == ["8.5.1"]


def test_list_methods_prompt_includes_structure_profile():
    import json

    from blueprints.FLGL.guided_prompts import GUIDED_LIST_METHODS_PROMPT

    profile = {"document_style": "trial_plan", "method_unit": "per_sub_item", "organization_mode": "dispersed"}
    prompt = GUIDED_LIST_METHODS_PROMPT.format(
        structure_profile_json=json.dumps(profile, ensure_ascii=False),
        organization_mode="dispersed",
        organization_reason="并列子方法",
        document_memory="记忆",
        document_structure_json="{}",
        heading_outline="目录",
        section_catalogue_json="[]",
    )
    assert "trial_plan" in prompt
    assert "per_sub_item" in prompt
    assert "reasoning" in prompt
    assert "semantic_slots" in prompt


def test_normalize_candidate_preserves_reasoning_fields():
    from blueprints.FLGL.guided_method_extract import _normalize_candidate

    raw = {
        "display_name": "方法甲",
        "reasoning": "4.1 为独立可执行方法",
        "granularity_rationale": "父章 4 下并列子方法",
        "semantic_slots": {"conditions": "4.1.1", "process": "4.1.2", "evaluation": "4.1.3"},
        "boundary_start": {"section_nos": ["4.1"]},
    }
    cand = _normalize_candidate(raw, 1, sections=[])
    assert cand["reasoning"] == "4.1 为独立可执行方法"
    assert cand["semantic_slots"]["process"] == "4.1.2"


def test_fetch_parent_uses_offset_when_md_truncated_not_title_only():
    """WJ_fgbz0024 类：node_slices_md 有（1）开头但仍远短于 offset 整段时应用 offset_span。"""
    from blueprints.FLGL.method_routes import _fetch_one_explicit_section_content

    full_text = (
        "8.2 温度和高度试验\n"
        "8.2.1 试验目的\n"
        "（1）设备与仪器配置\n"
        "1）试验箱:采用高低温低气压试验箱,需满足以下条件:\n"
        "温控范围:-40℃~70℃,精度±1℃;\n"
        "8.2.2 试验条件\n"
        "（1）功能与性能初检通电自检:启动无人机及地面站。\n"
        "8.2.3 试验方法\n"
        "（1）低温试验（非工作状态）\n"
        "降温:以≤1℃/min速率将试验箱温度降至-20℃;\n"
        "8.2.5 试验结果评判/评定\n"
        "（1）合格判据:功能正常。\n"
        "8.3 防水性试验\n"
    )
    end_82 = full_text.index("8.3")
    sec = {
        "section_id": "S040",
        "section_no": "8.2",
        "title": "8.2 温度和高度试验",
        "level": 2,
        "start_offset": 0,
        "end_offset": end_82,
        "text": "（1）设备与仪器配置",
        "html": "<p>8.2 温度和高度试验</p><p>（1）设备与仪器配置</p>",
    }
    child_secs = [
        {
            "section_id": "S041",
            "section_no": "8.2.1",
            "title": "8.2.1 试验目的",
            "level": 3,
            "start_offset": full_text.index("8.2.1"),
        },
        {
            "section_id": "S049",
            "section_no": "8.2.3",
            "title": "8.2.3 试验方法",
            "level": 3,
            "start_offset": full_text.index("8.2.3"),
        },
    ]
    parse_cache = {
        "sections": [sec] + child_secs,
        "node_slices_md": {
            "S040": (
                "### 8.2 温度和高度试验\n\n"
                "#### 8.2.1 试验目的\n\n"
                "（1）设备与仪器配置"
            ),
        },
        "node_slices": {"S040": "<p>8.2 温度和高度试验</p><p>（1）设备与仪器配置</p>"},
        "parsed_document": {"raw_full_text": full_text},
    }
    content, trace = _fetch_one_explicit_section_content(
        {"sectionNo": "8.2", "title": "8.2 温度和高度试验", "section_id": "S040"},
        parse_cache=parse_cache,
        category="",
        doc_id="",
        temp_path="",
        ext=".docx",
        all_tables=[],
        all_figures=[],
        tmp_dir="",
        pages=None,
        full_text=full_text,
    )
    assert "8.2.3" in content or "低温试验" in content
    assert "试验箱" in content or "高低温" in content
    assert trace.get("fetch_path") in {
        "offset_span",
        "offset_span_coverage",
        "node_slices_merge",
        "parse_cache_html",
    }
    assert trace["chunk_len"] >= trace["expected_span_len"] * 0.9
