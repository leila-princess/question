"""章节号自然排序单测。"""
from blueprints.ZSGL.WJGL1.section_number_utils import section_no_sort_key, sort_toc_tree


def test_section_no_sort_key_numeric_order():
    nos = ["1", "10", "11", "2", "3", "8.1", "8.2", "8.10"]
    assert sorted(nos, key=section_no_sort_key) == ["1", "2", "3", "8.1", "8.2", "8.10", "10", "11"]


def test_sort_toc_tree_children():
    toc = [
        {"sectionNo": "8", "title": "八", "children": [
            {"sectionNo": "8.10", "title": "子10", "children": []},
            {"sectionNo": "8.2", "title": "子2", "children": []},
        ]},
        {"sectionNo": "1", "title": "一", "children": []},
    ]
    out = sort_toc_tree(toc)
    assert [n["sectionNo"] for n in out] == ["1", "8"]
    assert [n["sectionNo"] for n in out[1]["children"]] == ["8.2", "8.10"]
