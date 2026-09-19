import os
import sys
import io
import json
import logging
import tempfile
import shutil
from typing import List, Dict, Any, Optional
from datetime import datetime

import cv2
import numpy as np
from PIL import Image
import docx
from docx.oxml.ns import qn

from ocr import run_ocr_pdf, run_extract_images_only

logger = logging.getLogger(__name__)


class DocumentExtractor:

    def __init__(self, dpi=300, use_gpu: Optional[bool] = None, output_dir="outputs"):
        self.dpi = dpi
        self.use_gpu = use_gpu
        self.output_dir = output_dir

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

        import fitz
        doc = fitz.open(file_path)
        total_pages = len(doc)
        pages_data = []
        all_text_parts = []
        all_tables = []
        all_formulas = []

        for idx in range(total_pages):
            page = doc[idx]
            page_text = page.get_text() or ""
            pages_data.append({
                "page_num": idx + 1,
                "text": page_text,
                "tables": [],
                "figures": [],
                "formulas": [],
            })
            if page_text.strip():
                all_text_parts.append(f"===== Page {idx + 1} =====\n{page_text}")
        doc.close()

        img_result = run_extract_images_only(
            file_path=file_path,
            output_dir=self.output_dir,
            original_filename=filename,
            use_gpu=self.use_gpu,
            dpi=min(self.dpi, 200),
            timeout=600,
        )

        all_figures = []
        if img_result.get("success"):
            all_figures = img_result["data"].get("all_figures", [])
            for fig in all_figures:
                fig_page = fig.get("page", 1)
                if 1 <= fig_page <= len(pages_data):
                    pages_data[fig_page - 1]["figures"].append(fig)

        result = {
            "file_name": filename,
            "file_type": "pdf",
            "total_pages": total_pages,
            "pages": pages_data,
            "full_text": "\n\n".join(all_text_parts),
            "all_tables": all_tables,
            "all_figures": all_figures,
            "all_formulas": all_formulas,
        }

        logger.info(f"Extraction complete: {result['total_pages']} pages, "
                    f"{len(result['all_tables'])} tables, "
                    f"{len(result['all_figures'])} figures, "
                    f"{len(result['all_formulas'])} formulas")
        return result

    def extract_images_only(self, pdf_path: str, figures_dir: str) -> List[Dict]:
        result = run_extract_images_only(
            file_path=pdf_path,
            output_dir=self.output_dir,
            use_gpu=self.use_gpu,
            dpi=self.dpi,
            timeout=300,
        )
        if not result.get("success"):
            logger.warning(f"PPStructure image extraction failed: {result.get('error')}")
            return []
        data = result["data"]
        figures = data.get("all_figures", [])
        logger.info(f"Extracted {len(figures)} content images via PPStructure layout analysis")
        return figures


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

    for page_idx, page_info in enumerate(pages_data, start=1):
        page_num = page_info.get("page_num")
        if page_num is None:
            page_num = page_info.get("page_number")
        if page_num is None:
            page_num = page_idx
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
