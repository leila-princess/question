"""
ZNWD5 关键词探测：CONTAINS 精确命中 → Zvec 语义召回 → FTS 兜底。
输出格式与 ZNWD4 probe_snapshot 完全兼容。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from blueprints.GLCX.ZNWD4 import keyword_probe as znwd4_probe

from .embedding_service import using_hash_fallback
from .zvec_entity_probe import merge_zvec_into_probe_result, zvec_semantic_probe

logger = logging.getLogger(__name__)

# 模块首次加载时捕获 ZNWD4 原始 CONTAINS probe（bootstrap patch 之前）
_probe_keyword_contains = znwd4_probe.probe_keyword

# 复用 ZNWD4 工具函数
compress_probe_for_tool = znwd4_probe.compress_probe_for_tool
extract_focus_candidates = znwd4_probe.extract_focus_candidates
plain_intent_slice = znwd4_probe.plain_intent_slice

_ENUM_QUESTION_RE = re.compile(
    r"有哪些|包括哪些|列举|都有什么|有什么试验项目|有什么项目|有包括哪些|装备类别有哪些|试验项目有哪些"
)


def _zvec_probe_enabled() -> bool:
    return os.environ.get("ZNWD5_ZVEC_PROBE", "1").lower() in ("1", "true", "yes")


def _is_enumeration_question(question: str, keyword: str) -> bool:
    q = (question or keyword or "").strip()
    return bool(_ENUM_QUESTION_RE.search(q))


def _should_skip_zvec_semantic(
    *,
    question: str,
    keyword: str,
    label_hints: Optional[List[str]],
    base: Dict[str, Any],
) -> str:
    if using_hash_fallback():
        return "hash_embedding_fallback"
    if not _zvec_probe_enabled():
        return "zvec_probe_disabled"
    return ""


def _zvec_probe_always() -> bool:
    return os.environ.get("ZNWD5_ZVEC_PROBE_ALWAYS", "").strip().lower() in ("1", "true", "yes")


def probe_keyword(
    driver: Any,
    keyword: str,
    *,
    label_hints: Optional[List[str]] = None,
    enhanced: bool = False,
    question: str = "",
    parent_hint: str = "",
    child_hint: str = "",
    force_zvec: bool = False,
) -> Dict[str, Any]:
    base = _probe_keyword_contains(
        driver,
        keyword,
        label_hints=label_hints,
        enhanced=enhanced,
        question=question,
        parent_hint=parent_hint,
        child_hint=child_hint,
    )

    skip_reason = _should_skip_zvec_semantic(
        question=question,
        keyword=keyword,
        label_hints=label_hints,
        base=base,
    )
    if skip_reason:
        base["zvec_used"] = False
        base["zvec_skip_reason"] = skip_reason
        if skip_reason == "hash_embedding_fallback":
            logger.debug("[ZNWD5][probe] 跳过 zvec 语义（hash 伪向量模式） kw=%r", keyword[:30])
        return base

    best_score = float((base.get("best") or {}).get("score") or 0.0)
    need_semantic = (
        force_zvec
        or _zvec_probe_always()
        or best_score < 0.72
        or int(base.get("match_count") or 0) == 0
    )

    if not need_semantic:
        base["zvec_used"] = False
        return base

    try:
        zvec_rows = zvec_semantic_probe(
            keyword,
            label_hints=label_hints,
            question=question or keyword,
        )
        if zvec_rows:
            merged = merge_zvec_into_probe_result(
                base,
                zvec_rows,
                keyword=keyword,
                question=question,
            )
            merged["zvec_used"] = True
            logger.info(
                "[ZNWD5][probe] zvec 增强 kw=%r before=%d after=%d best=%s",
                keyword[:30],
                base.get("match_count"),
                merged.get("match_count"),
                (merged.get("best") or {}).get("name"),
            )
            return merged
    except Exception as exc:
        logger.warning("[ZNWD5][probe] zvec 语义 probe 失败，保留 CONTAINS 结果: %s", exc)

    base["zvec_used"] = False
    return base
