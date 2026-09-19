import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils.neo4j_flgl_status import flgl_status_cypher_param, flgl_neighbor_filter_params

logger = logging.getLogger(__name__)

VALID_LABELS = {
    "EquipmentType", "Equipment", "Environment",
    "TestProject", "Indicator", "TestMethod",
}


class Neo4jSearchManager:
    def __init__(self, driver):
        self.driver = driver

    def _get_session(self):
        if self.driver is None:
            raise RuntimeError("Neo4j driver not initialized")
        return self.driver.session()

    def search_by_keyword_and_type(self, keyword, node_label, fields=None, limit=100):
        if node_label not in VALID_LABELS:
            return {"success": False, "error": f"Invalid node type: {node_label}. Must be one of {VALID_LABELS}"}

        if fields is None:
            fields = ["Name", "Label", "Description"]

        try:
            where_parts = []
            for field in fields:
                where_parts.append(f"n.{field} CONTAINS $keyword")
            where_clause = " OR ".join(where_parts)

            st = flgl_status_cypher_param()
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label})
                    WHERE ({where_clause}) AND n.Status IN $flgl_enabled_status
                    RETURN n
                    LIMIT $limit
                """, keyword=keyword, limit=limit, **st)
                nodes = [dict(record["n"]) for record in result]
            return {
                "success": True,
                "data": nodes,
                "count": len(nodes),
                "message": f"Found {len(nodes)} {node_label} nodes matching '{keyword}'"
            }
        except Exception as e:
            logger.error(f"Failed to search {node_label}: {e}")
            return {"success": False, "error": str(e)}

    def search_by_keyword_fuzzy(self, keyword, labels=None, fields=None, limit=100):
        if labels is None:
            labels = list(VALID_LABELS)
        if fields is None:
            fields = ["Name", "Label", "Description"]

        try:
            all_results = []
            with self._get_session() as session:
                stf = flgl_status_cypher_param()
                for label in labels:
                    if label not in VALID_LABELS:
                        continue
                    where_parts = []
                    for field in fields:
                        where_parts.append(f"n.{field} CONTAINS $keyword")
                    where_clause = " OR ".join(where_parts)

                    result = session.run(f"""
                        MATCH (n:{label})
                        WHERE ({where_clause}) AND n.Status IN $flgl_enabled_status
                        RETURN n, labels(n) AS node_labels
                        LIMIT $limit
                    """, keyword=keyword, limit=limit, **stf)
                    for record in result:
                        node_data = dict(record["n"])
                        node_data["_label"] = label
                        node_data["_labels"] = record["node_labels"]
                        all_results.append(node_data)
            return {
                "success": True,
                "data": all_results,
                "count": len(all_results),
                "message": f"Found {len(all_results)} nodes across {len(labels)} types matching '{keyword}'"
            }
        except Exception as e:
            logger.error(f"Failed to fuzzy search: {e}")
            return {"success": False, "error": str(e)}

    def search_exact_match(self, node_label, field, value):
        if node_label not in VALID_LABELS:
            return {"success": False, "error": f"Invalid node type: {node_label}"}
        try:
            stf = flgl_status_cypher_param()
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label})
                    WHERE n.{field} = $value AND n.Status IN $flgl_enabled_status
                    RETURN n
                """, value=value, **stf)
                nodes = [dict(record["n"]) for record in result]
            return {
                "success": True,
                "data": nodes,
                "count": len(nodes),
                "message": f"Found {len(nodes)} exact matches"
            }
        except Exception as e:
            logger.error(f"Failed exact match search: {e}")
            return {"success": False, "error": str(e)}

    def search_by_id(self, node_label, node_id):
        if node_label not in VALID_LABELS:
            return {"success": False, "error": f"Invalid node type: {node_label}"}
        try:
            stf = flgl_status_cypher_param()
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label} {{Id: $node_id}})
                    WHERE n.Status IN $flgl_enabled_status
                    RETURN n
                """, node_id=node_id, **stf)
                record = result.single()
            if record:
                return {"success": True, "data": dict(record["n"]), "message": "Node found"}
            return {"success": False, "error": f"Node {node_id} not found in {node_label}"}
        except Exception as e:
            logger.error(f"Failed to search by ID: {e}")
            return {"success": False, "error": str(e)}

    def search_related_nodes(self, node_label, node_id, depth=1):
        try:
            p = {'node_id': node_id, **flgl_neighbor_filter_params()}
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label} {{Id: $node_id}})-[r*1..{depth}]-(m)
                    WITH n, r, m
                    WHERE
                      (NOT any(lab IN $flgl_typed_label_list WHERE lab IN labels(m)))
                      OR (m.Status IN $flgl_enabled_status)
                      OR (any(lab IN $flgl_typed_label_list WHERE lab IN labels(m)) AND m.Status IS NULL)
                    RETURN m, labels(m) AS m_labels, type(r[0]) AS rel_type
                """, **p)
                nodes = []
                for record in result:
                    node_data = dict(record["m"])
                    node_data["_label"] = record["m_labels"][0] if record["m_labels"] else ""
                    node_data["_rel_type"] = record["rel_type"]
                    nodes.append(node_data)
            return {
                "success": True,
                "data": nodes,
                "count": len(nodes),
                "message": f"Found {len(nodes)} related nodes within depth {depth}"
            }
        except Exception as e:
            logger.error(f"Failed to search related nodes: {e}")
            return {"success": False, "error": str(e)}
