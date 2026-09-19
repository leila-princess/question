"""
Schema API — 提供知识图谱 Schema 的 HTTP 接口
前端通过此 API 获取节点类型、属性定义、关系类型等信息
"""
import json
import os
from flask import Blueprint, jsonify

schema_bp = Blueprint('schema', __name__)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'schema.json')


@schema_bp.route('/api/schema', methods=['GET'])
def get_schema():
    """返回完整 Schema 定义"""
    try:
        with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        return jsonify(schema)
    except FileNotFoundError:
        return jsonify({'error': 'schema.json not found'}), 404
    except json.JSONDecodeError:
        return jsonify({'error': 'Invalid JSON in schema.json'}), 500


@schema_bp.route('/api/schema/node-types', methods=['GET'])
def get_node_types():
    """返回所有节点类型定义"""
    try:
        with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        return jsonify(schema.get('nodeTypes', {}))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@schema_bp.route('/api/schema/relationships', methods=['GET'])
def get_relationships():
    """返回所有关系类型定义"""
    try:
        with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        return jsonify(schema.get('relationshipTypes', {}))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@schema_bp.route('/api/schema/queries', methods=['GET'])
def get_query_templates():
    """返回预定义的查询模板"""
    try:
        with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        return jsonify(schema.get('queryTemplates', {}))
    except Exception as e:
        return jsonify({'error': str(e)}), 500
