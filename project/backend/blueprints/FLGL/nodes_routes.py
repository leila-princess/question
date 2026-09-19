"""
FLGL 节点管理 API - 使用APOC自增ID生成

节点类型:
  Equipment(设备), TestProject(项目), Indicator(指标)
"""
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
import logging
import jwt

from utils.operation_log import log_user_action

logger = logging.getLogger(__name__)

flgl_nodes_bp = Blueprint('flgl_nodes', __name__, url_prefix='/api/FLGL/newnodes')

NODE_LABELS = {
    'Equipment': 'Equipment',
    'TestProject': 'TestProject',
    'Indicator': 'Indicator',
}

NODE_FIELDS = {
    'Equipment': ['Name', 'Type', 'Label', 'Description', 'Status', 'User_name', 'Create_time'],
    # TestProject: Type=category|test（兼容父级|子级）；Label=ENVIRONMENT_ORDER 中的环境分类名
    'TestProject': ['Name', 'Type', 'Label', 'Description', 'Status', 'User_name', 'Create_time'],
    # Indicator: Type=category|test（兼容父级|子级）；ParentId 通过 HAS 关系维护
    'Indicator': ['Name', 'Type', 'Description', 'Status', 'User_name', 'Create_time'],
}

ID_PREFIX = {
    'Equipment': 'ZB_',
    'TestProject': 'XM_',
    'Indicator': 'ID_',
}


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


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _normalize_testproject_type(type_val) -> str:
    """图谱/接口统一为 category（父）、test（子）；兼容旧数据 父级|子级。"""
    s = str(type_val or '').strip()
    if s in ('子级', 'test'):
        return 'test'
    if s in ('父级', 'category'):
        return 'category'
    return 'category'


def _normalize_indicator_type(type_val) -> str:
    """指标类型统一为 category（父）、test（子）；兼容旧数据 父级|子级。"""
    s = str(type_val or '').strip()
    if s in ('子级', 'test'):
        return 'test'
    if s in ('父级', 'category'):
        return 'category'
    return 'category'


def _sync_testproject_parent_has(child_id: str, parent_id: str, type_level: str):
    """父项目 -[:HAS]-> 子项目；父级则去掉入边。仅删除/创建 TestProject 之间的 HAS。"""
    _run_cypher(
        """
        MATCH (:TestProject)-[r:HAS]->(c:TestProject {Id: $cid})
        DELETE r
        """,
        {'cid': child_id},
    )
    if type_level == 'test' and parent_id:
        _run_cypher(
            """
            MATCH (p:TestProject {Id: $pid}), (c:TestProject {Id: $cid})
            WHERE p <> c
            MERGE (p)-[:HAS]->(c)
            """,
            {'pid': parent_id, 'cid': child_id},
        )


def _sync_indicator_parent_has(child_id: str, parent_id: str, type_level: str):
    """父指标 -[:HAS]-> 子指标；父级则去掉入边。仅删除/创建 Indicator 之间的 HAS。"""
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


def _sync_incoming_con(node_label: str, node_id: str, source_label: str, source_id: str):
    """同步 source_label -[:CON]-> node_label 的单一选择关系。

    仅在前端显式传入 equipment_id/environment_id 时调用，避免影响导入数据的既有多关联。
    """
    _run_cypher(
        f"""
        MATCH (src:{source_label})-[r:CON]->(n:{node_label} {{Id: $node_id}})
        DELETE r
        """,
        {'node_id': node_id},
    )
    sid = str(source_id or '').strip()
    if sid:
        _run_cypher(
            f"""
            MATCH (src:{source_label} {{Id: $source_id}}), (n:{node_label} {{Id: $node_id}})
            MERGE (src)-[:CON]->(n)
            """,
            {'source_id': sid, 'node_id': node_id},
        )


def _dedupe_str_list(values):
    out = []
    seen = set()
    for value in values or []:
        if value is None:
            continue
        s = str(value).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _get_current_user():
    from config import Config
    import jwt
    SECRET_KEY = 'your-secret-key-here-change-in-production'
    
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token:
        return {'userId': 'admin', 'userName': '管理员'}
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        user_id = payload.get('user_id')
        if Config.MYSQL_POOL:
            with Config.MYSQL_POOL.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT id, user_id, name, role FROM sys_user WHERE user_id = %s",
                        (user_id,)
                    )
                    row = cursor.fetchone()
            if row:
                return {'userId': row[1], 'userName': row[2]}
    except:
        pass
    return {'userId': 'admin', 'userName': '管理员'}


