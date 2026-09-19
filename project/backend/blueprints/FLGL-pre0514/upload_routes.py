"""
分类管理(FLGL) API — 文件上传

节点类型:
  EquipmentType, Equipment, Environment, TestProject, Indicator, TestMethod, File
"""
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
import uuid
import logging
# ==================== 文件上传接口（测试用）====================
# 在文件顶部添加必要的导入（如果没有的话）
import os
import time
from werkzeug.utils import secure_filename
#===================== 导入149文件解析文件 ===================
from docx import Document
import json
import re
from .test_extrac_output import (
    get_table_caption,
    extract_entities_from_table,
    export_to_json,
    merge_entities,
    print_entities,
    set_id_counters_for_upload,
)

logger = logging.getLogger(__name__)

from utils.operation_log import log_user_action

flgl_upload_bp = Blueprint('flgl_upload', __name__, url_prefix='/api/FLGL')

# 配置上传文件夹（放在文件顶部，其他路由之后）
UPLOAD_FOLDER = 'uploads/flgl'
ALLOWED_EXTENSIONS = {'doc', 'docx'}

# 确保上传目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def process_word_document_with_output(file_path, output_dir, id_starts=None):
    """处理Word文档，提取所有表格的实体，返回实体列表。id_starts 来自 Neo4j 最大 Id 续接，见 get_next_seq_starts_for_word_upload。"""
    set_id_counters_for_upload(id_starts)
    try:
        doc = Document(file_path)
    except Exception as e:
        print(f"无法打开文档: {e}")
        return []
    
    if not doc.tables:
        print("文档中没有找到表格")
        return []
    
    all_entities = []
    
    for table_idx, table in enumerate(doc.tables, 1):
        print("\n" + "=" * 80)
        
        # 获取表题注和一级设备类别
        level1_category, full_caption = get_table_caption(table)
        print(f"📋 表题注: {full_caption}")
        print(f"🏷️  一级设备类别: {level1_category}")
        
        # 提取实体
        entities = extract_entities_from_table(table, level1_category)
        all_entities.append(entities)
        
        # 打印实体
        print_entities(entities)
        
        # 导出为JSON（使用指定的输出目录）
        json_file = os.path.join(output_dir, f"entities_table_{table_idx}_{level1_category}.json")
        export_to_json(entities, json_file)
    
    print("\n" + "=" * 80)
    print("✅ 所有表格处理完成！")
    return all_entities

import hashlib

def get_file_md5(file_path):
    """计算文件的MD5值"""
    hash_md5 = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def get_file_md5_from_upload(file):
    """从上传的文件对象计算MD5（不保存文件）"""
    hash_md5 = hashlib.md5()
    file.seek(0)  # 重置文件指针
    for chunk in iter(lambda: file.read(4096), b""):
        hash_md5.update(chunk)
    file.seek(0)  # 重置文件指针，以便后续保存
    return hash_md5.hexdigest()


