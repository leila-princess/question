"""目录勾选提取：仅合并显式勾选章节，不 GB 扩展、不拉未勾选子节。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.FLGL.method_routes import (
    _explicit_section_items_from_group,
    _section_nos_from_group,
    _sort_explicit_section_items,
)


def test_no_gb_auto_expand():
    group = {
        "parent": {"sectionNo": "4", "title": "试验条件"},
        "children": [],
    }
    nos = _section_nos_from_group(group)
    assert nos == ["4"], f"GB expand leaked: {nos}"


def test_parent_and_children_only():
    group = {
        "parent": {"sectionNo": "3.1", "title": "最大起飞重量", "section_id": "S010"},
        "children": [
            {"sectionNo": "3.1.1", "title": "试验条件", "section_id": "S011"},
            {"sectionNo": "3.1.2", "title": "试验过程", "section_id": "S012"},
        ],
    }
    items = _explicit_section_items_from_group(group)
    assert len(items) == 3
    assert [i["sectionNo"] for i in items] == ["3.1", "3.1.1", "3.1.2"]


def test_sort_by_document_order():
    parse_cache = {
        "sections": [
            {"section_id": "S1", "section_no": "3.2", "title": "B"},
            {"section_id": "S2", "section_no": "3.1", "title": "A"},
        ]
    }
    items = [
        {"sectionNo": "3.2", "title": "B", "section_id": "S1"},
        {"sectionNo": "3.1", "title": "A", "section_id": "S2"},
    ]
    sorted_items = _sort_explicit_section_items(items, parse_cache)
    assert [i["sectionNo"] for i in sorted_items] == ["3.1", "3.2"]


if __name__ == "__main__":
    test_no_gb_auto_expand()
    test_parent_and_children_only()
    test_sort_by_document_order()
    print("ALL TESTS PASSED")
