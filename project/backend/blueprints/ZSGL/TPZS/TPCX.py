from flask import Blueprint, request, jsonify, current_app
import json
import traceback
import logging
from urllib.parse import quote, unquote
from datetime import datetime, date
from neo4j.time import DateTime as Neo4jDateTime
from utils.neo4j_utils import search_standards, get_standard_summary

logger = logging.getLogger(__name__)


def _convert_value(value):
    if isinstance(value, (datetime, date, Neo4jDateTime)):
        return value.isoformat()
    return value


def safe_node_properties(node):
    props = dict(node)
    for key, value in props.items():
        props[key] = _convert_value(value)
    return props


def safe_json_dumps(obj, **kwargs):
    def _convert_recursive(item):
        if isinstance(item, (datetime, date, Neo4jDateTime)):
            return item.isoformat()
        if isinstance(item, dict):
            return {k: _convert_recursive(v) for k, v in item.items()}
        if isinstance(item, list):
            return [_convert_recursive(x) for x in item]
        return item
    return json.dumps(_convert_recursive(obj), **kwargs)


# 装备试验知识图谱（ZSTPZS）允许的节点标签
TPZS_GRAPH_LABELS = frozenset({
    'EquipmentType', 'Equipment', 'Environment', 'TestProject', 'Indicator', 'TestMethod', 'File',
})

# ZSTPZS 一级视图：按 EquipmentType.Type 聚合的虚拟节点
TPZS_TYPE_CATEGORY_PREFIX = 'tpzs_type:'
TPZS_TYPE_CATEGORY_GROUP = 'EquipmentTypeKind'
TPZS_TYPE_UNCLASSIFIED = '未分类'


def _neo_graph_node_id(node) -> str:
    """与 Neo5+ Cypher elementId(n) 对齐，避免 id(n) 弃用告警；旧驱动仍回退 internal id。"""
    eid = getattr(node, "element_id", None)
    if eid is not None and str(eid).strip() != "":
        return str(eid)
    return str(node.id)


def _neo_graph_rel_id(rel) -> str:
    eid = getattr(rel, "element_id", None)
    if eid is not None and str(eid).strip() != "":
        return str(eid)
    return str(rel.id)


def _vis_node_dict(node):
    labels = list(node.labels)
    props = safe_node_properties(node)
    text = (
        props.get('Name') or props.get('name') or
        props.get('Label') or props.get('label') or
        (labels[0] if labels else 'Node')
    )
    return {
        'id': _neo_graph_node_id(node),
        'label': text,
        'group': labels[0] if labels else 'Node',
        'title': safe_json_dumps(props, ensure_ascii=False),
        'properties': props,
    }


def _tpzs_normalize_type_value(raw) -> str:
    t = str(raw or '').strip()
    return t if t else TPZS_TYPE_UNCLASSIFIED


def _tpzs_type_category_node_id(type_value: str) -> str:
    return f'{TPZS_TYPE_CATEGORY_PREFIX}{quote(type_value, safe="")}'


def _tpzs_parse_type_category_node_id(node_id: str):
    if not node_id or not str(node_id).startswith(TPZS_TYPE_CATEGORY_PREFIX):
        return None
    encoded = str(node_id)[len(TPZS_TYPE_CATEGORY_PREFIX):]
    return _tpzs_normalize_type_value(unquote(encoded))


def _tpzs_vis_type_category_node(type_value: str, member_count: int = 0):
    display = _tpzs_normalize_type_value(type_value)
    props = {
        'Type': display,
        'MemberCount': member_count,
        'Virtual': True,
    }
    return {
        'id': _tpzs_type_category_node_id(display),
        'label': display,
        'group': TPZS_TYPE_CATEGORY_GROUP,
        'title': safe_json_dumps(props, ensure_ascii=False),
        'properties': props,
    }


def _tpzs_fetch_equipment_types_by_category(driver, type_value: str, limit: int = 500):
    from utils.neo4j_flgl_status import flgl_status_cypher_param

    display = _tpzs_normalize_type_value(type_value)
    stp = flgl_status_cypher_param()
    cypher = """
        MATCH (et:EquipmentType)
        WHERE (et).Status IN $flgl_enabled_status
          AND trim(toString(coalesce(et.Type, ''))) = $type_raw
        RETURN et
        ORDER BY et.Name, et.Id
        LIMIT $limit
    """
    type_raw = '' if display == TPZS_TYPE_UNCLASSIFIED else display
    nodes = []
    with driver.session() as session:
        for record in session.run(cypher, {**stp, 'type_raw': type_raw, 'limit': limit}):
            node = record.get('et')
            if node:
                nodes.append(_vis_node_dict(node))
    return nodes


def _tpzs_append_kind_has_edges(category_id: str, et_nodes, edges, edge_set, edge_id_set):
    """一级分类 -> 设备类型：虚拟 HAS 包含关系。"""
    for et in et_nodes:
        et_id = et['id']
        edge_key = (category_id, et_id, 'HAS')
        if edge_key in edge_set:
            continue
        edge_set.add(edge_key)
        vid = f'virt_has_{category_id}_{et_id}'
        if vid in edge_id_set:
            vid = f'{vid}_{len(edges)}'
        edge_id_set.add(vid)
        edges.append({
            'id': vid,
            'from': category_id,
            'to': et_id,
            'label': 'HAS',
            'title': safe_json_dumps({'virtual': True, 'relation': 'HAS'}, ensure_ascii=False),
            'properties': {'virtual': True},
        })


