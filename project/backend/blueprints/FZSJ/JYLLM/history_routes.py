"""
JYLLM历史记录API路由
"""
import os
import logging
from flask import Blueprint, request, jsonify
from config import Config
from services.jyllm_history_manager import get_history_manager

logger = logging.getLogger(__name__)
jyllm_history_bp = Blueprint('jyllm_history', __name__, url_prefix='/api/fzsj/jyllm/history')


@jyllm_history_bp.route('/list', methods=['GET'])
def list_history():
    """获取用户历史记录列表"""
    user_id = request.args.get('user_id', 'anonymous')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 10))
    
    manager = get_history_manager()
    result = manager.get_user_history_list(user_id, page, page_size)
    
    return jsonify(result)


@jyllm_history_bp.route('/detail/<task_id>', methods=['GET'])
def get_detail(task_id: str):
    """获取方案详情"""
    user_id = request.args.get('user_id', 'anonymous')
    
    manager = get_history_manager()
    detail = manager.get_scheme_detail(task_id, user_id)
    
    if detail:
        return jsonify({"success": True, "data": detail})
    else:
        return jsonify({"success": False, "error": "Scheme not found"}), 404


@jyllm_history_bp.route('/save', methods=['POST'])
def save_history():
    """保存方案历史"""
    data = request.get_json()
    
    manager = get_history_manager()
    
    # 保存主表
    success = manager.save_scheme_history(data)
    
    # 保存章节
    if success and data.get('chapters'):
        manager.save_chapters(data['task_id'], data['chapters'])
    
    if success:
        return jsonify({"success": True, "message": "History saved"})
    else:
        return jsonify({"success": False, "error": "Failed to save"}), 500


@jyllm_history_bp.route('/save-interaction', methods=['POST'])
def save_interaction():
    """保存智能体交互"""
    data = request.get_json()
    
    manager = get_history_manager()
    success = manager.save_agent_interaction(data)
    
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Failed to save"}), 500


@jyllm_history_bp.route('/delete/<task_id>', methods=['DELETE'])
def delete_history(task_id: str):
    """删除历史记录，并同步删除 Chroma 向量索引。"""
    user_id = request.args.get('user_id', 'anonymous')
    
    manager = get_history_manager()
    success = manager.delete_scheme_history(task_id, user_id)
    
    chroma_result = {"deleted": 0}
    if success:
        try:
            from Database.Chroma.delete import ChromaDeleteManager
            if Config.CHROMA_COLLECTION_OBJ:
                dm = ChromaDeleteManager(Config.CHROMA_CLIENT, Config.CHROMA_COLLECTION_OBJ)
                chroma_result = dm.delete_document(task_id)
        except Exception as e:
            logger.warning("Chroma delete failed for task %s: %s", task_id, e)
            chroma_result = {"success": False, "error": str(e)}

        try:
            scheme_dir = os.path.join(Config.SCHEME_DIR, task_id)
            if os.path.isdir(scheme_dir):
                import shutil
                shutil.rmtree(scheme_dir, ignore_errors=True)
        except Exception as e:
            logger.warning("Local scheme dir delete failed: %s", e)
    
    if success:
        return jsonify({"success": True, "message": "Deleted", "chroma": chroma_result})
    else:
        return jsonify({"success": False, "error": "Failed to delete"}), 500
