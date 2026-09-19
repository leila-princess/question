"""ZNWD6 实体搜索：供引导式问答下拉选择。"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from blueprints.GLCX.ZNWD4.keyword_probe import _LABEL_ZH

from .probe.tau_builder import _probe_span

logger = logging.getLogger(__name__)

_LABEL_ZH_EXT = {
    **_LABEL_ZH,
    "File": "标准文档",
    "TestProject": "试验项目",
    "TestMethod": "试验方法",
    "Indicator": "考核指标",
}

_VALID_TYPES = frozenset(
    {"EquipmentType", "Equipment", "Environment", "TestProject", "Indicator", "TestMethod", "File"}
)


def _item(entity_id: str, name: str, node_type: str) -> Dict[str, Any]:
    return {
        "id": entity_id,
        "name": name,
        "nodeType": node_type,
        "labelZh": _LABEL_ZH_EXT.get(node_type, node_type),
    }


def search_entities(
    query: str,
    *,
    node_type: str = "",
    driver: Any = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    q = re.sub(r"\s+", "", (query or "").strip())
    nt = (node_type or "").strip()
    if nt and nt not in _VALID_TYPES:
        return []

    if not q or len(q) < 1:
        if nt:
            return enum_entities(node_type=nt, driver=driver, limit=limit)
        return []

    hits = _probe_span(q, question=q, driver=driver)
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for h in hits:
        hnt = str(h.get("node_type") or "")
        if nt and hnt != nt:
            continue
        if hnt not in _VALID_TYPES:
            continue
        eid = str(h.get("entity_id") or h.get("name") or "")
        name = str(h.get("name") or "")
        if not name:
            continue
        key = (hnt, eid, name)
        if key in seen:
            continue
        seen.add(key)
        out.append(_item(eid or name, name, hnt))
        if len(out) >= limit:
            break
    return out


def enum_entities(
    *,
    node_type: str,
    driver: Any = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    nt = (node_type or "").strip()
    if nt not in _VALID_TYPES or driver is None:
        return []

    from utils.neo4j_flgl_status import flgl_status_cypher_param

    params = flgl_status_cypher_param()
    lim = max(1, min(int(limit or 200), 500))
    out: List[Dict[str, Any]] = []

    queries: Dict[str, str] = {
        "EquipmentType": (
            "MATCH (n:EquipmentType) WHERE n.Status IN $flgl_enabled_status "
            "AND coalesce(n.Type, '') <> '' "
            "RETURN DISTINCT trim(toString(n.Type)) AS id, trim(toString(n.Type)) AS name "
            "ORDER BY name LIMIT $lim"
        ),
        "Equipment": (
            "MATCH (n:Equipment) WHERE n.Status IN $flgl_enabled_status "
            "RETURN coalesce(n.Id, n.id) AS id, coalesce(n.Name, n.name) AS name "
            "ORDER BY name LIMIT $lim"
        ),
        "Environment": (
            "MATCH (n:Environment) WHERE n.Status IN $flgl_enabled_status "
            "RETURN coalesce(n.Id, n.id) AS id, coalesce(n.Name, n.name) AS name "
            "ORDER BY name LIMIT $lim"
        ),
        "TestProject": (
            "MATCH (n:TestProject) WHERE n.Status IN $flgl_enabled_status "
            "RETURN coalesce(n.Id, n.id) AS id, coalesce(n.Name, n.name) AS name "
            "ORDER BY name LIMIT $lim"
        ),
        "Indicator": (
            "MATCH (n:Indicator) WHERE n.Status IN $flgl_enabled_status "
            "RETURN coalesce(n.Id, n.id) AS id, coalesce(n.Name, n.name) AS name "
            "ORDER BY name LIMIT $lim"
        ),
        "TestMethod": (
            "MATCH (n:TestMethod) WHERE n.Status IN $flgl_enabled_status "
            "RETURN coalesce(n.Id, n.id) AS id, coalesce(n.Name, n.name) AS name "
            "ORDER BY name LIMIT $lim"
        ),
    }

    cypher = queries.get(nt)
    if not cypher:
        return []

    try:
        with driver.session() as sess:
            for r in sess.run(cypher, {**params, "lim": lim}):
                eid = str(r.get("id") or "").strip()
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                out.append(_item(eid or name, name, nt))
    except Exception as exc:
        logger.warning("[ZNWD6][entity_search] enum %s failed: %s", nt, exc)
    return out


def load_guided_schema() -> Dict[str, Any]:
    from .ontology_loader import load_guided_intents

    guided = load_guided_intents()
    from .ontology_loader import load_ontology

    ont = load_ontology()
    labels_zh = guided.get("node_labels_zh") or {}
    from .guided_flow_builder import build_guided_flow_response

    guided_flow = build_guided_flow_response(
        guided,
        ont.get("node_labels") or [],
        labels_zh,
    )
    return {
        "nodeTypes": [
            {
                "type": t,
                "labelZh": guided.get("node_labels_zh", {}).get(t, t),
            }
            for t in (ont.get("node_labels") or [])
        ],
        "intents": guided.get("intents") or [],
        "composites": guided.get("composites") or [],
        "quickExamples": guided.get("quick_examples") or [],
        "slotKeys": ont.get("slot_keys") or {},
        "guidedFlow": guided_flow,
    }
