"""Standard API MinIO 路径解析单测。"""

from blueprints.Standard.routes import (
    _is_wjgl_style_object_key,
    _minio_object_candidates,
    _resolve_minio_pdf_object_path,
)


def test_resolve_wjgl_docx_to_pdf():
    mid = "法规标准/fgbz0001/origin.docx"
    assert _resolve_minio_pdf_object_path(mid) == "法规标准/fgbz0001/origin.pdf"
    assert mid in _minio_object_candidates(mid)


def test_resolve_legacy_short_id():
    assert _resolve_minio_pdf_object_path("fgbz0001") == "标准/fgbz0001/origin.pdf"


def test_resolve_pdf_unchanged():
    p = "法规标准/fgbz0001/origin.pdf"
    assert _resolve_minio_pdf_object_path(p) == p


def test_not_double_append_origin_pdf():
    mid = "法规标准/fgbz0001/origin.docx"
    resolved = _resolve_minio_pdf_object_path(mid)
    assert "/origin.docx/origin.pdf" not in resolved


def test_wjgl_style_detect():
    assert _is_wjgl_style_object_key("法规标准/fgbz0001/origin.docx") is True
    assert _is_wjgl_style_object_key("法规标准/fgbz0001/origin.pdf") is True
    assert _is_wjgl_style_object_key("标准/fgbz0001") is False
