import os
import sys
import json
import logging
import threading
import queue
import uuid
import mimetypes
import re
import hashlib
import docx
import fitz
from pathlib import Path
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, Response, send_file

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from config import Config
from urllib.parse import quote, unquote

from Database.minIo.main import (
    upload_original_file, save_image_to_minio, upload_images_from_extraction,
    _extract_doc_info, _extract_ics_ccs_from_pdf, _safe_meta
)
from Process_data.extractor import DocumentExtractor
from utils.operation_log import log_user_action
from utils.office_to_pdf import (
    get_or_create_preview_pdf,
    is_excel_native_preview,
    is_office_convertible,
    is_ppt_convertible,
    is_word_native_preview,
    office_convert_status,
)

from .document_heading_extract import (
    SECTION_RE as _SECTION_RE,
    extract_document_headings,
    extract_toc_from_pdf,
    extract_toc_from_pdf_fast,
    extract_toc_from_word as _extract_toc_from_word_impl,
    is_text_garbled as _is_text_garbled,
    normalize_ocr_text as _normalize_ocr_text,
)

logger = logging.getLogger(__name__)

wjgl1_bp = Blueprint('wjgl1', __name__, url_prefix='/api/wjgl1')

_OCR_TASK_QUEUE: "queue.Queue[dict]" = queue.Queue()
_OCR_TASK_STATUS: dict = {}
_OCR_STATUS_LOCK = threading.Lock()
_OCR_WORKER_STARTED = False

# ============================================================
# 异步上传处理 - 文件先上传到MinIO，后台处理文档提取和Chroma同步
# ============================================================

# 文件类型 → 编号前缀映射
TYPE_PREFIX_MAP = {
    "法规标准": "fgbz",
    "规范性文件": "gzfwj",
    "指导性文件": "zdzxwj",
    "试验方案": "syfa",
    "试验大纲": "sydg",
    "试验报告": "sybg",
    "问题": "wt",
    "问题管理": "wtgl",
    "专项评估报告": "zxpgbg",
    "其他": "qt",
}

def _max_seq_from_minio_for_doc_prefix(type_prefix: str) -> int:
    """从 MinIO 路径 category/<doc_id>/origin.* 中解析 doc_id 数字后缀，避免 Neo4j 未同步时重复编号覆盖对象。"""
    if not Config.MINIO_CLIENT:
        return 0
    max_seq = 0
    categories = [cat for cat, pfx in TYPE_PREFIX_MAP.items() if pfx == type_prefix]
    for category in categories:
        prefix_path = f"{category}/"
        try:
            for obj in Config.MINIO_CLIENT.list_objects(
                Config.MINIO_BUCKET_NAME, prefix=prefix_path, recursive=True
            ):
                on = obj.object_name
                if not _is_wjgl_origin_object_key(on):
                    continue
                parts = on.replace("\\", "/").split("/")
                if len(parts) < 3:
                    continue
                folder = parts[1]
                if not folder.startswith(type_prefix):
                    continue
                suffix = folder[len(type_prefix) :]
                if suffix.isdigit():
                    n = int(suffix)
                    if n > max_seq:
                        max_seq = n
        except Exception as e:
            logger.warning("MinIO scan max seq failed category=%s: %s", category, e)
    return max_seq


# 文件编号计数器 — Neo4j 与 MinIO 取最大序号 +1，避免仅依赖 Neo4j 时总是从 1 开始覆盖已有文件
def _get_next_file_number(type_prefix: str):
    """查询指定 doc_id 前缀（如 fgbz）的最大序号，返回下一个序号 (1-based)"""
    neo_max = 0
    if Config.NEO4J_DRIVER:
        try:
            with Config.NEO4J_DRIVER.session() as session:
                result = session.run(
                    "MATCH (f:File) WHERE f.Id STARTS WITH $prefix "
                    "RETURN toInteger(substring(f.Id, size($prefix))) AS maxNum ORDER BY maxNum DESC LIMIT 1",
                    prefix=f"WJ_{type_prefix}",
                )
                record = result.single()
                if record and record.get("maxNum") is not None:
                    neo_max = int(record["maxNum"])
        except Exception as e:
            logger.warning(f"Failed to query max file number for prefix {type_prefix}: {e}")
    minio_max = _max_seq_from_minio_for_doc_prefix(type_prefix)
    return max(neo_max, minio_max) + 1


def _generate_file_doc_id(category: str):
    """根据文件类别生成 doc_id，格式: std0001/fa0001/gf0001/mb0001/qt0001"""
    prefix = TYPE_PREFIX_MAP.get(category, "qt")
    next_num = _get_next_file_number(prefix)
    doc_id = f"{prefix}{next_num:04d}"
    return doc_id

CATEGORY_MAP = {
    "法规标准": "法规标准",
    "规范性文件": "规范性文件",
    "指导性文件": "指导性文件",
    "试验方案": "试验方案",
    "试验大纲": "试验大纲",
    "试验报告": "试验报告",
    "问题": "问题",
    "问题管理": "问题管理",
    "专项评估报告": "专项评估报告",
    "其他": "其他",
}

NODE_TYPE_COLORS = {
    "EquipmentType": "#1890ff",
    "Equipment": "#52c41a",
    "Environment": "#722ed1",
    "TestProject": "#fa8c16",
    "Indicator": "#13c2c2",
    "TestMethod": "#eb2f96",
}


def _is_wjgl_origin_object_key(object_name: str) -> bool:
    """MinIO 原始文件路径形如 category/docId/origin.ext"""
    base = object_name.rsplit("/", 1)[-1]
    return base.startswith("origin.") and len(base) > len("origin.")


def _is_safe_wjgl_object_name(object_name: str) -> bool:
    if not object_name or ".." in object_name:
        return False
    norm = object_name.replace("\\", "/").lstrip("/")
    if norm.startswith(".."):
        return False
    parts = norm.split("/")
    if len(parts) < 3:
        return False
    category = parts[0].replace(" ", "")
    if category not in TYPE_PREFIX_MAP:
        return False
    return _is_wjgl_origin_object_key(norm)


def _count_wjgl_origin_files_for_category(category: str) -> int:
    """统计某类别下可被列表展示的原始文件数，过滤规则与 list_files 一致（不按关键词/日期筛）。"""
    if not Config.MINIO_CLIENT:
        return 0
    prefix = f"{category}/" if category else ""
    try:
        all_objects = list(
            Config.MINIO_CLIENT.list_objects(
                Config.MINIO_BUCKET_NAME, prefix=prefix, recursive=True
            )
        )
    except Exception as e:
        logger.warning("category-counts list_objects failed prefix=%r: %s", prefix, e)
        return 0
    n = 0
    for obj in all_objects:
        on = obj.object_name
        if "/images/" in on or "/figures/" in on or "/ocr_" in on:
            continue
        if not _is_wjgl_origin_object_key(on):
            continue
        n += 1
    return n


_SIMPLE_UPLOAD_ALLOWED_EXT = frozenset({
    ".pdf", ".doc", ".docx", ".ppt", ".pptx",
    ".xls", ".xlsx", ".wps",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
})


# 标题乱码检测见 document_heading_extract.is_text_garbled

def _should_force_pdf_ocr(full_text: str, total_pages: int) -> bool:
    """判断 PDF 是否应强制走 OCR。"""
    if not full_text or not full_text.strip():
        return True
    avg_chars = len(full_text) / max(1, total_pages)
    if avg_chars < 80:
        return True
    return _is_text_garbled(full_text)


def _check_page_text_quality(page_text: str, sample_size: int = 20) -> bool:
    if not page_text or not page_text.strip():
        return True
    
    sample_text = page_text[:sample_size].strip()
    if not sample_text:
        return True
    
    total = len(sample_text)
    good_count = 0
    
    for ch in sample_text:
        code = ord(ch)
        if ch.isalnum() or ch.isspace() or ch in "，。、《》？！：；,.!?;:()[]{}-_/\\\"'％%＋+＝=·—":
            good_count += 1
        elif 0x4E00 <= code <= 0x9FFF:
            good_count += 1
    
    good_ratio = good_count / total
    return good_ratio < 0.5


def _extract_pdf_with_pymupdf(pdf_path: str) -> dict:
    import fitz
    
    result = {
        "file_name": os.path.basename(pdf_path),
        "file_type": "pdf",
        "total_pages": 0,
        "pages": [],
        "full_text": "",
        "all_tables": [],
        "all_figures": [],
        "all_formulas": [],
    }
    
    try:
        doc = fitz.open(pdf_path)
        result["total_pages"] = len(doc)
        
        all_text_parts = []
        pages_needing_ocr = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_text = page.get_text() or ""
            
            needs_ocr = _check_page_text_quality(page_text)
            
            if needs_ocr:
                pages_needing_ocr.append(page_num + 1)
                page_text = f"[需要OCR处理] {page_text}"
            
            result["pages"].append({
                "page_num": page_num + 1,
                "text": page_text,
                "tables": [],
                "figures": [],
                "formulas": [],
                "needs_ocr": needs_ocr
            })
            
            if page_text:
                all_text_parts.append(f"===== Page {page_num + 1} =====\n{page_text}")
        
        result["full_text"] = "\n\n".join(all_text_parts)
        result["pages_needing_ocr"] = pages_needing_ocr
        
        doc.close()
        
    except Exception as e:
        logger.error(f"PyMuPDF extraction failed: {e}")
    
    return result


def _quick_pdf_needs_ocr(pdf_path: str) -> bool:
    """快速判断 PDF 是否疑似扫描件/乱码，避免阻塞主请求。"""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text_parts = []
        sample_pages = min(3, len(doc))
        for i in range(sample_pages):
            text_parts.append(doc[i].get_text() or "")
        doc.close()
        sample_text = "\n".join(text_parts)
        return _should_force_pdf_ocr(sample_text, sample_pages)
    except Exception:
        # 无法读取文本层时，按需要 OCR 处理
        return True


def _set_task_status(task_id: str, status: str, **extra):
    with _OCR_STATUS_LOCK:
        data = _OCR_TASK_STATUS.get(task_id, {})
        data.update({"status": status, **extra})
        _OCR_TASK_STATUS[task_id] = data


def _parse_cache_ready(category: str, doc_id: str) -> bool:
    try:
        from .document_parse_service import local_parse_ready

        if local_parse_ready(category, doc_id):
            return True
    except Exception:
        pass
    if not Config.MINIO_CLIENT or not category or not doc_id:
        return False
    try:
        from .document_parse_service import _parse_minio_key
        Config.MINIO_CLIENT.stat_object(
            Config.MINIO_BUCKET_NAME,
            _parse_minio_key(category, doc_id, "sections.json"),
        )
        return True
    except Exception:
        try:
            Config.MINIO_CLIENT.stat_object(
                Config.MINIO_BUCKET_NAME,
                _parse_minio_key(category, doc_id, "toc.json"),
            )
            return True
        except Exception:
            return False


def _ocr_task_status_for_doc(doc_id: str) -> dict:
    if not doc_id:
        return {}
    with _OCR_STATUS_LOCK:
        for task_id, data in _OCR_TASK_STATUS.items():
            if data.get("doc_id") == doc_id:
                return {"task_id": task_id, **data}
            result = data.get("result") or {}
            if isinstance(result, dict) and result.get("doc_id") == doc_id:
                return {"task_id": task_id, **data}
    return {}


def _resolve_category_doc_id(object_name: str = "", doc_id: str = "", category: str = "") -> tuple:
    cat = (category or "").strip()
    did = (doc_id or "").strip()
    path = (object_name or "").replace("\\", "/")
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        if not cat:
            cat = parts[0]
        if not did:
            did = parts[1]
    return cat, did