@flgl_upload_bp.route('/upload', methods=['POST'])
def upload_word_file():
    """
    上传 Word 文件并解析，生成图数据库节点和关系的JSON文件
    支持去重：基于文件内容的MD5值判断是否已上传过相同的文件
    """
    try:
        logger.info("=" * 50)
        logger.info("收到文件上传请求")
        
        # 检查是否有文件
        if 'file' not in request.files:
            logger.error("请求中没有 'file' 字段")
            return jsonify({
                'success': False,
                'message': '没有文件，请确保表单字段名为 "file"'
            }), 400
        
        file = request.files['file']
        logger.info(f"收到文件: {file.filename}")
        
        # 检查文件名
        if file.filename == '':
            logger.error("文件名为空")
            return jsonify({
                'success': False,
                'message': '文件名为空'
            }), 400
        
        # 检查文件类型
        if not allowed_file(file.filename):
            logger.error(f"不支持的文件类型: {file.filename}")
            return jsonify({
                'success': False,
                'message': f'只支持 Word 文件（.doc 或 .docx），当前文件: {file.filename}'
            }), 400

        # 计算文件MD5值
        file_md5 = get_file_md5_from_upload(file)
        logger.info(f"文件MD5: {file_md5}")

        # 检查是否已存在相同内容文件
        records_file = os.path.join(UPLOAD_FOLDER, "upload_records.json")
        existing_record = None
        
        if os.path.exists(records_file):
            try:
                with open(records_file, 'r', encoding='utf-8') as f:
                    records = json.load(f)
                    for record in records:
                        if record.get('file_md5') == file_md5:
                            existing_record = record
                            break
            except Exception as e:
                logger.warning(f"读取上传记录失败: {e}")
        
        # 如果已存在相同文件，直接返回已有的task_id
        if existing_record:
            logger.info(f"发现重复文件，返回已有任务ID: {existing_record['task_id']}")
            output_dir = existing_record.get('output_dir')
            combined_json = os.path.join(output_dir, "all_entities_combined.json") if output_dir else None
            
            if not combined_json or not os.path.exists(combined_json):
                logger.warning(f"解析结果文件不存在，将重新解析")
                existing_record = None
            else:
                log_user_action(
                    f"分类管理-上传解析Word(重复跳过) task={existing_record['task_id']} file={file.filename[:120]}"
                )
                return jsonify({
                    'success': True,
                    'message': '文件已存在，无需重复上传',
                    'data': {
                        'task_id': existing_record['task_id'],
                        'original_filename': file.filename,
                        'existing_filename': existing_record['original_filename'],
                        'is_duplicate': True,
                        'upload_time': existing_record['upload_time'],
                        'output_dir': existing_record['output_dir'],
                        'parsed_tables': existing_record.get('parsed_tables', 0)
                    }
                })
        
        # 新文件：保存文件
        filename = secure_filename(file.filename)
        timestamp = int(time.time())
        saved_filename = f"{timestamp}_{filename}"
        filepath = os.path.join(UPLOAD_FOLDER, saved_filename)
        file.save(filepath)
        
        logger.info(f"文件保存成功: {filepath}")
        
        # 创建解析结果输出目录
        output_dir = os.path.join(UPLOAD_FOLDER, f"parsed_{timestamp}")
        os.makedirs(output_dir, exist_ok=True)
        
        # 解析Word文档并生成JSON（Id 与图库中各类型最大序号续接）
        parsed_tables = 0
        try:
            id_starts = None
            try:
                from .routes import get_next_seq_starts_for_word_upload
                id_starts = get_next_seq_starts_for_word_upload()
                logger.info(
                    "Word 解析使用 Neo4j 续接序号: %s",
                    id_starts,
                )
            except Exception as e:
                logger.warning("读取 Neo4j 最大 ID 续接失败，新节点从 0001 起: %s", e)
            all_entities = process_word_document_with_output(filepath, output_dir, id_starts=id_starts)
            
            logger.info(f"解析返回数据类型: {type(all_entities)}")
            logger.info(f"解析返回数据长度: {len(all_entities) if isinstance(all_entities, list) else 'N/A'}")
            
            # 生成合并的JSON文件
            if all_entities and isinstance(all_entities, list):
                # 保存原始解析结果（每个表格单独的实体）
                raw_json_path = os.path.join(output_dir, "all_entities_raw.json")
                # 注意：export_to_json 期望字典，但这里传入的是列表，所以需要特殊处理
                with open(raw_json_path, 'w', encoding='utf-8') as f:
                    json.dump(all_entities, f, ensure_ascii=False, indent=2)
                
                # 合并所有表格的实体
                combined_entities = merge_entities(all_entities)
                combined_json_path = os.path.join(output_dir, "all_entities_combined.json")
                
                # 保存合并后的数据
                if isinstance(combined_entities, dict):
                    export_to_json(combined_entities, combined_json_path)
                else:
                    # 如果合并后不是字典，直接保存
                    with open(combined_json_path, 'w', encoding='utf-8') as f:
                        json.dump(combined_entities, f, ensure_ascii=False, indent=2)
                
                parsed_tables = len(all_entities)
                
                # 生成摘要JSON
                summary = {
                    'tables_count': parsed_tables,
                    'upload_time': datetime.now().isoformat(),
                    'file_name': file.filename,
                    'file_md5': file_md5
                }
                summary_json_path = os.path.join(output_dir, "all_entities_summary.json")
                with open(summary_json_path, 'w', encoding='utf-8') as f:
                    json.dump(summary, f, ensure_ascii=False, indent=2)
                
                logger.info(f"解析完成，共处理 {parsed_tables} 个表格")
            else:
                logger.warning("解析结果为空或格式不正确")
                parsed_tables = 0
            
        except Exception as parse_error:
            logger.error(f"文件解析失败: {str(parse_error)}")
            import traceback
            traceback.print_exc()
            # 清理已创建的文件
            if os.path.exists(filepath):
                os.remove(filepath)
            if os.path.exists(output_dir):
                import shutil
                shutil.rmtree(output_dir)
            return jsonify({
                'success': False,
                'message': f'文件上传成功但解析失败: {str(parse_error)}'
            }), 500
        
        # 保存上传记录
        upload_record = {
            'task_id': str(timestamp),
            'file_md5': file_md5,
            'original_filename': file.filename,
            'saved_filename': saved_filename,
            'filepath': filepath,
            'output_dir': output_dir,
            'upload_time': datetime.now().isoformat(),
            'file_size': os.path.getsize(filepath),
            'parsed_tables': parsed_tables
        }
        
        # 更新记录文件
        records = []
        if os.path.exists(records_file):
            try:
                with open(records_file, 'r', encoding='utf-8') as f:
                    records = json.load(f)
            except:
                records = []
        
        records.append(upload_record)
        
        # 只保留最近100条记录
        if len(records) > 100:
            records = records[-100:]
        
        with open(records_file, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        
        logger.info("=" * 50)

        log_user_action(
            f"分类管理-上传并解析Word task={timestamp} file={file.filename[:120]} tables={parsed_tables}"
        )
        return jsonify({
            'success': True,
            'message': f'文件上传并解析成功',
            'data': {
                'task_id': str(timestamp),
                'original_filename': file.filename,
                'saved_filename': saved_filename,
                'filepath': filepath,
                'output_dir': output_dir,
                'size': os.path.getsize(filepath),
                'parsed_tables': parsed_tables,
                'is_duplicate': False
            }
        })
        
    except Exception as e:
        logger.error(f"上传失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'上传失败: {str(e)}'
        }), 500

