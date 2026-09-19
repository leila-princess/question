"""
分类管理(FLGL) API — Neo4j 图数据库节点CRUD + 关联管理

节点类型:
  EquipmentType, Equipment, Environment, TestProject, Indicator, TestMethod, File

与 2026-04-27 设备类型页面相关的后端修改：修改点1～3 见 create_node、update_node 中注释；修改点4（列表时间只展示
YYYY-MM-DD）由前端的 tableDateDisplay / 设备类型面板的列配置处理。
"""
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
import uuid
import logging
import re
from urllib.parse import unquote, quote
from utils.operation_log import log_user_action

logger = logging.getLogger(__name__)

flgl_bp = Blueprint('flgl', __name__, url_prefix='/api/FLGL')

NODE_LABELS = {
    'EquipmentType': 'EquipmentType',
    'Equipment': 'Equipment',
    'Environment': 'Environment',
    'TestProject': 'TestProject',
    'Indicator': 'Indicator',
    'TestMethod': 'TestMethod',
    'File': 'File',
}

NODE_FIELDS = {
    'EquipmentType': ['Id', 'Name', 'Label', 'Description', 'Type', 'Status', 'User_name', 'Create_time'],
    'Equipment': ['Id', 'Name', 'Label', 'Description', 'Type', 'Status', 'User_name', 'Create_time'],
    'Environment': ['Id', 'Name', 'Label', 'Description', 'Type', 'Class', 'Combination', 'Limit', 'Request', 'Status', 'User_name', 'Create_time'],
    'TestProject': ['Id', 'Name', 'Type', 'Label', 'Description', 'Status', 'User_name', 'Create_time'],
    'Indicator': ['Id', 'Name', 'Label', 'Description', 'Type', 'unit', 'Status', 'User_name', 'Create_time'],
    'TestMethod': ['Id', 'Name', 'Type', 'Class', 'Label', 'Description', 'SYNR', 'Sytj', 'Sygc', 'Jgpd', 'Sy', 'Source_name', 'Source_id', 'Source_section_ids', 'Source_section_nos', 'Source_category', 'Extract_source', 'Status', 'User_name', 'Create_time'],
    'File': ['Id', 'Type', 'Label', 'Name', 'Chroma_id', 'minIo_id', 'Image_true', 'Image_minIO_path', 'Status', 'User_name', 'Create_time'],
}

# ID 前缀映射
ID_PREFIX = {
    'EquipmentType': 'ZBLX_',
    'Equipment': 'ZB_',
    'Environment': 'HJ_',
    'TestProject': 'XM_',
    'Indicator': 'ID_',
    'TestMethod': 'FF_',
    'File': 'WJ_',
}

def _generate_id(node_type):
    """生成自增 ID，如 ZBLX_0001、ZBLX_0002"""
    import re
    import uuid

    if node_type not in NODE_LABELS:
        return f"{ID_PREFIX.get(node_type, 'N_')}{uuid.uuid4().hex[:8]}"

    prefix = ID_PREFIX.get(node_type, 'N_')
    label = NODE_LABELS[node_type]

    # 查询当前最大序号
    cypher = f"""
        MATCH (n:{label})
        WHERE n.Id STARTS WITH $prefix
        RETURN n.Id AS max_id
        ORDER BY n.Id DESC
        LIMIT 1
    """
    records = _run_cypher(cypher, {'prefix': prefix})

    if records and records[0].get('max_id'):
        max_id = records[0]['max_id']
        match = re.search(r'(\d+)$', max_id)
        if match:
            num = int(match.group(1)) + 1
            return f"{prefix}{num:04d}"

    # 如果没有找到以该前缀开头的ID，从1开始
    return f"{prefix}0001"


def get_next_seq_starts_for_word_upload() -> dict:
    """
    与 test_extrac_output.generate_id 使用的序号对齐：从 Neo4j 各标签已有 Id 取最大尾号 +1 作为下一段起始顺序。
    与 extract 中 counters 一致：ZBLX_/ZB_/HJ_/XM_/ID_
    """
    import re
    mapping = [
        ('EquipmentType', 'ZBLX_', 'equipment_type'),
        ('Equipment', 'ZB_', 'equipment'),
        ('Environment', 'HJ_', 'environment'),
        ('TestProject', 'XM_', 'project'),
        ('Indicator', 'ID_', 'indicator'),
    ]
    out: dict = {}
    for label, pfx, key in mapping:
        max_n = 0
        try:
            recs = _run_cypher(
                f"MATCH (n:{label}) WHERE n.Id STARTS WITH $p RETURN n.Id AS id",
                {"p": pfx},
            )
            for r in (recs or []):
                raw = r.get("id") or ""
                m = re.search(r"(\d+)$", raw)
                if m:
                    max_n = max(max_n, int(m.group(1)))
        except Exception:
            pass
        out[key] = max_n + 1
    return out


def get_environment_registry_for_word_upload() -> dict:
    """
    返回 Neo4j 中已有 Environment 的 Name -> Id 映射。
    多次上传不同 Word 时复用同一套环境分类节点，避免重复提取/创建。
    """
    registry: dict = {}
    try:
        records = _run_cypher(
            """
            MATCH (n:Environment)
            WHERE n.Name IS NOT NULL AND trim(n.Name) <> ''
            RETURN n.Name AS name, n.Id AS id
            ORDER BY n.Name
            """
        )
        for row in records or []:
            name = re.sub(r'\s+', ' ', str(row.get('name') or '').strip())
            env_id = row.get('id')
            if name and env_id and name not in registry:
                registry[name] = str(env_id)
    except Exception:
        pass
    return registry


