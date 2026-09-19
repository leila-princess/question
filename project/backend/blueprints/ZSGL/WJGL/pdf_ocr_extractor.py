import os
import sys
import json
import tempfile
import shutil
from typing import Dict, List, Any, Optional
from datetime import datetime
import logging

logging.disable(logging.DEBUG)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ocr import run_ocr_pdf


class PDFOCRExtractor:
    def __init__(self, config=None):
        self.config = config
        self.output_dir = config.output_dir if config else "outputs"
        self.dpi = config.pdf_image_dpi if config else 300
        self.use_gpu = getattr(config, "ocr_use_gpu", None) if config else None

    def extract(self, pdf_path: str, original_filename: str = None) -> Dict[str, Any]:
        result = run_ocr_pdf(
            file_path=pdf_path,
            original_filename=original_filename,
            output_dir=self.output_dir,
            use_gpu=self.use_gpu,
            dpi=self.dpi,
        )

        if not result.get("success"):
            return {
                "pdf_name": original_filename or os.path.basename(pdf_path),
                "total_pages": 0,
                "pages": [],
                "full_text": "",
                "all_tables": [],
                "all_figures": [],
                "error": result.get("error"),
            }

        data = result["data"]
        return {
            "pdf_name": data.get("pdf_name", original_filename or os.path.basename(pdf_path)),
            "total_pages": data.get("total_pages", 0),
            "pages": [{
                "page_number": p.get("page_num", i + 1),
                "text": p.get("text", ""),
                "tables": p.get("tables", []),
                "figures": p.get("figures", []),
            } for i, p in enumerate(data.get("pages", []))],
            "full_text": data.get("full_text", ""),
            "all_tables": data.get("all_tables", []),
            "all_figures": data.get("all_figures", []),
            "output_dir": data.get("output_dir", ""),
            "figures_dir": data.get("figures_dir", ""),
            "metadata": data.get("metadata", {}),
        }


def build_kg_extraction(ocr_result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pdf_name": ocr_result["pdf_name"],
        "total_pages": ocr_result["total_pages"],
        "full_text": ocr_result["full_text"],
        "text_by_page": {
            f"page_{p['page_number']}": {
                "page_number": p["page_number"],
                "text": p["text"],
                "original_text": p["text"],
                "ocr_text": p["text"],
                "ocr_used": True,
                "tables": p.get("tables", []),
                "figures": p.get("figures", []),
            }
            for p in ocr_result["pages"]
        },
        "all_tables": ocr_result.get("all_tables", []),
        "all_figures": ocr_result.get("all_figures", []),
        "metadata": ocr_result.get("metadata", {}),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="纯OCR版PDF提取器")
    parser.add_argument("pdf_path", help="PDF文件路径")
    parser.add_argument("--dpi", type=int, default=300, help="PDF转图片DPI")
    parser.add_argument("--no-gpu", action="store_true", help="禁用GPU")
    parser.add_argument("--output", type=str, default="outputs", help="输出目录")
    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"[ERROR] 文件不存在: {args.pdf_path}")
        sys.exit(1)

    class _Cfg:
        output_dir = args.output
        pdf_image_dpi = args.dpi
        ocr_use_gpu = not args.no_gpu

    extractor = PDFOCRExtractor(_Cfg())
    result = extractor.extract(args.pdf_path)

    print(f"\n{'='*60}")
    print("  提取结果摘要")
    print(f"{'='*60}")
    print(f"  PDF名称: {result['pdf_name']}")
    print(f"  总页数: {result['total_pages']}")
    print(f"  文本长度: {len(result.get('full_text', ''))} 字符")
    print(f"  表格数量: {len(result.get('all_tables', []))}")
    print(f"  图片数量: {len(result.get('all_figures', []))}")
