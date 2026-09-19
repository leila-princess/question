"""ZNWD6 迁移就绪检查。"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def run_migrate_check() -> Dict[str, Any]:
    report: Dict[str, Any] = {"ok": True, "checks": {}}

    try:
        from blueprints.GLCX.ZNWD6.ontology_loader import load_ontology

        load_ontology.cache_clear()
        ont = load_ontology()
        report["checks"]["ontology"] = {
            "ok": bool(ont.get("templates")),
            "templates": len(ont.get("templates") or {}),
            "node_labels": ont.get("node_labels") or [],
        }
    except Exception as exc:
        report["ok"] = False
        report["checks"]["ontology"] = {"ok": False, "error": str(exc)}

    try:
        from config import Config
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(Config.NEO4J_URI, auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD))
        with driver.session() as s:
            s.run("RETURN 1").single()
        driver.close()
        report["checks"]["neo4j"] = {"ok": True, "uri": Config.NEO4J_URI}
    except Exception as exc:
        report["ok"] = False
        report["checks"]["neo4j"] = {"ok": False, "error": str(exc)}

    try:
        from blueprints.GLCX.ZNWD5.zvec_index_manager import get_zvec_manager, zvec_available

        mgr = get_zvec_manager()
        mgr.open(read_only=True)
        report["checks"]["zvec"] = {
            "ok": zvec_available(),
            "graph_entities": mgr.graph_count(),
            "documents": mgr.doc_count(),
        }
    except Exception as exc:
        report["ok"] = False
        report["checks"]["zvec"] = {"ok": False, "error": str(exc)}

    try:
        from config import Config
        from openai import OpenAI

        base = (Config.OPENAI_BASE_URL or "").strip()
        client = OpenAI(base_url=base, api_key=Config.OPENAI_API_KEY or "EMPTY")
        models = client.models.list()
        report["checks"]["llm"] = {"ok": True, "base_url": base, "models": len(getattr(models, "data", []) or [])}
    except Exception as exc:
        report["checks"]["llm"] = {"ok": False, "error": str(exc)}

    return report


def main() -> None:
    report = run_migrate_check()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()