def _tpzs_explore_type_category(driver, type_value: str, max_depth: int, node_types, edge_types, limit: int):
    """从 Type 聚合虚拟节点展开：一级分类 + 同 Type 设备类型 + 虚拟 HAS；步长>1 时再探索邻居。"""
    display = _tpzs_normalize_type_value(type_value)
    category_id = _tpzs_type_category_node_id(display)
    et_nodes = _tpzs_fetch_equipment_types_by_category(driver, type_value, limit=limit)

    nodes_map = {category_id: _tpzs_vis_type_category_node(display, len(et_nodes))}
    for et in et_nodes:
        nodes_map[et['id']] = et

    edges = []
    edge_set = set()
    edge_id_set = set()
    include_has = not edge_types or 'HAS' in edge_types
    if include_has:
        _tpzs_append_kind_has_edges(category_id, et_nodes, edges, edge_set, edge_id_set)

    if max_depth > 1 and et_nodes:
        edge_path_filter = ""
        end_label_filter = ""
        params = {
            'type_raw': '' if display == TPZS_TYPE_UNCLASSIFIED else display,
            'path_limit': limit,
            'max_depth': max_depth,
        }
        from utils.neo4j_flgl_status import flgl_status_cypher_param
        params.update(flgl_status_cypher_param())

        if edge_types:
            edge_path_filter = " AND ALL(rel IN relationships(p) WHERE type(rel) IN $edge_types)"
            params['edge_types'] = edge_types
        if node_types:
            end_label_filter = " AND ANY(l IN labels(end) WHERE l IN $node_types)"
            params['node_types'] = node_types

        query = f"""
            MATCH (start:EquipmentType)
            WHERE (start).Status IN $flgl_enabled_status
              AND trim(toString(coalesce(start.Type, ''))) = $type_raw
            MATCH p = (start)-[*1..{max_depth}]-(end)
            WHERE 1=1 {edge_path_filter} {end_label_filter}
            WITH p
            LIMIT $path_limit
            UNWIND relationships(p) AS rel
            WITH DISTINCT rel
            RETURN startNode(rel) AS ns, endNode(rel) AS ne, rel
        """
        with driver.session() as session:
            for record in session.run(query, **params):
                try:
                    ns = record.get('ns')
                    ne = record.get('ne')
                    rel = record.get('rel')
                    if ns is None or ne is None or rel is None:
                        continue
                    _merge_triple(nodes_map, edges, edge_set, edge_id_set, ns, rel, ne)
                except Exception:
                    continue

    if node_types:
        allowed = set(node_types)
        nodes_map = {
            k: v for k, v in nodes_map.items()
            if v.get('group') in allowed or k == category_id
        }
        kept = set(nodes_map.keys())
        edges = [e for e in edges if e['from'] in kept and e['to'] in kept]

    return list(nodes_map.values()), edges


def _merge_triple(nodes_map, edges, edge_set, edge_id_set, node_start, rel, node_end):
    start_id = _neo_graph_node_id(node_start)
    if start_id not in nodes_map:
        nodes_map[start_id] = _vis_node_dict(node_start)

    end_id = _neo_graph_node_id(node_end)
    if end_id not in nodes_map:
        nodes_map[end_id] = _vis_node_dict(node_end)

    edge_key = (start_id, end_id, rel.type)
    if edge_key in edge_set:
        return
    edge_set.add(edge_key)

    raw_edge_id = _neo_graph_rel_id(rel)
    if raw_edge_id in edge_id_set:
        unique_edge_id = f"{raw_edge_id}_{start_id}_{end_id}"
    else:
        unique_edge_id = raw_edge_id
    edge_id_set.add(unique_edge_id)

    edges.append({
        'id': unique_edge_id,
        'from': start_id,
        'to': end_id,
        'label': rel.type,
        'title': safe_json_dumps(dict(rel), ensure_ascii=False),
        'properties': dict(rel),
    })


kg_bp = Blueprint('knowledge_graph', __name__, url_prefix='/api/kg')

@kg_bp.route('/search', methods=['POST'])
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

