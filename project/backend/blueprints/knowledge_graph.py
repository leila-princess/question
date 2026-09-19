from flask import Blueprint, request, jsonify, current_app
import json
import traceback
import logging
from utils.neo4j_utils import search_standards, get_standard_summary

logger = logging.getLogger(__name__)

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
    """Neo4j代理接口 - 支持任意Cypher查询"""
    try:
        data = request.json
        
        query = data.get('query')
        params = data.get('params', {})

        if not query:
            return jsonify({
                'success': False,
                'error': 'query参数不能为空'
            }), 400

        driver = current_app.config['NEO4J_DRIVER']
        if not driver:
            return jsonify({
                'success': False,
                'error': 'Neo4j驱动不可用'
            }), 500

        with driver.session() as session:
            result = session.run(query, **params)
            records = list(result)
            logger.info(f"代理接口获取到{len(records)}条记录")
            
            # 转换为通用格式
            nodes = []
            for record in records:
                row = {}
                for key in record.keys():
                    value = record[key]
                    if hasattr(value, 'items'):
                        # Neo4j节点
                        row[key] = {
                            'id': str(value.get('id', '')),
                            'name': value.get('name', ''),
                            'properties': dict(value)
                        }
                    else:
                        row[key] = value
                nodes.append(row)
            
        return jsonify({
                        'success': True,
                        'nodes': nodes,
                        'count': len(nodes)
                    })
            
    except Exception as e:
        logger.error(f"代理接口出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
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
                        'properties': dict(node)
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
        limit = request.json.get('limit', 500)
        
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
                            'title': json.dumps(dict(node_start), ensure_ascii=False),
                            'properties': dict(node_start)
                        }

                    end_id = str(node_end.id)
                    if end_id not in nodes_map:
                        labels = list(node_end.labels)
                        nodes_map[end_id] = {
                            'id': end_id,
                            'label': node_end.get('name', labels[0] if labels else 'Node'),
                            'group': labels[0] if labels else 'Node',
                            'title': json.dumps(dict(node_end), ensure_ascii=False),
                            'properties': dict(node_end)
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
                        'title': json.dumps(dict(rel), ensure_ascii=False),
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
                    'title': json.dumps(dict(node), ensure_ascii=False),
                    'properties': dict(node)
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
                            'title': json.dumps(dict(node), ensure_ascii=False),
                            'properties': dict(node)
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
                    'title': json.dumps(dict(rel), ensure_ascii=False),
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