def _generate_id_with_apoc(node_type):
    import re
    import uuid
    prefix = ID_PREFIX.get(node_type, 'N_')
    
    cypher = f"MATCH (n:{NODE_LABELS[node_type]}) WHERE n.Id STARTS WITH $prefix RETURN n.Id AS max_id ORDER BY n.Id DESC LIMIT 1"
    records = _run_cypher(cypher, {'prefix': prefix})
    
    if records and records[0].get('max_id'):
        max_id = records[0]['max_id']
        match = re.search(r'(\d+)$', max_id)
        if match:
            num = int(match.group(1)) + 1
            return f"{prefix}{num:04d}"
    
    cypher2 = f"MATCH (n:{NODE_LABELS[node_type]}) RETURN n.Id AS all_ids ORDER BY n.Id DESC"
    records2 = _run_cypher(cypher2)
    
    for r in records2:
        all_id = r.get('all_ids', '')
        if all_id.startswith(prefix):
            match = re.search(r'(\d+)$', all_id)
            if match:
                num = int(match.group(1)) + 1
                return f"{prefix}{num:04d}"
    
    return f"{prefix}{uuid.uuid4().hex[:8]}"

@flgl_nodes_bp.route('/<node_type>', methods=['GET'])
def list_nodes(node_type):
    if node_type not in NODE_LABELS:
        return jsonify({"success": False, "error": f"未知节点类型: {node_type}"}), 400

    label = NODE_LABELS[node_type]
    
    search = request.args.get('search', '')
    
    if node_type == 'TestProject':
        # 左侧分类用 Label；图谱环境仍用 CON；父子项目 parent-[:HAS]->p
        cypher = """
        MATCH (p:TestProject)
        OPTIONAL MATCH (parent:TestProject)-[:HAS]->(p)
        OPTIONAL MATCH (e_in:Environment)-[:CON]->(p)
        OPTIONAL MATCH (p)-[:CON]->(e_out:Environment)
        OPTIONAL MATCH (eq_in:Equipment)-[:CON]->(p)
        OPTIONAL MATCH (p)-[:CON]->(eq_out:Equipment)
        RETURN p,
               parent.Id AS parent_id,
               parent.Name AS parent_name,
               [x IN (collect(DISTINCT e_in.Id) + collect(DISTINCT e_out.Id)) WHERE x IS NOT NULL] AS env_ids,
               [x IN (collect(DISTINCT e_in.Name) + collect(DISTINCT e_out.Name)) WHERE x IS NOT NULL] AS env_names,
               [x IN (collect(DISTINCT eq_in.Id) + collect(DISTINCT eq_out.Id)) WHERE x IS NOT NULL] AS equipment_ids,
               [x IN (collect(DISTINCT eq_in.Name) + collect(DISTINCT eq_out.Name)) WHERE x IS NOT NULL] AS equipment_names
        ORDER BY p.Create_time DESC
        """
        records = _run_cypher(cypher)
        nodes = []
        for r in records:
            node = _get_node_props(r.get('p', {}))
            pid = r.get('parent_id')
            pname = r.get('parent_name')
            node['parent_id'] = str(pid).strip() if pid is not None else ''
            node['parent_name'] = str(pname).strip() if pname is not None else ''
            env_ids = _dedupe_str_list(r.get('env_ids') or [])
            env_names = _dedupe_str_list(r.get('env_names') or [])
            equipment_ids = _dedupe_str_list(r.get('equipment_ids') or [])
            equipment_names = _dedupe_str_list(r.get('equipment_names') or [])
            node['environment_names'] = env_names
            node['environment_ids'] = env_ids
            e0 = env_ids[0] if env_ids else ''
            node['environment_id'] = e0
            node['environmentid'] = e0
            node['equipment_ids'] = equipment_ids
            node['equipment_names'] = equipment_names
            node['equipment_id'] = equipment_ids[0] if equipment_ids else ''
            node['equipment_name'] = equipment_names[0] if equipment_names else ''
            nodes.append(node)
    elif node_type == 'Equipment':
        # 设备类型可能在 Equipment 的入边或出边，关系类型常见 HAS / CON
        cypher = """
        MATCH (e:Equipment)
        OPTIONAL MATCH (et_in:EquipmentType)-[:HAS|CON]->(e)
        OPTIONAL MATCH (e)-[:HAS|CON]->(et_out:EquipmentType)
        RETURN e,
               [x IN (collect(DISTINCT et_in.Id) + collect(DISTINCT et_out.Id)) WHERE x IS NOT NULL] AS et_ids,
               [x IN (collect(DISTINCT et_in.Name) + collect(DISTINCT et_out.Name)) WHERE x IS NOT NULL] AS et_names,
               [x IN (collect(DISTINCT et_in.Type) + collect(DISTINCT et_out.Type)) WHERE x IS NOT NULL] AS et_types
        ORDER BY e.Create_time DESC
        """
        records = _run_cypher(cypher)
        nodes = []
        for r in records:
            node = _get_node_props(r.get('e', {}))
            et_ids = [str(x) for x in (r.get('et_ids') or []) if x is not None and str(x).strip()]
            et_names = [x for x in (r.get('et_names') or []) if x is not None and str(x).strip()]
            et_types = [x for x in (r.get('et_types') or []) if x is not None and str(x).strip()]
            et0 = et_ids[0] if et_ids else ''
            node['equipment_type_id'] = et0
            node['equipmenttypeid'] = et0
            node['equipment_type_name'] = et_names[0] if et_names else ''
            node['equipment_type_category'] = et_types[0] if et_types else ''
            node['type'] = et_names[0] if et_names else (node.get('type') or '')
            if et_ids:
                node['equipment_type_ids'] = et_ids
            nodes.append(node)
    elif node_type == 'Indicator':
        cypher = """
        MATCH (i:Indicator)
        OPTIONAL MATCH (parent:Indicator)-[:HAS]->(i)
        OPTIONAL MATCH (eq_in:Equipment)-[:CON]->(i)
        OPTIONAL MATCH (i)-[:CON]->(eq_out:Equipment)
        RETURN i,
               parent.Id AS parent_id,
               parent.Name AS parent_name,
               [x IN (collect(DISTINCT eq_in.Id) + collect(DISTINCT eq_out.Id)) WHERE x IS NOT NULL] AS equipment_ids,
               [x IN (collect(DISTINCT eq_in.Name) + collect(DISTINCT eq_out.Name)) WHERE x IS NOT NULL] AS equipment_names
        ORDER BY i.Create_time DESC
        """
        records = _run_cypher(cypher)
        nodes = []
        for r in records:
            node = _get_node_props(r.get('i', {}))
            pid = r.get('parent_id')
            pname = r.get('parent_name')
            node['parent_id'] = str(pid).strip() if pid is not None else ''
            node['parent_name'] = str(pname).strip() if pname is not None else ''
            equipment_ids = _dedupe_str_list(r.get('equipment_ids') or [])
            equipment_names = _dedupe_str_list(r.get('equipment_names') or [])
            node['equipment_ids'] = equipment_ids
            node['equipment_names'] = equipment_names
            node['equipment_id'] = equipment_ids[0] if equipment_ids else ''
            node['equipment_name'] = equipment_names[0] if equipment_names else ''
            nodes.append(node)
    else:
        if search:
            cypher = f"""
            MATCH (n:{label})
            WHERE n.Name CONTAINS $search
            RETURN n ORDER BY n.Create_time DESC
            """
            records = _run_cypher(cypher, {'search': search})
        else:
            cypher = f"MATCH (n:{label}) RETURN n ORDER BY n.Create_time DESC"
            records = _run_cypher(cypher)
        nodes = [_get_node_props(r.get('n', {})) for r in records]
    
    return jsonify({"success": True, "data": nodes, "total": len(nodes)})


