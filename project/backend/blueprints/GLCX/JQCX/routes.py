"""
精确查询(JQCX) API — Neo4j 图数据库查询接口
实际数据库结构 (neo4j.txt):
  EquipmentCategory → HAS → EquipmentType → HAS → Equipment
  Equipment → CON → TestProject
  Equipment → CON → Indicator
  Environment → CON → TestProject
  TestProject → CON → TestMethod
  Indicator → CON → TestMethod
  TestMethod → CON → File
"""
from flask import Blueprint, request, jsonify, current_app, Response
import json
import os
import re
import logging
from datetime import timedelta
from urllib.parse import quote, unquote
from utils.neo4j_flgl_status import flgl_status_cypher_param, FLGL_GRAPH_LABELS
from ..equipment_query_shared import (
    build_indicator_nodes_for_equipment,
    build_project_nodes_for_equipment,
)

logger = logging.getLogger(__name__)

jqcx_bp = Blueprint('jqcx', __name__, url_prefix='/api/GLCX/JQCX')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, 'schema.json')
STANDARD_MAPPING_PATH = os.path.join(PROJECT_ROOT, 'standard', 'standard_mapping.json')

def _load_schema():
    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_standard_mapping():
    try:
        with open(STANDARD_MAPPING_PATH, 'r', encoding='utf-8') as f:
            return json.load(f).get('mappings', {})
    except Exception:
        return {}


def _get_source_name(source_id):
    mapping = _load_standard_mapping()
    if source_id in mapping:
        return mapping[source_id].get('source_name', source_id)
    return source_id


def _run_cypher(cypher, params=None):
    driver = current_app.config.get('NEO4J_DRIVER')
    if not driver:
        raise RuntimeError("Neo4j driver not available")
    with driver.session() as session:
        result = session.run(cypher, params or {})
        records = []
        for record in result:
            row = {}
            for key in record.keys():
                value = record[key]
                if hasattr(value, 'items'):
                    row[key] = dict(value.items())
                    row[key]['_labels'] = list(value.labels)
                elif hasattr(value, '__iter__') and not isinstance(value, str):
                    row[key] = list(value)
                else:
                    row[key] = value
            records.append(row)
        return records


def _serialize_value(v):
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    return v


def _get_node_props(node_data):
    if not node_data:
        return {}
    result = {}
    for k, v in node_data.items():
        if k.startswith('_'):
            continue
        result[k.lower()] = _serialize_value(v)
    return result


