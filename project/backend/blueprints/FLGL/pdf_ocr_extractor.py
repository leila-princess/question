# pdf_ocr_extractor.py
# 纯OCR版PDF提取器 — 全程使用PaddleOCR，不依赖pdfplumber文本提取
# 自动检测并安装PaddleOCR依赖

import os
import sys
import subprocess

# 必须在任何 import 之前设置 — 解决 protobuf 版本冲突
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
# PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import json
import re
import tempfile
import shutil
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field
import logging
logging.disable(logging.DEBUG)
# ============================================================================
# 依赖检查与自动安装
# ============================================================================

def _ensure_paddleocr():
    try:
        from paddleocr import PaddleOCR
        from paddleocr import PPStructure as PPStructure
        print("[OK] PaddleOCR 已安装")
        return True
    except (ImportError, TypeError) as e:
        print(f"[!!] PaddleOCR 不可用: {e}")
        print("[!!] 请手动安装: pip install protobuf==3.20.3 paddlepaddle paddleocr PyMuPDF opencv-python numpy")
        return False


_ensure_paddleocr()

import cv2
import numpy as np
from paddleocr import PaddleOCR

try:
    from paddleocr import PPStructure as PPStructure
except ImportError:
    try:
        from paddleocr import PPStructure
    except ImportError:
        PPStructure = None


# ============================================================================
# 配置
# ============================================================================

@dataclass
class OCRExtractorConfig:
    """OCR提取器配置"""
    pdf_image_dpi: int = 300
    save_figures: bool = True
    save_tables: bool = True
    save_text: bool = True
    output_dir: str = "outputs"
    rec_batch_num=4
    ocr_use_gpu: bool = False
    ocr_lang: List[str] = field(default_factory=lambda: ["ch", "en"])


# ============================================================================
# 核心提取器
# ============================================================================

