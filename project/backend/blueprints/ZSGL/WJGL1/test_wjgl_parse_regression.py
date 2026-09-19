"""WJGL 解析回归 — 基于 EFHR pdf_extract 样例文本验证归一化与乱码检测。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blueprints.ZSGL.WJGL1.text_normalize import (
    has_cid_garbled,
    normalize_document_text,
    garbled_score,
)
from blueprints.FLGL.GetTestMethod.parser import _should_force_ocr

EFHR = ROOT / "EFHR" / "experiments" / "pdf_extract"


def _load_full_text(rel: str) -> str:
    p = EFHR / rel
    if not p.is_file():
        raise FileNotFoundError(p)
    return p.read_text(encoding="utf-8", errors="replace")


def test_toc_noise_cleanup():
    raw = """４　试验条件
１
…………………………………………………………………………………………………
　４．１　低温贮存试验条件
１
…………………………………………………………………………………
４　试验条件
４．１　低温贮存试验条件
除另有规定外，低温贮存试验应按照下述试验条件进行。"""
    from blueprints.ZSGL.WJGL1.text_normalize import clean_document_text, is_toc_noise_line

    assert is_toc_noise_line("１")
    assert is_toc_noise_line("……………………")
    out = clean_document_text(raw)
    assert "…………" not in out
    assert "低温贮存试验应按照" in out
    print("OK TOC noise cleanup")


def test_fullwidth_normalization():
    raw = "６．１．４　试验步骤"
    out = normalize_document_text(raw)
    assert "6.1.4" in out, f"expected halfwidth digits, got {out!r}"
    assert "试验步骤" in out
    print("OK fullwidth normalization")


def test_cid_garbled_12547():
    text = _load_full_text("results_20260609T011220Z/12547-2009-gbt-e-300/full_text.txt")
    assert has_cid_garbled(text), "12547 should trigger CID garbled detection"
    assert garbled_score(text) >= 0.5
    fitz_data = {"full_text": text, "pages": [{"text": text[:500], "page_number": 1}]}
    assert _should_force_ocr(fitz_data), "12547 should force OCR"
    print("OK 12547 CID garbled detection")


def test_symbol_heading_38924_4():
    text = _load_full_text("results_20260609T011220Z/GBT+38924.4-2020/full_text.txt")
    assert has_cid_garbled(text), "38924.4 cover should trigger garbled/CID detection"
    norm = normalize_document_text("６．１．４　试验步骤")
    assert "6.1.4" in norm
    fitz_data = {"full_text": text, "pages": [{"text": text[:800], "page_number": 1}]}
    assert _should_force_ocr(fitz_data), "38924.4 should force OCR"
    print("OK 38924.4 symbol font / fullwidth")


def test_scan_12534_ocr_result():
    text = _load_full_text("results_ocr_test_12534_v3/12534-1990-gbt-e-300/full_text.txt")
    assert len(text.strip()) > 500, "12534 OCR text should be substantial"
    assert "汽车道路试验方法通则" in text
    assert not has_cid_garbled(text), "12534 OCR output should not be CID-garbled"
    status_path = EFHR / "results_20260609T011220Z/12534-1990-gbt-e-300/status.json"
    if status_path.is_file():
        st = json.loads(status_path.read_text(encoding="utf-8"))
        assert st.get("text_len", 1) == 0 or st.get("parse_mode") == "pdf_fitz", "12534 fitz layer empty"
    print("OK 12534 scan OCR output")


def test_optional_live_pdf():
    pdf_dir = os.environ.get("WJGL_REGRESSION_PDF_DIR", "").strip()
    if not pdf_dir or not os.path.isdir(pdf_dir):
        print("SKIP live PDF parse (set WJGL_REGRESSION_PDF_DIR)")
        return
    from blueprints.ZSGL.WJGL1.document_parse_service import parse_local_file

    samples = {
        "12547": "12547",
        "38924.4": "38924.4",
        "12534": "12534",
    }
    for label, pat in samples.items():
        matches = [f for f in os.listdir(pdf_dir) if pat in f and f.lower().endswith(".pdf")]
        if not matches:
            print(f"SKIP live {label}: no PDF matching {pat}")
            continue
        path = os.path.join(pdf_dir, matches[0])
        result = parse_local_file(path, fast=False)
        sections = result.get("sections") or []
        full = (result.get("parsed_document") or {}).get("full_text") or ""
        print(f"OK live {label}: sections={len(sections)} text_len={len(full)}")


def main():
    test_toc_noise_cleanup()
    test_fullwidth_normalization()
    test_cid_garbled_12547()
    test_symbol_heading_38924_4()
    test_scan_12534_ocr_result()
    test_optional_live_pdf()
    print("\nAll WJGL parse regression checks passed.")


if __name__ == "__main__":
    main()
