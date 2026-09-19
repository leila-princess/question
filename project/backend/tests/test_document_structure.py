"""document_structure：试验方案标题识别、全文保留、层级树。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.ZSGL.WJGL1.document_structure import (
    analyze_document_structure,
    build_structure_tree,
    collect_text_heading_candidates,
    merge_heading_candidates,
    nested_toc_from_flat_headings,
    scan_nested_headings,
    split_sections_from_structure,
    _slice_body_between_offsets,
)


TRIAL_SCHEME_TEXT = """某某试验方案

编制单位：测试单位
日期：2024年1月

高温试验

试验条件
环境温度应满足要求。

试验过程
（1）将试件放入试验箱。
（2）升温至规定温度。

结果评定
按产品规范评定。

低温试验

试验条件
低温条件说明。

试验过程
（1）降温过程描述。

结果评定
合格判据说明。
"""


def test_raw_full_text_preserves_cover():
    structure = analyze_document_structure(full_text=TRIAL_SCHEME_TEXT)
    assert "编制单位" in structure.raw_full_text
    assert "高温试验" in structure.raw_full_text
    assert len(structure.sections) >= 2, structure.sections
    first_title = str(structure.sections[0].get("title") or "")
    assert not first_title.startswith("4"), f"首节不应是第4章: {first_title}"


def test_mixed_heading_levels():
    text = """封面信息

一、总则
总则正文

1.1 适用范围
范围说明

（1）子步骤一
步骤内容

（2）子步骤二
更多内容
"""
    cands = collect_text_heading_candidates(text)
    assert len(cands) >= 3
    tree = build_structure_tree(merge_heading_candidates(cands))
    assert tree, tree
    sections = split_sections_from_structure(text, tree)
    assert len(sections) >= 2


def test_nested_subheadings_in_section():
    body = "试验过程\n（1）步骤A\n内容A\n（2）步骤B\n内容B"
    subs = scan_nested_headings(body, parent_level=2)
    assert len(subs) >= 2
    assert any("（1）" in s.get("title", "") or s.get("section_no") == "1" for s in subs)


def test_fit_llm_by_sections():
    from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import _fit_document_body_for_llm

    long_body = "A" * 50000
    sections = [
        {"title": f"第{i}章", "text": "X" * 3000}
        for i in range(1, 12)
    ]
    fitted, truncated = _fit_document_body_for_llm(long_body, max_chars=8000, sections=sections)
    assert truncated
    assert "第1章" in fitted
    assert "第2章" in fitted or "第3章" in fitted
    assert "中间省略" not in fitted or "=== 第" in fitted


def test_heading_outline_from_flat():
    structure = analyze_document_structure(full_text=TRIAL_SCHEME_TEXT)
    assert len(structure.flat_headings) >= 1 or len(structure.sections) >= 2


def test_slice_body_between_offsets():
    text = "高温试验\n环境温度应满足要求。\n\n低温试验\n低温条件说明。"
    chunk = _slice_body_between_offsets(text, 0, 20, "高温试验")
    assert "环境温度" in chunk
    assert "低温试验" not in chunk


def test_split_sections_body_not_title_only():
    structure = analyze_document_structure(full_text=TRIAL_SCHEME_TEXT)
    sections = structure.sections
    assert sections
    with_body = [
        s for s in sections
        if len(str(s.get("text") or "")) > len(str(s.get("title") or "")) + 5
    ]
    assert with_body, [(s.get("title"), s.get("text")) for s in sections]


def test_nested_toc_from_flat_headings():
    structure = analyze_document_structure(full_text=TRIAL_SCHEME_TEXT)
    flat = structure.flat_headings or [
        {
            "section_id": s.get("section_id"),
            "sectionNo": s.get("section_no") or "",
            "title": s.get("title") or "",
            "level": int(s.get("level") or 1),
        }
        for s in structure.sections
    ]
    toc = nested_toc_from_flat_headings(flat)
    assert isinstance(toc, list)
    assert len(toc) >= 1


if __name__ == "__main__":
    test_raw_full_text_preserves_cover()
    test_mixed_heading_levels()
    test_nested_subheadings_in_section()
    test_fit_llm_by_sections()
    test_heading_outline_from_flat()
    test_slice_body_between_offsets()
    test_split_sections_body_not_title_only()
    test_nested_toc_from_flat_headings()
    print("ALL TESTS PASSED")
