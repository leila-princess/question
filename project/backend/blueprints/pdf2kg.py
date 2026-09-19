# blueprint_kg.py
import threading
import uuid
import tempfile
import os
import json
import re
from datetime import datetime
from neo4j import GraphDatabase

from config import Config
from agent.data4neo4j.config import PDFExtractionConfig
# 全局进度存储
progress_store = {}
progress_lock = threading.Lock()
TEMP_DIR = os.path.join(tempfile.gettempdir(), 'kg_extractions')
os.makedirs(TEMP_DIR, exist_ok=True)

from flask import Blueprint, request, jsonify, current_app, send_file

# 导入处理类（延迟导入，避免模块加载时执行）
from agent.data4neo4j.save_integration import Neo4jIntegration

# 创建蓝图
pdf2kg_bp = Blueprint('pdf2kg', __name__, url_prefix='/api/pdf2kg')

# Neo4j集成实例
integrator = Neo4jIntegration()

# 临时文件存储目录（从配置读取或默认）
TEMP_DIR = os.path.join(tempfile.gettempdir(), 'kg_extractions')
os.makedirs(TEMP_DIR, exist_ok=True)

def update_progress(task_id, info):
    """回调函数，更新进度字典"""
    with progress_lock:
        if task_id not in progress_store:
            return
        prog = progress_store[task_id]
        prog['progress'] = info
        # 如果阶段是最后完成，标记状态
        if info.get('stage') == 'validation' and info.get('percent') == 100:
            prog['status'] = 'completed'

