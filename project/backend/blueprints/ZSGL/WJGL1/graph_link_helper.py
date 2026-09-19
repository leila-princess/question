"""规则化图节点关联候选（文档审核页 MVP）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


GRAPH_NODE_TYPES = (
    ("EquipmentType", "equipment_type"),
    ("Equipment", "equipment"),
    ("Environment", "environment"),
    ("TestProject", "test_project"),
    ("Indicator", "indicator"),
    ("TestMethod", "test_method"),
)

# slot → (rel_type, from_label, to_label)
SLOT_REL_MAP: Dict[str, Tuple[str, str, str]] = {
    "equipment": ("APPLIES_TO", "File", "Equipment"),
    "environment": ("CONSTRAINED_BY", "Environment", "File"),
    "test_project": ("GOVERNED_BY", "TestProject", "File"),
    "test_method": ("DERIVED_FROM", "TestMethod", "File"),
    "equipment_type": ("BELONGS_TO", "File", "EquipmentType"),
    "indicator": ("BELONGS_TO", "File", "Indicator"),
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


def _rule_score(mention: str, node_name: str, node_label: str = "") -> Tuple[float, str]:
    m = _norm(mention)
    n = _norm(node_name)
    if not m or not n:
        return 0.0, ""
    if m == n:
        return 0.35, "R1名称精确"
    if m in n or n in m:
        return 0.20, "R2名称包含"
    if node_label:
        labels = [x.strip() for x in re.split(r"[、,，;；]", node_label) if x.strip()]
        for lb in labels:
            ln = _norm(lb)
            if ln and (m in ln or ln in m):
                return 0.15, "R5标签重叠"
    return 0.0, ""


def _load_nodes(neo4j_driver, label: str, limit: int = 500) -> List[Dict[str, Any]]:
    if neo4j_driver is None:
        return []
    limit_clause = f" LIMIT {max(1, int(limit))}" if limit else ""
    cypher = f"""
    MATCH (n:{label})
    RETURN
        COALESCE(n.Id, '') AS id,
        COALESCE(n.Name, '') AS name,
        COALESCE(n.Label, '') AS label,
        COALESCE(n.Type, '') AS type,
        COALESCE(n.Description, '') AS description,
        COALESCE(n.Source_name, '') AS source_name,
        COALESCE(n.Equipment_name, '') AS equipment_name
    {limit_clause}
    """
    items: List[Dict[str, Any]] = []
    try:
        with neo4j_driver.session() as session:
            for row in session.run(cypher):
                items.append(dict(row))
    except Exception:
        return []
    return items


def _mentions_from_doc(doc: Dict[str, Any]) -> List[Dict[str, str]]:
    mentions: List[Dict[str, str]] = []
    std_name = (doc.get("std_name") or doc.get("title") or "").strip()
    std_code = (doc.get("std_code") or "").strip()
    abstract = (doc.get("abstract") or "").strip()
    keywords = doc.get("keywords") or ""
    if isinstance(keywords, list):
        kw_text = " ".join(str(k) for k in keywords)
    else:
        kw_text = str(keywords)

    if std_name:
        mentions.append({"slot": "equipment", "text": std_name, "reason": "标准/文档标题"})
    if std_code:
        mentions.append({"slot": "test_method", "text": std_code, "reason": "标准号交叉引用"})

    for kw in re.split(r"[,，;；\s]+", kw_text):
        kw = kw.strip()
        if len(kw) >= 2:
            mentions.append({"slot": "test_project", "text": kw, "reason": "关键词"})

    env_hints = ("高温", "低温", "湿热", "振动", "冲击", "盐雾", "砂尘", "淋雨", "霉菌", "防水")
    blob = f"{std_name} {abstract} {kw_text}"
    for hint in env_hints:
        if hint in blob:
            mentions.append({"slot": "environment", "text": hint, "reason": "环境关键词"})

    part_m = re.search(r"第[0-9０-９]+部分[：:]\s*(.+)", std_name)
    if part_m:
        part_title = part_m.group(1).strip()
        if part_title:
            mentions.append({"slot": "test_project", "text": part_title, "reason": "标准分部分标题"})
            for hint in env_hints:
                if hint in part_title:
                    mentions.append({"slot": "environment", "text": hint, "reason": "分部分环境主题"})

    if "无人机" in blob:
        mentions.append({"slot": "equipment", "text": "无人机", "reason": "装备关键词"})
    if "试验方法" in std_name:
        mentions.append({"slot": "test_method", "text": std_name, "reason": "试验方法标准"})

    equip_hints = re.findall(r"[\u4e00-\u9fff]{0,8}(?:型|机|车|舰|弹|雷达|无人机)", blob)
    for eq in equip_hints[:5]:
        mentions.append({"slot": "equipment", "text": eq, "reason": "装备名模式"})

    return mentions


def _rel_props_for_slot(slot: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    fact_index = doc.get("fact_index") or {}
    if isinstance(fact_index, str):
        try:
            import json
            fact_index = json.loads(fact_index)
        except Exception:
            fact_index = {}
    content_slots = doc.get("content_slots") or {}
    if isinstance(content_slots, str):
        try:
            import json
            content_slots = json.loads(content_slots)
        except Exception:
            content_slots = {}
    conditions = list(fact_index.get("conditions") or [])
    sytj = content_slots.get("sytj") if isinstance(content_slots, dict) else {}
    clause_no = (sytj or {}).get("clause_no", "") if isinstance(sytj, dict) else ""
    excerpt = (sytj or {}).get("excerpt", "")[:200] if isinstance(sytj, dict) else ""

    if slot == "environment" and conditions:
        temps = [c for c in conditions if c.get("type") == "temperature"]
        limit = " / ".join(f"{c.get('value', '')}{c.get('unit', '')}" for c in temps)[:120]
        return {
            "clause_no": clause_no,
            "conditions": conditions,
            "limit": limit,
            "request": excerpt,
        }
    if slot == "test_project":
        clause_nos = [
            (content_slots.get(s) or {}).get("clause_no", "")
            for s in ("sytj", "sygc", "jgpd")
            if isinstance(content_slots, dict) and (content_slots.get(s) or {}).get("clause_no")
        ]
        return {
            "role": "primary",
            "std_code": doc.get("std_code") or "",
            "clause_nos": clause_nos,
            "note": doc.get("std_name") or doc.get("title") or "",
        }
    if slot == "test_method" and isinstance(content_slots, dict):
        return {
            "slot": "sytj",
            "clause_no": clause_no,
            "clause_title": (sytj or {}).get("title", "") if isinstance(sytj, dict) else "",
            "excerpt": excerpt,
            "confidence": 0.85,
            "conditions": conditions,
        }
    if slot == "equipment":
        return {
            "scope": doc.get("std_name") or doc.get("std_code") or "",
            "mandatory": True,
            "note": "graph_link",
        }
    return {}


def build_graph_link_candidates(
    doc: Dict[str, Any],
    neo4j_driver,
    embed_fn=None,
    top_k_per_slot: int = 5,
) -> Dict[str, Any]:
    mentions = _mentions_from_doc(doc)
    candidates: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, str]] = []
    seen: set = set()

    for graph_label, slot in GRAPH_NODE_TYPES:
        nodes = _load_nodes(neo4j_driver, graph_label)
        slot_mentions = [m for m in mentions if m.get("slot") == slot] or mentions[:3]
        rel_type, from_label, to_label = SLOT_REL_MAP.get(slot, ("BELONGS_TO", "File", graph_label))
        rel_props = _rel_props_for_slot(slot, doc)

        for node in nodes:
            node_id = (node.get("id") or "").strip()
            node_name = (node.get("name") or "").strip()
            if not node_id or not node_name:
                continue
            best_rule = 0.0
            best_rule_detail = ""
            best_mention = ""
            for m in slot_mentions:
                rs, rd = _rule_score(m.get("text", ""), node_name, node.get("label") or "")
                if rs > best_rule:
                    best_rule = rs
                    best_rule_detail = rd
                    best_mention = m.get("text", "")

            std_code = (doc.get("std_code") or "").strip()
            if graph_label == "TestMethod" and std_code:
                src = (node.get("source_name") or node.get("name") or "")
                if std_code.lower().replace(" ", "") in src.lower().replace(" ", ""):
                    best_rule = max(best_rule, 0.30)
                    best_rule_detail = best_rule_detail or "R3标准号交叉"
                    best_mention = best_mention or std_code

            if graph_label == "TestProject":
                eqn = (node.get("equipment_name") or "").strip()
                for m in slot_mentions:
                    if eqn and _norm(m.get("text", "")) and _norm(eqn) == _norm(m.get("text", "")):
                        best_rule = max(best_rule, 0.25)
                        best_rule_detail = best_rule_detail or "R4设备名继承"

            embed_score = 0.0
            if embed_fn and best_mention:
                try:
                    node_text = "；".join(
                        x for x in [node_name, node.get("label"), node.get("description")] if x
                    )
                    embed_score = float(embed_fn(best_mention, node_text))
                except Exception:
                    embed_score = 0.0

            if embed_fn:
                final = 0.55 * embed_score + 0.25 * min(best_rule / 0.35, 1.0) + 0.20 * (0.1 if best_rule else 0)
            else:
                final = 0.70 * min(best_rule / 0.35, 1.0) + 0.30 * (0.15 if best_rule else 0)

            if final < 0.35:
                continue

            key = (graph_label, node_id)
            if key in seen:
                continue
            seen.add(key)

            status = "recommended" if final >= 0.72 else ("tentative" if final >= 0.50 else "low")
            candidates.append({
                "slot": slot,
                "node_type": graph_label,
                "node_id": node_id,
                "node_name": node_name,
                "score": round(final, 4),
                "status": status,
                "evidence": {
                    "mention_text": best_mention,
                    "rule_detail": best_rule_detail,
                    "rule_score": round(best_rule, 4),
                    "embed_score": round(embed_score, 4) if embed_fn else None,
                },
                "suggested_rel": rel_type,
                "from_label": from_label,
                "to_label": to_label,
                "rel_props": rel_props,
            })

    candidates.sort(key=lambda x: -x["score"])
    per_slot: Dict[str, int] = {}
    trimmed: List[Dict[str, Any]] = []
    for c in candidates:
        s = c["slot"]
        if per_slot.get(s, 0) >= top_k_per_slot:
            continue
        per_slot[s] = per_slot.get(s, 0) + 1
        trimmed.append(c)

    for m in mentions:
        if not any(
            _norm(m.get("text", "")) in _norm(c["node_name"]) or _norm(c["node_name"]) in _norm(m.get("text", ""))
            for c in trimmed if c["slot"] == m.get("slot")
        ):
            unresolved.append({
                "slot": m.get("slot", ""),
                "text": m.get("text", ""),
                "reason": "图库无近似节点",
            })

    summary_parts = []
    if doc.get("std_name") or doc.get("title"):
        summary_parts.append(str(doc.get("std_name") or doc.get("title")))
    if doc.get("std_code"):
        summary_parts.append(str(doc.get("std_code")))
    if doc.get("abstract"):
        summary_parts.append(str(doc.get("abstract"))[:80])

    return {
        "document_summary": " · ".join(summary_parts)[:300] or "暂无摘要",
        "candidates": trimmed,
        "unresolved_mentions": unresolved[:10],
        "neo4j_available": neo4j_driver is not None,
        "warnings": (
            ["Neo4j 未连接，无法从图库召回关联节点。请检查 NEO4J_URI 并重启 Flask。"]
            if neo4j_driver is None
            else []
        ),
    }