def _extract_method_image_minio_paths(html: str):
    if not html:
        return []
    paths = []
    for src in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', str(html), flags=re.IGNORECASE):
        minio_path = ''
        m = re.search(r'[?&]path=([^&#]+)', src)
        if m:
            minio_path = unquote(m.group(1))
        elif '试验方法/' in src:
            minio_path = unquote(src[src.find('试验方法/'):])
        if minio_path and minio_path not in paths:
            paths.append(minio_path)
    return paths


def _link_method_images(method_id: str, synr_html: str):
    paths = _extract_method_image_minio_paths(synr_html)
    if not method_id or not paths:
        return 0
    records = _run_cypher(
        """
        MATCH (m:TestMethod {Id: $method_id})
        MATCH (f:File)
        WHERE f.minIo_id IN $paths OR f.MinIo_id IN $paths OR f.MinIO_id IN $paths
        MERGE (f)-[:BELONGS_TO]->(m)
        RETURN count(DISTINCT f) AS cnt
        """,
        {"method_id": method_id, "paths": paths},
    )
    return int(records[0].get('cnt') or 0) if records else 0


def _environment_image_proxy_url(object_path: str) -> str:
    path = (object_path or '').strip().replace('\\', '/').lstrip('/')
    if path.startswith('faxm/'):
        path = path[5:]
    if not path:
        return ''
    return f"/api/GLCX/JQCX/minio-image?path={quote(path, safe='')}"


def _normalize_environment_media_html(html: str) -> str:
    """将 presigned / 裸 MinIO 路径转为长期有效的 JQCX 代理地址。"""
    if not html or '<img' not in str(html):
        return html or ''

    def _repl_img(match):
        full_tag = match.group(0)
        old_src = match.group(1)
        if '/minio-image?' in old_src and 'path=' in old_src:
            return full_tag
        obj_path = None
        faxm = re.search(r'/faxm/([^?"]+)', old_src)
        if faxm:
            obj_path = unquote(faxm.group(1))
        elif old_src.strip().startswith('环境/'):
            obj_path = unquote(old_src.strip().split('?')[0])
        if not obj_path:
            return full_tag
        return full_tag.replace(old_src, _environment_image_proxy_url(obj_path))

    return re.sub(
        r'<img\s+[^>]*src=["\']([^"\']+)["\'][^>]*/?>',
        _repl_img,
        str(html),
        flags=re.IGNORECASE,
    )


def _extract_environment_image_minio_paths(html: str, extra_paths=None):
    paths = []
    seen = set()

    def _add(raw_path):
        p = (raw_path or '').strip().replace('\\', '/').lstrip('/')
        if p.startswith('faxm/'):
            p = p[5:]
        if p and p not in seen:
            seen.add(p)
            paths.append(p)

    text = str(html or '')
    for src in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', text, flags=re.IGNORECASE):
        m = re.search(r'[?&]path=([^&#]+)', src)
        if m:
            _add(unquote(m.group(1)))
            continue
        faxm = re.search(r'/faxm/([^?]+)', src)
        if faxm:
            _add(unquote(faxm.group(1)))
            continue
        if '环境/' in src:
            _add(unquote(src[src.find('环境/'):].split('?')[0]))

    for m in re.finditer(r'(?:存储路径|路径)[：:]\s*\n?\s*(环境/[^\s\n<]+)', text, flags=re.IGNORECASE):
        _add(m.group(1))

    for item in extra_paths or []:
        if isinstance(item, dict):
            _add(item.get('path') or item.get('minio_path') or '')
        else:
            _add(item)
    return paths


def _ensure_environment_image_file_nodes(paths: list) -> int:
    created = 0
    for path in paths:
        existing = _run_cypher(
            """
            MATCH (f:File)
            WHERE f.minIo_id = $path OR f.MinIo_id = $path OR f.Image_minIO_path = $path
            RETURN f.Id AS id LIMIT 1
            """,
            {'path': path},
        )
        if existing:
            continue
        file_id = f"WJ_env_img_{uuid.uuid4().hex[:12]}"
        name = path.rsplit('/', 1)[-1] or path
        ext = name.rsplit('.', 1)[-1] if '.' in name else ''
        props = {
            'Id': file_id,
            'Type': '环境图片',
            'Label': '环境图片',
            'Name': name,
            'Chroma_id': '',
            'minIo_id': path,
            'Image_true': True,
            'Image_minIO_path': path,
            'Status': '启用',
            'User_name': '管理员',
            'Create_time': _now(),
            'File_type': ext,
        }
        prop_list = ', '.join([f'`{k}`: ${k}' for k in props.keys()])
        _run_cypher(f"CREATE (f:File {{{prop_list}}}) RETURN f", props)
        created += 1
    return created


def _link_environment_images(env_id: str, env: dict):
    """为环境字段中的图片创建 File 节点并建立 Environment-[:CONSTRAINED_BY]->File。"""
    if not env_id:
        return 0
    field_keys = ('description', 'type', 'class', 'combination', 'limit', 'request')
    html_blob = '\n'.join(str(env.get(k) or '') for k in field_keys)
    extra = env.get('images') or []
    paths = _extract_environment_image_minio_paths(html_blob, extra)
    if not paths:
        return 0
    _ensure_environment_image_file_nodes(paths)
    records = _run_cypher(
        """
        MATCH (e:Environment {Id: $env_id})
        MATCH (f:File)
        WHERE f.minIo_id IN $paths OR f.MinIo_id IN $paths OR f.Image_minIO_path IN $paths
        MERGE (e)-[:CONSTRAINED_BY]->(f)
        RETURN count(DISTINCT f) AS cnt
        """,
        {'env_id': env_id, 'paths': paths},
    )
    return int(records[0].get('cnt') or 0) if records else 0


