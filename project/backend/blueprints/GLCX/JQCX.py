from flask import Blueprint, request, jsonify, current_app
import json
import traceback
import logging
from utils.neo4j_utils import search_standards, get_standard_summary

logger = logging.getLogger(__name__)

jqcx_bp = Blueprint('knowledge_graph', __name__, url_prefix='/api/glcx/jqcx')

@jqcx_bp.route('/search', methods=['POST'])
def search():
    """知识图谱搜索接口"""
    try:
        data = request.json
        logger.info(f"收到搜索请求: {data}")
        
        if not request.is_json:
            return jsonify({
                "success": False,
                "error": "请求必须是JSON格式",
                "code": 400
            }), 400
            
        keyword = data.get('keyword')
        node_type = data.get('nodeType')
        relationship_type = data.get('relationshipType')
        source = data.get('source')
        limit = data.get('limit', 100)
        
        driver = current_app.config['NEO4J_DRIVER']
        if not driver:
            return jsonify({
                "success": False,
                "error": "Neo4j驱动不可用",
                "code": 500
            }), 500
        
        results = search_standards(
            driver=driver,
            keyword=keyword,
            node_type=node_type,
            relationship_type=relationship_type,
            source=source,
            limit=limit
        )
        
        logger.info(f"搜索完成，返回 {len(results)} 条结果")
        return jsonify({
            "success": True,
            "data": results,
            "total": len(results),
            "message": "查询成功"
        })
    
    except Exception as e:
        logger.error(f"搜索出错: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "error": "服务器内部错误",
            "code": 500,
            "details": str(e)
        }), 500