class PDFOCRExtractor:
    """
    纯OCR版PDF提取器
    
    流程:
    1. PDF每页转为图片 (pdf2image)
    2. 使用PaddleOCR PPStructure进行版面分析 (表格/图片/文字区域)
    3. 对每个区域分别OCR识别
    4. 输出结构化结果: 文本 + 表格HTML + 图片路径
    """

    def __init__(self, config: Optional[OCRExtractorConfig] = None):
        self.config = config or OCRExtractorConfig()
        self.ocr_engine = None
        self.layout_engine = None
        self._init_engines()

    def _init_engines(self):
        """初始化OCR引擎和版面分析引擎"""
        from backend.ocr.paddle_compat import create_paddle_ocr, create_pp_structure

        lang = self.config.ocr_lang[0] if self.config.ocr_lang else "ch"
        print("[..] 初始化 PaddleOCR 文字识别引擎...")
        self.ocr_engine = create_paddle_ocr(
            use_gpu=self.config.ocr_use_gpu,
            lang=lang,
            rec_batch_num=self.config.rec_batch_num,
        )
        print("[OK] 文字识别引擎就绪")

        self.layout_engine = create_pp_structure(use_gpu=self.config.ocr_use_gpu)
        if self.layout_engine is not None:
            print("[OK] 版面分析引擎就绪 (支持表格/图片/文字区域检测)")
        else:
            print("[WARN] PPStructure 不可用或已禁用，将使用纯OCR模式")

    # ------------------------------------------------------------------
    # PDF → 图片
    # ------------------------------------------------------------------

    def _pdf_to_images(self, pdf_path: str, temp_dir: str) -> List[str]:
        """将PDF每页转为图片，返回图片路径列表 (使用PyMuPDF替代pdf2image，避免poppler依赖)"""
        import fitz
        doc = fitz.open(pdf_path)
        paths = []
        for idx in range(len(doc)):
            page = doc[idx]
            mat = fitz.Matrix(self.config.pdf_image_dpi / 72, self.config.pdf_image_dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_path = os.path.join(temp_dir, f"page_{idx + 1:04d}.png")
            pix.save(img_path)
            paths.append(img_path)
        doc.close()
        return paths

    # ------------------------------------------------------------------
    # 版面分析 + OCR
    # ------------------------------------------------------------------

    def _analyze_page_layout(self, img_path: str, page_num: int, figures_dir: str) -> Dict:
        """
        使用PPStructure对单页进行版面分析
        
        返回:
        {
            "text": str,           # 页面整体文本
            "tables": List[str],   # 表格HTML列表
            "figures": List[dict], # 图片信息列表
        }
        """
        result = {
            "text": "",
            "tables": [],
            "figures": [],
        }

        img = cv2.imread(img_path)
        if img is None:
            return result

        h, w = img.shape[:2]
        page_area = h * w

        if self.layout_engine is None:
            # 降级: 整页OCR
            result["text"] = self._ocr_full_page(img_path)
            return result

        try:
            layout_res = self.layout_engine(img)
        except Exception as e:
            print(f"  [WARN] 版面分析失败 (第{page_num}页): {e}")
            result["text"] = self._ocr_full_page(img_path)
            return result

        def _norm_text_for_dedupe(text: str) -> str:
            return re.sub(r"\s+", "", str(text or ""))

        def _append_text_once(parts: List[str], seen: set, text: str) -> None:
            s = str(text or "").strip()
            if not s:
                return
            key = _norm_text_for_dedupe(s)
            if not key or key in seen:
                return
            seen.add(key)
            # 若当前文本是已有较长片段的子串，跳过；避免区域 OCR 与整页 OCR 双重重复。
            for old in list(seen):
                if old != key and len(key) > 20 and key in old:
                    return
            parts.append(s)

        all_text_parts = []
        seen_text = set()
        sorted_regions = sorted(
            layout_res,
            key=lambda r: ((r.get("bbox") or [0, 0, 0, 0])[1], (r.get("bbox") or [0, 0, 0, 0])[0]),
        )
        has_layout_text = False

        for region in sorted_regions:
            region_type = region.get("type", "")
            bbox = region.get("bbox")
            if not bbox or len(bbox) != 4:
                continue

            x1, y1, x2, y2 = map(int, bbox)
            region_area = (x2 - x1) * (y2 - y1)

            if region_type == "table":
                html = region.get("res", {}).get("html", "")
                if html:
                    result["tables"].append(html)
                    # 表格文本不再混入页面正文，避免单元格散落到试验条件/过程；表格 HTML 单独保留。
                    pass

            elif region_type == "figure":
                # 忽略面积过大的区域 (整页图)
                if page_area > 0 and region_area / page_area > 0.9:
                    continue
                cropped = img[y1:y2, x1:x2]
                fig_filename = f"{page_num}_img{len(result['figures']) + 1}.png"
                fig_path = os.path.join(figures_dir, fig_filename)
                cv2.imwrite(fig_path, cropped)
                ocr_text = self._ocr_image_array(cropped)
                result["figures"].append({
                    "path": fig_path,
                    "filename": fig_filename,
                    "ocr_text": ocr_text,
                    "bbox": list(bbox),
                    "page": page_num,
                })
                if ocr_text:
                    _append_text_once(all_text_parts, seen_text, ocr_text)
                    has_layout_text = True

            elif region_type == "text":
                # 文字区域: 裁剪后OCR
                cropped = img[y1:y2, x1:x2]
                region_text = self._ocr_image_array(cropped)
                if region_text:
                    _append_text_once(all_text_parts, seen_text, region_text)
                    has_layout_text = True

            elif region_type == "title":
                # 标题区域
                cropped = img[y1:y2, x1:x2]
                title_text = self._ocr_image_array(cropped)
                if title_text:
                    _append_text_once(all_text_parts, seen_text, title_text)
                    has_layout_text = True

            elif region_type == "header" or region_type == "footer":
                # 页眉页脚不进入正文，避免 GB/T 编号、页码、水印污染方法内容。
                continue

        # 整页OCR补充：如果版面区域识别的文本量太少（低于 200 字符），用整页 OCR 补充，避免大量内容丢失。
        layout_text_len = sum(len(p) for p in all_text_parts)
        if not has_layout_text or layout_text_len < 200:
            full_page_text = self._ocr_full_page(img_path)
            if full_page_text:
                _append_text_once(all_text_parts, seen_text, full_page_text)

        result["text"] = "\n".join(all_text_parts)
        return result

    def _ocr_full_page(self, img_path: str) -> str:
        """对整张图片进行OCR，返回拼接文本"""
        img = cv2.imread(img_path)
        if img is None:
            return ""
        return self._ocr_image_array(img)

    def _ocr_image_array(self, img_array: np.ndarray) -> str:
        """对numpy数组图片进行OCR"""
        try:
            result = self.ocr_engine.ocr(img_array)
            if not result or not result[0]:
                return ""
            lines = []
            for line in result[0]:
                if line and len(line) >= 2:
                    text_info = line[1]
                    if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                        text = text_info[0]
                        conf = text_info[1]
                        if conf > 0.5 and text.strip():
                            lines.append(text)
                    elif isinstance(text_info, str) and text_info.strip():
                        lines.append(text_info)
            return "\n".join(lines)
        except Exception as e:
            print(f"  [WARN] OCR识别失败: {e}")
            return ""

    # ------------------------------------------------------------------
    # 主入口: 提取整个PDF
    # ------------------------------------------------------------------

    def extract(self, pdf_path: str, original_filename: str = None) -> Dict[str, Any]:
        """
        提取PDF全部内容
        
        Args:
            pdf_path: PDF文件路径
            original_filename: 原始文件名 (可选)
        
        Returns:
            {
                "pdf_name": str,
                "total_pages": int,
                "pages": List[page_info],
                "full_text": str,
                "all_tables": List[html],
                "all_figures": List[dict],
                "metadata": {...}
            }
        """
        pdf_name = original_filename or os.path.basename(pdf_path)
        print(f"\n{'='*60}")
        print(f"  开始OCR提取: {pdf_name}")
        print(f"{'='*60}")

        # 创建输出目录
        base_name = pdf_name.replace(".pdf", "").replace("+", "_")
        output_dir = os.path.join(self.config.output_dir, f"ocr_{base_name}")
        figures_dir = os.path.join(output_dir, "figures")
        text_dir = os.path.join(output_dir, "text")
        os.makedirs(figures_dir, exist_ok=True)
        os.makedirs(text_dir, exist_ok=True)

        # 创建临时目录存放页面图片
        temp_dir = tempfile.mkdtemp(prefix="pdf_ocr_pages_")

        try:
            # 步骤1: PDF → 图片
            print("\n[1/3] 将PDF转换为图片...")
            image_paths = self._pdf_to_images(pdf_path, temp_dir)
            total_pages = len(image_paths)
            print(f"  共 {total_pages} 页")

            # 步骤2: 逐页版面分析 + OCR
            print("\n[2/3] 逐页版面分析与OCR识别...")
            pages = []
            all_tables = []
            all_figures = []
            all_text_parts = []

            for idx, img_path in enumerate(image_paths, 1):
                print(f"  处理第 {idx}/{total_pages} 页...", end="\r")
                page_info = self._analyze_page_layout(img_path, idx, figures_dir)

                pages.append({
                    "page_number": idx,
                    "text": page_info["text"],
                    "tables": page_info["tables"],
                    "figures": page_info["figures"],
                })

                if page_info["text"]:
                    all_text_parts.append(f"===== Page {idx} =====\n{page_info['text']}")

                for tbl in page_info["tables"]:
                    all_tables.append({
                        "page": idx,
                        "html": tbl,
                    })

                for fig in page_info["figures"]:
                    all_figures.append(fig)

            print(f"\n  完成! 共处理 {total_pages} 页")

            # 步骤3: 汇总输出
            print("\n[3/3] 汇总结果...")
            full_text = "\n\n".join(all_text_parts)

            # 保存文本与结构化 OCR 缓存
            if self.config.save_text:
                txt_path = os.path.join(output_dir, "extracted_text.txt")
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(full_text)
                print(f"  文本已保存: {txt_path}")

                json_path = os.path.join(output_dir, "ocr_result.json")
                cache_payload = {
                    "pdf_name": pdf_name,
                    "total_pages": total_pages,
                    "pages": pages,
                    "full_text": full_text,
                    "all_tables": all_tables,
                    "all_figures": all_figures,
                    "metadata": {
                        "pdf_name": pdf_name,
                        "processed_at": datetime.now().isoformat(),
                        "ocr_engine": "PaddleOCR",
                        "layout_engine": "PPStructure" if PPStructure else "None",
                        "dpi": self.config.pdf_image_dpi,
                        "table_count": len(all_tables),
                        "figure_count": len(all_figures),
                        "text_length": len(full_text),
                    },
                }
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(cache_payload, f, ensure_ascii=False, indent=2)
                print(f"  结构化OCR缓存已保存: {json_path}")

            # 保存表格
            if self.config.save_tables and all_tables:
                html_path = os.path.join(output_dir, "tables.html")
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write("<html><body>\n")
                    for i, tbl in enumerate(all_tables, 1):
                        f.write(f"<h3>表格 {i} (第{tbl['page']}页)</h3>\n")
                        f.write(tbl["html"] + "\n<br><br>\n")
                    f.write("</body></html>")
                print(f"  表格已保存: {html_path}")

            # 构建返回结果
            result = {
                "pdf_name": pdf_name,
                "total_pages": total_pages,
                "pages": pages,
                "full_text": full_text,
                "all_tables": all_tables,
                "all_figures": all_figures,
                "output_dir": output_dir,
                "figures_dir": figures_dir,
                "metadata": {
                    "pdf_name": pdf_name,
                    "processed_at": datetime.now().isoformat(),
                    "ocr_engine": "PaddleOCR",
                    "layout_engine": "PPStructure" if PPStructure else "None",
                    "dpi": self.config.pdf_image_dpi,
                    "table_count": len(all_tables),
                    "figure_count": len(all_figures),
                    "text_length": len(full_text),
                },
            }

            print(f"\n[OK] 提取完成!")
            print(f"  文本长度: {len(full_text)} 字符")
            print(f"  表格数量: {len(all_tables)}")
            print(f"  图片数量: {len(all_figures)}")
            return result

        finally:
            # 清理临时目录
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)


