"""RelFact API：标准事实检索与重建。"""
from __future__ import annotations

import json
import logging

from flask import Blueprint, jsonify, request

from config import Config

from .rel_fact_builder import attach_from_parsed
from .retrieval.rel_fact_rag import retrieve as rel_fact_retrieve
from .schema import infer_file_kind

logger = logging.getLogger(__name__)

relfact_bp = Blueprint("relfact", __name__, url_prefix="/api/relfact")


@relfact_bp.route("/search", methods=["POST"])
def search():
    body = request.get_json(silent=True) or {}
    q = str(body.get("query") or body.get("q") or "").strip()
    top_k = int(body.get("top_k") or 5)
    if not q:
        return jsonify({"success": False, "error": "query required"}), 400
    try:
        result = rel_fact_retrieve(q, top_k=top_k, driver=Config.NEO4J_DRIVER)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.exception("relfact search failed")
        return jsonify({"success": False, "error": str(e)}), 500


@relfact_bp.route("/file/<file_id>/facts", methods=["GET"])
def file_facts(file_id: str):
    if not Config.NEO4J_DRIVER:
        return jsonify({"success": False, "error": "Neo4j unavailable"}), 503
    try:
        with Config.NEO4J_DRIVER.session() as session:
            row = session.run(
                """
                MATCH (f:File {Id: $id})
                RETURN f.File_kind AS file_kind, f.content_slots AS content_slots,
                       f.fact_index AS fact_index, f.extract_status AS extract_status,
                       f.Std_code AS std_code, f.Name AS name
                """,
                id=file_id,
            ).single()
        if not row:
            return jsonify({"success": False, "error": "File not found"}), 404
        data = dict(row)

        def _parse_json_field(val):
            if not val:
                return val
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    return val
            return val

        data["content_slots"] = _parse_json_field(data.get("content_slots"))
        data["fact_index"] = _parse_json_field(data.get("fact_index"))
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
