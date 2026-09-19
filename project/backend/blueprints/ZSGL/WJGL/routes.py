import os
import sys
import json
import logging
import threading
import queue
import uuid
import mimetypes
import re
import docx
import fitz
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

logger = logging.getLogger(__name__)

wjgl_bp = Blueprint('wjgl', __name__, url_prefix='/api/wjgl')

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


def _is_text_garbled(text: str) -> bool:
    """检测文本是否疑似乱码。"""
    if not text or not text.strip():
        return True

    total = len(text)
    if total == 0:
        return True

    replacement_count = text.count("�")
    good_count = 0

    for ch in text:
        code = ord(ch)
        if ch.isalnum() or ch.isspace() or ch in "，。、《》？！：；,.!?;:()[]{}-_/\\\"'％%＋+＝=·—":
            good_count += 1
        elif 0x4E00 <= code <= 0x9FFF:
            good_count += 1

    good_ratio = good_count / total
    replacement_ratio = replacement_count / total
    return good_ratio < 0.55 or replacement_ratio > 0.02


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
    ics = task["ics"]
    ccs = task["ccs"]
    ext = task["ext"]
    image_paths = task.get("image_paths", [])

    _set_task_status(task_id, "processing")

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
        if file_sync_chroma and Config.CHROMA_COLLECTION_OBJ and extract_result:
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

        _set_task_status(task_id, "done", error="", result={
            "doc_id": doc_id,
            "title": title,
            "file_node_id": file_node_id,
            "chroma_id": chroma_id,
            "chroma_chunks": chroma_chunks,
            "images": len(image_paths),
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
    """供自动解析诊断：另一台机器常见为 .env 未配 OPENAI_API_KEY 导致 OPENAI_CLIENT=None。"""
    key_set = bool((Config.OPENAI_API_KEY or "").strip())
    eff_key = Config.effective_openai_api_key()
    client_ok = Config.OPENAI_CLIENT is not None
    local_no_auth = eff_key == "EMPTY"
    return {
        "openai_client_ready": client_ok,
        "openai_api_key_set": key_set,
        "openai_local_no_auth": local_no_auth,
        "openai_allow_empty_key": bool(getattr(Config, "OPENAI_ALLOW_EMPTY_KEY", False)),
        "openai_base_url": (Config.OPENAI_BASE_URL or "").strip(),
        "openai_model": (Config.OPENAI_MODEL or "").strip(),
        "hint": (
            "LLM 就绪，可正常自动解析摘要/ICS/CCS。"
            + ("（本地无鉴权模式）" if local_no_auth else "")
            if client_ok
            else (
                "未配置 LLM：云端填 OPENAI_API_KEY；本地 vLLM 设 OPENAI_ALLOW_EMPTY_KEY=1 "
                "且 OPENAI_BASE_URL=http://127.0.0.1:8000/v1（或 OPENAI_API_KEY=EMPTY），然后重启 Flask。"
                if not eff_key
                else "OPENAI_CLIENT 初始化失败，请查看后端启动日志。"
            )
        ),
    }


@wjgl_bp.route("/auto-parse/status", methods=["GET"])
def wjgl_auto_parse_status():
    """诊断：自动解析依赖的 LLM 是否可用（浏览器可访问 GET /api/wjgl/auto-parse/status）。"""
    return jsonify({"success": True, "data": _wjgl_llm_status()})


@wjgl_bp.route('/auto-parse', methods=['POST'])
def auto_parse_file():
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400

    uploaded_file = request.files['file']
    filename = uploaded_file.filename
    if not filename:
        return jsonify({"success": False, "error": "No filename"}), 400

    temp_dir = os.path.join(Config.OUTPUT_DIR, 'uploads')
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, filename)
    uploaded_file.save(temp_path)

    try:
        doc_id, title = _extract_doc_info(filename)
        ext = os.path.splitext(filename)[1].lower()

        abstract = ""
        keywords_list = []
        ics = ""
        ccs = ""
        std_code = ""
        std_name = ""
        llm_used = False
        warnings: list[str] = []

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
                            if len("".join(text_parts)) > 3000:
                                break
                        doc.close()
                        text_sample = "".join(text_parts)[:3000]
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
                            {"role": "system", "content": "你是文档元数据提取专家。请从文档文本中提取以下信息，返回纯JSON：\n1. abstract: 文档摘要，200字以内\n2. keywords: 3-5个关键词\n3. ics: 国际标准分类号，通常在文档开头几行，格式如 ICS 49.020 或 ICS49.020\n4. ccs: 中国标准分类号，通常在文档开头几行，格式如 V35 或 V 35\n5. std_code: 标准号，从文档标题或封面中提取，如GB/T 4208-2017\n6. std_name: 标准名称全称，如外壳防护等级(IP代码)\n文件名格式应为：标准号_标准名称全称，例如：GB/T 4208-2017_外壳防护等级(IP代码)\n如果找不到ICS或CCS，返回空字符串。JSON格式：{\"abstract\":\"\",\"keywords\":[],\"ics\":\"\",\"ccs\":\"\",\"std_code\":\"\",\"std_name\":\"\"}"},
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
                        ics = parsed.get("ics", "")
                        ccs = parsed.get("ccs", "")
                        std_code = parsed.get("std_code", "")
                        std_name = parsed.get("std_name", "")
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

        if ext == ".pdf" and (not ics or not ccs):
            try:
                pdf_meta = _extract_ics_ccs_from_pdf(temp_path)
                if not ics:
                    ics = (pdf_meta.get("ics") or "").strip()
                if not ccs:
                    ccs = (pdf_meta.get("ccs") or "").strip()
            except Exception as e:
                logger.warning("PDF ICS/CCS rule extract failed: %s", e)

        log_user_action(f"文件管理-自动解析元数据 file={filename[:120]}")
        return jsonify({
            "success": True,
            "data": {
                "doc_id": doc_id,
                "title": title,
                "ics": ics,
                "ccs": ccs,
                "abstract": abstract,
                "keywords": keywords_list,
                "file_type": ext.lstrip("."),
                "needs_manual_ics": not ics,
                "needs_manual_ccs": not ccs,
                "std_code": std_code,
                "std_name": std_name,
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


@wjgl_bp.route('/simple-upload', methods=['POST'])
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


@wjgl_bp.route('/upload', methods=['POST'])
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
        print(f"[UPLOAD-11] file_sync_chroma={file_sync_chroma}, file_ics={file_ics}, file_ccs={file_ccs}, file_title={file_title}, file_std_code={file_std_code}, file_std_name={file_std_name}")
        print(f"[UPLOAD-12] file_keywords={file_keywords}, file_associations={file_associations}")

        temp_dir = os.path.join(Config.OUTPUT_DIR, 'uploads')
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, filename)
        uploaded_file.save(temp_path)
        print(f"[UPLOAD-13] File saved to: {temp_path}, exists={os.path.exists(temp_path)}")

        try:
            print(f"[UPLOAD-14] Starting try block for file: {filename}")
            queued_async = False
            next_num = batch_start_num + allocated_count
            allocated_count += 1
            doc_id = f"{prefix}{next_num:04d}"
            print(f"[UPLOAD-14b] doc_id generated: prefix={prefix}, next_num={next_num}, doc_id={doc_id}")
            title = file_std_name if file_std_name else (file_title if file_title else os.path.splitext(filename)[0])
            ics = file_ics
            ccs = file_ccs
            ext = os.path.splitext(filename)[1].lower()
            print(f"[UPLOAD-15] doc_id={doc_id}, title={title}, ext={ext}")

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
                    results.append({
                        "success": True,
                        "doc_id": doc_id,
                        "title": title,
                        "file_node_id": file_node_id,
                        "duplicate": True,
                        "action_taken": "skipped",
                        "existing_data": existing_node,
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
                std_code=file_std_code, std_name=file_std_name
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
                    )
                    print(f"[UPLOAD-23f] Neo4j File node created: {file_node_id}")
                except Exception as e:
                    logger.warning(f"Failed to create Neo4j node: {e}")

            _ensure_ocr_worker()
            task_id = str(uuid.uuid4())
            _set_task_status(task_id, "queued", error="")
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
                "message": "File uploaded. Processing text extraction in background.",
            })
            continue

        except Exception as e:
            print(f"[UPLOAD-47] ERROR processing file {filename}: {e}")
            import traceback
            print(f"[UPLOAD-47b] Traceback: {traceback.format_exc()}")
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


@wjgl_bp.route('/resolve-duplicate', methods=['POST'])
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


@wjgl_bp.route('/task-status', methods=['GET'])
def get_task_status():
    task_id = request.args.get('task_id', '')
    if not task_id:
        return jsonify({"success": False, "error": "task_id required"}), 400
    
    with _OCR_STATUS_LOCK:
        status = _OCR_TASK_STATUS.get(task_id, {})
    
    if not status:
        return jsonify({"success": False, "error": "Task not found"}), 404
    
    return jsonify({"success": True, "data": status})


@wjgl_bp.route('/category-counts', methods=['GET'])
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


@wjgl_bp.route('/list', methods=['GET'])
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
                "upload_userid": meta.get("x-amz-meta-upload-userid", ""),
                "upload_time": meta.get("x-amz-meta-upload-time", ""),
                "abstract": meta.get("x-amz-meta-abstract", ""),
                "keywords": meta.get("x-amz-meta-keywords", ""),
                "file_size": obj.size,
                "last_modified": str(obj.last_modified),
            })
        except Exception as e:
            logger.warning(f"Failed to get metadata for {obj.object_name}: {e}")

    return jsonify({"success": True, "data": files, "count": len(files)})