def _environment_media_fields_from_env(env: dict) -> dict:
    """导入前规范化环境各字段中的图片 URL。"""
    out = dict(env)
    for key in ('description', 'type', 'class', 'combination', 'limit', 'request'):
        if out.get(key):
            out[key] = _normalize_environment_media_html(str(out[key]))
    return out


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


def _get_node_props(node_data):
    """获取节点属性，返回小写属性名（兼容前端）"""
    if not node_data:
        return {}
    result = {}
    for k, v in node_data.items():
        if k.startswith('_'):
            continue
        if hasattr(v, 'isoformat'):
            result[k.lower()] = v.isoformat()
        else:
            result[k.lower()] = v
    return result


def _gen_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _normalize_indicator_type(type_val) -> str:
    s = str(type_val or '').strip()
    if s in ('子级', 'test'):
        return 'test'
    if s in ('父级', 'category'):
        return 'category'
    return 'category'


def _sync_indicator_parent_has(child_id: str, parent_id: str, type_level: str):
    _run_cypher(
        """
        MATCH (:Indicator)-[r:HAS]->(c:Indicator {Id: $cid})
        DELETE r
        """,
        {'cid': child_id},
    )
    if type_level == 'test' and parent_id:
        _run_cypher(
            """
            MATCH (p:Indicator {Id: $pid}), (c:Indicator {Id: $cid})
            WHERE p <> c
            MERGE (p)-[:HAS]->(c)
            """,
            {'pid': parent_id, 'cid': child_id},
        )


# 与 create_node 使用同一套小写/驼峰 → 图节点属性名映射，供 _json_to_converted_data 与 update_node 复用
FLGL_FIELD_NAME_MAP = {
    'id': 'Id',
    'name': 'Name',
    'label': 'Label',
    'description': 'Description',
    'type': 'Type',
    'class': 'Class',
    'combination': 'Combination',
    'limit': 'Limit',
    'request': 'Request',
    'status': 'Status',
    'User_name': 'User_name',
    'create_time': 'Create_time',
    'unit': 'unit',
    'category': 'category',
    'synr': 'SYNR',
    'method_content': 'SYNR',
    'sytj': 'Sytj',
    'sygc': 'Sygc',
    'jgpd': 'Jgpd',
    'sy': 'Sy',
    'source_id': 'Source_id',
    'source_name': 'Source_name',
    'source_section_ids': 'Source_section_ids',
    'source_section_nos': 'Source_section_nos',
    'source_category': 'Source_category',
    'extract_source': 'Extract_source',
}


def _json_to_converted_data(data):
    """将请求 JSON 转为图数据库属性键（Name、Type 等）。"""
    if not data:
        return {}
    field_name_map = FLGL_FIELD_NAME_MAP
    converted = {}
    for key, value in data.items():
        if key in field_name_map:
            converted[field_name_map[key]] = value
        elif key and key[0].isupper():
            converted[key] = value
        else:
            capitalized = key[0].upper() + key[1:] if key else key
            converted[capitalized] = value
    return converted


@flgl_bp.route('/schema/<node_type>', methods=['GET'])
def get_node_schema(node_type):
    if node_type not in NODE_LABELS:
        return jsonify({"success": False, "error": f"未知节点类型: {node_type}"}), 400
    label = NODE_LABELS[node_type]
    cypher = f"MATCH (n:{label}) RETURN n LIMIT 1"
    records = _run_cypher(cypher)
    if not records:
        return jsonify({"success": True, "data": {"fields": [], "sample": None}})
    sample = _get_node_props(records[0].get('n', {}))
    fields = [k for k in sample.keys() if k != '_labels']
    return jsonify({"success": True, "data": {"fields": fields, "sample": sample}})


@flgl_bp.route('/nodes/<node_type>', methods=['GET'])
def list_nodes(node_type):
    if node_type not in NODE_LABELS:
        return jsonify({"success": False, "error": f"未知节点类型: {node_type}"}), 400

    label = NODE_LABELS[node_type]
    if node_type == 'Equipment':
        cypher = """
        MATCH (e:Equipment)
        OPTIONAL MATCH (et:EquipmentType)-[:HAS]->(e)
        RETURN e, collect(DISTINCT et.Id) AS type_ids, collect(DISTINCT et.Name) AS type_names
        ORDER BY e.Name, e.Id
        """
        records = _run_cypher(cypher)
        nodes = []
        for r in records:
            node = _get_node_props(r.get('e', {}))
            type_ids = r.get('type_ids', [])
            type_names = r.get('type_names', [])
            node['equipment_type_ids'] = [tid for tid in type_ids if tid]
            node['equipment_type_names'] = [tn for tn in type_names if tn]
            if type_names and type_names[0]:
                node['equipment_type'] = type_names[0]
            nodes.append(node)
    elif node_type == 'Indicator':
        cypher = """
        MATCH (i:Indicator)
        OPTIONAL MATCH (parent:Indicator)-[:HAS]->(i)
        RETURN i, parent.Id AS parent_id, parent.Name AS parent_name
        ORDER BY i.Name, i.Id
        """
        records = _run_cypher(cypher)
        nodes = []
        for r in records:
            node = _get_node_props(r.get('i', {}))
            pid = r.get('parent_id')
            pname = r.get('parent_name')
            node['parent_id'] = str(pid).strip() if pid is not None else ''
            node['parent_name'] = str(pname).strip() if pname is not None else ''
            nodes.append(node)
    else:
        cypher = f"MATCH (n:{label}) RETURN n ORDER BY n.Name, n.Id"
        records = _run_cypher(cypher)
        nodes = [_get_node_props(r.get('n', {})) for r in records]
    return jsonify({"success": True, "data": nodes, "total": len(nodes)})


