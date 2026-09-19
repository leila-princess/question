import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)


class Neo4jClearManager:
    def __init__(self, driver):
        self.driver = driver

    def _get_session(self):
        if self.driver is None:
            raise RuntimeError("Neo4j driver not initialized")
        return self.driver.session()

    def clear_all(self):
        try:
            with self._get_session() as session:
                result = session.run("MATCH (n) DETACH DELETE n")
                session.run("MATCH ()-[r]->() DELETE r")
            return {"success": True, "message": "All nodes and relationships cleared"}
        except Exception as e:
            logger.error(f"Failed to clear all: {e}")
            return {"success": False, "error": str(e)}

    def clear_by_type(self, node_type):
        try:
            with self._get_session() as session:
                session.run(f"MATCH (n:{node_type}) DETACH DELETE n")
            return {"success": True, "message": f"All {node_type} nodes cleared"}
        except Exception as e:
            logger.error(f"Failed to clear {node_type}: {e}")
            return {"success": False, "error": str(e)}

    def clear_relationships_only(self):
        try:
            with self._get_session() as session:
                session.run("MATCH ()-[r]->() DELETE r")
            return {"success": True, "message": "All relationships cleared, nodes preserved"}
        except Exception as e:
            logger.error(f"Failed to clear relationships: {e}")
            return {"success": False, "error": str(e)}
