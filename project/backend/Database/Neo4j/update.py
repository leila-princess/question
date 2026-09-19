import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)


class Neo4jUpdateManager:
    def __init__(self, driver):
        self.driver = driver

    def _get_session(self):
        if self.driver is None:
            raise RuntimeError("Neo4j driver not initialized")
        return self.driver.session()

    def update_node(self, node_label, node_id, updates):
        try:
            set_clauses = ", ".join([f"n.{k} = ${k}" for k in updates.keys()])
            query = f"""
                MATCH (n:{node_label} {{Id: $node_id}})
                SET {set_clauses}
                RETURN n
            """
            params = {"node_id": node_id}
            params.update(updates)
            with self._get_session() as session:
                result = session.run(query, **params)
                record = result.single()
            if record:
                return {"success": True, "data": dict(record["n"]), "message": "Node updated"}
            return {"success": False, "error": f"Node {node_id} not found"}
        except Exception as e:
            logger.error(f"Failed to update node {node_id}: {e}")
            return {"success": False, "error": str(e)}

    def update_relationship(self, from_id, to_id, rel_type, updates):
        try:
            set_clauses = ", ".join([f"r.{k} = ${k}" for k in updates.keys()])
            query = f"""
                MATCH (a)-[r:{rel_type}]->(b)
                WHERE a.Id = $from_id AND b.Id = $to_id
                SET {set_clauses}
                RETURN r
            """
            params = {"from_id": from_id, "to_id": to_id}
            params.update(updates)
            with self._get_session() as session:
                result = session.run(query, **params)
                record = result.single()
            if record:
                return {"success": True, "data": dict(record["r"]), "message": "Relationship updated"}
            return {"success": False, "error": "Relationship not found"}
        except Exception as e:
            logger.error(f"Failed to update relationship: {e}")
            return {"success": False, "error": str(e)}

    def update_node_status(self, node_label, node_id, status):
        return self.update_node(node_label, node_id, {"Status": status})

    def batch_update(self, node_label, updates):
        try:
            updated = []
            with self._get_session() as session:
                for item in updates:
                    node_id = item.get("id")
                    item_updates = item.get("updates", {})
                    if not node_id or not item_updates:
                        continue
                    set_clauses = ", ".join([f"n.{k} = $u_{node_id}_{k}" for k in item_updates.keys()])
                    query = f"""
                        MATCH (n:{node_label} {{Id: $node_id}})
                        SET {set_clauses}
                        RETURN n
                    """
                    params = {"node_id": node_id}
                    for k, v in item_updates.items():
                        params[f"u_{node_id}_{k}"] = v
                    result = session.run(query, **params)
                    record = result.single()
                    if record:
                        updated.append(dict(record["n"]))
            return {"success": True, "data": updated, "count": len(updated), "message": f"Batch updated {len(updated)} nodes"}
        except Exception as e:
            logger.error(f"Failed to batch update: {e}")
            return {"success": False, "error": str(e)}
