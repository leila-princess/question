"""
纯 LLM 模式 API（与配置模式 routes.py 解耦）。
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file, stream_with_context

from .config import task_output_dir
from .pipeline import iter_pure_llm_sse_events, run_pure_llm_pipeline

logger = logging.getLogger(__name__)

pure_llm_bp = Blueprint("jyllm_pure_llm", __name__, url_prefix="/api/fzsj/jyllm/pure-llm")


@pure_llm_bp.route("/run", methods=["POST"])
def pure_llm_run_stream():
    """SSE：自然语言 → 工具检索 → PDDL → 规划求解 → 补全重规划 → 可视化 → 方案正文。"""
    try:
        data = request.get_json() or {}
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

    description = (data.get("description") or data.get("user_description") or "").strip()
    if not description:
        return jsonify({"success": False, "error": "description 不能为空"}), 400

    task_id = (data.get("task_id") or f"pure_{uuid.uuid4().hex[:12]}").strip()
    llm_key = (data.get("llm_model_key") or data.get("llmModelKey") or "").strip()

    def stream():
        for chunk in iter_pure_llm_sse_events(description, task_id, llm_model_key=llm_key):
            yield chunk

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "Content-Encoding": "identity",
        },
    )


@pure_llm_bp.route("/run-sync", methods=["POST"])
def pure_llm_run_sync():
    """同步执行（调试 / demo）。"""
    data = request.get_json() or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"success": False, "error": "description 不能为空"}), 400
    task_id = (data.get("task_id") or f"pure_{uuid.uuid4().hex[:12]}").strip()
    llm_key = (data.get("llm_model_key") or "").strip()
    try:
        result = run_pure_llm_pipeline(description, task_id, llm_model_key=llm_key)
        figures = {
            k: f"/api/fzsj/jyllm/pure-llm/asset/{task_id}/{Path(v).name}"
            for k, v in (result.get("figure_paths") or {}).items()
        }
        return jsonify({"success": True, "task_id": task_id, "figures": figures, "result": result})
    except Exception as e:
        logger.exception("pure_llm sync failed")
        return jsonify({"success": False, "error": str(e)}), 500


@pure_llm_bp.route("/asset/<task_id>/<filename>", methods=["GET"])
def pure_llm_asset(task_id: str, filename: str):
    safe = Path(filename).name
    path = task_output_dir(task_id) / safe
    if not path.is_file():
        return jsonify({"success": False, "error": "not found"}), 404
    mime = None
    if safe.endswith(".png"):
        mime = "image/png"
    elif safe.endswith(".html"):
        mime = "text/html; charset=utf-8"
    elif safe.endswith(".dot"):
        mime = "text/plain; charset=utf-8"
    elif safe.endswith(".pddl"):
        mime = "text/plain; charset=utf-8"
    return send_file(path, mimetype=mime)
