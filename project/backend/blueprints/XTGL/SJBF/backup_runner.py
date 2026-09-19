# -*- coding: utf-8 -*-
"""
全量备份：MySQL / Neo4j / MinIO / Chroma，目录 BACKUP_ROOT/YYYY-MM-DD/。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tarfile
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from config import Config

logger = logging.getLogger(__name__)

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 全量备份互斥：手动与自动任务串行，避免同一天目录并发写入
_backup_execution_lock = threading.Lock()


def backup_root() -> str:
    if os.environ.get("BACKUP_ROOT"):
        return os.path.abspath(os.environ["BACKUP_ROOT"])
    return os.path.abspath(getattr(Config, "BACKUP_ROOT", "/data/backup"))


def retention_days() -> int:
    if os.environ.get("BACKUP_RETENTION_DAYS"):
        return int(os.environ["BACKUP_RETENTION_DAYS"])
    return int(getattr(Config, "BACKUP_RETENTION_DAYS", 7))


def _mkdir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError:
        return 0


def _dir_size(path: str) -> int:
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def prune_old_backup_dirs(root: Optional[str] = None) -> None:
    root = root or backup_root()
    if not os.path.isdir(root):
        return
    keep = retention_days()
    cutoff = (datetime.now() - timedelta(days=keep)).date()
    for name in os.listdir(root):
        sub = os.path.join(root, name)
        if not os.path.isdir(sub) or not DATE_DIR_RE.match(name):
            continue
        try:
            d = datetime.strptime(name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            try:
                shutil.rmtree(sub, ignore_errors=True)
                logger.info("已删除过期备份目录: %s", sub)
            except Exception as e:
                logger.warning("删除备份目录失败 %s: %s", sub, e)


def scan_backup_days(root: Optional[str] = None) -> List[Dict[str, Any]]:
    root = root or backup_root()
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root), reverse=True):
        if not DATE_DIR_RE.match(name):
            continue
        day = os.path.join(root, name)
        if not os.path.isdir(day):
            continue
        mysql_p = os.path.join(day, "mysql_backup.sql")
        neo_p = os.path.join(day, "neo4j.dump")
        chroma_p = os.path.join(day, "chroma_data.tar.gz")
        minio_d = os.path.join(day, "minio")
        mysql_b = _file_size(mysql_p)
        neo_b = _file_size(neo_p)
        chroma_b = _file_size(chroma_p)
        minio_b = _dir_size(minio_d) if os.path.isdir(minio_d) else 0
        total = mysql_b + neo_b + chroma_b + minio_b
        out.append(
            {
                "date": name,
                "path": day,
                "mysql_bytes": mysql_b,
                "neo4j_bytes": neo_b,
                "minio_bytes": minio_b,
                "chroma_bytes": chroma_b,
                "total_bytes": total,
            }
        )
    return out


def _run_cmd(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )


_neo4j_major_cache: Optional[int] = None


def neo4j_admin_bin() -> str:
    return os.environ.get("NEO4J_ADMIN_BIN", "neo4j-admin")


def neo4j_admin_env() -> Dict[str, str]:
    env = os.environ.copy()
    home = (os.environ.get("NEO4J_HOME") or "").strip()
    if not home:
        bin_path = neo4j_admin_bin()
        if os.path.isfile(bin_path):
            parent = os.path.dirname(os.path.abspath(bin_path))
            if os.path.basename(parent).lower() == "bin":
                home = os.path.dirname(parent)
    if home:
        env["NEO4J_HOME"] = os.path.abspath(home)
    return env


def neo4j_admin_cmd_prefix() -> List[str]:
    """Windows 下 .bat/.cmd 需经 cmd /c 调用。"""
    bin_path = neo4j_admin_bin()
    if os.name == "nt" and bin_path.lower().endswith((".bat", ".cmd")):
        return ["cmd", "/c", os.path.abspath(bin_path)]
    return [bin_path]


def neo4j_path_arg(path: str) -> str:
    """Neo4j CLI 在 Windows 上对反斜杠路径较敏感，统一为绝对路径。"""
    p = os.path.abspath(path)
    if os.name == "nt":
        return p.replace("\\", "/")
    return p


def neo4j_cmd_error(cp: subprocess.CompletedProcess) -> str:
    return ((cp.stderr or "") + "\n" + (cp.stdout or "")).strip() or f"exit {cp.returncode}"


def neo4j_major_version() -> Optional[int]:
    global _neo4j_major_cache
    if _neo4j_major_cache is not None:
        return _neo4j_major_cache

    prefix = neo4j_admin_cmd_prefix()
    env = neo4j_admin_env()
    for extra in (["--version"], ["version"], []):
        try:
            cp = _run_cmd(prefix + extra, env=env, timeout=30)
            text = (cp.stdout or "") + (cp.stderr or "")
            m = re.search(r"(\d+)\.\d+", text)
            if m:
                _neo4j_major_cache = int(m.group(1))
                return _neo4j_major_cache
        except Exception:
            pass

    try:
        cp = _run_cmd(prefix + ["database", "dump", "--help"], env=env, timeout=30)
        if cp.returncode == 0:
            _neo4j_major_cache = 5
            return 5
    except Exception:
        pass

    return None


def run_neo4j_admin(args: List[str], timeout: int) -> subprocess.CompletedProcess:
    return _run_cmd(neo4j_admin_cmd_prefix() + args, env=neo4j_admin_env(), timeout=timeout)


def _neo4j_failure_hint(err: str, *, action: str) -> str:
    low = err.lower()
    if "stop" in low or "running" in low or "online" in low:
        return f"；请先停止 Neo4j 服务后再{action}（Windows 服务或 neo4j stop）"
    if "neo4j_home" in low or "java_home" in low:
        return "；请在 .env 中设置 NEO4J_HOME 指向 Neo4j 安装目录"
    return ""


def backup_neo4j_v5(dump_path: str, dump_dir: str, db: str, timeout: int) -> Dict[str, Any]:
    _mkdir(dump_dir)
    to_path = neo4j_path_arg(dump_dir)
    cp = run_neo4j_admin(["database", "dump", db, f"--to-path={to_path}"], timeout)
    if cp.returncode == 0:
        cand = os.path.join(dump_dir, f"{db}.dump")
        if not os.path.isfile(cand):
            cand = os.path.join(dump_dir, "neo4j.dump")
        if os.path.isfile(cand):
            if os.path.abspath(cand) != os.path.abspath(dump_path):
                shutil.move(cand, dump_path)
            return {"status": "success", "bytes": _file_size(dump_path), "path": dump_path}
    err = neo4j_cmd_error(cp)
    hint = _neo4j_failure_hint(err, action="备份")
    return {"status": "error", "error": f"neo4j-admin database dump 失败: {err[:1800]}{hint}"}


def backup_neo4j_v4(dump_path: str, db: str, timeout: int) -> Dict[str, Any]:
    to_path = neo4j_path_arg(dump_path)
    cp = run_neo4j_admin(["dump", f"--database={db}", f"--to={to_path}"], timeout)
    if cp.returncode == 0 and os.path.isfile(dump_path):
        return {"status": "success", "bytes": _file_size(dump_path), "path": dump_path}
    err = neo4j_cmd_error(cp)
    hint = _neo4j_failure_hint(err, action="备份")
    return {"status": "error", "error": f"neo4j-admin dump 失败: {err[:1800]}{hint}"}

def backup_mysql(sql_path: str) -> Dict[str, Any]:
    host = Config.DB_HOST
    port = str(Config.DB_PORT)
    user = Config.DB_USER
    db = Config.DB_NAME
    pwd = Config.DB_PASSWORD or ""
    try:
        env = os.environ.copy()
        if pwd:
            env["MYSQL_PWD"] = pwd
        cmd = [
            os.environ.get("MYSQLDUMP_BIN", "mysqldump"),
            "-h",
            host,
            "-P",
            port,
            "-u",
            user,
            "--single-transaction",
            "--routines",
            "--events",
            "--default-character-set=utf8mb4",
            "--result-file",
            sql_path,
            db,
        ]
        cp = _run_cmd(cmd, env=env, timeout=int(os.environ.get("MYSQLDUMP_TIMEOUT", "7200")))
        if cp.returncode != 0:
            err = (cp.stderr or cp.stdout or "").strip() or f"exit {cp.returncode}"
            return {"status": "error", "error": err[:2000]}
        if not os.path.isfile(sql_path) or os.path.getsize(sql_path) == 0:
            return {"status": "error", "error": "mysqldump 未生成有效文件"}
        return {"status": "success", "bytes": os.path.getsize(sql_path), "path": sql_path}
    except FileNotFoundError:
        return {"status": "skipped", "reason": "未找到 mysqldump 命令"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "mysqldump 超时"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def backup_neo4j_admin(dump_path: str, dump_dir: str) -> Dict[str, Any]:
    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    timeout = int(os.environ.get("NEO4J_DUMP_TIMEOUT", "7200"))
    try:
        major = neo4j_major_version()
        if major is not None and major >= 5:
            return backup_neo4j_v5(dump_path, dump_dir, db, timeout)
        if major is not None and major < 5:
            return backup_neo4j_v4(dump_path, db, timeout)

        # 版本未知：先尝试 Neo4j 5 语法
        res5 = backup_neo4j_v5(dump_path, dump_dir, db, timeout)
        if res5.get("status") == "success":
            return res5
        err5 = res5.get("error") or ""
        if "database dump" in err5.lower() or "did you mean" in err5.lower():
            return backup_neo4j_v4(dump_path, db, timeout)
        return res5
    except FileNotFoundError:
        return {"status": "skipped", "reason": "未找到 neo4j-admin"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "neo4j-admin dump 超时"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def backup_minio_python(dest_dir: str) -> Dict[str, Any]:
    client = Config.MINIO_CLIENT
    bucket = Config.MINIO_BUCKET_NAME
    if not client:
        return {"status": "skipped", "reason": "MinIO 客户端未初始化"}
    try:
        _mkdir(dest_dir)
        count = 0
        for obj in client.list_objects(bucket, recursive=True):
            name = obj.object_name
            if name.endswith("/"):
                continue
            local_path = os.path.join(dest_dir, name.replace("/", os.sep))
            _mkdir(os.path.dirname(local_path))
            client.fget_object(bucket, name, local_path)
            count += 1
        b = _dir_size(dest_dir)
        return {"status": "success", "bytes": b, "path": dest_dir, "object_count": count}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def backup_minio_mc(dest_dir: str) -> Optional[Dict[str, Any]]:
    spec = os.environ.get("MINIO_MC_MIRROR", "").strip()
    if not spec:
        return None
    try:
        _mkdir(dest_dir)
        parts = spec.split()
        cmd = ["mc", "mirror", "--overwrite"] + parts + [dest_dir]
        cp = _run_cmd(cmd, timeout=int(os.environ.get("MINIO_MC_TIMEOUT", "7200")))
        if cp.returncode != 0:
            return {"status": "error", "error": (cp.stderr or cp.stdout or "")[:2000]}
        return {
            "status": "success",
            "bytes": _dir_size(dest_dir),
            "path": dest_dir,
            "via": "mc",
        }
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "mc mirror 超时"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def backup_chroma_tar(tar_path: str) -> Dict[str, Any]:
    src = os.path.abspath(Config.CHROMA_PATH or "")
    if not src or not os.path.isdir(src):
        return {"status": "skipped", "reason": "Chroma 数据目录不存在"}
    base = os.path.basename(src.rstrip(os.sep))
    parent = os.path.dirname(src)
    try:
        with tarfile.open(tar_path, "w:gz", compresslevel=6) as tar:
            tar.add(src, arcname=base)
        return {"status": "success", "bytes": _file_size(tar_path), "path": tar_path}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def run_full_backup(
    task: Dict[str, Any],
    task_lock: threading.Lock,
    on_part: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> None:
    """在后台线程中执行；写入 task 字典。"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    root = backup_root()
    day_dir = os.path.join(root, date_str)

    def touch(part: str, info: Dict[str, Any]) -> None:
        with task_lock:
            task["parts"][part] = info
        if on_part:
            try:
                on_part(part, info)
            except Exception:
                pass

    wait_lock = bool(task.get("_wait_backup_lock"))
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
                task["error"] = "已有备份任务正在执行，请稍后重试"
                task["finished"] = datetime.now().isoformat(timespec="seconds")
            return
    try:
        with task_lock:
            task["status"] = "running"
            task["date"] = date_str
            task["started"] = datetime.now().isoformat(timespec="seconds")

        _mkdir(root)
        prune_old_backup_dirs(root)
        _mkdir(day_dir)

        # MySQL
        sql_path = os.path.join(day_dir, "mysql_backup.sql")
        touch("mysql", {"status": "running"})
        touch("mysql", backup_mysql(sql_path))

        # Neo4j
        dump_path = os.path.join(day_dir, "neo4j.dump")
        neo_stage = os.path.join(day_dir, "_neo4j_dump_stage")
        touch("neo4j", {"status": "running"})
        touch("neo4j", backup_neo4j_admin(dump_path, neo_stage))
        if os.path.isdir(neo_stage):
            shutil.rmtree(neo_stage, ignore_errors=True)

        # MinIO：优先 mc（MINIO_MC_MIRROR），否则 Python SDK
        minio_dir = os.path.join(day_dir, "minio")
        touch("minio", {"status": "running"})
        mc_res = backup_minio_mc(minio_dir)
        if mc_res is not None:
            touch("minio", mc_res)
        else:
            touch("minio", backup_minio_python(minio_dir))

        # Chroma
        chroma_tar = os.path.join(day_dir, "chroma_data.tar.gz")
        touch("chroma", {"status": "running"})
        touch("chroma", backup_chroma_tar(chroma_tar))

        prune_old_backup_dirs(root)

        with task_lock:
            task["status"] = "completed"
            task["finished"] = datetime.now().isoformat(timespec="seconds")
    except Exception as e:
        logger.exception("全量备份失败")
        with task_lock:
            task["status"] = "failed"
            task["error"] = str(e)
            task["finished"] = datetime.now().isoformat(timespec="seconds")
    finally:
        if acquired:
            _backup_execution_lock.release()