# ==================== 新增：解析结果查看和编辑接口 ====================
@flgl_upload_bp.route('/parse-result/<task_id>', methods=['GET'])
def get_parse_result(task_id):
    """
    获取解析结果，返回结构化的节点信息供前端展示和编辑
    适配新的JSON格式：nodes包含 EquipmentType, Equipment, Environment, TestProject, Indicator, TestMethod, standard
    """
    try:
        # 构建解析结果目录路径
        output_dir = os.path.join(UPLOAD_FOLDER, f"parsed_{task_id}")
        
        if not os.path.exists(output_dir):
            return jsonify({
                'success': False,
                'message': f'未找到任务 {task_id} 的解析结果'
            }), 404
        
        # 优先读取编辑后的数据，如果没有则读取原始解析数据
        edited_path = os.path.join(output_dir, "all_entities_edited.json")
        if os.path.exists(edited_path):
            json_path = edited_path
            is_edited = True
        else:
            json_path = os.path.join(output_dir, "all_entities_combined.json")
            is_edited = False
        
        if not os.path.exists(json_path):
            return jsonify({
                'success': False,
                'message': '解析结果文件不存在'
            }), 404
        
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 转换为前端友好的表格数据结构
        table_data = {
            'task_id': task_id,
            'is_edited': is_edited,
            'equipment_types': [],      # EquipmentType
            'equipments': [],            # Equipment
            'environments': [],          # Environment
            'test_projects': [],         # TestProject
            'indicators': [],            # Indicator
            'test_methods': [],          # TestMethod
            'standards': [],             # standard
            'relationships': []          # relationships
        }
        
        # 处理节点数据
        nodes = data.get('nodes', {})
        
        # 1. 处理设备类型 (EquipmentType)
        for node in nodes.get('EquipmentType', []):
            table_data['equipment_types'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', ''),
                'status': node.get('Status', '')
            })
        
        # 2. 处理设备 (Equipment)
        for node in nodes.get('Equipment', []):
            table_data['equipments'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', ''),
                'level1_device_category': node.get('level1_device_category', '') or node.get('Level1_device_category', ''),
                'equipment_type_id': node.get('equipment_type_id', ''),
                'parent_id': node.get('parent_id', '')
            })
        
        # 3. 处理环境 (Environment)
        for node in nodes.get('Environment', []):
            table_data['environments'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', ''),
                'class': node.get('Class', ''),
                'combination': node.get('Combination', ''),
                'limit': node.get('Limit', ''),
                'request': node.get('Request', '')
            })
        
        # 4. 处理测试项目 (TestProject)
        for node in nodes.get('TestProject', []):
            table_data['test_projects'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', ''),
                'category': node.get('category', '')
            })
        
        # 5. 处理指标 (Indicator)
        for node in nodes.get('Indicator', []):
            table_data['indicators'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', ''),
                'unit': node.get('unit', '')
            })
        
        # 6. 处理测试方法 (TestMethod)
        for node in nodes.get('TestMethod', []):
            table_data['test_methods'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', '')
            })
        
        # 7. 处理标准 (standard)
        for node in nodes.get('standard', []):
            table_data['standards'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', '')
            })
        
        def _id_name_map() -> dict:
            m: dict = {}
            for n in nodes.get('EquipmentType', []):
                i = n.get('Id') or n.get('id')
                if i:
                    m[i] = (n.get('Name') or n.get('name') or '').strip()
            for n in nodes.get('Equipment', []):
                i = n.get('Id') or n.get('id')
                if i:
                    m[i] = (n.get('Name') or n.get('name') or '').strip()
            for n in nodes.get('Environment', []):
                i = n.get('Id') or n.get('id')
                if i:
                    m[i] = (n.get('Name') or n.get('name') or '').strip()
            for n in nodes.get('TestProject', []):
                i = n.get('Id') or n.get('id')
                if i:
                    m[i] = (n.get('Name') or n.get('name') or '').strip()
            for n in nodes.get('Indicator', []):
                i = n.get('Id') or n.get('id')
                if i:
                    m[i] = (n.get('Name') or n.get('name') or '').strip()
            for n in nodes.get('TestMethod', []):
                i = n.get('Id') or n.get('id')
                if i:
                    m[i] = (n.get('Name') or n.get('name') or '').strip()
            for n in nodes.get('standard', []):
                i = n.get('Id') or n.get('id')
                if i:
                    m[i] = (n.get('Name') or n.get('name') or '').strip()
            return m

        _names = _id_name_map()
        for rel in data.get('relationships', []):
            fid = rel.get('from_id', '') or ''
            tid = rel.get('to_id', '') or ''
            rtype = rel.get('type', '') or rel.get('relation', '')
            table_data['relationships'].append({
                'type': rtype,
                'relation': rtype,
                'from_id': fid,
                'to_id': tid,
                'from_name': _names.get(fid, ''),
                'to_name': _names.get(tid, ''),
                'from_type': rel.get('from_type', ''),
                'to_type': rel.get('to_type', ''),
            })
        
        logger.info(f"返回解析结果，任务ID: {task_id}")
        logger.info(f"设备类型数: {len(table_data['equipment_types'])}, 设备数: {len(table_data['equipments'])}")
        logger.info(f"测试项目数: {len(table_data['test_projects'])}, 指标数: {len(table_data['indicators'])}")
        
        return jsonify({
            'success': True,
            'data': table_data
        })
        
    except Exception as e:
        logger.error(f"获取解析结果失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取解析结果失败: {str(e)}'
        }), 500


