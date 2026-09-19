"""从 intents/composites 目录动态构建 guided_flow，YAML 仅保留问候语与展示顺序提示。"""

from __future__ import annotations

from typing import Any, Dict, List, Set


def _infer_aspect_kind(item: Dict[str, Any]) -> str:
    tid = str(item.get("template_id") or "")
    slots = item.get("slots") or []
    if not slots:
        if tid.startswith("enum_"):
            return "enum"
        if tid in ("document_lookup", "standard_chapter"):
            return "document"
        return "enum"
    if tid == "environment_properties":
        return "properties"
    if tid.startswith("composite_"):
        return "composite"
    if tid in ("document_lookup", "standard_chapter"):
        return "document"
    return "relation"


def _anchor_node_type(item: Dict[str, Any], *, is_composite: bool) -> str:
    if not is_composite:
        gt = str(item.get("goal_type") or "").strip()
        if gt:
            return gt
    slots = item.get("slots") or []
    if slots:
        return str(slots[0].get("node_type") or "Equipment")
    return "Equipment"


def _aspect_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    iid = str(item.get("id") or "")
    label = str(item.get("label") or iid)
    return {
        "id": iid,
        "labelZh": label,
        "labelEn": label,
        "aspectKind": _infer_aspect_kind(item),
        "intentId": iid,
    }


def build_guided_flow_response(
    guided: Dict[str, Any],
    node_labels: List[str],
    labels_zh: Dict[str, str],
) -> Dict[str, Any]:
    raw_flow = guided.get("guided_flow") or {}
    greeting_zh = raw_flow.get("greeting_zh") or "您好！我可以帮您查清单、关联和文档知识。"
    greeting_en = raw_flow.get("greeting_en") or "Hi! I can help you explore inventory, relations, and documents."

    yaml_nodes: Dict[str, Dict[str, Any]] = {}
    for nt in raw_flow.get("node_types") or []:
        t = str(nt.get("type") or "")
        if t:
            yaml_nodes[t] = nt

    catalog: Dict[str, List[Dict[str, Any]]] = {t: [] for t in node_labels}
    seen: Dict[str, Set[str]] = {t: set() for t in node_labels}

    def add_aspect(node_type: str, aspect: Dict[str, Any]) -> None:
        if node_type not in catalog:
            catalog[node_type] = []
            seen[node_type] = set()
        aid = str(aspect.get("id") or aspect.get("intentId") or "")
        if aid in seen[node_type]:
            return
        seen[node_type].add(aid)
        catalog[node_type].append(aspect)

    for intent in guided.get("intents") or []:
        if isinstance(intent, dict):
            nt = _anchor_node_type(intent, is_composite=False)
            add_aspect(nt, _aspect_from_item(intent))

    for comp in guided.get("composites") or []:
        if isinstance(comp, dict):
            nt = _anchor_node_type(comp, is_composite=True)
            add_aspect(nt, _aspect_from_item(comp))

    for t, ynt in yaml_nodes.items():
        for a in ynt.get("aspects") or []:
            aspect = {
                "id": a.get("id"),
                "labelZh": a.get("label_zh") or a.get("id"),
                "labelEn": a.get("label_en") or a.get("label_zh") or a.get("id"),
                "aspectKind": a.get("aspect_kind") or "relation",
                "intentId": a.get("intent_id"),
            }
            iid = str(aspect.get("intentId") or "")
            if iid and iid not in seen.get(t, set()):
                add_aspect(t, aspect)

    ordered_types: List[str] = []
    for nt in raw_flow.get("node_types") or []:
        t = str(nt.get("type") or "")
        if t and t not in ordered_types:
            ordered_types.append(t)
    for t in node_labels:
        if t not in ordered_types:
            ordered_types.append(t)

    node_types_out: List[Dict[str, Any]] = []
    for t in ordered_types:
        ynt = yaml_nodes.get(t) or {}
        aspects = list(catalog.get(t) or [])
        yaml_order = [str(a.get("intent_id") or a.get("id") or "") for a in (ynt.get("aspects") or [])]
        if yaml_order:
            ordered_aspects: List[Dict[str, Any]] = []
            by_id = {str(a.get("intentId") or a.get("id")): a for a in aspects}
            for iid in yaml_order:
                if iid in by_id:
                    ordered_aspects.append(by_id.pop(iid))
            ordered_aspects.extend(by_id.values())
            aspects = ordered_aspects

        node_types_out.append(
            {
                "type": t,
                "labelZh": ynt.get("label_zh") or labels_zh.get(t) or t,
                "labelEn": ynt.get("label_en") or labels_zh.get(t) or t,
                "aspects": aspects,
            }
        )

    return {
        "greetingZh": greeting_zh,
        "greetingEn": greeting_en,
        "nodeTypes": node_types_out,
    }
