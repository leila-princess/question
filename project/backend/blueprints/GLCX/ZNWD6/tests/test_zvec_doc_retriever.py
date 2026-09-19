from blueprints.GLCX.ZNWD5.zvec_doc_retriever import (
    _extract_section_hint,
    _section_match_score,
    _section_variants,
    extract_doc_query_hints,
)

from blueprints.GLCX.ZNWD4.formatters import format_direct_doc_answer as fmt_doc


def test_section_variants():
    assert "4.3" in _section_variants("43")
    assert "43" in _section_variants("4.3")


def test_section_match_score():
    assert _section_match_score("4.3", "43") == 0
    assert _section_match_score("43", "43") == 0


def test_extract_section_hint_fullwidth():
    assert _extract_section_hint("第４３节内容") == "43"


def test_extract_doc_query_hints_overview():
    hints = extract_doc_query_hints("机动车暴露于天气老化试验方法标准的主要内容是什么")
    assert hints["overview"] is True
    assert "机动车" in hints.get("title_keyword", "") or hints.get("title_keyword")


def test_format_direct_doc_answer():
    rows = [{"content": "GB/T 40512 封面原文片段", "title": "t1"}]
    assert "40512" in fmt_doc(rows)
