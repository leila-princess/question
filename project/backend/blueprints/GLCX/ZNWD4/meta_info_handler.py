"""
元信息问句：图库/向量库统计直答（不走 LLM）。
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional, Tuple

_NEO_STATS_Q = re.compile(r"(图数据库|neo4j|图库|知识图谱).{0,16}(多少|统计|有多少|几条|规模)", re.I)
_CHROMA_STATS_Q = re.compile(r"(向量库|chroma|文档库|文档片段).{0,16}(多少|统计|有多少|几条|规模)", re.I)


def looks_like_meta_info_question(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    return bool(_NEO_STATS_Q.search(q) or _CHROMA_STATS_Q.search(q))


def _count_neo4j(driver: Any) -> str:
    labels = (
        "EquipmentType",
        "Equipment",
        "Environment",
        "TestProject",
        "Indicator",
        "TestMethod",
        "File",
    )
    parts: list = []
    rel_count = 0
    with driver.session() as session:
        for lbl in labels:
            rec = session.run(f"MATCH (n:{lbl}) RETURN count(n) AS c").single()
            parts.append(f"{lbl}={int(rec['c']) if rec else 0}")
        rec = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()
        rel_count = int(rec["c"]) if rec else 0
    return f"当前图库统计：{'；'.join(parts)}；relations={rel_count}。"


def _count_chroma() -> str:
    try:
        from blueprints.GLCX.ZNWD1.generate_qa_pairs import fetch_chroma_data_sqlite
        from config import Config

        cp = getattr(Config, "CHROMA_PATH", "") or ""
        data = fetch_chroma_data_sqlite(cp if cp else None)
        chunks = data.get("chunks") or []
        doc_ids = {str(c.get("doc_id") or "") for c in chunks if c.get("doc_id")}
        return f"Chroma 向量库共 {len(chunks)} 条文档片段，涵盖 {len(doc_ids)} 份文档。"
    except Exception:
        return "Chroma 向量库统计暂不可用，请检查 CHROMA_PATH 配置。"


def try_answer_meta_info(
    question: str,
    *,
    driver: Any = None,
) -> Tuple[bool, str]:
    q = (question or "").strip()
    if not q:
        return False, ""
    if _NEO_STATS_Q.search(q):
        if not driver:
            return True, "图数据库未连接，无法统计节点数量。"
        return True, _count_neo4j(driver)
    if _CHROMA_STATS_Q.search(q):
        return True, _count_chroma()
    return False, ""
