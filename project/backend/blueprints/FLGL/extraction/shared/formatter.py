"""SYNR / blocks 格式化 — 薄封装手动路径。"""
from __future__ import annotations

from typing import Any, Dict


def plain_text_to_method_html(text: str) -> str:
    from blueprints.FLGL.method_section_enrich import _plain_text_to_method_html

    return _plain_text_to_method_html(text or "")


def finalize_section_extract_markdown(md: str) -> str:
    from blueprints.FLGL.method_section_enrich import finalize_section_extract_markdown as _fn

    return _fn(md or "")


def finalize_section_extract_synr(html: str) -> str:
    from blueprints.FLGL.method_section_enrich import ensure_method_synr_html

    return ensure_method_synr_html(html or "")


def blocks_to_synr_html(
    blocks: Dict[str, str],
    *,
    extract_source: str = "llm_v2",
    use_markdown: bool = False,
) -> str:
    """将 sytj/sygc/jgpd 合并为 SYNR。"""
    from blueprints.FLGL.method_section_enrich import is_section_extract_source

    parts = []
    for title, key in [("1. 试验条件", "sytj"), ("2. 试验过程", "sygc"), ("3. 结果评定", "jgpd")]:
        body = str(blocks.get(key) or "").strip()
        if not body:
            continue
        if use_markdown or is_section_extract_source(extract_source):
            parts.append(f"## {title}\n\n{body}")
        else:
            html_body = plain_text_to_method_html(body) if "<" not in body[:80] else body
            parts.append(f"<h4>{title}</h4>{html_body}")
    merged = "\n\n".join(parts)
    if use_markdown or is_section_extract_source(extract_source):
        return finalize_section_extract_markdown(merged)
    return finalize_section_extract_synr(merged)


def completeness_check(blocks: Dict[str, str]) -> Dict[str, Any]:
    missing = []
    for key in ("sytj", "sygc", "jgpd"):
        if not str(blocks.get(key) or "").strip():
            missing.append(key)
    return {
        "missing_status": "complete" if not missing else "partial",
        "missing_blocks": missing,
    }
