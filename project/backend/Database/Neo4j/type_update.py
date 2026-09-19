import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)


class Neo4jPropertyManager:
    def __init__(self, driver):
        self.driver = driver

    def _get_session(self):
        if self.driver is None:
            raise RuntimeError("Neo4j driver not initialized")
        return self.driver.session()

    def get_node_properties(self, node_label, node_id):
        try:
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label} {{Id: $node_id}})
                    RETURN properties(n) AS props
                """, node_id=node_id)
                record = result.single()
            if record:
                return {"success": True, "data": record["props"], "message": "Properties retrieved"}
            return {"success": False, "error": f"Node {node_id} not found"}
        except Exception as e:
            logger.error(f"Failed to get properties: {e}")
            return {"success": False, "error": str(e)}

    def add_property(self, node_label, node_id, prop_name, prop_value):
        try:
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label} {{Id: $node_id}})
                    SET n.{prop_name} = $prop_value
                    RETURN properties(n) AS props
                """, node_id=node_id, prop_value=prop_value)
                record = result.single()
            if record:
                return {"success": True, "data": record["props"], "message": f"Property {prop_name} added"}
            return {"success": False, "error": f"Node {node_id} not found"}
        except Exception as e:
            logger.error(f"Failed to add property: {e}")
            return {"success": False, "error": str(e)}

    def remove_property(self, node_label, node_id, prop_name):
        try:
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label} {{Id: $node_id}})
                    REMOVE n.{prop_name}
                    RETURN properties(n) AS props
                """, node_id=node_id)
                record = result.single()
            if record:
                return {"success": True, "data": record["props"], "message": f"Property {prop_name} removed"}
            return {"success": False, "error": f"Node {node_id} not found"}
        except Exception as e:
            logger.error(f"Failed to remove property: {e}")
            return {"success": False, "error": str(e)}

    def update_property(self, node_label, node_id, prop_name, prop_value):
        try:
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label} {{Id: $node_id}})
                    SET n.{prop_name} = $prop_value
                    RETURN properties(n) AS props
                """, node_id=node_id, prop_value=prop_value)
                record = result.single()
            if record:
                return {"success": True, "data": record["props"], "message": f"Property {prop_name} updated"}
            return {"success": False, "error": f"Node {node_id} not found"}
        except Exception as e:
            logger.error(f"Failed to update property: {e}")
            return {"success": False, "error": str(e)}

    def get_all_nodes_by_type(self, node_label, limit=100, offset=0):
        try:
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label})
                    RETURN n
                    SKIP $offset LIMIT $limit
                """, offset=offset, limit=limit)
                nodes = [dict(record["n"]) for record in result]
            return {"success": True, "data": nodes, "count": len(nodes), "message": f"Retrieved {len(nodes)} {node_label} nodes"}
        except Exception as e:
            logger.error(f"Failed to get nodes: {e}")
            return {"success": False, "error": str(e)}

    def search_nodes(self, node_label, search_field, search_value):
        try:
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label})
                    WHERE n.{search_field} CONTAINS $search_value
                    RETURN n
                """, search_value=search_value)
                nodes = [dict(record["n"]) for record in result]
            return {"success": True, "data": nodes, "count": len(nodes), "message": f"Found {len(nodes)} matching nodes"}
        except Exception as e:
            logger.error(f"Failed to search nodes: {e}")
            return {"success": False, "error": str(e)}

    def get_node_count(self, node_label):
        try:
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH (n:{node_label})
                    RETURN count(n) AS count
                """)
                record = result.single()
                count = record["count"] if record else 0
            return {"success": True, "data": {"count": count}, "message": f"Found {count} {node_label} nodes"}
        except Exception as e:
            logger.error(f"Failed to count nodes: {e}")
            return {"success": False, "error": str(e)}

    def get_all_relationships(self):
        try:
            with self._get_session() as session:
                result = session.run("""
                    MATCH (a)-[r]->(b)
                    RETURN a.Id AS from_id, labels(a) AS from_labels,
                           type(r) AS rel_type, properties(r) AS rel_props,
                           b.Id AS to_id, labels(b) AS to_labels
                """)
                rels = []
                for record in result:
                    rels.append({
                        "from_id": record["from_id"],
                        "from_labels": record["from_labels"],
                        "rel_type": record["rel_type"],
                        "rel_props": record["rel_props"],
                        "to_id": record["to_id"],
                        "to_labels": record["to_labels"],
                    })
            return {"success": True, "data": rels, "count": len(rels), "message": f"Retrieved {len(rels)} relationships"}
        except Exception as e:
            logger.error(f"Failed to get relationships: {e}")
            return {"success": False, "error": str(e)}

    def get_relationships_of_node(self, node_label, node_id, direction="both"):
        try:
            if direction == "outgoing":
                pattern = "(n)-[r]->(m)"
            elif direction == "incoming":
                pattern = "(m)-[r]->(n)"
            else:
                pattern = "(n)-[r]-(m)"
            with self._get_session() as session:
                result = session.run(f"""
                    MATCH {pattern}
                    WHERE n:{node_label} AND n.Id = $node_id
                    RETURN m.Id AS other_id, labels(m) AS other_labels,
                           type(r) AS rel_type, properties(r) AS rel_props,
                           direction(r, n) AS dir
                """, node_id=node_id)
                rels = []
                for record in result:
                    rels.append({
                        "other_id": record["other_id"],
                        "other_labels": record["other_labels"],
                        "rel_type": record["rel_type"],
                        "rel_props": record["rel_props"],
                        "direction": record["dir"],
                    })
            return {"success": True, "data": rels, "count": len(rels), "message": f"Retrieved {len(rels)} relationships"}
        except Exception as e:
            logger.error(f"Failed to get relationships of node: {e}")
            return {"success": False, "error": str(e)}

    def get_graph_schema(self):
        try:
            with self._get_session() as session:
                labels_result = session.run("CALL db.labels()")
                labels = [r["label"] for r in labels_result]
                types_result = session.run("CALL db.relationshipTypes()")
                rel_types = [r["relationshipType"] for r in types_result]
            return {"success": True, "data": {"labels": labels, "relationship_types": rel_types}, "message": "Schema retrieved"}
        except Exception as e:
            logger.error(f"Failed to get schema: {e}")
            return {"success": False, "error": str(e)}
