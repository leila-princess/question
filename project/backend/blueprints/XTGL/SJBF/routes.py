# -*- coding: utf-8 -*-
"""
数据备份 API：
  GET  /api/backups
  POST /api/backup
  GET  /api/backup/<task_id>
  POST /api/restore
  GET  /api/restore/<task_id>
  GET  /api/backup/schedule
  PUT  /api/backup/schedule
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, time
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from utils.operation_log import log_action, resolve_request_user

from .backup_runner import backup_root, retention_days, run_full_backup, scan_backup_days
from .restore_runner import run_full_restore
from .backup_schedule import (
    load_schedule,
    mark_autorun_done,
    save_schedule,
    schedule_file_path,
    should_fire_autobackup_now,
    start_scheduler_thread,
    today_ymd,
)

logger = logging.getLogger(__name__)

sjbf_bp = Blueprint("sjbf", __name__, url_prefix="/api")

_tasks_lock = threading.Lock()
_tasks: Dict[str, Dict[str, Any]] = {}
_restore_tasks_lock = threading.Lock()
_restore_tasks: Dict[str, Dict[str, Any]] = {}
_MAX_TASKS = 80


def _new_task(*, from_scheduler: bool) -> str:
    tid = uuid.uuid4().hex[:12]
    with _tasks_lock:
        while len(_tasks) >= _MAX_TASKS:
            oldest = min(_tasks.keys(), key=lambda k: _tasks[k].get("created") or "")
            _tasks.pop(oldest, None)
        _tasks[tid] = {
            "task_id": tid,
            "status": "pending",
            "created": datetime.now().isoformat(timespec="seconds"),
            "started": None,
            "finished": None,
            "date": None,
            "parts": {},
            "error": None,
            "from_scheduler": from_scheduler,
        }
        if from_scheduler:
            _tasks[tid]["_wait_backup_lock"] = True
    return tid


def _enqueue_backup(*, from_scheduler: bool) -> str:
    tid = _new_task(from_scheduler=from_scheduler)
    with _tasks_lock:
        task = _tasks[tid]

    def job() -> None:
        run_full_backup(task, _tasks_lock)
        try:
            with _tasks_lock:
                t = _tasks.get(tid, {})
                st = t.get("status")
                parts = t.get("parts") or {}
                errs = [
                    f"{k}:{v.get('error', '')}"
                    for k, v in parts.items()
                    if isinstance(v, dict) and v.get("status") == "error"
                ]
                user = "auto_backup" if from_scheduler else resolve_request_user()
                if st == "failed" and t.get("error"):
                    log_action(
                        user,
                        "全量数据备份",
                        level="ERROR",
                        error=str(t.get("error")),
                    )
                elif errs:
                    log_action(
                        user,
                        "全量数据备份",
                        level="ERROR",
                        error="; ".join(errs),
                    )
                elif st == "completed":
                    log_action(user, "全量数据备份", result="success")
        except Exception:
            pass

    th = threading.Thread(target=job, daemon=True)
    th.start()
    return tid


def trigger_scheduled_full_backup() -> None:
    """由调度线程调用：若当日未到点或已执行则直接返回。"""
    cfg = load_schedule()
    if not should_fire_autobackup_now(cfg):
        return
    day = today_ymd()
    mark_autorun_done(day)
    _enqueue_backup(from_scheduler=True)
    logger.info("已按计划触发自动全量备份（日期标记 %s）", day)


def register_sjbf_scheduler(app) -> None:
    start_scheduler_thread(app, trigger_scheduled_full_backup)


def _schedule_response() -> Dict[str, Any]:
    cfg = load_schedule()
    h, m = int(cfg["hour"]), int(cfg["minute"])
    today = datetime.now().date()
    slot = datetime.combine(today, time(hour=h, minute=m))
    now = datetime.now()
    last = (cfg.get("last_autorun_ymd") or "").strip()
    note_next = ""
    if cfg.get("enabled"):
        if last == today_ymd():
            note_next = "今日已触发过自动备份（按服务器日期）。"
        elif now < slot:
            note_next = f"下次将在今日 {h:02d}:{m:02d} 之后首次检查到即触发。"
        else:
            note_next = "已过今日计划时刻且尚未执行：将在数分钟内触发（若服务刚启动可能略有延迟）。"
    return {
        "enabled": cfg.get("enabled", False),
        "hour": h,
        "minute": m,
        "last_autorun_ymd": last,
        "backup_root": backup_root(),
        "retention_days": retention_days(),
        "schedule_file": schedule_file_path(),
        "server_time_note": note_next,
    }


@sjbf_bp.route("/backups", methods=["GET"])
def list_backups():
    try:
        data = scan_backup_days()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjbf_bp.route("/backup/schedule", methods=["GET"])
def get_backup_schedule():
    try:
        return jsonify({"success": True, "data": _schedule_response()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjbf_bp.route("/backup/schedule", methods=["PUT"])
def put_backup_schedule():
    try:
        body = request.get_json(silent=True) or {}
        save_schedule(body)
        log_action(resolve_request_user(), "修改自动备份计划", result="success")
        return jsonify({"success": True, "data": _schedule_response()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjbf_bp.route("/backup", methods=["POST"])
def start_backup():
    try:
        tid = _enqueue_backup(from_scheduler=False)
        return (
            jsonify({"success": True, "data": {"task_id": tid, "message": "备份任务已提交"}}),
            202,
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjbf_bp.route("/backup/<task_id>", methods=["GET"])
def backup_status(task_id: str):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return jsonify({"success": False, "error": "任务不存在"}), 404
    out = {
        k: v
        for k, v in task.items()
        if k not in ("task_id", "_wait_backup_lock", "from_scheduler")
    }
    out["task_id"] = task_id
    return jsonify({"success": True, "data": out})


def _new_restore_task() -> str:
    tid = uuid.uuid4().hex[:12]
    with _restore_tasks_lock:
        while len(_restore_tasks) >= _MAX_TASKS:
            oldest = min(
                _restore_tasks.keys(),
                key=lambda k: _restore_tasks[k].get("created") or "",
            )
            _restore_tasks.pop(oldest, None)
        _restore_tasks[tid] = {
            "task_id": tid,
            "status": "pending",
            "created": datetime.now().isoformat(timespec="seconds"),
            "started": None,
            "finished": None,
            "date": None,
            "parts": {},
            "error": None,
        }
    return tid


def _enqueue_restore(date_str: str) -> str:
    tid = _new_restore_task()
    with _restore_tasks_lock:
        task = _restore_tasks[tid]

    def job() -> None:
        run_full_restore(task, _restore_tasks_lock, date_str)
        try:
            with _restore_tasks_lock:
                t = _restore_tasks.get(tid, {})
                st = t.get("status")
                parts = t.get("parts") or {}
                errs = [
                    f"{k}:{v.get('error', '')}"
                    for k, v in parts.items()
                    if isinstance(v, dict) and v.get("status") == "error"
                ]
                user = resolve_request_user()
                if st == "failed" and t.get("error"):
                    log_action(
                        user,
                        "全量数据恢复",
                        level="ERROR",
                        error=str(t.get("error")),
                    )
                elif errs:
                    log_action(
                        user,
                        "全量数据恢复",
                        level="ERROR",
                        error="; ".join(errs),
                    )
                elif st == "completed":
                    log_action(user, "全量数据恢复", result="success")
        except Exception:
            pass

    th = threading.Thread(target=job, daemon=True)
    th.start()
    return tid


@sjbf_bp.route("/restore", methods=["POST"])
def start_restore():
    try:
        body = request.get_json(silent=True) or {}
        date_str = (body.get("date") or "").strip()
        if not date_str:
            return jsonify({"success": False, "error": "请指定备份日期 date"}), 400
        tid = _enqueue_restore(date_str)
        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "task_id": tid,
                        "date": date_str,
                        "message": "恢复任务已提交，将覆盖当前数据库数据",
                    },
                }
            ),
            202,
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjbf_bp.route("/restore/<task_id>", methods=["GET"])
def restore_status(task_id: str):
    with _restore_tasks_lock:
        task = _restore_tasks.get(task_id)
    if not task:
        return jsonify({"success": False, "error": "任务不存在"}), 404
    out = {
        k: v
        for k, v in task.items()
        if k not in ("task_id", "_wait_restore_lock")
    }
    out["task_id"] = task_id
    return jsonify({"success": True, "data": out})
