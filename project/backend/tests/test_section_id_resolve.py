"""章节解析 section_id 与勾选提取映射。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.FLGL.method_routes import (
    _normalize_section_no,
    _normalize_section_title,
    _resolve_parse_cache_section,
)


def test_resolve_by_section_id():
    parse_cache = {
        "sections": [
            {"section_id": "S001", "section_no": "1", "title": "1 范围", "html": "<p>范围</p>"},
            {"section_id": "S005", "section_no": "3.1", "title": "3.1 试验条件", "html": "<p>条件正文</p>"},
        ]
    }
    sec = _resolve_parse_cache_section(
        parse_cache,
        section_no="3.1",
        title="试验条件",
        section_id="S005",
    )
    assert sec and sec["section_id"] == "S005"


def test_ignore_wrong_synthetic_section_id():
    parse_cache = {
        "sections": [
            {"section_id": "S001", "section_no": "1", "title": "1 范围", "html": "<p>ch1</p>"},
            {"section_id": "S005", "section_no": "3.1", "title": "3.1 试验条件", "html": "<p>cond</p>"},
        ]
    }
    # 前端 synthetic S003 不存在时应按 section_no 回落
    sec = _resolve_parse_cache_section(
        parse_cache,
        section_no="3.1",
        title="试验条件",
        section_id="S003",
    )
    assert sec and sec["section_id"] == "S005"


def test_normalize_title():
    assert _normalize_section_title("3.1 试验条件") == "试验条件"
    assert _normalize_section_no(".3.1") == "3.1"


def test_resolve_fragment_section_id_falls_back_to_best_span():
    """section_id 指向短碎片时，应按 section_no 择优选完整切片。"""
    full_text = (
        "8.2 温度和高度试验\n"
        "8.2.1 试验目的\n目的正文。\n"
        "8.2.2 试验条件\n条件正文很长很长很长很长很长很长。\n"
        "8.3 防水性试验\n"
    )
    parse_cache = {
        "parsed_document": {"raw_full_text": full_text},
        "sections": [
            {
                "section_id": "S037",
                "section_no": "8.2",
                "title": "8.2.1 试验",
                "level": 3,
                "start_offset": full_text.find("8.2.1"),
                "text": "短碎片",
            },
            {
                "section_id": "S040",
                "section_no": "8.2",
                "title": "8.2 温度和高度试验",
                "level": 2,
                "start_offset": full_text.find("8.2 温度"),
                "text": "条件正文很长很长很长很长很长很长。",
            },
            {
                "section_id": "S050",
                "section_no": "8.3",
                "title": "8.3 防水性试验",
                "level": 2,
                "start_offset": full_text.find("8.3"),
                "text": "下一章",
            },
        ],
    }
    sec = _resolve_parse_cache_section(
        parse_cache,
        section_no="8.2",
        title="温度和高度试验",
        section_id="S037",
        full_text=full_text,
    )
    assert sec and sec["section_id"] == "S040"


def test_build_toc_skips_list_item_with_single_digit_section_no():
    from blueprints.ZSGL.WJGL1.document_parse_service import build_toc_and_headings_from_sections

    sections = [
        {"section_id": "S004", "section_no": "1", "title": "1 范围", "level": 1, "text": "正文"},
        {
            "section_id": "S032",
            "section_no": "1",
            "title": "1 试验温度:25℃;",
            "level": 3,
            "text": "列表项",
        },
        {"section_id": "S040", "section_no": "8.2", "title": "8.2 温度和高度试验", "level": 2, "text": "试验"},
    ]
    toc, flat = build_toc_and_headings_from_sections(sections)
    titles = [h.get("title") for h in flat]
    assert "试验温度" not in "".join(titles)
    assert any("范围" in t for t in titles)
    assert any("8.2" in str(h.get("sectionNo")) for h in flat)


def test_section_82_span_excludes_next_sibling_in_full_text():
    """8.2 正文应在 full_text 的 8.3 标题前截断，而非 parse 错位 offset。"""
    full_text = (
        "8.2 温度和高度试验\n"
        "8.2.1 试验目的\n目的。\n"
        "8.2.5 试验结果评判\n评定。\n"
        "8.3 防水性试验\n"
        "8.3.1 试验目的\n"
    )
    sections = [
        {
            "section_id": "S040",
            "section_no": "8.2",
            "title": "8.2 温度和高度试验",
            "level": 2,
            "start_offset": 0,
        },
        {
            "section_id": "S062",
            "section_no": "8.3",
            "title": "8.3 防水性试验",
            "level": 2,
            "start_offset": full_text.find("8.3 防水") + 50,
        },
    ]
    from blueprints.FLGL.method_routes import (
        _compute_section_span_end_offset,
        _span_start_for_decimal_section,
    )

    sec = sections[0]
    start = _span_start_for_decimal_section(sec, sections, full_text=full_text)
    end = _compute_section_span_end_offset(
        sec, sections, full_text_len=len(full_text), full_text=full_text,
    )
    chunk = full_text[start:end]
    assert "8.3 防水" not in chunk
    assert "8.2.5" in chunk or "评定" in chunk


def test_renumber_starts_at_one_not_zero():
    from blueprints.FLGL.method_section_enrich import renumber_document_sequential

    html = (
        '<p class="wjgl-section-heading" data-section-no="8.2.1">'
        "<strong>8.2.1 试验目的</strong></p><p>正文</p>"
        '<p class="wjgl-section-heading" data-section-no="8.2.2">'
        "<strong>8.2.2 试验条件</strong></p><p>条件</p>"
    )
    out, _ = renumber_document_sequential(html)
    assert "0.0.1" not in out
    assert "1 试验目的" in out or "data-section-no=\"1\"" in out


def test_span_end_prefers_full_text_over_early_parse_offset():
    """parse_cache 结束偏早时，应用 full_text 下一同级标题。"""
    full_text = (
        "8.5.1 最大起飞重量试验\n"
        "8.5.1.1 试验目的\n目的正文。\n"
        "8.5.1.3 试验方法\n"
        "试验步骤与操作如表12所示。\n"
        "表 12 步骤表\n"
        "步骤内容完整。\n"
        "8.5.2 下一试验\n"
    )
    sections = [
        {
            "section_id": "S121",
            "section_no": "8.5.1",
            "title": "8.5.1 最大起飞重量试验",
            "level": 3,
            "start_offset": full_text.find("8.5.1.3"),
        },
        {
            "section_id": "S127",
            "section_no": "8.5.2",
            "title": "8.5.2 下一试验",
            "level": 3,
            "start_offset": full_text.find("8.5.2") + 100,
        },
    ]
    from blueprints.FLGL.method_routes import (
        _compute_section_span_end_offset,
        _span_start_for_decimal_section,
    )

    sec = sections[0]
    start = _span_start_for_decimal_section(sec, sections, full_text=full_text)
    end = _compute_section_span_end_offset(
        sec, sections, full_text_len=len(full_text), full_text=full_text,
    )
    chunk = full_text[start:end]
    assert "表 12" in chunk or "步骤内容完整" in chunk
    assert "8.5.2" not in chunk


def test_trace_figure_refs_inline():
    from blueprints.FLGL.method_section_enrich import (
        apply_document_trace_to_html,
        enrich_section_method,
        resolve_see_references_html,
    )

    figure_html = (
        '<figure style="margin:12px 0;text-align:center;">'
        '<img src="data:image/png;base64,abc" alt="图3" style="max-width:100%;"/>'
        "</figure>"
    )
    full_text = "图3 试验装置示意图\n如图3所示进行连接。\n"
    html = (
        "<p>连接方式见图3所示。</p>"
        "<p>布局如图3布置。</p>"
        "<p>参考图3完成接线。</p>"
    )
    out = apply_document_trace_to_html(
        html,
        full_text=full_text,
        all_figures=[{"caption": "图3 试验装置", "html": figure_html}],
    )
    assert out.count("【溯源：图3】") >= 2
    assert figure_html in out
    assert 'alt="图3"' in out

    plain_out = resolve_see_references_html(
        "<p>步骤见图2。</p>",
        figures_by_no={"2": figure_html.replace("图3", "图2")},
    )
    assert "【溯源：图2】" in plain_out
    assert "图2" in plain_out

    enriched = enrich_section_method(
        "<p>试验步骤见图1。</p>",
        "某试验-试验方法",
        full_text="图1 示意图\n",
        all_figures=[{"caption": "图1", "html": figure_html.replace("图3", "图1")}],
        section_extract_mode=True,
    )
    synr = str(enriched.get("formatted_single") or "")
    assert "【溯源：图1】" in synr or "see-ref-inline" in synr
    assert "图1" in synr


def test_trace_same_table_and_same_context():
    from blueprints.FLGL.method_section_enrich import apply_document_trace_to_html

    full_text = (
        "8.2.3 试验方法\n"
        "降压模拟:以≤10 kPa/min速率降压至41kPa（7000m海拔等效）;\n"
        "8.2.4 试验数据处理\n"
        "（1）环境恢复以≤1℃/min速率升温/降温至25℃;\n"
    )
    table_html = "<table><tr><td>表 17</td><td>操作</td></tr></table>"
    html = (
        "<p>试验条件见8.2.3节。</p>"
        "<p>降压模拟:降压至41kPa（速率同前）;</p>"
        "<p>步骤见表17所示；另一处同表17。</p>"
    )
    out = apply_document_trace_to_html(
        html,
        full_text=full_text,
        all_tables=[{"html": table_html}],
    )
    assert "【溯源：见8.2.3】" in out or "【溯源：同8.2.3】" in out
    assert "【溯源：同前" in out
    assert "10 kPa/min" in out
    assert "【溯源：表17】" in out
    assert "【溯源：同表17】" in out


def test_trace_same_section_in_section_extract_markdown():
    from blueprints.FLGL.method_section_enrich import apply_section_extract_trace_markdown

    full_text = (
        "6.1.1 预处理\n"
        "按标准进行预处理。\n"
        "6.1.2 试验步骤\n"
        "（1）启动设备。\n"
    )
    md = "（1）启动设备。\n同6.1.1。\n"
    out = apply_section_extract_trace_markdown(md, full_text=full_text)
    assert "【溯源：同6.1.1】" in out or "see-ref-inline" in out
    assert "预处理" in out


def test_finalize_section_extract_renumbers_outline_from_one():
    from blueprints.FLGL.method_section_enrich import finalize_section_extract_markdown

    raw = "## 8.2.1 试验目的\n目的正文。\n## 8.2.2 试验条件\n（1）第一项\n（3）第三项\n"
    out = finalize_section_extract_markdown(raw)
    assert "1 试验目的" in out
    assert "2 试验条件" in out
    assert "8.2.1" not in out
    assert "（一）" not in out
    assert "（1）" in out and "（2）" in out


def test_section_extract_trace_skips_cross_section_inline():
    from blueprints.FLGL.method_section_enrich import apply_document_trace_to_html

    full_text = (
        "8.3.2 试验条件\n"
        "（1）通电自检:启动飞控系统。\n"
        "8.5.1.2 试验条件见8.3.2节。\n"
    )
    html = "<p>8.5.1.2 试验条件见8.3.2节。</p>"
    out = apply_document_trace_to_html(
        html, full_text=full_text, inline_section_refs=False,
    )
    assert "【溯源" not in out
    assert "通电自检" not in out


def test_trim_leading_before_formal_heading():
    from blueprints.FLGL.method_routes import _trim_leading_before_formal_heading

    text = (
        "3）RTK定位。\n"
        "（2）密封与结构分析\n"
        "噪声段落。\n"
        "8.5.1 最大起飞重量试验\n"
        "8.5.1.1 试验目的\n"
    )
    trimmed = _trim_leading_before_formal_heading(text, "8.5.1")
    assert "密封与结构" not in trimmed
    assert trimmed.startswith("8.5.1")


def test_build_toc_rejects_military_doc_table_noise():
    from blueprints.ZSGL.WJGL1.document_parse_service import build_toc_and_headings_from_sections

    sections = [
        {"section_id": "S001", "section_no": "1", "title": "1 任务依据", "level": 1,
         "text": "任务依据正文" * 20},
        {"section_id": "S002", "section_no": "1", "title": "1 抗同频异步干扰单次试验结评判", "level": 1, "text": "噪声"},
        {"section_id": "S003", "section_no": "1", "title": "1|冲淡|2|2~4|约1.5km/开机前|不小于10000", "level": 1, "text": "t"},
        {"section_id": "S004", "section_no": "1.2", "title": "1.2 编制依据", "level": 2, "text": "编制"},
        {"section_id": "S005", "section_no": "10", "title": "10 试验组织及任务分工", "level": 1,
         "text": "组织分工" * 30},
        {"section_id": "S006", "section_no": "10", "title": "10|边界底数试验|抗充气角反干扰能力|√|√|√/-- /", "level": 1, "text": "x"},
    ]
    _toc, flat = build_toc_and_headings_from_sections(sections)
    joined = "".join(h.get("title", "") for h in flat)
    assert len([h for h in flat if h.get("sectionNo") == "1"]) == 1
    assert "任务依据" in joined
    assert "冲淡" not in joined
    assert "抗同频" not in joined
    assert "边界底数" not in joined
    assert any(h.get("sectionNo") == "1.2" for h in flat)
    assert any(h.get("sectionNo") == "10" for h in flat)


def test_expand_peer_methods_for_boundary_section():
    full_text = (
        "8.5 边界性能考核\n"
        "8.5.1 最大起飞重量试验\n内容A\n"
        "8.5.2 最大旋转角速度试验\n内容B\n"
        "8.5.3 最大水平飞行速度试验\n内容C\n"
        "9 试验判定\n"
    )
    parse_cache = {
        "parsed_document": {"raw_full_text": full_text},
        "sections": [
            {"section_id": "S120", "section_no": "8.5", "title": "8.5 边界性能考核",
             "level": 2, "start_offset": 0},
            {"section_id": "S121", "section_no": "8.5.1", "title": "8.5.1 最大起飞重量试验",
             "level": 3, "start_offset": full_text.find("8.5.1")},
            {"section_id": "S127", "section_no": "8.5.2", "title": "8.5.2 最大旋转角速度试验",
             "level": 3, "start_offset": full_text.find("8.5.2")},
            {"section_id": "S140", "section_no": "8.5.3", "title": "8.5.3 最大水平飞行速度试验",
             "level": 3, "start_offset": full_text.find("8.5.3")},
        ],
    }
    from blueprints.FLGL.method_routes import _expand_peer_method_groups

    groups = _expand_peer_method_groups(
        {"parent": {"sectionNo": "8.5", "title": "8.5 边界性能考核", "section_id": "S120"}, "children": []},
        parse_cache,
        full_text,
    )
    assert len(groups) == 3
    assert groups[0]["parent"]["sectionNo"] == "8.5.1"
    assert groups[2]["parent"]["sectionNo"] == "8.5.3"


def test_build_toc_exact_preserves_section_order():
    from blueprints.ZSGL.WJGL1.document_parse_service import build_toc_exact_from_parse_sections

    sections = [
        {"section_id": "S1", "section_no": "1", "title": "1 范围", "text": "范围正文"},
        {"section_id": "S2", "section_no": "2", "title": "2 引用文件", "text": "引用"},
        {"section_id": "S3", "section_no": "3.1", "title": "3.1 试验条件", "text": "条件"},
        {"section_id": "S4", "section_no": "3.2", "title": "3.2 试验过程", "text": "过程"},
    ]
    _, flat = build_toc_exact_from_parse_sections(sections)
    assert [h["section_id"] for h in flat] == ["S1", "S2", "S3", "S4"]
    assert [h["sectionNo"] for h in flat] == ["1", "2", "3.1", "3.2"]


if __name__ == "__main__":
    test_resolve_by_section_id()
    test_ignore_wrong_synthetic_section_id()
    test_normalize_title()
    print("ALL TESTS PASSED")