@flgl_upload_bp.route('/update-node', methods=['POST'])
def update_node():
    """
    更新单个节点信息
    
    请求体格式：
    {
        "task_id": "1234567890",
        "node_type": "equipment",  # equipment_type, equipment, test_project, indicator
        "node_id": "eq_某设备",
        "updated_data": {
            "name": "新名称",
            "description": "新描述",
            ...
        }
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'message': '请求体不能为空'
            }), 400
        
        task_id = data.get('task_id')
        node_type = data.get('node_type')
        node_id = data.get('node_id')
        updated_data = data.get('updated_data', {})
        
        if not all([task_id, node_type, node_id]):
            return jsonify({
                'success': False,
                'message': '缺少必要参数: task_id, node_type, node_id'
            }), 400
        
        # 构建文件路径
        output_dir = os.path.join(UPLOAD_FOLDER, f"parsed_{task_id}")
        json_path = os.path.join(output_dir, "all_entities_edited.json")
        
        # 如果编辑文件不存在，则读取原始文件
        if not os.path.exists(json_path):
            original_path = os.path.join(output_dir, "all_entities_combined.json")
            if not os.path.exists(original_path):
                return jsonify({
                    'success': False,
                    'message': '未找到解析数据'
                }), 404
            # 复制原始数据到编辑文件
            import shutil
            shutil.copy(original_path, json_path)
        
        # 读取现有数据
        with open(json_path, 'r', encoding='utf-8') as f:
            entities_data = json.load(f)
        
        # 检测数据格式：新格式有 'nodes' 键，旧格式有 'level1_category' 键
        is_new_format = 'nodes' in entities_data
        
        # 更新节点
        updated = False
        
        if is_new_format:
            # ===== 新格式：nodes/relationships 结构 =====
            nodes = entities_data.get('nodes', {})
            new_type_mapping = {
                'equipment_type': 'EquipmentType',
                'equipment': 'Equipment',
                'environment': 'Environment',
                'test_project': 'TestProject',
                'indicator': 'Indicator',
                'test_method': 'TestMethod',
                'standard': 'standard',
            }
            node_list_key = new_type_mapping.get(node_type)
            if not node_list_key or node_list_key not in nodes:
                return jsonify({
                    'success': False,
                    'message': f'不支持的节点类型: {node_type}'
                }), 400
            
            node_list = nodes.get(node_list_key, [])
            for node in node_list:
                if node.get('Id') == node_id:
                    for k, v in updated_data.items():
                        # 映射前端字段名到后端字段名
                        field_map = {'name': 'Name', 'label': 'Label', 'description': 'Description',
                                     'type': 'Type', 'status': 'Status', 'class': 'Class',
                                     'combination': 'Combination', 'limit': 'Limit', 'request': 'Request',
                                     'unit': 'unit', 'category': 'category'}
                        backend_key = field_map.get(k, k)
                        node[backend_key] = v
                    updated = True
                    break
        else:
            # ===== 旧格式：level1_category/devices/test_items/indicators 结构 =====
            node_type_mapping = {
                'equipment_type': 'level1_category',
                'equipment': 'devices',
                'test_project': 'test_items',
                'indicator': 'indicators'
            }
            
            db_field = node_type_mapping.get(node_type)
            if not db_field:
                return jsonify({
                    'success': False,
                    'message': f'不支持的节点类型: {node_type}'
                }), 400
        
            # 遍历查找并更新（旧格式）
            for category in entities_data:
                if node_type == 'equipment_type' and category.get('level1_category') == node_id.replace('et_', ''):
                    # 更新设备类型名称
                    if 'name' in updated_data:
                        old_name = category['level1_category']
                        category['level1_category'] = updated_data['name']
                        # 同时更新所有相关设备的类型引用
                        for device in category.get('devices', []):
                            if device.get('equipment_type') == old_name:
                                device['equipment_type'] = updated_data['name']
                        for test_item in category.get('test_items', []):
                            if test_item.get('equipment_type') == old_name:
                                test_item['equipment_type'] = updated_data['name']
                    updated = True
                    break
                    
                elif node_type == 'equipment':
                    for device in category.get('devices', []):
                        if f"eq_{device.get('name')}" == node_id:
                            if 'name' in updated_data:
                                old_name = device['name']
                                device['name'] = updated_data['name']
                                for test_item in category.get('test_items', []):
                                    if test_item.get('device') == old_name:
                                        test_item['device'] = updated_data['name']
                            if 'description' in updated_data:
                                device['description'] = updated_data['description']
                            updated = True
                            break
                    if updated:
                        break
                        
                elif node_type == 'test_project':
                    for test_item in category.get('test_items', []):
                        if f"tp_{test_item.get('name')}" == node_id:
                            if 'name' in updated_data:
                                old_name = test_item['name']
                                test_item['name'] = updated_data['name']
                                for indicator in category.get('indicators', []):
                                    if indicator.get('test_item') == old_name:
                                        indicator['test_item'] = updated_data['name']
                            if 'description' in updated_data:
                                test_item['description'] = updated_data['description']
                            if 'related_device' in updated_data:
                                test_item['device'] = updated_data['related_device']
                            updated = True
                            break
                    if updated:
                        break
                        
                elif node_type == 'indicator':
                    for indicator in category.get('indicators', []):
                        if f"ind_{indicator.get('name')}" == node_id:
                            if 'name' in updated_data:
                                indicator['name'] = updated_data['name']
                            if 'description' in updated_data:
                                indicator['description'] = updated_data['description']
                            if 'unit' in updated_data:
                                indicator['unit'] = updated_data['unit']
                            if 'standard_value' in updated_data:
                                indicator['standard_value'] = updated_data['standard_value']
                            if 'related_test_project' in updated_data:
                                indicator['test_item'] = updated_data['related_test_project']
                            updated = True
                            break
                    if updated:
                        break
        
        if not updated:
            return jsonify({
                'success': False,
                'message': f'未找到要更新的节点: {node_id}'
            }), 404
        
        # 保存更新后的数据
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(entities_data, f, ensure_ascii=False, indent=2)
        
        # 同时生成前端表格格式的数据用于刷新
        table_data = convert_to_table_format(entities_data, task_id)
        
        logger.info(f"节点更新成功，任务ID: {task_id}, 节点类型: {node_type}, 节点ID: {node_id}")

        log_user_action(f"分类管理-解析数据编辑-更新节点 task={task_id} type={node_type} id={node_id}")
        return jsonify({
            'success': True,
            'message': '节点更新成功',
            'data': table_data
        })
        
    except Exception as e:
        logger.error(f"更新节点失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'更新失败: {str(e)}'
        }), 500


@flgl_upload_bp.route('/add-node', methods=['POST'])
def add_node():
    """
    添加新节点
    
    请求体格式：
    {
        "task_id": "1234567890",
        "node_type": "equipment",
        "node_data": {
            "name": "新设备",
            "equipment_type": "电气设备",
            "description": "描述"
        }
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'message': '请求体不能为空'
            }), 400
        
        task_id = data.get('task_id')
        node_type = data.get('node_type')
        node_data = data.get('node_data', {})
        
        if not all([task_id, node_type]):
            return jsonify({
                'success': False,
                'message': '缺少必要参数'
            }), 400
        
        # 构建文件路径
        output_dir = os.path.join(UPLOAD_FOLDER, f"parsed_{task_id}")
        json_path = os.path.join(output_dir, "all_entities_edited.json")
        
        if not os.path.exists(json_path):
            original_path = os.path.join(output_dir, "all_entities_combined.json")
            if not os.path.exists(original_path):
                return jsonify({
                    'success': False,
                    'message': '未找到解析数据'
                }), 404
            import shutil
            shutil.copy(original_path, json_path)
        
        # 读取现有数据
        with open(json_path, 'r', encoding='utf-8') as f:
            entities_data = json.load(f)
        
        # 检测数据格式
        is_new_format = 'nodes' in entities_data
        
        # 添加节点
        added = False
        
        if is_new_format:
            # ===== 新格式：nodes/relationships 结构 =====
            nodes = entities_data.get('nodes', {})
            new_type_mapping = {
                'equipment_type': 'EquipmentType',
                'equipment': 'Equipment',
                'environment': 'Environment',
                'test_project': 'TestProject',
                'indicator': 'Indicator',
                'test_method': 'TestMethod',
                'standard': 'standard',
            }
            node_list_key = new_type_mapping.get(node_type)
            if node_list_key and node_list_key in nodes:
                import uuid as _uuid
                new_node = {
                    'Id': node_data.get('id', f"new_{abs(hash(str(_uuid.uuid4()))) % 100000}"),
                    'Name': node_data.get('name', ''),
                    'Label': node_data.get('label', ''),
                    'Description': node_data.get('description', ''),
                    'Type': node_data.get('type', ''),
                    'Status': node_data.get('status', 'active'),
                    'User_id': node_data.get('user_id', 'system'),
                    'Create_time': datetime.now().isoformat(),
                }
                # 补充特定类型的额外字段
                if node_type == 'environment':
                    new_node.update({
                        'Class': node_data.get('class', ''),
                        'Combination': node_data.get('combination', ''),
                        'Limit': node_data.get('limit', ''),
                        'Request': node_data.get('request', ''),
                    })
                elif node_type == 'indicator':
                    new_node['unit'] = node_data.get('unit', '')
                elif node_type == 'test_project':
                    new_node['category'] = node_data.get('category', '')
                
                nodes[node_list_key].append(new_node)
                added = True
        else:
            # ===== 旧格式 =====
            if node_type == 'equipment':
                equipment_type = node_data.get('equipment_type')
                for category in entities_data:
                    if category.get('level1_category') == equipment_type:
                        new_device = {
                            'name': node_data.get('name'),
                            'description': node_data.get('description', '')
                        }
                        category.setdefault('devices', []).append(new_device)
                        added = True
                        break
                        
            elif node_type == 'test_project':
                equipment_name = node_data.get('related_device')
                for category in entities_data:
                    for device in category.get('devices', []):
                        if device.get('name') == equipment_name:
                            new_test_item = {
                                'name': node_data.get('name'),
                                'device': equipment_name,
                                'description': node_data.get('description', '')
                            }
                            category.setdefault('test_items', []).append(new_test_item)
                            added = True
                            break
                    if added:
                        break
                        
            elif node_type == 'indicator':
                test_project_name = node_data.get('related_test_project')
                for category in entities_data:
                    for test_item in category.get('test_items', []):
                        if test_item.get('name') == test_project_name:
                            new_indicator = {
                                'name': node_data.get('name'),
                                'test_item': test_project_name,
                                'unit': node_data.get('unit', ''),
                                'standard_value': node_data.get('standard_value', ''),
                                'description': node_data.get('description', '')
                            }
                            category.setdefault('indicators', []).append(new_indicator)
                            added = True
                            break
                    if added:
                        break
        
        if not added:
            return jsonify({
                'success': False,
                'message': f'无法添加节点，请检查关联关系是否正确'
            }), 400
        
        # 保存更新后的数据
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(entities_data, f, ensure_ascii=False, indent=2)
        
        # 生成前端表格格式的数据
        table_data = convert_to_table_format(entities_data, task_id)
        
        logger.info(f"节点添加成功，任务ID: {task_id}, 节点类型: {node_type}")

        log_user_action(f"分类管理-解析数据编辑-新增节点 task={task_id} type={node_type}")
        return jsonify({
            'success': True,
            'message': '节点添加成功',
            'data': table_data
        })
        
    except Exception as e:
        logger.error(f"添加节点失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'添加失败: {str(e)}'
        }), 500


