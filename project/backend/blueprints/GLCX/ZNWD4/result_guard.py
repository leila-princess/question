"""Post-retrieval deterministic guard for under_type equipment queries."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .executor import execute_meta_steps

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 0.6


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip()).lower()


@dataclass
class ResultGuardOutcome:
    neo_rows: List[Dict[str, Any]]
    retried: bool = False
    filtered: bool = False
    milestone: str = ""
    action: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


def _node_props(row: Dict[str, Any], key: str) -> Dict[str, Any]:
    v = row.get(key)
    if isinstance(v, dict):
        props = v.get("properties") if isinstance(v.get("properties"), dict) else v
        return props if isinstance(props, dict) else {}
    return {}


def _node_labels(row: Dict[str, Any], key: str) -> List[str]:
    v = row.get(key)
    if isinstance(v, dict):
        labs = v.get("labels")
        if isinstance(labs, list):
            return [str(x) for x in labs]
    return []


def _equipment_type_name(row: Dict[str, Any]) -> str:
    for side in ("n", "m"):
        if "EquipmentType" in _node_labels(row, side):
            props = _node_props(row, side)
            return str(props.get("Name") or props.get("name") or "").strip()
    return ""


def anchor_from_rec(rec: Dict[str, Any]) -> str:
    params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
    focus = str(params.get("focus") or "").strip()
    if focus:
        return focus
    et = params.get("equipment_type_keywords")
    if isinstance(et, list) and et:
        return str(et[0]).strip()
    return ""


def row_matches_anchor(row: Dict[str, Any], anchor: str) -> bool:
    if not anchor:
        return True
    na = _norm(anchor)
    if not na:
        return True
    et_name = _norm(_equipment_type_name(row))
    if not et_name:
        return False
    return na in et_name or et_name in na


def parent_type_from_rec(rec: Dict[str, Any]) -> str:
    params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
    return str(params.get("equipment_type_parent") or params.get("parent_type") or "").strip()


def _equipment_type_parent(row: Dict[str, Any]) -> str:
    for side in ("n", "m"):
        if "EquipmentType" in _node_labels(row, side):
            props = _node_props(row, side)
            return str(props.get("Type") or props.get("type") or "").strip()
    return ""


def row_matches_hierarchy(row: Dict[str, Any], parent: str, child: str) -> bool:
    if not child and not parent:
        return True
    if child and not row_matches_anchor(row, child):
        return False
    if parent:
        pt = _norm(_equipment_type_parent(row))
        pp = _norm(parent)
        if not pt or not pp:
            return False
        return pt == pp or pp in pt or pt in pp
    return True


def compute_match_rate(neo_rows: List[Dict[str, Any]], anchor: str, parent: str = "") -> float:
    valid = [r for r in neo_rows if isinstance(r, dict) and not r.get("error")]
    if not valid or not anchor:
        return 1.0
    hits = sum(1 for r in valid if row_matches_hierarchy(r, parent, anchor))
    return hits / len(valid)


def filter_rows_by_anchor(
    neo_rows: List[Dict[str, Any]], anchor: str, parent: str = ""
) -> List[Dict[str, Any]]:
    if not anchor and not parent:
        return list(neo_rows or [])
    filtered = [
        r
        for r in (neo_rows or [])
        if isinstance(r, dict) and not r.get("error") and row_matches_hierarchy(r, parent, anchor)
    ]
    return filtered if filtered else list(neo_rows or [])


def should_guard_under_type(rec: Dict[str, Any], steps: List[Dict[str, Any]]) -> bool:
    scenario = str(rec.get("scenario") or "").strip()
    if scenario == "under_type":
        return True
    for s in steps or []:
        if str(s.get("op_code") or "") == "neo_equipment_under_type":
            return True
    return False


def _patch_under_type_steps(
    steps: List[Dict[str, Any]],
    *,
    focus_id: str = "",
    keyword: str = "",
    parent_type: str = "",
) -> List[Dict[str, Any]]:
    patched: List[Dict[str, Any]] = []
    for idx, s in enumerate(steps or []):
        if str(s.get("op_code") or "") != "neo_equipment_under_type":
            patched.append(s)
            continue
        params = dict(s.get("params") or {})
        if focus_id:
            params["focus_id"] = focus_id
        if keyword:
            params["keyword"] = keyword
        if parent_type:
            params["parent_type"] = parent_type
            params["equipment_type_parent"] = parent_type
        step_id = str(s.get("step_id") or f"s{idx + 1}")
        patched.append({**s, "step_id": step_id, "params": params})
    return patched


def _step_used_focus_id(steps: List[Dict[str, Any]]) -> bool:
    for s in steps or []:
        if str(s.get("op_code") or "") == "neo_equipment_under_type":
            fid = str((s.get("params") or {}).get("focus_id") or "").strip()
            if fid:
                return True
    return False


def _resolve_focus_id(rec: Dict[str, Any]) -> str:
    params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
    focus_id = str(params.get("focus_id") or "").strip()
    if focus_id:
        return focus_id
    probe_snap = rec.get("probe_snapshot") if isinstance(rec.get("probe_snapshot"), dict) else {}
    probe = probe_snap.get("best") if isinstance(probe_snap.get("best"), dict) else None
    if isinstance(probe, dict) and str(probe.get("node_type") or "") == "EquipmentType":
        return str(probe.get("id") or probe.get("Id") or "").strip()
    return ""


def apply_result_guard(
    *,
    question: str,
    rec: Dict[str, Any],
    steps: List[Dict[str, Any]],
    neo_rows: List[Dict[str, Any]],
    driver: Any,
    chroma_collection: Any,
    client: Any = None,
    model: str = "",
    llm_limits: Optional[Dict[str, Any]] = None,
) -> ResultGuardOutcome:
    if not should_guard_under_type(rec, steps):
        return ResultGuardOutcome(neo_rows=neo_rows)

    params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
    if params.get("match_all_equipment_types") or params.get("match_all_by_name"):
        return ResultGuardOutcome(neo_rows=neo_rows)

    anchor = anchor_from_rec(rec)
    parent = parent_type_from_rec(rec)
    valid_rows = [r for r in neo_rows if isinstance(r, dict) and not r.get("error")]
    if not valid_rows:
        return ResultGuardOutcome(neo_rows=neo_rows)

    rate = compute_match_rate(valid_rows, anchor, parent)
    if rate >= MATCH_THRESHOLD:
        return ResultGuardOutcome(
            neo_rows=neo_rows,
            meta={"match_rate": rate, "anchor": anchor, "parent_type": parent},
        )

    logger.info(
        "[ZNWD4][result_guard] low match rate=%.2f anchor=%r rows=%d",
        rate,
        anchor,
        len(valid_rows),
    )

    focus_id = _resolve_focus_id(rec)
    milestone = "检索结果与问法不完全一致，正在按装备类型重新查找…"

    if driver and focus_id and not _step_used_focus_id(steps):
        retry_steps = _patch_under_type_steps(
            steps, focus_id=focus_id, keyword=anchor, parent_type=parent
        )
        new_neo, _, _ = execute_meta_steps(driver, chroma_collection, retry_steps)
        new_valid = [r for r in new_neo if isinstance(r, dict) and not r.get("error")]
        new_rate = compute_match_rate(new_valid, anchor, parent)
        if new_valid and new_rate >= MATCH_THRESHOLD:
            return ResultGuardOutcome(
                neo_rows=new_neo,
                retried=True,
                milestone=milestone,
                action="retry_focus_id",
                meta={"match_rate": new_rate, "anchor": anchor, "before_rate": rate},
            )

    if driver and anchor:
        retry_steps = _patch_under_type_steps(
            steps, focus_id=focus_id, keyword=anchor, parent_type=parent
        )
        new_neo, _, _ = execute_meta_steps(driver, chroma_collection, retry_steps)
        new_valid = [r for r in new_neo if isinstance(r, dict) and not r.get("error")]
        new_rate = compute_match_rate(new_valid, anchor, parent)
        if new_valid and new_rate > rate:
            return ResultGuardOutcome(
                neo_rows=new_neo,
                retried=True,
                milestone=milestone,
                action="retry_keyword",
                meta={"match_rate": new_rate, "anchor": anchor, "before_rate": rate},
            )

    filtered = filter_rows_by_anchor(valid_rows, anchor, parent)
    if filtered and len(filtered) < len(valid_rows):
        return ResultGuardOutcome(
            neo_rows=filtered,
            filtered=True,
            action="filter_rows",
            meta={
                "match_rate": compute_match_rate(filtered, anchor, parent),
                "anchor": anchor,
                "parent_type": parent,
                "before_rate": rate,
            },
        )

    from .result_guard_llm import apply_llm_result_guard, llm_guard_enabled

    if llm_guard_enabled() and client and model:
        llm_out = apply_llm_result_guard(
            question=question,
            anchor=anchor,
            rec=rec,
            neo_rows=valid_rows,
            steps=steps,
            driver=driver,
            chroma_collection=chroma_collection,
            client=client,
            model=model,
            llm_limits=llm_limits,
        )
        if llm_out is not None:
            return llm_out

    return ResultGuardOutcome(
        neo_rows=neo_rows,
        meta={"match_rate": rate, "anchor": anchor, "guard_failed": True},
    )


__all__ = [
    "ResultGuardOutcome",
    "anchor_from_rec",
    "apply_result_guard",
    "compute_match_rate",
    "filter_rows_by_anchor",
    "parent_type_from_rec",
    "row_matches_anchor",
    "row_matches_hierarchy",
    "should_guard_under_type",
]
