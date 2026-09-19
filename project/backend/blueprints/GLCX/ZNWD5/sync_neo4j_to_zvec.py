"""
Neo4j 图节点 → Zvec graph_entities 全量/增量同步。
用法（backend 目录）：
  C:\\Python310\\python.exe blueprints/GLCX/ZNWD5/sync_neo4j_to_zvec.py
  C:\\Python310\\python.exe blueprints/GLCX/ZNWD5/sync_neo4j_to_zvec.py --force
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sync_neo4j_to_zvec")

NODE_LABELS = [
    "EquipmentType",
    "Equipment",
    "Environment",
    "TestProject",
    "Indicator",
    "TestMethod",
    "File",
]


def _connect_neo4j():
    from neo4j import GraphDatabase
    from config import Config

    uri = os.environ.get("NEO4J_URI", Config.NEO4J_URI)
    user = os.environ.get("NEO4J_USER", Config.NEO4J_USER)
    pwd = os.environ.get("NEO4J_PASSWORD", Config.NEO4J_PASSWORD)
    return GraphDatabase.driver(uri, auth=(user, pwd))


def fetch_nodes(driver) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    cypher = """
    MATCH (n)
    WHERE any(l IN labels(n) WHERE l IN $labels)
    RETURN labels(n) AS labels,
           coalesce(n.Id, '') AS id,
           coalesce(n.Name, '') AS name,
           coalesce(n.Label, '') AS label,
           coalesce(n.Type, '') AS type,
           coalesce(n.Description, '') AS description,
           coalesce(toString(n.Status), '') AS status
    """
    with driver.session() as session:
        for rec in session.run(cypher, labels=NODE_LABELS):
            labs = list(rec.get("labels") or [])
            nt = next((l for l in labs if l in NODE_LABELS), labs[0] if labs else "")
            rows.append(
                {
                    "node_type": nt,
                    "neo4j_id": str(rec.get("id") or ""),
                    "name": str(rec.get("name") or ""),
                    "label": str(rec.get("label") or ""),
                    "type": str(rec.get("type") or ""),
                    "description": str(rec.get("description") or ""),
                    "status": str(rec.get("status") or ""),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Neo4j nodes to Zvec graph_entities")
    parser.add_argument("--force", action="store_true", help="删除旧索引目录后重建")
    parser.add_argument("--batch-size", type=int, default=64)
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
    node_to_text = emb.node_to_text
    get_zvec_manager = mgr_mod.get_zvec_manager
    reset_zvec_manager = mgr_mod.reset_zvec_manager
    remove_zvec_lock_files = mgr_mod.remove_zvec_lock_files

    mgr = get_zvec_manager()
    if args.force and os.path.isdir(mgr.graph_path):
        import shutil

        reset_zvec_manager()
        mgr = get_zvec_manager()
        shutil.rmtree(mgr.graph_path, ignore_errors=True)
        logger.info("已删除旧 graph_entities 索引: %s", mgr.graph_path)

    reset_zvec_manager()
    remove_zvec_lock_files()
    mgr = get_zvec_manager()

    driver = _connect_neo4j()
    nodes = fetch_nodes(driver)
    driver.close()
    logger.info("Neo4j 拉取节点 %d 条", len(nodes))

    by_label: Dict[str, int] = {}
    for n in nodes:
        by_label[n["node_type"]] = by_label.get(n["node_type"], 0) + 1
    logger.info("标签分布: %s", json.dumps(by_label, ensure_ascii=False))

    mgr.open_graph(read_only=False, create=True)
    total = 0
    batch: List[Dict[str, Any]] = []
    texts: List[str] = []
    for n in nodes:
        text = node_to_text(
            {
                "name": n.get("name"),
                "label": n.get("label"),
                "type": n.get("type"),
                "description": n.get("description"),
            }
        )
        texts.append(text or n.get("name") or "")
        batch.append({**n, "text": text})

    for i in range(0, len(batch), args.batch_size):
        chunk = batch[i : i + args.batch_size]
        chunk_texts = texts[i : i + args.batch_size]
        vecs = encode_texts(chunk_texts)
        payload = []
        for j, row in enumerate(chunk):
            payload.append({**row, "vector": vecs[j].tolist()})
        total += mgr.upsert_graph_entities(payload)
        logger.info("已写入 %d / %d", min(i + args.batch_size, len(batch)), len(batch))

    meta = {
        "synced_at": datetime.now().isoformat(),
        "node_count": len(nodes),
        "by_label": by_label,
        "backend": "zvec" if mgr.use_zvec else "fallback",
    }
    meta_path = os.path.join(mgr.base_path, "graph_entities_sync_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("同步完成 total=%d graph_count=%d meta=%s", total, mgr.graph_count(), meta_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