@flgl_upload_bp.route('/delete-node', methods=['POST'])
def delete_node():
    """
    删除节点
    
    请求体格式：
    {
        "task_id": "1234567890",
        "node_type": "equipment",
        "node_id": "eq_某设备"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'message': '请求体不能为空'
            }), 400
        
        task_id = data.get('task_id')
        node_type = data.get('node_type')
        node_id = data.get('node_id')
        
        if not all([task_id, node_type, node_id]):
            return jsonify({
                'success': False,
                'message': '缺少必要参数'
            }), 400
        
        # 构建文件路径
        output_dir = os.path.join(UPLOAD_FOLDER, f"parsed_{task_id}")
        json_path = os.path.join(output_dir, "all_entities_edited.json")
        
        if not os.path.exists(json_path):
            original_path = os.path.join(output_dir, "all_entities_combined.json")
            if not os.path.exists(original_path):
                return jsonify({
                    'success': False,
                    'message': '未找到解析数据'
                }), 404
            import shutil
            shutil.copy(original_path, json_path)
        
        # 读取现有数据
        with open(json_path, 'r', encoding='utf-8') as f:
            entities_data = json.load(f)
        
        # 检测数据格式
        is_new_format = 'nodes' in entities_data
        
        # 删除节点
        deleted = False
        
        if is_new_format:
            # ===== 新格式：nodes/relationships 结构 =====
            nodes = entities_data.get('nodes', {})
            relationships = entities_data.get('relationships', [])
            new_type_mapping = {
                'equipment_type': 'EquipmentType',
                'equipment': 'Equipment',
                'environment': 'Environment',
                'test_project': 'TestProject',
                'indicator': 'Indicator',
                'test_method': 'TestMethod',
                'standard': 'standard',
            }
            node_list_key = new_type_mapping.get(node_type)
            if node_list_key and node_list_key in nodes:
                node_list = nodes[node_list_key]
                for i, node in enumerate(node_list):
                    if node.get('Id') == node_id:
                        del node_list[i]
                        # 同时删除相关关系
                        entities_data['relationships'] = [
                            r for r in relationships
                            if r.get('from') != node_id and r.get('to') != node_id
                        ]
                        deleted = True
                        break
        else:
            # ===== 旧格式 =====
            if node_type == 'equipment':
                for category in entities_data:
                    devices = category.get('devices', [])
                    for i, device in enumerate(devices):
                        if f"eq_{device.get('name')}" == node_id:
                            device_name = device.get('name')
                            category['test_items'] = [ti for ti in category.get('test_items', []) 
                                                     if ti.get('device') != device_name]
                            category['indicators'] = [ind for ind in category.get('indicators', []) 
                                                     if ind.get('test_item') not in 
                                                     [ti.get('name') for ti in category.get('test_items', [])]]
                            del devices[i]
                            deleted = True
                            break
                    if deleted:
                        break
                        
            elif node_type == 'test_project':
                for category in entities_data:
                    test_items = category.get('test_items', [])
                    for i, test_item in enumerate(test_items):
                        if f"tp_{test_item.get('name')}" == node_id:
                            test_item_name = test_item.get('name')
                            category['indicators'] = [ind for ind in category.get('indicators', []) 
                                                     if ind.get('test_item') != test_item_name]
                            del test_items[i]
                            deleted = True
                            break
                    if deleted:
                        break
                        
            elif node_type == 'indicator':
                for category in entities_data:
                    indicators = category.get('indicators', [])
                    for i, indicator in enumerate(indicators):
                        if f"ind_{indicator.get('name')}" == node_id:
                            del indicators[i]
                            deleted = True
                            break
                    if deleted:
                        break
        
        if not deleted:
            return jsonify({
                'success': False,
                'message': f'未找到要删除的节点: {node_id}'
            }), 404
        
        # 保存更新后的数据
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(entities_data, f, ensure_ascii=False, indent=2)
        
        # 生成前端表格格式的数据
        table_data = convert_to_table_format(entities_data, task_id)
        
        logger.info(f"节点删除成功，任务ID: {task_id}, 节点类型: {node_type}")

        log_user_action(f"分类管理-解析数据编辑-删除节点 task={task_id} type={node_type} id={node_id}")
        return jsonify({
            'success': True,
            'message': '节点删除成功',
            'data': table_data
        })
        
    except Exception as e:
        logger.error(f"删除节点失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'删除失败: {str(e)}'
        }), 500


def convert_to_table_format(entities_data, task_id):
    """将实体数据转换为前端表格格式，兼容新旧两种数据格式"""
    table_data = {
        'task_id': task_id,
        'equipment_types': [],
        'equipments': [],
        'environments': [],
        'test_projects': [],
        'indicators': [],
        'test_methods': [],
        'standards': [],
        'relationships': []
    }
    
    # 检测数据格式
    is_new_format = 'nodes' in entities_data
    
    if is_new_format:
        # ===== 新格式：nodes/relationships 结构 =====
        nodes = entities_data.get('nodes', {})
        
        for node in nodes.get('EquipmentType', []):
            table_data['equipment_types'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', ''),
                'status': node.get('Status', '')
            })
        
        for node in nodes.get('Equipment', []):
            table_data['equipments'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', ''),
                'equipment_type_id': node.get('equipment_type_id', ''),
                'parent_id': node.get('parent_id', '')
            })
        
        for node in nodes.get('Environment', []):
            table_data['environments'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', ''),
                'class': node.get('Class', ''),
                'combination': node.get('Combination', ''),
                'limit': node.get('Limit', ''),
                'request': node.get('Request', '')
            })
        
        for node in nodes.get('TestProject', []):
            table_data['test_projects'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', ''),
                'category': node.get('category', '')
            })
        
        for node in nodes.get('Indicator', []):
            table_data['indicators'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', ''),
                'unit': node.get('unit', '')
            })
        
        for node in nodes.get('TestMethod', []):
            table_data['test_methods'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', '')
            })
        
        for node in nodes.get('standard', []):
            table_data['standards'].append({
                'id': node.get('Id', ''),
                'name': node.get('Name', ''),
                'label': node.get('Label', ''),
                'description': node.get('Description', ''),
                'type': node.get('Type', '')
            })
        
        for rel in entities_data.get('relationships', []):
            table_data['relationships'].append({
                'type': rel.get('type', ''),
                'from_id': rel.get('from', ''),
                'to_id': rel.get('to', '')
            })
    else:
        # ===== 旧格式：level1_category/devices/test_items/indicators 结构 =====
        for category in entities_data:
            level1_category = category.get('level1_category', '')
            
            if level1_category:
                table_data['equipment_types'].append({
                    'id': f"et_{level1_category}",
                    'name': level1_category,
                    'description': f"一级设备类别"
                })
            
            for device in category.get('devices', []):
                device_name = device.get('name', '')
                if device_name:
                    device_id = f"eq_{device_name}"
                    table_data['equipments'].append({
                        'id': device_id,
                        'name': device_name,
                        'equipment_type': level1_category,
                        'description': device.get('description', '')
                    })
                    table_data['relationships'].append({
                        'from_type': 'equipment_type',
                        'from_id': f"et_{level1_category}",
                        'from_name': level1_category,
                        'to_type': 'equipment',
                        'to_id': device_id,
                        'to_name': device_name,
                        'relation': '包含'
                    })
            
            for test_item in category.get('test_items', []):
                item_name = test_item.get('name', '')
                related_device = test_item.get('device', '')
                if item_name:
                    item_id = f"tp_{item_name}"
                    table_data['test_projects'].append({
                        'id': item_id,
                        'name': item_name,
                        'related_device': related_device,
                        'description': test_item.get('description', '')
                    })
                    if related_device:
                        table_data['relationships'].append({
                            'from_type': 'equipment',
                            'from_id': f"eq_{related_device}",
                            'from_name': related_device,
                            'to_type': 'test_project',
                            'to_id': item_id,
                            'to_name': item_name,
                            'relation': '测试项目'
                        })
            
            for indicator in category.get('indicators', []):
                indicator_name = indicator.get('name', '')
                related_test_item = indicator.get('test_item', '')
                if indicator_name:
                    indicator_id = f"ind_{indicator_name}"
                    table_data['indicators'].append({
                        'id': indicator_id,
                        'name': indicator_name,
                        'related_test_project': related_test_item,
                        'unit': indicator.get('unit', ''),
                        'standard_value': indicator.get('standard_value', ''),
                        'description': indicator.get('description', '')
                    })
                    if related_test_item:
                        table_data['relationships'].append({
                            'from_type': 'test_project',
                            'from_id': f"tp_{related_test_item}",
                            'from_name': related_test_item,
                            'to_type': 'indicator',
                            'to_id': indicator_id,
                            'to_name': indicator_name,
                            'relation': '测试指标'
                        })
    
    return table_data


# 修改原有的 upload_word_file 接口，返回 task_id
# 将原来的 upload_word_file 替换为以下版本
# 注意：如果你已经有了 upload_word_file，请将其替换或注释掉原来的版本

#==================== 上传至neo4j图数据库===============================
@flgl_upload_bp.route('/import-to-neo4j/<task_id>', methods=['POST'])
def import_to_neo4j(task_id):
    """
    将解析数据导入 Neo4j。无请求体时先做名称重复检测，有冲突则只返回冲突列表；无冲突则直接导入。
    请求体带 resolutions 时按「替换/跳过/重命名」处理后再导入（替换会先 DETACH DELETE 图库中已有同名旧节点及关系）。
    """
    try:
        from neo4j import GraphDatabase
        from .neo4j_import_conflicts import (
            apply_name_conflict_resolutions,
            collect_all_conflicts,
            detach_delete_nodes,
            run_flgl_neo4j_import,
            validate_resolutions_against_index,
        )

        neo4j_uri = current_app.config.get('NEO4J_URI', os.getenv('NEO4j_URI', 'bolt://localhost:7687'))
        neo4j_user = current_app.config.get('NEO4J_USER', os.getenv('NEO4j_USER', 'neo4j'))
        neo4j_password = current_app.config.get('NEO4J_PASSWORD', os.getenv('NEO4j_PASSWORD', 'password'))

        output_dir = os.path.join(UPLOAD_FOLDER, f"parsed_{task_id}")
        edited_path = os.path.join(output_dir, "all_entities_edited.json")
        if os.path.exists(edited_path):
            json_path = edited_path
        else:
            json_path = os.path.join(output_dir, "all_entities_combined.json")

        if not os.path.exists(json_path):
            return jsonify({'success': False, 'message': '未找到解析数据'}), 404

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        body = request.get_json(silent=True) or {}
        has_resolutions_key = 'resolutions' in body
        resolution_list = body.get('resolutions') if has_resolutions_key else None
        if resolution_list is None and has_resolutions_key:
            resolution_list = []

        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        with driver.session() as session:
            # 已按需求注释：导入前数据库名称查重/冲突检测
            # database_conflicts, in_file_conflicts, conflict_index = (
            #     collect_all_conflicts(session, data)
            # )
            # has_name_conflicts = bool(database_conflicts or in_file_conflicts)
            has_name_conflicts = False

            if not has_resolutions_key:
                # 已按需求注释：导入时不再因名称冲突中断并返回冲突列表
                # if has_name_conflicts:
                #     driver.close()
                #     log_user_action(
                #         f"分类管理-Neo4j导入(待处理冲突) task={task_id}",
                #         result="pending",
                #     )
                #     return jsonify(
                #         {
                #             'success': True,
                #             'name_conflicts': True,
                #             'message': '存在节点名称重复，请在前端选择替换、跳过或重命名后再次提交',
                #             'data': {
                #                 'database_conflicts': database_conflicts,
                #                 'in_file_conflicts': in_file_conflicts,
                #             },
                #         }
                #     )
                stats = run_flgl_neo4j_import(session, data)
            else:
                # 已按需求注释：导入时不再使用冲突解析分支
                # if has_name_conflicts:
                #     validate_resolutions_against_index(resolution_list, conflict_index)
                #     work, _s, to_delete = apply_name_conflict_resolutions(
                #         data, resolution_list, conflict_index
                #     )
                #     detach_delete_nodes(session, to_delete)
                #     stats = run_flgl_neo4j_import(session, work)
                # else:
                #     stats = run_flgl_neo4j_import(session, data)
                stats = run_flgl_neo4j_import(session, data)

        driver.close()

        logger.info(
            '导入成功: %s 个节点, %s 个关系',
            stats.get('nodes_created', 0),
            stats.get('relationships_created', 0),
        )
        log_user_action(
            f"分类管理-Neo4j导入成功 task={task_id} nodes={stats.get('nodes_created', 0)} rels={stats.get('relationships_created', 0)}"
        )
        return jsonify(
            {
                'success': True,
                'name_conflicts': False,
                'message': '导入成功',
                'data': {
                    'nodes_created': stats['nodes_created'],
                    'relationships_created': stats['relationships_created'],
                },
            }
        )
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except ImportError as e:
        logger.error(f"Neo4j驱动未安装: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Neo4j驱动未安装，请运行: pip install neo4j'
        }), 500
    except Exception as e:
        logger.error(f"导入Neo4j失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'导入失败: {str(e)}'
        }), 500