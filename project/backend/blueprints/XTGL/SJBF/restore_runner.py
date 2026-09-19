# -*- coding: utf-8 -*-
"""
全量恢复：从 BACKUP_ROOT/YYYY-MM-DD/ 将备份原样导回 MySQL / Neo4j / MinIO / Chroma，覆盖现有数据。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tarfile
import threading
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from config import Config

from .backup_runner import (
    DATE_DIR_RE,
    _backup_execution_lock,
    _mkdir,
    _run_cmd,
    backup_root,
    neo4j_cmd_error,
    neo4j_major_version,
    neo4j_path_arg,
    run_neo4j_admin,
    _neo4j_failure_hint,
)

logger = logging.getLogger(__name__)


def backup_day_dir(date_str: str) -> str:
    if not DATE_DIR_RE.match(date_str):
        raise ValueError(f"无效的备份日期: {date_str}")
    return os.path.join(backup_root(), date_str)


def _mysql_cmd_prefix(mysql_bin: str) -> list[str]:
    if os.name == "nt" and mysql_bin.lower().endswith((".bat", ".cmd")):
        return ["cmd", "/c", os.path.abspath(mysql_bin)]
    return [mysql_bin]


def _prepare_mysql_sql_bytes(sql_path: str) -> bytes:
    """规范化 SQL 字节流，避免 Windows CRLF 导致 mysql 将 \\ 误判为客户端命令。"""
    with open(sql_path, "rb") as f:
        data = f.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def restore_mysql(sql_path: str) -> Dict[str, Any]:
    if not os.path.isfile(sql_path) or os.path.getsize(sql_path) == 0:
        return {"status": "skipped", "reason": "MySQL 备份文件不存在或为空"}

    host = Config.DB_HOST
    port = str(Config.DB_PORT)
    user = Config.DB_USER
    db = Config.DB_NAME
    pwd = Config.DB_PASSWORD or ""
    mysql_bin = os.environ.get("MYSQL_BIN", "mysql")
    timeout = int(os.environ.get("MYSQL_RESTORE_TIMEOUT", "7200"))

    try:
        env = os.environ.copy()
        if pwd:
            env["MYSQL_PWD"] = pwd

        init_sql = (
            f"DROP DATABASE IF EXISTS `{db}`; "
            f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        )
        cp_init = _run_cmd(
            _mysql_cmd_prefix(mysql_bin) + ["-h", host, "-P", port, "-u", user, "-e", init_sql],
            env=env,
            timeout=timeout,
        )
        if cp_init.returncode != 0:
            err = (cp_init.stderr or cp_init.stdout or "").strip() or f"exit {cp_init.returncode}"
            return {"status": "error", "error": f"重建数据库失败: {err[:2000]}"}

        sql_data = _prepare_mysql_sql_bytes(sql_path)
        cp = subprocess.run(
            _mysql_cmd_prefix(mysql_bin)
            + [
                "-h",
                host,
                "-P",
                port,
                "-u",
                user,
                "--default-character-set=utf8mb4",
                "--binary-mode=1",
                db,
            ],
            input=sql_data,
            env=env,
            capture_output=True,
            timeout=timeout,
        )
        if cp.returncode != 0:
            err = (cp.stderr or cp.stdout or b"").decode("utf-8", errors="replace").strip()
            err = err or f"exit {cp.returncode}"
            return {"status": "error", "error": err[:2000]}
        return {"status": "success", "bytes": os.path.getsize(sql_path), "path": sql_path}
    except FileNotFoundError:
        return {"status": "skipped", "reason": "未找到 mysql 命令"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "mysql 导入超时"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def restore_neo4j_v5(dump_path: str, stage_dir: str, db: str, timeout: int) -> Dict[str, Any]:
    _mkdir(stage_dir)
    staged_dump = os.path.join(stage_dir, f"{db}.dump")
    if os.path.abspath(staged_dump) != os.path.abspath(dump_path):
        shutil.copy2(dump_path, staged_dump)

    from_path = neo4j_path_arg(stage_dir)
    cp = run_neo4j_admin(
        ["database", "load", db, f"--from-path={from_path}", "--overwrite-destination=true"],
        timeout,
    )
    if cp.returncode == 0:
        return {"status": "success", "bytes": os.path.getsize(dump_path), "path": dump_path}
    err = neo4j_cmd_error(cp)
    hint = _neo4j_failure_hint(err, action="恢复")
    return {"status": "error", "error": f"neo4j-admin database load 失败: {err[:1800]}{hint}"}


def restore_neo4j_v4(dump_path: str, db: str, timeout: int) -> Dict[str, Any]:
    from_path = neo4j_path_arg(dump_path)
    cp = run_neo4j_admin(["load", f"--from={from_path}", f"--database={db}", "--force"], timeout)
    if cp.returncode == 0:
        return {"status": "success", "bytes": os.path.getsize(dump_path), "path": dump_path}
    err = neo4j_cmd_error(cp)
    hint = _neo4j_failure_hint(err, action="恢复")
    return {"status": "error", "error": f"neo4j-admin load 失败: {err[:1800]}{hint}"}


def restore_neo4j_admin(dump_path: str, stage_dir: str) -> Dict[str, Any]:
    if not os.path.isfile(dump_path) or os.path.getsize(dump_path) == 0:
        return {"status": "skipped", "reason": "Neo4j 备份文件不存在或为空"}

    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    timeout = int(os.environ.get("NEO4J_LOAD_TIMEOUT", "7200"))
    try:
        major = neo4j_major_version()
        if major is not None and major >= 5:
            return restore_neo4j_v5(dump_path, stage_dir, db, timeout)
        if major is not None and major < 5:
            return restore_neo4j_v4(dump_path, db, timeout)

        res5 = restore_neo4j_v5(dump_path, stage_dir, db, timeout)
        if res5.get("status") == "success":
            return res5
        err5 = res5.get("error") or ""
        if "database load" in err5.lower() or "did you mean" in err5.lower():
            return restore_neo4j_v4(dump_path, db, timeout)
        return res5
    except FileNotFoundError:
        return {"status": "skipped", "reason": "未找到 neo4j-admin"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "neo4j-admin load 超时"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def restore_minio_python(src_dir: str) -> Dict[str, Any]:
    client = Config.MINIO_CLIENT
    bucket = Config.MINIO_BUCKET_NAME
    if not client:
        return {"status": "skipped", "reason": "MinIO 客户端未初始化"}
    if not os.path.isdir(src_dir):
        return {"status": "skipped", "reason": "MinIO 备份目录不存在"}

    try:
        for obj in client.list_objects(bucket, recursive=True):
            client.remove_object(bucket, obj.object_name)

        count = 0
        for root, _, files in os.walk(src_dir):
            for fname in files:
                local_path = os.path.join(root, fname)
                rel = os.path.relpath(local_path, src_dir).replace(os.sep, "/")
                client.fput_object(bucket, rel, local_path)
                count += 1
        return {
            "status": "success",
            "path": src_dir,
            "object_count": count,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def restore_minio_mc(src_dir: str) -> Optional[Dict[str, Any]]:
    spec = os.environ.get("MINIO_MC_RESTORE", "").strip()
    if not spec:
        mirror = os.environ.get("MINIO_MC_MIRROR", "").strip()
        if mirror:
            spec = mirror
    if not spec:
        return None

    try:
        parts = spec.split()
        cmd = ["mc", "mirror", "--overwrite", src_dir] + parts
        cp = _run_cmd(cmd, timeout=int(os.environ.get("MINIO_MC_TIMEOUT", "7200")))
        if cp.returncode != 0:
            return {"status": "error", "error": (cp.stderr or cp.stdout or "")[:2000]}
        return {"status": "success", "path": src_dir, "via": "mc"}
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "mc mirror 超时"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def restore_chroma_tar(tar_path: str) -> Dict[str, Any]:
    if not os.path.isfile(tar_path) or os.path.getsize(tar_path) == 0:
        return {"status": "skipped", "reason": "Chroma 备份文件不存在或为空"}

    dest = os.path.abspath(Config.CHROMA_PATH or "")
    if not dest:
        return {"status": "skipped", "reason": "未配置 CHROMA_PATH"}

    parent = os.path.dirname(dest)
    _mkdir(parent)

    try:
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(parent)
        if not os.path.isdir(dest):
            return {"status": "error", "error": "解压后未找到 Chroma 数据目录"}
        return {"status": "success", "path": dest}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def run_full_restore(
    task: Dict[str, Any],
    task_lock: threading.Lock,
    date_str: str,
    on_part: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> None:
    """在后台线程中执行；从指定日期备份目录恢复并覆盖现有数据。"""
    try:
        day_dir = backup_day_dir(date_str)
    except ValueError as e:
        with task_lock:
            task["status"] = "failed"
            task["error"] = str(e)
            task["finished"] = datetime.now().isoformat(timespec="seconds")
        return

    if not os.path.isdir(day_dir):
        with task_lock:
            task["status"] = "failed"
            task["error"] = f"备份目录不存在: {day_dir}"
            task["finished"] = datetime.now().isoformat(timespec="seconds")
        return

    def touch(part: str, info: Dict[str, Any]) -> None:
        with task_lock:
            task["parts"][part] = info
        if on_part:
            try:
                on_part(part, info)
            except Exception:
                pass

    wait_lock = bool(task.get("_wait_restore_lock"))
    acquired = False
    if wait_lock:
        _backup_execution_lock.acquire()
        acquired = True
    else:
        acquired = _backup_execution_lock.acquire(blocking=False)
        if not acquired:
            with task_lock:
                task["status"] = "skipped"
                task["date"] = date_str
                task["error"] = "已有备份或恢复任务正在执行，请稍后重试"
                task["finished"] = datetime.now().isoformat(timespec="seconds")
            return

    try:
        with task_lock:
            task["status"] = "running"
            task["date"] = date_str
            task["started"] = datetime.now().isoformat(timespec="seconds")

        sql_path = os.path.join(day_dir, "mysql_backup.sql")
        touch("mysql", {"status": "running"})
        touch("mysql", restore_mysql(sql_path))

        dump_path = os.path.join(day_dir, "neo4j.dump")
        neo_stage = os.path.join(day_dir, "_neo4j_load_stage")
        touch("neo4j", {"status": "running"})
        touch("neo4j", restore_neo4j_admin(dump_path, neo_stage))
        if os.path.isdir(neo_stage):
            shutil.rmtree(neo_stage, ignore_errors=True)

        minio_dir = os.path.join(day_dir, "minio")
        touch("minio", {"status": "running"})
        mc_res = restore_minio_mc(minio_dir)
        if mc_res is not None:
            touch("minio", mc_res)
        else:
            touch("minio", restore_minio_python(minio_dir))

        chroma_tar = os.path.join(day_dir, "chroma_data.tar.gz")
        touch("chroma", {"status": "running"})
        touch("chroma", restore_chroma_tar(chroma_tar))

        with task_lock:
            task["status"] = "completed"
            task["finished"] = datetime.now().isoformat(timespec="seconds")
    except Exception as e:
        logger.exception("全量恢复失败")
        with task_lock:
            task["status"] = "failed"
            task["error"] = str(e)
            task["finished"] = datetime.now().isoformat(timespec="seconds")
    finally:
        if acquired:
            _backup_execution_lock.release()
