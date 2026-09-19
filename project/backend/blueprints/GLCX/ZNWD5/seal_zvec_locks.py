"""
建索引后封存 zvec LOCK（Windows/zvec0.5：close 后若无 LOCK 则无法再 open）。

用法（backend 目录，索引 build 完成后立即执行）：
  python blueprints/GLCX/ZNWD5/seal_zvec_locks.py
"""

from __future__ import annotations

import logging
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seal_zvec_locks")


def seal_collection(path: str, label: str) -> bool:
    lock = os.path.join(path, "LOCK")
    if os.path.isfile(lock):
        logger.info("%s LOCK 已存在: %s", label, lock)
        return True
    if not os.path.isdir(path):
        logger.warning("%s 目录不存在: %s", label, path)
        return False
    try:
        import zvec
        from zvec import CollectionOption, init as zvec_init, open as zvec_open

        zvec_init()
        coll = zvec_open(path=path, option=CollectionOption(read_only=True, enable_mmap=False))
        count = int(coll.stats.doc_count)
        # zvec Collection 无 close()，依赖进程退出；打开后 LOCK 应已创建
        if os.path.isfile(lock):
            logger.info("%s 封存 OK count=%d LOCK=%s", label, count, lock)
            return True
        # 兜底：创建空 LOCK 占位（部分环境 open 后仍无 LOCK 文件）
        open(lock, "wb").close()
        logger.info("%s 兜底创建 LOCK count=%d", label, count)
        return True
    except Exception as exc:
        logger.error("%s 封存失败: %s", label, exc)
        return False


def main() -> int:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "zvec_index_manager",
        os.path.join(_HERE, "zvec_index_manager.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.reset_zvec_manager()
    mgr = mod.get_zvec_manager()
    ok = True
    ok = seal_collection(mgr.graph_path, "graph_entities") and ok
    ok = seal_collection(mgr.doc_path, "documents") and ok
    mod.reset_zvec_manager()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
