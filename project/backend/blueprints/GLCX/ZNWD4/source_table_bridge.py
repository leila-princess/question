"""ZNWD4 来源表格：复用 ZNWD source_table，避免重复 SSYQ 展开逻辑。"""

from __future__ import annotations

from typing import Any, Dict, List

from ..ZNWD.source_table import build_znwd_table_keyword, hydrate_source_table_payload


def build_znwd4_table_keyword(
    question: str,
    *,
    reconcile: Dict[str, Any] | None = None,
    neo_rows: List[Dict[str, Any]] | None = None,
) -> str:
    rec = reconcile or {}
    kw = str(rec.get("keyword") or "").strip()
    from_neo = build_znwd_table_keyword(question, neo_rows or [])
    if from_neo:
        return from_neo
    return kw[:80] if kw else build_znwd_table_keyword(question, []) or ""


__all__ = ["build_znwd4_table_keyword", "hydrate_source_table_payload"]
