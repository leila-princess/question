"""word_llm_upload_extract：16384 上下文自动分片。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import (
    FIXED_LLM_CONTEXT_TOKENS,
    _build_document_body_for_llm,
    _build_parsed_document_dict,
    _llm_chunk_text_char_budget,
    _split_body_into_llm_chunks,
)


def test_chunk_budget_uses_16384_context():
    budget = _llm_chunk_text_char_budget()
    assert budget >= 6000
    assert FIXED_LLM_CONTEXT_TOKENS == 16384


def test_short_body_single_chunk():
    text = "短文档\n\n试验条件\n满足要求。\n\n试验过程\n步骤一。"
    chunks = _split_body_into_llm_chunks(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_long_body_splits_by_sections():
    budget = _llm_chunk_text_char_budget()
    sections = [
        {"title": f"第{i}章试验", "text": "X" * (budget // 2)}
        for i in range(1, 5)
    ]
    body = "\n\n".join(f"=== {s['title']} ===\n{s['text']}" for s in sections)
    chunks = _split_body_into_llm_chunks(body, sections=sections, chunk_chars=budget)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= budget + 500


def test_long_plain_text_splits():
    budget = 2000
    text = ("段落内容。" * 200 + "\n\n") * 50
    chunks = _split_body_into_llm_chunks(text, chunk_chars=budget)
    assert len(chunks) >= 2


def test_build_parsed_document_dict_preserves_markdown():
    def _split(_text):
        return [{"section_id": "S1", "title": "1 试验", "text": "正文"}]

    def _catalogue(_secs):
        return []

    doc = _build_parsed_document_dict(
        resolved="/tmp/a.docx",
        file_name="a.docx",
        ext=".docx",
        parse_mode="docx_native",
        full_text="纯文本正文",
        full_markdown="## 1 试验\n\n| 列 | 值 |\n|---|---|\n| A | 1 |",
        full_html="<p>html</p>",
        pages=[],
        all_tables=[],
        ocr_used=False,
        split_standard_sections=_split,
        build_section_catalogue=_catalogue,
    )
    assert "## 1 试验" in doc.get("full_markdown", "")
    assert doc.get("full_html") == "<p>html</p>"


def test_llm_body_prefers_markdown():
    body = _build_document_body_for_llm({
        "full_markdown": "## 8.2 温度\n\n| 参数 | 值 |",
        "full_text": "纯文本无表格",
        "raw_full_text": "纯文本无表格",
    })
    assert "## 8.2" in body
    assert "| 参数 |" in body
