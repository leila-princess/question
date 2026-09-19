from neo4j import GraphDatabase

# Neo4j 配置
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "CHANGE_ME"

neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def single_query(keyword: str, node_type: str = None, limit: int = 50):
    """
    单次查询：只查找符合关键字的节点
    :param keyword: 关键字
    :param node_type: 节点类型（可选）
    :param limit: 限制返回数量
    :return: 节点列表
    """
    with neo4j_driver.session() as session:
        # 节点类型
        if node_type:
            match_clause = f"(n:{node_type})"
        else:
            match_clause = "(n)"

        query = f"""
        MATCH {match_clause}
        WHERE
        (n.title_zh IS NOT NULL AND toLower(n.title_zh) CONTAINS toLower($keyword) AND toLower(n.title_zh) CONTAINS "试验方法")
        AND 
        (n.standardNumber IS NOT NULL )
        RETURN n
        LIMIT $limit
        """
        result = session.run(query, keyword=keyword, limit=limit)

        nodes = []
        for record in result:
            node = record["n"]
            labels = list(node.labels)
            nodes.append({
                "id": str(node.id),
                "label": node.get("title_zh", labels[0] if labels else "Node"),
                "group": labels[0] if labels else "Node",
                "properties": dict(node),
            })

        return nodes


def expand_query(start_nodes: list, node_type: str = None, rel_type: str = None, depth: int = 1):
    """
    扩展查询：基于已有节点扩展查询相关节点和关系
    :param start_nodes: 起始节点 ID 列表
    :param node_type: 节点类型（可选）
    :param rel_type: 关系类型（可选）
    :param depth: 扩展深度
    :return: (nodes, edges)
    """
    if not start_nodes:
        return [], []

    with neo4j_driver.session() as session:
        start_ids = [int(nid) for nid in start_nodes]
        id_list_str = ",".join(map(str, start_ids))

        # 节点和关系类型过滤
        node_filter = f":{node_type}" if node_type else ""
        rel_filter = f":{rel_type}" if rel_type else ""

        # 根据深度构建查询
        if depth == 1:
            # 一度关系查询
            query = f"""
            MATCH (n)-[r{rel_filter}]-(m{node_filter})
            WHERE id(n) IN [{id_list_str}]
            RETURN n, r, m
            """
        else:
            # 多度关系查询
            query = f"""
            MATCH path = (n)-[r{rel_filter}*1..{depth}]-(m{node_filter})
            WHERE id(n) IN [{id_list_str}]
            UNWIND relationships(path) as rel
            UNWIND nodes(path) as node
            RETURN DISTINCT node, rel
            """

        result = session.run(query)

        nodes_map, edges, edge_set = {}, [], set()

        for record in result:
            if "n" in record and "r" in record and "m" in record:
                # 一度关系查询结果处理
                node_start = record["n"]
                node_end = record["m"]
                rel = record["r"]
                
                # 添加节点
                for node in [node_start, node_end]:
                    nid = str(node.id)
                    if nid not in nodes_map:
                        labels = list(node.labels)
                        nodes_map[nid] = {
                            "id": nid,
                            "label": node.get("name", labels[0] if labels else "Node"),
                            "group": labels[0] if labels else "Node",
                            "properties": dict(node),
                        }
                
                # 添加边
                edge_key = (node_start.id, node_end.id, rel.type)
                if edge_key not in edge_set:
                    edge_set.add(edge_key)
                    edges.append({
                        "from": str(node_start.id),
                        "to": str(node_end.id),
                        "label": rel.type,
                        "properties": dict(rel),
                    })
            elif "node" in record and "rel" in record:
                # 多度关系查询结果处理
                node = record["node"]
                rel = record["rel"]
                
                # 添加节点
                nid = str(node.id)
                if nid not in nodes_map:
                    labels = list(node.labels)
                    nodes_map[nid] = {
                        "id": nid,
                        "label": node.get("name", labels[0] if labels else "Node"),
                        "group": labels[0] if labels else "Node",
                        "properties": dict(node),
                    }
                
                # 添加边（对于多度关系，我们只添加直接关系）
                if hasattr(rel, 'start_node') and hasattr(rel, 'end_node'):
                    edge_key = (rel.start_node.id, rel.end_node.id, rel.type)
                    if edge_key not in edge_set:
                        edge_set.add(edge_key)
                        edges.append({
                            "from": str(rel.start_node.id),
                            "to": str(rel.end_node.id),
                            "label": rel.type,
                            "properties": dict(rel),
                        })

        return list(nodes_map.values()), edges


# 组合查询函数
def combined_query(keyword: str, node_type: str = None, rel_type: str = None, expand_depth: int = 1, limit: int = 50):
    """
    组合查询：先查找符合关键字的节点，然后扩展查询相关节点和关系
    :param keyword: 关键字
    :param node_type: 节点类型（可选）
    :param rel_type: 关系类型（可选）
    :param expand_depth: 扩展深度
    :param limit: 限制返回数量
    :return: (nodes, edges)
    """
    # 第一步：查找符合关键字的节点
    nodes = single_query(keyword, node_type, limit)
    
    if not nodes:
        return [], []
    
    # 第二步：基于找到的节点扩展查询
    node_ids = [node["id"] for node in nodes]
    additional_nodes, edges = expand_query(node_ids, node_type, rel_type, expand_depth)
    
    # 合并节点（去重）
    all_nodes_map = {node["id"]: node for node in nodes}
    for node in additional_nodes:
        if node["id"] not in all_nodes_map:
            all_nodes_map[node["id"]] = node
    
    return list(all_nodes_map.values()), edges