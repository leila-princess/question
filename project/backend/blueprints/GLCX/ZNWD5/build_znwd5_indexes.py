"""
ZNWD5 一键重建索引：Neo4j 图实体 + Chroma 文档 → Zvec。

用法（backend 目录，须先停 Flask 避免 LOCK）：
  python blueprints/GLCX/ZNWD5/build_znwd5_indexes.py
  python blueprints/GLCX/ZNWD5/build_znwd5_indexes.py --force --skip-chroma
  python blueprints/GLCX/ZNWD5/build_znwd5_indexes.py --force --skip-neo4j

步骤：
  1. Neo4j 全量节点 → zvec_data/graph_entities
  2. Chroma 全量片段 → zvec_data/documents（BGE 重嵌入）
  3. 写入 build_manifest.json，关闭 zvec 句柄
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_znwd5_indexes")


def _load_local_module(module_name: str):
    import importlib.util

    path = os.path.join(_HERE, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(f"znwd5_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _zvec_mod():
    return _load_local_module("zvec_index_manager")


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


def _preflight_numpy_chroma() -> None:
    try:
        import numpy as np

        major = int(str(np.__version__).split(".")[0])
        if major >= 2:
            logger.warning(
                "NumPy %s 与 chromadb 0.4.x 不兼容；Chroma 同步将自动改用 sqlite 直读。"
                "建议执行: pip install \"numpy>=1.23,<2.0\"",
                np.__version__,
            )
    except Exception:
        pass


def _read_meta_count(base: str, name: str, key: str) -> int:
    path = os.path.join(base, name)
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get(key) or 0)
    except Exception:
        return 0


def build_indexes(
    *,
    force: bool = True,
    skip_neo4j: bool = False,
    skip_chroma: bool = False,
    batch_neo4j: int = 64,
    batch_chroma: int = 32,
    chroma_limit: int = 0,
) -> Dict[str, Any]:
    _ensure_bge_env()
    _preflight_numpy_chroma()
    mgr_mod = _zvec_mod()
    base = os.path.abspath(_zvec_base_path())

    mgr_mod.reset_zvec_manager()
    mgr_mod.remove_zvec_lock_files(base)

    report: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(),
        "force": force,
        "steps": {},
    }
    rc = 0

    if not skip_neo4j:
        logger.info("========== [1/2] Neo4j → graph_entities ==========")
        neo_sync = _load_local_module("sync_neo4j_to_zvec")

        class _Args:
            pass

        args = _Args()
        args.force = force
        args.batch_size = batch_neo4j
        rc = neo_sync._run_sync(args)
        report["steps"]["neo4j"] = {"exit_code": rc}
        if rc != 0:
            report["success"] = False
            report["finished_at"] = datetime.now().isoformat()
            mgr_mod.reset_zvec_manager()
            return report
    else:
        report["steps"]["neo4j"] = {"skipped": True}

    if not skip_chroma:
        logger.info("========== [2/2] Chroma → documents ==========")
        doc_sync = _load_local_module("sync_chroma_to_zvec")

        class _Args:
            pass

        args = _Args()
        args.force = force
        args.limit = chroma_limit
        args.re_embed = True
        args.batch_size = batch_chroma
        rc = doc_sync._run_sync(args)
        report["steps"]["chroma"] = {"exit_code": rc}
        if rc != 0:
            report["success"] = False
            report["finished_at"] = datetime.now().isoformat()
            mgr_mod.reset_zvec_manager()
            return report
    else:
        report["steps"]["chroma"] = {"skipped": True}

    mgr_mod.reset_zvec_manager()
    # 勿删 LOCK；封存供 Flask 只读打开（Windows/zvec0.5）
    try:
        seal = _load_local_module("seal_zvec_locks")
        seal.main()
    except Exception as exc:
        logger.warning("seal_zvec_locks skipped: %s", exc)

    graph_count = _read_meta_count(base, "graph_entities_sync_meta.json", "node_count")
    doc_count = _read_meta_count(base, "documents_sync_meta.json", "chunk_count")

    report["zvec"] = {
        "base_path": base,
        "backend": "zvec",
        "graph_count": graph_count,
        "doc_count": doc_count,
    }
    report["success"] = True
    report["finished_at"] = datetime.now().isoformat()

    manifest_path = os.path.join(base, "build_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(
        "索引构建完成 graph=%d docs=%d manifest=%s",
        graph_count,
        doc_count,
        manifest_path,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild ZNWD5 Zvec indexes (Neo4j + Chroma)")
    parser.add_argument("--force", action="store_true", help="删除旧索引后重建")
    parser.add_argument("--no-force", action="store_true", help="增量写入，不删旧目录")
    parser.add_argument("--skip-neo4j", action="store_true")
    parser.add_argument("--skip-chroma", action="store_true")
    parser.add_argument("--batch-neo4j", type=int, default=64)
    parser.add_argument("--batch-chroma", type=int, default=32)
    parser.add_argument("--chroma-limit", type=int, default=0, help="0=全部 Chroma 片段")
    args = parser.parse_args()
    force = args.force or not args.no_force

    report = build_indexes(
        force=force,
        skip_neo4j=args.skip_neo4j,
        skip_chroma=args.skip_chroma,
        batch_neo4j=args.batch_neo4j,
        batch_chroma=args.batch_chroma,
        chroma_limit=args.chroma_limit,
    )
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