@flgl_bp.route('/nodes/<node_type>', methods=['POST'])
def create_node(node_type):
    if node_type not in NODE_LABELS:
        return jsonify({"success": False, "error": f"未知节点类型: {node_type}"}), 400

    data = request.json or {}
    label = NODE_LABELS[node_type]
    fields = NODE_FIELDS[node_type]

    converted_data = _json_to_converted_data(data)

    # 生成自增 ID；试验方法支持前端草稿 ID，用于提前上传富文本图片到 试验方法/{id}/图片
    provided_id = str(converted_data.get('Id') or data.get('id') or '').strip()
    node_id = provided_id if node_type == 'TestMethod' and provided_id else _generate_id(node_type)

    logger.info(f"创建节点: {node_type}, 生成的ID: {node_id}")
    logger.info(f"接收到的数据: {data}")
    logger.info(f"转换后的数据: {converted_data}")

    props = {'Id': node_id}
    for f in fields:
        if f in ('Id', 'Create_time'):
            continue
        if f in converted_data and converted_data[f] is not None:
            props[f] = converted_data[f]
    if node_type == 'Indicator':
        props['Type'] = _normalize_indicator_type(props.get('Type'))
    # 2026-04-27 修改点1：设备类型（EquipmentType）「一级类别」为节点属性 Type；选「其他」时自定义的父级标题也写入该字段
    if node_type == 'EquipmentType' and 'Type' in props and props.get('Type') is not None:
        t1 = str(props['Type']).strip()
        if t1:
            props['Type'] = t1
        else:
            props.pop('Type', None)
    # 2026-04-27 修改点2：未传或空的 User_name 时默认 管理员
    u0 = props.get('User_name')
    if u0 is None or (isinstance(u0, str) and u0.strip() == ''):
        props['User_name'] = '管理员'
    props['Create_time'] = _now()
    # 2026-04-27 修改点3：新增节点未指定状态字段时默认为启用，对应前端的 value active
    props['Status'] = props.get('Status', '启用')

    if node_type == 'Environment':
        for k in ('Description', 'Type', 'Class', 'Combination', 'Limit', 'Request'):
            if k in props and props[k]:
                props[k] = _normalize_environment_media_html(str(props[k]))

    # 构建创建语句
    prop_list = ', '.join([f'`{k}`: ${k}' for k in props.keys()])
    cypher = f"CREATE (n:{label} {{{prop_list}}}) RETURN n"

    logger.info(f"执行的Cypher: {cypher}")
    logger.info(f"参数: {props}")

    records = _run_cypher(cypher, props)
    if node_type == 'TestMethod':
        _link_method_images(node_id, str(props.get('SYNR') or ''))
    if node_type == 'Environment':
        _link_environment_images(node_id, {
            'description': props.get('Description') or '',
            'type': props.get('Type') or '',
            'class': props.get('Class') or '',
            'combination': props.get('Combination') or '',
            'limit': props.get('Limit') or '',
            'request': props.get('Request') or '',
        })
    if node_type == 'Indicator':
        _sync_indicator_parent_has(
            node_id,
            str(data.get('parent_id') or converted_data.get('ParentId') or '').strip(),
            props.get('Type', 'category'),
        )
    node = _get_node_props(records[0].get('n', {})) if records else None

    log_user_action(f"分类管理-新增图节点 type={node_type} id={node_id}")
    return jsonify({"success": True, "data": node})


