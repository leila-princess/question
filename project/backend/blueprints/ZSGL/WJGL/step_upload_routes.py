"""
文件分步骤上传API
将文件上传过程分解为3个独立步骤，避免超时
"""
import os
import json
import uuid
import logging
from flask import Blueprint, request, jsonify
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

from config import Config
from Database.minIo.main import upload_original_file, upload_images_from_extraction
from Database.Chroma.main import process_single_file as chroma_process_single
from Process_data.extractor import DocumentExtractor

logger = logging.getLogger(__name__)

step_upload_bp = Blueprint('step_upload', __name__, url_prefix='/api/fzsj/wjgl')

# 临时存储上传状态（生产环境应使用Redis）
_upload_sessions: Dict[str, Dict] = {}

# 文件类型前缀映射
TYPE_PREFIX_MAP = {
    "法规标准": "fgbz",
    "规范性文件": "gzfwj",
    "指导性文件": "zdzxwj",
    "试验方案": "syfa",
    "试验大纲": "sydg",
    "试验报告": "sybg",
    "专项评估报告": "zxpgbg",
    "其他": "qt"
}


def _generate_session_id() -> str:
    return f"upload_{uuid.uuid4().hex[:16]}"


def _get_next_file_number(prefix: str) -> int:
    from blueprints.ZSGL.WJGL.routes import _get_next_file_number as get_num
    return get_num(prefix)


def _extract_file_info(temp_path: str, doc_id: str, category: str) -> Dict[str, Any]:
    """提取文件信息（文档解析）"""
    ext = os.path.splitext(temp_path)[1].lower()
    extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
    
    extract_result = None
    try:
        if ext == '.pdf':
            extract_result = extractor.extract(temp_path)
        elif ext in ('.docx', '.doc'):
            extract_result = extractor.extract(temp_path)
        else:
            extract_result = extractor.extract(temp_path)
            
    except Exception as e:
        logger.error(f"File extraction failed: {e}")
        return {"error": str(e)}
    
    return extract_result


@step_upload_bp.route('/upload-step1', methods=['POST'])
def upload_step1():
    """
    步骤1：保存文件到文件数据库（MinIO + Neo4j File节点基础信息）
    
    返回：
    - file_id: 文件ID
    - doc_id: MinIO文档ID
    - file_info: 文件基本信息
    - has_images: 是否包含图片
    - image_preview_urls: 图片预览URL列表
    """
    try:
        # 1. 获取上传文件
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "No file provided"}), 400
        
        uploaded_file = request.files['file']
        filename = uploaded_file.filename
        if not filename:
            return jsonify({"success": False, "error": "Empty filename"}), 400
        
        # 2. 获取表单数据
        category = request.form.get('category', '其他')
        label = request.form.get('label', '')
        user_id = request.form.get('user_id', 'admin')
        file_title = request.form.get('title', '')
        file_abstract = request.form.get('abstract', '')
        file_keywords = json.loads(request.form.get('keywords', '[]'))
        file_ics = request.form.get('ics', '')
        file_ccs = request.form.get('ccs', '')
        
        # 3. 生成文件ID
        prefix = TYPE_PREFIX_MAP.get(category, "qt")
        next_num = _get_next_file_number(prefix)
        doc_id = f"{prefix}{next_num:04d}"
        file_node_id = f"WJ_{doc_id}"
        
        # 4. 保存到临时目录
        temp_dir = os.path.join(Config.OUTPUT_DIR, 'uploads')
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, filename)
        uploaded_file.save(temp_path)
        
        # 5. 提取文件元数据
        extract_result = _extract_file_info(temp_path, doc_id, category)
        
        if isinstance(extract_result, dict) and extract_result.get("error"):
            return jsonify({
                "success": False,
                "step": 1,
                "error": f"文件提取失败: {extract_result['error']}"
            }), 500
        
        # 6. 上传到MinIO
        title = file_title if file_title else os.path.splitext(filename)[0]
        ext = os.path.splitext(filename)[1].lower()
        
        minio_path = upload_original_file(
            Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, temp_path,
            doc_id, title, category, label, file_ics, file_ccs, user_id,
            abstract=file_abstract, keywords=file_keywords
        )
        
        # 7. 上传图片（如果有）
        image_paths = []
        image_preview_urls = []
        if extract_result and extract_result.get("all_figures"):
            image_paths = upload_images_from_extraction(
                Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, extract_result,
                doc_id, title, category, user_id,
                file_ext=ext.lstrip(".")
            )
            # 生成预览URL
            for img_path in image_paths:
                if isinstance(img_path, dict) and img_path.get("minio_url"):
                    image_preview_urls.append(img_path.get("minio_url"))
        
        # 8. 创建Neo4j File节点（基础信息）
        neo4j_result = None
        if Config.NEO4J_DRIVER:
            from blueprints.ZSGL.WJGL.routes import _create_file_node
            neo4j_result = _create_file_node(
                file_node_id, category, label, title,
                "", 0, doc_id, image_paths, user_id,
                abstract=file_abstract,
                keywords=file_keywords,
                ics=file_ics,
                ccs=file_ccs,
                file_type_ext=ext.lstrip("."),
            )
        
        # 9. 保存session信息供后续步骤使用
        session_id = _generate_session_id()
        _upload_sessions[session_id] = {
            "file_node_id": file_node_id,
            "doc_id": doc_id,
            "temp_path": temp_path,
            "extract_result": extract_result,
            "category": category,
            "title": title,
            "user_id": user_id,
            "image_paths": image_paths,
            "created_at": datetime.now().isoformat()
        }
        
        # 10. 准备文件信息
        file_info = {
            "filename": filename,
            "title": title,
            "category": category,
            "label": label,
            "abstract": file_abstract[:200] + "..." if len(file_abstract) > 200 else file_abstract,
            "keywords": file_keywords,
            "ics": file_ics,
            "ccs": file_ccs,
            "doc_id": doc_id,
            "pages": extract_result.get("total_pages", 0) if extract_result else 0,
            "tables": len(extract_result.get("all_tables", [])) if extract_result else 0,
            "figures": len(extract_result.get("all_figures", [])) if extract_result else 0,
            "file_type": ext.lstrip(".")
        }
        
        return jsonify({
            "success": True,
            "step": 1,
            "message": "文件已成功保存到文件数据库",
            "session_id": session_id,
            "file_id": file_node_id,
            "doc_id": doc_id,
            "file_info": file_info,
            "has_images": len(image_preview_urls) > 0,
            "image_preview_urls": image_preview_urls[:5],  # 最多返回5张预览
            "image_count": len(image_preview_urls),
            "neo4j_result": neo4j_result
        })
        
    except Exception as e:
        logger.error(f"Step 1 failed: {e}")
        return jsonify({
            "success": False,
            "step": 1,
            "error": str(e)
        }), 500


