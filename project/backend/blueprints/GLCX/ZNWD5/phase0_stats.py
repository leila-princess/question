"""
Phase 0：Neo4j 节点规模统计 + 环境检查。
用法：C:\\Python310\\python.exe blueprints/GLCX/ZNWD5/phase0_stats.py
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def main() -> int:
    from neo4j import GraphDatabase
    from config import Config

    from blueprints.GLCX.ZNWD5.zvec_index_manager import get_zvec_manager, zvec_available

    uri = os.environ.get("NEO4J_URI", Config.NEO4J_URI)
    user = os.environ.get("NEO4J_USER", Config.NEO4J_USER)
    pwd = os.environ.get("NEO4J_PASSWORD", Config.NEO4J_PASSWORD)

    labels = [
        "EquipmentType",
        "Equipment",
        "Environment",
        "TestProject",
        "Indicator",
        "TestMethod",
        "File",
    ]
    stats = {"neo4j_uri": uri, "labels": {}, "zvec_available": zvec_available()}

    try:
        driver = GraphDatabase.driver(uri, auth=(user, pwd))
        with driver.session() as session:
            for lbl in labels:
                cnt = session.run(f"MATCH (n:{lbl}) RETURN count(n) AS c").single()["c"]
                stats["labels"][lbl] = int(cnt)
            ver = session.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v").single()
            stats["neo4j_version"] = str((ver or {}).get("v") or "")
        driver.close()
    except Exception as exc:
        stats["neo4j_error"] = str(exc)

    mgr = get_zvec_manager()
    try:
        mgr.open(read_only=True)
        stats["zvec_graph_count"] = mgr.graph_count()
        stats["zvec_doc_count"] = mgr.doc_count()
        stats["zvec_backend"] = "zvec" if mgr.use_zvec else "fallback"
    except Exception as exc:
        stats["zvec_error"] = str(exc)

    # ZNWD4 基线指标（来自已有报告，无需重跑 500 题）
    stats["znwd4_baseline"] = {
        "source": "qa_benchmark_report_qwen25_14b_awq.md",
        "overall_accuracy": 0.53,
        "equip_indicator_accuracy": 0.188,
        "vector_chapter_accuracy": 0.084,
        "missing_entities_errors": 119,
    }

    out_path = os.path.join(_HERE, "phase0_environment_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
