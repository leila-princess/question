from flask import Blueprint, request, jsonify, current_app
import traceback
import logging
from utils.document_utils import (
    extract_document_structure,
    extract_tables_and_titles,
    extract_images_from_docx_bytes
)
from docx import Document
import io

logger = logging.getLogger(__name__)

parser_bp = Blueprint('document_parser', __name__, url_prefix='/api/parser')

@parser_bp.route('/parseWord', methods=['POST'])
def parse_word_api():
    """解析Word文档API"""
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "缺少 file 字段"}), 400
        
        f = request.files['file']
        if not f.filename.lower().endswith('.docx'):
            return jsonify({"success": False, "error": "只支持 .docx 文件"}), 400

        # 使用大模型解析文档
        file_bytes = f.read()
        
        # 获取客户端和模型
        client = current_app.config['OPENAI_CLIENT']
        model = current_app.config['OPENAI_MODEL']
        
        if not client:
            return jsonify({
                "success": False,
                "error": "OpenAI客户端不可用"
            }), 500
        
        doc_info = extract_document_structure(client, model, file_bytes)
        
        # 提取表格和图片
        doc = Document(io.BytesIO(file_bytes))
        tables = extract_tables_and_titles(doc)
        images = extract_images_from_docx_bytes(file_bytes)
        
        return jsonify({
            "success": True,
            "title": doc_info["title"],
            "headings": doc_info["headings"],
            "sections": doc_info["sections"],
            "fullText": doc_info.get("full_text", ""),
            "tables": tables,
            "images": images
        })

    except Exception as e:
        logger.exception("文档解析错误: %s", e)
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500