@pdf2kg_bp.route('/extract', methods=['POST'])
def extract_pdf():
    if 'file' not in request.files:
        return jsonify({'error': '缺少文件字段'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': '未选择文件'}), 400
    
    # 支持的文件格式
    filename_lower = file.filename.lower() if file.filename else ''
    is_pdf = filename_lower.endswith('.pdf')
    is_docx = filename_lower.endswith(('.docx', '.doc'))
    
    if not is_pdf and not is_docx:
        return jsonify({'error': '只支持PDF、Word(.docx/.doc)文件'}), 400

    # 保存临时文件
    original_filename = file.filename
    
    # 解析配置参数（从表单或JSON body）
    config_dict = {}
    if request.form:
        for key in ['gibberish_min_length', 'gibberish_acceptable_ratio', 'pdf_image_dpi', 'llm_max_tokens']:
            if key in request.form:
                try:
                    config_dict[key] = float(request.form[key]) if '.' in request.form[key] else int(request.form[key])
                except:
                    pass
        for key in ['enable_ocr', 'force_ocr_all_pages', 'save_text', 'save_tables', 'save_figures']:
            if key in request.form:
                config_dict[key] = request.form[key].lower() in ('true', '1', 'yes')
        if 'llm_model' in request.form:
            config_dict['llm_model'] = request.form['llm_model']
        if 'llm_temperature' in request.form:
            try:
                config_dict['llm_temperature'] = float(request.form['llm_temperature'])
            except:
                pass
        if 'output_subdir' in request.form:
            config_dict['output_subdir'] = request.form['output_subdir']
    
    extraction_config = PDFExtractionConfig.from_dict(config_dict)
    
    try:
        if is_pdf:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                file.save(tmp.name)
                file_path = tmp.name
        else:
            suffix = '.docx' if filename_lower.endswith('.docx') else '.doc'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                file.save(tmp.name)
                file_path = tmp.name
    except Exception as e:
        return jsonify({'error': f'文件保存失败: {str(e)}'}), 500

    task_id = str(uuid.uuid4())
    with progress_lock:
        progress_store[task_id] = {
            'status': 'processing',
            'progress': None,
            'result_path': None,
            'error': None,
            'config': extraction_config.to_dict()
        }

    def process_task():
        try:
            if is_pdf:
                from backend.agent.data4neo4j.pdf_agent import EnhancedPDFToKnowledgeGraphAgent
                agent = EnhancedPDFToKnowledgeGraphAgent(extraction_config=extraction_config)
                
                def callback(info):
                    update_progress(task_id, info)
                
                result = agent.process_pdf(
                    file_path, 
                    enable_confirmation=False, 
                    progress_callback=callback,
                    config=extraction_config,
                    original_filename=original_filename
                )
            else:
                from backend.agent.data4neo4j.word_agent import WordToKnowledgeGraphAgent
                agent = WordToKnowledgeGraphAgent()
                
                def callback(info):
                    update_progress(task_id, info)
                
                result = agent.process_word(
                    file_path,
                    original_filename,
                    enable_confirmation=False,
                    progress_callback=callback
                )

            if 'error' in result:
                with progress_lock:
                    progress_store[task_id]['status'] = 'failed'
                    progress_store[task_id]['error'] = result['error']
                return

            result_path = os.path.join(TEMP_DIR, f'{task_id}.json')
            with open(result_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            with progress_lock:
                progress_store[task_id]['result_path'] = result_path
                progress_store[task_id]['status'] = 'completed'

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            print(f"处理任务失败: {error_msg}")
            with progress_lock:
                progress_store[task_id]['status'] = 'failed'
                progress_store[task_id]['error'] = str(e)
        finally:
            if os.path.exists(file_path):
                os.unlink(file_path)

    thread = threading.Thread(target=process_task)
    thread.daemon = True
    thread.start()

    return jsonify({'task_id': task_id, 'message': '任务已提交', 'config': extraction_config.to_dict()})

@pdf2kg_bp.route('/progress/<task_id>', methods=['GET'])
def get_progress(task_id):
    with progress_lock:
        prog = progress_store.get(task_id)
        if not prog:
            return jsonify({'error': '任务不存在'}), 404
        return jsonify({
            'status': prog['status'],
            'progress': prog['progress'],
            'error': prog['error']
        })

@pdf2kg_bp.route('/result/<task_id>', methods=['GET'])
def get_result(task_id):
    with progress_lock:
        prog = progress_store.get(task_id)
        if not prog:
            return jsonify({'error': '任务不存在'}), 404
        if prog['status'] != 'completed':
            return jsonify({'error': '任务尚未完成'}), 400
        result_path = prog['result_path']
    try:
        with open(result_path, 'r', encoding='utf-8') as f:
            result = json.load(f)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'读取结果失败: {str(e)}'}), 500


@pdf2kg_bp.route('/confirm', methods=['POST'])
def confirm_extraction():
    """
    接收确认请求，将之前提取的结果导入Neo4j。
    请求体JSON应包含：{'session_id': 'xxx'}
    """
    data = request.get_json()
    if not data or 'session_id' not in data:
        return jsonify({'error': '缺少session_id'}), 400

    session_id = data['session_id']
    result_path = os.path.join(TEMP_DIR, f'{session_id}.json')
    if not os.path.exists(result_path):
        return jsonify({'error': '会话不存在或已过期'}), 404

    # 读取保存的提取结果
    try:
        with open(result_path, 'r', encoding='utf-8') as f:
            extraction = json.load(f)
    except Exception as e:
        return jsonify({'error': f'读取结果失败: {str(e)}'}), 500

    # 可选：添加确认标记
    extraction['confirmed'] = True
    extraction['confirmed_at'] = datetime.now().isoformat()  # 需要导入datetime

    # 导入Neo4j
    try:
        # 调用导入方法，第二个参数控制是否清空数据库（可设为False或由前端决定）
        success = integrator.import_to_neo4j(extraction, confirm_before_import=False, ask_clear_db=False)
        if not success:
            return jsonify({'error': '导入失败（具体错误请查看日志）'}), 500
    except Exception as e:
        return jsonify({'error': f'导入过程出错: {str(e)}'}), 500
    finally:
        # 删除临时文件（可选，可根据业务决定是否保留）
        if os.path.exists(result_path):
            os.unlink(result_path)

    return jsonify({'status': 'ok', 'message': '导入成功'})


# 可选：添加健康检查端点
@pdf2kg_bp.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


@pdf2kg_bp.route('/save', methods=['POST'])
def save_extraction():
    """
    接收前端修改后的知识图谱数据，直接导入Neo4j。
    请求体JSON格式：{ "nodes": [...], "relationships": [...] }
    """
    data = request.get_json()
    if not data or 'nodes' not in data or 'relationships' not in data:
        return jsonify({'error': '缺少 nodes 或 relationships 字段'}), 400

    # 可选：进行基本验证，如节点ID唯一性等
    nodes = data['nodes']
    relationships = data['relationships']
    node_ids = set()
    for node in nodes:
        node_id = node.get('id')
        if not node_id:
            return jsonify({'error': '节点缺少 id 字段'}), 400
        if node_id in node_ids:
            return jsonify({'error': f'重复的节点ID: {node_id}'}), 400
        node_ids.add(node_id)

    for rel in relationships:
        if not rel.get('id') or not rel.get('startNodeId') or not rel.get('endNodeId') or not rel.get('type'):
            return jsonify({'error': '关系缺少必要字段 (id, startNodeId, endNodeId, type)'}), 400
        if rel['startNodeId'] not in node_ids or rel['endNodeId'] not in node_ids:
            return jsonify({'error': f'关系 {rel["id"]} 引用了不存在的节点'}), 400

    # 将数据包装成与之前 extraction 结构一致，以便复用导入函数
    extraction = {
        'nodes': nodes,
        'relationships': relationships,
        'metadata': {
            'pdf_name': '用户修改后的数据',
            'processed_at': datetime.now().isoformat()
        },
        'confirmed': True,
        'confirmed_at': datetime.now().isoformat()
    }

    try:
        # 调用导入方法（注意：import_to_neo4j 内部可能还需要序列化属性）
        success = integrator.import_to_neo4j(extraction, confirm_before_import=False, ask_clear_db=False)
        if not success:
            return jsonify({'error': '导入失败，请查看日志'}), 500
    except Exception as e:
        return jsonify({'error': f'导入过程出错: {str(e)}'}), 500

    return jsonify({'status': 'ok', 'message': '数据已成功导入图数据库'})

@pdf2kg_bp.route('/images/<path:filename>', methods=['GET'])
def get_image(filename):
    """
    提供提取的图片文件
    参数 filename: 例如 "12_img1.png" 或 "subdir/12_img1.png"
    """
    # 图片存储的根目录（从配置中读取）
    # image_dir = os.path.join(PROCESSING_CONFIG.output_dir, 'extracted_figures')
    image_dir = "C:\\Users\\hyx\\antd-demo1\\outputs\\extracted_figures"
    
    # 安全处理：防止路径穿越攻击
    safe_path = os.path.normpath(os.path.join(image_dir, filename))
    if not safe_path.startswith(image_dir):
        return 'Invalid path', 400
    
    if not os.path.exists(safe_path):
        return 'File not found', 404
    
    return send_file(safe_path)


@pdf2kg_bp.route('/clear', methods=['POST'])
def clear_graph():
    """
    清空整个图数据库（删除所有节点和关系）
    注意：此操作不可逆，建议在生产环境中添加身份验证/授权
    """
    try:
        with driver.session() as session:
            # 执行清空操作（Cypher语句）
            result = session.run("MATCH (n) DETACH DELETE n")
            # 可选：返回被删除的节点数量
            summary = result.consume()
            count = summary.counters.nodes_deleted
        return jsonify({
            "success": True,
            "message": f"图数据库已清空，共删除 {count} 个节点",
            "deleted_nodes": count
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@pdf2kg_bp.route('/config', methods=['GET'])
def get_config():
    """获取 PDF 提取的默认配置参数"""
    default_config = PDFExtractionConfig.get_default_config()
    return jsonify({
        'status': 'ok',
        'config': default_config
    })


@pdf2kg_bp.route('/ocr-extract', methods=['POST'])
def ocr_extract():
    """
    纯OCR版PDF提取 — 不依赖pdfplumber文本提取
    使用PaddleOCR + PPStructureV3进行版面分析
    """
    if 'file' not in request.files:
        return jsonify({'error': '缺少文件字段'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    filename_lower = file.filename.lower() if file.filename else ''
    if not filename_lower.endswith('.pdf'):
        return jsonify({'error': '只支持PDF文件'}), 400

    original_filename = file.filename

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            file.save(tmp.name)
            file_path = tmp.name
    except Exception as e:
        return jsonify({'error': f'文件保存失败: {str(e)}'}), 500

    task_id = str(uuid.uuid4())
    with progress_lock:
        progress_store[task_id] = {
            'status': 'processing',
            'progress': None,
            'result_path': None,
            'error': None,
        }

    def process_ocr_task():
        try:
            from backend.agent.data4neo4j.pdf_ocr_extractor import PDFOCRExtractor, OCRExtractorConfig, build_kg_extraction

            config = OCRExtractorConfig(
                pdf_image_dpi=300,
                ocr_use_gpu=False,
                output_dir=os.path.join(tempfile.gettempdir(), 'ocr_outputs'),
            )
            extractor = PDFOCRExtractor(config)
            ocr_result = extractor.extract(file_path, original_filename=original_filename)
            kg_result = build_kg_extraction(ocr_result)

            result_path = os.path.join(TEMP_DIR, f'{task_id}.json')
            with open(result_path, 'w', encoding='utf-8') as f:
                json.dump(kg_result, f, ensure_ascii=False, indent=2)

            with progress_lock:
                progress_store[task_id]['result_path'] = result_path
                progress_store[task_id]['status'] = 'completed'

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            print(f"OCR提取任务失败: {error_msg}")
            with progress_lock:
                progress_store[task_id]['status'] = 'failed'
                progress_store[task_id]['error'] = str(e)
        finally:
            if os.path.exists(file_path):
                os.unlink(file_path)

    thread = threading.Thread(target=process_ocr_task)
    thread.daemon = True
    thread.start()

    return jsonify({'task_id': task_id, 'message': 'OCR提取任务已提交'})


@pdf2kg_bp.route('/extract-metadata', methods=['POST'])
def extract_metadata():
    """
    从PDF提取元数据（标准号、名称、发布日期等）
    使用PaddleNLP UIE模型，降级为正则
    """
    if 'file' not in request.files:
        return jsonify({'error': '缺少文件字段'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    filename_lower = file.filename.lower() if file.filename else ''
    if not filename_lower.endswith('.pdf'):
        return jsonify({'error': '只支持PDF文件'}), 400

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            file.save(tmp.name)
            file_path = tmp.name
    except Exception as e:
        return jsonify({'error': f'文件保存失败: {str(e)}'}), 500

    try:
        from backend.agent.data4neo4j.pdf_ocr_extractor import PDFOCRExtractor, OCRExtractorConfig
        from backend.agent.data4neo4j.metadata_extractor import MetadataExtractor

        # 步骤1: 仅提取第一页OCR文本
        config = OCRExtractorConfig(
            pdf_image_dpi=200,
            ocr_use_gpu=False,
            output_dir=os.path.join(tempfile.gettempdir(), 'meta_ocr'),
        )
        extractor = PDFOCRExtractor(config)
        ocr_result = extractor.extract(file_path, original_filename=file.filename)

        # 只取第一页文本
        first_page_text = ""
        if ocr_result.get("pages"):
            first_page_text = ocr_result["pages"][0].get("text", "")
        full_text = ocr_result.get("full_text", "")

        # 步骤2: UIE元数据提取
        meta_extractor = MetadataExtractor()
        metadata = meta_extractor.extract(first_page_text)

        # 如果UIE没提取到足够信息，尝试全文
        if not metadata.get("standard_number") and len(full_text) > len(first_page_text):
            full_metadata = meta_extractor.extract(full_text[:5000])
            for key in metadata:
                if key != "raw_results" and not metadata[key]:
                    metadata[key] = full_metadata.get(key, "")

        return jsonify({
            'status': 'ok',
            'metadata': metadata,
            'pdf_name': file.filename,
        })

    except Exception as e:
        import traceback
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        print(f"元数据提取失败: {error_msg}")
        return jsonify({'error': f'元数据提取失败: {str(e)}'}), 500
    finally:
        if os.path.exists(file_path):
            os.unlink(file_path)