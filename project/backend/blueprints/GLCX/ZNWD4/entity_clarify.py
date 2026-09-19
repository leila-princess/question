"""Entity-level disambiguation prompts (same-name nodes under different parents)."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .entity_disambiguation import _equipment_type_label, entity_clarify_enabled

ENTITY_CLARIFY_EVENT = "entity_clarify"


def build_entity_clarify_payload(
    *,
    question: str,
    slot_key: str,
    child_name: str,
    candidates: List[Dict[str, Any]],
    scenario: str = "",
) -> Dict[str, Any]:
    options: List[Dict[str, Any]] = []
    for idx, c in enumerate(candidates):
        if not isinstance(c, dict):
            continue
        nid = str(c.get("id") or "").strip()
        if not nid:
            continue
        label = str(c.get("label") or "").strip() or _equipment_type_label(c)
        options.append(
            {
                "id": f"entity_{idx + 1}",
                "nodeId": nid,
                "label": label,
                "parentType": str(c.get("type_value") or "").strip(),
                "name": str(c.get("name") or "").strip(),
            }
        )

    prompt = f"图谱中有多个「{child_name}」装备类型，请选择您要问的是哪一个："
    return {
        "clarifyEventId": str(uuid.uuid4()),
        "slotKey": slot_key,
        "scenario": scenario,
        "prompt": prompt,
        "childName": child_name,
        "options": options,
        "question": question,
    }


def needs_entity_clarify(rec: Dict[str, Any], plan: Dict[str, Any]) -> bool:
    if not entity_clarify_enabled():
        return False
    scenario = str(rec.get("scenario") or plan.get("scenario") or "").strip()
    if scenario != "under_type":
        return False
    snap = plan.get("probe_snapshot") if isinstance(plan.get("probe_snapshot"), dict) else {}
    dis = snap.get("entity_disambiguation") if isinstance(snap.get("entity_disambiguation"), dict) else {}
    if not dis.get("ambiguous"):
        return False
    cands = dis.get("candidates") if isinstance(dis.get("candidates"), list) else []
    return len(cands) >= 2


def entity_clarify_from_plan(
    plan: Dict[str, Any],
    rec: Dict[str, Any],
    *,
    question: str,
) -> Optional[Dict[str, Any]]:
    if not needs_entity_clarify(rec, plan):
        return None
    snap = plan.get("probe_snapshot") if isinstance(plan.get("probe_snapshot"), dict) else {}
    dis = snap.get("entity_disambiguation") if isinstance(snap.get("entity_disambiguation"), dict) else {}
    cands = dis.get("candidates") if isinstance(dis.get("candidates"), list) else []
    params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
    child = ""
    et = params.get("equipment_type_keywords")
    if isinstance(et, list) and et:
        child = str(et[0]).strip()
    if not child:
        child = str(params.get("focus") or "").strip()
    return build_entity_clarify_payload(
        question=question,
        slot_key="equipment_type_keywords",
        child_name=child or "装备类型",
        candidates=cands,
        scenario=str(rec.get("scenario") or ""),
    )


def apply_entity_selection(
    plan: Dict[str, Any],
    *,
    node_id: str,
    parent_type: str = "",
    child_name: str = "",
) -> Dict[str, Any]:
    """Patch plan params after user selects a disambiguated entity."""
    out = dict(plan)
    draft = dict(out.get("params_draft") or {}) if isinstance(out.get("params_draft"), dict) else {}
    if child_name:
        draft["equipment_type_keywords"] = [child_name]
        draft["focus"] = child_name
    if parent_type:
        draft["equipment_type_parent"] = parent_type
    if node_id:
        draft["focus_id"] = node_id
    out["params_draft"] = draft
    snap = dict(out.get("probe_snapshot") or {}) if isinstance(out.get("probe_snapshot"), dict) else {}
    best = dict(snap.get("best") or {}) if isinstance(snap.get("best"), dict) else {}
    if node_id:
        best["id"] = node_id
    if child_name:
        best["name"] = child_name
    if parent_type:
        best["type_value"] = parent_type
    best["node_type"] = "EquipmentType"
    snap["best"] = best
    snap.pop("entity_disambiguation", None)
    out["probe_snapshot"] = snap
    out["match_confidence"] = "high"
    return out


__all__ = [
    "ENTITY_CLARIFY_EVENT",
    "apply_entity_selection",
    "build_entity_clarify_payload",
    "entity_clarify_from_plan",
    "needs_entity_clarify",
]
