"""
OCR Worker Script — 运行在 paddle_env 独立环境中
通过 stdin 接收 JSON 任务，通过 stdout 返回 JSON 结果
支持的操作:
  1. ocr_pdf: PDF OCR 提取 (全文+版面分析+图片)
  2. ocr_image: 单张图片 OCR
  3. ocr_ics_ccs: 从 PDF 封面提取 ICS/CCS
"""
import os
import sys
import json
import tempfile
import shutil
import logging
import base64
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
logging.disable(logging.DEBUG)

import cv2
import numpy as np

_OCR_DIR = os.path.dirname(os.path.abspath(__file__))
if _OCR_DIR not in sys.path:
    sys.path.insert(0, _OCR_DIR)
from paddle_compat import create_paddle_ocr, create_pp_structure


def _json_safe(obj: Any) -> Any:
    """将 Paddle/numpy 等不可 JSON 序列化的值转为可 dump 类型，避免 worker 以 exit 1 退出。"""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, (np.integer, np.int64, np.int32, np.uint32, np.uint64)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return str(obj)
    return str(obj)

class OCRWorker:
    def __init__(self, use_gpu=False, dpi=300, use_layout=None):
        self.use_gpu = use_gpu
        self.dpi = dpi
        self.use_layout = use_layout
        self.ocr_engine = None
        self.layout_engine = None
        self._init_engines()

    def _init_engines(self):
        print("[OCR-WORKER] Initializing PaddleOCR...", file=sys.stderr)
        self.ocr_engine = create_paddle_ocr(use_gpu=self.use_gpu, lang="ch", rec_batch_num=4)
        print("[OCR-WORKER] OCR engine ready", file=sys.stderr)

        self.layout_engine = create_pp_structure(use_gpu=self.use_gpu, use_layout=self.use_layout)
        if self.layout_engine is not None:
            print("[OCR-WORKER] Layout engine ready", file=sys.stderr)
        else:
            print("[OCR-WORKER] Layout disabled, pure OCR mode", file=sys.stderr)

    def _pdf_to_images(
        self,
        pdf_path: str,
        temp_dir: str,
        *,
        start_page_1based: int = 1,
        end_page_1based: Optional[int] = None,
    ) -> Tuple[List[str], int]:
        """渲染 PDF 为图片。start_page_1based 默认 1；标准类文档可用环境变量从第 3 页起 OCR 以加速。"""
        import fitz
        doc = fitz.open(pdf_path)
        total = len(doc)
        lo = max(1, int(start_page_1based or 1))
        hi = total if end_page_1based is None else min(total, int(end_page_1based))
        paths: List[str] = []
        for idx in range(lo - 1, hi):
            page = doc[idx]
            mat = fitz.Matrix(self.dpi / 72, self.dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_path = os.path.join(temp_dir, f"page_{idx + 1:04d}.png")
            pix.save(img_path)
            paths.append(img_path)
        doc.close()
        return paths, total

    def _ocr_image_array(self, img_array: np.ndarray) -> str:
        try:
            result = self.ocr_engine.ocr(img_array)
            if not result or not result[0]:
                return ""
            lines = []
            for line in result[0]:
                if line and len(line) >= 2:
                    text_info = line[1]
                    if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                        text, conf = text_info[0], text_info[1]
                        if conf > 0.5 and text.strip():
                            lines.append(text)
                    elif isinstance(text_info, str) and text_info.strip():
                        lines.append(text_info)
            return "\n".join(lines)
        except Exception as e:
            print(f"[OCR-WORKER] OCR failed: {e}", file=sys.stderr)
            return ""

    def _is_valid_figure(self, img: np.ndarray, bbox: list, page_h: int, page_w: int) -> bool:
        x1, y1, x2, y2 = bbox
        area = (x2 - x1) * (y2 - y1)
        page_area = page_h * page_w
        if area < page_area * 0.001:
            return False
        margin_ratio = 0.05
        if (x1 < page_w * margin_ratio or
            x2 > page_w * (1 - margin_ratio) or
            y1 < page_h * margin_ratio or
            y2 > page_h * (1 - margin_ratio)):
            if area < page_area * 0.8:
                return False
        aspect = (x2 - x1) / max(1, y2 - y1)
        if aspect > 5 or aspect < 0.2:
            return False
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        std = np.std(gray)
        if std < 10:
            return False
        return True

    def _analyze_page(self, img_path: str, page_num: int, figures_dir: str, base_name: str = "") -> Dict:
        result = {"text": "", "tables_html": [], "figures": [], "formulas": []}
        img = cv2.imread(img_path)
        if img is None:
            return result

        h, w = img.shape[:2]
        page_area = h * w

        if self.layout_engine is None:
            result["text"] = self._ocr_image_array(img)
            return result

        try:
            layout_res = self.layout_engine(img)
        except Exception as e:
            print(f"[OCR-WORKER] Layout failed (page {page_num}): {e}", file=sys.stderr)
            result["text"] = self._ocr_image_array(img)
            return result

        all_text_parts = []
        fig_counter = 0

        for region in layout_res:
            region_type = region.get("type", "")
            bbox = region.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = map(int, bbox)
            region_area = (x2 - x1) * (y2 - y1)
            cropped = img[y1:y2, x1:x2]
            area_ratio = region_area / max(1, w * h)

            if region_type == "figure" and area_ratio < 0.002:
                continue

            if region_type == "table":
                html = region.get("res", {}).get("html", "")
                if html:
                    result["tables_html"].append({"page": page_num, "html": html, "bbox": list(bbox)})
                    table_text = self._ocr_image_array(cropped)
                    if table_text:
                        all_text_parts.append(table_text)
                else:
                    table_text = self._ocr_image_array(cropped)
                    if table_text:
                        all_text_parts.append(table_text)

            elif region_type == "figure":
                if page_area > 0 and region_area / page_area > 0.9:
                    continue
                if not self._is_valid_figure(cropped, bbox, h, w):
                    continue
                fig_counter += 1
                ocr_text = self._ocr_image_array(cropped)
                desc = ocr_text[:50] if ocr_text else f"Fig{fig_counter}"
                safe_desc = "".join(c if c.isalnum() or c in '_-' else '_' for c in desc)[:40]
                fig_filename = f"{base_name}_p{page_num}_fig{fig_counter}_{safe_desc}.png"
                fig_path = os.path.join(figures_dir, fig_filename)
                ret = cv2.imencode('.png', cropped)
                if ret[0]:
                    with open(fig_path, 'wb') as f:
                        f.write(ret[1].tobytes())
                result["figures"].append({
                    "path": fig_path,
                    "filename": fig_filename,
                    "ocr_text": ocr_text,
                    "description": desc,
                    "image_type": "figure",
                    "bbox": list(bbox),
                    "page": page_num,
                })
                if ocr_text:
                    all_text_parts.append(ocr_text)

            elif region_type in ("text", "title", "header", "footer"):
                region_text = self._ocr_image_array(cropped)
                if region_text:
                    all_text_parts.append(region_text)

            elif region_type == "equation":
                eq_text = self._ocr_image_array(cropped)
                if eq_text:
                    result["formulas"].append({"page": page_num, "latex": eq_text, "bbox": list(bbox)})
                    all_text_parts.append(eq_text)

        full_page_text = self._ocr_image_array(img)
        if full_page_text:
            all_text_parts.append(full_page_text)

        result["text"] = "\n".join(all_text_parts)
        return result

    def ocr_pdf(
        self,
        file_path: str,
        original_filename: str = None,
        output_dir: str = "outputs",
        *,
        start_page_1based: int = 1,
        end_page_1based: Optional[int] = None,
    ) -> Dict[str, Any]:
        filename = original_filename or os.path.basename(file_path)
        base_name = filename.replace(".pdf", "").replace(".docx", "").replace("+", "_")
        out_dir = os.path.join(output_dir, f"ocr_{base_name}")
        figures_dir = os.path.join(out_dir, "figures")
        os.makedirs(figures_dir, exist_ok=True)

        temp_dir = tempfile.mkdtemp(prefix="doc_ocr_pages_")
        result = {
            "file_name": filename,
            "file_type": "pdf",
            "total_pages": 0,
            "ocr_start_page": int(start_page_1based or 1),
            "ocr_end_page": end_page_1based,
            "pages": [],
            "full_text": "",
            "all_tables": [],
            "all_figures": [],
            "all_formulas": [],
        }

        try:
            image_paths, doc_total_pages = self._pdf_to_images(
                file_path,
                temp_dir,
                start_page_1based=start_page_1based,
                end_page_1based=end_page_1based,
            )
            result["total_pages"] = doc_total_pages

            all_text_parts = []
            for rel_i, img_path in enumerate(image_paths):
                page_num = start_page_1based + rel_i
                page_info = self._analyze_page(img_path, page_num, figures_dir, base_name)
                result["pages"].append({
                    "page_num": page_num,
                    "text": page_info["text"],
                    "tables": page_info["tables_html"],
                    "figures": page_info["figures"],
                    "formulas": page_info["formulas"],
                })
                if page_info["text"]:
                    all_text_parts.append(f"===== Page {page_num} =====\n{page_info['text']}")
                for tbl in page_info["tables_html"]:
                    result["all_tables"].append(tbl)
                for fig in page_info["figures"]:
                    result["all_figures"].append(fig)
                for frm in page_info["formulas"]:
                    result["all_formulas"].append(frm)

            result["full_text"] = "\n\n".join(all_text_parts)
            return result
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def extract_images_only(self, pdf_path: str, output_dir: str, original_filename: str = None) -> Dict[str, Any]:
        filename = original_filename or os.path.basename(pdf_path)
        base_name = filename.replace(".pdf", "").replace("+", "_")
        figures_dir = os.path.join(output_dir, f"quick_{base_name}", "figures")
        os.makedirs(figures_dir, exist_ok=True)

        temp_dir = tempfile.mkdtemp(prefix="doc_img_extract_")
        all_figures = []
        image_paths: List[str] = []
        doc_total = 0

        try:
            image_paths, doc_total = self._pdf_to_images(pdf_path, temp_dir, start_page_1based=1, end_page_1based=None)
            fig_counter = 0

            for idx, img_path in enumerate(image_paths, 1):
                img = cv2.imread(img_path)
                if img is None:
                    continue
                h, w = img.shape[:2]

                if self.layout_engine is None:
                    continue

                try:
                    layout_res = self.layout_engine(img)
                except Exception as e:
                    print(f"[OCR-WORKER] Layout failed for image extraction (page {idx}): {e}", file=sys.stderr)
                    continue

                for region in (layout_res or []):
                    if region.get("type", "") != "figure":
                        continue
                    bbox = region.get("bbox")
                    if not bbox or len(bbox) != 4:
                        continue
                    x1, y1, x2, y2 = map(int, bbox)
                    cropped = img[y1:y2, x1:x2]
                    if not self._is_valid_figure(cropped, bbox, h, w):
                        continue

                    fig_counter += 1
                    ocr_text = self._ocr_image_array(cropped)
                    desc = ocr_text[:50] if ocr_text else f"Fig{fig_counter}"
                    safe_desc = "".join(c if c.isalnum() or c in '_-' else '_' for c in desc)[:40]
                    fig_filename = f"{base_name}_p{idx}_fig{fig_counter}_{safe_desc}.png"
                    fig_path = os.path.join(figures_dir, fig_filename)
                    ret = cv2.imencode('.png', cropped)
                    if ret[0]:
                        with open(fig_path, 'wb') as f:
                            f.write(ret[1].tobytes())
                    all_figures.append({
                        "path": fig_path,
                        "filename": fig_filename,
                        "ocr_text": ocr_text,
                        "description": desc,
                        "image_type": "figure",
                        "bbox": list(bbox),
                        "page": idx,
                    })
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return {
            "file_name": filename,
            "total_pages": doc_total or len(image_paths),
            "all_figures": all_figures,
            "figures_dir": figures_dir,
        }

    def ocr_image(self, image_path: str) -> str:
        img = cv2.imread(image_path)
        if img is None:
            return ""
        return self._ocr_image_array(img)

    def ocr_ics_ccs(self, pdf_path: str) -> Dict[str, Any]:
        result = {'ics': '', 'ccs': '', 'needs_manual': True}
        try:
            import fitz
            doc = fitz.open(pdf_path)
            page = doc[0]
            mat = fitz.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_path = os.path.join(tempfile.gettempdir(), "ics_ccs_page.png")
            pix.save(img_path)
            doc.close()

            img = cv2.imread(img_path)
            if img is None:
                os.unlink(img_path)
                return result

            h, w = img.shape[:2]
            crop_h = int(h * 0.15)
            crop_w = int(w * 0.45)
            cropped = img[:crop_h, :crop_w]

            ocr_result = self.ocr_engine.ocr(cropped)
            os.unlink(img_path)

            if not ocr_result or not ocr_result[0]:
                return result

            lines = []
            for line in ocr_result[0]:
                if line and len(line) >= 2:
                    text_info = line[1]
                    if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                        text, conf = text_info[0], text_info[1]
                        if conf > 0.4 and text.strip():
                            lines.append(text.strip())

            if len(lines) < 2:
                return result

            import re
            ics_pattern = re.compile(r'ICS\s*[\d.]+', re.IGNORECASE)
            ccs_pattern = re.compile(r'[A-Z]\s*\d+', re.IGNORECASE)

            first_line = lines[0]
            second_line = lines[1] if len(lines) > 1 else ''

            ics_match = ics_pattern.search(first_line)
            if ics_match:
                result['ics'] = ics_match.group().strip()

            ccs_match = ccs_pattern.search(second_line)
            if ccs_match:
                result['ccs'] = ccs_match.group().strip()

            result['needs_manual'] = not (result['ics'] and result['ccs'])
        except Exception as e:
            print(f"[OCR-WORKER] ICS/CCS extraction failed: {e}", file=sys.stderr)
        return result


def _emit_json(obj: Dict[str, Any]) -> None:
    payload = json.dumps(_json_safe(obj), ensure_ascii=False) + "\n"
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(payload.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    else:
        sys.stdout.write(payload)
        sys.stdout.flush()


def main():
    if len(sys.argv) < 2:
        _emit_json({"success": False, "error": "Usage: ocr_worker.py <command_json>"})
        sys.exit(1)

    command_file = sys.argv[1]
    with open(command_file, 'r', encoding='utf-8') as f:
        command = json.load(f)

    action = command.get("action", "")

    try:
        if action == "ocr_pdf":
            use_layout = command.get("use_layout")
            if use_layout is not None:
                use_layout = bool(use_layout)
            worker = OCRWorker(
                use_gpu=command.get("use_gpu", False),
                dpi=int(command.get("dpi") or 300),
                use_layout=use_layout,
            )
            sp = int(command.get("start_page") or os.environ.get("FLGL_OCR_START_PAGE", "1") or 1)
            ep = command.get("end_page")
            ep_i = int(ep) if ep not in (None, "") else None
            result = worker.ocr_pdf(
                file_path=command["file_path"],
                original_filename=command.get("original_filename"),
                output_dir=command.get("output_dir", "outputs"),
                start_page_1based=max(1, sp),
                end_page_1based=ep_i,
            )
            _emit_json({"success": True, "data": result})

        elif action == "ocr_image":
            worker = OCRWorker(use_gpu=command.get("use_gpu", False))
            text = worker.ocr_image(command["image_path"])
            _emit_json({"success": True, "data": {"text": text}})

        elif action == "ocr_ics_ccs":
            worker = OCRWorker(use_gpu=command.get("use_gpu", False))
            result = worker.ocr_ics_ccs(command["file_path"])
            _emit_json({"success": True, "data": result})

        elif action == "extract_images_only":
            worker = OCRWorker(
                use_gpu=command.get("use_gpu", False),
                dpi=int(command.get("dpi") or 300),
            )
            result = worker.extract_images_only(
                pdf_path=command["file_path"],
                output_dir=command.get("output_dir", "outputs"),
                original_filename=command.get("original_filename"),
            )
            _emit_json({"success": True, "data": result})

        else:
            _emit_json({"success": False, "error": f"Unknown action: {action}"})
            sys.exit(1)

    except Exception as e:
        _emit_json({"success": False, "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