def _cascade_delete_wjgl_document(
    *,
    category: str,
    doc_id: str,
    object_name: str = "",
) -> dict:
    """删除文档在 MinIO / Chroma / Neo4j / 本地缓存中的全部关联数据。"""
    stats = {"minio_deleted": 0, "chroma_deleted": 0, "neo4j_deleted": 0, "local_dirs": 0}
    category = (category or "").strip()
    doc_id = (doc_id or "").strip()

    if Config.MINIO_CLIENT and category and doc_id:
        try:
            from Database.minIo.delete import MinIODeleteManager

            mgr = MinIODeleteManager(Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME)
            prefix = f"{category}/{doc_id}/"
            result = mgr.delete_by_prefix(prefix)
            stats["minio_deleted"] = int((result.get("data") or {}).get("deleted") or 0)
        except Exception as exc:
            logger.warning("[wjgl-delete] MinIO prefix delete failed: %s", exc)
    elif Config.MINIO_CLIENT and object_name:
        try:
            Config.MINIO_CLIENT.remove_object(Config.MINIO_BUCKET_NAME, object_name)
            stats["minio_deleted"] = 1
            prefix = object_name.rsplit("/", 1)[0] + "/"
            images = Config.MINIO_CLIENT.list_objects(
                Config.MINIO_BUCKET_NAME, prefix=prefix + "images/", recursive=True,
            )
            for img in images:
                Config.MINIO_CLIENT.remove_object(Config.MINIO_BUCKET_NAME, img.object_name)
                stats["minio_deleted"] += 1
        except Exception as exc:
            logger.warning("[wjgl-delete] MinIO object delete failed: %s", exc)

    if Config.CHROMA_COLLECTION_OBJ and doc_id:
        try:
            from blueprints.RelFact.section_chroma_indexer import delete_doc_chunks

            stats["chroma_deleted"] += delete_doc_chunks(Config.CHROMA_COLLECTION_OBJ, doc_id)
        except Exception as exc:
            logger.warning("[wjgl-delete] Chroma doc_id delete failed: %s", exc)
        try:
            for where in ({"file_id": f"WJ_{doc_id}"}, {"doc_id": doc_id}):
                results = Config.CHROMA_COLLECTION_OBJ.get(where=where)
                ids = results.get("ids") or []
                if ids:
                    Config.CHROMA_COLLECTION_OBJ.delete(ids=ids)
                    stats["chroma_deleted"] += len(ids)
        except Exception as exc:
            logger.debug("[wjgl-delete] Chroma extra filter delete: %s", exc)

    if Config.NEO4J_DRIVER and doc_id:
        file_node_id = f"WJ_{doc_id}"
        minio_prefix = f"{category}/{doc_id}/" if category else ""
        try:
            with Config.NEO4J_DRIVER.session() as session:
                if minio_prefix:
                    session.run(
                        """
                        MATCH (f:File)
                        WHERE f.Id = $id OR f.minIo_id STARTS WITH $prefix
                        DETACH DELETE f
                        """,
                        id=file_node_id,
                        prefix=minio_prefix,
                    )
                else:
                    session.run(
                        "MATCH (f:File {Id: $id}) DETACH DELETE f",
                        id=file_node_id,
                    )
                stats["neo4j_deleted"] = 1
        except Exception as exc:
            logger.warning("[wjgl-delete] Neo4j File delete failed: %s", exc)

    if category and doc_id:
        import shutil

        try:
            from pdf_extract.mineru_wjgl_service import local_doc_dir

            local_dir = local_doc_dir(category, doc_id)
            if local_dir.is_dir():
                shutil.rmtree(local_dir, ignore_errors=True)
                stats["local_dirs"] += 1
        except Exception as exc:
            logger.debug("[wjgl-delete] local parse dir remove: %s", exc)
        ocr_dir = os.path.join(Config.OUTPUT_DIR, "wjgl_ocr_cache", category, doc_id)
        if os.path.isdir(ocr_dir):
            shutil.rmtree(ocr_dir, ignore_errors=True)
            stats["local_dirs"] += 1

    if doc_id:
        stale = [
            tid for tid, data in list(_OCR_TASK_STATUS.items())
            if str((data or {}).get("doc_id") or "") == doc_id
        ]
        for tid in stale:
            _OCR_TASK_STATUS.pop(tid, None)

    if doc_id:
        try:
            from blueprints.FLGL.section_extract_debug import purge_flgl_local_artifacts_for_document

            flgl_stats = purge_flgl_local_artifacts_for_document(doc_id=doc_id, category=category)
            stats["flgl_debug_dirs"] = flgl_stats.get("debug_dirs", 0)
            stats["flgl_preview_cache"] = flgl_stats.get("preview_cache", 0)
            stats["flgl_guided_dirs"] = flgl_stats.get("guided_dirs", 0)
        except Exception as exc:
            logger.debug("[wjgl-delete] FLGL local cache purge: %s", exc)

    return stats


def _toc_from_parse_sections(sections: list) -> list:
    """从 MinIO parse sections 构建目录树（毫秒级，无 OCR）。"""
    from .document_parse_service import build_toc_from_sections

    return build_toc_from_sections(sections)


def _process_ocr_task(task: dict):
    task_id = task["task_id"]
    temp_path = task["temp_path"]
    filename = task["filename"]
    doc_id = task["doc_id"]
    title = task["title"]
    category = task["category"]
    label = task["label"]
    user_id = task["user_id"]
    file_node_id = task["file_node_id"]
    file_associations = task["file_associations"]
    file_abstract = task["file_abstract"]
    file_keywords = task["file_keywords"]
    file_sync_chroma = task["file_sync_chroma"]
    file_std_code = task.get("file_std_code") or ""
    file_std_name = task.get("file_std_name") or ""
    ics = task["ics"]
    ccs = task["ccs"]
    ext = task["ext"]
    image_paths = task.get("image_paths", [])

    _set_task_status(task_id, "processing", doc_id=doc_id)

    try:
        extract_result = None
        extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
        
        if ext == ".pdf":
            extract_result = extractor.extract(temp_path)
            
            if extract_result and extract_result.get("all_figures"):
                new_images = upload_images_from_extraction(
                    Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, extract_result,
                    doc_id, title, category, user_id,
                    file_ext="pdf"
                )
                image_paths.extend(new_images)
                logger.info(f"[OCR-TASK] Layout analysis found {len(new_images)} additional images")
        else:
            extract_result = extractor.extract(temp_path)
            
            if extract_result and extract_result.get("all_figures"):
                new_images = upload_images_from_extraction(
                    Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, extract_result,
                    doc_id, title, category, user_id,
                    file_ext=ext.lstrip(".")
                )
                image_paths.extend(new_images)

        chroma_id = ""
        chroma_chunks = 0
        from blueprints.RelFact.schema import infer_file_kind, should_section_chroma_index

        file_kind = infer_file_kind(category, file_std_code)
        use_section_chroma = should_section_chroma_index(file_kind)

        if file_sync_chroma and Config.CHROMA_COLLECTION_OBJ and extract_result and not use_section_chroma:
            from Database.Chroma.main import process_single_file as chroma_process_single
            chroma_result = chroma_process_single(
                temp_path, extractor, Config.CHROMA_CLIENT,
                Config.CHROMA_COLLECTION_OBJ, Config.MINIO_BUCKET_NAME,
                Config.MINIO_CLIENT, file_node_id,
                pre_extracted_result=extract_result,
                pre_doc_id=doc_id,
                pre_title=title,
                category=category
            )
            if chroma_result.get("success"):
                chroma_id = chroma_result.get("doc_id", "")
                chroma_chunks = chroma_result.get("chunks", 0)

        if Config.NEO4J_DRIVER:
            with Config.NEO4J_DRIVER.session() as session:
                session.run(
                    "MATCH (n:File {Id: $id}) SET n.Chroma_id = $chroma_id, n.Chroma_chunks = $chroma_chunks, n.Image_minIO_id = $image_ids",
                    id=file_node_id, chroma_id=chroma_id, chroma_chunks=chroma_chunks,
                    image_ids=json.dumps(image_paths)
                )
            
            if file_associations:
                _create_file_associations(file_node_id, file_associations)

        parse_error = ""
        try:
            from .document_parse_service import parse_local_file, save_parse_artifacts
            from pdf_extract.mineru_wjgl_service import parse_pdf_save_local_and_minio, use_mineru_for_wjgl_pdf

            if os.path.exists(temp_path):
                ocr_dir = os.path.join(Config.OUTPUT_DIR, "wjgl_ocr_cache", category, doc_id)
                os.makedirs(ocr_dir, exist_ok=True)
                parsed = None
                force_ocr = False
                if ext == ".pdf" and use_mineru_for_wjgl_pdf():
                    try:
                        parsed = parse_pdf_save_local_and_minio(
                            temp_path, category, doc_id, ocr_dir,
                        )
                        logger.info(
                            "[OCR-TASK] MinerU parse saved doc_id=%s sections=%s dir=%s",
                            doc_id,
                            (parsed.get("stats") or {}).get("section_count"),
                            (parsed.get("parsed_document") or {}).get("local_dir"),
                        )
                    except Exception as me:
                        logger.warning("[OCR-TASK] MinerU 失败，回退 parse_local_file: %s", me)

                if parsed is None:
                    force_ocr = ext == ".pdf"
                    if force_ocr:
                        os.environ["FLGL_FORCE_OCR"] = "1"
                    try:
                        parsed = parse_local_file(temp_path, ocr_output_dir=ocr_dir)
                    finally:
                        if force_ocr:
                            os.environ.pop("FLGL_FORCE_OCR", None)

                save_parse_artifacts(category, doc_id, parsed)
                logger.info(
                    "[OCR-TASK] parse artifacts saved doc_id=%s tables=%s figures=%s",
                    doc_id,
                    (parsed.get("stats") or {}).get("table_count"),
                    (parsed.get("stats") or {}).get("figure_count"),
                )

                if file_sync_chroma and Config.CHROMA_COLLECTION_OBJ and use_section_chroma:
                    from blueprints.RelFact.section_chroma_indexer import index_sections_to_chroma
                    minio_src = f"minio://{Config.MINIO_BUCKET_NAME}/{category}/{doc_id}/origin{ext}"
                    sec_result = index_sections_to_chroma(
                        Config.CHROMA_COLLECTION_OBJ,
                        doc_id=doc_id,
                        file_id=file_node_id,
                        title=title,
                        category=category,
                        std_code=file_std_code,
                        sections=list(parsed.get("sections") or []),
                        minio_source=minio_src,
                        replace_existing=True,
                        node_slices=dict(parsed.get("node_slices") or {}),
                    )
                    if sec_result.get("success"):
                        chroma_id = doc_id
                        chroma_chunks = sec_result.get("chunks", 0)
                        if Config.NEO4J_DRIVER:
                            with Config.NEO4J_DRIVER.session() as session:
                                session.run(
                                    "MATCH (n:File {Id: $id}) SET n.Chroma_id = $chroma_id, n.Chroma_chunks = $chroma_chunks",
                                    id=file_node_id,
                                    chroma_id=chroma_id,
                                    chroma_chunks=chroma_chunks,
                                )
                        logger.info(
                            "[OCR-TASK] section chroma indexed doc_id=%s chunks=%s",
                            doc_id, chroma_chunks,
                        )
                    elif sec_result.get("error"):
                        logger.warning(
                            "[OCR-TASK] section chroma skipped doc_id=%s: %s",
                            doc_id, sec_result.get("error"),
                        )

                from blueprints.RelFact.rel_fact_builder import attach_from_parsed, save_rel_facts_artifact
                rel_result = attach_from_parsed(
                    file_node_id=file_node_id,
                    doc_id=doc_id,
                    category=category,
                    parsed=parsed,
                    std_code=file_std_code,
                    std_name=file_std_name,
                )
                if rel_result.get("success") and not rel_result.get("skipped"):
                    save_rel_facts_artifact(category, doc_id, rel_result)
                    logger.info(
                        "[OCR-TASK] rel facts attached file=%s relations=%d",
                        file_node_id,
                        len(rel_result.get("relations") or []),
                    )

                if Config.NEO4J_DRIVER:
                    with Config.NEO4J_DRIVER.session() as session:
                        session.run(
                            "MATCH (f:File {Id: $id}) SET f.Parse_status = $st",
                            id=file_node_id, st="done",
                        )
        except Exception as pe:
            parse_error = str(pe)
            logger.exception("[OCR-TASK] parse artifacts save failed: %s", pe)

        _set_task_status(task_id, "done", error=parse_error, doc_id=doc_id, result={
            "doc_id": doc_id,
            "title": title,
            "file_node_id": file_node_id,
            "chroma_id": chroma_id,
            "chroma_chunks": chroma_chunks,
            "images": len(image_paths),
            "parse_error": parse_error or None,
        })
    except Exception as e:
        logger.error(f"[OCR-QUEUE] Task failed ({task_id}, {filename}): {e}")
        _set_task_status(task_id, "failed", error=str(e))
    finally:
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except Exception:
            pass


def _ocr_worker_loop():
    while True:
        task = _OCR_TASK_QUEUE.get()
        if task is None:
            _OCR_TASK_QUEUE.task_done()
            break
        try:
            _process_ocr_task(task)
        finally:
            _OCR_TASK_QUEUE.task_done()


def _ensure_ocr_worker():
    global _OCR_WORKER_STARTED
    if _OCR_WORKER_STARTED:
        return
    worker = threading.Thread(target=_ocr_worker_loop, name="wjgl-ocr-worker", daemon=True)
    worker.start()
    _OCR_WORKER_STARTED = True


def _wjgl_llm_status() -> dict:
    from .llm_diagnostics import wjgl_llm_status

    return wjgl_llm_status()


@wjgl1_bp.route("/auto-parse/status", methods=["GET"])
def wjgl_auto_parse_status():
    """诊断：自动解析依赖的 LLM 是否可用（浏览器可访问 GET /api/wjgl/auto-parse/status）。"""
    return jsonify({"success": True, "data": _wjgl_llm_status()})


