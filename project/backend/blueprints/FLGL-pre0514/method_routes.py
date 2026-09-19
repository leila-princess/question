"""
方法提取 HTTP API — 包装洪实 GetTestMethod pipeline 为 REST 接口

接口:
  POST /api/FLGL/method-extract   上传标准文件，执行抽取+召回，返回方法列表+推荐关联节点
  POST /api/FLGL/method-import    将方法数据导入 Neo4j
"""
import os
import sys
import json
import tempfile
import logging
import threading

from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from datetime import datetime

from utils.operation_log import log_user_action

from .method_block_markdown import text_block_to_markdown

logger = logging.getLogger(__name__)


def _flgl_cfg_int(main_config, env_key: str, default: int) -> int:
    """优先环境变量，其次主工程 Config 类属性；避免 GetTestMethod 覆盖 sys.modules 后 MainConfig 指向存根而缺字段。"""
    ev = os.environ.get(env_key)
    if ev is not None and str(ev).strip() != "":
        try:
            return int(ev)
        except ValueError:
            pass
    v = getattr(main_config, env_key, None)
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    return default


# 与 Neo4j 一致：仅允许三字符串；导入边方向 (Target)-[rel]->(TestMethod)
_METHOD_IMPORT_REL_TYPES = frozenset({'CON', 'HAS', 'BELONG_TO'})


def _rows_from_graph_matches(
    graph_matches: dict,
    test_project_limit: int = 3,
    indicator_limit: int = 3,
) -> list:
    """从召回 dict 中仅取 TestProject、Indicator 各前 N 条，供方法导入表格默认行。"""
    if not isinstance(graph_matches, dict):
        graph_matches = {}
    rows: list = []
    for item in (graph_matches.get('test_project') or [])[:test_project_limit]:
        it = item or {}
        raw_id = it.get('id') or it.get('Id')
        tid = str(raw_id).strip() if raw_id is not None else ''
        if not tid:
            continue
        rows.append({
            'target_type': 'TestProject',
            'target_id': tid,
            'node_name': (it.get('name') or ''),
            'rel_type': 'CON',
            'score': it.get('score'),
        })
    for item in (graph_matches.get('indicator') or [])[:indicator_limit]:
        it = item or {}
        raw_id = it.get('id') or it.get('Id')
        tid = str(raw_id).strip() if raw_id is not None else ''
        if not tid:
            continue
        rows.append({
            'target_type': 'Indicator',
            'target_id': tid,
            'node_name': (it.get('name') or ''),
            'rel_type': 'CON',
            'score': it.get('score'),
        })
    return rows


def _parse_rel_score_for_import(rel: dict) -> tuple:
    """
    未带 score 键或为空：合法，不 SET 分数。
    带了 score 但不可解析为 float：非法，整行跳过 (返回 (None, False)）。
    """
    if 'score' not in rel:
        return None, True
    v = rel.get('score')
    if v is None or v == '':
        return None, True
    if isinstance(v, str) and not v.strip():
        return None, True
    try:
        return float(v), True
    except (TypeError, ValueError):
        return None, False


def _testmethod_props_by_name(name: str):
    """按「名称」在库中查 TestMethod，有则返回 _get_node_props 结果，否则 None。"""
    n = (name or '').strip()
    if not n:
        return None
    from .routes import _run_cypher, _get_node_props
    recs = _run_cypher(
        "MATCH (n:TestMethod) WHERE n.Name = $name RETURN n LIMIT 1",
        {'name': n},
    )
    if not recs:
        return None
    return _get_node_props(recs[0].get('n', {}))


flgl_method_bp = Blueprint('flgl_method', __name__, url_prefix='/api/FLGL_method')

# 任务状态存储（简单内存版，生产环境可换 Redis）
_TASK_STATUS = {}
_TASK_LOCK = threading.Lock()


def _set_task(task_id, **kwargs):
    with _TASK_LOCK:
        _TASK_STATUS[task_id] = kwargs


def _get_task(task_id):
    with _TASK_LOCK:
        return _TASK_STATUS.get(task_id)


