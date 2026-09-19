"""LLM 输入预处理单测。"""
import re

import pytest

from blueprints.FLGL.GetTestMethod.llm_text_prep import (
    build_llm_heading_outline,
    is_valid_toc_heading,
    sanitize_text_for_llm,
    split_text_at_section_boundaries,
)
from blueprints.FLGL.GetTestMethod.llm_text_prep import _normalize_outline_pair


def test_sanitize_strips_base64():
    b64 = "A" * 300
    raw = f"前言\n<img src=\"data:image/png;base64,{b64}\">\n正文"
    out = sanitize_text_for_llm(raw)
    assert "data:image" not in out
    assert "[图片]" in out
    assert "正文" in out


def test_normalize_outline_dedupes_section_no():
    sno, title = _normalize_outline_pair("1", "1 试验条件")
    assert sno == "1"
    assert title == "试验条件"


def test_build_llm_heading_outline_filters_noise():
    doc = {
        "flat_headings": [
            {"sectionNo": "1", "title": "1 试验条件"},
            {"sectionNo": "1", "title": "表 6 水压参数"},
            {"sectionNo": "8.2", "title": "低温试验"},
            {"sectionNo": "8.3", "title": "高温试验"},
        ]
    }
    outline = build_llm_heading_outline(doc, limit=20)
    assert "1 试验条件" in outline
    assert "8.2 低温试验" in outline
    assert "表 6" not in outline


def test_is_valid_toc_heading_rejects_table():
    assert not is_valid_toc_heading("6", "表 6 水压")
    assert is_valid_toc_heading("8.2", "低温试验")


def test_split_at_section_boundaries_keeps_section_intact():
    text = "\n".join([
        "8.1 低温试验",
        "低温内容 " + ("低" * 800),
        "8.2 高温试验",
        "高温内容 " + ("高" * 800),
        "8.3 防水试验",
        "防水内容 " + ("水" * 800),
    ])
    chunks = split_text_at_section_boundaries(text, chunk_chars=1200, overlap_chars=0)
    assert len(chunks) >= 2
    for ch in chunks:
        if "8.1 低温" in ch and "8.3 防水" in ch:
            pytest.fail("8.1 与 8.3 被切到同一批")
