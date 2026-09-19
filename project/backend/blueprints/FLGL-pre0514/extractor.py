import os
import sys
import io
import json
import logging
import tempfile
import shutil
from typing import List, Dict, Any, Optional
from datetime import datetime

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
os.environ["FLAGS_enable_pir_api"] = "0"

import cv2
import numpy as np

# NumPy 2.x 兼容补丁：部分旧版 OCR / 图像依赖仍会访问 np.sctypes
if not hasattr(np, 'sctypes'):
    np.sctypes = {
        'int': [np.int8, np.int16, np.int32, np.int64],
        'uint': [np.uint8, np.uint16, np.uint32, np.uint64],
        'float': [np.float16, np.float32, np.float64],
        'complex': [np.complex64, np.complex128],
        'others': [np.bool_, np.object_, np.str_, np.bytes_],
    }

from PIL import Image
import docx
from docx.oxml.ns import qn

logger = logging.getLogger(__name__)


def _ensure_paddleocr():
    try:
        from paddleocr import PaddleOCR
        return True
    except (ImportError, TypeError):
        logger.error("PaddleOCR not available. Install: pip install protobuf==3.20.3 paddlepaddle paddleocr pdf2image")
        return False


class DocumentExtractor:

    def __init__(self, dpi=300, use_gpu=False, output_dir="outputs"):
        self.dpi = dpi
        self.use_gpu = use_gpu
        self.output_dir = output_dir
        self.ocr_engine = None
        self.layout_engine = None
        self._init_engines()

    def _init_engines(self):
        try:
            from paddleocr import PaddleOCR
            logger.info("Initializing PaddleOCR text recognition engine...")
            self.ocr_engine = PaddleOCR(
                use_angle_cls=True,
                lang="ch",
            )
        except Exception as e:
            logger.warning(f"PaddleOCR init failed: {e}, text extraction will be limited")
            self.ocr_engine = None
        try:
            from paddleocr import PPStructure
            self.layout_engine = PPStructure()
            logger.info("PPStructure layout analysis engine ready")
        except Exception as e:
            logger.warning(f"PPStructure init failed: {e}, will skip layout analysis")
            self.layout_engine = None

    def _pdf_to_images(self, pdf_path: str, temp_dir: str) -> List[str]:
        import fitz
        doc = fitz.open(pdf_path)
        paths = []
        for idx in range(len(doc)):
            page = doc[idx]
            mat = fitz.Matrix(self.dpi / 72, self.dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_path = os.path.join(temp_dir, f"page_{idx + 1:04d}.png")
            pix.save(img_path)
            paths.append(img_path)
        doc.close()
        return paths

    def _ocr_image_array(self, img_array: np.ndarray) -> str:
        if self.ocr_engine is None:
            return ""
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
            logger.warning(f"OCR failed: {e}")
            return ""

    def _ocr_image_file(self, img_path: str) -> str:
        try:
            img = cv2.imread(img_path)
            if img is None:
                logger.warning(f"Failed to read image: {img_path}")
                return ""
            return self._ocr_image_array(img)
        except Exception as e:
            logger.warning(f"OCR on file failed: {e}")
            return ""

    def _analyze_page(self, img_path: str, page_num: int, figures_dir: str, base_name: str = "") -> Dict:
        result = {"text": "", "tables_html": [], "figures": [], "formulas": []}
        img = cv2.imread(img_path)
        if img is None:
            logger.warning(f"[Image] Failed to read page image: {img_path}")
            return result

        h, w = img.shape[:2]
        page_area = h * w
        logger.info(f"[Image] Page {page_num}: {w}x{h}, area={page_area}")

        if self.layout_engine is None:
            logger.info(f"[Image] Page {page_num}: No layout engine, using full-page OCR")
            result["text"] = self._ocr_image_array(img)
            return result

        try:
            layout_res = self.layout_engine(img)
        except Exception as e:
            logger.warning(f"Layout analysis failed (page {page_num}): {e}")
            result["text"] = self._ocr_image_array(img)
            return result

        all_text_parts = []
        fig_counter = len(result["figures"])

        for region in layout_res:
            region_type = region.get("type", "")
            bbox = region.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = map(int, bbox)
            region_area = (x2 - x1) * (y2 - y1)
            cropped = img[y1:y2, x1:x2]

            page_area = (y2 - y1) * (x2 - x1)
            page_h, page_w = h, w
            area_ratio = region_area / max(1, page_w * page_h)

            if region_type == "figure" and not self._should_keep_region(area_ratio, page_w, page_h, (x1, y1, x2, y2)):
                logger.debug(f"Skipping figure region (ratio={area_ratio:.4f})")
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
                    logger.info(f"[Image] Page {page_num}: Skipping full-page figure (area ratio={region_area/page_area:.2f})")
                    continue
                fig_counter += 1
                ocr_text = self._ocr_image_array(cropped)
                desc = ocr_text[:50] if ocr_text else f"图{fig_counter}"
                safe_desc = "".join(c if c.isalnum() or c in '_-' else '_' for c in desc)[:30]
                fig_filename = f"{base_name}_page{page_num}_图{fig_counter}_{safe_desc}.png"
                fig_path = os.path.join(figures_dir, fig_filename)
                cv2.imwrite(fig_path, cropped)
                logger.info(f"[Image] Page {page_num}: Figure #{fig_counter} saved to {fig_path}, "
                            f"ocr_text_len={len(ocr_text)}, bbox={bbox}")
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
                    result["formulas"].append({
                        "page": page_num,
                        "latex": eq_text,
                        "bbox": list(bbox),
                    })
                    all_text_parts.append(eq_text)

        full_page_text = self._ocr_image_array(img)
        if full_page_text:
            all_text_parts.append(full_page_text)

        result["text"] = "\n".join(all_text_parts)
        return result

    @staticmethod
    def _should_keep_region(area_ratio: float, page_w: int, page_h: int, bbox: tuple) -> bool:
        min_ratio = 0.002
        max_ratio = 0.85
        if not (min_ratio <= area_ratio <= max_ratio):
            return False

        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width < page_w * 0.1 and height < page_h * 0.1:
            return False

        margin_threshold = 0.02
        x1, y1, x2, y2 = bbox
        if (x1 < page_w * margin_threshold or y1 < page_h * margin_threshold or
                x2 > page_w * (1 - margin_threshold) or y2 > page_h * (1 - margin_threshold)):
            # If figure touches page borders but is very small, treat as watermark/icon
            if area_ratio < 0.05:
                return False

        return True

    def _extract_word(self, docx_path: str, figures_dir: str) -> Dict[str, Any]:
        doc = docx.Document(docx_path)
        pages = []
        all_text_parts = []
        all_tables = []
        all_figures = []
        all_formulas = []
        full_text = ""
        page_num = 1

        current_page_text = []
        current_page_tables = []
        current_page_figures = []
        current_page_formulas = []

        def flush_page():
            nonlocal page_num
            text = "\n".join(current_page_text)
            pages.append({
                "page_num": page_num,
                "text": text,
                "tables": current_page_tables[:],
                "figures": current_page_figures[:],
                "formulas": current_page_formulas[:],
            })
            if text:
                all_text_parts.append(f"===== Page {page_num} =====\n{text}")
            all_tables.extend(current_page_tables)
            all_figures.extend(current_page_figures)
            all_formulas.extend(current_page_formulas)
            page_num += 1
            current_page_text.clear()
            current_page_tables.clear()
            current_page_figures.clear()
            current_page_formulas.clear()

        for element in doc.element.body:
            tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag

            if tag == "tbl":
                self._process_word_table(element, current_page_tables, page_num)
                table_text = self._table_element_to_text(element)
                if table_text:
                    current_page_text.append(table_text)
            elif tag == "p":
                self._process_word_paragraph(element, current_page_text,
                                              current_page_figures, figures_dir, page_num)
            elif tag == "sectPr":
                flush_page()

        if current_page_text or current_page_tables or current_page_figures:
            flush_page()

        full_text = "\n\n".join(all_text_parts)

        return {
            "file_name": os.path.basename(docx_path),
            "file_type": "word",
            "total_pages": len(pages),
            "pages": pages,
            "full_text": full_text,
            "all_tables": all_tables,
            "all_figures": all_figures,
            "all_formulas": all_formulas,
        }

    def _process_word_paragraph(self, p_element, text_list: list, figures_list: list,
                                 figures_dir: str, page_num: int):
        runs = p_element.findall(".//" + qn("w:r"))
        para_text = ""
        fig_count = 0

        for run in runs:
            blips = run.findall(".//" + qn("a:blip"))
            for blip in blips:
                embed_id = blip.get(qn("r:embed"))
                if embed_id:
                    fig_count += 1
                    fig_filename = f"page{page_num}_fig{fig_count}.png"
                    fig_path = os.path.join(figures_dir, fig_filename)
                    figures_list.append({
                        "path": fig_path,
                        "filename": fig_filename,
                        "ocr_text": "",
                        "page": page_num,
                    })

            texts = run.findall(".//" + qn("w:t"))
            for t_elem in texts:
                if t_elem.text:
                    para_text += t_elem.text

        style_elem = p_element.find(qn("w:pPr") + "/" + qn("w:pStyle"))
        if style_elem is not None:
            style_val = style_elem.get(qn("w:val"), "")
            if "Heading" in style_val:
                para_text = f"\n# {para_text}\n"

        if para_text.strip():
            text_list.append(para_text.strip())

    def _process_word_table(self, tbl_element, tables_list: list, page_num: int):
        rows = tbl_element.findall(qn("w:tr"))
        md_rows = []
        html_cells = []

        for row in rows:
            cells = row.findall(qn("w:tc"))
            md_row = []
            html_row = []
            for cell in cells:
                texts = cell.findall(".//" + qn("w:t"))
                cell_text = "".join(t.text or "" for t in texts)
                md_row.append(cell_text)
                html_row.append(f"<td>{cell_text}</td>")
            md_rows.append(md_row)
            html_cells.append(f"<tr>{''.join(html_row)}</tr>")

        if md_rows:
            md = self._md_rows_to_markdown(md_rows)
            html = f"<table>{''.join(html_cells)}</table>"
            tables_list.append({
                "page": page_num,
                "html": html,
                "content_markdown": md,
            })

    def _md_rows_to_markdown(self, rows: list) -> str:
        if not rows:
            return ""
        lines = []
        lines.append("| " + " | ".join(rows[0]) + " |")
        lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    def _table_element_to_text(self, tbl_element) -> str:
        rows = tbl_element.findall(qn("w:tr"))
        lines = []
        for row in rows:
            cells = row.findall(qn("w:tc"))
            cell_texts = []
            for cell in cells:
                texts = cell.findall(".//" + qn("w:t"))
                cell_texts.append("".join(t.text or "" for t in texts))
            lines.append(" | ".join(cell_texts))
        return "\n".join(lines)

    def _extract_word_images(self, docx_path: str, figures_dir: str, base_name: str = "") -> List[Dict]:
        images = []
        try:
            doc = docx.Document(docx_path)
            rels = doc.part.rels
            img_idx = 0
            for rel_id, rel in rels.items():
                if "image" in rel.reltype:
                    img_idx += 1
                    blob = rel.target_part.blob
                    img = Image.open(io.BytesIO(blob))
                    ext = rel.target_part.content_type.split("/")[-1].lower()
                    if ext == "jpeg":
                        ext = "jpg"
                    fig_filename = f"{base_name}_图{img_idx}.{ext}"
                    fig_path = os.path.join(figures_dir, fig_filename)
                    img.save(fig_path)
                    images.append({
                        "path": fig_path,
                        "filename": fig_filename,
                        "ocr_text": "",
                        "description": f"图{img_idx}",
                        "image_type": "figure",
                        "page": 0,
                    })
        except Exception as e:
            logger.warning(f"Failed to extract Word images: {e}")
        return images

    def extract(self, file_path: str, original_filename: str = None) -> Dict[str, Any]:
        ext = os.path.splitext(file_path)[1].lower()
        is_word = ext in (".docx", ".doc")
        filename = original_filename or os.path.basename(file_path)

        logger.info(f"Starting extraction: {filename} ({'Word' if is_word else 'PDF'})")

        base_name = filename.replace(".pdf", "").replace(".docx", "").replace("+", "_")
        output_dir = os.path.join(self.output_dir, f"ocr_{base_name}")
        figures_dir = os.path.join(output_dir, "figures")
        os.makedirs(figures_dir, exist_ok=True)

        if is_word:
            result = self._extract_word(file_path, figures_dir)
            word_images = self._extract_word_images(file_path, figures_dir, base_name)
            result["all_figures"].extend(word_images)
            logger.info(f"Word extraction complete: {result['total_pages']} logical pages, "
                        f"{len(result['all_tables'])} tables, "
                        f"{len(result['all_figures'])} figures, "
                        f"{len(result['all_formulas'])} formulas")
            return result

        temp_dir = tempfile.mkdtemp(prefix="doc_ocr_pages_")
        result = {
            "file_name": filename,
            "file_type": "pdf",
            "total_pages": 0,
            "pages": [],
            "full_text": "",
            "all_tables": [],
            "all_figures": [],
            "all_formulas": [],
        }

        try:
            image_paths = self._pdf_to_images(file_path, temp_dir)
            result["total_pages"] = len(image_paths)
            logger.info(f"Converted to {len(image_paths)} page images")

            all_text_parts = []
            for idx, img_path in enumerate(image_paths, 1):
                page_info = self._analyze_page(img_path, idx, figures_dir, base_name)
                result["pages"].append({
                    "page_num": idx,
                    "text": page_info["text"],
                    "tables": page_info["tables_html"],
                    "figures": page_info["figures"],
                    "formulas": page_info["formulas"],
                })
                if page_info["text"]:
                    all_text_parts.append(f"===== Page {idx} =====\n{page_info['text']}")
                for tbl in page_info["tables_html"]:
                    result["all_tables"].append(tbl)
                for fig in page_info["figures"]:
                    result["all_figures"].append(fig)
                for frm in page_info["formulas"]:
                    result["all_formulas"].append(frm)

            result["full_text"] = "\n\n".join(all_text_parts)
            logger.info(f"Extraction complete: {len(result['pages'])} pages, "
                        f"{len(result['all_tables'])} tables, "
                        f"{len(result['all_figures'])} figures, "
                        f"{len(result['all_formulas'])} formulas")
            return result

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def extract_images_only(self, pdf_path: str, figures_dir: str) -> List[Dict]:
        import fitz
        images = []
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images()
                for img_idx, img in enumerate(image_list):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    if base_image:
                        image_bytes = base_image["image"]
                        image_ext = base_image.get("ext", "png")
                        if image_ext == "jpeg":
                            image_ext = "jpg"
                        filename = f"page{page_num+1}_img{img_idx+1}.{image_ext}"
                        filepath = os.path.join(figures_dir, filename)
                        with open(filepath, "wb") as f:
                            f.write(image_bytes)
                        images.append({
                            "path": filepath,
                            "filename": filename,
                            "ocr_text": "",
                            "description": f"第{page_num+1}页图片{img_idx+1}",
                            "image_type": "embedded_image",
                            "page": page_num + 1,
                        })
            doc.close()
            logger.info(f"Extracted {len(images)} embedded images from PDF")
        except Exception as e:
            logger.warning(f"Failed to extract embedded images: {e}")
        return images


def chunk_document(full_text: str, pages_data: list,
                   max_chars: int = 1000, overlap: int = 200) -> List[Dict[str, Any]]:
    """
    Split document text into chunks, keeping tables and formulas intact.
    Tables are emitted as separate chunks.
    """
    chunks = []
    current_text = ""
    current_start = 0
    current_tables = []
    current_formulas = []
    current_page_refs = []
    
    if not full_text or not full_text.strip():
        logger.warning("[Chunk] Empty full_text provided")
        return chunks
    
    if not pages_data:
        logger.warning("[Chunk] Empty pages_data, creating single chunk from full_text")
        chunks.append({
            "text": full_text.strip(),
            "start_pos": 0,
            "end_pos": len(full_text),
            "tables": [],
            "formulas": [],
            "page_refs": [1],
        })
        return chunks

    for page_info in pages_data:
        page_num = page_info["page_num"]
        page_text = page_info.get("text", "")

        for tbl in page_info.get("tables", []):
            current_tables.append({
                "page": page_num,
                "html": tbl.get("html", ""),
            })
        for frm in page_info.get("formulas", []):
            current_formulas.append({
                "page": page_num,
                "latex": frm.get("latex", ""),
            })

        if not page_text.strip():
            continue

        current_page_refs.append(page_num)
        paragraphs = page_text.split("\n\n")

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_text) + len(para) > max_chars and current_text:
                chunks.append({
                    "text": current_text.strip(),
                    "start_pos": current_start,
                    "end_pos": current_start + len(current_text),
                    "tables": current_tables[:],
                    "formulas": current_formulas[:],
                    "page_refs": list(set(current_page_refs)),
                })
                overlap_text = current_text[-overlap:] if len(current_text) > overlap else current_text
                current_text = overlap_text + "\n\n" + para
                current_start = current_start + len(current_text) - len(para) - overlap
                current_tables = []
                current_formulas = []
                current_page_refs = [page_num]
            else:
                current_text += ("\n\n" if current_text else "") + para

        if current_tables:
            for tbl in current_tables:
                chunks.append({
                    "text": f"[Table] Page {tbl['page']}\n{tbl['html']}",
                    "start_pos": -1,
                    "end_pos": -1,
                    "tables": [tbl],
                    "formulas": [],
                    "page_refs": [tbl["page"]],
                })
            current_tables = []

    if current_text.strip():
        chunks.append({
            "text": current_text.strip(),
            "start_pos": current_start,
            "end_pos": current_start + len(current_text),
            "tables": current_tables,
            "formulas": current_formulas,
            "page_refs": list(set(current_page_refs)),
        })

    logger.info(f"Document chunked into {len(chunks)} blocks")
    return chunks
