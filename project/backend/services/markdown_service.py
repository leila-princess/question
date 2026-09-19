from neo4j import GraphDatabase

# Neo4j 配置
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "CHANGE_ME"

neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def convert_to_markdown(data):
    """
    将图数据转换为Markdown格式
    """
    markdown_content = ""
    processed_nodes = set()
    for item in data:
        try:
            # 尝试不同的方法来访问路径和节点
            # 方法1: 直接访问路径对象
            path = item['完整路径']
            
            # 方法2: 尝试使用nodes属性获取所有节点
            if hasattr(path, 'nodes'):
                nodes = list(path.nodes)
                # print(nodes)
                for node in nodes:
                    if node.element_id not in processed_nodes:
                        processed_nodes.add(node.element_id)
                        
                        # 获取节点属性
                        node_number = node._properties.get('number', '')
                        node_name = node._properties.get('name', '')
                        definition = node._properties.get('definition', '')
                        # 调试信息 - 打印节点详情
                        # print(f"处理节点: {node_number} {node_name}, 是否有definition: {bool(definition)}")
                        # 根据节点编号确定标题级别
                        if not node_number:  # 如果没有编号，使用默认级别
                            level = 1
                        else:
                            # 计算点的数量来确定层级
                            dot_count = node_number.count('.')
                            level = dot_count + 1  # 例如: "6" -> 0点 -> 1级, "6.2" -> 1点 -> 2级
                        # 添加标题
                        if level == 1:
                            markdown_content += f"# {node_number} {node_name}\n\n"
                        elif level == 2:
                            markdown_content += f"## {node_number} {node_name}\n\n"
                        elif level == 3:
                            markdown_content += f"### {node_number} {node_name}\n\n"
                        else:
                            markdown_content += f"#### {node_number} {node_name}\n\n"
                        
                        # 添加定义内容
                        if definition:
                            # print("有definition") 
                            markdown_content += f"{definition}\n\n"
                print("方法2 成功")    
            # 方法3: 尝试从关系路径中提取节点
            elif '关系路径' in item:
                relationships = item['关系路径']                
                # 收集所有节点
                all_nodes = set()
                for rel in relationships:
                    # 关系通常有start_node和end_node属性
                    if hasattr(rel, 'start_node'):
                        all_nodes.add(rel.start_node)
                    if hasattr(rel, 'end_node'):
                        all_nodes.add(rel.end_node)
                
                # 处理所有节点
                for node in all_nodes:
                    if node.element_id not in processed_nodes:
                        processed_nodes.add(node.element_id)
                        
                        # 获取节点属性
                        node_number = node._properties.get('number', '')
                        node_name = node._properties.get('name', '')
                        definition = node._properties.get('definition', '')
                        
                        # 根据节点编号确定标题级别
                        if not node_number:  # 如果没有编号，使用默认级别
                            level = 1
                        else:
                            # 计算点的数量来确定层级
                            dot_count = node_number.count('.')
                            level = dot_count + 1  # 例如: "6" -> 0点 -> 1级, "6.2" -> 1点 -> 2级
                            
                        # 添加标题
                        if level == 1:
                            markdown_content += f"# {node_number} {node_name}\n\n"
                        elif level == 2:
                            markdown_content += f"## {node_number} {node_name}\n\n"
                        elif level == 3:
                            markdown_content += f"### {node_number} {node_name}\n\n"
                        else:
                            markdown_content += f"#### {node_number} {node_name}\n\n"
                            
                        # 添加定义内容
                        if definition:
                            markdown_content += f"{definition}\n\n"
                print("方法3 成功")
            # 方法4: 如果以上方法都失败，尝试直接访问记录中的节点
            else:
                # 尝试查找可能的节点字段
                for key, value in item.items():
                    if hasattr(value, 'element_id') and hasattr(value, '_properties'):
                        # 这很可能是一个节点
                        node = value
                        if node.element_id not in processed_nodes:
                            processed_nodes.add(node.element_id)
                            
                            # 获取节点属性
                            node_number = node._properties.get('number', '')
                            node_name = node._properties.get('name', '')
                            definition = node._properties.get('definition', '')
                            
                            # 根据节点编号确定标题级别
                            if not node_number:  # 如果没有编号，使用默认级别
                                level = 1
                            else:
                                # 计算点的数量来确定层级
                                dot_count = node_number.count('.')
                                level = dot_count + 1  # 例如: "6" -> 0点 -> 1级, "6.2" -> 1点 -> 2级
                                
                            # 添加标题
                            if level == 1:
                                markdown_content += f"# {node_number} {node_name}\n\n"
                            elif level == 2:
                                markdown_content += f"## {node_number} {node_name}\n\n"
                            elif level == 3:
                                markdown_content += f"### {node_number} {node_name}\n\n"
                            else:
                                markdown_content += f"#### {node_number} {node_name}\n\n"
                                
                            # 添加定义内容
                            if definition:
                                markdown_content += f"{definition}\n\n"
                print("方法4 成功")
        except Exception as e:
            print(f"处理记录时出错: {e}")
            import traceback
            traceback.print_exc()
        
    return markdown_content

