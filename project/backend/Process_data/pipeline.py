import os
import sys
import re
import io
import json
import logging
import tempfile
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import fitz
import pdfplumber
from PIL import Image
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from Database.Chroma.input import ChromaInputManager
from Database.minIo.input import MinIOInputManager

logger = logging.getLogger(__name__)

STANDARD_ID_MAP = {
    "GBT38924.1": ("std001", "总则"),
    "GBT38924.2": ("std002", "低温试验"),
    "GBT38924.3": ("std003", "高温试验"),
    "GBT38924.4": ("std004", "温度和高度试验"),
    "GBT38924.5": ("std005", "冲击试验"),
    "GBT38924.6": ("std006", "振动试验"),
    "GBT38924.7": ("std007", "湿热试验"),
    "GBT38924.8": ("std008", "盐雾试验"),
    "GBT38924.9": ("std009", "防水性试验"),
    "GBT38924.10": ("std010", "砂尘试验"),
    "GBT38924.11": ("std011", "霉菌试验"),
}

NEO4J_METHOD_MAP = {
    "std001": "GB/T 38924.1-2020",
    "std002": "GB/T 38924.2-2020",
    "std003": "GB/T 38924.3-2020",
    "std004": "GB/T 38924.4-2020",
    "std005": "GB/T 38924.5-2020",
    "std006": "GB/T 38924.6-2020",
    "std007": "GB/T 38924.7-2020",
    "std008": "GB/T 38924.8-2020",
    "std009": "GB/T 38924.9-2020",
    "std010": "GB/T 38924.10-2020",
    "std011": "GB/T 38924.11-2023",
}


def _extract_standard_info(filename: str) -> Tuple[str, str]:
    """从文件名提取标准ID和名称"""
    base = filename.replace(".pdf", "").replace("+", "").replace("-", "").replace("_", "")
    for key, (doc_id, title) in STANDARD_ID_MAP.items():
        if key in base:
            return doc_id, title
    doc_id = f"std_{hashlib.md5(filename.encode()).hexdigest()[:6]}"
    return doc_id, filename.replace(".pdf", "")


def parse_pdf(pdf_path: str) -> dict:
    """
    解析PDF，提取文本、表格、图片
    使用pdfplumber提取文本和表格，PyMuPDF提取图片
    """
    logger.info(f"开始解析PDF: {pdf_path}")
    result = {
        "title": "",
        "standard_id": "",
        "pages": [],
        "full_text": "",
        "all_tables": [],
        "all_images": [],
    }

    filename = os.path.basename(pdf_path)
    doc_id, title = _extract_standard_info(filename)
    result["title"] = title
    result["standard_id"] = doc_id

    # --- pdfplumber: 文本 + 表格 ---
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                page_data = {
                    "page_num": page_num,
                    "text": page.extract_text() or "",
                    "tables": [],
                    "formulas": [],
                    "images": [],
                }

                # 提取表格
                tables = page.extract_tables()
                for tbl_idx, table in enumerate(tables):
                    if table:
                        md = _table_to_markdown(table)
                        bbox = table[0] if table else None
                        page_data["tables"].append({
                            "table_index": tbl_idx,
                            "bbox": bbox,
                            "content_markdown": md,
                        })
                        result["all_tables"].append({
                            "page": page_num,
                            "table_index": tbl_idx,
                            "content_markdown": md,
                        })

                result["pages"].append(page_data)
                result["full_text"] += f"\n===== Page {page_num} =====\n{page_data['text']}\n"

    except Exception as e:
        logger.error(f"pdfplumber 提取失败: {e}")

    # --- PyMuPDF: 提取图片 ---
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            images = page.get_images(full=True)
            for img_idx, img in enumerate(images):
                xref = img[0]
                base_image = doc.extract_image(xref)
                if base_image:
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    pil_img = Image.open(io.BytesIO(image_bytes))
                    result["pages"][page_num]["images"].append({
                        "image_index": img_idx,
                        "pil_image": pil_img,
                        "ext": image_ext,
                        "width": pil_img.width,
                        "height": pil_img.height,
                    })
                    result["all_images"].append({
                        "page": page_num + 1,
                        "image_index": img_idx,
                        "pil_image": pil_img,
                        "ext": image_ext,
                    })
        doc.close()
    except Exception as e:
        logger.error(f"PyMuPDF 图片提取失败: {e}")

    # --- OCR补充（扫描识别） ---
    try:
        from ocr import run_ocr_pdf
        ocr_result = run_ocr_pdf(pdf_path, output_dir=Config.OUTPUT_DIR)
        if ocr_result.get("success"):
            ocr_text = ocr_result["data"].get("full_text", "")
            if len(ocr_text) > len(result["full_text"]) * 0.5:
                logger.info("OCR提取文本更多，使用OCR结果补充")
                result["full_text"] = ocr_text
                for fig in ocr_result["data"].get("all_figures", []):
                    result["all_images"].append({
                        "page": fig.get("page", 0),
                        "path": fig.get("path", ""),
                        "ocr_text": fig.get("ocr_text", ""),
                    })
    except Exception as e:
        logger.warning(f"OCR补充失败: {e}")

    logger.info(f"PDF解析完成: {len(result['pages'])}页, {len(result['all_tables'])}表格, {len(result['all_images'])}图片")
    return result