@jqcx_bp.route('/projects', methods=['GET'])
def get_projects():
    try:
        equipment_ids = request.args.get('equipment_id', '')
        if not equipment_ids:
            return jsonify({"success": False, "error": "equipment_id required"}), 400

        eq_id_list = [e.strip() for e in equipment_ids.split(',') if e.strip()]
        if not eq_id_list:
            return jsonify({"success": False, "error": "equipment_id required"}), 400

        nodes = build_project_nodes_for_equipment(
            eq_id_list, run_cypher=_run_cypher, get_node_props=_get_node_props
        )
        return jsonify({"success": True, "data": nodes})
    except Exception as e:
        logger.error(f"项目查询出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/indicators', methods=['GET'])
def get_indicators():
    try:
        equipment_ids = request.args.get('equipment_id', '')
        if not equipment_ids:
            return jsonify({"success": False, "error": "equipment_id required"}), 400

        eq_id_list = [e.strip() for e in equipment_ids.split(',') if e.strip()]
        if not eq_id_list:
            return jsonify({"success": False, "error": "equipment_id required"}), 400

        all_indicators = build_indicator_nodes_for_equipment(
            eq_id_list, run_cypher=_run_cypher, get_node_props=_get_node_props
        )
        return jsonify({"success": True, "data": all_indicators})
    except Exception as e:
        logger.error(f"指标查询出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/db-info', methods=['GET'])
def get_db_info():
    try:
        labels = _run_cypher("CALL db.labels()")
        rel_types = _run_cypher("CALL db.relationshipTypes()")
        samples = {}
        stp = flgl_status_cypher_param()
        for row in labels:
            label = row.get('label', '')
            if label in FLGL_GRAPH_LABELS:
                count = _run_cypher(
                    f"MATCH (n:{label}) WHERE n.Status IN $flgl_enabled_status RETURN count(n) AS cnt", {**stp}
                )
            else:
                count = _run_cypher(f"MATCH (n:{label}) RETURN count(n) AS cnt")
            cnt = count[0]['cnt'] if count else 0
            if cnt > 0:
                if label in FLGL_GRAPH_LABELS:
                    sample = _run_cypher(
                        f"MATCH (n:{label}) WHERE n.Status IN $flgl_enabled_status RETURN n LIMIT 1", {**stp}
                    )
                else:
                    sample = _run_cypher(f"MATCH (n:{label}) RETURN n LIMIT 1")
                samples[label] = {
                    'count': cnt,
                    'sample': _get_node_props(sample[0].get('n', {})) if sample else None
                }
        rel_samples = {}
        for row in rel_types:
            rel = row.get('relationshipType', '')
            count = _run_cypher(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS cnt")
            cnt = count[0]['cnt'] if count else 0
            if cnt > 0:
                sample = _run_cypher(f"MATCH (a)-[r:{rel}]->(b) RETURN labels(a) AS fromLabels, labels(b) AS toLabels, properties(r) AS relProps LIMIT 1")
                rel_samples[rel] = {'count': cnt, 'sample': sample[0] if sample else None}
        return jsonify({"success": True, "labels": samples, "relationships": rel_samples})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/schema', methods=['GET'])
def get_schema():
    try:
        schema = _load_schema()
        return jsonify({"success": True, "data": schema})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/query', methods=['POST'])
def query_nodes():
    try:
        data = request.json or {}
        node_type = data.get('nodeType')
        keyword = data.get('keyword', '')
        filters = data.get('filters', {})
        if not node_type:
            return jsonify({"success": False, "error": "nodeType is required"}), 400
        st = flgl_status_cypher_param()
        if node_type in FLGL_GRAPH_LABELS:
            if keyword:
                cypher = f"""
                    MATCH (n:{node_type})
                    WHERE n.Name CONTAINS $keyword AND n.Status IN $flgl_enabled_status
                    RETURN n ORDER BY n.Name
                """
                records = _run_cypher(cypher, {**st, 'keyword': keyword})
            elif filters:
                conditions = ' AND '.join([f"n.{k} = ${k}" for k in filters.keys()])
                cypher = f"""
                    MATCH (n:{node_type})
                    WHERE {conditions} AND n.Status IN $flgl_enabled_status
                    RETURN n ORDER BY n.Name
                """
                records = _run_cypher(cypher, {**dict(filters), **st})
            else:
                cypher = f"""
                    MATCH (n:{node_type})
                    WHERE n.Status IN $flgl_enabled_status
                    RETURN n ORDER BY n.Name
                """
                records = _run_cypher(cypher, {**st})
        elif keyword:
            cypher = f"MATCH (n:{node_type}) WHERE n.Name CONTAINS $keyword RETURN n ORDER BY n.Name"
            records = _run_cypher(cypher, {'keyword': keyword})
        elif filters:
            conditions = ' AND '.join([f"n.{k} = ${k}" for k in filters.keys()])
            cypher = f"MATCH (n:{node_type}) WHERE {conditions} RETURN n ORDER BY n.Name"
            records = _run_cypher(cypher, filters)
        else:
            cypher = f"MATCH (n:{node_type}) RETURN n ORDER BY n.Name"
            records = _run_cypher(cypher)
        nodes = [_get_node_props(record.get('n', {})) for record in records]
        return jsonify({"success": True, "data": nodes, "total": len(nodes)})
    except Exception as e:
        logger.error(f"查询出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/equipment-tree', methods=['GET'])
def get_equipment_tree():
    """
    三级设备树:
    Level 1: EquipmentType 的 Type 属性 (航空装备, 地面装备, 电子装备)
    Level 2: EquipmentType 节点
    Level 3: Equipment 节点 (具体设备)
    """
    try:
        cypher = """
        MATCH (et:EquipmentType)-[:HAS]->(e:Equipment)
        WHERE (et).Status IN $flgl_enabled_status AND (e).Status IN $flgl_enabled_status
        RETURN et, e
        ORDER BY et.Type, et.Name, e.Name
        """
        records = _run_cypher(cypher, {**flgl_status_cypher_param()})

        tree = {}
        for record in records:
            et_data = _get_node_props(record.get('et', {}))
            e_data = _get_node_props(record.get('e', {}))
            et_type = et_data.get('type', '未分类')
            et_id = et_data.get('id', '')

            if et_type not in tree:
                tree[et_type] = {'label': et_type, 'types': {}}
            if et_id not in tree[et_type]['types']:
                tree[et_type]['types'][et_id] = {'type': et_data, 'equipment': []}
            tree[et_type]['types'][et_id]['equipment'].append(e_data)

        result = []
        for et_type, info in tree.items():
            types_list = []
            for et_id, et_info in info['types'].items():
                types_list.append({
                    'title': et_info['type'].get('name', et_id),
                    'key': f'et_{et_id}',
                    'children': [
                        {'title': eq.get('name', eq.get('id', '')), 'key': f'eq_{eq.get("id", "")}', 'isLeaf': True}
                        for eq in et_info['equipment']
                    ],
                })
            result.append({
                'title': info['label'],
                'key': f'type_{et_type}',
                'children': types_list,
            })

        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"设备树查询出错: {e}", exc_info=True)
        return jsonify({"success": True, "data": []})


@jqcx_bp.route('/methods', methods=['POST'])
def get_methods():
    """
    根据选中的项目和指标查询关联方法 (TestMethod)，按关系 rate 相关度排序。
    关联文件为图谱 File：(File)-[:BELONGS_TO]->(TestMethod)，或 File.Id = m.Source_id；
    标准页使用 File.Id，由 /api/standard 解析 minIo_id 加载 PDF 与 MinIO 摘要。
    """
    try:
        from ..methods_shared import build_methods_by_project_and_indicators

        data = request.json or {}
        project_ids = data.get('projectIds', [])
        indicator_ids = data.get('indicatorIds', [])
        nodes = build_methods_by_project_and_indicators(
            project_ids,
            indicator_ids,
            run_cypher=_run_cypher,
            get_node_props=_get_node_props,
            get_source_name=_get_source_name,
        )
        return jsonify({"success": True, "data": nodes, "total": len(nodes)})
    except Exception as e:
        logger.error(f"方法查询出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/file-abstract', methods=['POST'])
def get_file_abstract():
    """
    获取文件摘要：优先从 MinIO 元数据读取，为空则用 LLM 提取
    """
    from config import Config
    data = request.json or {}
    file_id = data.get('file_id', '')
    minio_path = data.get('minio_path', '')

    if not file_id and not minio_path:
        return jsonify({"success": False, "error": "file_id or minio_path required"}), 400

    abstract = ""

    if Config.MINIO_CLIENT and minio_path:
        try:
            stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, minio_path)
            if stat.metadata:
                meta = {}
                for k, v in stat.metadata.items():
                    meta[k.lower()] = v
                abstract = meta.get('x-amz-meta-abstract', '')
        except Exception as e:
            logger.warning(f"MinIO metadata read failed: {e}")

    if not abstract and Config.OPENAI_CLIENT and minio_path:
        try:
            import tempfile
            tmp_path = os.path.join(Config.OUTPUT_DIR, 'tmp_abstract', f'{file_id}.tmp')
            os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
            Config.MINIO_CLIENT.fget_object(Config.MINIO_BUCKET_NAME, minio_path, tmp_path)

            text_sample = ""
            ext = os.path.splitext(minio_path)[1].lower()
            if ext == '.pdf':
                try:
                    import fitz
                    doc = fitz.open(tmp_path)
                    text_parts = []
                    for page in doc:
                        text_parts.append(page.get_text())
                        if len("".join(text_parts)) > 3000:
                            break
                    doc.close()
                    text_sample = "".join(text_parts)[:3000]
                except Exception:
                    pass
            elif ext in ('.docx', '.doc'):
                try:
                    import docx
                    doc = docx.Document(tmp_path)
                    text_sample = "\n".join(p.text for p in doc.paragraphs)[:3000]
                except Exception:
                    pass

            if text_sample.strip():
                resp = Config.OPENAI_CLIENT.chat.completions.create(
                    model=Config.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": "请从以下文档文本中提取摘要(200字以内)。只返回摘要文本，不要其他内容。"},
                        {"role": "user", "content": text_sample}
                    ],
                    temperature=0.1,
                    max_tokens=500,
                )
                abstract = (resp.choices[0].message.content or "").strip()

            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception as e:
            logger.warning(f"LLM abstract extraction failed: {e}")

    return jsonify({"success": True, "abstract": abstract})


@jqcx_bp.route('/environments-legacy', methods=['GET'])
def get_environments_legacy():
    try:
        cypher = """
        MATCH (env:Environment)
        WHERE (env).Status IN $flgl_enabled_status
        RETURN env ORDER BY env.Name
        """
        records = _run_cypher(cypher, {**flgl_status_cypher_param()})
        nodes = [_get_node_props(r.get('env', {})) for r in records]
        return jsonify({"success": True, "data": nodes})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/environment-detail/<env_id>', methods=['GET'])
def get_environment_detail(env_id):
    try:
        cypher = """
        MATCH (env:Environment)
        WHERE (env.Id = $id OR env.Name = $name) AND (env).Status IN $flgl_enabled_status
        RETURN env
        """
        records = _run_cypher(cypher, {**flgl_status_cypher_param(), "id": env_id, "name": env_id})
        if records:
            node = _get_node_props(records[0].get('env', {}))
            return jsonify({"success": True, "data": node})
        return jsonify({"success": False, "error": "环境节点不存在"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/projects-by-env', methods=['POST'])
def get_projects_by_env():
    try:
        data = request.get_json()
        device_ids = data.get('deviceIds', [])
        env_ids = data.get('envIds', [])

        if not device_ids:
            return jsonify({"success": False, "error": "deviceIds不能为空"}), 400

        all_projects = []
        seen = set()
        for eq_id in device_ids:
            if env_ids:
                for env_id in env_ids:
                    cypher = """
                    MATCH (eq:Equipment)-[:CON]->(proj:TestProject)
                    WHERE eq.Id = $eq_id
                      AND (eq).Status IN $flgl_enabled_status
                      AND (proj).Status IN $flgl_enabled_status
                    OPTIONAL MATCH (proj)-[:CON]-(env:Environment)
                    WHERE (env) IS NULL
                       OR ((env.Name = $env_id OR env.Id = $env_id) AND (env).Status IN $flgl_enabled_status)
                    RETURN DISTINCT proj
                    """
                    records = _run_cypher(
                        cypher, {**flgl_status_cypher_param(), "eq_id": eq_id, "env_id": env_id}
                    )
                    for r in records:
                        proj = r.get('proj')
                        if proj:
                            pid = proj.get('Id', proj.get('Name', str(proj)))
                            if pid not in seen:
                                seen.add(pid)
                                all_projects.append(_get_node_props(proj))
            else:
                cypher = """
                MATCH (eq:Equipment)-[:CON]->(proj:TestProject)
                WHERE eq.Id = $eq_id
                  AND (eq).Status IN $flgl_enabled_status
                  AND (proj).Status IN $flgl_enabled_status
                RETURN proj
                """
                records = _run_cypher(cypher, {**flgl_status_cypher_param(), "eq_id": eq_id})
                for r in records:
                    proj = r.get('proj')
                    if proj:
                        pid = proj.get('Id', proj.get('Name', str(proj)))
                        if pid not in seen:
                            seen.add(pid)
                            all_projects.append(_get_node_props(proj))

        return jsonify({"success": True, "data": all_projects})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/indicators-by-env', methods=['POST'])
def get_indicators_by_env():
    try:
        data = request.get_json()
        device_ids = data.get('deviceIds', [])
        env_ids = data.get('envIds', [])

        if not device_ids:
            return jsonify({"success": False, "error": "deviceIds不能为空"}), 400

        all_indicators = []
        seen = set()
        for eq_id in device_ids:
            if env_ids:
                for env_id in env_ids:
                    cypher = """
                    MATCH (eq:Equipment)-[:CON]->(ind:Indicator)
                    WHERE eq.Id = $eq_id
                      AND (eq).Status IN $flgl_enabled_status
                      AND (ind).Status IN $flgl_enabled_status
                    OPTIONAL MATCH (ind)-[:CON]-(env:Environment)
                    WHERE (env) IS NULL
                       OR ((env.Name = $env_id OR env.Id = $env_id) AND (env).Status IN $flgl_enabled_status)
                    RETURN DISTINCT ind
                    """
                    records = _run_cypher(
                        cypher, {**flgl_status_cypher_param(), "eq_id": eq_id, "env_id": env_id}
                    )
                    for r in records:
                        ind = r.get('ind')
                        if ind:
                            iid = ind.get('Id', ind.get('Name', str(ind)))
                            if iid not in seen:
                                seen.add(iid)
                                all_indicators.append(_get_node_props(ind))
            else:
                cypher = """
                MATCH (eq:Equipment)-[:CON]->(ind:Indicator)
                WHERE eq.Id = $eq_id
                  AND (eq).Status IN $flgl_enabled_status
                  AND (ind).Status IN $flgl_enabled_status
                RETURN ind
                """
                records = _run_cypher(cypher, {**flgl_status_cypher_param(), "eq_id": eq_id})
                for r in records:
                    ind = r.get('ind')
                    if ind:
                        iid = ind.get('Id', ind.get('Name', str(ind)))
                        if iid not in seen:
                            seen.add(iid)
                            all_indicators.append(_get_node_props(ind))

        return jsonify({"success": True, "data": all_indicators})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


_MINIO_PATH_RE = re.compile(r'<img\s+[^>]*src="([^"]*)"[^>]*/?>', re.IGNORECASE)
_MINIO_PATH_EXTRACT_RE = re.compile(r'/faxm/([^?"]+)')
_PRESIGNED_HOST_RE = re.compile(r'https?://[^/]+/faxm/')


def _refresh_presigned_urls(html_content: str) -> str:
    """扫描 HTML 中的 <img> 标签，将过期的 MinIO presigned URL 替换为 /minio-image 代理地址。"""
    if not html_content or '<img' not in html_content:
        return html_content

    def _replace_img(match):
        full_tag = match.group(0)
        old_src = match.group(1)

        # 已经是代理地址则跳过
        if '/minio-image?' in old_src:
            return full_tag

        # 从 URL 中提取 MinIO 对象路径
        obj_path = None
        path_match = _MINIO_PATH_EXTRACT_RE.search(old_src)
        if path_match:
            obj_path = path_match.group(1)

        if not obj_path:
            return full_tag

        # 用代理地址（永不过期），路径需要 URL 编码
        encoded_path = quote(obj_path, safe='')
        proxy_url = f"/api/GLCX/JQCX/minio-image?path={encoded_path}"
        return full_tag.replace(old_src, proxy_url)

    return _MINIO_PATH_RE.sub(_replace_img, html_content)


@jqcx_bp.route('/minio-image', methods=['GET'])
def get_minio_image():
    """代理 MinIO 图片，避免 presigned URL 过期问题。"""
    raw_path = request.args.get('path', '').strip()
    obj_path = unquote(raw_path)
    logger.info("MinIO 图片请求: raw_path=%s, decoded=%s", raw_path, obj_path)
    if not obj_path:
        return jsonify({"error": "path required"}), 400

    minio_client = current_app.config.get('MINIO_CLIENT')
    bucket_name = current_app.config.get('MINIO_BUCKET_NAME', 'faxm')
    if not minio_client:
        logger.warning("MinIO 客户端不可用")
        return jsonify({"error": "MinIO不可用"}), 500

    try:
        logger.info("尝试从 MinIO 获取: bucket=%s, path=%s", bucket_name, obj_path)
        response = minio_client.get_object(bucket_name, obj_path)
        data = response.read()
        logger.info("MinIO 获取成功: path=%s, size=%d", obj_path, len(data))
        content_type = response.headers.get('Content-Type') or response.headers.get('content-type') or 'application/octet-stream'
        if not content_type.startswith('image/'):
            import mimetypes
            guessed, _ = mimetypes.guess_type(obj_path)
            if guessed and guessed.startswith('image/'):
                content_type = guessed
            else:
                content_type = 'image/png'
        return Response(data, content_type=content_type)
    except Exception as e:
        logger.warning("MinIO 图片获取失败: path=%s, error=%s", obj_path, e, exc_info=True)
        return jsonify({"error": "图片不存在", "detail": str(e)}), 404


@jqcx_bp.route('/environment-settings-all', methods=['GET'])
def get_all_environment_settings():
    """返回全部启用环境的五要素，供前端一次性缓存。"""
    driver = current_app.config.get('NEO4J_DRIVER')
    if not driver:
        return jsonify({"success": False, "error": "Neo4j驱动不可用"}), 500
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (env:Environment)
                WHERE (env).Status IN $flgl_enabled_status
                RETURN env.Name AS name,
                       env.Type AS type,
                       env.Class AS class,
                       env.Limit AS limit,
                       env.Request AS request,
                       env.Combination AS combination
                ORDER BY env.Id
                """,
                flgl_status_cypher_param(),
            )
            settings_list = []
            for record in result:
                env_name = _serialize_value(record.get("name")) or ""
                if not env_name:
                    continue
                settings_list.append({
                    "env_name": env_name,
                    "type": _refresh_presigned_urls(_serialize_value(record.get("type")) or ""),
                    "class": _refresh_presigned_urls(_serialize_value(record.get("class")) or ""),
                    "limit": _refresh_presigned_urls(_serialize_value(record.get("limit")) or ""),
                    "request": _refresh_presigned_urls(_serialize_value(record.get("request")) or ""),
                    "combination": _refresh_presigned_urls(_serialize_value(record.get("combination")) or ""),
                })
        return jsonify({"success": True, "data": settings_list})
    except Exception as e:
        logger.error("获取全部环境五要素失败: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/environments', methods=['GET'])
def get_environments():
    try:
        query = """
        MATCH (n:Environment)
        WHERE n.Id STARTS WITH 'HJ_' AND (n).Status IN $flgl_enabled_status
        RETURN n.Id AS id, n.Name AS name
        ORDER BY n.Id
        """
        
        driver = current_app.config.get('NEO4J_DRIVER')
        if not driver:
            return jsonify({"success": False, "error": "Neo4j驱动不可用"}), 500
        
        with driver.session() as session:
            result = session.run(query, {**flgl_status_cypher_param()})
            records = list(result)
            
        environments = [dict(record) for record in records]
        
        return jsonify({
            "success": True, 
            "data": environments,
            "message": "获取环境列表成功"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