def extract_nodes_from_records(records):
    """
    从records数据中提取所有节点，并返回一个去重的节点数组
    """
    nodes = []
    seen_element_ids = set()  # 用于跟踪已经处理过的节点element_id
    
    for record in records:
        # 获取路径对象
        path = record.get('完整路径')
        if path:
            # 获取路径中的所有节点
            path_nodes = list(path.nodes)
            for node in path_nodes:
                element_id = node.element_id
                # 如果这个节点还没有被处理过
                if element_id not in seen_element_ids:
                    seen_element_ids.add(element_id)
                    # 将节点属性转换为字典并添加到结果中
                    node_dict = dict(node)
                    # 添加element_id和labels信息
                    node_dict['element_id'] = element_id
                    node_dict['labels'] = list(node.labels)
                    nodes.append(node_dict)
    
    return nodes

def get_structured_content(keyword: str, node_type: str = None, limit: int = 50):
    """
    获取结构化内容（Markdown格式）
    """
    try:
        with neo4j_driver.session() as session:
             # 构建查询
            query = """
            MATCH (standard:Standard {id: $keyword})
            MATCH (standard)-[:HAS_PART]->(c:Clause)
            WHERE toLower(c.name) CONTAINS "试验过程"
            MATCH path = (c)-[*1..3]-(connectedNode)
            WHERE 
                any(label IN labels(connectedNode) WHERE label IN ['Condition', 'Clause', 'TestMethod', 'Device'])
                AND 
                all(node IN nodes(path) WHERE NOT 'Standard' IN labels(node))
            RETURN 
                path AS 完整路径,
                relationships(path) AS 关系路径,
                labels(connectedNode) AS 连接节点标签
            """

            query1 = """
            MATCH (standard:Standard {id: $keyword})
            MATCH (standard)-[:HAS_PART]->(c:Clause)
            WHERE toLower(c.name) CONTAINS "试验结果评定"
            MATCH path = (c)-[*1..3]-(connectedNode)
            WHERE 
                any(label IN labels(connectedNode) WHERE label IN ['Condition', 'Clause', 'TestMethod', 'Device'])
                AND 
                all(node IN nodes(path) WHERE NOT 'Standard' IN labels(node))
            RETURN 
                path AS 完整路径,
                relationships(path) AS 关系路径,
                labels(connectedNode) AS 连接节点标签
            """

            result = session.run(query, keyword=keyword, limit=limit)
            result1 = session.run(query1, keyword=keyword, limit=limit)

            # 收集所有记录
            records = list(result)
            records1 = list(result1)
            if records or records1:
                # 转换为Markdown格式
                markdown_content = convert_to_markdown(records)
                # print("Markdown content:", markdown_content)    
                # 提取所有节点
                nodes = extract_nodes_from_records(records1)
                # print(f"提取了 {len(nodes)} 个唯一节点")
                # print("Nodes:", nodes)  
                # print("markdown_content:", markdown_content)  
                return {
                    "content": markdown_content,
                    "nodes": nodes
                }
            else:
                return {
                    "success": True,
                    "content": "未找到相关数据",
                    "nodes": []
                }


    except Exception as e:
        return {"success": False, "error": str(e)}