"""LLM 16384 上下文切片预算测试。"""
from blueprints.FLGL.extraction.llm.chunker import format_chunk_input, plan_anatomy_chunks
from blueprints.FLGL.extraction.shared.line_index import LineIndex
from blueprints.FLGL.extraction.shared.llm_budget import (
    LLM_CONTEXT_TOKENS,
    anatomy_numbered_text_budget_chars,
    anatomy_raw_slice_char_budget,
    word_llm_chunk_text_budget,
)


def test_llm_context_tokens_default_16384():
    assert LLM_CONTEXT_TOKENS == 16384


def test_anatomy_budgets_conservative():
    numbered = anatomy_numbered_text_budget_chars()
    raw = anatomy_raw_slice_char_budget()
    v1 = word_llm_chunk_text_budget()
    assert numbered >= 4000
    assert raw >= 4000
    assert raw <= v1
    assert numbered + 2000 < 16384 * 2  # 字符上限应远低于粗暴 2x token 估算


def test_format_chunk_input_within_budget():
    lines = [f"8.{i} 试验节标题" + ("内容" * 120) for i in range(1, 120)]
    idx = LineIndex.build("\n".join(lines))
    chunks = plan_anatomy_chunks(idx, max_chars=3000)
    budget = anatomy_numbered_text_budget_chars()
    assert len(chunks) > 1
    for chunk in chunks:
        formatted = format_chunk_input(idx, chunk)
        assert len(formatted) <= budget
