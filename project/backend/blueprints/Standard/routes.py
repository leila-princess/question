"""
标准相关 API — PDF服务 + AI问答
根据图数据库File节点的minio_id关联MinIO中的X-Amz-Meta-Doc-Id来查找文件
"""
from typing import Any, Dict, List, Optional

from flask import Blueprint, request, jsonify, current_app, send_file
from urllib.parse import quote, unquote
import json
import os
import re
import logging
from openai import OpenAI
from .prompt import SYSTEM_PROMPT, DEFAULT_ANSWER_REQUIREMENTS

logger = logging.getLogger(__name__)

standard_bp = Blueprint('standard', __name__, url_prefix='/api/standard')

# 标准映射文件路径 — 与 routes.py 同目录
MAPPING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'standard_mapping.json')
# PDF_DIR: backend/blueprints/Standard → backend (上3层) → agent/... (备用本地路径)
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PDF_DIR = os.path.join(BACKEND_ROOT, 'agent', 'data4neo4j', 'data', 'stardand', 'wurenji')
# TXT_DIR: backend → 项目根目录(上1层) → fronted/public/...
PROJECT_ROOT = os.path.dirname(BACKEND_ROOT)
TXT_DIR = os.path.join(PROJECT_ROOT, 'fronted', 'public', 'tqtxt', 'cleaned_docs')


def _load_mapping():
    try:
        with open(MAPPING_PATH, 'r', encoding='utf-8') as f:
            return json.load(f).get('mappings', {})
    except Exception as e:
        logger.error(f"加载标准映射表失败: {e}")
        return {}


def _get_llm_client():
    return current_app.config.get('OPENAI_CLIENT')


def _get_neo4j_driver():
    return current_app.config.get('NEO4J_DRIVER')


def _get_minio_client():
    return current_app.config.get('MINIO_CLIENT')


def _get_minio_bucket():
    return current_app.config.get('MINIO_BUCKET_NAME', '')


def _normalize_minio_key(minio_id) -> str:
    if minio_id is None:
        return ''
    s = str(minio_id).strip().replace('\\', '/').strip('/')
    return s


_OFFICE_EXTS = ('.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls')
_WJGL_ORIGIN_KEY = re.compile(r'^[^/]+/[^/]+/origin\.[^/]+$', re.I)


def _is_wjgl_style_object_key(path: str) -> bool:
    return bool(_WJGL_ORIGIN_KEY.match(_normalize_minio_key(path)))


def _resolve_minio_pdf_object_path(minio_id) -> Optional[str]:
    """
    将 Neo4j File.minIo_id 解析为 bucket 内 PDF 对象键。

    - 已是 .pdf 路径：直接使用
    - WJGL origin.docx 等：同目录 origin.pdf（非 …/origin.docx/origin.pdf）
    - 含 / 的目录前缀：{prefix}/origin.pdf
    - 短 id：标准/{id}/origin.pdf
    """
    raw = _normalize_minio_key(minio_id)
    if not raw:
        return None
    lower = raw.lower()
    if lower.endswith('.pdf'):
        return raw
    for ext in _OFFICE_EXTS:
        if lower.endswith(ext):
            return raw[: -len(ext)] + '.pdf'
    if '/' in raw:
        return f'{raw.rstrip("/")}/origin.pdf'
    return f'标准/{raw}/origin.pdf'


def _minio_object_candidates(minio_id) -> List[str]:
    """按优先级尝试 MinIO 对象键：原始路径 → PDF 解析路径。"""
    raw = _normalize_minio_key(minio_id)
    if not raw:
        return []
    out: List[str] = []

    def _add(path: Optional[str]) -> None:
        p = _normalize_minio_key(path)
        if p and p not in out:
            out.append(p)

    _add(raw)
    _add(_resolve_minio_pdf_object_path(minio_id))
    return out


