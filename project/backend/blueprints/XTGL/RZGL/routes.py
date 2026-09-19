# -*- coding: utf-8 -*-
"""
日志管理 API。业务侧增删改请使用：
  from utils.operation_log import log_user_action
"""
import os

from flask import Blueprint, jsonify, request, send_file

from utils.operation_log import get_action_log_path, query_logs

rzgl_bp = Blueprint("rzgl", __name__, url_prefix="/api/xtgl/rzgl")


@rzgl_bp.route("/logs", methods=["GET"])
def list_logs():
    try:
        level = request.args.get("level") or ""
        keyword = request.args.get("keyword") or ""
        page = int(request.args.get("page", 1))
        size = int(request.args.get("size", 20))
        rows, total = query_logs(level=level or None, keyword=keyword, page=page, size=size)
        return jsonify(
            {
                "success": True,
                "data": {"list": rows, "total": total, "page": page, "size": size},
            }
        )
    except ValueError:
        return jsonify({"success": False, "error": "page/size 参数无效"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@rzgl_bp.route("/logs/download", methods=["GET"])
def download_logs():
    path = get_action_log_path()
    if not os.path.isfile(path):
        return jsonify({"success": False, "error": "日志文件不存在"}), 404
    return send_file(
        path,
        as_attachment=True,
        download_name="app.log",
        mimetype="text/plain; charset=utf-8",
    )