@wjgl_bp.route("/object-meta", methods=["GET"])
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


@wjgl_bp.route("/preview-pdf/status", methods=["GET"])
def wjgl_preview_pdf_status():
    """诊断：后端是否能找到 LibreOffice（与前端 react-doc-viewer 无关）。"""
    return jsonify({"success": True, "data": office_convert_status()})


@wjgl_bp.route("/preview-pdf", methods=["GET"])
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


@wjgl_bp.route("/object-file", methods=["GET"])
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


@wjgl_bp.route('/delete', methods=['POST'])
def delete_file():
    """Delete a file from MinIO, Chroma, and Neo4j"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    data = request.get_json()
    object_name = data.get('object_name', '')

    if not object_name:
        return jsonify({"success": False, "error": "object_name required"}), 400

    try:
        Config.MINIO_CLIENT.remove_object(Config.MINIO_BUCKET_NAME, object_name)
        prefix = object_name.rsplit('/', 1)[0] + '/'
        images = Config.MINIO_CLIENT.list_objects(
            Config.MINIO_BUCKET_NAME, prefix=prefix + "images/", recursive=True
        )
        for img in images:
            Config.MINIO_CLIENT.remove_object(Config.MINIO_BUCKET_NAME, img.object_name)

        if Config.CHROMA_COLLECTION_OBJ:
            doc_id = data.get('doc_id', '')
            if doc_id:
                results = Config.CHROMA_COLLECTION_OBJ.get(where={"doc_id": doc_id})
                ids = results.get("ids", [])
                if ids:
                    Config.CHROMA_COLLECTION_OBJ.delete(ids=ids)

        if Config.NEO4J_DRIVER:
            file_node_id = f"WJ_{data.get('doc_id', '')}"
            if file_node_id != "WJ_":
                with Config.NEO4J_DRIVER.session() as session:
                    session.run(
                        "MATCH (f:File {Id: $id}) DETACH DELETE f",
                        id=file_node_id
                    )

        log_user_action(f"文件管理-删除文件 object={object_name[:200]} doc_id={data.get('doc_id', '')}")
        return jsonify({"success": True, "message": "File deleted"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@wjgl_bp.route('/update', methods=['POST'])
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


@wjgl_bp.route('/ccs-tree', methods=['GET'])
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


@wjgl_bp.route('/label-tree', methods=['GET'])
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


@wjgl_bp.route('/document-toc', methods=['GET'])
def document_toc():
    """解析已上传文件的目录结构，返回章节树供方法提取选择"""
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO not initialized"}), 500

    object_name = (request.args.get('object_name') or '').strip()
    doc_id = (request.args.get('doc_id') or '').strip()
    if not object_name and not doc_id:
        return jsonify({"success": False, "error": "object_name 或 doc_id 至少提供一个"}), 400

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
                return jsonify({"success": False, "error": "文件不存在"}), 404
            temp_path = result['path']

        ext = os.path.splitext(temp_path)[1].lower()
        toc = []

        if ext in ('.docx', '.doc'):
            file_size = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
            logger.info(f"[document-toc] 下载文件: {temp_path}, 大小={file_size} bytes")
            toc = _extract_toc_from_word(temp_path)
            logger.info(f"[document-toc] Word 提取完成，共 {len(toc)} 个顶级节点")
            if not toc:
                try:
                    debug_doc = docx.Document(temp_path)
                    heading_count = sum(1 for p in debug_doc.paragraphs if p.style and 'heading' in (p.style.name or '').lower())
                    total_paragraphs = len(debug_doc.paragraphs)
                    logger.info(f"[document-toc] 调试: 总段落={total_paragraphs}, Heading段落={heading_count}")
                    styles = set()
                    for p in debug_doc.paragraphs:
                        if p.style and p.style.name:
                            styles.add(p.style.name.lower())
                    logger.info(f"[document-toc] 调试: 所有样式={sorted(styles)}")
                    first_headings = [(p.style.name, p.text[:60]) for p in debug_doc.paragraphs if p.style and 'heading' in (p.style.name or '').lower()][:5]
                    logger.info(f"[document-toc] 调试: 前5个Heading={first_headings}")
                except Exception as de:
                    logger.warning(f"[document-toc] 调试失败: {de}")
        elif ext == '.pdf':
            toc = _extract_toc_from_pdf(temp_path)
            logger.info(f"[document-toc] PDF 提取完成，共 {len(toc)} 个顶级节点")
        else:
            return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

        if not toc:
            logger.warning(f"[document-toc] 未提取到目录，文件: {object_name or doc_id}")

        log_user_action(f"文件管理-获取文档目录 object={object_name or doc_id}")
        return jsonify({"success": True, "data": {"toc": toc}})

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


@wjgl_bp.route('/document-toc-upload', methods=['POST'])
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
            toc = _extract_toc_from_pdf(temp_path)
            logger.info(f"[document-toc-upload] PDF 提取完成，共 {len(toc)} 个顶级节点")
        else:
            return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

        if not toc:
            logger.warning(f"[document-toc-upload] 未提取到目录，文件: {uploaded_file.filename}")

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


def _extract_toc_from_word(docx_path: str) -> list:
    """从 Word 文档提取目录树。
    策略：
    1. 先尝试从 Heading 样式提取
    2. 如果 Heading 样式少于 3 个，则自动扫描编号段落（如 "8.2 振动试验"）
    """
    try:
        doc = docx.Document(docx_path)
    except Exception as e:
        logger.warning(f"[_extract_toc_from_word] 无法打开文档: {e}")
        return []

    total_paragraphs = len(doc.paragraphs)
    logger.info(f"[_extract_toc_from_word] 总段落数: {total_paragraphs}")

    try:
        toc_from_headings = _extract_headings_by_style(doc)
        logger.info(f"[_extract_toc_from_word] Heading 样式提取: {len(toc_from_headings)} 个标题")
        if toc_from_headings:
            logger.info(f"[_extract_toc_from_word] 前3个标题: {toc_from_headings[:3]}")

        if len(toc_from_headings) >= 3:
            logger.info(f"[_extract_toc_from_word] 使用 Heading 样式提取，构建目录树...")
            result = _build_toc_tree(toc_from_headings)
            logger.info(f"[_extract_toc_from_word] 构建完成，顶级节点: {len(result)}")
            return result

        logger.info(f"[_extract_toc_from_word] Heading 不足 3 个，使用编号提取")
        toc_from_numbers = _extract_headings_by_number(doc)
        logger.info(f"[_extract_toc_from_word] 编号提取: {len(toc_from_numbers)} 个标题")
        return _build_toc_tree(toc_from_numbers)
    except Exception as e:
        import traceback
        logger.error(f"[_extract_toc_from_word] 提取异常: {e}")
        logger.error(traceback.format_exc())
        return []


def _extract_headings_by_style(doc) -> list:
    """通过 Heading 样式提取标题。"""
    items = []
    for para in doc.paragraphs:
        style_name = (para.style.name or '').lower() if para.style else ''
        text = para.text.strip()
        if not text:
            continue
        if 'heading' not in style_name:
            continue
        m = re.search(r'heading\s*(\d+)', style_name)
        level = int(m.group(1)) if m else 1
        section_no, display_title = _parse_section_number(text)
        items.append({
            "title": text,
            "sectionNo": section_no,
            "level": level,
        })
    return items


def _extract_headings_by_number(doc) -> list:
    """通过编号模式自动提取标题（适用于未使用 Heading 样式的文档）。"""
    items = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if len(text) > 100:
            continue
        m = re.match(r'^(\d+(?:\.\d+){0,4})\s*[)）.、\s]+(.+)', text)
        if not m:
            continue
        nums = m.group(1).split('.')
        if len(nums) > 5:
            continue
        first_num = int(nums[0])
        if first_num < 1 or first_num > 20:
            continue
        after = m.group(2).strip()
        if not after:
            continue
        if re.match(r'^[图表附注]', after):
            continue
        level = len(nums)
        section_no = m.group(1)
        items.append({
            "title": text,
            "sectionNo": section_no,
            "level": level,
        })
    return items


def _parse_section_number(text: str) -> tuple:
    """从标题文本中解析章节号和显示标题。"""
    m = re.match(r'^(\d+(?:\.\d+){0,4})\s*[)）.、\s]+(.+)', text)
    if m:
        return m.group(1), m.group(2).strip() or text

    m = re.match(r'^(第[一二三四五六七八九十百\d]+[章节部分篇])\s*(.*)', text)
    if m:
        return m.group(1), m.group(2).strip() or text

    return '', text


def _build_toc_tree(items: list) -> list:
    """将扁平的标题列表构建为嵌套树结构。"""
    toc = []
    stack = []

    for item in items:
        level = item.get('level', 1)
        node = {
            "title": item['title'],
            "sectionNo": item.get('sectionNo', ''),
            "level": level,
            "children": [],
        }

        while stack and stack[-1][0] >= level:
            stack.pop()

        if stack:
            stack[-1][1]["children"].append(node)
        else:
            toc.append(node)

        stack.append((level, node))

    return toc


def _extract_toc_from_pdf(pdf_path: str) -> list:
    """从 PDF 文档提取目录树（基于内置书签）"""
    try:
        doc = fitz.open(pdf_path)
        bookmarks = doc.get_toc()
        doc.close()
    except Exception:
        return []

    toc = []
    stack = []

    for level, title, page in bookmarks:
        section_no = ''
        m = re.match(r'^(\d+(?:\.\d+)*)\s*(.*)', title)
        if m:
            section_no = m.group(1)
            title = m.group(2).strip() or title

        node = {
            "title": title,
            "sectionNo": section_no,
            "level": level,
            "children": [],
        }

        while stack and stack[-1][0] >= level:
            stack.pop()

        if stack:
            stack[-1][1]["children"].append(node)
        else:
            toc.append(node)

        stack.append((level, node))

    return toc


@wjgl_bp.route('/neo4j-nodes', methods=['GET'])
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
                      image_minio_ids, user_id, abstract='', keywords='', ics='', ccs='', file_type_ext=''):
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
                    File_type: $file_type_ext,
                    Status: '启用', User_id: $user_id,
                    Create_time: $create_time
                })
            """, id=file_id, type=file_type, label=label, name=name,
                chroma_id=chroma_id, chroma_chunks=chroma_chunks, minio_id=minio_id,
                image_true=len(image_minio_ids) > 0,
                image_ids=json.dumps(image_minio_ids),
                abstract=abstract, keywords=keywords,
                ics=ics, ccs=ccs, file_type_ext=file_type_ext,
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
        with Config.NEO4J_DRIVER.session() as session:
            for assoc in associations:
                node_type = assoc.get("node_type", "")
                node_ids = assoc.get("node_ids", [])
                for nid in node_ids:
                    session.run(f"""
                        MATCH (f:File {{Id: $file_id}})
                        MATCH (n:{node_type} {{Id: $node_id}})
                        MERGE (f)-[r:BELONGS_TO]->(n)
                    """, file_id=file_id, node_id=nid)
    except Exception as e:
        logger.error(f"Failed to create associations: {e}")