@kg_bp.route('/proxy', methods=['POST'])
def neo4j_proxy():
    """Neo4j代理接口 - 返回vis-network所需格式"""
    try:
        data = request.json
        
        keyword = data.get('keyword')
        node_type = data.get('nodeType')
        relationship_type = data.get('relationshipType')
        limit = data.get('limit', 100)
        
        if not keyword:
            return jsonify({
                'success': False,
                'error': 'keyword参数不能为空'
            }), 400

        try:
            limit = int(limit)
            if limit <= 0:
                limit = 100
        except (ValueError, TypeError):
            limit = 100
        
        driver = current_app.config['NEO4J_DRIVER']
        if not driver:
            return jsonify({
                'success': False,
                'error': 'Neo4j驱动不可用'
            }), 500
        
        query = """
        MATCH (start:Standard)-[r]-(end)
        WHERE toLower(start.name) CONTAINS toLower($keyword)
        """

        # 如果你还想限制结束节点的类型
        if node_type and node_type != "all":
            query += f"""
            AND (end:{node_type})
            """

        if relationship_type:
            query += """
            AND type(r) = $relationship_type
            """
            
        query += """
        RETURN start, r, end
        LIMIT $limit
        """

        params = {
            "keyword": keyword,
            "limit": limit
        }
        
        if node_type and node_type != "all":
            params["node_type"] = node_type
        if relationship_type:
            params["relationship_type"] = relationship_type

        with driver.session() as session:
            result = session.run(query, **params)
            records = list(result)
            logger.info(f"获取到{len(records)}条原始记录")
            
            nodes_map = {}
            edges = []
            edge_set = set()
            edge_id_set = set()

            for idx, record in enumerate(records):
                try:
                    node_start = record['start']
                    node_end = record['end']
                    rel = record['r']

                    start_id = str(node_start.id)
                    if start_id not in nodes_map:
                        labels = list(node_start.labels)
                        nodes_map[start_id] = {
                            'id': start_id,
                            'label': node_start.get('name', labels[0] if labels else 'Node'),
                            'group': labels[0] if labels else 'Node',
                            'title': safe_json_dumps(safe_node_properties(node_start), ensure_ascii=False),
                            'properties': safe_node_properties(node_start)
                        }

                    end_id = str(node_end.id)
                    if end_id not in nodes_map:
                        labels = list(node_end.labels)
                        nodes_map[end_id] = {
                            'id': end_id,
                            'label': node_end.get('name', labels[0] if labels else 'Node'),
                            'group': labels[0] if labels else 'Node',
                            'title': safe_json_dumps(safe_node_properties(node_end), ensure_ascii=False),
                            'properties': safe_node_properties(node_end)
                        }

                    edge_key = (start_id, end_id, rel.type)
                    if edge_key in edge_set:
                        logger.debug(f"跳过重复边: {edge_key}")
                        continue
                    edge_set.add(edge_key)

                    raw_edge_id = str(rel.id)
                    if raw_edge_id in edge_id_set:
                        unique_edge_id = f"{raw_edge_id}_{start_id}_{end_id}"
                    else:
                        unique_edge_id = raw_edge_id
                    edge_id_set.add(unique_edge_id)

                    edges.append({
                        'id': unique_edge_id,
                        'from': start_id,
                        'to': end_id,
                        'label': rel.type,
                        'title': safe_json_dumps(dict(rel), ensure_ascii=False),
                        'properties': dict(rel)
                    })

                except KeyError as e:
                    logger.warning(f"[第{idx + 1}条记录]字段缺失: {e}")
                    continue

            nodes = list(nodes_map.values())
            logger.info(f"处理后数据 - 节点数: {len(nodes)}, 边数: {len(edges)}")
            # print(nodes)
            return jsonify({
                'success': True,
                'nodes': nodes,
                'edges': edges,
                'count': {
                    'nodes': len(nodes),
                    'edges': len(edges)
                }
            })

    except Exception as e:
        logger.error(f"Neo4j代理接口错误: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@kg_bp.route('/query_by_standard', methods=['POST'])
def query_by_standard():
    """
    根据标准和节点类型查询相关节点
    """
    try:
        data = request.json
        logger.info(f"收到标准查询请求: {data}")
        
        if not request.is_json:
            return jsonify({
                "success": False,
                "error": "请求必须是JSON格式",
                "code": 400
            }), 400
        
        # 检查必需参数
        required_fields = ['queryType', 'testItemId']
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            return jsonify({
                "success": False,
                "error": f"缺少必要参数: {', '.join(missing_fields)}",
                "code": 400
            }), 400
            
        query_type = data.get('queryType')
        test_item_id = data.get('testItemId')
        clause_name = data.get('name')  # 默认为"试验过程"
        
        logger.info(f"查询参数 - queryType: {query_type}, testItemId: {test_item_id}, clauseName: {clause_name}")
        
        driver = current_app.config['NEO4J_DRIVER']
        if not driver:
            return jsonify({
                "success": False,
                "error": "Neo4j驱动不可用",
                "code": 500
            }), 500
        
        # 根据 query_type 构建不同的查询
        query = ""
        params = {
            "standard_id": test_item_id,
            "clause_name": clause_name
        }
        
        if query_type == "Clause":
            # 查找试验过程节点的下级节点
            query = """
            // 找到标准节点和指定的Clause节点
            MATCH (standard {id: $standard_id})--(clause:Clause {name: $clause_name})
            
            // 查找该Clause节点的所有下级节点（1-3级深度）
            MATCH path = (clause)-[*1..3]->(sub_node)
            
            RETURN DISTINCT 
                sub_node,
                length(path) as depth,
                labels(sub_node) as sub_labels
            ORDER BY 
                CASE 
                    WHEN sub_node.orderNumber IS NOT NULL THEN sub_node.orderNumber
                    WHEN sub_node.number IS NOT NULL THEN sub_node.number
                    WHEN sub_node.name IS NOT NULL THEN sub_node.name
                    ELSE toString(id(sub_node))
                END
            LIMIT 200
            """
            
        elif query_type == "TestType":
            # 查找与标准相关的TestType节点
            query = """
            MATCH (standard {id: $standard_id})--(test:TestType)
            RETURN DISTINCT test
            ORDER BY test.name
            LIMIT 100
            """
            
        elif query_type == "EquipmentCategory":
            # 查找与标准相关的EquipmentCategory节点
            query = """
            MATCH (standard {id: $standard_id})--(equip:EquipmentCategory)
            RETURN DISTINCT equip
            ORDER BY equip.name
            LIMIT 100
            """
            
        else:
            # 通用的关系查找
            query = f"""
            MATCH (standard {{id: $standard_id}})--(related:{query_type})
            RETURN DISTINCT related
            ORDER BY related.name
            LIMIT 100
            """
        
        logger.info(f"执行查询: {query}")
        logger.info(f"查询参数: {params}")
        
        with driver.session() as session:
            result = session.run(query, **params)
            records = list(result)
            
            # 处理结果
            nodes = []
            for record in records:
                # 根据不同的查询类型获取节点
                node_key = 'sub_node' if query_type == "Clause" else \
                          'test' if query_type == "TestType" else \
                          'equip' if query_type == "EquipmentCategory" else 'related'
                
                node = record.get(node_key)
                if node:
                    node_data = {
                        'id': str(node.id),
                        'name': node.get('name', ''),
                        'title': node.get('title', ''),
                        'number': node.get('number', ''),
                        'content': node.get('content', ''),
                        'definition': node.get('definition', ''),
                        'labels': list(node.labels),
                        'properties': safe_node_properties(node)
                    }
                    
                    # 如果是Clause查询，添加深度信息
                    if query_type == "Clause" and 'depth' in record:
                        node_data['depth'] = record['depth']
                    if query_type == "Clause" and 'sub_labels' in record:
                        node_data['labels'] = record['sub_labels']
                    
                    nodes.append(node_data)
            
            logger.info(f"查询完成，找到 {len(nodes)} 个节点")
            
            return jsonify({
                "success": True,
                "data": {
                    "nodes": nodes,
                    "queryType": query_type,
                    "standardId": test_item_id,
                    "clauseName": clause_name if query_type == "Clause" else None,
                    "count": len(nodes)
                },
                "message": f"成功找到 {len(nodes)} 个{query_type}节点"
            })
    
    except Exception as e:
        logger.error(f"标准查询出错: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}",
            "code": 500,
            "traceback": traceback.format_exc() if current_app.debug else None
        }), 500

