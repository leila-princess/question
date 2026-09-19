"""图库节点选择器 API — 设备 Tab 关联。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from flask import jsonify, request, send_file

from config import Config
from blueprints.GLCX.equipment_query_shared import (
    build_indicator_nodes_for_equipment,
    build_project_nodes_for_equipment,
    flatten_indicators_for_picker,
    flatten_projects_for_picker,
    get_node_props,
)

logger = logging.getLogger(__name__)


def _run_cypher(cypher: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    if not Config.NEO4J_DRIVER:
        return []
    params = params or {}
    with Config.NEO4J_DRIVER.session() as session:
        return [dict(r) for r in session.run(cypher, **params)]


def _node_row(record: Dict[str, Any], key: str = "n") -> Dict[str, str]:
    n = record.get(key)
    if n is None:
        return {}
    props = dict(n) if hasattr(n, "items") else (n if isinstance(n, dict) else {})
    return {
        "id": props.get("Id") or props.get("id") or "",
        "name": props.get("Name") or props.get("name") or "",
        "label": props.get("Label") or props.get("label") or "",
        "type": props.get("Type") or props.get("type") or "",
    }


def register_graph_picker_routes(bp):
    @bp.route("/graph-picker/equipment-tree", methods=["GET"])
    def graph_picker_equipment_tree():
        try:
            records = _run_cypher(
                """
                MATCH (et:EquipmentType)
                OPTIONAL MATCH (et)-[:HAS|CON]->(e:Equipment)
                WITH et, collect(DISTINCT e) AS equipments
                RETURN et, equipments
                ORDER BY et.Type, et.Name
                """
            )
            first_map: Dict[str, List[Dict[str, Any]]] = {}
            for r in records:
                et = _node_row(r, "et")
                if not et.get("id"):
                    continue
                l1 = str(et.get("type") or "未分类")
                second = {
                    "id": et["id"],
                    "name": et.get("name") or et["id"],
                    "type": "EquipmentType",
                    "children": [],
                }
                for eq in r.get("equipments") or []:
                    eqp = _node_row({"n": eq}, "n")
                    if eqp.get("id"):
                        eqp["type"] = "Equipment"
                        second["children"].append(eqp)
                first_map.setdefault(l1, []).append(second)
            tree = [{"id": f"L1::{k}", "name": k, "type": "level1", "children": v} for k, v in first_map.items()]
            return jsonify({"success": True, "data": tree})
        except Exception as e:
            logger.error("equipment-tree failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    def _picker_projects_for_equipment(eq_id: str) -> List[Dict[str, str]]:
        nodes = build_project_nodes_for_equipment(
            [eq_id], run_cypher=_run_cypher, get_node_props=get_node_props
        )
        return flatten_projects_for_picker(nodes)

    def _picker_indicators_for_equipment(eq_id: str) -> List[Dict[str, str]]:
        nodes = build_indicator_nodes_for_equipment(
            [eq_id], run_cypher=_run_cypher, get_node_props=get_node_props
        )
        return flatten_indicators_for_picker(nodes)

    def _equipment_preview_payload(eq_id: str) -> Dict[str, Any]:
        """与 SSYQ 搜索一致的设备子图 + EnvironmentTable 行（含无父级项目、指标 HAS）。"""
        from blueprints.GLCX.SSYQ.routes import _build_full_graph, _get_equipment_data

        rd = _get_equipment_data(eq_id)
        if not rd or not rd.get("equipment"):
            return {"graphData": {"nodes": [], "edges": []}, "environmentRow": None}

        eq = rd["equipment"]
        table_row = {
            "type": "equipment",
            "equipmentId": eq.get("id", eq_id),
            "equipmentName": eq.get("name", eq_id),
            "projectsByEnv": rd.get("projectsByEnv") or {},
            "indicators": rd.get("indicators") or [],
            "equipments": [eq],
        }
        graph_data = _build_full_graph({}, [table_row])
        env_row = {
            "equipmentTypeId": eq.get("id", eq_id),
            "equipmentTypeName": eq.get("name", eq_id),
            "projectsByEnv": rd.get("projectsByEnv") or {},
            "indicators": rd.get("indicators") or [],
        }
        return {"graphData": graph_data, "environmentRow": env_row}

    @bp.route("/graph-picker/equipment/<equipment_id>/preview-graph", methods=["GET"])
    def graph_picker_equipment_preview_graph(equipment_id: str):
        """返回设备关联子图（与 SSYQ 语义搜索图谱结构一致）。"""
        eq_id = (equipment_id or "").strip()
        if not eq_id:
            return jsonify({"success": False, "error": "equipment_id required"}), 400
        if not Config.NEO4J_DRIVER:
            return jsonify({"success": False, "error": "Neo4j not initialized"}), 500
        try:
            payload = _equipment_preview_payload(eq_id)
            return jsonify({"success": True, "data": payload})
        except Exception as e:
            logger.error("preview-graph failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/graph-picker/equipment/<equipment_id>/related", methods=["GET"])
    def graph_picker_equipment_related(equipment_id: str):
        eq_id = (equipment_id or "").strip()
        if not eq_id:
            return jsonify({"success": False, "error": "equipment_id required"}), 400
        try:
            projects = _picker_projects_for_equipment(eq_id)
            indicators = _picker_indicators_for_equipment(eq_id)
            proj_ids = [p["id"] for p in projects if p.get("id")]
            ind_ids = [i["id"] for i in indicators if i.get("id")]

            methods: List[Dict[str, str]] = []
            if proj_ids or ind_ids:
                method_records = _run_cypher(
                    """
                    OPTIONAL MATCH (p:TestProject)-[:CON]-(m:TestMethod)
                    WHERE p.Id IN $pids
                    WITH collect(DISTINCT m) AS ms1
                    OPTIONAL MATCH (i:Indicator)-[:CON]-(m2:TestMethod)
                    WHERE i.Id IN $iids
                    WITH ms1, collect(DISTINCT m2) AS ms2
                    UNWIND (ms1 + ms2) AS m
                    WITH DISTINCT m WHERE m IS NOT NULL
                    RETURN m AS n ORDER BY m.Name
                    """,
                    {"pids": proj_ids or [""], "iids": ind_ids or [""]},
                )
                methods = [_node_row(r) for r in method_records if _node_row(r).get("id")]

            eq_info = _run_cypher(
                "MATCH (e:Equipment {Id: $id}) RETURN e AS n LIMIT 1", {"id": eq_id}
            )
            equipment = _node_row(eq_info[0], "n") if eq_info else {"id": eq_id, "name": eq_id}

            return jsonify({
                "success": True,
                "data": {
                    "equipment": equipment,
                    "projects": [_node_row(r) for r in projects if _node_row(r).get("id")],
                    "indicators": [_node_row(r) for r in indicators if _node_row(r).get("id")],
                    "methods": methods,
                },
            })
        except Exception as e:
            logger.error("equipment related failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/graph-picker/environments", methods=["GET"])
    def graph_picker_environments():
        try:
            records = _run_cypher("MATCH (e:Environment) RETURN e AS n ORDER BY e.Name")
            envs = [_node_row(r) for r in records if _node_row(r).get("id")]
            return jsonify({"success": True, "data": envs})
        except Exception as e:
            logger.error("environments failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/graph-picker/equipment-type/<type_id>/equipments", methods=["GET"])
    def graph_picker_equipments_under_type(type_id: str):
        """列出设备类型下的具体 Equipment 节点。"""
        tid = (type_id or "").strip()
        if not tid:
            return jsonify({"success": False, "error": "type_id required"}), 400
        if not Config.NEO4J_DRIVER:
            return jsonify({"success": False, "error": "Neo4j not initialized"}), 500
        try:
            records = _run_cypher(
                """
                MATCH (et:EquipmentType {Id: $tid})
                OPTIONAL MATCH (et)-[:HAS|CON]->(e:Equipment)
                WITH collect(DISTINCT e) AS eqs
                UNWIND eqs AS e
                WITH e WHERE e IS NOT NULL
                RETURN e AS n ORDER BY e.Name
                """,
                {"tid": tid},
            )
            nodes = [_node_row(r) for r in records if _node_row(r).get("id")]
            return jsonify({"success": True, "data": nodes, "equipment_type_id": tid})
        except Exception as e:
            logger.error("equipments_under_type failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    _PICKER_NODE_TYPES = frozenset({
        "TestProject", "Indicator", "TestMethod", "Environment", "Equipment", "EquipmentType",
    })

    @bp.route("/graph-picker/nodes", methods=["GET"])
    def graph_picker_nodes():
        """按类型列出节点；项目/指标/方法可按 equipment_id 过滤，环境不限。"""
        node_type = (request.args.get("node_type") or "").strip()
        equipment_id = (request.args.get("equipment_id") or "").strip()
        if node_type not in _PICKER_NODE_TYPES:
            return jsonify({"success": False, "error": f"unsupported node_type: {node_type}"}), 400
        if not Config.NEO4J_DRIVER:
            return jsonify({"success": False, "error": "Neo4j not initialized"}), 500
        try:
            if node_type == "Environment":
                records = _run_cypher("MATCH (n:Environment) RETURN n AS n ORDER BY n.Name")
                nodes = [_node_row(r) for r in records if _node_row(r).get("id")]
                return jsonify({"success": True, "data": nodes, "scoped": False})

            if equipment_id and node_type in ("TestProject", "Indicator", "TestMethod"):
                eq_check = _run_cypher(
                    "MATCH (e:Equipment {Id: $id}) RETURN e AS n LIMIT 1",
                    {"id": equipment_id},
                )
                if not eq_check:
                    return jsonify({
                        "success": True,
                        "data": [],
                        "scoped": True,
                        "warning": "请先选择具体设备（Equipment），不能使用设备类型 ID",
                    })
                if node_type == "TestProject":
                    nodes = _picker_projects_for_equipment(equipment_id)
                    return jsonify({"success": True, "data": nodes, "scoped": True})
                elif node_type == "Indicator":
                    nodes = _picker_indicators_for_equipment(equipment_id)
                    return jsonify({"success": True, "data": nodes, "scoped": True})
                else:
                    pids = [p["id"] for p in _picker_projects_for_equipment(equipment_id) if p.get("id")]
                    iids = [i["id"] for i in _picker_indicators_for_equipment(equipment_id) if i.get("id")]
                    if pids or iids:
                        records = _run_cypher(
                            """
                            OPTIONAL MATCH (p:TestProject)-[:CON]-(m:TestMethod)
                            WHERE p.Id IN $pids
                            WITH collect(DISTINCT m) AS ms1
                            OPTIONAL MATCH (i:Indicator)-[:CON]-(m2:TestMethod)
                            WHERE i.Id IN $iids
                            WITH ms1, collect(DISTINCT m2) AS ms2
                            UNWIND (ms1 + ms2) AS m
                            WITH DISTINCT m WHERE m IS NOT NULL
                            RETURN m AS n ORDER BY m.Name
                            """,
                            {"pids": pids or [""], "iids": iids or [""]},
                        )
                    else:
                        records = []
                nodes = [_node_row(r) for r in records if _node_row(r).get("id")]
                return jsonify({"success": True, "data": nodes, "scoped": True})

            records = _run_cypher(
                f"MATCH (n:{node_type}) RETURN n AS n ORDER BY n.Name"
            )
            nodes = [_node_row(r) for r in records if _node_row(r).get("id")]
            return jsonify({"success": True, "data": nodes, "scoped": False})
        except Exception as e:
            logger.error("graph_picker_nodes failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/graph-picker/resolve-equipment", methods=["GET"])
    def graph_picker_resolve_equipment():
        """按名称模糊匹配设备节点（推荐仅有名称时使用）。"""
        name = (request.args.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "name required"}), 400
        if not Config.NEO4J_DRIVER:
            return jsonify({"success": False, "error": "Neo4j not initialized"}), 500
        try:
            records = _run_cypher(
                """
                MATCH (e:Equipment)
                WHERE e.Name = $name OR e.Name CONTAINS $name OR $name CONTAINS e.Name
                RETURN e AS n
                ORDER BY CASE WHEN e.Name = $name THEN 0 ELSE 1 END, size(e.Name)
                LIMIT 5
                """,
                {"name": name},
            )
            nodes = [_node_row(r) for r in records if _node_row(r).get("id")]
            if not nodes:
                return jsonify({"success": False, "error": "未找到匹配设备"}), 404
            return jsonify({"success": True, "data": nodes[0], "candidates": nodes})
        except Exception as e:
            logger.error("resolve_equipment failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/preview-figure", methods=["GET"])
    def preview_figure():
        path = request.args.get("path") or ""
        if not path or not os.path.isfile(path):
            return jsonify({"success": False, "error": "not found"}), 404
        norm = os.path.normpath(path).lower()
        allowed = ("temp", "tmp", "ocr", "output_json", "wjgl")
        if not any(a in norm for a in allowed):
            return jsonify({"success": False, "error": "forbidden"}), 403
        return send_file(path)
