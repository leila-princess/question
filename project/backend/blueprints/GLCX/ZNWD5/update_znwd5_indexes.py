"""
ZNWD5 索引一键更新：数据库变化后自动检测并同步到 Zvec。

用法（backend 目录，须先停 Flask 避免 LOCK）：
  python blueprints/GLCX/ZNWD5/update_znwd5_indexes.py
  python blueprints/GLCX/ZNWD5/update_znwd5_indexes.py --dry-run
  python blueprints/GLCX/ZNWD5/update_znwd5_indexes.py --mode full
  python blueprints/GLCX/ZNWD5/update_znwd5_indexes.py --watch 300

模式：
  auto（默认）— 对比上次快照，有删除则重建，有新增/修改则增量 upsert
  incremental — 不删索引，全量 upsert（更新已有、追加新增，不清理孤儿）
  full        — 删除旧索引目录后完整重建

状态文件：backend/zvec_data/index_update_state.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("update_znwd5_indexes")

UpdateAction = Literal["skip", "incremental", "rebuild"]
STATE_FILE = "index_update_state.json"


def _load_local_module(module_name: str):
    import importlib.util

    path = os.path.join(_HERE, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(f"znwd5_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _zvec_base_path() -> str:
    return os.environ.get("ZVEC_DATA_PATH") or os.path.join(_BACKEND, "zvec_data")


def _ensure_bge_env() -> None:
    try:
        from config import Config

        bge_path = os.path.abspath(getattr(Config, "FLGL_TESTMETHOD_BGE_MODEL_PATH", "") or "")
        if bge_path and os.path.isdir(bge_path):
            os.environ.setdefault("FLGL_TESTMETHOD_BGE_MODEL_PATH", bge_path)
    except Exception as exc:
        logger.warning("BGE path setup skipped: %s", exc)


def _hash_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()


def _hash_ids(ids: Set[str]) -> str:
    return _hash_text("\n".join(sorted(ids)))


@dataclass
class CollectionSnapshot:
    count: int
    ids: Set[str]
    content_fingerprints: Dict[str, str]

    @property
    def ids_hash(self) -> str:
        return _hash_ids(self.ids)

    @property
    def content_hash(self) -> str:
        parts = [f"{k}:{v}" for k, v in sorted(self.content_fingerprints.items())]
        return _hash_text("\n".join(parts))


def snapshot_neo4j() -> CollectionSnapshot:
    neo = _load_local_module("sync_neo4j_to_zvec")
    emb = _load_local_module("embedding_service")
    driver = neo._connect_neo4j()
    nodes = neo.fetch_nodes(driver)
    driver.close()

    ids: Set[str] = set()
    fps: Dict[str, str] = {}
    for n in nodes:
        nt = str(n.get("node_type") or "")
        nid = str(n.get("neo4j_id") or "")
        doc_id = f"{nt}:{nid}"
        ids.add(doc_id)
        text = emb.node_to_text(
            {
                "name": n.get("name"),
                "label": n.get("label"),
                "type": n.get("type"),
                "description": n.get("description"),
            }
        )
        payload = "|".join(
            [
                nt,
                nid,
                str(n.get("name") or ""),
                str(n.get("status") or ""),
                text or "",
            ]
        )
        fps[doc_id] = _hash_text(payload)
    return CollectionSnapshot(count=len(nodes), ids=ids, content_fingerprints=fps)


def snapshot_chroma() -> CollectionSnapshot:
    chroma = _load_local_module("sync_chroma_to_zvec")
    rows = chroma.fetch_chroma_chunks(limit=0)
    ids: Set[str] = set()
    fps: Dict[str, str] = {}
    for r in rows:
        cid = str(r.get("chunk_id") or "")
        ids.add(cid)
        payload = "|".join(
            [
                cid,
                str(r.get("title") or ""),
                str(r.get("section") or ""),
                str(r.get("std_code") or ""),
                str(r.get("content") or "")[:4000],
            ]
        )
        fps[cid] = _hash_text(payload)
    return CollectionSnapshot(count=len(rows), ids=ids, content_fingerprints=fps)


def load_state(base: str) -> Dict[str, Any]:
    path = os.path.join(base, STATE_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(base: str, graph: CollectionSnapshot, docs: CollectionSnapshot) -> None:
    payload = {
        "updated_at": datetime.now().isoformat(),
        "graph": {
            "count": graph.count,
            "ids_hash": graph.ids_hash,
            "content_hash": graph.content_hash,
        },
        "docs": {
            "count": docs.count,
            "ids_hash": docs.ids_hash,
            "content_hash": docs.content_hash,
        },
    }
    path = os.path.join(base, STATE_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _index_exists(base: str, sub: str) -> bool:
    return os.path.isdir(os.path.join(base, sub))


def plan_collection_update(
    *,
    name: str,
    snap: CollectionSnapshot,
    prev: Dict[str, Any],
    index_exists: bool,
    mode: str,
    force: bool,
) -> Tuple[UpdateAction, Set[str], str]:
    """返回 (动作, 需同步的 id 集合空=全量, 原因说明)。"""
    if mode == "full" or force:
        return "rebuild", set(), f"{name}: 强制完整重建"

    if mode == "incremental":
        if not index_exists:
            return "rebuild", set(), f"{name}: 索引不存在，改为完整重建"
        return "incremental", set(), f"{name}: 增量 upsert 全量数据"

    # auto
    if not index_exists:
        return "rebuild", set(), f"{name}: 索引不存在"

    if not prev:
        return "incremental", set(), f"{name}: 无历史快照，增量 upsert 并建立快照"

    prev_ids_hash = str(prev.get("ids_hash") or "")
    prev_content_hash = str(prev.get("content_hash") or "")

    if snap.ids_hash == prev_ids_hash and snap.content_hash == prev_content_hash:
        return "skip", set(), f"{name}: 无变化 (count={snap.count})"

    prev_count = int(prev.get("count") or 0)
    if snap.count < prev_count:
        return "rebuild", set(), f"{name}: 数据减少 {prev_count}→{snap.count}，需重建清理孤儿"

    if snap.ids_hash != prev_ids_hash:
        if snap.count > prev_count:
            return "incremental", set(), f"{name}: 新增 {snap.count - prev_count} 条，增量 upsert"
        return "rebuild", set(), f"{name}: ID 变化且非纯新增，需重建清理孤儿"

    if snap.content_hash != prev_content_hash:
        return "incremental", set(), f"{name}: 内容变更，增量 upsert"

    return "skip", set(), f"{name}: 无变化"


def _run_graph_sync(*, force: bool, batch_size: int) -> int:
    neo = _load_local_module("sync_neo4j_to_zvec")

    class _Args:
        pass

    args = _Args()
    args.force = force
    args.batch_size = batch_size
    return neo._run_sync(args)


def _run_docs_sync(*, force: bool, batch_size: int) -> int:
    doc = _load_local_module("sync_chroma_to_zvec")

    class _Args:
        pass

    args = _Args()
    args.force = force
    args.limit = 0
    args.re_embed = True
    args.batch_size = batch_size
    return doc._run_sync(args)


def update_indexes(
    *,
    mode: str = "auto",
    force: bool = False,
    skip_neo4j: bool = False,
    skip_chroma: bool = False,
    batch_neo4j: int = 64,
    batch_chroma: int = 32,
    dry_run: bool = False,
    init_state: bool = False,
) -> Dict[str, Any]:
    _ensure_bge_env()
    mgr_mod = _load_local_module("zvec_index_manager")
    base = os.path.abspath(_zvec_base_path())
    state = load_state(base)

    report: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(),
        "mode": mode,
        "dry_run": dry_run,
        "plan": {},
        "steps": {},
    }

    graph_snap = docs_snap = None
    if not skip_neo4j:
        logger.info("扫描 Neo4j …")
        graph_snap = snapshot_neo4j()
        logger.info("Neo4j 节点 %d", graph_snap.count)
    if not skip_chroma:
        logger.info("扫描 Chroma …")
        docs_snap = snapshot_chroma()
        logger.info("Chroma 片段 %d", docs_snap.count)

    graph_action: UpdateAction = "skip"
    docs_action: UpdateAction = "skip"

    if not skip_neo4j and graph_snap is not None:
        graph_action, _, reason = plan_collection_update(
            name="graph_entities",
            snap=graph_snap,
            prev=state.get("graph") or {},
            index_exists=_index_exists(base, "graph_entities"),
            mode=mode,
            force=force,
        )
        report["plan"]["graph"] = {"action": graph_action, "reason": reason, "count": graph_snap.count}

    if not skip_chroma and docs_snap is not None:
        docs_action, _, reason = plan_collection_update(
            name="documents",
            snap=docs_snap,
            prev=state.get("docs") or {},
            index_exists=_index_exists(base, "documents"),
            mode=mode,
            force=force,
        )
        report["plan"]["docs"] = {"action": docs_action, "reason": reason, "count": docs_snap.count}

    if dry_run:
        report["success"] = True
        report["finished_at"] = datetime.now().isoformat()
        logger.info("dry-run 计划: %s", json.dumps(report["plan"], ensure_ascii=False))
        return report

    if init_state:
        new_state = dict(state)
        new_state["updated_at"] = datetime.now().isoformat()
        if graph_snap is not None:
            new_state["graph"] = {
                "count": graph_snap.count,
                "ids_hash": graph_snap.ids_hash,
                "content_hash": graph_snap.content_hash,
            }
        if docs_snap is not None:
            new_state["docs"] = {
                "count": docs_snap.count,
                "ids_hash": docs_snap.ids_hash,
                "content_hash": docs_snap.content_hash,
            }
        state_path = os.path.join(base, STATE_FILE)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(new_state, f, ensure_ascii=False, indent=2)
        logger.info("已写入快照 %s（未修改 zvec 索引）", state_path)
        report["success"] = True
        report["finished_at"] = datetime.now().isoformat()
        return report

    if graph_action == "skip" and docs_action == "skip":
        logger.info("索引已是最新，无需更新")
        report["success"] = True
        report["finished_at"] = datetime.now().isoformat()
        return report

    mgr_mod.reset_zvec_manager()
    # 重建前清 LOCK（仅 full/force）；完成后勿删
    if force or mode == "full":
        mgr_mod.remove_zvec_lock_files(base)

    if not skip_neo4j and graph_action != "skip":
        logger.info("========== 更新 graph_entities (%s) ==========", graph_action)
        try:
            rc = _run_graph_sync(force=(graph_action == "rebuild"), batch_size=batch_neo4j)
        except RuntimeError as exc:
            if "lock" in str(exc).lower():
                logger.error("无法打开 zvec 写锁，请先停止 Flask/backend 后再运行本脚本")
            raise
        report["steps"]["neo4j"] = {"action": graph_action, "exit_code": rc}
        if rc != 0:
            report["success"] = False
            report["finished_at"] = datetime.now().isoformat()
            mgr_mod.reset_zvec_manager()
            return report

    if not skip_chroma and docs_action != "skip":
        logger.info("========== 更新 documents (%s) ==========", docs_action)
        rc = _run_docs_sync(force=(docs_action == "rebuild"), batch_size=batch_chroma)
        report["steps"]["chroma"] = {"action": docs_action, "exit_code": rc}
        if rc != 0:
            report["success"] = False
            report["finished_at"] = datetime.now().isoformat()
            mgr_mod.reset_zvec_manager()
            return report

    mgr_mod.reset_zvec_manager()
    # 保留 LOCK 供 Flask 只读打开（Windows/zvec0.5）

    if graph_snap is None and not skip_neo4j:
        graph_snap = snapshot_neo4j()
    if docs_snap is None and not skip_chroma:
        docs_snap = snapshot_chroma()

    new_state = dict(state)
    new_state["updated_at"] = datetime.now().isoformat()
    if graph_snap is not None:
        new_state["graph"] = {
            "count": graph_snap.count,
            "ids_hash": graph_snap.ids_hash,
            "content_hash": graph_snap.content_hash,
        }
    if docs_snap is not None:
        new_state["docs"] = {
            "count": docs_snap.count,
            "ids_hash": docs_snap.ids_hash,
            "content_hash": docs_snap.content_hash,
        }
    with open(os.path.join(base, STATE_FILE), "w", encoding="utf-8") as f:
        json.dump(new_state, f, ensure_ascii=False, indent=2)

    report["zvec"] = {
        "base_path": base,
        "graph_count": graph_snap.count if graph_snap else int((state.get("graph") or {}).get("count") or 0),
        "doc_count": docs_snap.count if docs_snap else int((state.get("docs") or {}).get("count") or 0),
    }
    report["success"] = True
    report["finished_at"] = datetime.now().isoformat()

    manifest_path = os.path.join(base, "index_update_report.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(
        "索引更新完成 graph=%s docs=%s report=%s",
        report["zvec"]["graph_count"],
        report["zvec"]["doc_count"],
        manifest_path,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Update ZNWD5 Zvec indexes when Neo4j/Chroma changes")
    parser.add_argument(
        "--mode",
        choices=("auto", "incremental", "full"),
        default="auto",
        help="auto=检测变化后增量/重建；incremental=不删索引全量 upsert；full=完整重建",
    )
    parser.add_argument("--force", action="store_true", help="等同 --mode full")
    parser.add_argument("--dry-run", action="store_true", help="仅输出更新计划，不写入")
    parser.add_argument(
        "--init-state",
        action="store_true",
        help="仅扫描 Neo4j/Chroma 并写入快照（不打开 zvec，Flask 运行时可用）",
    )
    parser.add_argument("--skip-neo4j", action="store_true")
    parser.add_argument("--skip-chroma", action="store_true")
    parser.add_argument("--batch-neo4j", type=int, default=64)
    parser.add_argument("--batch-chroma", type=int, default=32)
    parser.add_argument(
        "--watch",
        type=int,
        default=0,
        metavar="SECONDS",
        help="每隔 N 秒自动检测并更新（0=仅运行一次）",
    )
    args = parser.parse_args()
    mode = "full" if args.force else args.mode

    interval = max(0, int(args.watch or 0))
    while True:
        report = update_indexes(
            mode=mode,
            force=args.force,
            skip_neo4j=args.skip_neo4j,
            skip_chroma=args.skip_chroma,
            batch_neo4j=args.batch_neo4j,
            batch_chroma=args.batch_chroma,
            dry_run=args.dry_run,
            init_state=args.init_state,
        )
        if interval <= 0 or args.dry_run:
            return 0 if report.get("success") else 1
        logger.info("watch 模式：%d 秒后再次检测 …", interval)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