@wjgl1_bp.route('/auto-parse', methods=['POST'])
def auto_parse_file():
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400

    uploaded_file = request.files['file']
    filename = uploaded_file.filename
    if not filename:
        return jsonify({"success": False, "error": "No filename"}), 400

    logger.info("auto-parse: received file=%s content_length=%s", filename, request.content_length)

    temp_dir = os.path.join(Config.OUTPUT_DIR, 'uploads')
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, filename)
    uploaded_file.save(temp_path)

    try:
        from .std_filename import parse_std_metadata_from_filename

        doc_id, title = _extract_doc_info(filename)
        fn_meta = parse_std_metadata_from_filename(filename)
        ext = os.path.splitext(filename)[1].lower()

        abstract = ""
        keywords_list = []
        std_code = fn_meta.get("std_code") or ""
        std_name = fn_meta.get("std_name") or ""
        publish_date = ""
        issuer = ""
        llm_used = False
        warnings: list[str] = []

        if fn_meta.get("std_code") and not std_name:
            warnings.append(f"已从文件名识别标准号：{fn_meta['std_code']}；标准名称需 LLM 或手工补充。")
        if fn_meta.get("std_code"):
            doc_id = fn_meta.get("doc_id") or doc_id
            if fn_meta.get("std_code") and fn_meta.get("std_name"):
                title = f"{fn_meta['std_code']}_{fn_meta['std_name']}"
            elif fn_meta.get("std_code"):
                title = fn_meta["std_code"]

        if not Config.OPENAI_CLIENT:
            warnings.append(_wjgl_llm_status()["hint"])

        if Config.OPENAI_CLIENT:
            try:
                text_sample = ""
                if ext == ".pdf":
                    try:
                        import fitz
                        doc = fitz.open(temp_path)
                        text_parts = []
                        for page in doc:
                            text_parts.append(page.get_text())
                            if len("".join(text_parts)) > 8000:
                                break
                        doc.close()
                        text_sample = "".join(text_parts)[:8000]
                    except Exception as e:
                        logger.warning(f"PyMuPDF text extraction failed: {e}")
                elif ext in (".docx", ".doc"):
                    try:
                        import docx
                        doc = docx.Document(temp_path)
                        text_sample = "\n".join(p.text for p in doc.paragraphs)[:3000]
                    except Exception as e:
                        logger.warning(f"python-docx text extraction failed: {e}")

                if text_sample.strip():
                    llm_used = True
                    resp = Config.OPENAI_CLIENT.chat.completions.create(
                        model=Config.OPENAI_MODEL,
                        messages=[
                            {"role": "system", "content": "你是文档元数据提取专家。请从文档文本中提取以下信息，返回纯JSON：\n1. abstract: 文档摘要，200字以内\n2. keywords: 3-5个关键词\n3. std_code: 标准号/文档标识，如 GB/T 4208-2017\n4. std_name: 标准名称全称（标题）\n5. publish_date: 颁布/发布日期，格式 YYYY-MM-DD，找不到则空字符串\n6. issuer: 颁布/发布单位全称，找不到则空字符串\nJSON格式：{\"abstract\":\"\",\"keywords\":[],\"std_code\":\"\",\"std_name\":\"\",\"publish_date\":\"\",\"issuer\":\"\"}"},
                            {"role": "user", "content": f"请分析以下文档内容并提取元数据：\n\n{text_sample}"}
                        ],
                        temperature=0.1,
                        max_tokens=600,
                    )
                    content = resp.choices[0].message.content or ""
                    import re as _re
                    json_match = _re.search(r'\{.*\}', content, _re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group())
                        abstract = parsed.get("abstract", "")
                        keywords_list = parsed.get("keywords", [])
                        std_code = parsed.get("std_code", "") or std_code
                        std_name = parsed.get("std_name", "") or std_name
                        publish_date = parsed.get("publish_date", "")
                        issuer = parsed.get("issuer", "")
                        if std_code and std_name:
                            title = f"{std_code}_{std_name}"
                        elif std_code:
                            title = std_code
                elif Config.OPENAI_CLIENT:
                    warnings.append(
                        "未能从文档提取正文（请确认已安装 PyMuPDF：pip install pymupdf；"
                        "Word 需 python-docx），LLM 未参与解析。"
                    )
            except Exception as e:
                logger.warning(f"LLM extraction failed: {e}")
                warnings.append(f"LLM 调用失败: {e}")

        log_user_action(f"文件管理-自动解析元数据 file={filename[:120]}")
        return jsonify({
            "success": True,
            "data": {
                "doc_id": doc_id,
                "title": title,
                "abstract": abstract,
                "keywords": keywords_list,
                "file_type": ext.lstrip("."),
                "std_code": std_code,
                "std_name": std_name,
                "publish_date": publish_date,
                "issuer": issuer,
                "llm_used": llm_used,
                "warnings": warnings,
            }
        })

    except Exception as e:
        logger.error(f"Auto-parse failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@wjgl1_bp.route('/simple-upload', methods=['POST'])
def simple_upload_file():
    """「其他」分类：表单直传（名称、摘要、附件、用户、日期），不做自动解析与异步 OCR。"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400

    uploaded_file = request.files['file']
    filename = uploaded_file.filename or ''
    if not filename:
        return jsonify({"success": False, "error": "No filename"}), 400

    category = request.form.get('category', '其他')
    if category != '其他':
        return jsonify({"success": False, "error": "simple-upload 仅支持「其他」分类"}), 400

    title = (request.form.get('title') or '').strip() or os.path.splitext(filename)[0]
    abstract = (request.form.get('abstract') or '')[:500]
    user_id = (request.form.get('upload_user') or request.form.get('user_id') or 'admin')[:80]
    upload_date = (request.form.get('upload_time') or '').strip()[:10]
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _SIMPLE_UPLOAD_ALLOWED_EXT:
        return jsonify({"success": False, "error": f"不支持的文件类型: {ext or '(无扩展名)'}"}), 400

    prefix = TYPE_PREFIX_MAP.get(category, "qt")
    next_num = _get_next_file_number(prefix)
    doc_id = f"{prefix}{next_num:04d}"
    upload_time_iso = None
    if len(upload_date) >= 10 and upload_date[4] == '-' and upload_date[7] == '-':
        upload_time_iso = f"{upload_date[:10]}T00:00:00+00:00"

    temp_dir = os.path.join(Config.OUTPUT_DIR, 'uploads')
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, filename)
    uploaded_file.save(temp_path)
    file_node_id = f"WJ_{doc_id}"

    try:
        minio_path = upload_original_file(
            Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, temp_path,
            doc_id, title, category, '', '', '', user_id,
            abstract=abstract, keywords='',
            std_code='', std_name='',
            upload_time_iso=upload_time_iso,
        )
        ext_no_dot = ext.lstrip('.')
        if Config.NEO4J_DRIVER:
            try:
                _create_file_node(
                    file_node_id, category, '', title,
                    "", 0, minio_path, [], user_id,
                    abstract=abstract,
                    keywords='',
                    ics='',
                    ccs='',
                    file_type_ext=ext_no_dot,
                )
            except Exception as e:
                logger.warning(f"Failed to create Neo4j node (simple-upload): {e}")

        log_user_action(f"文件管理-简单上传(其他) doc_id={doc_id} file={filename[:120]}")
        return jsonify({
            "success": True,
            "data": {"doc_id": doc_id, "minio_path": minio_path, "title": title},
        })
    except Exception as e:
        logger.error(f"simple-upload failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


@wjgl1_bp.route('/upload', methods=['POST'])
def upload_files():
    print("[UPLOAD-1] ===== upload_files called =====")
    """Upload files to MinIO with per-file metadata, optionally sync to Chroma and create Neo4j File node.

    Supports duplicate detection: if a File node with the same ID already exists in Neo4j,
    returns duplicate info for frontend to decide: update / replace / skip.
    """
    print(f"[UPLOAD-2] MINIO_CLIENT available: {Config.MINIO_CLIENT is not None}")
    if not Config.MINIO_CLIENT:
        print("[UPLOAD-3] FAIL: MinIO not initialized")
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    print(f"[UPLOAD-4] request.files keys: {list(request.files.keys())}")
    print(f"[UPLOAD-5] request.form keys: {list(request.form.keys())}")
    files = request.files.getlist('files') or request.files.getlist('file')
    category = request.form.get('category', '其他')
    label = request.form.get('label', '')
    user_id = request.form.get('user_id', 'admin')
    duplicate_action = request.form.get('duplicate_action', 'check')
    review_mode = request.form.get('review_mode', '').lower() in ('1', 'true', 'yes')

    print(f"[UPLOAD-6] files count: {len(files)}, category: {category}, label: {label}, user_id: {user_id}, duplicate_action: {duplicate_action}")

    if not files:
        print("[UPLOAD-7] FAIL: No files provided")
        return jsonify({"success": False, "error": "No files provided"}), 400

    prefix = TYPE_PREFIX_MAP.get(category, "qt")
    print(f"[UPLOAD-7b] prefix={prefix}, files count={len(files)}")

    # Pre-allocate sequential doc_ids for all files in this batch
    batch_start_num = _get_next_file_number(prefix)
    allocated_count = 0

    print(f"[UPLOAD-7c] batch_start_num={batch_start_num}, prefix={prefix}")

    results = []
    duplicates = []
    shared_extractor = None

    for idx, uploaded_file in enumerate(files):
        print(f"[UPLOAD-8] Processing file #{idx+1}: filename={uploaded_file.filename}")
        filename = uploaded_file.filename
        if not filename:
            print(f"[UPLOAD-9] Skipping file #{idx+1}: no filename")
            continue

        uid = request.form.get(f'uid_{filename}', '')
        file_abstract = request.form.get(f'abstract_{uid}', '')
        print(f"[UPLOAD-10] uid={uid}, file_abstract={file_abstract[:50] if file_abstract else ''}")
        file_keywords = json.loads(request.form.get(f'keywords_{uid}', '[]'))
        file_sync_chroma = request.form.get(f'sync_chroma_{uid}', 'true').lower() == 'true'
        file_associations = json.loads(request.form.get(f'associations_{uid}', '[]'))
        file_ics = request.form.get(f'ics_{uid}', '')
        file_ccs = request.form.get(f'ccs_{uid}', '')
        file_title = request.form.get(f'title_{uid}', '')
        file_std_code = request.form.get(f'std_code_{uid}', '')
        file_std_name = request.form.get(f'std_name_{uid}', '')
        file_publish_date = request.form.get(f'publish_date_{uid}', '')
        file_issuer = request.form.get(f'issuer_{uid}', '')
        print(f"[UPLOAD-11] file_sync_chroma={file_sync_chroma}, file_ics={file_ics}, file_ccs={file_ccs}, file_title={file_title}, file_std_code={file_std_code}, file_std_name={file_std_name}")
        print(f"[UPLOAD-12] file_keywords={file_keywords}, file_associations={file_associations}")

        temp_dir = os.path.join(Config.OUTPUT_DIR, 'uploads')
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, filename)
        uploaded_file.save(temp_path)
        print(f"[UPLOAD-13] File saved to: {temp_path}, exists={os.path.exists(temp_path)}")

        try:
            print(f"[UPLOAD-14] Starting try block for file: {filename}")
            file_md5 = _compute_file_md5(temp_path)
            queued_async = False
            next_num = batch_start_num + allocated_count
            allocated_count += 1
            doc_id = f"{prefix}{next_num:04d}"
            print(f"[UPLOAD-14b] doc_id generated: prefix={prefix}, next_num={next_num}, doc_id={doc_id}")
            title = file_std_name if file_std_name else (file_title if file_title else os.path.splitext(filename)[0])
            if review_mode and not file_title and not file_std_name:
                title = os.path.splitext(filename)[0]
            ics = file_ics
            ccs = file_ccs
            ext = os.path.splitext(filename)[1].lower()
            print(f"[UPLOAD-15] doc_id={doc_id}, title={title}, ext={ext}")

            if Config.NEO4J_DRIVER:
                existing_by_md5 = _find_file_node_by_md5(file_md5)
                if existing_by_md5:
                    existing_file_id = str(existing_by_md5.get('id') or '')
                    existing_doc_id = (
                        existing_file_id[3:]
                        if existing_file_id.upper().startswith('WJ_')
                        else existing_file_id
                    )
                    print(f"[UPLOAD-15b] MD5 duplicate: {file_md5} -> {existing_file_id}")
                    if duplicate_action == 'replace':
                        _delete_existing_file_node(existing_file_id, existing_doc_id)
                    else:
                        results.append({
                            "success": True,
                            "doc_id": existing_doc_id,
                            "title": existing_by_md5.get('name') or title,
                            "file_node_id": existing_file_id,
                            "duplicate": True,
                            "action_taken": "skipped_md5",
                            "existing_data": existing_by_md5,
                            "minio_id": existing_by_md5.get('minio_id'),
                            "object_name": existing_by_md5.get('minio_id'),
                            "file_md5": file_md5,
                            "message": "相同内容文件已存在于文件库，已跳过上传",
                        })
                        continue

            file_node_id = f"WJ_{doc_id}"
            print(f"[UPLOAD-16] file_node_id={file_node_id}, NEO4J_DRIVER available: {Config.NEO4J_DRIVER is not None}")
            if Config.NEO4J_DRIVER:
                existing_node = _find_existing_file_node(file_node_id)
                print(f"[UPLOAD-17] existing_node: {existing_node}")
            else:
                existing_node = None
                print("[UPLOAD-17b] NEO4J_DRIVER not available, existing_node=None")

            if existing_node:
                print(f"[UPLOAD-18] Duplicate found! duplicate_action={duplicate_action}")
                if duplicate_action == 'skip':
                    print("[UPLOAD-19] Action: skip")
                    skip_doc_id = doc_id
                    if existing_node and existing_node.get('id'):
                        eid = str(existing_node['id'])
                        skip_doc_id = eid[3:] if eid.upper().startswith('WJ_') else eid
                    results.append({
                        "success": True,
                        "doc_id": skip_doc_id,
                        "title": existing_node.get('name') if existing_node else title,
                        "file_node_id": existing_node.get('id') if existing_node else file_node_id,
                        "duplicate": True,
                        "action_taken": "skipped",
                        "existing_data": existing_node,
                        "minio_id": (existing_node or {}).get('minio_id'),
                        "object_name": (existing_node or {}).get('minio_id'),
                    })
                    continue
                elif duplicate_action == 'replace':
                    print("[UPLOAD-20] Action: replace, deleting existing node")
                    _delete_existing_file_node(file_node_id, doc_id)
                elif duplicate_action == 'check':
                    print(f"[UPLOAD-21] Action: check (default), returning duplicate info")
                    duplicates.append({
                        "success": True,
                        "doc_id": doc_id,
                        "title": title,
                        "file_node_id": file_node_id,
                        "duplicate": True,
                        "existing_data": existing_node,
                        "message": f"File node '{file_node_id}' already exists. Choose: update, replace, or skip.",
                    })
                    results.append({
                        "success": True,
                        "doc_id": doc_id,
                        "title": title,
                        "file_node_id": file_node_id,
                        "duplicate": True,
                        "existing_data": existing_node,
                    })
                    continue

            print("[UPLOAD-22] Calling upload_original_file...")
            keywords_str = ','.join(file_keywords) if isinstance(file_keywords, list) else str(file_keywords)
            abstract_limited = file_abstract[:500] if file_abstract else ''
            keywords_limited = keywords_str[:200]
            print(f"[UPLOAD-22b] Metadata lengths: title={len(title)}, abstract={len(abstract_limited)}, keywords={len(keywords_limited)}, std_code={len(file_std_code)}, std_name={len(file_std_name)}, ics={len(ics)}, ccs={len(ccs)}, label={len(label)}")
            minio_path = upload_original_file(
                Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, temp_path,
                doc_id, title, category, label, ics, ccs, user_id,
                abstract=abstract_limited, keywords=keywords_limited,
                std_code=file_std_code, std_name=file_std_name,
                publish_date=file_publish_date, issuer=file_issuer,
                review_status='draft' if review_mode else '',
            )
            print(f"[UPLOAD-23] MinIO upload success: {minio_path}")

            image_paths = []

            print("[UPLOAD-23e] Creating Neo4j File node with basic info...")
            if Config.NEO4J_DRIVER:
                try:
                    _create_file_node(
                        file_node_id, category, label, title,
                        "", 0, minio_path, image_paths, user_id,
                        abstract=abstract_limited,
                        keywords=keywords_limited,
                        ics=ics,
                        ccs=ccs,
                        file_type_ext=ext.lstrip("."),
                        std_code=file_std_code,
                        std_name=file_std_name,
                        publish_date=file_publish_date,
                        issuer=file_issuer,
                        review_status='draft' if review_mode else 'confirmed',
                        file_md5=file_md5,
                    )
                    print(f"[UPLOAD-23f] Neo4j File node created: {file_node_id}")
                except Exception as e:
                    logger.warning(f"Failed to create Neo4j node: {e}")

            _ensure_ocr_worker()
            task_id = str(uuid.uuid4())
            _set_task_status(task_id, "queued", error="", doc_id=doc_id)
            _OCR_TASK_QUEUE.put({
                "task_id": task_id,
                "temp_path": temp_path,
                "filename": filename,
                "doc_id": doc_id,
                "title": title,
                "category": category,
                "label": label,
                "user_id": user_id,
                "file_node_id": file_node_id,
                "file_associations": file_associations,
                "file_abstract": file_abstract,
                "file_keywords": file_keywords,
                "file_sync_chroma": file_sync_chroma,
                "file_std_code": file_std_code,
                "file_std_name": file_std_name,
                "ics": ics,
                "ccs": ccs,
                "ext": ext,
                "image_paths": image_paths,
            })
            queued_async = True
            queue_size = _OCR_TASK_QUEUE.qsize()
            print(f"[UPLOAD-23b] Async task queued: task_id={task_id}, queue_size={queue_size}")
            results.append({
                "success": True,
                "doc_id": doc_id,
                "title": title,
                "minio_path": minio_path,
                "file_node_id": file_node_id,
                "ics": ics,
                "ccs": ccs,
                "abstract": abstract_limited,
                "keywords": keywords_limited,
                "async": True,
                "task_id": task_id,
                "queue_position": queue_size,
                "images": len(image_paths),
                "review_mode": review_mode,
                "review_url": f"/ZSGL/WJGL/files/{doc_id}?category={quote(category)}" if review_mode else "",
                "message": "File uploaded. Processing text extraction in background.",
            })
            continue

        except Exception as e:
            logger.exception("[UPLOAD] failed processing file %s: %s", filename, e)
            results.append({"success": False, "file": filename, "error": str(e)})
        finally:
            if (not locals().get("queued_async", False)) and os.path.exists(temp_path):
                os.unlink(temp_path)
                print(f"[UPLOAD-48] Cleaned up temp file: {temp_path}")

    print(f"[UPLOAD-49] All files processed. results count: {len(results)}, duplicates count: {len(duplicates)}")
    response_data = {"success": True, "data": results}
    if duplicates:
        response_data["duplicates"] = duplicates
    print(f"[UPLOAD-50] Returning response: {json.dumps(response_data, ensure_ascii=False)[:500]}")
    n_ok = sum(1 for r in results if isinstance(r, dict) and r.get("success"))
    log_user_action(f"文件管理-上传文件 batch_files={len(files)} success_items={n_ok}")
    return jsonify(response_data)


@wjgl1_bp.route('/resolve-duplicate', methods=['POST'])
def resolve_duplicate():
    """Handle duplicate file upload: update / replace / skip"""
    data = request.get_json()
    file_node_id = data.get('file_node_id', '')
    action = data.get('action', '')
    file_path = data.get('file_path', '')
    category = data.get('category', '其他')
    label = data.get('label', '')
    user_id = data.get('user_id', 'admin')
    title = data.get('title', '')
    ics = data.get('ics', '')
    ccs = data.get('ccs', '')
    abstract = data.get('abstract', '')
    keywords = data.get('keywords', [])
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except:
            keywords = []
    associations = data.get('associations', [])
    sync_chroma = data.get('sync_chroma', True)

    if not file_node_id or not action:
        return jsonify({"success": False, "error": "file_node_id and action required"}), 400

    doc_id = file_node_id.replace("WJ_", "")

    try:
        if action == 'skip':
            log_user_action(f"文件管理-重复文件处理 skip id={file_node_id}")
            return jsonify({"success": True, "action": "skipped", "file_node_id": file_node_id})

        if action == 'replace':
            _delete_existing_file_node(file_node_id, doc_id)

            if file_path and os.path.exists(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                minio_path = upload_original_file(
                    Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, file_path,
                    doc_id, title, category, label, ics, ccs, user_id,
                    abstract=abstract, keywords=keywords
                )

                extract_result = None
                image_paths = []
                extractor = None
                try:
                    extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
                    extract_result = extractor.extract(file_path)
                    image_paths = upload_images_from_extraction(
                        Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, extract_result,
                        doc_id, title, category, user_id,
                        file_ext=ext.lstrip(".")
                    )
                except Exception as e:
                    logger.warning(f"Extraction failed during replace: {e}")

                chroma_id = ""
                chroma_chunks = 0
                if sync_chroma and Config.CHROMA_COLLECTION_OBJ and extract_result and extractor:
                    try:
                        from Database.Chroma.main import process_single_file as chroma_process_single
                        chroma_result = chroma_process_single(
                            file_path, extractor, Config.CHROMA_CLIENT,
                            Config.CHROMA_COLLECTION_OBJ, Config.MINIO_BUCKET_NAME,
                            Config.MINIO_CLIENT, "",
                            pre_extracted_result=extract_result,
                            pre_doc_id=doc_id,
                            pre_title=title,
                            category=category
                        )
                        if chroma_result.get("success"):
                            chroma_id = chroma_result.get("doc_id", "")
                            chroma_chunks = chroma_result.get("chunks", 0)
                    except Exception as e:
                        logger.warning(f"Chroma sync failed during replace: {e}")

                _create_file_node(
                    file_node_id, category, label, title,
                    chroma_id, chroma_chunks, minio_path, image_paths, user_id,
                    abstract=abstract, keywords=keywords, ics=ics, ccs=ccs,
                    file_type_ext=ext.lstrip("."),
                )
                if associations:
                    _create_file_associations(file_node_id, associations)

                log_user_action(f"文件管理-重复文件处理 replace id={file_node_id}")
                return jsonify({
                    "success": True, "action": "replaced", "file_node_id": file_node_id,
                    "chroma_id": chroma_id, "chroma_chunks": chroma_chunks,
                })

        if action == 'update':
            if Config.NEO4J_DRIVER:
                with Config.NEO4J_DRIVER.session() as session:
                    session.run(
                        "MATCH (n:File {Id: $id}) SET n.Name = $name, n.Label = $label, "
                        "n.Type = $type, n.ICS = $ics, n.CCS = $ccs, n.Abstract = $abstract, "
                        "n.Keywords = $keywords, n.Create_time = $create_time",
                        id=file_node_id, name=title, label=label, type=category,
                        ics=ics, ccs=ccs, abstract=abstract, keywords=keywords,
                        create_time=datetime.now(timezone.utc).isoformat()
                    )

            if file_path and os.path.exists(file_path) and sync_chroma and Config.CHROMA_COLLECTION_OBJ:
                try:
                    extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
                    extract_result = extractor.extract(file_path)
                    if extract_result:
                        _delete_existing_file_node("", doc_id)
                        from Database.Chroma.main import process_single_file as chroma_process_single
                        chroma_result = chroma_process_single(
                            file_path, extractor, Config.CHROMA_CLIENT,
                            Config.CHROMA_COLLECTION_OBJ, Config.MINIO_BUCKET_NAME,
                            Config.MINIO_CLIENT, "",
                            pre_extracted_result=extract_result,
                            pre_doc_id=doc_id,
                            pre_title=title,
                            category=category
                        )
                        if chroma_result.get("success"):
                            chroma_id = chroma_result.get("doc_id", "")
                            chroma_chunks = chroma_result.get("chunks", 0)
                            if Config.NEO4J_DRIVER:
                                with Config.NEO4J_DRIVER.session() as session:
                                    session.run(
                                        "MATCH (n:File {Id: $id}) SET n.Chroma_id = $chroma_id, n.Chroma_chunks = $chroma_chunks",
                                        id=file_node_id, chroma_id=chroma_id, chroma_chunks=chroma_chunks
                                    )
                except Exception as e:
                    logger.warning(f"Chroma update failed: {e}")

            log_user_action(f"文件管理-重复文件处理 update id={file_node_id}")
            return jsonify({"success": True, "action": "updated", "file_node_id": file_node_id})

    except Exception as e:
        logger.error(f"Resolve duplicate failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({"success": False, "error": "Unknown action"}), 400


@wjgl1_bp.route('/task-status', methods=['GET'])
def get_task_status():
    task_id = request.args.get('task_id', '')
    if not task_id:
        return jsonify({"success": False, "error": "task_id required"}), 400
    
    with _OCR_STATUS_LOCK:
        status = _OCR_TASK_STATUS.get(task_id, {})
    
    if not status:
        return jsonify({"success": False, "error": "Task not found"}), 404
    
    return jsonify({"success": True, "data": status})


@wjgl1_bp.route('/category-counts', methods=['GET'])
def category_counts():
    """按文件类别统计 MinIO 中原始文件数量（键为中文类别名，与前端 TAB / 上传目录一致）。"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500
    try:
        data = {cat: _count_wjgl_origin_files_for_category(cat) for cat in TYPE_PREFIX_MAP.keys()}
        return jsonify({"success": True, "data": data})
    except Exception as e:
        logger.error("category-counts failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wjgl1_bp.route('/list', methods=['GET'])
def list_files():
    """List files from MinIO with filters"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    category = request.args.get('category', '')
    file_type = request.args.get('file_type', '')
    keyword = request.args.get('keyword', '')
    std_code = (request.args.get("std_code") or "").strip()
    upload_user = (request.args.get("upload_user") or "").strip()
    upload_time_from = request.args.get('upload_time_from', '')
    upload_time_to = request.args.get('upload_time_to', '')

    prefix = f"{category}/" if category else ""
    all_objects = list(Config.MINIO_CLIENT.list_objects(
        Config.MINIO_BUCKET_NAME, prefix=prefix, recursive=True
    ))
    
    objects = all_objects

    neo_abstract_map = {}
    if Config.NEO4J_DRIVER:
        try:
            with Config.NEO4J_DRIVER.session() as session:
                q = (
                    "MATCH (n:File) WHERE n.Abstract IS NOT NULL AND trim(toString(n.Abstract)) <> '' "
                )
                params = {}
                if category:
                    q += "AND n.Category = $category "
                    params["category"] = category
                q += "RETURN n.Id AS id, n.Abstract AS abstract"
                for rec in session.run(q, **params):
                    fid = str(rec.get("id") or "")
                    ab = str(rec.get("abstract") or "").strip()
                    if not fid or not ab:
                        continue
                    doc_key = fid[3:] if fid.upper().startswith("WJ_") else fid
                    neo_abstract_map[doc_key] = ab
        except Exception as neo_exc:
            logger.debug("list_files neo abstract map failed: %s", neo_exc)

    files = []
    for obj in objects:
        if file_type and not obj.object_name.lower().endswith(f".{file_type.lower()}"):
            continue
        
        if "/images/" in obj.object_name or "/figures/" in obj.object_name or "/ocr_" in obj.object_name:
            continue

        if not _is_wjgl_origin_object_key(obj.object_name):
            continue

        try:
            stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, obj.object_name)
            meta = {}
            if stat.metadata:
                for k, v in stat.metadata.items():
                    if v:
                        try:
                            meta[k] = unquote(v)
                        except:
                            meta[k] = v
                    else:
                        meta[k] = v

            title = meta.get("x-amz-meta-title", "")
            if keyword and keyword.lower() not in title.lower():
                continue

            if std_code:
                sc = (meta.get("x-amz-meta-std-code", "") or "").lower()
                if std_code.lower() not in sc:
                    continue

            if upload_user:
                uu = (meta.get("x-amz-meta-upload-userid", "") or "").lower()
                if upload_user.lower() not in uu:
                    continue

            if upload_time_from:
                ut = meta.get("x-amz-meta-upload-time", "")
                if ut:
                    ut_date = ut[:10]
                    if ut_date < upload_time_from:
                        continue
            if upload_time_to:
                ut = meta.get("x-amz-meta-upload-time", "")
                if ut:
                    ut_date = ut[:10]
                    if ut_date > upload_time_to:
                        continue

            files.append({
                "object_name": obj.object_name,
                "doc_id": meta.get("x-amz-meta-doc-id", ""),
                "title": meta.get("x-amz-meta-title", ""),
                "category": meta.get("x-amz-meta-category", ""),
                "file_type": meta.get("x-amz-meta-file-type", ""),
                "file_label": meta.get("x-amz-meta-file-label", ""),
                "ics": meta.get("x-amz-meta-file-ics", ""),
                "ccs": meta.get("x-amz-meta-file-ccs", ""),
                "std_code": meta.get("x-amz-meta-std-code", ""),
                "std_name": meta.get("x-amz-meta-std-name", ""),
                "publish_date": meta.get("x-amz-meta-publish-date", ""),
                "issuer": meta.get("x-amz-meta-issuer", ""),
                "review_status": meta.get("x-amz-meta-review-status", ""),
                "upload_userid": meta.get("x-amz-meta-upload-userid", ""),
                "upload_time": meta.get("x-amz-meta-upload-time", ""),
                "abstract": (
                    meta.get("x-amz-meta-abstract", "")
                    or neo_abstract_map.get(meta.get("x-amz-meta-doc-id", ""), "")
                    or ""
                ),
                "keywords": meta.get("x-amz-meta-keywords", ""),
                "file_size": obj.size,
                "last_modified": str(obj.last_modified),
            })
        except Exception as e:
            logger.warning(f"Failed to get metadata for {obj.object_name}: {e}")

    return jsonify({"success": True, "data": files, "count": len(files)})


@wjgl1_bp.route("/object-meta", methods=["GET"])
def wjgl_object_meta():
    """按 MinIO object_name 返回文件元数据，供标准阅读页等多格式预览使用。"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500
    object_name = (request.args.get("object_name") or "").strip()
    if not object_name:
        return jsonify({"success": False, "error": "object_name required"}), 400
    if not _is_safe_wjgl_object_name(object_name):
        return jsonify({"success": False, "error": "invalid object_name"}), 400
    try:
        stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
        meta = {}
        if stat.metadata:
            for k, v in stat.metadata.items():
                lk = k.lower() if isinstance(k, str) else k
                if v:
                    try:
                        meta[lk] = unquote(v)
                    except Exception:
                        meta[lk] = v
                else:
                    meta[lk] = v
        doc_id = meta.get("x-amz-meta-doc-id", "") or ""
        ext = (object_name.rsplit(".", 1)[-1] or "").lower()
        file_type = (meta.get("x-amz-meta-file-type", "") or ext or "").lower()
        preview = "other"
        if file_type == "pdf" or ext == "pdf":
            preview = "pdf"
        elif ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff"):
            preview = "image"
        elif is_word_native_preview(object_name):
            preview = "office_word"
        elif is_excel_native_preview(object_name):
            preview = "office_excel"
        elif is_ppt_convertible(object_name):
            preview = "office_pdf"
        source_id = f"WJ_{doc_id}" if doc_id else ""
        preview_pdf_url = ""
        if preview == "office_pdf":
            preview_pdf_url = f"/api/wjgl/preview-pdf?object_name={quote(object_name, safe='')}"
        return jsonify(
            {
                "success": True,
                "data": {
                    "object_name": object_name,
                    "doc_id": doc_id,
                    "source_id": source_id,
                    "title": meta.get("x-amz-meta-title", ""),
                    "category": meta.get("x-amz-meta-category", ""),
                    "std_code": meta.get("x-amz-meta-std-code", ""),
                    "abstract": meta.get("x-amz-meta-abstract", ""),
                    "file_type": file_type,
                    "preview_kind": preview,
                    "preview_pdf_url": preview_pdf_url,
                    "metadata": meta,
                },
            }
        )
    except Exception as e:
        logger.warning("object-meta failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 404


@wjgl1_bp.route("/preview-pdf/status", methods=["GET"])
def wjgl_preview_pdf_status():
    """诊断：后端是否能找到 LibreOffice（与前端 react-doc-viewer 无关）。"""
    return jsonify({"success": True, "data": office_convert_status()})


@wjgl1_bp.route("/preview-pdf", methods=["GET"])
def wjgl_preview_pdf():
    """PPT 等演示文稿转 PDF 后 inline 返回（带本地缓存）；Word/Excel 请走 object-file 前端预览。"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500
    object_name = (request.args.get("object_name") or "").strip()
    if not object_name:
        return jsonify({"success": False, "error": "object_name required"}), 400
    if not _is_safe_wjgl_object_name(object_name):
        return jsonify({"success": False, "error": "invalid object_name"}), 400
    if not is_ppt_convertible(object_name):
        return jsonify({"success": False, "error": "仅 PPT/PPTX 支持转 PDF 预览；Word/Excel 请使用 object-file 前端预览"}), 400
    try:
        stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
        etag = getattr(stat, "etag", "") or ""
        size = int(getattr(stat, "size", 0) or 0)
        pdf_path = get_or_create_preview_pdf(
            Config.MINIO_CLIENT,
            Config.MINIO_BUCKET_NAME,
            object_name,
            etag=etag,
            size=size,
        )
        return send_file(
            pdf_path,
            mimetype="application/pdf",
            as_attachment=False,
            download_name=os.path.splitext(object_name.rsplit("/", 1)[-1])[0] + ".pdf",
        )
    except RuntimeError as e:
        logger.warning("preview-pdf failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 503
    except Exception as e:
        logger.warning("preview-pdf failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@wjgl1_bp.route("/object-file", methods=["GET"])
def wjgl_object_file():
    """流式返回 MinIO 原始文件（inline 预览）。"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500
    object_name = (request.args.get("object_name") or "").strip()
    if not object_name:
        return jsonify({"success": False, "error": "object_name required"}), 400
    if not _is_safe_wjgl_object_name(object_name):
        return jsonify({"success": False, "error": "invalid object_name"}), 400
    try:
        resp = Config.MINIO_CLIENT.get_object(Config.MINIO_BUCKET_NAME, object_name)
        data = resp.read()
        resp.close()
        resp.release_conn()
        ext = (object_name.rsplit(".", 1)[-1] or "").lower()
        mime, _ = mimetypes.guess_type(f"file.{ext}")
        if not mime:
            mime = "application/octet-stream"
        basename = object_name.rsplit("/", 1)[-1]
        return Response(
            data,
            mimetype=mime,
            headers={"Content-Disposition": f'inline; filename="{basename}"'},
        )
    except Exception as e:
        logger.warning("object-file failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 404


@wjgl1_bp.route('/delete', methods=['POST'])
def delete_file():
    """Delete a file and cascade MinIO parse artifacts, Chroma vectors, Neo4j File node, local cache."""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    data = request.get_json() or {}
    object_name = (data.get('object_name') or '').strip()

    if not object_name:
        return jsonify({"success": False, "error": "object_name required"}), 400

    category, doc_id = _resolve_category_doc_id(
        object_name,
        doc_id=(data.get('doc_id') or '').strip(),
        category=(data.get('category') or '').strip(),
    )

    try:
        stats = _cascade_delete_wjgl_document(
            category=category,
            doc_id=doc_id,
            object_name=object_name,
        )
        log_user_action(
            f"文件管理-删除文件 object={object_name[:200]} doc_id={doc_id} "
            f"minio={stats.get('minio_deleted')} chroma={stats.get('chroma_deleted')} "
            f"neo4j={stats.get('neo4j_deleted')}"
        )
        return jsonify({
            "success": True,
            "message": "File deleted",
            "data": stats,
        })
    except Exception as e:
        logger.error("[wjgl-delete] cascade delete failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@wjgl1_bp.route('/update', methods=['POST'])
def update_file():
    """Update file metadata in MinIO and Neo4j"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    data = request.get_json()
    object_name = data.get('object_name', '')
    updates = data.get('updates', {})

    if not object_name:
        return jsonify({"success": False, "error": "object_name required"}), 400

    try:
        stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
        existing_meta = {}
        if stat.metadata:
            for k, v in stat.metadata.items():
                existing_meta[k] = v

        for k, v in updates.items():
            key = k if k.startswith("x-amz-meta-") else f"x-amz-meta-{k}"
            existing_meta[key] = _safe_meta(str(v))

        import tempfile as _tf
        with _tf.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
            Config.MINIO_CLIENT.fget_object(Config.MINIO_BUCKET_NAME, object_name, tmp_path)
            Config.MINIO_CLIENT.fput_object(
                Config.MINIO_BUCKET_NAME, object_name, tmp_path,
                metadata=existing_meta
            )
            os.unlink(tmp_path)

        if Config.NEO4J_DRIVER:
            doc_id = data.get('doc_id', '')
            if doc_id:
                file_node_id = f"WJ_{doc_id}"
                neo4j_updates = {}
                meta_map = {
                    'title': 'Name',
                    'file-label': 'Label',
                    'file-ics': 'ICS',
                    'file-ccs': 'CCS',
                    'abstract': 'Abstract',
                    'keywords': 'Keywords',
                    'std-code': 'Std_Code',
                }
                for k, v in updates.items():
                    clean_key = k.replace('x-amz-meta-', '')
                    if clean_key in meta_map:
                        neo4j_updates[meta_map[clean_key]] = v

                if neo4j_updates:
                    set_clause = ', '.join([f"n.{k} = ${k}" for k in neo4j_updates.keys()])
                    params = {'id': file_node_id, **neo4j_updates}
                    with Config.NEO4J_DRIVER.session() as session:
                        session.run(
                            f"MATCH (n:File {{Id: $id}}) SET {set_clause}",
                            params
                        )

        log_user_action(f"文件管理-更新文件元数据 object={object_name[:200]}")
        return jsonify({"success": True, "message": "Metadata updated"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@wjgl1_bp.route('/ccs-tree', methods=['GET'])
def get_ccs_tree():
    """Get CCS-based category tree for standards"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    category = request.args.get('category', '法规标准')
    objects = Config.MINIO_CLIENT.list_objects(
        Config.MINIO_BUCKET_NAME, prefix=f"{category}/", recursive=True
    )

    ccs_map = {}
    for obj in objects:
        if '/images/' in obj.object_name:
            continue
        if not _is_wjgl_origin_object_key(obj.object_name):
            continue
        try:
            stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, obj.object_name)
            meta = {}
            if stat.metadata:
                for k, v in stat.metadata.items():
                    meta[k] = unquote(v) if v else v
            ccs = meta.get("x-amz-meta-file-ccs", "未分类")
            if ccs not in ccs_map:
                ccs_map[ccs] = []
            ccs_map[ccs].append({
                "doc_id": meta.get("x-amz-meta-doc-id", ""),
                "std_code": meta.get("x-amz-meta-std-code", ""),
                "title": meta.get("x-amz-meta-title", ""),
                "object_name": obj.object_name,
            })
        except Exception:
            pass

    tree = []
    for ccs, files in sorted(ccs_map.items()):
        tree.append({
            "title": f"CCS: {ccs}",
            "key": f"ccs_{ccs}",
            "ccs": ccs,
            "children": files,
        })

    return jsonify({"success": True, "data": tree})


@wjgl1_bp.route('/label-tree', methods=['GET'])
def get_label_tree():
    """Get label-based category tree for non-standard files"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    category = request.args.get('category', '')
    prefix = f"{category}/" if category else ""
    objects = Config.MINIO_CLIENT.list_objects(
        Config.MINIO_BUCKET_NAME, prefix=prefix, recursive=True
    )

    label_map = {}
    for obj in objects:
        if '/images/' in obj.object_name:
            continue
        if not _is_wjgl_origin_object_key(obj.object_name):
            continue
        try:
            stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, obj.object_name)
            meta = {}
            if stat.metadata:
                for k, v in stat.metadata.items():
                    meta[k] = unquote(v) if v else v
            label = meta.get("x-amz-meta-file-label", "未标签")
            if label not in label_map:
                label_map[label] = []
            label_map[label].append({
                "doc_id": meta.get("x-amz-meta-doc-id", ""),
                "title": meta.get("x-amz-meta-title", ""),
                "object_name": obj.object_name,
            })
        except Exception:
            pass

    tree = []
    for label, files in sorted(label_map.items()):
        tree.append({
            "title": f"标签: {label}",
            "key": f"label_{label}",
            "label": label,
            "children": files,
        })

    return jsonify({"success": True, "data": tree})


@wjgl1_bp.route('/document-toc', methods=['GET'])
def document_toc():
    """解析已上传文件的目录结构，返回章节树供方法提取选择（不阻塞 OCR）。"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    object_name = (request.args.get('object_name') or '').strip()
    doc_id = (request.args.get('doc_id') or '').strip()
    category = (request.args.get('category') or '').strip()
    if not object_name and not doc_id:
        return jsonify({"success": False, "error": "object_name 或 doc_id 至少提供一个"}), 400

    category, doc_id = _resolve_category_doc_id(object_name, doc_id, category)
    toc = []
    source = "none"
    pending = False
    task_status = _ocr_task_status_for_doc(doc_id) if doc_id else {}
    parse_ready = bool(category and doc_id and _parse_cache_ready(category, doc_id))

    # 0) 优先使用入库/审核时已保存的目录（toc.json）
    if category and doc_id:
        try:
            from .document_parse_service import load_saved_toc

            saved_toc = load_saved_toc(category, doc_id)
            if saved_toc:
                log_user_action(f"文件管理-获取文档目录(saved_toc) object={object_name or doc_id}")
                return jsonify({
                    "success": True,
                    "data": {
                        "toc": saved_toc,
                        "source": "saved_toc",
                        "pending": False,
                        "parse_ready": True,
                    },
                })
        except Exception as e:
            logger.warning("[document-toc] load saved toc 失败: %s", e)

    # 1) 优先 Markdown 标题目录（最准），其次已保存 toc
    if category and doc_id:
        try:
            from .document_parse_service import load_parse_artifacts, resolve_document_toc

            cached = load_parse_artifacts(category, doc_id)
            if cached:
                toc, source = resolve_document_toc(category, doc_id, cached)
                if toc:
                    log_user_action(f"文件管理-获取文档目录({source}) object={object_name or doc_id}")
                    return jsonify({
                        "success": True,
                        "data": {
                            "toc": toc,
                            "source": source,
                            "pending": False,
                            "parse_ready": True,
                        },
                    })
        except Exception as e:
            logger.warning("[document-toc] resolve toc 失败: %s", e)

        if parse_ready:
            log_user_action(f"文件管理-获取文档目录(缓存空) object={object_name or doc_id}")
            return jsonify({
                "success": True,
                "data": {
                    "toc": [],
                    "source": "parse_cache_empty",
                    "pending": False,
                    "parse_ready": True,
                    "message": "解析缓存存在但未识别到目录，请重新上传或触发解析",
                },
            })

        if not toc:
            pending = (
                not parse_ready
                and task_status.get("status") in ("queued", "processing", None)
                and bool(task_status)
            ) or (
                not parse_ready
                and not task_status
            )

    import tempfile as _tf

    temp_dir = _tf.mkdtemp()
    temp_path = None
    try:
        if object_name:
            if not _is_safe_wjgl_object_name(object_name):
                return jsonify({"success": False, "error": "invalid object_name"}), 400
            temp_path = os.path.join(temp_dir, os.path.basename(object_name))
            Config.MINIO_CLIENT.fget_object(Config.MINIO_BUCKET_NAME, object_name, temp_path)
        else:
            from Database.minIo.main import download_file_by_doc_id
            result = download_file_by_doc_id(
                Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, doc_id
            )
            if not result or not result.get('path'):
                if pending:
                    return jsonify({
                        "success": True,
                        "data": {
                            "toc": [],
                            "source": "pending",
                            "pending": True,
                            "parse_ready": False,
                            "task_status": task_status.get("status") or "processing",
                            "message": "文档后台解析中，请稍候…",
                        },
                    })
                return jsonify({"success": False, "error": "文件不存在"}), 404
            temp_path = result['path']

        ext = os.path.splitext(temp_path)[1].lower()

        if ext in ('.docx', '.doc'):
            file_size = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
            logger.info(f"[document-toc] 下载文件: {temp_path}, 大小={file_size} bytes")
            toc = _extract_toc_from_word(temp_path)
            source = "word"
            logger.info(f"[document-toc] Word 提取完成，共 {len(toc)} 个顶级节点")
        elif ext == '.pdf':
            toc = _extract_toc_from_pdf_fast(temp_path)
            source = "pdf_fast"
            logger.info(f"[document-toc] PDF 快速提取完成，共 {len(toc)} 个顶级节点")
            if toc:
                _log_toc_tree(toc, prefix="[document-toc] ")
        else:
            return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

        if not toc:
            logger.warning(f"[document-toc] 未提取到目录，文件: {object_name or doc_id}")

        from .body_content_trim import filter_toc_from_chapter_one
        toc = filter_toc_from_chapter_one(toc or [])

        if not toc and category and doc_id and not parse_ready:
            pending = True

        log_user_action(f"文件管理-获取文档目录 object={object_name or doc_id}")
        return jsonify({
            "success": True,
            "data": {
                "toc": toc,
                "source": source,
                "pending": pending,
                "parse_ready": bool(toc) or parse_ready,
                "task_status": task_status.get("status") or ("" if not pending else "processing"),
                "message": "文档后台解析中，请稍候…" if pending and not toc else "",
            },
        })

    except Exception as e:
        logger.error(f"document-toc failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


@wjgl1_bp.route('/document-toc-upload', methods=['POST'])
def document_toc_upload():
    """从上传的本地文件提取目录结构（不保存到 MinIO），供上传弹窗中使用"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "没有文件"}), 400

    uploaded_file = request.files['file']
    if not uploaded_file.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    ext = os.path.splitext(uploaded_file.filename)[1].lower()
    if ext not in ('.docx', '.doc', '.pdf'):
        return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

    import tempfile as _tf

    temp_dir = _tf.mkdtemp()
    temp_path = os.path.join(temp_dir, uploaded_file.filename)
    try:
        uploaded_file.save(temp_path)

        if ext in ('.docx', '.doc'):
            toc = _extract_toc_from_word(temp_path)
            logger.info(f"[document-toc-upload] Word 提取完成，共 {len(toc)} 个顶级节点")
        elif ext == '.pdf':
            toc = _extract_toc_from_pdf_fast(temp_path)
            logger.info(f"[document-toc-upload] PDF 快速提取完成，共 {len(toc)} 个顶级节点")
        else:
            return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

        if not toc:
            logger.warning(f"[document-toc-upload] 未提取到目录，文件: {uploaded_file.filename}")

        from .body_content_trim import filter_toc_from_chapter_one
        toc = filter_toc_from_chapter_one(toc or [])

        return jsonify({"success": True, "data": {"toc": toc}})

    except Exception as e:
        logger.error(f"document-toc-upload failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def _section_payload_to_html(payload: dict) -> str:
    """章节提取结果转 HTML（供审核页编辑器）。"""
    import html as html_module
    import base64

    if payload.get("html"):
        return payload["html"]
    parts = []
    content = (payload.get("content") or "").strip()
    if content:
        for line in content.split("\n"):
            s = line.strip()
            if s:
                parts.append(f"<p>{html_module.escape(s)}</p>")
    for t in payload.get("tables") or []:
        if t:
            parts.append(f'<div class="doc-table">{t}</div>')
    for img in payload.get("images") or []:
        b64 = img.get("base64")
        if not b64 and img.get("blob"):
            ct = img.get("content_type") or "image/png"
            b64 = f"data:{ct};base64,{base64.b64encode(img['blob']).decode()}"
        if b64:
            parts.append(f'<figure><img src="{b64}" style="max-width:100%"/></figure>')
    return "".join(parts)


def _download_wjgl_temp_file(object_name: str, doc_id: str, temp_dir: str):
    """下载 MinIO 文件到临时目录，返回 local path。"""
    if object_name:
        if not _is_safe_wjgl_object_name(object_name):
            raise ValueError("invalid object_name")
        temp_path = os.path.join(temp_dir, os.path.basename(object_name))
        Config.MINIO_CLIENT.fget_object(Config.MINIO_BUCKET_NAME, object_name, temp_path)
        return temp_path
    from Database.minIo.main import download_file_by_doc_id
    result = download_file_by_doc_id(Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, doc_id)
    if not result or not result.get("path"):
        raise FileNotFoundError("文件不存在")
    return result["path"]


def _section_anchor_id(section_no: str) -> str:
    return "wjgl-sec-" + (section_no or "").lstrip(".").replace(".", "-")


def _append_figure_html(parts: list, figure: dict) -> None:
    import base64
    img_path = figure.get("path", "")
    if img_path and os.path.exists(img_path):
        try:
            with open(img_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
            ext = os.path.splitext(img_path)[1].lower()
            mime = "image/png" if ext == ".png" else "image/jpeg"
            parts.append(f'<figure><img src="data:{mime};base64,{img_data}" style="max-width:100%"/></figure>')
        except Exception as e:
            logger.warning("figure html failed: %s", e)


def _full_document_html_from_pdf(pdf_path: str, ocr_output_dir: str = None, embed_figures: bool = True, *, preview: bool = False) -> str:
    """PDF 全文 HTML（含表格/图片），章节标题带锚点。"""
    from .document_parse_service import build_full_document_html
    return build_full_document_html(
        pdf_path,
        ocr_output_dir=ocr_output_dir,
        embed_figures=embed_figures,
        preview=preview,
    )


def _full_document_html_from_docx(docx_path: str, ocr_output_dir: str = None, embed_figures: bool = True, *, preview: bool = False) -> str:
    """Word 全文 HTML（含表格/图片），章节标题带锚点。"""
    from .document_parse_service import build_full_document_html
    return build_full_document_html(
        docx_path,
        ocr_output_dir=ocr_output_dir,
        embed_figures=embed_figures,
        preview=preview,
    )


@wjgl1_bp.route('/document-full-content', methods=['POST'])
def document_full_content():
    """读取已入库的全文 HTML（上传时已解析落盘）。不再重复 OCR。"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    data = request.json or {}
    object_name = (data.get("object_name") or "").strip()
    doc_id = (data.get("doc_id") or "").strip()
    category = (data.get("category") or "").strip()
    if not object_name and not doc_id:
        return jsonify({"success": False, "error": "object_name 或 doc_id 至少提供一个"}), 400

    if not category and object_name:
        category = object_name.split("/")[0] if "/" in object_name else ""

    from .document_parse_service import load_full_document_html, load_parse_artifacts

    if category and doc_id:
        html = load_full_document_html(category, doc_id)
        if html:
            cached = load_parse_artifacts(category, doc_id) or {}
            return jsonify({
                "success": True,
                "data": {
                    "html": html,
                    "from_cache": True,
                    "document_blocks": cached.get("document_blocks") or [],
                },
            })
        return jsonify({
            "success": False,
            "error": "文档结构化解析未完成，请等待上传后台处理完成后再打开审核页",
            "code": "parse_pending",
        }), 409

    return jsonify({
        "success": False,
        "error": "缺少 category，无法读取已保存的全文",
        "code": "missing_category",
    }), 400


@wjgl1_bp.route('/document-full-content-upload', methods=['POST'])
def document_full_content_upload():
    """上传文件全文 HTML 预览（不入库）。"""
    if "file" not in request.files:
        return jsonify({"success": False, "error": "没有文件"}), 400
    uploaded_file = request.files["file"]
    if not uploaded_file.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    ext = os.path.splitext(uploaded_file.filename)[1].lower()
    if ext not in (".docx", ".doc", ".pdf"):
        return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

    import tempfile as _tf
    temp_dir = _tf.mkdtemp()
    temp_path = os.path.join(temp_dir, uploaded_file.filename)
    cache_key = __import__("hashlib").md5(uploaded_file.filename.encode()).hexdigest()[:12]
    ocr_dir = os.path.join(_tf.gettempdir(), "wjgl_ocr_cache", cache_key)
    os.makedirs(ocr_dir, exist_ok=True)
    try:
        uploaded_file.save(temp_path)
        lite = request.args.get("lite", "").strip().lower() in ("1", "true", "yes")
        embed_figures = not lite
        mineru_key = None
        preview_mode = "fitz"
        if ext == ".pdf":
            try:
                from pdf_extract.mineru_preview_service import start_preview_parse
                from pdf_extract.mineru_wjgl_service import use_mineru_for_wjgl_pdf, wjgl_upload_preview_mode
                import shutil

                preview_mode = wjgl_upload_preview_mode()
                if use_mineru_for_wjgl_pdf():
                    persist_dir = os.path.join(_tf.gettempdir(), "wjgl_mineru_preview", cache_key)
                    os.makedirs(persist_dir, exist_ok=True)
                    persist_pdf = os.path.join(persist_dir, os.path.basename(temp_path))
                    shutil.copy2(temp_path, persist_pdf)
                    mineru_key = start_preview_parse(persist_pdf, force=True)
                    logger.info(
                        "[document-full-content-upload] MinerU 后台解析已排队 key=%s preview=%s",
                        mineru_key,
                        preview_mode,
                    )
            except Exception as me:
                logger.warning("[document-full-content-upload] MinerU 后台启动失败: %s", me)
        if ext in (".docx", ".doc"):
            html = _full_document_html_from_docx(temp_path, ocr_output_dir=ocr_dir, embed_figures=embed_figures, preview=True)
        elif ext == ".pdf" and preview_mode == "mineru" and mineru_key:
            html = ""
        else:
            html = _full_document_html_from_pdf(temp_path, ocr_output_dir=ocr_dir, embed_figures=embed_figures, preview=True)
        if mineru_key and (html or "").strip() and preview_mode == "fitz":
            try:
                from pdf_extract.mineru_preview_service import save_preview_draft_html

                save_preview_draft_html(mineru_key, html)
            except Exception as draft_err:
                logger.warning("[document-full-content-upload] fitz 预览 HTML 落盘失败: %s", draft_err)
        if request.args.get("format", "").strip().lower() == "html":
            from flask import Response
            headers = {
                "X-WJGL-Success": "1",
                "X-WJGL-Html-Bytes": str(len(html or "")),
                "X-WJGL-Preview-Mode": preview_mode,
                "Access-Control-Expose-Headers": "X-WJGL-MinerU-Key, X-WJGL-Success, X-WJGL-Html-Bytes, X-WJGL-Preview-Mode",
            }
            if mineru_key:
                headers["X-WJGL-MinerU-Key"] = mineru_key
            return Response(
                html or "",
                mimetype="text/html; charset=utf-8",
                headers=headers,
            )
        payload = {"success": True, "data": {"html": html}}
        if mineru_key:
            payload["data"]["mineru_key"] = mineru_key
        return jsonify(payload)
    except Exception as e:
        logger.exception("document-full-content-upload failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def _slim_mineru_preview_task(task: dict) -> dict:
    """状态轮询不返回巨型 markdown/html，避免 Next 代理 JSON 解析失败。"""
    if not isinstance(task, dict):
        return {}
    slim = {k: v for k, v in task.items() if k not in ("markdown", "html")}
    html = task.get("html") or ""
    md = task.get("markdown") or ""
    slim["has_html"] = bool(task.get("has_html")) or bool(str(html).strip())
    slim["has_markdown"] = bool(task.get("has_markdown")) or bool(str(md).strip())
    slim_html_bytes = task.get("html_bytes")
    slim_md_bytes = task.get("markdown_bytes")
    slim["html_bytes"] = int(slim_html_bytes) if isinstance(slim_html_bytes, int) else 0
    slim["markdown_bytes"] = int(slim_md_bytes) if isinstance(slim_md_bytes, int) else 0
    if task.get("status") in ("done", "running"):
        try:
            from pdf_extract.mineru_preview_service import preview_local_dir

            local_dir = preview_local_dir(str(task.get("cache_key") or ""))
            html_path = local_dir / "document.html"
            md_path = local_dir / "document.md"
            if html_path.is_file():
                slim["has_html"] = True
                slim["html_bytes"] = html_path.stat().st_size
            elif html and not slim["html_bytes"]:
                slim["html_bytes"] = len(str(html).encode("utf-8"))
            if md_path.is_file():
                slim["has_markdown"] = True
                slim["markdown_bytes"] = md_path.stat().st_size
            elif md and not slim["markdown_bytes"]:
                slim["markdown_bytes"] = len(str(md).encode("utf-8"))
        except Exception:
            if html and not slim["html_bytes"]:
                slim["html_bytes"] = len(str(html).encode("utf-8"))
            if md and not slim["markdown_bytes"]:
                slim["markdown_bytes"] = len(str(md).encode("utf-8"))
    return slim


@wjgl1_bp.route('/document-mineru-parse/status', methods=['GET'])
def document_mineru_parse_status():
    """轮询上传弹窗 MinerU 后台解析状态（轻量 JSON，不含正文）。"""
    cache_key = (request.args.get("key") or request.args.get("cache_key") or "").strip()
    if not cache_key:
        return jsonify({"success": False, "error": "缺少 key"}), 400
    try:
        from pdf_extract.mineru_preview_service import get_preview_task

        task = get_preview_task(cache_key, include_content=False)
        return jsonify({"success": True, "data": _slim_mineru_preview_task(task)})
    except Exception as e:
        logger.exception("[document-mineru-parse/status] key=%s failed: %s", cache_key, e)
        return jsonify({"success": False, "error": str(e)}), 500


@wjgl1_bp.route('/document-mineru-parse/content', methods=['GET'])
def document_mineru_parse_content():
    """MinerU 预览正文：HTML 或 Markdown（大文件单独拉取）。"""
    cache_key = (request.args.get("key") or request.args.get("cache_key") or "").strip()
    fmt = (request.args.get("format") or "html").strip().lower()
    if not cache_key:
        return jsonify({"success": False, "error": "缺少 key"}), 400
    from pdf_extract.mineru_preview_service import get_preview_task, preview_local_dir
    from flask import Response

    task = get_preview_task(cache_key, include_content=True)
    status = task.get("status")
    local_dir = preview_local_dir(cache_key)
    html_path = local_dir / "document.html"
    can_serve_html_draft = fmt not in ("md", "markdown") and html_path.is_file()
    if status not in ("done",) and not task.get("from_disk") and not can_serve_html_draft:
        return jsonify({
            "success": False,
            "error": "MinerU 解析未完成",
            "code": "parse_pending",
            "data": _slim_mineru_preview_task(task),
        }), 409

    if fmt in ("md", "markdown"):
        md_path = local_dir / "document.md"
        if not md_path.is_file():
            md_path = Path(str(task.get("md_path") or ""))
        if md_path.is_file():
            body = md_path.read_text(encoding="utf-8", errors="replace")
            return Response(body, mimetype="text/markdown; charset=utf-8")
        md = str(task.get("markdown") or "")
        if md.strip():
            return Response(md, mimetype="text/markdown; charset=utf-8")
        return jsonify({"success": False, "error": "Markdown 为空"}), 404

    html_path = local_dir / "document.html"
    html = html_path.read_text(encoding="utf-8", errors="replace") if html_path.is_file() else ""
    if not html.strip():
        html = str(task.get("html") or "")
    if not html.strip():
        return jsonify({"success": False, "error": "HTML 为空"}), 404
    return Response(html, mimetype="text/html; charset=utf-8")


@wjgl1_bp.route('/document-section-content', methods=['POST'])
def document_section_content():
    """预览单个章节内容（优先 MinIO 已保存章节，否则 PDF 切分）。"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    data = request.json or {}
    object_name = (data.get('object_name') or '').strip()
    doc_id = (data.get('doc_id') or '').strip()
    category = (data.get('category') or '').strip()
    section_no = (data.get('sectionNo') or data.get('section_no') or '').strip()
    title = (data.get('title') or '').strip()
    if not object_name and not doc_id:
        return jsonify({"success": False, "error": "object_name 或 doc_id 至少提供一个"}), 400
    if not section_no and not title:
        return jsonify({"success": False, "error": "sectionNo 或 title 至少提供一个"}), 400

    category, doc_id = _resolve_category_doc_id(object_name, doc_id, category)
    if category and doc_id:
        try:
            from .document_parse_service import parse_artifacts_ready, fetch_section_content_html

            if parse_artifacts_ready(category, doc_id):
                html = fetch_section_content_html(category, doc_id, section_no, section_title=title)
                if html.strip():
                    return jsonify({
                        "success": True,
                        "data": {
                            "sectionNo": section_no,
                            "title": title,
                            "html": html,
                            "content": html,
                            "source": "minio_section_store",
                        },
                    })
        except Exception as e:
            logger.warning("[document-section-content] MinIO 章节读取失败，降级 PDF: %s", e)

    import tempfile as _tf
    temp_dir = _tf.mkdtemp()
    temp_path = None
    try:
        temp_path = _download_wjgl_temp_file(object_name, doc_id, temp_dir)
        ext = os.path.splitext(temp_path)[1].lower()
        if ext in ('.docx', '.doc'):
            payload = _extract_section_from_docx(temp_path, title, section_no)
        elif ext == '.pdf':
            payload = _extract_section_from_pdf(temp_path, title, section_no)
        else:
            return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400
        payload["html"] = _section_payload_to_html(payload)
        return jsonify({"success": True, "data": payload})
    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.error("document-section-content failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


@wjgl1_bp.route('/document-section-content-upload', methods=['POST'])
def document_section_content_upload():
    """上传文件预览单个章节（不入库）。"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "没有文件"}), 400
    uploaded_file = request.files['file']
    if not uploaded_file.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    section_no = (request.form.get('sectionNo') or request.form.get('section_no') or '').strip()
    title = (request.form.get('title') or '').strip()
    if not section_no and not title:
        return jsonify({"success": False, "error": "sectionNo 或 title 至少提供一个"}), 400

    ext = os.path.splitext(uploaded_file.filename)[1].lower()
    if ext not in ('.docx', '.doc', '.pdf'):
        return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

    import tempfile as _tf
    temp_dir = _tf.mkdtemp()
    temp_path = os.path.join(temp_dir, uploaded_file.filename)
    try:
        uploaded_file.save(temp_path)
        if ext in ('.docx', '.doc'):
            payload = _extract_section_from_docx(temp_path, title, section_no)
        else:
            payload = _extract_section_from_pdf(temp_path, title, section_no)
        payload["html"] = _section_payload_to_html(payload)
        return jsonify({"success": True, "data": payload})
    except Exception as e:
        logger.error("document-section-content-upload failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


@wjgl1_bp.route('/method-extract-sections', methods=['POST'])
def method_extract_sections():
    """根据选择的章节，提取 PDF/Word 文档内容（包含表格 HTML 和图片 base64）"""
    import tempfile as _tf

    temp_dir = _tf.mkdtemp()
    temp_path = None
    try:
        data = request.json or {}
        object_name = (data.get('object_name') or '').strip()
        doc_id = (data.get('doc_id') or '').strip()
        if not object_name and not doc_id:
            return jsonify({"success": False, "error": "object_name 或 doc_id 至少提供一个"}), 400

        if object_name:
            if not _is_safe_wjgl_object_name(object_name):
                return jsonify({"success": False, "error": "invalid object_name"}), 400
            temp_path = os.path.join(temp_dir, os.path.basename(object_name))
            Config.MINIO_CLIENT.fget_object(Config.MINIO_BUCKET_NAME, object_name, temp_path)
        else:
            from Database.minIo.main import download_file_by_doc_id
            result = download_file_by_doc_id(
                Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, doc_id
            )
            if not result or not result.get('path'):
                return jsonify({"success": False, "error": "文件不存在"}), 404
            temp_path = result['path']

        ext = os.path.splitext(temp_path)[1].lower()
        sections = data.get('sections', [])
        if not sections:
            return jsonify({"success": False, "error": "sections 不能为空"}), 400

        content_map = {}
        for section in sections:
            section_no = section.get('sectionNo', '')
            title = section.get('title', '')

            if ext in ('.docx', '.doc'):
                content = _extract_section_from_docx(temp_path, title, section_no)
            elif ext == '.pdf':
                content = _extract_section_from_pdf(temp_path, title, section_no)
            else:
                return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

            key = f"{section_no}_{title}" if section_no else title
            content_map[key] = content

        log_user_action(f"文件管理-方法提取章节 object={object_name or doc_id}")
        return jsonify({"success": True, "data": {"sections": content_map}})

    except Exception as e:
        logger.error(f"method-extract-sections failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


@wjgl1_bp.route('/method-extract-sections-file', methods=['POST'])
def method_extract_sections_file():
    """从上传的本地文件提取章节内容（不保存到 MinIO）"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "没有文件"}), 400

    uploaded_file = request.files['file']
    if not uploaded_file.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    ext = os.path.splitext(uploaded_file.filename)[1].lower()
    if ext not in ('.docx', '.doc', '.pdf'):
        return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

    sections_raw = request.form.get('sections', '[]')
    try:
        sections = json.loads(sections_raw)
    except Exception:
        return jsonify({"success": False, "error": "sections 格式错误"}), 400

    if not sections:
        return jsonify({"success": False, "error": "sections 不能为空"}), 400

    import tempfile as _tf

    temp_dir = _tf.mkdtemp()
    temp_path = os.path.join(temp_dir, uploaded_file.filename)
    try:
        uploaded_file.save(temp_path)

        content_map = {}
        for section in sections:
            section_no = section.get('sectionNo', '')
            title = section.get('title', '')

            if ext in ('.docx', '.doc'):
                content = _extract_section_from_docx(temp_path, title, section_no)
            elif ext == '.pdf':
                content = _extract_section_from_pdf(temp_path, title, section_no)
            else:
                return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

            key = f"{section_no}_{title}" if section_no else title
            content_map[key] = content

        return jsonify({"success": True, "data": {"sections": content_map}})

    except Exception as e:
        logger.error(f"method-extract-sections-file failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def _extract_section_from_pdf(pdf_path: str, title: str, section_no: str) -> dict:
    """从 PDF 提取指定章节内容，包含表格 HTML 和图片 base64"""
    try:
        extractor = DocumentExtractor()
        result = extractor.extract(pdf_path)
        pages = result.get("pages", [])
    except Exception as e:
        logger.warning(f"[_extract_section_from_pdf] 提取失败: {e}")
        return {"title": title, "sectionNo": section_no, "content": "", "tables": [], "images": []}

    section_text = []
    section_tables = []
    section_images = []
    in_section = False

    target = section_no if section_no else title

    for page_idx, page in enumerate(pages):
        text = _normalize_ocr_text(page.get("text", ""))
        tables = page.get("tables", [])
        figures = page.get("figures", [])

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            if section_no:
                if line.startswith(section_no) or line.startswith('.' + section_no):
                    in_section = True
                    continue
                elif in_section and _SECTION_RE.match(line):
                    m2 = _SECTION_RE.match(line)
                    if m2:
                        sub_title = m2.group(2).strip()
                        if len(sub_title) <= 15:
                            break
            else:
                if line == title or line.startswith(title):
                    in_section = True
                    continue
                elif in_section:
                    cleaned = line.replace(' ', '')
                    if 2 <= len(cleaned) <= 10 and re.match(r'^[\u4e00-\u9fff]+$', cleaned):
                        break

            if in_section:
                section_text.append(line)

        if in_section:
            for table in tables:
                html = table.get("html", "")
                if html:
                    section_tables.append(html)

            for figure in figures:
                img_path = figure.get("path", "")
                if img_path and os.path.exists(img_path):
                    try:
                        import base64
                        with open(img_path, "rb") as f:
                            img_data = base64.b64encode(f.read()).decode()
                        ext = os.path.splitext(img_path)[1].lower()
                        mime = "image/png" if ext == ".png" else "image/jpeg"
                        section_images.append({
                            "path": img_path,
                            "base64": f"data:{mime};base64,{img_data}",
                            "ocr_text": figure.get("ocr_text", ""),
                        })
                    except Exception as e:
                        logger.warning(f"[_extract_section_from_pdf] 图片读取失败: {e}")

    return {
        "title": title,
        "sectionNo": section_no,
        "content": "\n".join(section_text),
        "tables": section_tables,
        "images": section_images,
    }


def _extract_section_from_docx(docx_path: str, title: str, section_no: str) -> dict:
    """从 Word 提取指定章节内容，基于 XML 解析"""
    try:
        doc = docx.Document(docx_path)
    except Exception as e:
        logger.warning(f"[_extract_section_from_docx] 打开失败: {e}")
        return {"title": title, "sectionNo": section_no, "content": "", "tables": [], "images": []}

    section_text = []
    section_tables = []
    section_images = []
    in_section = False

    target_heading = section_no if section_no else title

    from lxml import etree
    from docx.oxml.ns import qn

    body = doc.element.body
    for child in body:
        if child.tag == qn('w:p'):
            para = docx.text.paragraph.Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue

            is_heading = False
            if para.style and 'heading' in (para.style.name or '').lower():
                is_heading = True

            if is_heading and target_heading in text:
                in_section = True
                continue
            elif is_heading and in_section:
                break

            if in_section:
                section_text.append(text)

        elif child.tag == qn('w:tbl') and in_section:
            tbl = docx.table.Table(child, doc)
            html = "<table>"
            for row in tbl.rows:
                html += "<tr>"
                for cell in row.cells:
                    html += f"<td>{cell.text}</td>"
                html += "</tr>"
            html += "</table>"
            section_tables.append(html)

    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            section_images.append({
                "blob": rel.target_part.blob,
                "content_type": rel.target_part.content_type,
            })

    return {
        "title": title,
        "sectionNo": section_no,
        "content": "\n".join(section_text),
        "tables": section_tables,
        "images": section_images,
    }


def _extract_toc_from_word(docx_path: str) -> list:
    """从 Word 文档提取目录树（委托 document_heading_extract）。"""
    toc, _method = _extract_toc_from_word_impl(docx_path)
    return toc


def _extract_toc_from_pdf(pdf_path: str) -> list:
    toc, _method = extract_toc_from_pdf(pdf_path)
    return toc


def _extract_toc_from_pdf_fast(pdf_path: str) -> list:
    toc, _method = extract_toc_from_pdf_fast(pdf_path)
    return toc


@wjgl1_bp.route('/document-headings-extract', methods=['POST'])
def document_headings_extract():
    """
    上传 PDF/Word，自动识别标题（WJGL 算法）。
    form: file, mode=full|fast（PDF 可选，默认 full）
    """
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "没有文件"}), 400
    uploaded = request.files['file']
    if not uploaded.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    ext = os.path.splitext(uploaded.filename)[1].lower()
    if ext not in ('.pdf', '.doc', '.docx', '.md', '.txt'):
        return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

    import tempfile as _tf
    temp_dir = _tf.mkdtemp()
    temp_path = os.path.join(temp_dir, uploaded.filename)
    mode = (request.form.get('mode') or 'full').strip().lower()
    try:
        uploaded.save(temp_path)
        result = extract_document_headings(temp_path, mode=mode)
        return jsonify({
            "success": True,
            "data": {
                "toc": result.get("toc") or [],
                "headings": result.get("headings") or [],
                "full_headings": result.get("full_headings") or [],
                "heading_count": result.get("heading_count", 0),
                "extract_method": result.get("extract_method"),
                "file_ext": result.get("file_ext"),
                "mode": result.get("mode"),
            },
        })
    except Exception as e:
        logger.error("[document-headings-extract] 失败: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass



@wjgl1_bp.route('/neo4j-nodes', methods=['GET'])
def get_neo4j_nodes():
    """Get all nodes from Neo4j by type for association dropdown"""
    if not Config.NEO4J_DRIVER:
        return jsonify({"success": False, "error": "Neo4j not initialized"}), 500

    node_type = request.args.get('node_type', '')
    if not node_type:
        return jsonify({"success": False, "error": "node_type required"}), 400

    try:
        with Config.NEO4J_DRIVER.session() as session:
            result = session.run(f"""
                MATCH (n:{node_type})
                RETURN n.Id AS id, n.Name AS name, n.Label AS label
            """)
            nodes = [{"id": r["id"], "name": r["name"], "label": r["label"]} for r in result]
        return jsonify({"success": True, "data": nodes})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _find_existing_file_node(file_id):
    if not Config.NEO4J_DRIVER:
        return None
    try:
        with Config.NEO4J_DRIVER.session() as session:
            result = session.run(
                "MATCH (n:File {Id: $id}) RETURN n",
                id=file_id
            )
            record = result.single()
            if record:
                node = dict(record["n"])
                return {
                    "id": node.get("Id", ""),
                    "name": node.get("Name", ""),
                    "type": node.get("Type", ""),
                    "chroma_id": node.get("Chroma_id", ""),
                    "chroma_chunks": node.get("Chroma_chunks", 0),
                    "minio_id": node.get("minIo_id", ""),
                    "create_time": node.get("Create_time", ""),
                }
    except Exception as e:
        logger.error(f"Failed to query File node: {e}")
    return None


def _compute_file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def _find_file_node_by_md5(file_md5: str):
    if not Config.NEO4J_DRIVER:
        return None
    md5 = (file_md5 or '').strip().lower()
    if not md5:
        return None
    try:
        with Config.NEO4J_DRIVER.session() as session:
            result = session.run(
                "MATCH (n:File) WHERE toLower(coalesce(n.File_md5, '')) = $md5 RETURN n LIMIT 1",
                md5=md5,
            )
            record = result.single()
            if record:
                node = dict(record["n"])
                return {
                    "id": node.get("Id", ""),
                    "name": node.get("Name", ""),
                    "type": node.get("Type", ""),
                    "chroma_id": node.get("Chroma_id", ""),
                    "chroma_chunks": node.get("Chroma_chunks", 0),
                    "minio_id": node.get("minIo_id", ""),
                    "file_type": node.get("File_type", ""),
                    "file_md5": node.get("File_md5", ""),
                    "create_time": node.get("Create_time", ""),
                }
    except Exception as e:
        logger.error(f"Failed to query File node by md5: {e}")
    return None


def _delete_existing_file_node(file_id, doc_id):
    if not Config.NEO4J_DRIVER:
        return
    try:
        with Config.NEO4J_DRIVER.session() as session:
            session.run(
                "MATCH (f:File {Id: $id}) DETACH DELETE f",
                id=file_id
            )
    except Exception as e:
        logger.error(f"Failed to delete File node: {e}")

    if Config.CHROMA_COLLECTION_OBJ and doc_id:
        try:
            results = Config.CHROMA_COLLECTION_OBJ.get(where={"doc_id": doc_id})
            ids = results.get("ids", [])
            if ids:
                Config.CHROMA_COLLECTION_OBJ.delete(ids=ids)
                logger.info(f"[DEBUG] Deleted {len(ids)} Chroma entries for doc_id={doc_id}")
        except Exception as e:
            logger.warning(f"[DEBUG] Failed to delete Chroma entries: {e}")


def _create_file_node(file_id, file_type, label, name, chroma_id, chroma_chunks, minio_id,
                      image_minio_ids, user_id, abstract='', keywords='', ics='', ccs='',
                      file_type_ext='', std_code='', std_name='', publish_date='',
                      issuer='', review_status='', file_md5=''):
    if not Config.NEO4J_DRIVER:
        return None
    try:
        with Config.NEO4J_DRIVER.session() as session:
            session.run("""
                CREATE (n:File {
                    Id: $id, Type: $type, Label: $label, Name: $name,
                    Chroma_id: $chroma_id, Chroma_chunks: $chroma_chunks, minIo_id: $minio_id,
                    Image_true: $image_true, Image_minIO_id: $image_ids,
                    Abstract: $abstract, Keywords: $keywords,
                    ICS: $ics, CCS: $ccs,
                    Std_code: $std_code, Std_name: $std_name,
                    Publish_date: $publish_date, Issuer: $issuer,
                    Review_status: $review_status,
                    Graph_link_status: 'none',
                    File_type: $file_type_ext,
                    File_md5: $file_md5,
                    Status: '启用', User_id: $user_id,
                    Create_time: $create_time
                })
            """, id=file_id, type=file_type, label=label, name=name,
                chroma_id=chroma_id, chroma_chunks=chroma_chunks, minio_id=minio_id,
                image_true=len(image_minio_ids) > 0,
                image_ids=json.dumps(image_minio_ids),
                abstract=abstract, keywords=keywords,
                ics=ics, ccs=ccs,
                std_code=std_code or '', std_name=std_name or '',
                publish_date=publish_date or '', issuer=issuer or '',
                review_status=review_status or 'confirmed',
                file_type_ext=file_type_ext,
                file_md5=(file_md5 or '').lower(),
                user_id=user_id,
                create_time=datetime.now(timezone.utc).isoformat())
        return {"success": True, "file_id": file_id}
    except Exception as e:
        logger.error(f"Failed to create File node: {e}")
        return {"success": False, "error": str(e)}


def _create_file_associations(file_id, associations):
    if not Config.NEO4J_DRIVER:
        return
    try:
        from blueprints.RelFact.neo4j_relations import merge_file_relations
        merge_file_relations(file_id, associations or [])
    except Exception as e:
        logger.error(f"Failed to create associations: {e}")


from .review_routes import register_review_routes
from .graph_picker_routes import register_graph_picker_routes
register_review_routes(wjgl1_bp)
register_graph_picker_routes(wjgl1_bp)
