"""统一文档解析入口。"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .parse_bundle import attach_bundle_to_context, bundle_from_extract_context


def parse_document_file(
    file_path: str,
    *,
    ext: str = "",
    tmp_dir: str = "",
    preview_cache_key: str = "",
) -> Dict[str, Any]:
    """
    解析文档并返回带 parse_bundle 的 context（与 method_routes 上下文形状兼容）。
    优先 Word 原生 / MinerU；内部复用 method_routes 已有函数。
    """
    from blueprints.FLGL.method_routes import _load_method_extract_context

    path = os.path.abspath(file_path)
    if not ext:
        ext = os.path.splitext(path)[1].lower()
    if not tmp_dir:
        tmp_dir = os.path.dirname(path)
    ctx = _load_method_extract_context(
        path,
        ext,
        tmp_dir,
        minio_client=None,
        bucket_name="",
        object_name="",
        file_node=None,
        preview_cache_key=preview_cache_key,
    )
    return attach_bundle_to_context(ctx, file_name=os.path.basename(path))


def get_line_index_from_bundle(bundle: dict):
    from .line_index import LineIndex

    return LineIndex.build(str(bundle.get("plain_text") or ""))