@step_upload_bp.route('/upload-step2/<session_id>', methods=['POST'])
def upload_step2(session_id: str):
    """
    步骤2：保存文件到向量数据库（Chroma）
    
    参数：
    - session_id: 步骤1返回的session_id
    
    返回：
    - chroma_id: Chroma文档ID
    - chroma_chunks: 分块数量
    - chroma_metadata: Chroma存储的元数据
    """
    try:
        # 1. 获取session信息
        session = _upload_sessions.get(session_id)
        if not session:
            return jsonify({
                "success": False,
                "step": 2,
                "error": "Session not found or expired. Please start from step 1."
            }), 400
        
        file_node_id = session["file_node_id"]
        doc_id = session["doc_id"]
        temp_path = session["temp_path"]
        extract_result = session["extract_result"]
        category = session["category"]
        title = session["title"]
        
        # 2. 检查Chroma是否可用
        if not Config.CHROMA_COLLECTION_OBJ:
            return jsonify({
                "success": False,
                "step": 2,
                "error": "Chroma database not initialized"
            }), 500
        
        # 3. 检查提取结果
        if not extract_result or not extract_result.get("full_text"):
            return jsonify({
                "success": False,
                "step": 2,
                "error": "No text content to sync to Chroma"
            }), 400
        
        # 4. 同步到Chroma
        chroma_id = ""
        chroma_chunks = 0
        chroma_ids_list = []
        chroma_error = None
        
        try:
            extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
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
                chroma_ids_list = chroma_result.get("chroma_ids", [])
            else:
                chroma_error = chroma_result.get("error", "Unknown error")
                
        except Exception as e:
            chroma_error = str(e)
            logger.error(f"Chroma sync failed: {e}")
        
        # 5. 更新session
        session["chroma_id"] = chroma_id
        session["chroma_chunks"] = chroma_chunks
        session["chroma_error"] = chroma_error
        
        if chroma_error:
            return jsonify({
                "success": False,
                "step": 2,
                "error": f"向量数据库同步失败: {chroma_error}",
                "chroma_id": chroma_id,
                "chroma_chunks": chroma_chunks
            }), 500
        
        # 6. 准备返回信息
        chroma_metadata = {
            "doc_id": chroma_id,
            "title": title,
            "category": category,
            "chunks": chroma_chunks,
            "chunk_ids": chroma_ids_list[:10],  # 最多返回10个ID
            "pages": extract_result.get("total_pages", 0),
            "text_length": len(extract_result.get("full_text", ""))
        }
        
        return jsonify({
            "success": True,
            "step": 2,
            "message": "文件已成功保存到向量数据库",
            "session_id": session_id,
            "file_id": file_node_id,
            "chroma_id": chroma_id,
            "chroma_chunks": chroma_chunks,
            "chroma_metadata": chroma_metadata
        })
        
    except Exception as e:
        logger.error(f"Step 2 failed: {e}")
        return jsonify({
            "success": False,
            "step": 2,
            "error": str(e)
        }), 500