# ============================================================================
# 兼容旧接口: 转换为知识图谱提取格式
# ============================================================================

def build_kg_extraction(ocr_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    将OCR提取结果转换为知识图谱提取的格式
    (与 pdf_agent.py 返回格式兼容)
    """
    # 这里只返回OCR数据，知识图谱提取由LLM完成
    # 这个函数是为了方便接入现有流程
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


# ============================================================================
# 独立运行入口
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="纯OCR版PDF提取器")
    parser.add_argument("pdf_path", help="PDF文件路径")
    parser.add_argument("--dpi", type=int, default=300, help="PDF转图片DPI (默认300)")
    parser.add_argument("--no-gpu", action="store_true", help="禁用GPU")
    parser.add_argument("--output", type=str, default="outputs", help="输出目录")
    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"[ERROR] 文件不存在: {args.pdf_path}")
        sys.exit(1)

    config = OCRExtractorConfig(
        pdf_image_dpi=args.dpi,
        ocr_use_gpu=not args.no_gpu,
        output_dir=args.output,
    )

    extractor = PDFOCRExtractor(config)
    result = extractor.extract(args.pdf_path)

    # 打印摘要
    print(f"\n{'='*60}")
    print("  提取结果摘要")
    print(f"{'='*60}")
    print(f"  PDF名称: {result['pdf_name']}")
    print(f"  总页数: {result['total_pages']}")
    print(f"  文本长度: {result['metadata']['text_length']} 字符")
    print(f"  表格数量: {result['metadata']['table_count']}")
    print(f"  图片数量: {result['metadata']['figure_count']}")
    print(f"  输出目录: {result['output_dir']}")

    # 预览前3页文本
    for p in result["pages"][:3]:
        print(f"\n--- 第 {p['page_number']} 页 ---")
        preview = p["text"][:300].replace("\n", " ")
        print(f"  {preview}{'...' if len(p['text']) > 300 else ''}")
        if p["tables"]:
            print(f"  [表格] {len(p['tables'])} 个")
        if p["figures"]:
            print(f"  [图片] {len(p['figures'])} 个: {[f['filename'] for f in p['figures']]}")
