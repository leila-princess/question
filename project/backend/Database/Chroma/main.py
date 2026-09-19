import os
import sys
import json
import logging
from typing import List, Dict, Any
from datetime import datetime, timezone
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import Config
from Process_data.extractor import DocumentExtractor, chunk_document

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


def _extract_doc_info(filename: str):
    base = filename.replace(".pdf", "").replace(".docx", "").replace("+", "").replace("-", "").replace("_", "")
    for key, (doc_id, title) in STANDARD_ID_MAP.items():
        if key in base:
            return doc_id, title
    import hashlib
    doc_id = f"std_{hashlib.md5(filename.encode()).hexdigest()[:6]}"
    return doc_id, os.path.splitext(filename)[0]


def process_single_file(file_path: str, extractor: DocumentExtractor,
                        chroma_client, chroma_collection, bucket_name: str,
                        minio_client, neo4j_method_id: str = "",
                        pre_extracted_result: Dict = None,
                        pre_doc_id: str = None,
                        pre_title: str = None,
                        category: str = "标准") -> Dict[str, Any]:
    """
    处理单个文件，将文本分块存入Chroma向量数据库。
    
    Args:
        file_path: 文件路径
        extractor: 文档提取器
        chroma_client: Chroma客户端
        chroma_collection: Chroma集合
        bucket_name: MinIO桶名
        minio_client: MinIO客户端
        neo4j_method_id: Neo4j方法ID
        pre_extracted_result: 预提取的结果（避免重复提取）
        pre_doc_id: 预计算的doc_id（确保一致性）
        pre_title: 预计算的title
        category: 文件类别
    
    Returns:
        包含处理结果的字典
    """
    filename = os.path.basename(file_path)
    
    # 使用预计算的doc_id和title，确保与调用方一致
    if pre_doc_id and pre_title:
        doc_id, title = pre_doc_id, pre_title
    else:
        doc_id, title = _extract_doc_info(filename)
    
    logger.info(f"[Chroma] Processing: {filename} -> doc_id={doc_id}, title={title}")

    result = {"file": filename, "success": False, "doc_id": doc_id, "title": title,
              "pages": 0, "chunks": 0, "images": 0, "tables": 0, "formulas": 0, "error": None}

    try:
        # 使用预提取的结果，避免重复提取
        if pre_extracted_result:
            extract_result = pre_extracted_result
            logger.info(f"[Chroma] Using pre-extracted result: {extract_result.get('total_pages', 0)} pages")
        else:
            extract_result = extractor.extract(file_path)
            logger.info(f"[Chroma] Extracted: {extract_result.get('total_pages', 0)} pages")
        
        result["pages"] = extract_result.get("total_pages", 0)
        result["tables"] = len(extract_result.get("all_tables", []))
        result["formulas"] = len(extract_result.get("all_formulas", []))
        result["images"] = len(extract_result.get("all_figures", []))

        # 使用传入的category构建路径
        source_minio_path = f"{category}/{doc_id}/origin.{extract_result.get('file_type', 'pdf')}"
        image_paths = []
        for fig in extract_result.get("all_figures", []):
            image_paths.append(f"minio://{bucket_name}/{category}/{doc_id}/images/{fig.get('filename', '')}")

        # 分块文档
        chunks = chunk_document(extract_result.get("full_text", ""), extract_result.get("pages", []))
        
        if not chunks:
            logger.warning(f"[Chroma] No chunks generated for {filename}")
            result["error"] = "No chunks generated"
            return result

        chroma_ids = []
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_{i}"
            meta = {
                "doc_id": doc_id,
                "title": title,
                "chunk_index": i,
                "start_pos": chunk.get("start_pos", 0),
                "end_pos": chunk.get("end_pos", 0),
                "type": category,
                "neo4j_file_id": neo4j_method_id or "",
                "neo4j_testmethod_id": neo4j_method_id or "",
                "source_file": f"minio://{bucket_name}/{source_minio_path}",
                "images": "、".join(image_paths) if image_paths else "",
                "tables": json.dumps(chunk.get("tables", []), ensure_ascii=False),
                "formulas": json.dumps(chunk.get("formulas", []), ensure_ascii=False),
            }
            try:
                chroma_collection.add(ids=[chunk_id], documents=[chunk["text"]], metadatas=[meta])
                chroma_ids.append(chunk_id)
            except Exception as e:
                logger.error(f"[Chroma] Failed to add chunk {chunk_id}: {e}")
                result["error"] = f"Failed to add chunk {i}: {e}"
                return result

        result["chunks"] = len(chroma_ids)
        result["success"] = True
        result["chroma_ids"] = chroma_ids  # 返回所有chunk IDs
        logger.info(f"[Chroma] Done: {doc_id} | {result['pages']}p | {result['chunks']} chunks | "
                     f"{result['images']} imgs | {result['tables']} tables | {result['formulas']} formulas")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"[Chroma] Failed to process {filename}: {e}")
        import traceback
        logger.error(traceback.format_exc())

    return result


def main(input_path: str):
    Config.init_app(type('App', (), {'config': {}})())

    chroma_client = Config.CHROMA_CLIENT
    chroma_collection = Config.CHROMA_COLLECTION_OBJ
    minio_client = Config.MINIO_CLIENT
    bucket_name = Config.MINIO_BUCKET_NAME

    if not chroma_client:
        logger.error("Chroma client not initialized")
        return
    if not minio_client:
        logger.error("MinIO client not initialized")
        return

    extractor = DocumentExtractor(dpi=300, use_gpu=False, output_dir=Config.OUTPUT_DIR)

    if os.path.isfile(input_path):
        files = [input_path]
    elif os.path.isdir(input_path):
        files = sorted([
            os.path.join(input_path, f) for f in os.listdir(input_path)
            if f.lower().endswith((".pdf", ".docx", ".doc"))
        ])
    else:
        logger.error(f"Path not found: {input_path}")
        return

    if not files:
        logger.warning(f"No PDF/Word files found in: {input_path}")
        return

    logger.info(f"Found {len(files)} files, starting processing...")
    results = []

    for fpath in tqdm(files, desc="Processing"):
        neo4j_method_id = ""
        doc_id, _ = _extract_doc_info(os.path.basename(fpath))
        neo4j_method_id = NEO4J_METHOD_MAP.get(doc_id, "")
        r = process_single_file(fpath, extractor, chroma_client, chroma_collection,
                                bucket_name, minio_client, neo4j_method_id)
        results.append(r)

    logger.info(f"\n{'='*60}")
    logger.info("Summary")
    logger.info(f"{'='*60}")
    ok = 0
    for r in results:
        status = "OK" if r["success"] else f"FAIL: {r['error']}"
        logger.info(f"  {r['file']}: {status}")
        if r["success"]:
            ok += 1
            logger.info(f"    -> {r['doc_id']} | {r['title']} | {r['pages']}p | "
                         f"{r['chunks']} chunks | {r['images']} imgs | {r['tables']} tables")
    logger.info(f"\nTotal: {ok}/{len(results)} succeeded")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path", help="PDF/Word file or directory")
    args = parser.parse_args()
    main(args.input_path)
