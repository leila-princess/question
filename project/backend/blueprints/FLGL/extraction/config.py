"""提取流水线配置。"""
from __future__ import annotations

import os

DEFAULT_EXTRACTOR_VERSION = "v1"
VALID_EXTRACTOR_VERSIONS = frozenset({"v1", "v2"})

# 解剖师分片（默认按 16384 上下文推算，可用 FLGL_ANATOMY_CHUNK_MAX_CHARS 覆盖）
ANATOMY_CONTEXT_OVERLAP_LINES = int(os.environ.get("FLGL_ANATOMY_CONTEXT_OVERLAP_LINES", "30"))


def get_anatomy_chunk_max_chars() -> int:
    override = os.environ.get("FLGL_ANATOMY_CHUNK_MAX_CHARS", "").strip()
    if override.isdigit():
        return max(2000, int(override))
    try:
        from .shared.llm_budget import anatomy_raw_slice_char_budget

        return anatomy_raw_slice_char_budget()
    except Exception:
        return 5500


def get_extractor_version() -> str:
    v = str(os.environ.get("LLM_EXTRACTOR_VERSION", DEFAULT_EXTRACTOR_VERSION)).strip().lower()
    return v if v in VALID_EXTRACTOR_VERSIONS else DEFAULT_EXTRACTOR_VERSION