@kg_bp.route('/all_nodes', methods=['POST'])
def neo4j_all_nodes():
    """
    查询所有节点及其关系，返回 vis-network 所需格式
    """
    try:
        data = request.get_json(silent=True) or {}
        limit = data.get('limit', 500)
        
        driver = current_app.config['NEO4J_DRIVER']
        if not driver:
            return jsonify({
                'success': False,
                'error': 'Neo4j驱动不可用'
            }), 500
            
        with driver.session() as session:
            query = """
            MATCH (start)-[r]-(end)
            RETURN start, r, end
            LIMIT $limit
            """
            params = {"limit": limit}
            result = session.run(query, **params)
            records = list(result)
            nodes_map = {}
            edges = []
            edge_set = set()
            edge_id_set = set()

            for idx, record in enumerate(records):
                try:
                    node_start = record['start']
                    node_end = record['end']
                    rel = record['r']

                    start_id = str(node_start.id)
                    if start_id not in nodes_map:
                        labels = list(node_start.labels)
                        nodes_map[start_id] = {
                            'id': start_id,
                            'label': node_start.get('name', labels[0] if labels else 'Node'),
                            'group': labels[0] if labels else 'Node',
                            'title': safe_json_dumps(safe_node_properties(node_start), ensure_ascii=False),
                            'properties': safe_node_properties(node_start)
                        }

                    end_id = str(node_end.id)
                    if end_id not in nodes_map:
                        labels = list(node_end.labels)
                        nodes_map[end_id] = {
                            'id': end_id,
                            'label': node_end.get('name', labels[0] if labels else 'Node'),
                            'group': labels[0] if labels else 'Node',
                            'title': safe_json_dumps(safe_node_properties(node_end), ensure_ascii=False),
                            'properties': safe_node_properties(node_end)
                        }

                    edge_key = (start_id, end_id, rel.type)
                    if edge_key in edge_set:
                        continue
                    edge_set.add(edge_key)

                    raw_edge_id = str(rel.id)
                    if raw_edge_id in edge_id_set:
                        unique_edge_id = f"{raw_edge_id}_{start_id}_{end_id}"
                    else:
                        unique_edge_id = raw_edge_id
                    edge_id_set.add(unique_edge_id)

                    edges.append({
                        'id': unique_edge_id,
                        'from': start_id,
                        'to': end_id,
                        'label': rel.type,
                        'title': safe_json_dumps(dict(rel), ensure_ascii=False),
                        'properties': dict(rel)
                    })

                except KeyError as e:
                    continue

            nodes = list(nodes_map.values())
            return jsonify({
                'success': True,
                'nodes': nodes,
                'edges': edges,
                'count': {
                    'nodes': len(nodes),
                    'edges': len(edges)
                }
            })
    except Exception as e:
        logger.error(f"查询所有节点失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@kg_bp.route('/filter', methods=['POST'])
def filter_graph():
    """
    根据节点标签和关系类型过滤图谱
    请求体: {"node_labels": ["Standard", ...], "rel_types": ["HAS_PART", ...], "limit": 500}
    返回: 过滤后的节点和关系列表
    """
    try:
        data = request.get_json() or {}
        node_labels = data.get('node_labels', [])
        rel_types = data.get('rel_types', [])
        limit = data.get('limit', 500)

        driver = current_app.config['NEO4J_DRIVER']
        if not driver:
            return jsonify({'success': False, 'error': 'Neo4j驱动不可用'}), 500

        with driver.session() as session:
            nodes_map = {}
            edges = []

            # 1. 根据节点标签查询节点
            if node_labels:
                # 构建节点标签条件，例如: n:Standard OR n:Clause
                label_conditions = " OR ".join([f"n:{label}" for label in node_labels])
                node_query = f"MATCH (n) WHERE {label_conditions} RETURN n LIMIT $limit"
                result_nodes = session.run(node_query, limit=limit)
            else:
                node_query = "MATCH (n) RETURN n LIMIT $limit"
                result_nodes = session.run(node_query, limit=limit)

            for record in result_nodes:
                node = record['n']
                node_id = str(node.id)
                labels = list(node.labels)
                nodes_map[node_id] = {
                    'id': node_id,
                    'label': node.get('name', labels[0] if labels else 'Node'),
                    'group': labels[0] if labels else 'Node',
                    'title': safe_json_dumps(safe_node_properties(node), ensure_ascii=False),
                    'properties': safe_node_properties(node)
                }

            # 2. 如果指定了关系类型，则查询这些节点间的关系（否则查询所有关系）
            if rel_types:
                rel_condition = " AND type(r) IN $rel_types"
            else:
                rel_condition = ""

            if nodes_map:
                # 将节点ID转换为整数列表（因为Neo4j内部ID是整数）
                node_int_ids = [int(nid) for nid in nodes_map.keys()]
                rel_query = f"""
                MATCH (a)-[r]->(b)
                WHERE id(a) IN $node_ids AND id(b) IN $node_ids {rel_condition}
                RETURN r, a, b
                LIMIT $limit
                """
                result_rels = session.run(rel_query, node_ids=node_int_ids, rel_types=rel_types, limit=limit)
            else:
                result_rels = []

            edge_set = set()
            edge_id_set = set()
            for record in result_rels:
                start = record['a']
                end = record['b']
                rel = record['r']
                start_id = str(start.id)
                end_id = str(end.id)

                # 确保两端节点在nodes_map中（如果之前漏掉了，补上）
                for node, node_id in [(start, start_id), (end, end_id)]:
                    if node_id not in nodes_map:
                        labels = list(node.labels)
                        nodes_map[node_id] = {
                            'id': node_id,
                            'label': node.get('name', labels[0] if labels else 'Node'),
                            'group': labels[0] if labels else 'Node',
                            'title': safe_json_dumps(safe_node_properties(node), ensure_ascii=False),
                            'properties': safe_node_properties(node)
                        }

                edge_key = (start_id, end_id, rel.type)
                if edge_key in edge_set:
                    continue
                edge_set.add(edge_key)

                raw_edge_id = str(rel.id)
                if raw_edge_id in edge_id_set:
                    unique_edge_id = f"{raw_edge_id}_{start_id}_{end_id}"
                else:
                    unique_edge_id = raw_edge_id
                edge_id_set.add(unique_edge_id)

                edges.append({
                    'id': unique_edge_id,
                    'from': start_id,
                    'to': end_id,
                    'label': rel.type,
                    'title': safe_json_dumps(dict(rel), ensure_ascii=False),
                    'properties': dict(rel)
                })

            return jsonify({
                'success': True,
                'nodes': list(nodes_map.values()),
                'edges': edges,
                'count': {'nodes': len(nodes_map), 'edges': len(edges)}
            })

    except Exception as e:
        current_app.logger.error(f"过滤图谱失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@kg_bp.route('/standard/summary', methods=['GET'])
def get_standard_summary_api():
    """标准摘要生成接口"""
    try:
        standard_id = request.args.get('standardId')
        if not standard_id:
            return jsonify({
                "success": False,
                "error": "缺少standardId参数",
                "code": 400
            }), 400
            
        driver = current_app.config['NEO4J_DRIVER']
        if not driver:
            logger.error("Neo4j 驱动未初始化")
            return jsonify({
                "success": False,
                "error": "数据库连接不可用",
                "code": 500
            }), 500

        summary = get_standard_summary(driver, standard_id)
        
        if summary:
            return jsonify({
                "success": True,
                "data": summary
            })
        else:
            return jsonify({
                "success": False,
                "error": f"未找到标准号 {standard_id}",
                "code": 404
            }), 404
                
    except Exception as e:
        logger.error(f"获取标准摘要失败: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "error": "服务器内部错误",
            "code": 500
        }), 500


def _tpzs_resolve_biz_node_vis(driver, biz_id: str, group: str, label: str, props: dict) -> dict:
    """按业务 Id 查找 Neo4j 节点，返回 vis 节点 dict（elementId 优先）。"""
    label_safe = group if group in TPZS_GRAPH_LABELS else 'Node'
    with driver.session() as session:
        rec = session.run(
            f"""
            MATCH (n:{label_safe})
            WHERE n.Id = $id OR toString(n.Id) = $id
            RETURN n LIMIT 1
            """,
            id=str(biz_id),
        ).single()
        if rec and rec.get('n'):
            return _vis_node_dict(rec['n'])
    return {
        'id': str(biz_id),
        'label': label or str(biz_id),
        'group': group if group in TPZS_GRAPH_LABELS else 'Node',
        'properties': props or {},
    }


def _tpzs_merge_ssyq_equipment_graph(driver, biz_eq_id: str, nodes_map, edges, edge_set, edge_id_set):
    """将 SSYQ 设备子图（含无父级项目、指标/项目 HAS）合并进 ZSTPZS 探索结果。"""
    if not biz_eq_id or not driver:
        return
    try:
        from blueprints.GLCX.SSYQ.routes import _build_full_graph, _get_equipment_data

        rd = _get_equipment_data(str(biz_eq_id).strip())
        if not rd or not rd.get('equipment'):
            return
        eq = rd['equipment']
        table_row = {
            'type': 'equipment',
            'equipmentId': eq.get('id', biz_eq_id),
            'equipmentName': eq.get('name', biz_eq_id),
            'projectsByEnv': rd.get('projectsByEnv') or {},
            'indicators': rd.get('indicators') or [],
            'equipments': [eq],
        }
        built = _build_full_graph({}, [table_row])
        biz_to_gid: dict[str, str] = {}
        for n in built.get('nodes') or []:
            vis = _tpzs_resolve_biz_node_vis(
                driver,
                n.get('id', ''),
                n.get('group', 'Node'),
                n.get('label', ''),
                n.get('properties') or {},
            )
            gid = vis['id']
            biz_to_gid[str(n.get('id', ''))] = gid
            if gid not in nodes_map:
                nodes_map[gid] = vis
        for e in built.get('edges') or []:
            from_gid = biz_to_gid.get(str(e.get('from', '')), str(e.get('from', '')))
            to_gid = biz_to_gid.get(str(e.get('to', '')), str(e.get('to', '')))
            rel_type = str(e.get('label') or 'CON')
            edge_key = (from_gid, to_gid, rel_type)
            if edge_key in edge_set:
                continue
            edge_set.add(edge_key)
            edge_id = str(e.get('id') or f"{from_gid}__{to_gid}__{rel_type}")
            if edge_id in edge_id_set:
                continue
            edge_id_set.add(edge_id)
            edges.append({
                'id': edge_id,
                'from': from_gid,
                'to': to_gid,
                'label': rel_type,
            })
    except Exception as ex:
        logger.warning("merge ssyq equipment graph failed: %s", ex)


@kg_bp.route('/explore', methods=['POST'])
def explore_node():
    """
    从中心节点按步长展开子图。node_id 支持 Neo4j elementId、业务 Id（start.Id）或内部 id 字符串。
    node_types / edge_types 为空时不限制对应维度（与前端「留空表示全部」一致）。
    """
    try:
        data = request.get_json() or {}
        node_id = str(data.get('node_id', '') or '').strip()
        max_depth = min(int(data.get('max_depth', 1)), 5)
        node_types = data.get('node_types') or []
        edge_types = data.get('edge_types') or []
        if isinstance(node_types, str):
            node_types = [node_types]
        if isinstance(edge_types, str):
            edge_types = [edge_types]
        node_types = [str(x) for x in node_types if x]
        edge_types = [str(x) for x in edge_types if x]
        try:
            limit = int(data.get('limit', 200))
            if limit <= 0:
                limit = 200
        except (ValueError, TypeError):
            limit = 200

        driver = current_app.config['NEO4J_DRIVER']
        if not driver:
            return jsonify({'success': False, 'error': 'Neo4j驱动不可用'}), 500

        if not node_id:
            return jsonify({'success': False, 'error': 'node_id不能为空'}), 400

        type_value = _tpzs_parse_type_category_node_id(node_id)
        if type_value is not None:
            nodes, edges = _tpzs_explore_type_category(
                driver, type_value, max_depth, node_types, edge_types, limit,
            )
            return jsonify({
                'success': True,
                'nodes': nodes,
                'edges': edges,
                'count': {'nodes': len(nodes), 'edges': len(edges)},
            })

        edge_path_filter = ""
        end_label_filter = ""
        params = {'node_id': node_id, 'path_limit': limit, 'max_depth': max_depth}
        if edge_types:
            edge_path_filter = " AND ALL(rel IN relationships(p) WHERE type(rel) IN $edge_types)"
            params['edge_types'] = edge_types
        if node_types:
            end_label_filter = " AND ANY(l IN labels(end) WHERE l IN $node_types)"
            params['node_types'] = node_types

        start_match = """
        MATCH (start)
        WHERE elementId(start) = $node_id
           OR (start.Id IS NOT NULL AND toString(start.Id) = $node_id)
        RETURN start
        LIMIT 1
        """
        path_query = f"""
        MATCH (start)
        WHERE elementId(start) = $node_id
           OR (start.Id IS NOT NULL AND toString(start.Id) = $node_id)
        WITH start
        LIMIT 1
        MATCH p = (start)-[*1..{max_depth}]-(end)
        WHERE 1=1 {edge_path_filter} {end_label_filter}
        WITH p
        LIMIT $path_limit
        UNWIND relationships(p) AS rel
        WITH DISTINCT rel
        RETURN startNode(rel) AS ns, endNode(rel) AS ne, rel
        """

        with driver.session() as session:
            start_rec = session.run(start_match, node_id=node_id).single()
            if not start_rec or not start_rec.get('start'):
                return jsonify({'success': False, 'error': '未找到起始节点'}), 404

            start = start_rec['start']
            start_labels = list(start.labels)
            nodes_map = {}
            edges = []
            edge_set = set()
            edge_id_set = set()
            category_id = None

            if 'EquipmentType' in start_labels:
                props = dict(start)
                type_raw = str(props.get('Type') or props.get('type') or '').strip()
                display = _tpzs_normalize_type_value(type_raw)
                category_id = _tpzs_type_category_node_id(display)
                et_nodes = _tpzs_fetch_equipment_types_by_category(driver, display, limit)
                nodes_map[category_id] = _tpzs_vis_type_category_node(display, len(et_nodes))
                for et in et_nodes:
                    nodes_map[et['id']] = et
                if not edge_types or 'HAS' in edge_types:
                    _tpzs_append_kind_has_edges(category_id, et_nodes, edges, edge_set, edge_id_set)

            for record in session.run(path_query, **params):
                try:
                    ns = record.get('ns')
                    ne = record.get('ne')
                    rel = record.get('rel')
                    if ns is None or ne is None or rel is None:
                        continue
                    _merge_triple(nodes_map, edges, edge_set, edge_id_set, ns, rel, ne)
                except Exception:
                    continue

            if 'Equipment' in start_labels:
                start_props = dict(start)
                biz_eq_id = str(start_props.get('Id') or start_props.get('id') or '').strip()
                if biz_eq_id:
                    _tpzs_merge_ssyq_equipment_graph(
                        driver, biz_eq_id, nodes_map, edges, edge_set, edge_id_set,
                    )

            if node_types:
                allowed = set(node_types)
                if category_id:
                    nodes_map = {
                        k: v for k, v in nodes_map.items()
                        if v.get('group') in allowed or k == category_id
                    }
                else:
                    nodes_map = {k: v for k, v in nodes_map.items() if v.get('group') in allowed}
                kept = set(nodes_map.keys())
                edges = [e for e in edges if e['from'] in kept and e['to'] in kept]

            return jsonify({
                'success': True,
                'nodes': list(nodes_map.values()),
                'edges': edges,
                'count': {'nodes': len(nodes_map), 'edges': len(edges)},
            })

    except Exception as e:
        logger.error(f"探索节点失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


def _tpzs_normalize_indicator_node(x):
    """保留指标 type / children / parent，供 EnvironmentTable 与图谱 HAS 补边。"""
    if not isinstance(x, dict):
        return None
    out = {
        'id': str(x.get('id', '')),
        'name': str(x.get('name', '') or ''),
        'description': str(x.get('description', '') or ''),
    }
    if x.get('type') is not None:
        out['type'] = x.get('type')
    if x.get('parent_id'):
        out['parent_id'] = str(x.get('parent_id'))
    if x.get('parent_name'):
        out['parent_name'] = str(x.get('parent_name'))
    children = x.get('children')
    if children:
        norm_children = [_tpzs_normalize_indicator_node(c) for c in children]
        out['children'] = [c for c in norm_children if c]
    return out if out.get('id') else None


def _tpzs_env_table_row_from_ssyq(row_data):
    """将 SSYQ._get_equipment_data 结果转为前端 EnvironmentTable 行结构。"""
    if not row_data or not row_data.get('equipment'):
        return None
    eq = row_data['equipment']
    eq_id = eq.get('id', '')
    inds = row_data.get('indicators') or []
    norm_ind = []
    for x in inds:
        item = _tpzs_normalize_indicator_node(x)
        if item:
            norm_ind.append(item)
    return {
        'equipmentTypeId': eq_id,
        'equipmentTypeName': eq.get('name', '') or eq_id,
        'projectsByEnv': row_data.get('projectsByEnv') or {},
        'indicators': norm_ind,
    }


@kg_bp.route('/tpzs_node_panel', methods=['POST'])
def tpzs_node_panel():
    """
    ZSTPZS 节点抽屉：按节点类型返回 EnvironmentTable 数据源、设备类型下设备列表或环境属性字段。
    请求体: { "node_id": 图谱节点 id(elementId 或业务 Id), "node_group": 节点类型标签 }
    """
    try:
        data = request.get_json(silent=True) or {}
        graph_node_id = str(data.get('node_id') or data.get('graph_node_id') or '').strip()
        node_group = str(data.get('node_group') or data.get('group') or '').strip()

        if not graph_node_id:
            return jsonify({'success': False, 'error': 'node_id 不能为空'}), 400

        type_category = _tpzs_parse_type_category_node_id(graph_node_id)
        if type_category is not None or node_group == TPZS_TYPE_CATEGORY_GROUP:
            driver = current_app.config.get('NEO4J_DRIVER')
            if not driver:
                return jsonify({'success': False, 'error': 'Neo4j驱动不可用'}), 500
            tv = type_category if type_category is not None else _tpzs_normalize_type_value('')
            et_nodes = _tpzs_fetch_equipment_types_by_category(driver, tv, limit=500)
            items = []
            for n in et_nodes:
                props = n.get('properties') or {}
                biz = props.get('Id') or props.get('id') or ''
                items.append({
                    'id': str(biz) if biz else n.get('id', ''),
                    'name': n.get('label') or str(biz),
                    'graphId': n.get('id', ''),
                })
            return jsonify({
                'success': True,
                'panel': 'equipment_type_kind_list',
                'equipmentTypes': items,
                'typeCategory': tv,
            })

        driver = current_app.config.get('NEO4J_DRIVER')
        if not driver:
            return jsonify({'success': False, 'error': 'Neo4j驱动不可用'}), 500

        from blueprints.GLCX.SSYQ.routes import (
            _get_equipment_data,
            _get_project_data_with_traceback,
            _get_indicator_data_with_traceback,
            _run_cypher,
            _get_node_props,
        )
        from utils.neo4j_flgl_status import flgl_status_cypher_param

        stp = flgl_status_cypher_param()
        props = {}
        primary = node_group or 'Node'
        biz_id = ''

        with driver.session() as session:
            rec = session.run(
                """
                MATCH (n)
                WHERE elementId(n) = $gid OR (n.Id IS NOT NULL AND toString(n.Id) = $gid)
                RETURN n, labels(n)[0] AS primary_label
                LIMIT 1
                """,
                gid=graph_node_id,
            ).single()
            if not rec:
                return jsonify({'success': False, 'error': '未找到节点'}), 404
            node = rec['n']
            primary = rec['primary_label'] or primary
            props = dict(node)
            biz_raw = props.get('Id') or props.get('id')
            biz_id = str(biz_raw).strip() if biz_raw is not None else ''

        label = primary if primary in TPZS_GRAPH_LABELS else (node_group if node_group in TPZS_GRAPH_LABELS else primary)

        if label == 'Equipment' and biz_id:
            rd = _get_equipment_data(biz_id)
            row = _tpzs_env_table_row_from_ssyq(rd)
            if row:
                return jsonify({'success': True, 'panel': 'env_table', 'environmentRows': [row]})
            return jsonify({'success': True, 'panel': 'empty', 'environmentRows': []})

        if label == 'TestProject' and biz_id:
            pdata = _get_project_data_with_traceback(biz_id)
            rows = []
            if pdata and pdata.get('equipments'):
                for eq in pdata['equipments']:
                    eid = eq.get('id', '')
                    if not eid:
                        continue
                    rd = _get_equipment_data(eid)
                    r = _tpzs_env_table_row_from_ssyq(rd)
                    if r:
                        rows.append(r)
            if not rows:
                eq_recs = _run_cypher(
                    """
                    MATCH (eq:Equipment)-[:CON]->(p:TestProject {Id: $pid})
                    WHERE (eq).Status IN $flgl_enabled_status AND (p).Status IN $flgl_enabled_status
                    RETURN DISTINCT eq ORDER BY eq.Name
                    """,
                    {**stp, 'pid': biz_id},
                )
                seen = set()
                for er in eq_recs:
                    eqp = _get_node_props(er.get('eq', {}))
                    eid = eqp.get('id', '')
                    if not eid or eid in seen:
                        continue
                    seen.add(eid)
                    rd = _get_equipment_data(eid)
                    r = _tpzs_env_table_row_from_ssyq(rd)
                    if r:
                        rows.append(r)
            dedup = {r['equipmentTypeId']: r for r in rows}
            out = list(dedup.values())
            if out:
                return jsonify({'success': True, 'panel': 'env_table', 'environmentRows': out})
            return jsonify({'success': True, 'panel': 'empty', 'environmentRows': []})

        if label == 'Indicator' and biz_id:
            idata = _get_indicator_data_with_traceback(biz_id)
            rows = []
            if idata and idata.get('equipments'):
                for eq in idata['equipments']:
                    eid = eq.get('id', '')
                    if not eid:
                        continue
                    rd = _get_equipment_data(eid)
                    r = _tpzs_env_table_row_from_ssyq(rd)
                    if r:
                        rows.append(r)
            dedup = {r['equipmentTypeId']: r for r in rows}
            out = list(dedup.values())
            if out:
                return jsonify({'success': True, 'panel': 'env_table', 'environmentRows': out})
            return jsonify({'success': True, 'panel': 'empty', 'environmentRows': []})

        if label == 'EquipmentType' and biz_id:
            eq_recs = _run_cypher(
                """
                MATCH (et:EquipmentType {Id: $tid})-[:HAS]->(e:Equipment)
                WHERE (e).Status IN $flgl_enabled_status
                RETURN e ORDER BY e.Name
                """,
                {**stp, 'tid': biz_id},
            )
            devices = []
            for er in eq_recs:
                ep = _get_node_props(er.get('e', {}))
                if ep.get('id'):
                    devices.append({'id': ep.get('id'), 'name': ep.get('name', '') or ep.get('id')})
            return jsonify({'success': True, 'panel': 'equipment_type_devices', 'typeDevices': devices})

        if label == 'Environment':
            env_keys = [
                ('Name', '名称'),
                ('Label', '标签'),
                ('Description', '描述'),
                ('Type', '分类'),
                ('Class', '分级'),
                ('Combination', '典型组合'),
                ('Limit', '约束'),
                ('Request', '要求'),
            ]
            fields = []
            for en_key, zh_label in env_keys:
                val = props.get(en_key)
                if val is None:
                    val = props.get(en_key.lower())
                if val is None:
                    val = ''
                else:
                    val = _convert_value(val)
                fields.append({'label': zh_label, 'key': en_key, 'value': val if val is not None else ''})
            return jsonify({'success': True, 'panel': 'environment_fields', 'environmentFields': fields})

        return jsonify({
            'success': True,
            'panel': 'other',
            'environmentRows': [],
            'typeDevices': [],
            'hint': '该节点类型不按设备-环境表格展示。',
        })
    except Exception as e:
        logger.error(f"tpzs_node_panel 失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@kg_bp.route('/tpzs_base', methods=['POST'])
def tpzs_base_equipment_types():
    """
    ZSTPZS 初始视图：按 EquipmentType.Type 去重，每个 Type 一个虚拟节点（EquipmentTypeKind）。
    """
    try:
        data = request.get_json(silent=True) or {}
        limit = data.get('limit', 500)
        try:
            limit = int(limit)
            if limit <= 0:
                limit = 500
        except (ValueError, TypeError):
            limit = 500

        driver = current_app.config['NEO4J_DRIVER']
        if not driver:
            return jsonify({'success': False, 'error': 'Neo4j驱动不可用'}), 500

        from utils.neo4j_flgl_status import flgl_status_cypher_param
        stp = flgl_status_cypher_param()

        with driver.session() as session:
            rows = list(session.run(
                """
                MATCH (et:EquipmentType)
                WHERE (et).Status IN $flgl_enabled_status
                WITH trim(toString(coalesce(et.Type, ''))) AS raw_type, count(et) AS cnt
                RETURN raw_type, cnt
                ORDER BY raw_type
                LIMIT $limit
                """,
                {**stp, 'limit': limit},
            ))

        nodes = []
        for row in rows:
            display = _tpzs_normalize_type_value(row.get('raw_type'))
            cnt = row.get('cnt') or 0
            try:
                cnt = int(cnt)
            except (TypeError, ValueError):
                cnt = 0
            nodes.append(_tpzs_vis_type_category_node(display, cnt))

        return jsonify({
            'success': True,
            'nodes': nodes,
            'edges': [],
            'count': {'nodes': len(nodes), 'edges': 0},
        })
    except Exception as e:
        logger.error(f"tpzs_base 失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@kg_bp.route('/tpzs_query', methods=['POST'])
def tpzs_query_by_type_keyword():
    """
    按节点标签 + 关键词模糊查询，返回匹配节点及其之间的所有关系（子图）。
    请求体: {"nodeType": "Equipment", "keyword": "无人机", "nodeLimit": 120, "relLimit": 2000}
    keyword 为空字符串时返回该类型节点（受 nodeLimit 限制）。
    """
    try:
        data = request.get_json(silent=True) or {}
        node_type = (data.get('nodeType') or data.get('node_type') or '').strip()
        keyword = data.get('keyword')
        if keyword is None:
            keyword = ''
        else:
            keyword = str(keyword).strip()

        if node_type not in TPZS_GRAPH_LABELS:
            return jsonify({
                'success': False,
                'error': f'不支持的节点类型，允许: {", ".join(sorted(TPZS_GRAPH_LABELS))}',
            }), 400

        node_limit = data.get('nodeLimit', data.get('limit', 150))
        rel_limit = data.get('relLimit', 2000)
        try:
            node_limit = int(node_limit)
            if node_limit <= 0:
                node_limit = 150
        except (ValueError, TypeError):
            node_limit = 150
        try:
            rel_limit = int(rel_limit)
            if rel_limit <= 0:
                rel_limit = 2000
        except (ValueError, TypeError):
            rel_limit = 2000

        driver = current_app.config['NEO4J_DRIVER']
        if not driver:
            return jsonify({'success': False, 'error': 'Neo4j驱动不可用'}), 500

        # node_type 已白名单校验，可安全用于标签插值
        cypher_match = f"""
            MATCH (n:{node_type})
            WITH n,
              toLower(trim(
                coalesce(toString(n.Name), '') + ' ' + coalesce(toString(n.name), '') + ' ' +
                coalesce(toString(n.Label), '') + ' ' + coalesce(toString(n.label), '') + ' ' +
                coalesce(toString(n.Description), '') + ' ' + coalesce(toString(n.description), '') + ' ' +
                coalesce(toString(n.Type), '') + ' ' + coalesce(toString(n.type), '') + ' ' +
                coalesce(toString(n.Id), '') + ' ' + coalesce(toString(n.id), '')
              )) AS blob
            WHERE $kw = '' OR blob CONTAINS toLower(trim($kw))
            RETURN n
            LIMIT $node_limit
        """

        nodes_map = {}
        edges = []
        edge_set = set()
        edge_id_set = set()

        with driver.session() as session:
            matched = list(session.run(cypher_match, kw=keyword, node_limit=node_limit))
            first_node = next((record.get('n') for record in matched if record.get('n')), None)
            use_element_id = bool(first_node is not None and getattr(first_node, "element_id", None))

            ids = []
            for record in matched:
                node = record.get('n')
                if not node:
                    continue
                nid = _neo_graph_node_id(node)
                nodes_map[nid] = _vis_node_dict(node)
                if use_element_id:
                    ids.append(str(node.element_id))
                else:
                    ids.append(int(node.id))

            if ids:
                if use_element_id:
                    rel_cypher = """
                    MATCH (a)-[r]-(b)
                    WHERE elementId(a) IN $ids AND elementId(b) IN $ids
                    RETURN a, r, b
                    LIMIT $rel_limit
                    """
                else:
                    rel_cypher = """
                    MATCH (a)-[r]-(b)
                    WHERE id(a) IN $ids AND id(b) IN $ids
                    RETURN a, r, b
                    LIMIT $rel_limit
                    """
                rel_result = session.run(rel_cypher, ids=ids, rel_limit=rel_limit)
                for record in rel_result:
                    try:
                        _merge_triple(
                            nodes_map, edges, edge_set, edge_id_set,
                            record['a'], record['r'], record['b'],
                        )
                    except KeyError:
                        continue

        return jsonify({
            'success': True,
            'nodes': list(nodes_map.values()),
            'edges': edges,
            'count': {'nodes': len(nodes_map), 'edges': len(edges)},
        })
    except Exception as e:
        logger.error(f"tpzs_query 失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500