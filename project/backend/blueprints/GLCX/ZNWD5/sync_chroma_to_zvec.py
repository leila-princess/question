"""
Chroma drone_standards → Zvec documents 迁移。
用法（backend 目录）：
  python blueprints/GLCX/ZNWD5/sync_chroma_to_zvec.py --force --re-embed
  python blueprints/GLCX/ZNWD5/sync_chroma_to_zvec.py --force --re-embed --batch-size 32
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sync_chroma_to_zvec")


def _extract_std_code(title: str) -> str:
    m = re.search(r"(G\s*B\s*/?\s*T\s*[\d\.\-]+)", title or "", re.I)
    return re.sub(r"\s+", "", m.group(1)) if m else ""


def _fetch_chroma_chunks_sqlite(limit: int = 0) -> List[Dict[str, Any]]:
    """sqlite 直读 Chroma（避免 chromadb 与 NumPy 2.0 不兼容）。"""
    import sqlite3

    from config import Config

    base = os.path.abspath(Config.CHROMA_PATH)
    db_file = os.path.join(base, "chroma.sqlite3")
    if not os.path.isfile(db_file):
        raise FileNotFoundError(f"Chroma sqlite 不存在: {db_file}")

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    ids = [r[0] for r in conn.execute("SELECT id FROM embeddings")]
    conn.close()

    take = len(ids) if limit <= 0 else min(limit, len(ids))
    ids = ids[:take]

    rows: List[Dict[str, Any]] = []
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    for cid in ids:
        meta: Dict[str, Any] = {}
        content = ""
        for r in conn.execute(
            "SELECT key, string_value, int_value FROM embedding_metadata WHERE id=?",
            (cid,),
        ):
            k = r["key"]
            if k in ("#document", "chroma:document"):
                content = r["string_value"] or content
            elif r["string_value"] is not None:
                meta[k] = r["string_value"]
            elif r["int_value"] is not None:
                meta[k] = r["int_value"]
        title = str(meta.get("title") or "")
        section = str(meta.get("section_no") or meta.get("section") or "")
        std_code = str(meta.get("std_code") or _extract_std_code(title))
        rows.append(
            {
                "chunk_id": str(cid),
                "doc_id": str(meta.get("doc_id") or cid),
                "title": title,
                "section": section,
                "std_code": std_code,
                "content": content or "",
                "vector": None,
            }
        )
    conn.close()
    logger.info("Chroma sqlite 直读 %d 条", len(rows))
    return rows


def fetch_chroma_chunks(limit: int = 0) -> List[Dict[str, Any]]:
    from config import Config

    try:
        import chromadb
    except Exception as exc:
        logger.warning("chromadb 导入失败 (%s)，改用 sqlite 直读", exc)
        return _fetch_chroma_chunks_sqlite(limit)

    try:
        path = os.path.abspath(Config.CHROMA_PATH)
        name = Config.CHROMA_COLLECTION
        client = chromadb.PersistentClient(path=path)
        col = client.get_collection(name=name)
        total = col.count()
        take = total if limit <= 0 else min(limit, total)
        data = col.get(limit=take, include=["documents", "metadatas"])
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        rows: List[Dict[str, Any]] = []
        for i, cid in enumerate(ids):
            md = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
            title = str(md.get("title") or "")
            content = docs[i] if i < len(docs) else ""
            section = str(md.get("section_no") or md.get("section") or "")
            std_code = str(md.get("std_code") or _extract_std_code(title))
            rows.append(
                {
                    "chunk_id": cid,
                    "doc_id": str(md.get("doc_id") or cid),
                    "title": title,
                    "section": section,
                    "std_code": std_code,
                    "content": content or "",
                    "vector": None,
                }
            )
        return rows
    except Exception as exc:
        logger.warning("chromadb 客户端失败 (%s)，改用 sqlite 直读", exc)
        return _fetch_chroma_chunks_sqlite(limit)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Chroma chunks to Zvec documents")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="仅迁移前 N 条，0=全部")
    parser.add_argument("--re-embed", action="store_true", help="用 BGE 重新嵌入（推荐）")
    parser.add_argument("--batch-size", type=int, default=32, help="嵌入/写入批大小")
    args = parser.parse_args()

    try:
        from config import Config

        bge_path = os.path.abspath(getattr(Config, "FLGL_TESTMETHOD_BGE_MODEL_PATH", "") or "")
        if bge_path and os.path.isdir(bge_path):
            os.environ.setdefault("FLGL_TESTMETHOD_BGE_MODEL_PATH", bge_path)
    except Exception:
        pass

    return _run_sync(args)


def _import_znwd5_module(module_name: str):
    """避免加载 ZNWD5/__init__.py（Flask 蓝图副作用）。"""
    import importlib.util

    path = os.path.join(_HERE, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(f"znwd5_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_sync(args) -> int:
    emb = _import_znwd5_module("embedding_service")
    mgr_mod = _import_znwd5_module("zvec_index_manager")
    encode_texts = emb.encode_texts
    embedding_backend = emb.embedding_backend
    using_hash_fallback = emb.using_hash_fallback
    get_zvec_manager = mgr_mod.get_zvec_manager

    if using_hash_fallback():
        logger.error("BGE 不可用（backend=%s），请先修复 embedding_service", embedding_backend())
        return 1

    batch_size = max(8, int(args.batch_size or 32))
    mgr = get_zvec_manager()
    if args.force and os.path.isdir(mgr.doc_path):
        import shutil

        reset_zvec_manager = mgr_mod.reset_zvec_manager
        remove_zvec_lock_files = mgr_mod.remove_zvec_lock_files
        reset_zvec_manager()
        mgr = get_zvec_manager()
        shutil.rmtree(mgr.doc_path, ignore_errors=True)

    reset_zvec_manager = mgr_mod.reset_zvec_manager
    remove_zvec_lock_files = mgr_mod.remove_zvec_lock_files
    reset_zvec_manager()
    remove_zvec_lock_files(mgr.base_path, collections=("documents",))
    mgr = get_zvec_manager()

    rows = fetch_chroma_chunks(limit=args.limit)
    logger.info("Chroma 拉取 %d 条，batch_size=%d embed=%s", len(rows), batch_size, embedding_backend())

    mgr.open_docs(read_only=False, create=True)
    total = 0
    for i in range(0, len(rows), batch_size):
        chunk_rows = rows[i : i + batch_size]
        if args.re_embed or any(r.get("vector") is None for r in chunk_rows):
            texts = [str(r.get("content") or r.get("title") or "")[:2000] for r in chunk_rows]
            vecs = encode_texts(texts, batch_size=batch_size)
            for j, vec in enumerate(vecs):
                chunk_rows[j]["vector"] = vec.tolist()

        upsert_batch = [r for r in chunk_rows if r.get("vector")]
        if upsert_batch:
            total += mgr.upsert_documents(upsert_batch)
        logger.info("已写入 %d / %d", min(i + batch_size, len(rows)), len(rows))

    meta = {
        "synced_at": datetime.now().isoformat(),
        "chunk_count": len(rows),
        "backend": "zvec" if mgr.use_zvec else "fallback",
        "re_embed": args.re_embed,
        "batch_size": batch_size,
        "embedding_backend": embedding_backend(),
    }
    meta_path = os.path.join(mgr.base_path, "documents_sync_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("迁移完成 total=%d doc_count=%d", total, mgr.doc_count())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
