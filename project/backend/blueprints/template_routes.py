"""
模板管理API路由
提供模板的CRUD、搜索、导入导出功能
"""
from doctest import debug_script
from flask import Blueprint, request, jsonify
import json
import logging
from typing import Dict, Any
from datetime import datetime

from config import Config
from services.template_manager import (
    get_template_manager, 
    create_default_template_for_user,
    TemplateManager,
    TemplateValidator
)

logger = logging.getLogger(__name__)
template_bp = Blueprint('template', __name__, url_prefix='/api/templates')


def get_chroma_collection():
    """获取Chroma集合"""
    return getattr(Config, 'CHROMA_COLLECTION_OBJ', None)


@template_bp.route('/list', methods=['GET'])
def list_templates():
    """获取用户的模板列表"""
    user_id = request.args.get('user_id', 'anonymous')
    category = request.args.get('category')
    
    try:
        manager = get_template_manager(get_chroma_collection())
        
        # 搜索该用户的所有模板
        results = manager.search_templates("模板", user_id=user_id, n_results=50)
        
        templates = []
        for result in results:
            template_data = result.get("template", {})
            metadata = template_data.get("metadata", {})
            
            # 分类过滤
            if category and metadata.get("category") != category:
                continue
            
            templates.append({
                "id": metadata.get("id"),
                "name": metadata.get("name"),
                "description": metadata.get("description"),
                "category": metadata.get("category"),
                "version": metadata.get("version"),
                "created_at": metadata.get("created_at"),
                "updated_at": metadata.get("updated_at"),
                "is_default": metadata.get("is_default"),
                "is_system": metadata.get("is_system"),
                "status": metadata.get("status"),
                "variables_count": len(template_data.get("variables", [])),
                "sections_count": len(template_data.get("sections", []))
            })
        
        return jsonify({
            "success": True,
            "templates": templates,
            "total": len(templates)
        })
        
    except Exception as e:
        logger.error(f"List templates failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@template_bp.route('/detail/<template_id>', methods=['GET'])
def get_template_detail(template_id: str):
    """获取模板详情"""
    try:
        manager = get_template_manager(get_chroma_collection())
        template = manager.load_from_chroma(template_id)
        
        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404
        
        result = template.to_dict()
        
        import re
        sections = []
        content = template.content
        if content:
            section_pattern = r'(##\s+.+?)(?=\n##\s+|\n*$)'
            matches = re.findall(section_pattern, content, re.DOTALL)
            for match in matches:
                match = match.strip()
                if match.startswith('##'):
                    first_newline = match.find('\n')
                    if first_newline > 0:
                        title = match[3:first_newline].strip()
                        section_content = match[first_newline+1:].strip()
                    else:
                        title = match[3:].strip()
                        section_content = ''
                    if title:
                        sections.append({"title": title, "content": section_content})
        if sections:
            result['sections'] = sections
        
        if 'metadata' in result:
            result['metadata']['name'] = result['metadata'].get('name', template.metadata.name)
            result['metadata']['description'] = result['metadata'].get('description', template.metadata.description)
        
        return jsonify({
            "success": True,
            "template": result
        })
        
    except Exception as e:
        logger.error(f"Get template detail failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@template_bp.route('/create', methods=['POST'])
def create_template():
    """创建新模板"""
    data = request.get_json()
    user_id = data.get('user_id', 'anonymous')
    
    try:
        manager = get_template_manager(get_chroma_collection())
        
        # 创建默认模板
        template = manager.create_default_template(user_id)
        
        # 应用用户自定义内容
        if data.get('name'):
            template.metadata.name = data['name']
        if data.get('description'):
            template.metadata.description = data['description']
        if data.get('category'):
            template.metadata.category = data['category']
        if data.get('content'):
            template.content = data['content']
        if data.get('variables'):
            from services.template_manager import TemplateVariable
            template.variables = [TemplateVariable(**v) for v in data['variables']]
        if data.get('sections'):
            from services.template_manager import TemplateSection
            template.sections = [TemplateSection(
                id=s.get('id', f'section_{i+1}'),
                title=s.get('title', ''),
                required=s.get('required', False),
                can_edit_title=s.get('can_edit_title', True),
                can_delete=s.get('can_delete', True),
                has_retrieval_mark=s.get('has_retrieval_mark', False),
                depends_on=s.get('depends_on'),
                content=s.get('content', '')
            ) for i, s in enumerate(data['sections'])]
        
        # 校验模板
        validator = TemplateValidator()
        validation_result = validator.validate(template)
        
        # 保存到Chroma
        if get_chroma_collection():
            success = manager.save_to_chroma(template)
            if not success:
                return jsonify({
                    "success": False, 
                    "error": "Failed to save template to database"
                }), 500
        
        return jsonify({
            "success": True,
            "template_id": template.metadata.id,
            "template": template.to_dict(),
            "validation": validation_result
        })
        
    except Exception as e:
        logger.error(f"Create template failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@template_bp.route('/update/<template_id>', methods=['PUT'])
def update_template(template_id: str):
    """更新模板"""
    data = request.get_json()
    user_id = data.get('user_id', 'anonymous')
    
    try:
        manager = get_template_manager(get_chroma_collection())
        
        # 加载现有模板
        existing = manager.load_from_chroma(template_id)
        if not existing:
            return jsonify({"success": False, "error": "Template not found"}), 404
        
        # 检查权限
        if existing.metadata.created_by != user_id and existing.metadata.is_system:
            return jsonify({"success": False, "error": "Permission denied"}), 403
        
        # 更新字段
        if data.get('name'):
            existing.metadata.name = data['name']
        if data.get('description'):
            existing.metadata.description = data['description']
        if data.get('category'):
            existing.metadata.category = data['category']
        if data.get('content'):
            existing.content = data['content']
        if data.get('variables'):
            from services.template_manager import TemplateVariable
            existing.variables = [TemplateVariable(**v) for v in data['variables']]
        if data.get('sections'):
            from services.template_manager import TemplateSection
            existing.sections = [TemplateSection(**s) for s in data['sections']]
        
        existing.metadata.updated_at = datetime.now().isoformat()
        
        # 校验
        validator = TemplateValidator()
        validation_result = validator.validate(existing)
        
        # 保存
        if get_chroma_collection():
            success = manager.update_in_chroma(existing)
            if not success:
                return jsonify({
                    "success": False,
                    "error": "Failed to update template"
                }), 500
        
        return jsonify({
            "success": True,
            "template": existing.to_dict(),
            "validation": validation_result
        })
        
    except Exception as e:
        logger.error(f"Update template failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@template_bp.route('/delete/<template_id>', methods=['DELETE'])
def delete_template(template_id: str):
    """删除模板"""
    user_id = request.args.get('user_id', 'anonymous')
    
    try:
        manager = get_template_manager(get_chroma_collection())
        
        # 加载模板检查权限
        existing = manager.load_from_chroma(template_id)
        if not existing:
            return jsonify({"success": False, "error": "Template not found"}), 404
        
        if existing.metadata.created_by != user_id:
            return jsonify({"success": False, "error": "Permission denied"}), 403
        
        if existing.metadata.is_system:
            return jsonify({"success": False, "error": "Cannot delete system template"}), 400
        
        # 删除
        if get_chroma_collection():
            success = manager.delete_from_chroma(template_id)
            if not success:
                return jsonify({"success": False, "error": "Failed to delete"}), 500
        
        return jsonify({"success": True, "message": "Template deleted"})
        
    except Exception as e:
        logger.error(f"Delete template failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@template_bp.route('/search', methods=['POST'])
def search_templates():
    """搜索模板"""
    data = request.get_json()
    query = data.get('query', '')
    user_id = data.get('user_id', 'anonymous')
    n_results = data.get('n_results', 10)
    
    try:
        manager = get_template_manager(get_chroma_collection())
        results = manager.search_templates(query, user_id, n_results)
        
        templates = []
        for result in results:
            template_data = result.get("template", {})
            metadata = template_data.get("metadata", {})
            templates.append({
                "id": metadata.get("id"),
                "name": metadata.get("name"),
                "description": metadata.get("description"),
                "category": metadata.get("category"),
                "relevance_score": result.get("relevance_score", 0),
                "variables_count": len(template_data.get("variables", [])),
                "sections_count": len(template_data.get("sections", []))
            })
        
        return jsonify({
            "success": True,
            "templates": templates,
            "query": query
        })
        
    except Exception as e:
        logger.error(f"Search templates failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@template_bp.route('/validate', methods=['POST'])
def validate_template():
    """校验模板"""
    data = request.get_json()
    
    try:
        from services.template_manager import (
            TemplateMetadata, TemplateVariable, 
            TemplateSection, UserTemplate
        )
        
        # 构建模板对象
        metadata = TemplateMetadata(**data.get('metadata', {}))
        variables = [TemplateVariable(**v) for v in data.get('variables', [])]
        sections = [TemplateSection(**s) for s in data.get('sections', [])]
        
        template = UserTemplate(
            metadata=metadata,
            variables=variables,
            sections=sections,
            content=data.get('content', ''),
            retrieval_marks=data.get('retrieval_marks', []),
            validation_rules=data.get('validation_rules', {})
        )
        
        # 执行校验
        validator = TemplateValidator()
        result = validator.validate(template)
        
        return jsonify({
            "success": True,
            "validation": result
        })
        
    except Exception as e:
        logger.error(f"Validate template failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@template_bp.route('/default', methods=['POST'])
def create_default():
    """为用户创建默认模板"""
    data = request.get_json()
    user_id = data.get('user_id', 'anonymous')
    
    try:
        template = create_default_template_for_user(
            user_id, 
            get_chroma_collection()
        )
        
        if not template:
            return jsonify({"success": False, "error": "Failed to create"}), 500
        
        return jsonify({
            "success": True,
            "template_id": template.metadata.id,
            "template": template.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Create default template failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
