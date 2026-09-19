import json
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

def search_standards(driver, keyword=None, node_type=None, relationship_type=None, source=None, limit=100):
    """搜索标准"""
    if not driver:
        return []

    with driver.session() as session:
        match_clauses = []
        where_clauses = []
        return_items = []
        
        if node_type:
            match_clauses.append(f"(n:{node_type})")
        else:
            match_clauses.append("(n)")
        
        if relationship_type:
            match_clauses.append(f"-[r:{relationship_type}]->(m)")
        
        if node_type == "Standard":
            return_items = ["n.name", "n.title", "n.releaseDate", "n.implementDate"]
        elif node_type == "Part":
            return_items = ["n.name", "n.title", "n.releaseDate", "n.implementDate"]
        elif node_type == "TestType":
            return_items = ["n.name", "n.part", "n.purpose"]
        elif node_type == "EquipmentCategory":
            return_items = ["n.code", "n.name", "n.source"]
        elif node_type == "Term":
            return_items = ["n.name", "n.definition", "n.source"]
        elif node_type == "TestCondition":
            return_items = ["n.type", "n.content", "n.cycle", "n.source"]
        else:
            return_items = ["n"]
        
        if relationship_type:
            return_items.extend(["type(r) as relationship_type", "r"])
            if not node_type:
                return_items.append("m as related_node")
        
        if keyword:
            if node_type == "Standard" or node_type == "Part":
                where_clauses.append("(toLower(n.name) CONTAINS toLower($keyword) OR toLower(n.title) CONTAINS toLower($keyword))")
            elif node_type == "TestType":
                where_clauses.append("(toLower(n.name) CONTAINS toLower($keyword) OR toLower(n.purpose) CONTAINS toLower($keyword))")
            elif node_type == "EquipmentCategory":
                where_clauses.append("(toLower(n.name) CONTAINS toLower($keyword) OR toLower(coalesce(n.description,'')) CONTAINS toLower($keyword))")
            elif node_type == "Term":
                where_clauses.append("(toLower(n.name) CONTAINS toLower($keyword) OR toLower(n.definition) CONTAINS toLower($keyword))")
            else:
                where_clauses.append("(toLower(n.name) CONTAINS toLower($keyword) OR toLower(coalesce(n.title,'')) CONTAINS toLower($keyword) OR toLower(coalesce(n.description,'')) CONTAINS toLower($keyword))")
        
        if source:
            if node_type in ["Part", "TestType", "EquipmentCategory", "Term", "TestCondition"]:
                where_clauses.append("n.source = $source")
            elif node_type == "Standard":
                where_clauses.append("n.name = $source")
        
        query = f"""
        MATCH {', '.join(match_clauses)}
        {f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""}
        RETURN {', '.join(return_items)}
        LIMIT $limit
        """
        
        params = {"limit": limit}
        if keyword:
            params["keyword"] = keyword
        if source:
            params["source"] = source
        
        logger.debug(f"执行Neo4j查询: {query}")
        logger.debug(f"查询参数: {params}")
        
        result = session.run(query, **params)
        
        results = []
        processed_records = set()

        def make_hashable(obj):
            if isinstance(obj, dict):
                return tuple(sorted((k, make_hashable(v)) for k, v in obj.items()))
            elif isinstance(obj, list):
                return tuple(make_hashable(x) for x in obj)
            return obj

        for record in result:
            result_item = {}
            for key in record.keys():
                value = record[key]
                if hasattr(value, "items"):
                    result_item[key] = dict(value)
                else:
                    result_item[key] = value
            
            hashable_key = make_hashable(result_item)
            
            if hashable_key not in processed_records:
                processed_records.add(hashable_key)
                results.append(result_item)

        return results

def execute_cypher_query(driver, query, params=None):
    """执行Cypher查询"""
    if not driver:
        return []
    
    try:
        with driver.session() as session:
            result = session.run(query, **(params or {}))
            records = []
            for record in result:
                # 将记录转换为字典
                rec_dict = {}
                for key in record.keys():
                    value = record[key]
                    # 处理Neo4j节点和关系
                    if hasattr(value, 'items'):
                        rec_dict[key] = dict(value)
                    elif hasattr(value, 'labels'):
                        # 节点
                        rec_dict[key] = {
                            'id': value.id,
                            'labels': list(value.labels),
                            'properties': dict(value)
                        }
                    elif hasattr(value, 'type'):
                        # 关系
                        rec_dict[key] = {
                            'id': value.id,
                            'type': value.type,
                            'properties': dict(value)
                        }
                    else:
                        rec_dict[key] = value
                records.append(rec_dict)
            return records
    except Exception as e:
        logger.error(f"执行Cypher查询失败: {str(e)}")
        return []

def get_standard_summary(driver, standard_id):
    """获取标准摘要"""
    if not driver:
        return None
    
    try:
        with driver.session() as session:
            result = session.run(
                "MATCH (n {name: $standard_id}) WHERE n.id > 138 RETURN n LIMIT 1",
                standard_id=standard_id
            )
            record = result.single()
            
            if record:
                node = record["n"]
                return {
                    "standardName": node.get("title"),
                    "People": node.get("drafters"),
                    "DraftingUnits": node.get("draftingUnits"),
                    "releaseDate": node.get("releaseDate"),
                    "status": node.get("status"),
                }
            return None
    except Exception as e:
        logger.error(f"获取标准摘要失败: {str(e)}")
        return None