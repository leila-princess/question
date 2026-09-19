import logging
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)

ID_PREFIXES = {
    "EquipmentType": "ZBLX",
    "Equipment": "ZB",
    "Environment": "HJ",
    "TestProject": "XM",
    "Indicator": "ZB",
    "TestMethod": "FF",
    "File": "WJ",
}


class Neo4jInputManager:
    def __init__(self, driver):
        self.driver = driver

    def _get_session(self):
        if self.driver is None:
            raise RuntimeError("Neo4j driver not initialized")
        return self.driver.session()

    def _now(self):
        return datetime.now().isoformat()

    def generate_id(self, prefix, counter):
        return f"{prefix}_{counter:04d}"

    def _create_node(self, label, data):
        props = ", ".join([f"{k} = ${k}" for k in data.keys()])
        query = f"CREATE (n:{label} {{{props}}}) RETURN n"
        return query

    def create_equipment_type(self, data):
        try:
            node_data = {
                "Id": data.get("Id", ""),
                "Name": data.get("Name", ""),
                "Label": data.get("Label", ""),
                "Description": data.get("Description", ""),
                "Type": data.get("Type", ""),
                "Status": data.get("Status", "启用"),
                "User_id": data.get("User_id", ""),
                "Create_time": self._now(),
            }
            with self._get_session() as session:
                result = session.run(self._create_node("EquipmentType", node_data), **node_data)
                record = result.single()
            return {"success": True, "data": dict(record["n"]) if record else None, "message": "EquipmentType created"}
        except Exception as e:
            logger.error(f"Failed to create EquipmentType: {e}")
            return {"success": False, "error": str(e)}

    def create_equipment(self, data):
        try:
            node_data = {
                "Id": data.get("Id", ""),
                "Name": data.get("Name", ""),
                "Label": data.get("Label", ""),
                "Type": data.get("Type", ""),
                "Description": data.get("Description", ""),
                "Status": data.get("Status", "启用"),
                "User_id": data.get("User_id", ""),
                "Create_time": self._now(),
            }
            with self._get_session() as session:
                result = session.run(self._create_node("Equipment", node_data), **node_data)
                record = result.single()
            return {"success": True, "data": dict(record["n"]) if record else None, "message": "Equipment created"}
        except Exception as e:
            logger.error(f"Failed to create Equipment: {e}")
            return {"success": False, "error": str(e)}

    def create_environment(self, data):
        try:
            node_data = {
                "Id": data.get("Id", ""),
                "Name": data.get("Name", ""),
                "Label": data.get("Label", ""),
                "Description": data.get("Description", ""),
                "Type": data.get("Type", ""),
                "Class": data.get("Class", ""),
                "Limit": data.get("Limit", ""),
                "Request": data.get("Request", ""),
                "Status": data.get("Status", "启用"),
                "User_id": data.get("User_id", ""),
                "Create_time": self._now(),
            }
            with self._get_session() as session:
                result = session.run(self._create_node("Environment", node_data), **node_data)
                record = result.single()
            return {"success": True, "data": dict(record["n"]) if record else None, "message": "Environment created"}
        except Exception as e:
            logger.error(f"Failed to create Environment: {e}")
            return {"success": False, "error": str(e)}

    def create_test_project(self, data):
        try:
            node_data = {
                "Id": data.get("Id", ""),
                "Name": data.get("Name", ""),
                "Type": data.get("Type", ""),
                "Label": data.get("Label", ""),
                "Description": data.get("Description", ""),
                "Status": data.get("Status", "启用"),
                "User_id": data.get("User_id", ""),
                "Create_time": self._now(),
            }
            with self._get_session() as session:
                result = session.run(self._create_node("TestProject", node_data), **node_data)
                record = result.single()
            return {"success": True, "data": dict(record["n"]) if record else None, "message": "TestProject created"}
        except Exception as e:
            logger.error(f"Failed to create TestProject: {e}")
            return {"success": False, "error": str(e)}

    def create_indicator(self, data):
        try:
            node_data = {
                "Id": data.get("Id", ""),
                "Name": data.get("Name", ""),
                "Label": data.get("Label", ""),
                "Description": data.get("Description", ""),
                "Type": data.get("Type", ""),
                "Status": data.get("Status", "启用"),
                "User_id": data.get("User_id", ""),
                "Create_time": self._now(),
            }
            with self._get_session() as session:
                result = session.run(self._create_node("Indicator", node_data), **node_data)
                record = result.single()
            return {"success": True, "data": dict(record["n"]) if record else None, "message": "Indicator created"}
        except Exception as e:
            logger.error(f"Failed to create Indicator: {e}")
            return {"success": False, "error": str(e)}

    def create_test_method(self, data):
        try:
            node_data = {
                "Id": data.get("Id", ""),
                "Name": data.get("Name", ""),
                "Type": data.get("Type", ""),
                "Label": data.get("Label", ""),
                "Description": data.get("Description", ""),
                "Source_name": data.get("Source_name", ""),
                "Source_id": data.get("Source_id", ""),
                "Status": data.get("Status", "启用"),
                "User_id": data.get("User_id", ""),
                "Create_time": self._now(),
            }
            with self._get_session() as session:
                result = session.run(self._create_node("TestMethod", node_data), **node_data)
                record = result.single()
            return {"success": True, "data": dict(record["n"]) if record else None, "message": "TestMethod created"}
        except Exception as e:
            logger.error(f"Failed to create TestMethod: {e}")
            return {"success": False, "error": str(e)}

    def create_relationship(self, rel_type, from_id, to_id, from_label, to_label, props=None):
        try:
            props_set = ""
            if props:
                props_set = "SET r += $props"
            query = f"""
                MATCH (a:{from_label} {{Id: $from_id}})
                MATCH (b:{to_label} {{Id: $to_id}})
                CREATE (a)-[r:{rel_type}]->(b)
                {props_set}
                RETURN r
            """
            params = {"from_id": from_id, "to_id": to_id}
            if props:
                params["props"] = props
            with self._get_session() as session:
                result = session.run(query, **params)
                record = result.single()
            return {"success": True, "data": dict(record["r"]) if record else None, "message": "Relationship created"}
        except Exception as e:
            logger.error(f"Failed to create relationship: {e}")
            return {"success": False, "error": str(e)}

    def batch_create_nodes(self, node_label, data_list):
        try:
            created = []
            with self._get_session() as session:
                for data in data_list:
                    props = ", ".join([f"{k} = ${k}_{i}" for k in data.keys()])
                    query = f"CREATE (n:{node_label} {{{props}}}) RETURN n"
                    params = {f"{k}_{i}": v for k, v in data.items()}
                    result = session.run(query, **params)
                    record = result.single()
                    if record:
                        created.append(dict(record["n"]))
            return {"success": True, "data": created, "count": len(created), "message": f"Batch created {len(created)} {node_label} nodes"}
        except Exception as e:
            logger.error(f"Failed to batch create {node_label}: {e}")
            return {"success": False, "error": str(e)}