@flgl_bp.route('/nodes/<node_type>/<node_id>', methods=['PUT'])
def update_node(node_type, node_id):
    if node_type not in NODE_LABELS:
        return jsonify({"success": False, "error": f"未知节点类型: {node_type}"}), 400

    label = NODE_LABELS[node_type]
    fields = NODE_FIELDS[node_type]
    data = request.json or {}
    # 2026-04-27 修改点1/配套：与 POST 一致，将前端的 name/type 等小写键规范为图属性 Name、Type
    converted = _json_to_converted_data(data)

    logger.info(f"PUT /nodes/{node_type}/{node_id} - 收到数据: {data}, 归一化后: {converted}")

    # 检查节点是否存在
    check = _run_cypher(f"MATCH (n:{label} {{Id: $id}}) RETURN n LIMIT 1", {'id': node_id})
    if not check:
        return jsonify({"success": False, "error": "节点不存在"}), 404

    # 构建更新语句
    set_parts = []
    params = {'id': node_id}
    for key, value in converted.items():
        if key in ('Id', 'Create_time'):
            continue
        if key in fields:
            v = value
            if key == 'Type' and v is not None and node_type == 'EquipmentType':
                v = str(v).strip()
            elif key == 'Type' and node_type == 'Indicator':
                v = _normalize_indicator_type(v)
            elif node_type == 'Environment' and key in ('Description', 'Type', 'Class', 'Combination', 'Limit', 'Request') and v:
                v = _normalize_environment_media_html(str(v))
            set_parts.append(f"n.`{key}` = ${key}")
            params[key] = v
            logger.info(f"更新字段: {key} = {v}")
        else:
            logger.warning(f"未知字段(归一化后): {key}, 允许的字段: {fields}")

    if not set_parts:
        return jsonify({"success": False, "error": "无更新字段"}), 400

    cypher = f"MATCH (n:{label} {{Id: $id}}) SET {', '.join(set_parts)} RETURN n"
    logger.info(f"执行Cypher: {cypher}")

    records = _run_cypher(cypher, params)
    if node_type == 'TestMethod' and 'SYNR' in params:
        _link_method_images(node_id, str(params.get('SYNR') or ''))
    if node_type == 'Indicator':
        type_level = _normalize_indicator_type(params.get('Type'))
        if 'Type' not in params:
            existing = _run_cypher("MATCH (n:Indicator {Id: $id}) RETURN n.Type AS t LIMIT 1", {'id': node_id})
            type_level = _normalize_indicator_type(existing[0].get('t') if existing else 'category')
        parent_id = str(data.get('parent_id') or data.get('ParentId') or '').strip()
        if type_level == 'category':
            parent_id = ''
        elif not parent_id:
            pr = _run_cypher(
                "MATCH (p:Indicator)-[:HAS]->(c:Indicator {Id: $id}) RETURN p.Id AS pid LIMIT 1",
                {'id': node_id},
            )
            parent_id = str(pr[0].get('pid') or '').strip() if pr and pr[0].get('pid') is not None else ''
        _sync_indicator_parent_has(node_id, parent_id, type_level)
    node = _get_node_props(records[0].get('n', {})) if records else None
    if node_type == 'Environment' and node:
        _link_environment_images(node_id, {
            'description': str(node.get('description') or ''),
            'type': str(node.get('type') or ''),
            'class': str(node.get('class') or ''),
            'combination': str(node.get('combination') or ''),
            'limit': str(node.get('limit') or ''),
            'request': str(node.get('request') or ''),
        })

    log_user_action(f"分类管理-修改图节点 type={node_type} id={node_id}")
    return jsonify({"success": True, "data": node})


@flgl_bp.route('/nodes/<node_type>/<node_id>', methods=['DELETE'])
def delete_node(node_type, node_id):
    if node_type not in NODE_LABELS:
        return jsonify({"success": False, "error": f"未知节点类型: {node_type}"}), 400

    label = NODE_LABELS[node_type]
    if node_type == 'Indicator':
        child_recs = _run_cypher(
            "MATCH (:Indicator {Id: $id})-[:HAS]->(c:Indicator) RETURN count(c) AS cnt",
            {'id': node_id},
        )
        child_cnt = int(child_recs[0].get('cnt') or 0) if child_recs else 0
        if child_cnt > 0:
            return jsonify({"success": False, "error": f"该指标下仍有 {child_cnt} 个子指标，请先处理子指标"}), 400
    cypher = f"MATCH (n:{label} {{Id: $id}}) DETACH DELETE n"
    _run_cypher(cypher, {'id': node_id})
    log_user_action(f"分类管理-删除图节点 type={node_type} id={node_id}")
    return jsonify({"success": True, "data": None})


@flgl_bp.route('/nodes/<node_type>/<node_id>/relations', methods=['GET'])
def get_node_relations(node_type, node_id):
    if node_type not in NODE_LABELS:
        return jsonify({"success": False, "error": f"未知节点类型: {node_type}"}), 400

    cypher = """
    MATCH (n {Id: $id})-[r]-(m)
    RETURN type(r) AS rel_type, labels(m) AS target_labels, m.Id AS target_id, m.Name AS target_name,
           CASE WHEN startNode(r).Id = $id THEN 'outgoing' ELSE 'incoming' END AS direction,
           CASE WHEN 'rate' IN keys(r) THEN r.rate WHEN 'Rate' IN keys(r) THEN r.Rate ELSE null END AS rate
    ORDER BY rel_type, target_name
    """
    records = _run_cypher(cypher, {'id': node_id})
    relations = []
    for r in records:
        raw_rate = r.get('rate')
        rate_out = None
        if raw_rate is not None and raw_rate != '':
            try:
                rate_out = float(raw_rate)
            except (TypeError, ValueError):
                rate_out = None
        relations.append({
            'rel_type': r.get('rel_type', ''),
            'target_labels': r.get('target_labels', []),
            'target_id': r.get('target_id', ''),
            'target_name': r.get('target_name', ''),
            'direction': r.get('direction', ''),
            'rate': rate_out,
        })
    return jsonify({"success": True, "data": relations})