@step_upload_bp.route('/upload-step3/<session_id>', methods=['POST'])
def upload_step3(session_id: str):
    """
    步骤3：保存文件到图数据库（Neo4j关系/节点属性更新）
    
    参数：
    - session_id: 步骤1返回的session_id
    - associations: 关联信息（可选）
    
    返回：
    - neo4j_node: Neo4j节点完整属性
    - associations: 创建的关联列表
    """
    try:
        # 1. 获取session信息
        session = _upload_sessions.get(session_id)
        if not session:
            return jsonify({
                "success": False,
                "step": 3,
                "error": "Session not found or expired"
            }), 400
        
        file_node_id = session["file_node_id"]
        doc_id = session["doc_id"]
        category = session["category"]
        title = session["title"]
        user_id = session["user_id"]
        chroma_id = session.get("chroma_id", "")
        chroma_chunks = session.get("chroma_chunks", 0)
        image_paths = session.get("image_paths", [])
        extract_result = session.get("extract_result")
        
        # 2. 获取请求参数
        data = request.get_json() or {}
        associations = data.get('associations', [])
        
        # 3. 检查Neo4j是否可用
        if not Config.NEO4J_DRIVER:
            return jsonify({
                "success": False,
                "step": 3,
                "error": "Neo4j not initialized"
            }), 500
        
        # 4. 更新Neo4j节点属性（添加Chroma信息）
        from blueprints.ZSGL.WJGL.routes import _update_file_node_chroma_info
        
        update_result = _update_file_node_chroma_info(
            file_node_id,
            chroma_id=chroma_id,
            chroma_chunks=chroma_chunks
        )
        
        # 5. 创建关联
        associations_result = []
        if associations:
            from blueprints.ZSGL.WJGL.routes import _create_file_associations
            associations_result = _create_file_associations(file_node_id, associations)
        
        # 6. 查询完整的节点信息
        node_properties = _get_file_node_properties(file_node_id)
        
        # 7. 清理临时文件
        temp_path = session.get("temp_path")
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass
        
        # 8. 清理session
        del _upload_sessions[session_id]
        
        return jsonify({
            "success": True,
            "step": 3,
            "message": "文件已成功保存到图数据库",
            "file_id": file_node_id,
            "neo4j_node": node_properties,
            "associations": associations_result,
            "chroma_update": update_result
        })
        
    except Exception as e:
        logger.error(f"Step 3 failed: {e}")
        return jsonify({
            "success": False,
            "step": 3,
            "error": str(e)
        }), 500


def _get_file_node_properties(file_node_id: str) -> Dict[str, Any]:
    """获取Neo4j File节点的完整属性"""
    try:
        with Config.NEO4J_DRIVER.session() as neo4j_session:
            result = neo4j_session.run(
                """
                MATCH (f:File {Id: $file_id})
                RETURN f
                """,
                file_id=file_node_id
            )
            record = result.single()
            if record:
                node = record["f"]
                return {
                    "id": node.get("Id"),
                    "name": node.get("Name"),
                    "type": node.get("Type"),
                    "label": node.get("Label"),
                    "status": node.get("Status"),
                    "minio_id": node.get("minIo_id"),
                    "chroma_id": node.get("Chroma_id"),
                    "chroma_chunks": node.get("Chroma_chunks"),
                    "ics": node.get("ICS"),
                    "ccs": node.get("CCS"),
                    "abstract": node.get("Abstract"),
                    "keywords": node.get("Keywords", []),
                    "file_type": node.get("File_type"),
                    "user_id": node.get("User_id"),
                    "has_images": node.get("Image_true", False),
                    "created_at": node.get("Created_at")
                }
    except Exception as e:
        logger.error(f"Get file node properties failed: {e}")
    return {}


@step_upload_bp.route('/upload-status/<session_id>', methods=['GET'])
def get_upload_status(session_id: str):
    """获取上传session的当前状态"""
    session = _upload_sessions.get(session_id)
    if not session:
        return jsonify({
            "success": False,
            "error": "Session not found or expired"
        }), 404
    
    return jsonify({
        "success": True,
        "session_id": session_id,
        "file_id": session.get("file_node_id"),
        "doc_id": session.get("doc_id"),
        "step1_completed": True,
        "step2_completed": session.get("chroma_id") is not None,
        "step3_completed": False,  # 步骤3完成后session会被删除
        "chroma_id": session.get("chroma_id"),
        "chroma_chunks": session.get("chroma_chunks"),
        "chroma_error": session.get("chroma_error")
    })


@step_upload_bp.route('/upload-cancel/<session_id>', methods=['POST'])
def cancel_upload(session_id: str):
    """取消上传并清理资源"""
    session = _upload_sessions.pop(session_id, None)
    
    if session:
        # 清理临时文件
        temp_path = session.get("temp_path")
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass
    
    return jsonify({
        "success": True,
        "message": "Upload cancelled and resources cleaned up"
    })
