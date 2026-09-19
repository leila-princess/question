"""从现有 method-extract ctx 构建 ParseBundle。"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .line_index import LineIndex
from .schemas import PARSE_BUNDLE_SCHEMA_VERSION, ParseBundle


def _plain_from_ctx(ctx: dict) -> str:
    from blueprints.ZSGL.WJGL1.text_normalize import clean_document_text

    pc = ctx.get("parse_cache") or {}
    md = str(pc.get("full_markdown") or "").strip()
    if md:
        try:
            from pdf_extract.mineru_wjgl_service import _plain_from_markdown

            return clean_document_text(_plain_from_markdown(md))
        except Exception:
            return clean_document_text(md)
    ft = str(ctx.get("full_text") or "").strip()
    if ft:
        return clean_document_text(ft)
    html = str(pc.get("full_html") or "").strip()
    if html:
        return clean_document_text(re.sub(r"<[^>]+>", "\n", html))
    sections = pc.get("sections") or []
    if sections:
        from blueprints.ZSGL.WJGL1.text_normalize import clean_document_text

        return "\n\n".join(
            clean_document_text(str(s.get("text") or ""))
            for s in sections
            if str(s.get("text") or "").strip()
        )
    return ""


def bundle_from_extract_context(ctx: dict, *, file_name: str = "") -> ParseBundle:
    """将 _load_method_extract_context 返回值转为 ParseBundle。"""
    pc = ctx.get("parse_cache") or {}
    plain = _plain_from_ctx(ctx)
    md = str(pc.get("full_markdown") or "").strip()
    html = str(pc.get("full_html") or pc.get("raw_full_html") or "").strip()
    pd = pc.get("parsed_document") or {}
    return ParseBundle(
        schema_version=PARSE_BUNDLE_SCHEMA_VERSION,
        parse_mode=str(pd.get("parse_mode") or ctx.get("source") or "unknown"),
        source=str(ctx.get("source") or ""),
        file_name=file_name or str(pd.get("file_name") or ""),
        canonical_markdown=md,
        plain_text=plain,
        html=html,
        sections=list(pc.get("sections") or ctx.get("sections") or []),
        node_slices_md=dict(pc.get("node_slices_md") or {}),
        node_slices_html=dict(pc.get("node_slices") or {}),
        section_catalogue=list(pc.get("section_catalogue") or []),
        word_paragraph_map=pd.get("word_paragraph_map"),
        flat_headings=list(ctx.get("flat_headings") or pc.get("flat_headings") or []),
        toc_display=list(ctx.get("toc_display") or pc.get("toc_display") or []),
        all_tables=list(ctx.get("all_tables") or []),
        category=str(ctx.get("category") or ""),
        doc_id=str(ctx.get("doc_id") or ""),
        stats=dict(pc.get("stats") or {}),
    )


def attach_bundle_to_context(ctx: dict, *, file_name: str = "") -> dict:
    """在 ctx 上附加 parse_bundle 与 line_index。"""
    bundle = bundle_from_extract_context(ctx, file_name=file_name)
    ctx = dict(ctx)
    ctx["parse_bundle"] = bundle
    ctx["line_index"] = LineIndex.build(bundle.get("plain_text") or "")
    return ctx


def bundle_to_cache_payload(ctx: dict) -> dict:
    """preview 缓存可序列化字段。"""
    bundle = ctx.get("parse_bundle")
    if not isinstance(bundle, dict):
        bundle = bundle_from_extract_context(ctx)
    return {
        "schema_version": bundle.get("schema_version"),
        "parse_mode": bundle.get("parse_mode"),
        "canonical_markdown": bundle.get("canonical_markdown"),
        "plain_text": bundle.get("plain_text"),
        "sections": bundle.get("sections"),
        "node_slices_md": bundle.get("node_slices_md"),
        "node_slices_html": bundle.get("node_slices_html"),
        "section_catalogue": bundle.get("section_catalogue"),
        "flat_headings": bundle.get("flat_headings"),
        "toc_display": bundle.get("toc_display"),
    }