def _table_to_markdown(table: list) -> str:
    """将pdfplumber表格转为Markdown格式"""
    if not table:
        return ""
    lines = []
    header = table[0]
    lines.append("| " + " | ".join(str(c or "") for c in header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in table[1:]:
        lines.append("| " + " | ".join(str(c or "") for c in row) + " |")
    return "\n".join(lines)


def chunk_document(full_text: str, pages_data: list, max_chars: int = 1000, overlap: int = 200) -> List[Dict[str, Any]]:
    """
    文本分块，保持表格和公式完整性
    返回: [{"text": str, "start_pos": int, "end_pos": int, "tables": list, "formulas": list, "page_refs": list}]
    """
    chunks = []
    current_text = ""
    current_start = 0
    current_tables = []
    current_formulas = []
    current_page_refs = []

    for page_info in pages_data:
        page_num = page_info["page_num"]
        page_text = page_info["text"]

        # 收集该页的表格和公式
        for tbl in page_info.get("tables", []):
            current_tables.append({
                "page": page_num,
                "markdown": tbl["content_markdown"],
            })
        for frm in page_info.get("formulas", []):
            current_formulas.append({
                "page": page_num,
                "latex": frm.get("latex", ""),
            })

        if not page_text.strip():
            continue

        current_page_refs.append(page_num)

        # 按段落分割
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
                # 保留重叠部分
                overlap_text = current_text[-overlap:] if len(current_text) > overlap else current_text
                current_text = overlap_text + "\n\n" + para
                current_start = current_start + len(current_text) - len(para) - overlap
                current_tables = []
                current_formulas = []
                current_page_refs = [page_num]
            else:
                current_text += ("\n\n" if current_text else "") + para

        # 每页结束后，将表格作为独立chunk
        if current_tables:
            for tbl in current_tables:
                chunks.append({
                    "text": f"[表格] Page {tbl['page']}\n{tbl['markdown']}",
                    "start_pos": -1,
                    "end_pos": -1,
                    "tables": [tbl],
                    "formulas": [],
                    "page_refs": [tbl["page"]],
                })
            current_tables = []

    # 最后一个chunk
    if current_text.strip():
        chunks.append({
            "text": current_text.strip(),
            "start_pos": current_start,
            "end_pos": current_start + len(current_text),
            "tables": current_tables,
            "formulas": current_formulas,
            "page_refs": list(set(current_page_refs)),
        })

    logger.info(f"文本分块完成: {len(chunks)}个块")
    return chunks


def save_pdf_to_minio(minio_manager: MinIOInputManager, pdf_path: str, doc_id: str, title: str) -> str:
    """上传原始PDF到MinIO"""
    result = minio_manager.upload_file(
        file_path=pdf_path,
        doc_id=doc_id,
        title=title,
        category="标准",
        folder_name=doc_id,
    )
    if result["success"]:
        return result["data"]["object_name"]
    raise RuntimeError(f"PDF上传MinIO失败: {result.get('error')}")


def save_image_to_minio(minio_manager: MinIOInputManager, image: Image.Image, bucket: str,
                        object_path: str, metadata: dict) -> str:
    """保存图片到MinIO"""
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, os.path.basename(object_path))
    image.save(temp_path, format="PNG")
    try:
        minio_manager.client.fput_object(
            bucket, object_path, temp_path,
            metadata={k: str(v) for k, v in metadata.items()}
        )
        return object_path
    finally:
        os.unlink(temp_path)
        os.rmdir(temp_dir)


def extract_and_save_images(minio_manager: MinIOInputManager, pdf_data: dict,
                            bucket: str, doc_id: str, title: str) -> List[str]:
    """提取PDF中所有图片并保存到MinIO，返回MinIO路径列表"""
    image_paths = []
    standard_folder = doc_id

    for page_info in pdf_data["pages"]:
        page_num = page_info["page_num"]
        for img_info in page_info.get("images", []):
            pil_img = img_info.get("pil_image")
            if pil_img is None:
                continue
            img_idx = img_info["image_index"]
            ext = img_info.get("ext", "png")
            img_type = "figure"
            filename = f"page{page_num}_img{img_idx}.{ext}"
            object_path = f"标准/{standard_folder}/images/{filename}"

            metadata = {
                "x-amz-meta-doc-id": doc_id,
                "x-amz-meta-title": title,
                "x-amz-meta-category": "标准",
                "x-amz-meta-file-type": ext,
                "x-amz-meta-source-path": object_path,
                "x-amz-meta-upload-time": datetime.now(timezone.utc).isoformat(),
                "x-amz-meta-page-number": str(page_num),
                "x-amz-meta-image-type": img_type,
            }

            try:
                path = save_image_to_minio(minio_manager, pil_img, bucket, object_path, metadata)
                image_paths.append(f"minio://{bucket}/{path}")
            except Exception as e:
                logger.error(f"保存图片失败 (第{page_num}页): {e}")

    # 保存表格为图片
    for page_info in pdf_data["pages"]:
        page_num = page_info["page_num"]
        for tbl_info in page_info.get("tables", []):
            tbl_idx = tbl_info.get("table_index", 0)
            md_content = tbl_info.get("content_markdown", "")
            if not md_content:
                continue
            filename = f"page{page_num}_table{tbl_idx}.png"
            object_path = f"标准/{standard_folder}/images/{filename}"
            metadata = {
                "x-amz-meta-doc-id": doc_id,
                "x-amz-meta-title": title,
                "x-amz-meta-category": "标准",
                "x-amz-meta-file-type": "png",
                "x-amz-meta-source-path": object_path,
                "x-amz-meta-upload-time": datetime.now(timezone.utc).isoformat(),
                "x-amz-meta-page-number": str(page_num),
                "x-amz-meta-image-type": "table",
            }
            try:
                # 将表格Markdown转为图片
                from PIL import ImageDraw, ImageFont
                lines = md_content.split("\n")
                font = ImageFont.load_default()
                line_h = 20
                img_h = max(200, len(lines) * line_h + 40)
                img_w = 800
                img = Image.new("RGB", (img_w, img_h), "white")
                draw = ImageDraw.Draw(img)
                for i, line in enumerate(lines):
                    draw.text((10, 10 + i * line_h), line, fill="black", font=font)
                path = save_image_to_minio(minio_manager, img, bucket, object_path, metadata)
                image_paths.append(f"minio://{bucket}/{path}")
            except Exception as e:
                logger.error(f"保存表格图片失败 (第{page_num}页): {e}")

    logger.info(f"图片保存完成: {len(image_paths)}张")
    return image_paths


def create_chroma_chunks(chroma_manager: ChromaInputManager, doc_id: str, title: str,
                         chunks: List[Dict[str, Any]], image_paths: List[str],
                         neo4j_method_id: str, source_file_minio_path: str) -> List[str]:
    """将分块文本存入Chroma向量数据库"""
    chroma_ids = []
    for i, chunk in enumerate(chunks):
        chunk_id = f"{doc_id}_{i}"
        meta = {
            "doc_id": doc_id,
            "title": title,
            "chunk_index": i,
            "start_pos": chunk.get("start_pos", 0),
            "end_pos": chunk.get("end_pos", 0),
            "type": "标准",
            "neo4j_testmethod_id": neo4j_method_id or "",
            "source_file": f"minio://{source_file_minio_path}",
            "images": "、".join(image_paths) if image_paths else "",
            "tables": json.dumps(chunk.get("tables", []), ensure_ascii=False),
            "formulas": json.dumps(chunk.get("formulas", []), ensure_ascii=False),
        }
        try:
            chroma_manager.collection.add(
                ids=[chunk_id],
                documents=[chunk["text"]],
                metadatas=[meta],
            )
            chroma_ids.append(chunk_id)
        except Exception as e:
            logger.error(f"Chroma存入失败 (块{i}): {e}")

    logger.info(f"Chroma存入完成: {len(chroma_ids)}个块")
    return chroma_ids


def process_single_pdf(pdf_path: str, minio_manager: MinIOInputManager,
                       chroma_manager: ChromaInputManager, bucket: str) -> Dict[str, Any]:
    """处理单个PDF的完整流程"""
    filename = os.path.basename(pdf_path)
    logger.info(f"{'='*60}")
    logger.info(f"处理PDF: {filename}")
    logger.info(f"{'='*60}")

    result = {
        "file": filename,
        "success": False,
        "doc_id": "",
        "title": "",
        "pages": 0,
        "chunks": 0,
        "images": 0,
        "tables": 0,
        "error": None,
    }

    try:
        # 1. 解析PDF
        pdf_data = parse_pdf(pdf_path)
        doc_id = pdf_data["standard_id"]
        title = pdf_data["title"]
        neo4j_method_id = NEO4J_METHOD_MAP.get(doc_id, "")

        result["doc_id"] = doc_id
        result["title"] = title
        result["pages"] = len(pdf_data["pages"])
        result["tables"] = len(pdf_data["all_tables"])

        # 2. 保存原始PDF到MinIO
        minio_path = save_pdf_to_minio(minio_manager, pdf_path, doc_id, title)
        logger.info(f"PDF已保存到MinIO: {minio_path}")

        # 3. 提取并保存图片到MinIO
        image_paths = extract_and_save_images(minio_manager, pdf_data, bucket, doc_id, title)
        result["images"] = len(image_paths)

        # 4. 文本分块
        chunks = chunk_document(pdf_data["full_text"], pdf_data["pages"])

        # 5. 存入Chroma
        chroma_ids = create_chroma_chunks(
            chroma_manager, doc_id, title, chunks, image_paths,
            neo4j_method_id, minio_path
        )
        result["chunks"] = len(chroma_ids)
        result["success"] = True

        logger.info(f"处理完成: {doc_id} | {result['pages']}页 | {result['chunks']}块 | {result['images']}图 | {result['tables']}表")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"处理PDF失败 {filename}: {e}")

    return result


