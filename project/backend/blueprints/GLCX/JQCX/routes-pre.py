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
from flask import Blueprint, request, jsonify, current_app
import json
import os
import logging
from utils.neo4j_flgl_status import flgl_status_cypher_param, FLGL_GRAPH_LABELS

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


@jqcx_bp.route('/projects', methods=['GET'])
def get_projects():
    try:
        equipment_ids = request.args.get('equipment_id', '')
        if not equipment_ids:
            return jsonify({"success": False, "error": "equipment_id required"}), 400

        eq_id_list = [e.strip() for e in equipment_ids.split(',') if e.strip()]
        if not eq_id_list:
            return jsonify({"success": False, "error": "equipment_id required"}), 400

        cypher = """
            MATCH (eq:Equipment)-[:CON]->(seed:TestProject)
            WHERE eq.Id IN $eqIds
              AND eq.Status IN $flgl_enabled_status
              AND seed.Status IN $flgl_enabled_status
              AND seed.Type IN ['test', 'category']

            // 若 seed 是父级，补齐其子级
            OPTIONAL MATCH (seed)-[:HAS]->(seed_child:TestProject)
            WHERE seed_child.Status IN $flgl_enabled_status
              AND seed_child.Type IN ['test', 'category']

            // 若 seed 是子级，补齐其父级
            OPTIONAL MATCH (seed_parent:TestProject)-[:HAS]->(seed)
            WHERE seed_parent.Type = 'category'
              AND seed_parent.Status IN $flgl_enabled_status

            WITH DISTINCT seed,
                 collect(DISTINCT seed_child) AS seed_children,
                 collect(DISTINCT seed_parent) AS seed_parents

            UNWIND ([seed] + seed_children + seed_parents) AS proj
            WITH DISTINCT proj
            WHERE proj IS NOT NULL

            OPTIONAL MATCH (parent:TestProject)-[:HAS]->(proj)
            WHERE parent.Type = 'category'
              AND parent.Status IN $flgl_enabled_status

            OPTIONAL MATCH (env:Environment)-[:CON]->(proj)
            WHERE env.Status IN $flgl_enabled_status

            RETURN DISTINCT proj, parent, collect(DISTINCT env.Name) AS env_names
            ORDER BY proj.Name
        """
        records = _run_cypher(cypher, {**flgl_status_cypher_param(), 'eqIds': eq_id_list})

        categories = {}
        for record in records:
            project_data = _get_node_props(record.get('proj') or {})
            parent_data = _get_node_props(record.get('parent') or {})
            env_names = [e for e in (record.get('env_names') or []) if e]

            if not project_data.get('id') and not project_data.get('name'):
                continue

            project_type = str(project_data.get('type', '')).lower()
            project_id = project_data.get('id') or project_data.get('name')

            if project_type == 'category':
                bucket_id = project_id
                if bucket_id not in categories:
                    categories[bucket_id] = {
                        'category': project_data,
                        'children': [],
                        'child_ids': set(),
                        'env_names': set()
                    }
                categories[bucket_id]['env_names'].update(env_names)
                continue

            if parent_data and parent_data.get('id'):
                bucket_id = parent_data.get('id')
                if bucket_id not in categories:
                    categories[bucket_id] = {
                        'category': parent_data,
                        'children': [],
                        'child_ids': set(),
                        'env_names': set()
                    }
                child_data = dict(project_data)
                child_data['_env'] = '、'.join(sorted(set(env_names))) if env_names else ''
                if project_id not in categories[bucket_id]['child_ids']:
                    categories[bucket_id]['children'].append(child_data)
                    categories[bucket_id]['child_ids'].add(project_id)
                categories[bucket_id]['env_names'].update(env_names)
                continue

            # 没有父级的 test 项目也要返回，避免漏项
            bucket_id = f'__single__{project_id}'
            if bucket_id not in categories:
                categories[bucket_id] = {
                    'category': {
                        'id': project_id,
                        'name': project_data.get('name', project_id or '未命名项目'),
                        'type': 'category',
                    },
                    'children': [],
                    'child_ids': set(),
                    'env_names': set()
                }
            child_data = dict(project_data)
            child_data['_env'] = '、'.join(sorted(set(env_names))) if env_names else ''
            if project_id not in categories[bucket_id]['child_ids']:
                categories[bucket_id]['children'].append(child_data)
                categories[bucket_id]['child_ids'].add(project_id)
            categories[bucket_id]['env_names'].update(env_names)

        nodes = []
        for _, cat_info in categories.items():
            cat_node = dict(cat_info['category'])
            cat_node['children'] = cat_info['children']
            if cat_info['env_names']:
                cat_node['_env'] = '、'.join(sorted(cat_info['env_names']))
            nodes.append(cat_node)

        nodes.sort(key=lambda x: (str(x.get('name') or ''), str(x.get('id') or '')))

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

        all_indicators = []
        indicator_ids = set()

        for eq_id in eq_id_list:
            # 尝试多种关系类型: CON, CON
            for rel_type in ['CON', 'CON', 'BELONGS_TO']:
                cypher = f"""
                MATCH (eq:Equipment {{Id: $eqId}})-[:{rel_type}]->(i:Indicator)
                WHERE (eq).Status IN $flgl_enabled_status AND (i).Status IN $flgl_enabled_status
                RETURN DISTINCT i ORDER BY i.Name
                """
                records = _run_cypher(cypher, {**flgl_status_cypher_param(), 'eqId': eq_id})
                for r in records:
                    ind = _get_node_props(r.get('i', {}))
                    ind_id = ind.get('id', '')
                    if ind_id and ind_id not in indicator_ids:
                        indicator_ids.add(ind_id)
                        all_indicators.append(ind)
                if all_indicators:
                    break
            # 也尝试从TestProjectCON查询
            if not all_indicators:
                cypher = """
                MATCH (eq:Equipment {Id: $eqId})-[:CON]->(p:TestProject)-[:CON]->(i:Indicator)
                WHERE (eq).Status IN $flgl_enabled_status
                  AND (p).Status IN $flgl_enabled_status
                  AND (i).Status IN $flgl_enabled_status
                RETURN DISTINCT i ORDER BY i.Name
                """
                records = _run_cypher(cypher, {**flgl_status_cypher_param(), 'eqId': eq_id})
                for r in records:
                    ind = _get_node_props(r.get('i', {}))
                    ind_id = ind.get('id', '')
                    if ind_id and ind_id not in indicator_ids:
                        indicator_ids.add(ind_id)
                        all_indicators.append(ind)

        return jsonify({"success": True, "data": all_indicators})
    except Exception as e:
        logger.error(f"指标查询出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@jqcx_bp.route('/methods', methods=['POST'])
def get_methods():
    """
    根据选中的项目和指标查询关联方法 (TestMethod)
    同时返回关联的文件信息 (File)
    """
    try:
        data = request.json or {}
        project_ids = data.get('projectIds', [])
        indicator_ids = data.get('indicatorIds', [])

        if not project_ids and not indicator_ids:
            return jsonify({"success": True, "data": [], "total": 0})

        method_data = {}

        if project_ids:
            cypher = """
            MATCH (p:TestProject)-[:CON]->(m:TestMethod)
            WHERE p.Id IN $projectIds
              AND (p).Status IN $flgl_enabled_status
              AND (m).Status IN $flgl_enabled_status
            OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
            WHERE (f) IS NULL OR (f).Status IN $flgl_enabled_status
            RETURN m, collect(DISTINCT p.Id) AS projIds, collect(DISTINCT f) AS files
            ORDER BY m.Name
            """
            records = _run_cypher(cypher, {**flgl_status_cypher_param(), 'projectIds': project_ids})
            for r in records:
                method = _get_node_props(r.get('m', {}))
                method_id = method.get('id', '')
                if method_id not in method_data:
                    method_data[method_id] = {
                        'method': method,
                        'proj_ids': set(),
                        'ind_ids': set(),
                        'files': []
                    }
                for pid in r.get('projIds', []):
                    if pid in project_ids:
                        method_data[method_id]['proj_ids'].add(pid)
                for f in r.get('files', []):
                    if f:
                        method_data[method_id]['files'].append(_get_node_props(f))

        if indicator_ids:
            cypher = """
            MATCH (i:Indicator)-[:CON]->(m:TestMethod)
            WHERE i.Id IN $indicatorIds
              AND (i).Status IN $flgl_enabled_status
              AND (m).Status IN $flgl_enabled_status
            OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
            WHERE (f) IS NULL OR (f).Status IN $flgl_enabled_status
            RETURN m, collect(DISTINCT i.Id) AS indIds, collect(DISTINCT f) AS files
            ORDER BY m.Name
            """
            records = _run_cypher(cypher, {**flgl_status_cypher_param(), 'indicatorIds': indicator_ids})
            for r in records:
                method = _get_node_props(r.get('m', {}))
                method_id = method.get('id', '')
                if method_id not in method_data:
                    method_data[method_id] = {
                        'method': method,
                        'proj_ids': set(),
                        'ind_ids': set(),
                        'files': []
                    }
                for iid in r.get('indIds', []):
                    if iid in indicator_ids:
                        method_data[method_id]['ind_ids'].add(iid)
                for f in r.get('files', []):
                    if f:
                        method_data[method_id]['files'].append(_get_node_props(f))

        nodes = []
        for method_id, data_item in method_data.items():
            method = data_item['method']
            if method.get('source_id'):
                method['source_name'] = _get_source_name(method['source_id'])

            files = list({f.get('id', ''): f for f in data_item['files']}.values())
            if files:
                method['files'] = files

            proj_ids = list(data_item['proj_ids'])
            ind_ids = list(data_item['ind_ids'])

            if project_ids and indicator_ids:
                has_project = len(proj_ids) > 0
                has_indicator = len(ind_ids) > 0
                if has_project and has_indicator:
                    method['_relationType'] = 'both'
                elif has_project:
                    method['_relationType'] = 'project-only'
                else:
                    method['_relationType'] = 'indicator-only'
            elif project_ids:
                method['_relationType'] = 'project-only'
            else:
                method['_relationType'] = 'indicator-only'

            nodes.append(method)
            print(f"方法: {method.get('name', method.get('id', ''))}, 关联项目: {proj_ids}, 关联指标: {ind_ids}, relationType: {method['_relationType']}")

        relation_weight = {'both': 3, 'project-only': 2, 'indicator-only': 1}
        nodes.sort(key=lambda x: (-relation_weight.get(x.get('_relationType', 'indicator-only'), 0), x.get('name', '')))
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