@flgl_bp.route('/relations', methods=['POST'])
def create_relations():
    data = request.json or {}
    node_id = data.get('node_id', '')
    relations = data.get('relations', [])

    if not node_id or not relations:
        return jsonify({"success": False, "error": "缺少必要参数"}), 400

    allowed_rel_types = {'CON', 'HAS', 'BELONGS_TO'}
    created = 0
    for rel in relations:
        target_id = rel.get('target_id', '')
        rel_type = str(rel.get('rel_type', 'CON') or 'CON').strip()
        direction = rel.get('direction', 'outgoing')

        if not target_id or rel_type not in allowed_rel_types:
            continue

        if direction == 'outgoing':
            cypher = """
            MATCH (a {Id: $from_id})
            MATCH (b {Id: $to_id})
            MERGE (a)-[r:`%s`]->(b)
            RETURN type(r) AS rel_type
            """ % rel_type
            records = _run_cypher(cypher, {'from_id': node_id, 'to_id': target_id})
        else:
            cypher = """
            MATCH (a {Id: $from_id})
            MATCH (b {Id: $to_id})
            MERGE (a)<-[r:`%s`]-(b)
            RETURN type(r) AS rel_type
            """ % rel_type
            records = _run_cypher(cypher, {'from_id': node_id, 'to_id': target_id})
        if records:
            created += 1

    log_user_action(f"分类管理-批量创建关系 from={node_id} 条数={created}")
    return jsonify({"success": True, "data": {"created": created}})


@flgl_bp.route('/relations', methods=['DELETE'])
def delete_relation():
    data = request.json or {}
    node_id = data.get('node_id', '')
    target_id = data.get('target_id', '')
    rel_type = str(data.get('rel_type', 'CON') or 'CON').strip()
    direction = data.get('direction', 'outgoing')

    if not node_id or not target_id:
        return jsonify({"success": False, "error": "缺少必要参数"}), 400
    if rel_type not in {'CON', 'HAS', 'BELONGS_TO'}:
        return jsonify({"success": False, "error": f"不支持的关系类型: {rel_type}"}), 400

    if direction == 'outgoing':
        cypher = f"MATCH (a {{Id: $from_id}})-[r:`{rel_type}`]->(b {{Id: $to_id}}) DELETE r"
    else:
        cypher = f"MATCH (a {{Id: $from_id}})<-[r:`{rel_type}`]-(b {{Id: $to_id}}) DELETE r"
    _run_cypher(cypher, {'from_id': node_id, 'to_id': target_id})
    log_user_action(
        f"分类管理-删除关系 type={rel_type} from={node_id} to={target_id} dir={direction}"
    )
    return jsonify({"success": True, "data": None})


@flgl_bp.route('/available-nodes', methods=['POST'])
def get_available_nodes():
    data = request.json or {}
    target_type = data.get('target_type', '')
    source_node_id = (data.get('source_node_id') or '').strip()
    source_node_type = (data.get('source_node_type') or '').strip()

    if target_type not in NODE_LABELS:
        return jsonify({"success": False, "error": f"未知节点类型: {target_type}"}), 400

    label = NODE_LABELS[target_type]
    params = {'source_id': source_node_id}

    if source_node_id and source_node_type == 'EquipmentType' and target_type == 'Equipment':
        cypher = """
        MATCH (src:EquipmentType {Id: $source_id})-[:HAS]->(n:Equipment)
        RETURN n.Id AS id, n.Name AS name ORDER BY n.Name
        """
        records = _run_cypher(cypher, params)
    elif source_node_id and source_node_type == 'Equipment' and target_type == 'TestProject':
        cypher = """
        MATCH (src:Equipment {Id: $source_id})-[:CON]->(n:TestProject)
        RETURN n.Id AS id, n.Name AS name ORDER BY n.Name
        """
        records = _run_cypher(cypher, params)
    elif source_node_id and source_node_type == 'Equipment' and target_type == 'Indicator':
        cypher = """
        MATCH (src:Equipment {Id: $source_id})-[:CON]->(n:Indicator)
        RETURN n.Id AS id, n.Name AS name ORDER BY n.Name
        """
        records = _run_cypher(cypher, params)
    else:
        cypher = f"MATCH (n:{label}) RETURN n.Id AS id, n.Name AS name ORDER BY n.Name"
        records = _run_cypher(cypher)

    nodes = [{'id': r.get('id', ''), 'name': r.get('name', '')} for r in records]
    return jsonify({"success": True, "data": nodes})


@flgl_bp.route('/document-parse', methods=['POST'])
def document_parse():
    """文档解析入口（预留）"""
    return jsonify({"success": True, "data": {"message": "文档解析功能待实现"}})

# ==================== 环境文档解析接口 ====================

import os
import tempfile
from werkzeug.utils import secure_filename

