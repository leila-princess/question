# -*- coding: utf-8 -*-
"""自动全量备份：每日定时触发（配置持久化 JSON + 后台守护线程）。"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, time as dt_time
from typing import Any, Callable, Dict, Optional

from config import PROJECT_ROOT

logger = logging.getLogger(__name__)

_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "hour": 2,
    "minute": 0,
    "last_autorun_ymd": "",
}

_schedule_lock = threading.Lock()
_scheduler_started = False
_scheduler_lock = threading.Lock()


def schedule_file_path() -> str:
    inst = os.path.join(PROJECT_ROOT, "backend", "instance")
    return os.path.join(inst, "sjbf_auto_backup.json")


def _ensure_instance_dir() -> str:
    path = schedule_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def load_schedule() -> Dict[str, Any]:
    path = _ensure_instance_dir()
    if not os.path.isfile(path):
        return dict(_DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        out = dict(_DEFAULTS)
        if isinstance(raw, dict):
            out["enabled"] = bool(raw.get("enabled", False))
            h = int(raw.get("hour", 2))
            m = int(raw.get("minute", 0))
            out["hour"] = max(0, min(23, h))
            out["minute"] = max(0, min(59, m))
            last = raw.get("last_autorun_ymd")
            out["last_autorun_ymd"] = last if isinstance(last, str) else ""
        return out
    except Exception as e:
        logger.warning("读取自动备份配置失败，使用默认: %s", e)
        return dict(_DEFAULTS)


def save_schedule(data: Dict[str, Any]) -> Dict[str, Any]:
    path = _ensure_instance_dir()
    with _schedule_lock:
        current = load_schedule()
        current["enabled"] = bool(data.get("enabled", current["enabled"]))
        h = int(data.get("hour", current["hour"]))
        m = int(data.get("minute", current["minute"]))
        current["hour"] = max(0, min(23, h))
        current["minute"] = max(0, min(59, m))
        if "last_autorun_ymd" in data and isinstance(data["last_autorun_ymd"], str):
            current["last_autorun_ymd"] = data["last_autorun_ymd"]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return load_schedule()


def mark_autorun_done(ymd: str) -> None:
    with _schedule_lock:
        cur = load_schedule()
        cur["last_autorun_ymd"] = ymd
        path = schedule_file_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def today_ymd() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def should_fire_autobackup_now(cfg: Dict[str, Any]) -> bool:
    if not cfg.get("enabled"):
        return False
    last = (cfg.get("last_autorun_ymd") or "").strip()
    today = today_ymd()
    if last == today:
        return False
    h = int(cfg.get("hour", 2))
    m = int(cfg.get("minute", 0))
    now = datetime.now()
    slot = datetime.combine(now.date(), dt_time(hour=h, minute=m))
    return now >= slot


def start_scheduler_thread(app, trigger_backup: Callable[[], None]) -> None:
    """在 create_app 中调用一次；按服务器本地时间每日至多触发一次。"""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def loop() -> None:
        interval = 45.0
        while True:
            try:
                time.sleep(interval)
                with app.app_context():
                    try:
                        trigger_backup()
                    except Exception:
                        logger.exception("自动备份触发失败")
            except Exception:
                logger.exception("自动备份调度循环异常")

    t = threading.Thread(target=loop, name="sjbf-auto-backup", daemon=True)
    t.start()
    logger.info("自动备份调度线程已启动（配置: %s）", schedule_file_path())
