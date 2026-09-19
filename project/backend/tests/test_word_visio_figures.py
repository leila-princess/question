"""Word Visio/OLE/VML 图片关系 Id 提取。"""

from lxml import etree

from docx.text.paragraph import Paragraph
from docx import Document


def _para_from_xml(xml: str):
    doc = Document()
    body = doc.element.body
    el = etree.fromstring(xml)
    body.append(el)
    return Paragraph(el, doc), doc


def test_image_rel_ids_vml_imagedata():
    from blueprints.ZSGL.WJGL1.word_native_parse import _image_rel_ids_from_paragraph

    xml = (
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office">'
        '<w:r><w:object>'
        '<v:shape><v:imagedata r:id="rId7"/></v:shape>'
        '<o:OLEObject ProgID="Visio.Drawing.15" Type="Embed"/>'
        "</w:object></w:r></w:p>"
    )
    para, _doc = _para_from_xml(xml)
    rels = _image_rel_ids_from_paragraph(para)
    assert rels == ["rId7"]


def test_guess_metafile_ext_emf():
    from blueprints.ZSGL.WJGL1.word_native_parse import (
        _guess_metafile_ext,
        _is_metafile_content_type,
        _is_ole_compound,
        _detect_raster_mime,
        _normalize_figure_blob,
    )

    blob = b"\x01\x00\x00\x00" + b"\x00" * 40
    assert _guess_metafile_ext(blob, "image/x-emf") == ".emf"
    assert _is_metafile_content_type("image/x-emf", blob)
    assert _is_ole_compound(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 8)
    png_blob = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    assert _detect_raster_mime(png_blob) == "image/png"
    norm, mime = _normalize_figure_blob(png_blob, "application/octet-stream")
    assert norm == png_blob and mime == "image/png"
    skipped, skipped_mime = _normalize_figure_blob(blob, "image/x-emf", work_dir="")
    assert skipped is None and skipped_mime == ""