@flgl_bp.route('/env-parse', methods=['POST'])
def env_parse():
    """上传 Word 文件，解析环境数据，返回结构化 JSON"""
    from .env_parser import parse_environment_doc
    from config import Config

    if 'file' not in request.files:
        return jsonify({"success": False, "error": "没有文件"}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    # 保存到临时目录
    tmp_dir = tempfile.mkdtemp()
    filename = secure_filename(file.filename)
    filepath = os.path.join(tmp_dir, filename)
    file.save(filepath)

    try:
        envs = parse_environment_doc(
            filepath,
            minio_client=Config.MINIO_CLIENT,
            bucket_name=Config.MINIO_BUCKET_NAME,
        )
        return jsonify({"success": True, "data": envs})
    except Exception as e:
        logger.error(f"环境文档解析失败: {e}")
        return jsonify({"success": False, "error": f"解析失败: {str(e)}"}), 500
    finally:
        # 清理临时文件
        try:
            os.remove(filepath)
            os.rmdir(tmp_dir)
        except:
            pass



def _find_environment_id_by_name(name: str):
    """按名称精确匹配（与 trim 后的名称一致）"""
    nm = (name or '').strip()
    if not nm:
        return None
    records = _run_cypher(
        "MATCH (n:Environment) WHERE n.Name = $name RETURN n.Id AS id LIMIT 1",
        {'name': nm},
    )
    if records and records[0].get('id'):
        return records[0]['id']
    return None


def _environment_import_props_create(node_id: str, env: dict) -> dict:
    env = _environment_media_fields_from_env(env)
    u0 = env.get('User_name', '')
    if u0 is None or (isinstance(u0, str) and u0.strip() == ''):
        u0 = '管理员'
    return {
        'Id': node_id,
        'Name': (env.get('name') or '').strip(),
        'Label': env.get('label', '') or '',
        'Description': env.get('description', '') or '',
        'Type': env.get('type', '') or '',
        'Class': env.get('class', '') or '',
        'Combination': env.get('combination', '') or '',
        'Limit': env.get('limit', '') or '',
        'Request': env.get('request', '') or '',
        'Status': '启用',
        'User_name': u0,
        'Create_time': _now(),
    }


def _environment_apply_update_from_env(existing_id: str, env: dict):
    """覆盖更新已有环境节点（保留 Id、Create_time）"""
    env = _environment_media_fields_from_env(env)
    label = NODE_LABELS['Environment']
    fields = NODE_FIELDS['Environment']
    payload = {
        'name': (env.get('name') or '').strip(),
        'label': env.get('label', '') or '',
        'description': env.get('description', '') or '',
        'type': env.get('type', '') or '',
        'class': env.get('class', '') or '',
        'combination': env.get('combination', '') or '',
        'limit': env.get('limit', '') or '',
        'request': env.get('request', '') or '',
        'status': '启用',
    }
    u = env.get('User_name', '')
    if u is not None and str(u).strip() != '':
        payload['User_name'] = u
    converted = _json_to_converted_data(payload)
    set_parts = []
    params = {'id': existing_id}
    for key, value in converted.items():
        if key in ('Id', 'Create_time'):
            continue
        if key in fields:
            set_parts.append(f"n.`{key}` = ${key}")
            params[key] = value
    if not set_parts:
        return None
    cypher = f"MATCH (n:{label} {{Id: $id}}) SET {', '.join(set_parts)} RETURN n"
    records = _run_cypher(cypher, params)
    return _get_node_props(records[0].get('n', {})) if records else None


def _env_import_detect_conflicts(environments: list):
    conflicts = []
    seen_names = {}
    for i, env in enumerate(environments):
        nm = (env.get('name') or '').strip()
        if not nm:
            conflicts.append({
                'index': i,
                'name': nm,
                'existing_id': None,
                'reason': 'empty_name',
            })
            continue
        if nm in seen_names:
            conflicts.append({
                'index': i,
                'name': nm,
                'existing_id': _find_environment_id_by_name(nm),
                'reason': 'duplicate_in_batch',
                'duplicate_of_index': seen_names[nm],
            })
            continue
        seen_names[nm] = i
        ex_id = _find_environment_id_by_name(nm)
        if ex_id:
            conflicts.append({
                'index': i,
                'name': nm,
                'existing_id': ex_id,
                'reason': 'exists_in_db',
            })
    return conflicts


def _env_import_apply(environments: list, resolutions: list):
    """按用户选择的策略执行导入"""
    created_nodes = []
    stats = {'created': 0, 'replaced': 0, 'skipped': 0, 'renamed': 0}

    for i, env in enumerate(environments):
        res = resolutions[i] if i < len(resolutions) else {}
        if not isinstance(res, dict):
            return jsonify({"success": False, "error": f"第 {i} 条 resolutions 格式无效"}), 400
        action = (res.get('action') or 'create').strip().lower()

        if action == 'skip':
            stats['skipped'] += 1
            continue

        if action == 'replace':
            existing_id = (res.get('existing_id') or '').strip()
            if not existing_id:
                return jsonify({"success": False, "error": f"第 {i} 条 replace 缺少 existing_id"}), 400
            chk = _run_cypher(
                "MATCH (n:Environment {Id: $id}) RETURN n LIMIT 1",
                {'id': existing_id},
            )
            if not chk:
                return jsonify({"success": False, "error": f"节点不存在: {existing_id}"}), 404
            node = _environment_apply_update_from_env(existing_id, env)
            if node is None:
                return jsonify({"success": False, "error": f"替换更新失败: {existing_id}"}), 500
            _link_environment_images(existing_id, env)
            created_nodes.append(node)
            stats['replaced'] += 1
            continue

        if action == 'rename':
            new_name = (res.get('new_name') or '').strip()
            if not new_name:
                return jsonify({"success": False, "error": f"第 {i} 条 rename 缺少 new_name"}), 400
            if _find_environment_id_by_name(new_name):
                return jsonify({
                    "success": False,
                    "error": f"重命名后的名称已存在: {new_name}",
                }), 400
            node_id = _generate_id('Environment')
            props = _environment_import_props_create(node_id, env)
            props['Name'] = new_name
            prop_list = ', '.join([f'`{k}`: ${k}' for k in props.keys()])
            cypher = f"CREATE (n:Environment {{{prop_list}}}) RETURN n"
            records = _run_cypher(cypher, props)
            node = _get_node_props(records[0].get('n', {})) if records else None
            if node:
                _link_environment_images(node_id, env)
                created_nodes.append(node)
            stats['renamed'] += 1
            continue

        if action == 'create':
            nm = (env.get('name') or '').strip()
            if not nm:
                stats['skipped'] += 1
                continue
            dup = _find_environment_id_by_name(nm)
            if dup:
                return jsonify({
                    "success": False,
                    "error": f"第 {i} 条以 create 导入时名称仍与库中重复: {nm}",
                }), 400
            node_id = _generate_id('Environment')
            props = _environment_import_props_create(node_id, env)
            prop_list = ', '.join([f'`{k}`: ${k}' for k in props.keys()])
            cypher = f"CREATE (n:Environment {{{prop_list}}}) RETURN n"
            records = _run_cypher(cypher, props)
            node = _get_node_props(records[0].get('n', {})) if records else None
            if node:
                _link_environment_images(node_id, env)
                created_nodes.append(node)
            stats['created'] += 1
            continue

        return jsonify({"success": False, "error": f"未知 action: {action}"}), 400

    log_user_action(
        f"分类管理-环境批量导入(带策略) created={stats['created']} replaced={stats['replaced']} skipped={stats['skipped']} renamed={stats['renamed']}"
    )
    return jsonify({
        "success": True,
        "data": {
            "status": "done",
            "created": stats['created'],
            "replaced": stats['replaced'],
            "skipped": stats['skipped'],
            "renamed": stats['renamed'],
            "nodes": created_nodes,
        }
    })


@flgl_bp.route('/env-import', methods=['POST'])
def env_import():
    """将解析的环境数据批量导入 Neo4j。

    未传 resolutions：先检测同名（库内、批次内）。有冲突则返回 status=conflict，不落库；
    无冲突则直接全部新建。

    传入与 environments 等长的 resolutions：按每条 action 执行
    create | replace | skip | rename（rename 需提供 new_name，replace 需提供 existing_id）。
    """
    data = request.json or {}
    environments = data.get('environments', [])
    resolutions = data.get('resolutions')

    if not environments:
        return jsonify({"success": False, "error": "没有环境数据"}), 400

    if resolutions is not None:
        if not isinstance(resolutions, list) or len(resolutions) != len(environments):
            return jsonify({"success": False, "error": "resolutions 必须与 environments 等长"}), 400
        return _env_import_apply(environments, resolutions)

    conflicts = _env_import_detect_conflicts(environments)
    if conflicts:
        log_user_action(
            f"分类管理-环境批量导入(待处理冲突) count={len(environments)}",
            result="pending",
        )
        return jsonify({
            "success": True,
            "data": {
                "status": "conflict",
                "conflicts": conflicts,
            }
        })

    created_nodes = []
    for env in environments:
        nm = (env.get('name') or '').strip()
        if not nm:
            continue
        node_id = _generate_id('Environment')
        props = _environment_import_props_create(node_id, env)
        prop_list = ', '.join([f'`{k}`: ${k}' for k in props.keys()])
        cypher = f"CREATE (n:Environment {{{prop_list}}}) RETURN n"
        records = _run_cypher(cypher, props)
        node = _get_node_props(records[0].get('n', {})) if records else None
        if node:
            _link_environment_images(node_id, env)
            created_nodes.append(node)

    log_user_action(f"分类管理-环境批量导入完成 created={len(created_nodes)}")
    return jsonify({
        "success": True,
        "data": {
            "status": "done",
            "created": len(created_nodes),
            "replaced": 0,
            "skipped": 0,
            "renamed": 0,
            "nodes": created_nodes,
        }
    })


@flgl_bp.route('/env-repair-media', methods=['POST'])
def env_repair_media():
    """修复已入库环境节点的图片：规范化 HTML 中的图片 URL，并补建 File 节点与 CONSTRAINED_BY 关系。"""
    data = request.json or {}
    env_id = (data.get('env_id') or '').strip()

    if env_id:
        records = _run_cypher(
            "MATCH (n:Environment {Id: $id}) RETURN n",
            {'id': env_id},
        )
    else:
        records = _run_cypher("MATCH (n:Environment) RETURN n")

    if not records:
        return jsonify({"success": False, "error": "未找到环境节点"}), 404

    repaired = 0
    linked_files = 0
    for row in records:
        raw = row.get('n', {})
        props = _get_node_props(raw) if hasattr(raw, 'items') else {}
        eid = props.get('id') or ''
        if not eid:
            continue
        media_keys = [
            ('description', 'Description'),
            ('type', 'Type'),
            ('class', 'Class'),
            ('combination', 'Combination'),
            ('limit', 'Limit'),
            ('request', 'Request'),
        ]
        set_parts = []
        params = {'id': eid}
        env_snapshot = {}
        for low_key, neo_key in media_keys:
            val = props.get(low_key) or ''
            norm = _normalize_environment_media_html(str(val))
            env_snapshot[low_key] = norm
            if norm != str(val):
                set_parts.append(f"n.`{neo_key}` = ${neo_key}")
                params[neo_key] = norm
        if set_parts:
            _run_cypher(
                f"MATCH (n:Environment {{Id: $id}}) SET {', '.join(set_parts)} RETURN n",
                params,
            )
            repaired += 1
        linked_files += _link_environment_images(eid, env_snapshot)

    log_user_action(f"分类管理-环境图片修复 nodes={len(records)} repaired={repaired} linked_files={linked_files}")
    return jsonify({
        "success": True,
        "data": {
            "nodes": len(records),
            "html_normalized": repaired,
            "file_links": linked_files,
        },
    })


@flgl_bp.route('/method-categories', methods=['GET'])
def method_categories():
    """方法 Tab 左侧分类树：环境（来自项目-方法链上的环境）+ 指标 + 未分类（与 method_routes 同源）。"""
    from .method_routes import get_method_category_tree_response
    return get_method_category_tree_response()