def _stat_minio_object(paths: List[str]) -> Optional[Dict[str, Any]]:
    client = _get_minio_client()
    bucket = _get_minio_bucket()
    if not client or not bucket:
        return None
    try:
        if not client.bucket_exists(bucket):
            logger.error("MinIO bucket不存在: %s", bucket)
            return None
    except Exception as exc:
        logger.error("MinIO bucket检查失败: %s", exc)
        return None

    last_err: Optional[Exception] = None
    for minio_path in paths:
        try:
            logger.info("从MinIO获取文件: bucket=%s, path=%s", bucket, minio_path)
            stat = client.stat_object(bucket, minio_path)
            meta: Dict[str, Any] = {}
            if stat.metadata:
                for k, v in stat.metadata.items():
                    key = k.lower()
                    val = v if v else ''
                    if isinstance(val, str):
                        val = unquote(val)
                    meta[key] = val
            return {
                'path': minio_path,
                'size': stat.size,
                'last_modified': str(stat.last_modified),
                'metadata': meta,
            }
        except Exception as exc:
            last_err = exc
            continue
    if last_err:
        logger.error("MinIO文件查找失败: %s", last_err)
    return None


def _preview_fields_for_object(object_name: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """与 WJGL object-meta 对齐的预览字段，供 Standards 页多格式展示。"""
    meta = metadata or {}
    ext = (object_name.rsplit('.', 1)[-1] or '').lower()
    file_type = str(meta.get('x-amz-meta-file-type') or ext or '').lower()
    preview = 'other'
    if file_type == 'pdf' or ext == 'pdf':
        preview = 'pdf'
    elif ext in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tif', 'tiff'):
        preview = 'image'
    else:
        try:
            from blueprints.ZSGL.WJGL.routes import (
                is_excel_native_preview,
                is_ppt_convertible,
                is_word_native_preview,
            )

            if is_word_native_preview(object_name):
                preview = 'office_word'
            elif is_excel_native_preview(object_name):
                preview = 'office_excel'
            elif is_ppt_convertible(object_name):
                preview = 'office_pdf'
        except Exception as exc:
            logger.debug("preview helper import skipped: %s", exc)

    encoded = quote(object_name, safe='')
    file_view_url = f'/api/wjgl/object-file?object_name={encoded}'
    preview_pdf_url = ''
    if preview == 'office_pdf':
        preview_pdf_url = f'/api/wjgl/preview-pdf?object_name={encoded}'

    return {
        'object_name': object_name,
        'preview_kind': preview,
        'fileViewUrl': file_view_url,
        'preview_pdf_url': preview_pdf_url,
    }


def _find_file_by_minio_id(minio_id):
    """根据minio_id在MinIO中查找文件对象（stat + 元数据）"""
    if not _get_minio_client():
        logger.warning("MinIO客户端未初始化")
        return None
    if not _get_minio_bucket():
        logger.warning("MinIO bucket名称为空")
        return None
    return _stat_minio_object(_minio_object_candidates(minio_id))


def _get_file_info_from_neo4j(source_id):
    """从Neo4j查找File节点并返回minio_id、chroma_id等信息"""
    driver = _get_neo4j_driver()
    if not driver:
        return None
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (f:File {Id: $sourceId})
                RETURN f.Id AS id, f.Name AS name, f.minIo_id AS minio_id,
                       f.Chroma_id AS chroma_id, f.Label AS label, f.Type AS type,
                       f.Abstract AS abstract
            """, sourceId=source_id)
            records = list(result)
            if records:
                r = records[0]
                name = r.get('name', '')
                abs_raw = r.get('abstract', '') or ''
                if abs_raw is not None and not isinstance(abs_raw, str):
                    abs_raw = str(abs_raw)
                abstract = unquote(abs_raw).strip() if abs_raw else ''
                return {
                    'id': r.get('id', ''),
                    'name': unquote(name) if name else '',
                    'minio_id': r.get('minio_id', ''),
                    'chroma_id': r.get('chroma_id', ''),
                    'label': r.get('label', ''),
                    'type': r.get('type', ''),
                    'abstract': abstract,
                }
            return None
    except Exception as e:
        logger.error(f"Neo4j查询失败: {e}")
        return None



def _get_standard_txt(source_name):
    """读取标准文本内容（用于AI问答上下文）"""
    # 转换标准号格式: GB/T 38924.3-2020 → GBT38924.3-2020
    txt_name = source_name.replace('GB/T', 'GBT').replace(' ', '')
    txt_path = os.path.join(TXT_DIR, f"{txt_name}.txt")
    
    if os.path.exists(txt_path):
        with open(txt_path, 'r', encoding='utf-8') as f:
            return f.read()
    return None


# ==================== 标准映射查询 ====================

@standard_bp.route('/mapping', methods=['GET'])
def get_mapping():
    """返回完整标准映射表"""
    try:
        mapping = _load_mapping()
        return jsonify({"success": True, "data": mapping})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@standard_bp.route('/info', methods=['GET'])
def get_standard_info():
    """
    根据 source_id 获取标准信息
    1. 从Neo4j的File节点获取minio_id
    2. 用minio_id从MinIO获取文件元数据
    参数: source_id — 如 WJ_00002 (File节点ID)
    """
    try:
        source_id = request.args.get('source_id', '')
        if not source_id:
            return jsonify({"success": False, "error": "source_id required"}), 400
        
        file_info = _get_file_info_from_neo4j(source_id)
        if not file_info:
            return jsonify({"success": False, "error": "标准未找到"}), 404
        
        minio_id = file_info.get('minio_id', '')
        stored_key = _normalize_minio_key(minio_id)
        minio_path = _resolve_minio_pdf_object_path(minio_id) or ''

        result = {
            'id': file_info['id'],
            'source_name': file_info['name'],
            'title': file_info['name'],
            'minio_id': minio_id,
            'minio_path': minio_path,
            'label': file_info.get('label', ''),
            'type': file_info.get('type', ''),
            'pdfUrl': f"/api/standard/pdf/{source_id}",
        }

        if minio_id:
            minio_file = _find_file_by_minio_id(minio_id)
            if minio_file:
                result['file_size'] = minio_file['size']
                result['last_modified'] = minio_file['last_modified']
                result['metadata'] = minio_file['metadata']

                preview_object = stored_key
                if _is_wjgl_style_object_key(stored_key):
                    preview_object = stored_key
                elif _is_wjgl_style_object_key(minio_file['path']):
                    preview_object = minio_file['path']
                else:
                    preview_object = minio_file['path']

                preview_fields = _preview_fields_for_object(
                    preview_object,
                    minio_file.get('metadata') if isinstance(minio_file.get('metadata'), dict) else {},
                )
                result.update(preview_fields)
                if preview_fields.get('preview_kind') == 'pdf':
                    result['pdfUrl'] = preview_fields.get('fileViewUrl') or result['pdfUrl']
                elif preview_fields.get('preview_kind') == 'office_pdf' and preview_fields.get('preview_pdf_url'):
                    result['pdfUrl'] = preview_fields['preview_pdf_url']
        
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"获取标准信息失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== PDF 服务 ====================

@standard_bp.route('/pdf/<source_id>', methods=['GET'])
def serve_pdf(source_id):
    """
    提供标准 PDF 文件
    1. 从Neo4j的File节点获取minio_id
    2. 用解析后的对象键从 MinIO 读取 PDF（与 /info、stat 元数据一致）
    """
    try:
        file_info = _get_file_info_from_neo4j(source_id)
        if file_info and file_info.get('minio_id'):
            minio_id = file_info['minio_id']
            client = _get_minio_client()
            bucket = _get_minio_bucket()
            if client and bucket:
                minio_file = _stat_minio_object(_minio_object_candidates(minio_id))
                pdf_path = _resolve_minio_pdf_object_path(minio_id)
                paths_to_try: List[str] = []
                if pdf_path:
                    paths_to_try.append(pdf_path)
                for p in _minio_object_candidates(minio_id):
                    if p not in paths_to_try:
                        paths_to_try.append(p)
                for minio_path in paths_to_try:
                    if not str(minio_path).lower().endswith('.pdf'):
                        continue
                    try:
                        response = client.get_object(bucket, minio_path)
                        return current_app.response_class(
                            response=response.read(),
                            status=200,
                            mimetype='application/pdf',
                            headers={'Content-Disposition': f'inline; filename="{os.path.basename(minio_path)}"'}
                        )
                    except Exception as e:
                        logger.debug("MinIO PDF 候选失败 path=%s: %s", minio_path, e)
                if minio_file and not str(minio_file.get('path') or '').lower().endswith('.pdf'):
                    logger.error(
                        "MinIO文件读取失败: 源文件为 %s，无对应 PDF；请使用 object-file 预览",
                        minio_file.get('path'),
                    )
        
        return jsonify({"success": False, "error": "PDF文件未找到"}), 404
    except Exception as e:
        logger.error(f"PDF服务失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== AI 问答 ====================

def _get_chroma_collection():
    return current_app.config.get('CHROMA_COLLECTION_OBJ')


def _get_full_document_by_chroma_id(chroma_id: str):
    """根据chroma_id获取该文档的所有分块并合并"""
    collection = _get_chroma_collection()
    if not collection:
        logger.warning("Chroma collection未初始化")
        return None
    try:
        results = collection.get(
            where={'doc_id': chroma_id},
            include=['documents', 'metadatas']
        )
        if results and results.get('documents') and results['documents']:
            docs = results['documents']
            meta = results['metadatas'][0] if results.get('metadatas') else {}
            full_text = '\n\n'.join(docs)
            return {
                'doc_id': chroma_id,
                'title': meta.get('title', chroma_id),
                'content': full_text,
                'chunk_count': len(docs),
                'total_length': len(full_text)
            }
        return None
    except Exception as e:
        logger.error(f"Chroma获取文档失败: {e}")
        return None


def _chroma_similarity_search(query: str, top_k: int = 5):
    """从Chroma向量数据库检索相关内容"""
    collection = _get_chroma_collection()
    if not collection:
        logger.warning("Chroma collection未初始化")
        return []
    try:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        ef = ONNXMiniLM_L6_V2()
        query_embedding = ef([query])[0]
        
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k
        )
        
        chunks = []
        if results and results.get('documents') and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                metadata = results['metadatas'][0][i] if results.get('metadatas') and results['metadatas'][0] else {}
                distance = results['distances'][0][i] if results.get('distances') and results['distances'][0] else None
                chunks.append({
                    'content': doc,
                    'metadata': metadata,
                    'title': metadata.get('title', '未知'),
                    'doc_id': metadata.get('doc_id', ''),
                    'score': 1.0 - (distance or 0),
                })
        
        return chunks
    except Exception as e:
        logger.error(f"Chroma检索失败: {e}")
        return []


@standard_bp.route('/summary', methods=['POST'])
def generate_summary():
    """
    返回标准摘要：直接读取 Neo4j File 节点的 Abstract，不再调用 LLM。
    body: { source_id: "WJ_00002" }
    """
    try:
        data = request.json or {}
        source_id = data.get('source_id', '')

        if not source_id:
            return jsonify({"success": False, "error": "source_id is required"}), 400

        file_info = _get_file_info_from_neo4j(source_id)
        if not file_info:
            return jsonify({"success": False, "error": "标准文件未找到"}), 404

        summary = (file_info.get('abstract') or '').strip()
        return jsonify({"success": True, "data": {"summary": summary}})
    except Exception as e:
        logger.error(f"读取摘要失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@standard_bp.route('/chat', methods=['POST'])
def standard_chat():
    """
    标准AI问答
    body: { source_id: "WJ_00002", question: "你的问题" }
    """
    try:
        data = request.json or {}
        source_id = data.get('source_id', '')
        question = data.get('question', '')
        
        if not question:
            return jsonify({"success": False, "error": "question is required"}), 400
        
        if not source_id:
            return jsonify({"success": False, "error": "source_id is required"}), 400
        
        file_info = _get_file_info_from_neo4j(source_id)
        if not file_info:
            return jsonify({"success": False, "error": "标准文件未找到"}), 404
        
        chroma_id = file_info.get('chroma_id', '')
        if not chroma_id:
            return jsonify({"success": False, "error": "该文件未关联向量数据"}), 404
        
        doc = _get_full_document_by_chroma_id(chroma_id)
        if not doc:
            return jsonify({"success": False, "error": "向量数据库中未找到该文档"}), 404
        
        prompt = f"""
{SYSTEM_PROMPT}

### 标准完整内容:
{doc['content']}

{DEFAULT_ANSWER_REQUIREMENTS}

### 用户问题: {question}
"""
        client = _get_llm_client()
        if not client:
            return jsonify({"success": False, "error": "LLM客户端不可用"}), 500
        
        response = client.chat.completions.create(
            model=current_app.config.get('OPENAI_MODEL', 'qwen-plus'),
            messages=[{"role": "user", "content": prompt}]
        )
        
        answer = response.choices[0].message.content
        return jsonify({"success": True, "data": {"answer": answer}})
    except Exception as e:
        logger.error(f"AI问答失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
