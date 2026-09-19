"""
ZNWD6 一键重建索引（复用 ZNWD5 逻辑 + ontology 预检）。

用法（backend 目录）：
  python blueprints/GLCX/ZNWD6/build_indexes.py --force
"""

from __future__ import annotations

import argparse
import hashlib
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
logger = logging.getLogger("build_znwd6_indexes")


def _ontology_hash() -> str:
    from blueprints.GLCX.ZNWD6.ontology_loader import load_ontology

    load_ontology.cache_clear()
    raw = json.dumps(load_ontology(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _check_neo4j_labels(expected: List[str]) -> Dict[str, Any]:
    from config import Config
    from neo4j import GraphDatabase

    out: Dict[str, Any] = {"ok": True, "labels_in_db": [], "missing": [], "extra": []}
    expected_set = set(expected or [])
    try:
        driver = GraphDatabase.driver(Config.NEO4J_URI, auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD))
        with driver.session() as s:
            rows = s.run(
                "CALL db.labels() YIELD label RETURN collect(label) AS labels"
            ).single()
            labels = set(rows.get("labels") or []) if rows else set()
        driver.close()
        out["labels_in_db"] = sorted(labels)
        out["missing"] = sorted(expected_set - labels)
        out["extra"] = sorted(labels - expected_set - {"__Entity__"})
        if out["missing"]:
            out["ok"] = False
            logger.warning("Neo4j 缺少 ontology 节点标签: %s", out["missing"])
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)
        logger.warning("Neo4j label 检查失败: %s", exc)
    return out


def build_indexes(*, force: bool = True, skip_neo4j: bool = False, skip_chroma: bool = False) -> Dict[str, Any]:
    from blueprints.GLCX.ZNWD6.ontology_loader import load_ontology

    load_ontology.cache_clear()
    ont = load_ontology()
    label_check = _check_neo4j_labels(list(ont.get("node_labels") or []))

    znwd5_build = os.path.join(_HERE, "..", "ZNWD5", "build_znwd5_indexes.py")
    import importlib.util

    spec = importlib.util.spec_from_file_location("znwd5_build_indexes", znwd5_build)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {znwd5_build}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    report = mod.build_indexes(force=force, skip_neo4j=skip_neo4j, skip_chroma=skip_chroma)
    report["znwd6_ontology_hash"] = _ontology_hash()
    report["znwd6_label_check"] = label_check
    report["engine"] = "znwd6"

    base = os.environ.get("ZVEC_DATA_PATH") or os.path.join(_BACKEND, "zvec_data")
    manifest_path = os.path.join(base, "znwd6_build_manifest.json")
    os.makedirs(base, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({**report, "written_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
    logger.info("已写入 %s", manifest_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild ZNWD6 indexes (wraps ZNWD5 builder)")
    parser.add_argument("--force", action="store_true", default=True)
    parser.add_argument("--skip-neo4j", action="store_true")
    parser.add_argument("--skip-chroma", action="store_true")
    args = parser.parse_args()
    build_indexes(force=args.force, skip_neo4j=args.skip_neo4j, skip_chroma=args.skip_chroma)


if __name__ == "__main__":
    main()