@flgl_nodes_bp.route('/environments', methods=['GET'])
def list_environments():
    cypher = "MATCH (e:Environment) RETURN e.Id AS id, e.Name AS name ORDER BY e.Name"
    records = _run_cypher(cypher)
    envs = [{'id': r.get('id', ''), 'name': r.get('name', '')} for r in records]
    return jsonify({"success": True, "data": envs})


@flgl_nodes_bp.route('/equipment-tree', methods=['GET'])
def equipment_tree():
    """返回一级设备 -> 二级设备类型 -> 三级具体设备树。"""
    try:
        records = _run_cypher(
            """
            MATCH (et:EquipmentType)
            OPTIONAL MATCH (et)-[:HAS|CON]->(e:Equipment)
            WITH et, collect(DISTINCT e) AS equipments
            RETURN et, equipments
            ORDER BY et.Type, et.Name, et.Id
            """
        )
        first_level_map = {}
        for r in records:
            et_node = _get_node_props(r.get('et', {}))
            if not et_node:
                continue
            first_name = str(et_node.get('type') or '未分类').strip() or '未分类'
            second_id = str(et_node.get('id') or '').strip()
            second_item = {
                'id': second_id,
                'name': et_node.get('name') or second_id,
                'type': et_node.get('type') or '',
                'label': et_node.get('label') or '',
                'children': [],
            }
            for eq in r.get('equipments') or []:
                eq_node = _get_node_props(eq)
                if not eq_node:
                    continue
                third_id = str(eq_node.get('id') or '').strip()
                if not third_id:
                    continue
                eq_node['equipment_type_id'] = second_id
                eq_node['equipment_type_name'] = second_item['name']
                eq_node['equipment_type_category'] = first_name
                second_item['children'].append(eq_node)
            first_level_map.setdefault(first_name, []).append(second_item)

        tree = [
            {
                'id': f'level1::{name}',
                'name': name,
                'type': 'level1',
                'children': seconds,
            }
            for name, seconds in first_level_map.items()
        ]
        return jsonify({'success': True, 'data': tree})
    except Exception as e:
        logger.error(f"设备树查询失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@flgl_nodes_bp.route('/<node_type>', methods=['POST'])
def create_node(node_type):
    if node_type not in NODE_LABELS:
        return jsonify({"success": False, "error": f"未知节点类型: {node_type}"}), 400
    logger.info(f"Creating node of type: {node_type}")
    data = request.json or {}
    label = NODE_LABELS[node_type]
    fields = NODE_FIELDS[node_type]

    node_name = data.get('name', '').strip()
    if not node_name:
        return jsonify({"success": False, "error": "名称不能为空"}), 400

    check_duplicate = _run_cypher(f"MATCH (n:{label} {{Name: $name}}) RETURN n LIMIT 1", {'name': node_name})
    if check_duplicate:
        return jsonify({"success": False, "error": f"节点名称'{node_name}'已存在，请使用其他名称"}), 400
    
    node_id = _generate_id_with_apoc(node_type)
    
    user_info = _get_current_user()
    current_time = _now()

    if node_type == 'TestProject':
        type_level = _normalize_testproject_type(data.get('type', 'category'))
        label_str = str(data.get('label', '') or '').strip()
        props = {
            'Id': node_id,
            'Name': node_name,
            'Type': type_level,
            'Label': label_str,
            'Description': data.get('description', ''),
            'Status': data.get('status', '启用'),
            'User_name': data.get('user_name', user_info['userName']),
            'Create_time': data.get('create_time', current_time),
        }
    elif node_type == 'Equipment':
        type_val = data.get('type')
        if type_val is None or type_val == '':
            type_val = ''
        label_str = str(data.get('label', '') or '').strip()
        props = {
            'Id': node_id,
            'Name': node_name,
            'Type': type_val if isinstance(type_val, str) else str(type_val or ''),
            'Label': label_str,
            'Description': data.get('description', ''),
            'Status': data.get('status', '启用'),
            'User_name': data.get('user_name', user_info['userName']),
            'Create_time': data.get('create_time', current_time),
        }
    else:
        type_val = data.get('type')
        if type_val is None or type_val == '':
            type_val = data.get('label', '')
        props = {
            'Id': node_id,
            'Name': node_name,
            'Type': _normalize_indicator_type(type_val),
            'Description': data.get('description', ''),
            'Status': data.get('status', '启用'),
            'User_name': data.get('user_name', user_info['userName']),
            'Create_time': data.get('create_time', current_time),
        }

    cypher = f"CREATE (n:{label} {{{', '.join([f'{k}: ${k}' for k in props.keys()])}}}) RETURN n"
    records = _run_cypher(cypher, props)
    node = _get_node_props(records[0].get('n', {})) if records else None

    if node_type == 'TestProject' and node:
        parent_id = str(data.get('parent_id', '') or '').strip()
        _sync_testproject_parent_has(node_id, parent_id, props['Type'])
        if 'equipment_id' in data:
            _sync_incoming_con('TestProject', node_id, 'Equipment', data.get('equipment_id'))
        if 'environment_id' in data:
            _sync_incoming_con('TestProject', node_id, 'Environment', data.get('environment_id'))
    elif node_type == 'Indicator' and node:
        parent_id = str(data.get('parent_id', '') or '').strip()
        _sync_indicator_parent_has(node_id, parent_id, props['Type'])
        if 'equipment_id' in data:
            _sync_incoming_con('Indicator', node_id, 'Equipment', data.get('equipment_id'))

    log_user_action(f"分类管理(新节点API)-新增 type={node_type} id={node_id} name={node_name[:80]}")
    return jsonify({"success": True, "data": node})


def _merge_update_payload_v2(raw):
    """小写 payload 转节点属性；parent/equipment/environment id 仅用于关系同步，不写节点属性。"""
    if not raw:
        return {}
    out = {}
    for k, v in raw.items():
        lk = str(k).lower()
        if lk in ('name', 'description', 'status', 'user_name', 'create_time'):
            out[lk] = v
        if lk == 'type':
            out['type'] = v
        if lk == 'label':
            out['label'] = v
    return out


@flgl_nodes_bp.route('/<node_type>/<node_id>', methods=['PUT'])
def update_node(node_type, node_id):
    if node_type not in NODE_LABELS:
        return jsonify({"success": False, "error": f"未知节点类型: {node_type}"}), 400

    label = NODE_LABELS[node_type]
    raw = request.json or {}
    data = _merge_update_payload_v2(raw)

    check = _run_cypher(f"MATCH (n:{label} {{Id: $id}}) RETURN n LIMIT 1", {'id': node_id})
    if not check:
        return jsonify({"success": False, "error": "节点不存在"}), 404

    set_parts = []
    params = {'id': node_id}
    # description 允许置空；name/type/status 非空才写入（避免无意清空名称）
    if 'name' in data and data['name'] is not None and str(data['name']).strip() != '':
        set_parts.append('n.Name = $name')
        params['name'] = str(data['name']).strip()
    if 'type' in data and data['type'] is not None:
        set_parts.append('n.Type = $type')
        params['type'] = (
            _normalize_testproject_type(data['type'])
            if node_type == 'TestProject'
            else (
                _normalize_indicator_type(data['type'])
                if node_type == 'Indicator'
                else str(data['type'])
            )
        )
    if 'label' in data and data['label'] is not None:
        set_parts.append('n.Label = $label')
        params['label'] = str(data['label'])
    if 'description' in data and data['description'] is not None:
        set_parts.append('n.Description = $description')
        params['description'] = str(data['description'])
    if 'status' in data and data['status'] is not None and str(data['status']).strip() != '':
        set_parts.append('n.Status = $status')
        params['status'] = str(data['status']).strip()
    if 'user_name' in data and data['user_name'] is not None:
        set_parts.append('n.User_name = $user_name')
        params['user_name'] = str(data['user_name'])
    if 'create_time' in data and data['create_time'] is not None:
        set_parts.append('n.Create_time = $create_time')
        params['create_time'] = str(data['create_time'])

    if not set_parts:
        return jsonify({"success": False, "error": "无更新字段"}), 400

    cypher = f"MATCH (n:{label} {{Id: $id}}) SET {', '.join(set_parts)} RETURN n"
    records = _run_cypher(cypher, params)
    node = _get_node_props(records[0].get('n', {})) if records else None

    if node_type == 'TestProject':
        type_level = _normalize_testproject_type(raw.get('type', node.get('type', 'category')))
        if type_level == 'category':
            parent_id = ''
        elif 'parent_id' in raw:
            parent_id = str(raw.get('parent_id', '') or '').strip()
        else:
            pr = _run_cypher(
                "MATCH (parent:TestProject)-[:HAS]->(c:TestProject {Id: $id}) RETURN parent.Id AS pid LIMIT 1",
                {'id': node_id},
            )
            parent_id = str(pr[0].get('pid') or '').strip() if pr and pr[0].get('pid') is not None else ''
        _sync_testproject_parent_has(node_id, parent_id, type_level)
        if 'equipment_id' in raw:
            _sync_incoming_con('TestProject', node_id, 'Equipment', raw.get('equipment_id'))
        if 'environment_id' in raw:
            _sync_incoming_con('TestProject', node_id, 'Environment', raw.get('environment_id'))
    elif node_type == 'Indicator':
        type_level = _normalize_indicator_type(raw.get('type', node.get('type', 'category')))
        if type_level == 'category':
            parent_id = ''
        elif 'parent_id' in raw:
            parent_id = str(raw.get('parent_id', '') or '').strip()
        else:
            pr = _run_cypher(
                "MATCH (parent:Indicator)-[:HAS]->(c:Indicator {Id: $id}) RETURN parent.Id AS pid LIMIT 1",
                {'id': node_id},
            )
            parent_id = str(pr[0].get('pid') or '').strip() if pr and pr[0].get('pid') is not None else ''
        _sync_indicator_parent_has(node_id, parent_id, type_level)
        if 'equipment_id' in raw:
            _sync_incoming_con('Indicator', node_id, 'Equipment', raw.get('equipment_id'))

    log_user_action(f"分类管理(新节点API)-修改 type={node_type} id={node_id}")
    return jsonify({"success": True, "data": node})


@flgl_nodes_bp.route('/<node_type>/<node_id>', methods=['DELETE'])
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
    log_user_action(f"分类管理(新节点API)-删除 type={node_type} id={node_id}")
    return jsonify({"success": True, "data": None})


@flgl_nodes_bp.route('/<node_type>/<node_id>/relations', methods=['GET'])
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