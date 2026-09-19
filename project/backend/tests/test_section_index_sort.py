"""章节索引 section_no 排序回归。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.ZSGL.WJGL1.document_parse_service import _section_no_sort_key, build_section_index


def test_section_no_sort_key_mixed_numeric_alpha():
    keys = ["2.10", "2.2", "2.a", "10", "", "A.1"]
    sorted_keys = sorted(keys, key=_section_no_sort_key)
    assert sorted_keys.index("2.2") < sorted_keys.index("2.10")
    assert sorted_keys.index("2.10") < sorted_keys.index("2.a")
    # 仅验证混合 int/str 不抛错且相对顺序合理
    _ = sorted_keys


def test_build_section_index_sort_no_type_error():
    sections = [
        {"section_id": "S001", "section_no": "2.a", "title": "试验A", "text": "正文A", "level": 2},
        {"section_id": "S002", "section_no": "2.10", "title": "试验B", "text": "正文B", "level": 2},
        {"section_id": "S003", "section_no": "2.2", "title": "试验C", "text": "正文C", "level": 2},
    ]
    slices = {s["section_id"]: f"<p>{s['text']}</p>" for s in sections}
    index = build_section_index("_preview", "doc1", sections, slices, toc=[])
    assert len(index.get("entries") or []) == 3
