"""LLM 上下文与切片字符预算（默认 16384 tokens，与 word_llm 对齐）。"""
from __future__ import annotations

import os

# 与 GetTestMethod/word_llm_upload_extract.FIXED_LLM_CONTEXT_TOKENS 保持一致
LLM_CONTEXT_TOKENS = int(os.environ.get("LLM_CONTEXT_TOKENS", "16384"))
LLM_MAX_COMPLETION_TOKENS = int(os.environ.get("LLM_MAX_COMPLETION_TOKENS", "4096"))
PROMPT_RESERVE_CHARS = int(os.environ.get("FLGL_PROMPT_RESERVE_CHARS", "4500"))
MIN_CHUNK_TEXT_CHARS = int(os.environ.get("FLGL_MIN_CHUNK_TEXT_CHARS", "4000"))
# 带行号前缀估算："[n] " 平均约 6 字符/行
LINE_NUMBER_OVERHEAD_CHARS = int(os.environ.get("FLGL_LINE_NUMBER_OVERHEAD_CHARS", "6"))


def ensure_llm_context_config() -> None:
    """确保 extractor 侧 clamp 使用 16384 上下文。"""
    from blueprints.FLGL.GetTestMethod import Config

    Config.LLM_CONTEXT_TOKENS = LLM_CONTEXT_TOKENS
    Config.LLM_MAX_COMPLETION_TOKENS = LLM_MAX_COMPLETION_TOKENS


def _full_prompt_char_cap(*, reserve_out: int, label: str) -> int:
    try:
        from blueprints.FLGL.GetTestMethod.extractor import _prompt_char_cap_vs_context

        return _prompt_char_cap_vs_context(50_000, reserve_out, label=label)
    except Exception:
        input_tok = max(512, LLM_CONTEXT_TOKENS - reserve_out - 256)
        return max(3500, int((input_tok - 320) * 2))


def anatomy_prompt_shell_chars() -> int:
    from ..llm.prompts import ANATOMIST_SYSTEM_PROMPT, ANATOMIST_USER_TEMPLATE

    template_shell = ANATOMIST_USER_TEMPLATE.replace("{text}", "")
    return len(ANATOMIST_SYSTEM_PROMPT) + len(template_shell) + 96


def anatomy_numbered_text_budget_chars() -> int:
    """解剖师 user 消息中 {text} 部分（带行号正文）的字符上限。"""
    cap = _full_prompt_char_cap(reserve_out=LLM_MAX_COMPLETION_TOKENS, label="anatomy")
    return max(MIN_CHUNK_TEXT_CHARS, cap - anatomy_prompt_shell_chars())


def word_llm_chunk_text_budget() -> int:
    """与 v1 word_llm 单次文档片段预算一致。"""
    try:
        from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import (
            FIXED_CHUNK_TEXT_CHARS,
            _llm_chunk_text_char_budget,
        )

        return max(FIXED_CHUNK_TEXT_CHARS, _llm_chunk_text_char_budget(prompt_shell_chars=PROMPT_RESERVE_CHARS))
    except Exception:
        return max(MIN_CHUNK_TEXT_CHARS, int(LLM_CONTEXT_TOKENS * 1.6) - PROMPT_RESERVE_CHARS - LLM_MAX_COMPLETION_TOKENS)


def anatomy_raw_slice_char_budget() -> int:
    """
    分片时按「纯文本」计的字符上限（未加行号）。
    取 16384 预算与 v1 分片预算的较小值，并预留行号膨胀余量。
    """
    numbered_budget = anatomy_numbered_text_budget_chars()
    v1_budget = word_llm_chunk_text_budget()
    # 纯文本 ≈ 带行号文本；预留约 15% 给行号与前缀
    raw_from_numbered = int(numbered_budget * 0.85)
    raw_from_v1 = max(MIN_CHUNK_TEXT_CHARS, v1_budget - 800)
    return max(MIN_CHUNK_TEXT_CHARS, min(raw_from_numbered, raw_from_v1))


def estimate_numbered_text_chars(raw_chars: int, line_count: int) -> int:
    if line_count <= 0:
        return raw_chars
    return raw_chars + line_count * LINE_NUMBER_OVERHEAD_CHARS


def fit_text_to_char_budget(text: str, budget: int, *, suffix: str = "\n...[截断]") -> str:
    text = text or ""
    if len(text) <= budget:
        return text
    keep = max(0, budget - len(suffix))
    return text[:keep] + suffix
