# -*- coding: utf-8 -*-
"""操作与异常日志：按天滚动的 JSON 行文件（logs/app.log）。"""
from __future__ import annotations

import glob
import json
import logging
import os
import threading
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Dict, List, Optional, Tuple

import jwt
from flask import has_request_context, request

from config import Config

# 与 blueprints.Login.routes 中签发 token 的密钥保持一致（若未统一至 Config）
_LOGIN_JWT_FALLBACK = "your-secret-key-here-change-in-production"

_BACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(_BACK_DIR, "logs")
LOG_BASENAME = "app.log"
LOG_PATH = os.path.join(LOG_DIR, LOG_BASENAME)

_init_lock = threading.Lock()
_logger: Optional[logging.Logger] = None


def init_operation_logging() -> None:
    """在应用启动时调用：注册按天滚动的 JSON 行日志。"""
    global _logger
    with _init_lock:
        if _logger is not None:
            return
        os.makedirs(LOG_DIR, exist_ok=True)
        lg = logging.getLogger("app_actions")
        lg.setLevel(logging.DEBUG)
        lg.propagate = False
        lg.handlers.clear()
        handler = TimedRotatingFileHandler(
            LOG_PATH,
            when="midnight",
            interval=1,
            backupCount=90,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(handler)
        _logger = lg


def get_action_log_path() -> str:
    return LOG_PATH


def resolve_request_user(fallback: str = "anonymous") -> str:
    """从当前请求的 JWT 中解析 user_id，失败则返回 fallback。"""
    try:
        if not has_request_context():
            return fallback
        token = (request.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        if not token:
            return fallback
        for secret in (Config.SECRET_KEY, _LOGIN_JWT_FALLBACK):
            if not secret:
                continue
            try:
                payload = jwt.decode(token, secret, algorithms=["HS256"])
                uid = payload.get("user_id")
                return uid if uid else fallback
            except jwt.InvalidTokenError:
                continue
        return fallback
    except Exception:
        return fallback


def log_action(
    user: str,
    action: str,
    result: Optional[str] = None,
    level: str = "INFO",
    error: Optional[str] = None,
    **extra: Any,
) -> None:
    """
    写入一行 JSON 日志。level 为 INFO/ERROR 等；异常场景可传 error=。
    """
    if _logger is None:
        init_operation_logging()
    assert _logger is not None
    payload: Dict[str, Any] = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": (level or "INFO").upper(),
        "user": user or "system",
        "action": action,
    }
    if error is not None:
        payload["error"] = str(error)
    elif result is not None:
        payload["result"] = str(result)
    if extra:
        for k, v in extra.items():
            if k in payload:
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = str(v)
    line = json.dumps(payload, ensure_ascii=False)
    _logger.info(line)


def log_user_action(
    action: str,
    *,
    result: Optional[str] = "success",
    level: str = "INFO",
    error: Optional[str] = None,
    user: Optional[str] = None,
    **extra: Any,
) -> None:
    """从当前请求解析用户并写入操作日志；失败不影响主流程。"""
    try:
        uid = user if user is not None else resolve_request_user()
        log_action(uid, action, result=result, level=level, error=error, **extra)
    except Exception:
        logging.getLogger(__name__).warning("log_user_action 写入失败", exc_info=True)


def _iter_log_file_paths() -> List[str]:
    os.makedirs(LOG_DIR, exist_ok=True)
    paths = glob.glob(os.path.join(LOG_DIR, "app.log*"))
    paths = [p for p in paths if os.path.isfile(p)]
    paths.sort(key=lambda p: os.path.getmtime(p))
    return paths


def _parse_line(line: str) -> Optional[Dict[str, Any]]:
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def query_logs(
    level: Optional[str] = None,
    keyword: str = "",
    page: int = 1,
    size: int = 20,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    读取所有滚动日志中的 JSON 行，按时间倒序，筛选后分页。
    level 为空或 ALL 表示不过滤；keyword 对 action 子串匹配（忽略大小写）。
    """
    entries: List[Dict[str, Any]] = []
    for path in _iter_log_file_paths():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    obj = _parse_line(line)
                    if obj is None:
                        continue
                    entries.append(obj)
        except OSError:
            continue

    entries.sort(key=lambda e: e.get("time") or "", reverse=True)

    lv = (level or "").strip().upper()
    if lv and lv != "ALL":
        entries = [e for e in entries if (e.get("level") or "").upper() == lv]

    kw = (keyword or "").strip().lower()
    if kw:
        entries = [e for e in entries if kw in (e.get("action") or "").lower()]

    total = len(entries)
    page = max(1, page)
    size = max(1, min(size, 500))
    start = (page - 1) * size
    return entries[start : start + size], total
