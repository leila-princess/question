"""将标准抽取事实写入 File 属性与 Neo4j 关系边。"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from config import Config

from .extractors import (
    bucket_sections_for_slots,
    extract_conditions,
    extract_refs_from_text,
    extract_normative_ref_list,
)
from .extractors.slot_bucketer import excerpt_from_section, pick_primary_section, section_clause_no
from .schema import (
    EXTRACT_VERSION,
    REL_APPLIES_TO,
    REL_CITES,
    REL_CONSTRAINED_BY,
    REL_DERIVED_FROM,
    REL_GOVERNED_BY,
    REL_LIMITED_BY,
    infer_file_kind,
    serialize_json_field,
    should_extract_facts,
)

logger = logging.getLogger(__name__)


def _build_content_slots(buckets: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    slots: Dict[str, Any] = {}
    for slot, secs in buckets.items():
        sec = pick_primary_section(secs)
        if not sec:
            continue
        slots[slot] = {
            "clause_no": section_clause_no(sec),
            "title": str(sec.get("title") or "").strip(),
            "excerpt": excerpt_from_section(sec),
            "section_id": sec.get("section_id"),
        }
    return slots


def _build_fact_index(
    buckets: Dict[str, List[Dict[str, Any]]],
    sections: List[Dict[str, Any]],
) -> Dict[str, Any]:
    conditions = extract_conditions(sections, buckets)
    refs: List[Dict[str, Any]] = []
    terms: List[Dict[str, Any]] = []

    for sec in sections or []:
        title = str(sec.get("title") or "")
        if "术语" in title or "定义" in title:
            clause = section_clause_no(sec)
            for line in str(sec.get("text") or "").splitlines()[:30]:
                line = line.strip()
                if not line or len(line) < 4:
                    continue
                terms.append({"name": line[:80], "clause_no": clause})
        text = str(sec.get("text") or "")
        refs.extend(extract_refs_from_text(text))

    if not refs:
        for sec in sections or []:
            if "规范性引用" in str(sec.get("title") or ""):
                refs.extend(extract_normative_ref_list(str(sec.get("text") or "")))

    # dedupe refs
    seen = set()
    uniq_refs = []
    for r in refs:
        key = (r.get("target_std"), r.get("target_clause"))
        if key in seen:
            continue
        seen.add(key)
        uniq_refs.append(r)

    return {"conditions": conditions, "refs": uniq_refs[:50], "terms": terms[:30]}


def _derived_from_links(content_slots: Dict[str, Any], fact_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    links = []
    cond_by_slot: Dict[str, List] = {}
    for c in fact_index.get("conditions") or []:
        slot = str(c.get("slot") or "sytj")
        cond_by_slot.setdefault(slot, []).append(c)

    for slot, info in (content_slots or {}).items():
        if not isinstance(info, dict):
            continue
        links.append({
            "slot": slot,
            "clause_no": info.get("clause_no") or "",
            "clause_title": info.get("title") or "",
            "excerpt": info.get("excerpt") or "",
            "confidence": 0.9,
            "conditions": cond_by_slot.get(slot, []),
        })
    return links


def _merge_file_properties(session: Any, file_id: str, props: Dict[str, Any]) -> None:
    session.run(
        """
        MATCH (f:File {Id: $file_id})
        SET f.File_kind = $file_kind,
            f.content_slots = $content_slots,
            f.fact_index = $fact_index,
            f.extract_version = $extract_version,
            f.extract_status = $extract_status
        """,
        file_id=file_id,
        file_kind=props.get("file_kind", ""),
        content_slots=props.get("content_slots", ""),
        fact_index=props.get("fact_index", ""),
        extract_version=props.get("extract_version", EXTRACT_VERSION),
        extract_status=props.get("extract_status", "done"),
    )


def merge_relationship(
    session: Any,
    rel_type: str,
    from_label: str,
    from_id: str,
    to_label: str,
    to_id: str,
    props: Optional[Dict[str, Any]] = None,
) -> None:
    props = props or {}
    flat_props = dict(props)
    for k, v in list(flat_props.items()):
        if isinstance(v, (list, dict)):
            flat_props[k] = serialize_json_field(v)
    cypher = f"""
    MATCH (a:{from_label} {{Id: $from_id}})
    MATCH (b:{to_label} {{Id: $to_id}})
    MERGE (a)-[r:{rel_type}]->(b)
    SET r += $props
    """
    session.run(cypher, from_id=from_id, to_id=to_id, props=flat_props)


def _link_cites(session: Any, file_id: str, refs: List[Dict[str, Any]], std_code: str) -> int:
    count = 0
    for ref in refs or []:
        target_std = str(ref.get("target_std") or "").strip()
        if not target_std:
            continue
        session.run(
            """
            MATCH (f1:File {Id: $from_id})
            MERGE (f2:File {Std_code: $target_std})
            ON CREATE SET f2.Id = $placeholder_id, f2.Name = $target_std, f2.Type = '法规标准', f2.Status = '占位'
            MERGE (f1)-[r:CITES]->(f2)
            SET r.target_std = $target_std,
                r.target_clause = $target_clause,
                r.ref_text = $ref_text,
                r.ref_type = $ref_type
            """,
            from_id=file_id,
            target_std=target_std,
            target_clause=str(ref.get("target_clause") or ""),
            ref_text=str(ref.get("ref_text") or "")[:200],
            ref_type=str(ref.get("ref_type") or "normative"),
            placeholder_id=f"PLACEHOLDER_{target_std.replace('/', '_').replace(' ', '_')[:80]}",
        )
        count += 1
    return count


def attach_from_parsed(
    *,
    file_node_id: str,
    doc_id: str,
    category: str,
    parsed: Dict[str, Any],
    std_code: str = "",
    std_name: str = "",
    graph_matches: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """从 parse 结果挂接 File 事实与关系（法规标准）。"""
    sections = list(parsed.get("sections") or [])
    file_kind = infer_file_kind(category, std_code)

    out: Dict[str, Any] = {
        "success": False,
        "file_node_id": file_node_id,
        "file_kind": file_kind,
        "skipped": False,
        "relations": [],
    }

    if not should_extract_facts(file_kind):
        out["skipped"] = True
        out["success"] = True
        return out

    buckets = bucket_sections_for_slots(sections)
    content_slots = _build_content_slots(buckets)
    fact_index = _build_fact_index(buckets, sections)
    derived_links = _derived_from_links(content_slots, fact_index)

    rel_snapshot: List[Dict[str, Any]] = []

    if not Config.NEO4J_DRIVER:
        out["error"] = "Neo4j not available"
        return out

    try:
        with Config.NEO4J_DRIVER.session() as session:
            _merge_file_properties(session, file_node_id, {
                "file_kind": file_kind,
                "content_slots": serialize_json_field(content_slots),
                "fact_index": serialize_json_field(fact_index),
                "extract_version": EXTRACT_VERSION,
                "extract_status": "done",
            })

            cite_n = _link_cites(session, file_node_id, fact_index.get("refs") or [], std_code)
            rel_snapshot.append({"rel_type": REL_CITES, "count": cite_n})

            for gm in graph_matches or []:
                matches = gm.get("graph_matches") or {}
                method_id = str(gm.get("test_method_id") or gm.get("method_id") or "").strip()
                if method_id:
                    for link in derived_links:
                        merge_relationship(
                            session, REL_DERIVED_FROM, "TestMethod", method_id,
                            "File", file_node_id, link,
                        )
                        rel_snapshot.append({
                            "rel_type": REL_DERIVED_FROM,
                            "from": method_id,
                            "to": file_node_id,
                            "slot": link.get("slot"),
                        })

                for env in matches.get("environment") or []:
                    eid = str(env.get("Id") or env.get("id") or "").strip()
                    if not eid:
                        continue
                    conds = fact_index.get("conditions") or []
                    props = {
                        "clause_no": (content_slots.get("sytj") or {}).get("clause_no", ""),
                        "conditions": conds,
                        "limit": _limit_from_conditions(conds),
                        "request": (content_slots.get("sytj") or {}).get("excerpt", "")[:200],
                    }
                    merge_relationship(
                        session, REL_CONSTRAINED_BY, "Environment", eid,
                        "File", file_node_id, props,
                    )
                    rel_snapshot.append({"rel_type": REL_CONSTRAINED_BY, "from": eid, "to": file_node_id})

                for tp in matches.get("test_project") or []:
                    tid = str(tp.get("Id") or tp.get("id") or "").strip()
                    if not tid:
                        continue
                    clause_nos = [
                        (content_slots.get(s) or {}).get("clause_no", "")
                        for s in ("sytj", "sygc", "jgpd")
                        if (content_slots.get(s) or {}).get("clause_no")
                    ]
                    merge_relationship(
                        session, REL_GOVERNED_BY, "TestProject", tid,
                        "File", file_node_id,
                        {
                            "role": "primary",
                            "std_code": std_code or "",
                            "clause_nos": clause_nos,
                            "note": std_name or "",
                        },
                    )
                    rel_snapshot.append({"rel_type": REL_GOVERNED_BY, "from": tid, "to": file_node_id})

                for eq in matches.get("equipment") or []:
                    eid = str(eq.get("Id") or eq.get("id") or "").strip()
                    if not eid:
                        continue
                    merge_relationship(
                        session, REL_APPLIES_TO, "File", file_node_id,
                        "Equipment", eid,
                        {"scope": std_name or std_code or "", "mandatory": True, "note": "recall"},
                    )
                    rel_snapshot.append({"rel_type": REL_APPLIES_TO, "from": file_node_id, "to": eid})

        out["success"] = True
        out["content_slots"] = content_slots
        out["fact_index"] = fact_index
        out["derived_links"] = derived_links
        out["relations"] = rel_snapshot
    except Exception as e:
        logger.exception("[RelFact] attach failed: %s", e)
        out["error"] = str(e)

    return out


def _limit_from_conditions(conditions: List[Dict[str, Any]]) -> str:
    temps = [c for c in conditions if c.get("type") == "temperature"]
    if not temps:
        return ""
    vals = [f"{c.get('value', '')}{c.get('unit', '')}" for c in temps]
    return " / ".join(vals)[:120]


def save_rel_facts_artifact(category: str, doc_id: str, payload: Dict[str, Any]) -> Optional[str]:
    """写入 MinIO parse/rel_facts.json。"""
    try:
        if not Config.MINIO_CLIENT:
            return None
        bucket = Config.MINIO_BUCKET_NAME
        key = f"{category}/{doc_id}/parse/rel_facts.json"
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        from io import BytesIO
        Config.MINIO_CLIENT.put_object(bucket, key, BytesIO(body), length=len(body), content_type="application/json")
        return key
    except Exception as e:
        logger.warning("[RelFact] save artifact failed: %s", e)
        return None
