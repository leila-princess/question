from flask import Blueprint, send_file, send_from_directory, request, jsonify, current_app
import os
import logging

logger = logging.getLogger(__name__)

download_bp = Blueprint('download', __name__, url_prefix='/api/download')

@download_bp.route('/report', methods=['GET'])
def api_download_report():
    """下载生成的报告"""
    file = request.args.get("file")
    if not file:
        return jsonify({"success": False, "error": "缺少文件名参数"}), 400
    
    file_path = os.path.join(current_app.config['OUTPUT_DIR'], file)
    
    if not os.path.exists(file_path):
        return jsonify({"success": False, "error": "文件不存在"}), 404
    
    try:
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        logger.error(f"下载文件失败: {str(e)}")
        return jsonify({"success": False, "error": "文件下载失败"}), 500

@download_bp.route('/scheme/<filename>', methods=['GET'])
def download_scheme(filename):
    """下载方案文件"""
    try:
        scheme_path = os.path.join(current_app.config['SCHEME_DIR'], filename)
        
        if not os.path.exists(scheme_path):
            return jsonify({"success": False, "error": "文件不存在"}), 404
        
        return send_from_directory(current_app.config['SCHEME_DIR'], filename, as_attachment=True)
    except Exception as e:
        logger.error(f"下载方案失败: {str(e)}")
        return jsonify({"success": False, "error": "文件下载失败"}), 500