"""Word 单页文档：章节不应重复挂载全文表格。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.ZSGL.WJGL1.document_parse_service import (
    _build_sections_with_blocks,
    _sections_should_attach_page_tables,
)


def _make_table(n: int) -> str:
    return f'<table data-t="{n}"><tr><td>表{n}</td></tr></table>'


def test_single_page_word_should_not_attach_page_tables():
    pages = [
        {
            "page_number": 1,
            "text": "1 范围\n正文\n2 引用文件\n引用",
            "tables_html": [_make_table(i) for i in range(1, 6)],
            "figures": [],
        }
    ]
    sections_raw = [
        {"section_id": "S001", "title": "1 范围", "level": 1, "start_page": 1, "end_page": 1, "text": "正文"},
        {"section_id": "S002", "title": "2 引用文件", "level": 1, "start_page": 1, "end_page": 1, "text": "引用"},
    ]
    assert _sections_should_attach_page_tables(pages, sections_raw) is False

    built = _build_sections_with_blocks(
        sections_raw,
        {1: pages[0]["tables_html"]},
        pages,
        attach_page_tables=_sections_should_attach_page_tables(pages, sections_raw),
    )
    for sec in built:
        assert sec.get("tables_html") == [], sec.get("section_id")
        assert "<table" not in (sec.get("html") or "").lower(), sec.get("section_id")
        table_blocks = [b for b in (sec.get("blocks") or []) if b.get("type") == "table"]
        assert table_blocks == [], sec.get("section_id")


def test_multi_page_pdf_with_distinct_ranges_should_attach():
    pages = [
        {"page_number": 1, "text": "1 范围", "tables_html": [_make_table(1)], "figures": []},
        {"page_number": 2, "text": "2 引用", "tables_html": [_make_table(2)], "figures": []},
    ]
    sections_raw = [
        {"section_id": "S001", "title": "1 范围", "level": 1, "start_page": 1, "end_page": 1, "text": "范围正文"},
        {"section_id": "S002", "title": "2 引用文件", "level": 1, "start_page": 2, "end_page": 2, "text": "引用正文"},
    ]
    assert _sections_should_attach_page_tables(pages, sections_raw) is True

    built = _build_sections_with_blocks(
        sections_raw,
        {1: [_make_table(1)], 2: [_make_table(2)]},
        pages,
        attach_page_tables=_sections_should_attach_page_tables(pages, sections_raw),
    )
    assert len(built[0].get("tables_html") or []) == 1
    assert len(built[1].get("tables_html") or []) == 1
    assert _make_table(2) not in (built[0].get("html") or "")


if __name__ == "__main__":
    test_single_page_word_should_not_attach_page_tables()
    test_multi_page_pdf_with_distinct_ranges_should_attach()
    print("ALL TESTS PASSED")