@flgl_method_bp.route('/method-extract', methods=['POST'])
def method_extract():
    """上传标准文件，调用洪实 pipeline 提取方法 + 图谱召回"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "没有文件"}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    source_file_id = request.form.get('source_file_id', 'WJ_std0001')
    user_id = request.form.get('user_id', 'admin')

    # 保存到临时目录
    tmp_dir = tempfile.mkdtemp()
    filename = secure_filename(file.filename)
    filepath = os.path.join(tmp_dir, filename)
    file.save(filepath)

    _saved_backend_config = None
    try:
        # 必须在注入 GetTestMethod 的 backend.config 之前 import，否则下方会
        # sys.modules['backend.config'] = 子包，再 import Config 得到的是子模块的 Config（无 FLGL_TESTMETHOD_*）
        from backend.config import Config as MainConfig

        # 延迟导入，避免启动时加载 pipeline 依赖
        import numpy as np
        if not hasattr(np, 'sctypes'):
            np.sctypes = {
                'int': [np.int8, np.int16, np.int32, np.int64],
                'uint': [np.uint8, np.uint16, np.uint32, np.uint64],
                'float': [np.float16, np.float32, np.float64],
                'complex': [np.complex64, np.complex128],
                'others': [np.bool_, np.object_, np.str_, np.bytes_],
            }
        # 与仓库一致：包在 GetTestMethod/ 下，无已废弃的 GetTestMethod/testmethod/ 目录
        _flgl_dir = os.path.dirname(os.path.abspath(__file__))
        _gtm_root = os.path.join(_flgl_dir, 'GetTestMethod')
        if _flgl_dir not in sys.path:
            sys.path.insert(0, _flgl_dir)
        if _gtm_root not in sys.path:
            sys.path.insert(0, _gtm_root)

        # 给 pipeline 的独立 backend.config 注入主项目已初始化好的运行时对象
        import importlib.util
        method_backend_config_path = os.path.join(_gtm_root, 'backend', 'config.py')
        if not os.path.isfile(method_backend_config_path):
            return jsonify({
                "success": False,
                "error": f"未找到方法抽取配置: {method_backend_config_path}",
            }), 500
        spec = importlib.util.spec_from_file_location('backend.config', method_backend_config_path)
        method_backend_config = importlib.util.module_from_spec(spec)
        _saved_backend_config = sys.modules.get('backend.config')
        sys.modules['backend.config'] = method_backend_config
        assert spec and spec.loader
        spec.loader.exec_module(method_backend_config)

        MethodConfig = method_backend_config.Config
        MethodConfig.OPENAI_CLIENT = current_app.config.get('OPENAI_CLIENT')
        MethodConfig.OPENAI_MODEL = (
            getattr(MainConfig, "FLGL_TESTMETHOD_OPENAI_MODEL", None)
            or current_app.config.get('OPENAI_MODEL', getattr(MethodConfig, 'OPENAI_MODEL', None))
        )
        MethodConfig.LLM_MAX_COMPLETION_TOKENS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_LLM_MAX_COMPLETION_TOKENS", 3072
        )
        MethodConfig.LLM_CONTEXT_TOKENS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_LLM_CONTEXT_TOKENS", 8192
        )
        MethodConfig.DISCOVERY_MAX_PROMPT_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DISCOVERY_MAX_PROMPT_CHARS", 11000
        )
        MethodConfig.DISCOVERY_CATALOGUE_MAX_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DISCOVERY_CATALOGUE_MAX_CHARS", 5000
        )
        MethodConfig.DISCOVERY_PREVIEW_MAX_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DISCOVERY_PREVIEW_MAX_CHARS", 6000
        )
        MethodConfig.DETAIL_MAX_PROMPT_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DETAIL_MAX_PROMPT_CHARS", 10000
        )
        MethodConfig.DETAIL_SECTION_MAX_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DETAIL_SECTION_MAX_CHARS", 2000
        )
        MethodConfig.DETAIL_CHUNK_BUDGET_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DETAIL_CHUNK_BUDGET_CHARS", 8000
        )
        MethodConfig.DETAIL_TABLE_HTML_MAX_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DETAIL_TABLE_HTML_MAX_CHARS", 1200
        )
        MethodConfig.DETAIL_MAX_TABLES = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DETAIL_MAX_TABLES", 40
        )
        MethodConfig.NEO4J_DRIVER = (
            current_app.config.get('FLGL_TESTMETHOD_NEO4J_DRIVER')
            or current_app.config.get('NEO4J_DRIVER')
        )
        MethodConfig.CHROMA_CLIENT = current_app.config.get('CHROMA_CLIENT')
        MethodConfig.CHROMA_COLLECTION_OBJ = current_app.config.get('CHROMA_COLLECTION_OBJ')
        MethodConfig.MINIO_CLIENT = current_app.config.get('MINIO_CLIENT')
        _bge = (getattr(MainConfig, 'FLGL_TESTMETHOD_BGE_MODEL_PATH', None) or '').strip()
        MethodConfig.BGE_MODEL_PATH = os.path.abspath(_bge) if _bge else None
        MethodConfig.BGE_LOCAL_FILES_ONLY = bool(getattr(MainConfig, 'FLGL_TESTMETHOD_BGE_LOCAL_FILES_ONLY', True))
        # 新请求使用当前 .env 中的 BGE 路径，避免单例缓存旧模型
        MethodConfig.TESTMETHOD_RETRIEVER = None
        if MethodConfig.BGE_MODEL_PATH:
            logger.info(
                "方法提取 BGE: %s, local_files_only=%s, Neo4j=%s",
                MethodConfig.BGE_MODEL_PATH,
                MethodConfig.BGE_LOCAL_FILES_ONLY,
                "FLGL 专用" if current_app.config.get("FLGL_TESTMETHOD_NEO4J_DRIVER") else "主库",
            )

        from GetTestMethod.pipeline import run_extract_only, run_recall_only

        # 与 pdf_ocr_extractor 一致：持久化 OCR 在 backend/output_json/ocr_tmp/ocr_{stem}/extracted_text.txt
        # 若已存在该文件则跳过 fitz+版面 OCR，直接走后续 LLM 抽取
        _f = os.path.abspath(__file__)
        _backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(_f)))
        _method_ocr_tmp = os.path.join(_backend_dir, "output_json", "ocr_tmp")

        # 第一步：抽取
        extract_result = run_extract_only(
            file_path=filepath,
            source_file_id=source_file_id,
            user_id=user_id,
            output_root=os.path.join(tmp_dir, 'output'),
            ocr_output_dir=_method_ocr_tmp,
        )

        checkpoint_path = extract_result.get('extraction_checkpoint_path')
        extraction_data = extract_result.get('extraction_data', {})
        methods = extraction_data.get('methods', [])

        # 第二步：召回（BGE + Neo4j；TopK 见主 Config 中 FLGL_TESTMETHOD_RECALL_TOPK_*）
        recall_result = None
        try:
            recall_result = run_recall_only(
                extraction_checkpoint_path=checkpoint_path,
                source_file_id=source_file_id,
                user_id=user_id,
                topk_config={
                    'equipment': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_EQUIPMENT', 0),
                    'environment': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_ENVIRONMENT', 0),
                    'test_project': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_TEST_PROJECT', 3),
                    'indicator': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_INDICATOR', 3),
                },
                method_id_prefix=(
                    (os.environ.get('FLGL_TESTMETHOD_ID_PREFIX') or '').strip()
                    or getattr(MainConfig, 'FLGL_TESTMETHOD_ID_PREFIX', None)
                    or 'FF'
                ),
                save_method_json=False,
            )
        except Exception as e:
            logger.warning(f"图谱召回失败（不影响抽取结果）: {e}")

        # 构造返回数据
        def _format_terms(terms):
            lines = []
            for item in terms or []:
                if isinstance(item, str):
                    s = item.strip()
                    if s:
                        lines.append(s)
                elif isinstance(item, dict):
                    term = str(item.get('term') or item.get('name') or item.get('text') or item.get('title') or '').strip()
                    definition = str(item.get('definition') or item.get('desc') or item.get('description') or '').strip()
                    if term and definition:
                        lines.append(f"{term}：{definition}")
                    elif term:
                        lines.append(term)
                    elif definition:
                        lines.append(definition)
                else:
                    s = str(item).strip()
                    if s:
                        lines.append(s)
            return '\n'.join(lines)

        if methods:
            first_method = methods[0]
            method_name = first_method.get('name', '')
            source_name = extraction_data.get('source_file', {}).get('file_name', filename)

            # 尝试从文档全文中提取标准全称（如"民用轻小型无人机系统环境试验方法 第7部分：湿热试验"）
            full_text = extraction_data.get('full_text', '') or ''
            import re
            # 匹配常见标准标题格式
            title_match = re.search(
                r'(民用轻小型无人机系统[^\n]{0,50}(?:第\s*\d+\s*部分[：:]\s*\S+))',
                full_text
            )
            if title_match:
                method_name = title_match.group(1).strip()
            elif method_name and source_name:
                # 如果 LLM 提取的名称不含标准编号，拼上文件名前缀
                base_name = os.path.splitext(source_name)[0]
                if base_name and base_name not in method_name:
                    method_name = f"{base_name} {method_name}"

            enriched_from_recall: list = []
            if recall_result and isinstance(recall_result.get('result'), dict):
                enriched_from_recall = recall_result.get('result', {}).get('methods') or []

            formatted_methods = []
            for index, method in enumerate(methods):
                current_name = method.get('name', '')
                if index == 0 and method_name:
                    current_name = method_name
                graph_matches: dict = {}
                if index < len(enriched_from_recall) and isinstance(enriched_from_recall[index], dict):
                    gm = enriched_from_recall[index].get('graph_matches')
                    if isinstance(gm, dict):
                        graph_matches = gm
                rec_rows = _rows_from_graph_matches(
                    graph_matches,
                    _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_TEST_PROJECT', 3),
                    _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_INDICATOR', 3),
                )
                existing_by_name = _testmethod_props_by_name(current_name)
                formatted_methods.append({
                    'name': current_name,
                    'type': method.get('type', ''),
                    'label': ', '.join(str(x) for x in method.get('keywords', []) if x),
                    'description': method.get('description', '') or method.get('summary', ''),
                    'sy': _format_terms(extraction_data.get('terms', [])),
                    'sytj': text_block_to_markdown(method.get('sytj')),
                    'sygc': text_block_to_markdown(method.get('sygc')),
                    'jgpd': text_block_to_markdown(method.get('jgpd')),
                    'source_name': extraction_data.get('source_file', {}).get('file_name', filename),
                    'recommended_relations': rec_rows,
                    'name_duplicate': {
                        'duplicate': existing_by_name is not None,
                        'existing': existing_by_name,
                    },
                })

            result_data = {
                'name': method_name,
                'type': first_method.get('type', ''),
                'label': ', '.join(str(x) for x in first_method.get('keywords', []) if x),
                'description': first_method.get('description', '') or first_method.get('summary', ''),
                'sy': _format_terms(extraction_data.get('terms', [])),
                'sytj': text_block_to_markdown(first_method.get('sytj')),
                'sygc': text_block_to_markdown(first_method.get('sygc')),
                'jgpd': text_block_to_markdown(first_method.get('jgpd')),
                'source_name': extraction_data.get('source_file', {}).get('file_name', filename),
                'methods_count': len(methods),
                'methods': formatted_methods,
                'all_methods': [
                    {
                        'name': m.get('name', ''),
                        'type': m.get('type', ''),
                        'summary': m.get('description', '') or m.get('summary', ''),
                    }
                    for m in methods
                ],
                'recommended_nodes': [],
            }
        else:
            result_data = {
                'name': '',
                'type': '',
                'label': '',
                'description': '未提取到方法',
                'sy': '',
                'sytj': '',
                'sygc': '',
                'jgpd': '',
                'source_name': filename,
                'methods_count': 0,
                'all_methods': [],
                'methods': [],
                'recommended_nodes': [],
            }

        log_user_action(f"分类管理-检测方法抽取 file={filename[:120]} methods={result_data.get('methods_count', 0)}")
        return jsonify({"success": True, "data": result_data})

    except Exception as e:
        logger.error(f"方法提取失败: {e}")
        return jsonify({"success": False, "error": f"提取失败: {str(e)}"}), 500
    finally:
        if _saved_backend_config is not None:
            sys.modules['backend.config'] = _saved_backend_config
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@flgl_method_bp.route('/method-name-duplicate', methods=['GET'])
def method_name_duplicate_check():
    """按名称检查 TestMethod 是否与库中重复，供重命名后二次校验。"""
    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({
            'success': True,
            'data': {'duplicate': False, 'existing': None},
        })
    try:
        ex = _testmethod_props_by_name(name)
        return jsonify({
            'success': True,
            'data': {'duplicate': ex is not None, 'existing': ex},
        })
    except Exception as e:
        logger.error(f"方法重名检查失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@flgl_method_bp.route('/method-import', methods=['POST'])
def method_import():
    """将方法数据导入 Neo4j（创建 TestMethod 节点 + 关联关系）"""
    data = request.json or {}
    method_data = data.get('method', {})
    relations = data.get('relations', [])

    if not method_data.get('Name'):
        return jsonify({"success": False, "error": "方法名称不能为空"}), 400

    try:
        from .routes import _gen_id, _run_cypher, _now, _get_node_props, NODE_FIELDS

        relation_details = data.get('relation_details', [])
        fields = NODE_FIELDS['TestMethod']
        replace_mode = (data.get('import_mode') or '').strip().lower() == 'replace'
        replace_id = (data.get('replace_node_id') or '').strip()
        source_name = method_data.get('Source_name', '')
        method_name = method_data.get('Name', '')

        if replace_mode and replace_id:
            if not _run_cypher(
                'MATCH (n:TestMethod {Id: $id}) RETURN n LIMIT 1',
                {'id': replace_id},
            ):
                return jsonify({'success': False, 'error': '要替换的方法节点不存在'}), 404
            node_id = replace_id
            upd = {}
            for f in fields:
                if f in ('Id', 'Create_time'):
                    continue
                if f in method_data and method_data[f] is not None:
                    upd[f] = method_data[f]
            upd['Relation_details'] = json.dumps(relation_details, ensure_ascii=False)
            set_parts = [f'n.{k} = ${k}' for k in upd.keys()]
            cypher = f'MATCH (n:TestMethod {{Id: $node_id}}) SET {", ".join(set_parts)} RETURN n'
            params = {'node_id': node_id, **upd}
            records = _run_cypher(cypher, params)
            node = _get_node_props(records[0].get('n', {})) if records else None
        else:
            # 重复校验：优先按 Source_name，其次按 Name（新建时）
            duplicate_records = _run_cypher(
                """
                MATCH (n:TestMethod)
                WHERE ($source_name <> '' AND n.Source_name = $source_name)
                   OR ($method_name <> '' AND n.Name = $method_name)
                RETURN n LIMIT 1
                """,
                {'source_name': source_name, 'method_name': method_name},
            )
            if duplicate_records:
                existing = _get_node_props(duplicate_records[0].get('n', {}))
                return jsonify({
                    'success': False,
                    'error': '该方法已存在，请勿重复保存',
                    'data': {'duplicate': True, 'existing': existing},
                }), 409

            node_id = _gen_id('TestMethod')
            props = {'Id': node_id}
            for f in fields:
                if f in ('Id', 'Create_time'):
                    continue
                if f in method_data and method_data[f] is not None:
                    props[f] = method_data[f]
            props['Relation_details'] = json.dumps(relation_details, ensure_ascii=False)
            props['Create_time'] = _now()
            props['Status'] = props.get('Status', 'active')

            set_str = ', '.join([f'{k}: ${k}' for k in props.keys()])
            cypher = f"CREATE (n:TestMethod {{{set_str}}}) RETURN n"
            records = _run_cypher(cypher, props)
            node = _get_node_props(records[0].get('n', {})) if records else None

        # (Target {Id: target_id})-[CON|HAS|BELONG_TO]->(TestMethod {Id: node_id})
        created_rels = 0
        for rel in relations or []:
            if not isinstance(rel, dict):
                continue
            raw_tid = rel.get('target_id')
            target_id = str(raw_tid).strip() if raw_tid is not None else ''
            if not target_id:
                continue
            rel_type = (rel.get('rel_type') or '').strip()
            if rel_type not in _METHOD_IMPORT_REL_TYPES:
                continue
            score, score_ok = _parse_rel_score_for_import(rel)
            if not score_ok:
                continue
            merge_tail = {
                'CON': 'MERGE (a)-[r:CON]->(b)',
                'HAS': 'MERGE (a)-[r:HAS]->(b)',
                'BELONG_TO': 'MERGE (a)-[r:BELONG_TO]->(b)',
            }[rel_type]
            cypher = (
                'MATCH (a {Id: $from_id}) '
                'MATCH (b:TestMethod {Id: $to_id}) ' + merge_tail
            )
            if score is not None:
                cypher += ' SET r.score = $score'
            cypher += ' RETURN 1 AS _ok'
            params = {
                'from_id': target_id,
                'to_id': node_id,
            }
            if score is not None:
                params['score'] = score
            recs = _run_cypher(cypher, params)
            if recs:
                created_rels += 1

        log_user_action(
            f"分类管理-检测方法导入Neo4j id={node_id} name={method_name[:80]} rels={created_rels} replace={replace_mode}"
        )
        return jsonify({
            "success": True,
            "data": {
                "node": node,
                "relations_created": created_rels,
            }
        })

    except Exception as e:
        logger.error(f"方法导入失败: {e}")
        return jsonify({"success": False, "error": f"导入失败: {str(e)}"}), 500


# 与前端 @/constants/environments 中「短名」对齐，用于环境类排序（名称可能为「复杂电磁环境」等）
_ENV_ORDER_TOKENS = [
    '电磁', '地理', '气象', '水文', '水声', '高原', '海上', '其他',
]


def _env_sort_key(name: str) -> tuple:
    n = (name or '').strip()
    for i, tok in enumerate(_ENV_ORDER_TOKENS):
        if tok in n:
            return (0, i, n)
    return (1, 99, n)


def get_method_category_tree_response():
    """
    从 Neo4j 自动构建方法 Tab 左侧分类树数据：
    - 环境类：由「项目 -CON-> 方法」链上项目所关联的环境（Environment -CON-> TestProject 或 EnvironmentId）去重得到；
    - 指标类：任有关联「指标 -CON-> 方法」的方法归入「指标」；
    - 未分类：既无项目连接也无指标连接的方法。
    返回 categories（树节点）与 method_category_keys（方法 id -> 所属分类 key 列表）。
    """
    from .routes import _run_cypher

    try:
        # 方法 <- 项目 -> 环境（与 seed：Environment -CON-> TestProject，TestProject -CON-> TestMethod 一致）
        env_rows = _run_cypher(
            """
            MATCH (p:TestProject)-[:CON]->(m:TestMethod)
            OPTIONAL MATCH (e1:Environment)-[:CON]->(p)
            OPTIONAL MATCH (e2:Environment)
            WHERE p.EnvironmentId IS NOT NULL AND e2.Id = p.EnvironmentId
            WITH m, coalesce(e1, e2) AS e
            WHERE e IS NOT NULL
            RETURN DISTINCT m.Id AS mid, e.Id AS eid, e.Name AS ename
            """,
            {},
        )

        ind_rows = _run_cypher(
            """
            MATCH (i:Indicator)-[:CON]->(m:TestMethod)
            RETURN DISTINCT m.Id AS mid
            """,
            {},
        )

        all_m_rows = _run_cypher(
            "MATCH (m:TestMethod) RETURN m.Id AS mid",
            {},
        )

        method_keys: dict[str, list[str]] = {}
        env_meta: dict[str, str] = {}  # eid -> 展示名称

        for r in env_rows or []:
            mid = r.get('mid')
            eid = r.get('eid')
            ename = (r.get('ename') or '') or (eid or '')
            if not mid or not eid:
                continue
            key = f"env_{eid}"
            method_keys.setdefault(mid, []).append(key)
            env_meta[eid] = ename

        ind_set = {r.get('mid') for r in (ind_rows or []) if r.get('mid')}
        for mid in ind_set:
            method_keys.setdefault(mid, []).append("mcat_indicator")

        all_ids = {r.get('mid') for r in (all_m_rows or []) if r.get('mid')}
        for mid in all_ids:
            if mid not in method_keys or not method_keys[mid]:
                method_keys.setdefault(mid, []).append("mcat_unassigned")
            # 去重保序
            method_keys[mid] = list(dict.fromkeys(method_keys.get(mid, [])))

        # 各分类下方法数
        def count_for(k: str) -> int:
            return sum(1 for _mid, ks in method_keys.items() if k in ks)

        categories = []
        for eid, title in sorted(env_meta.items(), key=lambda x: _env_sort_key(x[1])):
            k = f"env_{eid}"
            c = count_for(k)
            categories.append({
                "key": k,
                "title": title,
                "type": "environment",
                "env_id": eid,
                "count": c,
            })

        categories.append({
            "key": "mcat_indicator",
            "title": "指标",
            "type": "indicator",
            "count": count_for("mcat_indicator"),
        })
        u_cnt = count_for("mcat_unassigned")
        if u_cnt > 0:
            categories.append({
                "key": "mcat_unassigned",
                "title": "未分类",
                "type": "unassigned",
                "count": u_cnt,
            })

        return jsonify({
            "success": True,
            "data": {
                "categories": categories,
                "method_category_keys": method_keys,
            },
        })
    except Exception as e:
        logger.error(f"方法分类树查询失败: {e}")
        return jsonify({"success": False, "error": f"查询失败: {str(e)}"}), 500


@flgl_method_bp.route('/method-categories', methods=['GET'])
def flgl_method_categories():
    """同 routes.method_categories，便于 /api/FLGL_method/ 下直接访问"""
    return get_method_category_tree_response()