def main(pdf_directory: str):
    """
    主流程：遍历目录下所有PDF，逐个处理并存储到MinIO和Chroma
    """
    Config.init_app(type('App', (), {'config': {}})())

    minio_client = Config.MINIO_CLIENT
    chroma_client = Config.CHROMA_CLIENT
    chroma_collection = Config.CHROMA_COLLECTION_OBJ
    bucket_name = Config.MINIO_BUCKET_NAME

    if not minio_client:
        logger.error("MinIO客户端未初始化")
        return
    if not chroma_client:
        logger.error("Chroma客户端未初始化")
        return

    minio_manager = MinIOInputManager(minio_client, bucket_name)
    chroma_manager = ChromaInputManager(chroma_client, chroma_collection)

    pdf_files = sorted([
        os.path.join(pdf_directory, f)
        for f in os.listdir(pdf_directory)
        if f.lower().endswith(".pdf")
    ])

    if not pdf_files:
        logger.warning(f"目录 {pdf_directory} 下未找到PDF文件")
        return

    logger.info(f"找到 {len(pdf_files)} 个PDF文件，开始处理...")

    results = []
    for pdf_path in tqdm(pdf_files, desc="处理PDF"):
        result = process_single_pdf(pdf_path, minio_manager, chroma_manager, bucket_name)
        results.append(result)

    # 打印统计
    logger.info(f"\n{'='*60}")
    logger.info("处理结果汇总")
    logger.info(f"{'='*60}")
    success_count = 0
    total_chunks = 0
    total_images = 0
    total_tables = 0
    for r in results:
        status = "成功" if r["success"] else f"失败: {r['error']}"
        logger.info(f"  {r['file']}: {status}")
        if r["success"]:
            success_count += 1
            total_chunks += r["chunks"]
            total_images += r["images"]
            total_tables += r["tables"]
            logger.info(f"    -> {r['doc_id']} | {r['title']} | {r['pages']}页 | {r['chunks']}块 | {r['images']}图 | {r['tables']}表")

    logger.info(f"\n总计: {success_count}/{len(results)} 成功")
    logger.info(f"  文本块: {total_chunks}")
    logger.info(f"  图片: {total_images}")
    logger.info(f"  表格: {total_tables}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    import argparse
    parser = argparse.ArgumentParser(description="PDF标准文档处理流水线")
    parser.add_argument("pdf_directory", help="包含PDF文件的目录路径")
    args = parser.parse_args()
    main(args.pdf_directory)
