import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)


class Neo4jDeleteManager:
    def __init__(self, driver):
        self.driver = driver

    def _get_session(self):
        if self.driver is None:
            raise RuntimeError("Neo4j driver not initialized")
        return self.driver.session()

    def delete_node(self, node_label, node_id):
        try:
            with self._get_session() as session:
                session.run(f"MATCH (n:{node_label} {{Id: $node_id}}) DETACH DELETE n", node_id=node_id)
            return {"success": True, "message": f"Node {node_id} deleted"}
        except Exception as e:
            logger.error(f"Failed to delete node {node_id}: {e}")
            return {"success": False, "error": str(e)}

    def delete_node_with_relations(self, node_label, node_id):
        try:
            with self._get_session() as session:
                session.run(f"""
                    MATCH (n:{node_label} {{Id: $node_id}})
                    OPTIONAL MATCH (n)-[r]-()
                    DELETE r, n
                """, node_id=node_id)
            return {"success": True, "message": f"Node {node_id} and all its relationships deleted"}
        except Exception as e:
            logger.error(f"Failed to delete node {node_id} with relations: {e}")
            return {"success": False, "error": str(e)}

    def delete_relationship(self, from_id, to_id, rel_type):
        try:
            with self._get_session() as session:
                session.run(f"""
                    MATCH (a)-[r:{rel_type}]->(b)
                    WHERE a.Id = $from_id AND b.Id = $to_id
                    DELETE r
                """, from_id=from_id, to_id=to_id)
            return {"success": True, "message": "Relationship deleted"}
        except Exception as e:
            logger.error(f"Failed to delete relationship: {e}")
            return {"success": False, "error": str(e)}

    def delete_by_type(self, node_label):
        try:
            with self._get_session() as session:
                session.run(f"MATCH (n:{node_label}) DETACH DELETE n")
            return {"success": True, "message": f"All {node_label} nodes deleted"}
        except Exception as e:
            logger.error(f"Failed to delete {node_label}: {e}")
            return {"success": False, "error": str(e)}

    def delete_orphan_nodes(self):
        try:
            with self._get_session() as session:
                result = session.run("""
                    MATCH (n)
                    WHERE NOT (n)--()
                    DELETE n
                    RETURN count(n) AS deleted
                """)
                record = result.single()
                count = record["deleted"] if record else 0
            return {"success": True, "data": {"deleted": count}, "message": f"Deleted {count} orphan nodes"}
        except Exception as e:
            logger.error(f"Failed to delete orphan nodes: {e}")
            return {"success": False, "error": str(e)}
