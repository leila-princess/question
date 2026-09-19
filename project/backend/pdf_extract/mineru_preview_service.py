"""上传弹窗：后台 MinerU 解析 + 本地 Markdown 落盘（不阻塞 fitz 快速预览）。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_TASKS: Dict[str, Dict[str, Any]] = {}
_TASK_STATUS_FILE = ".task_status.json"
# 无心跳超过此时长视为任务已中断（后端重启或线程崩溃）
_STALE_RUNNING_SEC = int(os.environ.get("MINERU_PREVIEW_STALE_SEC", "7200"))
# 心跳略慢仍视为运行中（MinerU 大 PDF 可能数分钟无 Flask 侧更新）
_HEARTBEAT_STALE_SEC = int(os.environ.get("MINERU_PREVIEW_HEARTBEAT_STALE_SEC", "600"))
_PARSE_MODE_MINERU = "pdf_mineru"
_PARSE_MODE_FITZ_DRAFT = "fitz_preview_draft"


def _reuse_mineru_cache() -> bool:
    """为 true 时同 PDF 命中历史 MinerU 结果则不再重新解析（默认 false）。"""
    return (os.environ.get("MINERU_PREVIEW_REUSE_CACHE") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _read_preview_meta(local_dir: Path) -> Dict[str, Any]:
    meta_path = local_dir / "meta.json"
    if not meta_path.is_file():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _mineru_complete_on_disk(cache_key: str) -> bool:
    local_dir = preview_local_dir(cache_key)
    md = local_dir / "document.md"
    if not md.is_file():
        return False
    return str(_read_preview_meta(local_dir).get("parse_mode") or "") == _PARSE_MODE_MINERU


def _task_status_path(cache_key: str) -> Path:
    return preview_local_dir(cache_key) / _TASK_STATUS_FILE


def _persist_task(cache_key: str, task: Dict[str, Any]) -> None:
    """落盘任务状态，避免 Flask 重载后内存丢失导致前端一直轮询 missing。"""
    try:
        local_dir = preview_local_dir(cache_key)
        local_dir.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in task.items() if k not in ("markdown", "html")}
        payload["cache_key"] = cache_key
        payload["updated_at"] = time.time()
        _task_status_path(cache_key).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("[MinerU-PREVIEW] persist task failed key=%s: %s", cache_key, e)


def _load_persisted_task(cache_key: str) -> Optional[Dict[str, Any]]:
    path = _task_status_path(cache_key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning("[MinerU-PREVIEW] load task failed key=%s: %s", cache_key, e)
        return None


def _stale_running_task(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """运行中任务过久无心跳 → 视为中断。"""
    if task.get("status") != "running":
        return None
    heartbeat = task.get("heartbeat_at") or task.get("updated_at") or task.get("started_at")
    if not isinstance(heartbeat, (int, float)):
        return None
    if time.time() - float(heartbeat) <= _STALE_RUNNING_SEC:
        return None
    return {
        "status": "error",
        "cache_key": task.get("cache_key"),
        "error": (
            f"MinerU 解析任务已中断（超过 {_STALE_RUNNING_SEC // 60} 分钟无响应，"
            "可能因后端重启或解析进程异常退出）。请重新上传文件。"
        ),
    }


def preview_local_dir(cache_key: str) -> Path:
    from pdf_extract.mineru_wjgl_service import project_root

    base = (os.environ.get("WJGL_LOCAL_PARSE_ROOT") or "").strip()
    if not base:
        base = str(project_root() / "data" / "wjgl_documents")
    base_path = Path(base)
    if not base_path.is_absolute():
        base_path = project_root() / base_path
    return (base_path / "_preview" / cache_key).resolve()


def file_cache_key(file_path: str) -> str:
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def save_preview_draft_html(cache_key: str, html: str) -> None:
    """
    fitz 秒级预览 HTML 落盘为草稿，避免目录里长时间只有 .task_status.json。
    MinerU 完成后会写入 document.md 并覆盖为正式 HTML。
    """
    content = (html or "").strip()
    if not content:
        return
    local_dir = preview_local_dir(cache_key)
    local_dir.mkdir(parents=True, exist_ok=True)
    md_path = local_dir / "document.md"
    if md_path.is_file():
        return
    html_path = local_dir / "document.html"
    meta_path = local_dir / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if str(meta.get("parse_mode") or "") == "pdf_mineru":
                return
        except Exception:
            pass
    html_path.write_text(content, encoding="utf-8")
    meta = {
        "parse_mode": "fitz_preview_draft",
        "html_bytes": len(content.encode("utf-8")),
        "updated_at": time.time(),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[MinerU-PREVIEW] saved fitz draft html key=%s bytes=%s", cache_key, meta["html_bytes"])


def get_preview_task(cache_key: str, *, include_content: bool = False) -> Dict[str, Any]:
    with _LOCK:
        task = _TASKS.get(cache_key)
        if not task:
            task = _load_persisted_task(cache_key)
            if task:
                stale = _stale_running_task(task)
                if stale:
                    _TASKS[cache_key] = stale
                    _persist_task(cache_key, stale)
                    return dict(stale)
                if task.get("status") == "running":
                    hb = task.get("heartbeat_at") or task.get("updated_at")
                    if isinstance(hb, (int, float)):
                        hb_age = time.time() - float(hb)
                        if hb_age > _STALE_RUNNING_SEC:
                            interrupted = {
                                "status": "error",
                                "cache_key": cache_key,
                                "error": (
                                    f"MinerU 解析任务已中断（超过 {_STALE_RUNNING_SEC // 60} 分钟无心跳）。"
                                    "可点「继续等待」重试轮询，或「强制重新解析」。"
                                ),
                            }
                            _TASKS[cache_key] = interrupted
                            _persist_task(cache_key, interrupted)
                            return dict(interrupted)
                        if hb_age > _HEARTBEAT_STALE_SEC:
                            task = dict(task)
                            task["stale_heartbeat"] = True
                            task["message"] = (
                                "MinerU 可能仍在后台解析（心跳较慢），请继续等待或稍后点「继续等待」"
                            )
                    _TASKS[cache_key] = task
        if not task:
            local_dir = preview_local_dir(cache_key)
            md = local_dir / "document.md"
            meta = _read_preview_meta(local_dir)
            parse_mode = str(meta.get("parse_mode") or "")
            if md.is_file() and parse_mode == _PARSE_MODE_MINERU:
                html_path = local_dir / "document.html"
                toc: list = []
                try:
                    from pdf_extract.mineru_wjgl_service import load_local_toc

                    toc = load_local_toc("_preview", cache_key)
                except Exception:
                    pass
                done: Dict[str, Any] = {
                    "status": "done",
                    "cache_key": cache_key,
                    "local_dir": str(local_dir),
                    "md_path": str(md),
                    "has_markdown": True,
                    "has_html": html_path.is_file(),
                    "markdown_bytes": md.stat().st_size if md.is_file() else 0,
                    "html_bytes": html_path.stat().st_size if html_path.is_file() else 0,
                    "toc": toc,
                    "parse_mode": _PARSE_MODE_MINERU,
                    "mineru_cached": True,
                    "from_disk": True,
                }
                if include_content:
                    done["markdown"] = md.read_text(encoding="utf-8", errors="replace")
                    done["html"] = (
                        html_path.read_text(encoding="utf-8", errors="replace")
                        if html_path.is_file()
                        else ""
                    )
                _TASKS[cache_key] = done
                return done
            html_path = local_dir / "document.html"
            if html_path.is_file() and parse_mode == _PARSE_MODE_FITZ_DRAFT:
                return {
                    "status": "running",
                    "cache_key": cache_key,
                    "local_dir": str(local_dir),
                    "has_draft_html": True,
                    "parse_mode": _PARSE_MODE_FITZ_DRAFT,
                    "message": "fitz 草稿已就绪，MinerU 后台解析中…",
                }
            return {"status": "missing", "cache_key": cache_key}
        task = dict(task)
        if task.get("status") == "done" and task.get("parse_mode") == _PARSE_MODE_MINERU:
            if task.get("mineru_cached") is not False:
                task.setdefault("mineru_cached", True)
        if task.get("status") == "running":
            task.setdefault("message", "MinerU 正在解析 PDF，请耐心等待（大型文档可能需要 20–60 分钟）")
            started = task.get("started_at")
            if isinstance(started, (int, float)):
                task["elapsed_sec"] = max(0, int(time.time() - started))
            phase = task.get("phase")
            if phase:
                task["message"] = f"MinerU 解析中（{phase}，已用时 {task.get('elapsed_sec', 0)} 秒）"
            local_dir = preview_local_dir(cache_key)
            html_path = local_dir / "document.html"
            if html_path.is_file():
                task["has_draft_html"] = True
        return task


def _touch_running(cache_key: str, *, phase: str) -> None:
    now = time.time()
    with _LOCK:
        task = dict(_TASKS.get(cache_key) or _load_persisted_task(cache_key) or {})
        task.update({
            "status": "running",
            "cache_key": cache_key,
            "phase": phase,
            "heartbeat_at": now,
        })
        task.setdefault("started_at", now)
        _TASKS[cache_key] = task
        _persist_task(cache_key, task)


def _heartbeat_loop(cache_key: str, stop: threading.Event) -> None:
    while not stop.wait(30.0):
        try:
            _touch_running(cache_key, phase="MinerU 推理中")
        except Exception:
            pass


def _run_preview_parse(pdf_path: str, cache_key: str) -> None:
    local_dir = preview_local_dir(cache_key)
    work_dir = (local_dir.parent / f"work_{cache_key}").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    logger.info("[MinerU-PREVIEW] start key=%s pdf=%s out=%s", cache_key, pdf_path, local_dir)
    stop_hb = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(cache_key, stop_hb),
        name=f"mineru-preview-hb-{cache_key}",
        daemon=True,
    )
    hb_thread.start()
    try:
        _touch_running(cache_key, phase="连接 MinerU API")
        from pdf_extract.mineru_adapter import (
            ensure_mineru_api_reachable,
            resolve_mineru_api_url,
            validate_pdf_for_mineru,
        )
        from pdf_extract.mineru_wjgl_service import _mineru_pdf_to_wjgl_bundle

        validate_pdf_for_mineru(pdf_path)
        api_url = ensure_mineru_api_reachable()
        logger.info("[MinerU-PREVIEW] using api=%s", api_url or resolve_mineru_api_url())
        _touch_running(cache_key, phase="PDF 解析中")
        result = _mineru_pdf_to_wjgl_bundle(
            pdf_path,
            str(work_dir),
            local_dir=local_dir,
            doc_id=cache_key,
            category="_preview",
            fast=True,
        )
        md_path = local_dir / "document.md"
        markdown = md_path.read_text(encoding="utf-8", errors="replace") if md_path.is_file() else ""
        logger.info(
            "[MinerU-PREVIEW] done key=%s md=%s html=%s dir=%s",
            cache_key,
            md_path.is_file(),
            len(result.get("full_html") or ""),
            local_dir,
        )
        with _LOCK:
            done_task = {
                "status": "done",
                "cache_key": cache_key,
                "local_dir": str(local_dir),
                "md_path": str(md_path),
                "markdown": markdown,
                "html": result.get("full_html") or "",
                "toc": result.get("toc") or [],
                "parse_mode": _PARSE_MODE_MINERU,
                "mineru_cached": False,
                "finished_at": time.time(),
                "stats": result.get("stats") or {},
            }
            _TASKS[cache_key] = done_task
            _persist_task(cache_key, done_task)
    except Exception as e:
        logger.exception("[MinerU-PREVIEW] failed key=%s: %s", cache_key, e)
        with _LOCK:
            err_task = {
                "status": "error",
                "cache_key": cache_key,
                "error": str(e),
            }
            _TASKS[cache_key] = err_task
            _persist_task(cache_key, err_task)
    finally:
        stop_hb.set()


def _attach_running_task(cache_key: str, existing: Dict[str, Any], *, force: bool) -> Optional[str]:
    """运行中任务：始终附着，不因 force 重启（避免前端重试时打断 MinerU）。"""
    if existing.get("status") != "running":
        return None
    hb = existing.get("heartbeat_at") or existing.get("updated_at") or existing.get("started_at")
    if not isinstance(hb, (int, float)):
        return None
    hb_age = time.time() - float(hb)
    if hb_age <= _STALE_RUNNING_SEC:
        attached = dict(existing)
        if hb_age > _HEARTBEAT_STALE_SEC:
            attached["stale_heartbeat"] = True
        _TASKS[cache_key] = attached
        logger.info(
            "[MinerU-PREVIEW] attach running task key=%s force=%s heartbeat_age=%.0fs",
            cache_key,
            force,
            hb_age,
        )
        return cache_key
    return None


def start_preview_parse(pdf_path: str, *, force: Optional[bool] = None) -> str:
    """
    启动后台 MinerU 解析。默认每次上传不复用已完成结果（force=True）。
    若同 key 已有运行中任务则附着，不重复排队。MINERU_PREVIEW_REUSE_CACHE=1 可复用 done。
    """
    if force is None:
        force = not _reuse_mineru_cache()
    cache_key = file_cache_key(pdf_path)
    with _LOCK:
        existing = _TASKS.get(cache_key) or _load_persisted_task(cache_key)
        if existing:
            attached = _attach_running_task(cache_key, existing, force=force)
            if attached:
                return attached
            if not force:
                if existing.get("status") == "done" and _mineru_complete_on_disk(cache_key):
                    cached = dict(existing)
                    cached["mineru_cached"] = True
                    cached.setdefault("parse_mode", _PARSE_MODE_MINERU)
                    _TASKS[cache_key] = cached
                    logger.info("[MinerU-PREVIEW] reuse cached MinerU result key=%s (no re-parse)", cache_key)
                    return cache_key
            elif existing.get("status") == "running":
                logger.warning("[MinerU-PREVIEW] stale running task key=%s, restarting", cache_key)
        running = {
            "status": "running",
            "cache_key": cache_key,
            "local_dir": str(preview_local_dir(cache_key)),
            "pdf_path": pdf_path,
            "started_at": time.time(),
            "heartbeat_at": time.time(),
            "phase": "排队中",
            "parse_mode": None,
            "mineru_cached": False,
        }
        _TASKS[cache_key] = running
        _persist_task(cache_key, running)
    logger.info("[MinerU-PREVIEW] queue background parse key=%s force=%s", cache_key, force)
    t = threading.Thread(
        target=_run_preview_parse,
        args=(pdf_path, cache_key),
        name=f"mineru-preview-{cache_key}",
        daemon=True,
    )
    t.start()
    return cache_key
