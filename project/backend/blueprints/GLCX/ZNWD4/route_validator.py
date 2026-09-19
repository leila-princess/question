"""ZNWD4 路由校验：强制 probe、route 对齐、组装 RoutePlanV2。"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from .keyword_probe import extract_focus_candidates, probe_keyword
from .pcsr_assembly import build_evidence_for_question, pcsr_slots, slot_key_to_label
from .question_route_lock import (
    RouteLock,
    apply_route_lock_params,
    locked_slot_values,
    resolve_route_lock,
    _looks_like_level2_equipment_types,
    _looks_like_project_methods,
    _looks_like_project_standards,
    _looks_like_project_environment,
)
from .rel_inference import looks_like_equipment_belongs_to_type, looks_like_type_equipment_relation
from .route_plan_v2 import merge_slots_from_keywords, normalize_to_legacy_plan
from .routing_catalog import PURPOSE_CHAT, PURPOSE_ENUM, RouteEntry, get_route, filter_routes_by_purpose

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def _question_anchors(question: str, step1: Dict[str, Any], step2: Dict[str, Any]) -> List[str]:
    seen: set = set()
    out: List[str] = []

    def add(v: Any) -> None:
        s = str(v or "").strip()
        if len(s) < 2 or s in seen:
            return
        seen.add(s)
        out.append(s)

    add(step2.get("focus"))
    add(step1.get("keyword"))
    for k in step1.get("keywords") or []:
        add(k)
    for k in step2.get("keywords") or []:
        add(k)
    for c in extract_focus_candidates(question):
        add(c)
    out.sort(key=lambda x: len(_norm(x)), reverse=True)
    return out


def _value_grounded(value: str, question: str, anchors: List[str]) -> bool:
    v = _norm(value)
    if len(v) < 2:
        return False
    q = _norm(question)
    if v in q:
        return True
    for a in anchors:
        na = _norm(a)
        if not na:
            continue
        if v == na or v in na or na in v:
            return True
    return False


def _pick_best_anchor(anchors: List[str], question: str) -> str:
    q = _norm(question)
    for a in anchors:
        if _norm(a) and _norm(a) in q:
            return a
    return anchors[0] if anchors else ""


def _resolve_equipment_scenario(
    *,
    route_lock: Optional[RouteLock],
    entry: Optional[RouteEntry],
    out: Dict[str, Any],
    question: str = "",
) -> str:
    sc = str(out.get("scenario") or "").strip()
    if sc:
        return sc
    if route_lock and route_lock.extra:
        sc = str(route_lock.extra.get("scenario") or "").strip()
        if sc:
            return sc
    if entry and isinstance(entry.internal, dict):
        sc = str(entry.internal.get("scenario") or "").strip()
        if sc:
            return sc
    if _looks_like_level2_equipment_types(question):
        return "level2_under_level1"
    if looks_like_type_equipment_relation(question):
        return "under_type"
    if looks_like_equipment_belongs_to_type(question):
        return "belongs_to"
    return ""


def _equipment_type_slot_value(
    *,
    scenario: str,
    type_val: str,
    name_val: str,
    focus: str,
) -> str:
    sc = (scenario or "").strip()
    if sc == "level2_under_level1":
        return type_val or name_val or focus
    if sc in ("under_type", "belongs_to"):
        return name_val or focus or type_val
    return name_val or focus or type_val


def _apply_probe_canonical(
    out: Dict[str, Any],
    *,
    entry: Optional[RouteEntry],
    probe_snapshot: Optional[Dict[str, Any]],
    route_lock: Optional[RouteLock] = None,
) -> Dict[str, Any]:
    """PCSR: probe 规范名优先写入槽位。"""
    best = (probe_snapshot or {}).get("best") if isinstance(probe_snapshot, dict) else None
    if not isinstance(best, dict) or not entry:
        return out
    nt = str(best.get("node_type") or "").strip()
    bname = str(best.get("name") or "").strip()
    if route_lock and route_lock.probe_labels:
        allowed = set(route_lock.probe_labels)
        if nt and nt not in allowed:
            return out
    if not bname:
        return out
    for slot in entry.required_slots or []:
        if not slot.endswith("_keywords"):
            continue
        if slot_key_to_label(slot) == nt:
            out[slot] = [bname]
            out["focus"] = bname
            if slot == "project_keywords":
                out["project_name"] = bname
            bid = str(best.get("id") or "").strip()
            if bid:
                out["focus_id"] = bid
            break
    return out


def _distinct_parent_types_for_child_name(
    probe_snapshot: Optional[Dict[str, Any]], child_name: str
) -> List[str]:
    if not child_name or not isinstance(probe_snapshot, dict):
        return []
    hits = probe_snapshot.get("hits") or []
    type_vals: set = set()
    child_lower = child_name.lower()
    for h in hits:
        if not isinstance(h, dict):
            continue
        if str(h.get("node_type") or "") != "EquipmentType":
            continue
        name = str(h.get("name") or "").strip()
        if child_lower not in name.lower():
            continue
        tv = str(h.get("type_value") or "").strip()
        if tv:
            type_vals.add(tv)
    return sorted(type_vals)


def _apply_broad_under_type_if_needed(
    out: Dict[str, Any],
    *,
    probe_snapshot: Optional[Dict[str, Any]],
    scenario: str,
    question: str,
) -> Dict[str, Any]:
    """无 parent 且同名 EquipmentType 跨多个一级类型时，汇总全部而非锁死单一 focus_id。"""
    if scenario != "under_type":
        return out
    from .hierarchy_focus import parse_equipment_type_hierarchy

    hf = parse_equipment_type_hierarchy(question)
    if hf and hf.parent_type:
        return out
    child = ""
    et_kw = out.get("equipment_type_keywords")
    if isinstance(et_kw, list) and et_kw:
        child = str(et_kw[0]).strip()
    if not child:
        child = str(out.get("focus") or "").strip()
    if not child:
        return out
    parents = _distinct_parent_types_for_child_name(probe_snapshot, child)
    if len(parents) >= 2:
        out["match_all_equipment_types"] = True
        out.pop("focus_id", None)
        out.pop("equipment_type_parent", None)
        out["equipment_type_keywords"] = [child]
        out["focus"] = child
        logger.info(
            "[ZNWD4][validator] broad under_type child=%r parents=%r -> match_all",
            child,
            parents,
        )
    return out


def sanitize_params_draft(
    *,
    question: str,
    step1: Dict[str, Any],
    step2: Dict[str, Any],
    params: Dict[str, Any],
    entry: Optional[RouteEntry],
    probe_snapshot: Optional[Dict[str, Any]] = None,
    route_lock: Optional[RouteLock] = None,
    slot_source: str = "",
) -> Dict[str, Any]:
    """
    纠正 Step2 LLM 幻觉槽位：仅保留与问句/Step1/focus 一致的 keywords；
    probe 命中 EquipmentType 时按 scenario 选择一级 Type 或二级 Name。
    """
    out = dict(params or {})
    if route_lock:
        probe_snapshot = _filter_probe_for_lock(probe_snapshot, route_lock)

    out = _apply_probe_canonical(
        out,
        entry=entry,
        probe_snapshot=probe_snapshot,
        route_lock=route_lock,
    )

    if route_lock:
        locked_vals = locked_slot_values(route_lock)
        if locked_vals and route_lock.slot_key and not out.get(route_lock.slot_key):
            out = apply_route_lock_params(out, route_lock)
            out[route_lock.slot_key] = locked_vals
            out["focus"] = locked_vals[0]
            if route_lock.slot_key == "project_keywords":
                out["project_name"] = locked_vals[0]

    anchors = _question_anchors(question, step1, step2)
    focus = str(out.get("focus") or step2.get("focus") or step1.get("keyword") or "").strip()
    if not focus and anchors:
        focus = _pick_best_anchor(anchors, question)
        out["focus"] = focus

    slots = list(entry.required_slots) if entry else []
    for slot in slots:
        if not slot.endswith("_keywords"):
            continue
        if route_lock and slot == route_lock.slot_key and locked_slot_values(route_lock):
            if out.get(slot):
                continue
            out[slot] = locked_slot_values(route_lock)
            continue
        raw = out.get(slot)
        if isinstance(raw, str):
            vals = [raw.strip()] if raw.strip() else []
        elif isinstance(raw, list):
            vals = [str(x).strip() for x in raw if str(x).strip()]
        else:
            vals = []
        grounded = [v for v in vals if _value_grounded(v, question, anchors)]
        if not grounded:
            anchor = focus or _pick_best_anchor(anchors, question)
            if anchor:
                out[slot] = [anchor]
                logger.info(
                    "[ZNWD4][validator] reset slot %s -> %r (dropped hallucinated %r)",
                    slot,
                    anchor,
                    vals,
                )
        elif len(grounded) != len(vals):
            out[slot] = grounded
            logger.info("[ZNWD4][validator] trimmed slot %s %r -> %r", slot, vals, grounded)

    scenario = _resolve_equipment_scenario(
        route_lock=route_lock,
        entry=entry,
        out=out,
        question=question,
    )
    if scenario == "under_type" or looks_like_type_equipment_relation(question):
        from .hierarchy_focus import apply_hierarchy_to_params

        user_correction = str(slot_source or "").strip() == "user_correction"
        has_user_parent = bool(str(out.get("equipment_type_parent") or "").strip())
        has_user_child = isinstance(out.get("equipment_type_keywords"), list) and any(
            str(x).strip() for x in out.get("equipment_type_keywords") or []
        )
        out = apply_hierarchy_to_params(
            out,
            question,
            overwrite=not (user_correction and (has_user_parent or has_user_child)),
        )
        if out.get("equipment_type_keywords"):
            child = out["equipment_type_keywords"][0] if isinstance(out["equipment_type_keywords"], list) else ""
            if child:
                out["focus"] = str(child).strip()

    best = (probe_snapshot or {}).get("best") if isinstance(probe_snapshot, dict) else None
    if route_lock and route_lock.probe_labels == ["EquipmentType"]:
        if isinstance(best, dict) and str(best.get("node_type") or "") != "EquipmentType":
            best = None
    if isinstance(best, dict) and str(best.get("node_type") or "") == "EquipmentType":
        user_correction = str(slot_source or "").strip() == "user_correction"
        existing_et = out.get("equipment_type_keywords")
        has_user_et = isinstance(existing_et, list) and any(str(x).strip() for x in existing_et)
        if user_correction and has_user_et:
            pass
        else:
            type_val = str(best.get("type_value") or "").strip()
            name_val = str(best.get("name") or "").strip()
            parent_slot = str(out.get("equipment_type_parent") or "").strip()
            if parent_slot and type_val and parent_slot != type_val:
                pass
            else:
                filter_val = _equipment_type_slot_value(
                    scenario=scenario,
                    type_val=type_val,
                    name_val=name_val,
                    focus=focus,
                )
                if filter_val:
                    out["equipment_type_keywords"] = [filter_val]
                    out["focus"] = name_val or focus or filter_val
                    if best.get("id"):
                        out["focus_id"] = str(best.get("id"))
                if type_val and not parent_slot:
                    out["equipment_type_parent"] = type_val

    from .slot_focus import expand_environment_keywords

    env_raw = out.get("environment_keywords")
    if isinstance(env_raw, list) and env_raw:
        if not route_lock or route_lock.slot_key == "environment_keywords":
            expanded: List[str] = []
            seen_env: set = set()
            for k in env_raw:
                for v in expand_environment_keywords(str(k)):
                    if v not in seen_env:
                        seen_env.add(v)
                        expanded.append(v)
            if expanded:
                out["environment_keywords"] = expanded
                out["environment_value_list"] = expanded

    out = _apply_broad_under_type_if_needed(
        out,
        probe_snapshot=probe_snapshot,
        scenario=scenario,
        question=question,
    )

    return out


def _filter_probe_for_lock(
    probe_snapshot: Optional[Dict[str, Any]], route_lock: RouteLock
) -> Optional[Dict[str, Any]]:
    if not isinstance(probe_snapshot, dict):
        return probe_snapshot
    best = probe_snapshot.get("best") if isinstance(probe_snapshot.get("best"), dict) else None
    if not best:
        return probe_snapshot
    nt = str(best.get("node_type") or "").strip()
    allowed = set(route_lock.probe_labels or [])
    if allowed and nt not in allowed:
        logger.info(
            "[ZNWD4][validator] discard probe node_type=%s (lock expects %s)",
            nt,
            allowed,
        )
        return {}
    return probe_snapshot


def ensure_probe_snapshot(
    *,
    driver: Any,
    keyword: str,
    label_hints: Optional[List[str]] = None,
    existing: Optional[Dict[str, Any]] = None,
    question: str = "",
) -> Tuple[Dict[str, Any], str]:
    """返回 (probe_snapshot, source) source=tool|auto|empty。"""
    if isinstance(existing, dict) and existing.get("best"):
        return existing, "tool"
    kw = (keyword or "").strip()
    if not kw or not driver:
        return existing or {}, "empty"
    from .keyword_refine import probe_keyword_refined, probe_refine_enabled
    from .keyword_probe import probe_keyword

    if probe_refine_enabled():
        snap = probe_keyword_refined(driver, kw, question=question, label_hints=label_hints or None)
    else:
        snap = probe_keyword(driver, kw, label_hints=label_hints or None, question=question)
    best = snap.get("best") if isinstance(snap, dict) else None
    if best:
        return {"best": best, "hits": snap.get("hits", [])[:5]}, "auto"
    return {}, "empty"


def validate_and_align_route(
    route_id: str,
    probe_snapshot: Dict[str, Any],
    purpose: str = "",
    *,
    question: str = "",
) -> Tuple[str, str]:
    """若 probe 与 route 的 keyword_nodes 不一致，或问句意图锁冲突，尝试换 route。"""
    lock = resolve_route_lock(question) if question else None
    if lock and route_id != lock.route_id:
        logger.info(
            "[ZNWD4][validator] route lock override %s -> %s (%s)",
            route_id,
            lock.route_id,
            lock.reason,
        )
        return lock.route_id, "high"

    if question:
        if _looks_like_level2_equipment_types(question) and route_id != "R-EQUIPMENT_TYPE-level2_under_level1":
            logger.info("[ZNWD4][validator] question pattern -> R-EQUIPMENT_TYPE-level2_under_level1")
            return "R-EQUIPMENT_TYPE-level2_under_level1", "high"
        if looks_like_type_equipment_relation(question) and route_id != "R-EQUIPMENT-under_type":
            logger.info("[ZNWD4][validator] question pattern -> R-EQUIPMENT-under_type")
            return "R-EQUIPMENT-under_type", "high"
        if looks_like_equipment_belongs_to_type(question) and route_id != "R-EQUIPMENT_TYPE-belongs_to":
            logger.info("[ZNWD4][validator] question pattern -> R-EQUIPMENT_TYPE-belongs_to")
            return "R-EQUIPMENT_TYPE-belongs_to", "high"
        if _looks_like_project_methods(question) and route_id != "R-TEST_METHOD-by_project":
            logger.info("[ZNWD4][validator] question pattern -> R-TEST_METHOD-by_project")
            return "R-TEST_METHOD-by_project", "high"
        from .rel_inference import looks_like_equipment_by_project, looks_like_equipment_by_indicator

        if looks_like_equipment_by_project(question) and route_id != "R-EQUIPMENT-by_project":
            logger.info("[ZNWD4][validator] question pattern -> R-EQUIPMENT-by_project")
            return "R-EQUIPMENT-by_project", "high"
        if looks_like_equipment_by_indicator(question) and route_id != "R-EQUIPMENT-by_indicator":
            logger.info("[ZNWD4][validator] question pattern -> R-EQUIPMENT-by_indicator")
            return "R-EQUIPMENT-by_indicator", "high"
        from .rel_inference import looks_like_projects_by_method

        if looks_like_projects_by_method(question) and route_id != "R-TEST_PROJECT-by_method":
            logger.info("[ZNWD4][validator] question pattern -> R-TEST_PROJECT-by_method")
            return "R-TEST_PROJECT-by_method", "high"
        if _looks_like_project_standards(question) and route_id != "R-STANDARD-for_projects":
            logger.info("[ZNWD4][validator] question pattern -> R-STANDARD-for_projects")
            return "R-STANDARD-for_projects", "high"
        if _looks_like_project_environment(question) and route_id != "R-ENVIRONMENT-for_project":
            logger.info("[ZNWD4][validator] question pattern -> R-ENVIRONMENT-for_project")
            return "R-ENVIRONMENT-for_project", "high"

    entry = get_route(route_id)
    if not entry:
        return route_id, "low"
    # 拼装路由需多跳 Playbook；probe 单点 best 不能据此换成原子 one-hop 路由。
    if entry.route_id.startswith("R-C-") or str(entry.purpose or "").startswith("composite_"):
        return route_id, "high"
    best = (probe_snapshot or {}).get("best") if isinstance(probe_snapshot, dict) else None
    if not best or not entry.keyword_nodes:
        return route_id, "high" if entry else "medium"
    nt = str((best or {}).get("node_type") or "").strip()
    if nt in entry.keyword_nodes:
        return route_id, "high"
    # 同 purpose 下找 node_type 匹配的 route
    candidates = filter_routes_by_purpose(purpose or entry.purpose)
    for c in candidates:
        if nt in c.keyword_nodes:
            logger.info("[ZNWD4][validator] realign route %s -> %s (probe node_type=%s)", route_id, c.route_id, nt)
            return c.route_id, "medium"
    return route_id, "low"


def assemble_route_plan_v2(
    *,
    question: str,
    step1: Dict[str, Any],
    step2: Dict[str, Any],
    probe_snapshot: Dict[str, Any],
    probe_source: str = "auto",
    driver: Any = None,
    history_snapshot: Optional[Dict[str, Any]] = None,
    slot_source: str = "",
) -> Dict[str, Any]:
    route_lock = resolve_route_lock(question)
    purpose = str(step1.get("purpose") or "").strip()
    route_id = str(step2.get("route_id") or "").strip()
    if route_lock:
        step1 = {**step1, "purpose": route_lock.purpose}
        route_id = route_lock.route_id
        step2 = {**step2, "route_id": route_lock.route_id}
        purpose = route_lock.purpose
    if purpose == PURPOSE_CHAT:
        route_id = route_id or "R-CHAT-direct"
    entry = get_route(route_id)
    conf = "high"
    params = merge_slots_from_keywords(step1, step2, route_id)
    params = sanitize_params_draft(
        question=question,
        step1=step1,
        step2=step2,
        params=params,
        entry=entry,
        probe_snapshot=probe_snapshot,
        route_lock=route_lock,
        slot_source=slot_source,
    )
    kw = str(
        (route_lock.focus if route_lock and route_lock.focus else "")
        or params.get("focus")
        or step2.get("focus")
        or step1.get("keyword")
        or question
    ).strip()[:80]

    if purpose != PURPOSE_CHAT and purpose != PURPOSE_ENUM and driver:
        lock_for_probe = route_lock
        evidence = build_evidence_for_question(
            question,
            driver=driver,
            lock=lock_for_probe,
        )
        if evidence.probe_ran and entry:
            pcsr_params = pcsr_slots(
                route_id=route_id,
                lock=lock_for_probe,
                evidence=evidence,
                raw_slots=params,
                preserve_filled=str(slot_source or "").strip() == "user_correction",
            )
            if pcsr_params.get("focus") or any(
                pcsr_params.get(s) for s in (entry.required_slots or []) if s.endswith("_keywords")
            ):
                params = pcsr_params
                from .pcsr_assembly import evidence_to_probe_snapshot

                label = ""
                if entry.keyword_nodes:
                    label = entry.keyword_nodes[0]
                probe_snapshot = evidence_to_probe_snapshot(evidence, label) or probe_snapshot

        kw = str(
            params.get("focus")
            or (route_lock.focus if route_lock and route_lock.focus else "")
            or step2.get("focus")
            or step1.get("keyword")
            or question
        ).strip()[:80]

        hints = list(route_lock.probe_labels) if route_lock else list(entry.keyword_nodes) if entry else []
        probe_snapshot = _filter_probe_for_lock(probe_snapshot, route_lock) if route_lock else probe_snapshot
        if not (isinstance(probe_snapshot, dict) and probe_snapshot.get("best")):
            probe_snapshot, probe_source = ensure_probe_snapshot(
                driver=driver,
                keyword=kw,
                label_hints=hints or None,
                existing=probe_snapshot,
                question=question,
            )
        route_id, conf = validate_and_align_route(
            route_id, probe_snapshot, purpose=purpose, question=question
        )
        route_lock = resolve_route_lock(question)
        entry = get_route(route_id) or entry
        if entry:
            purpose = entry.purpose or purpose
        params = merge_slots_from_keywords(step1, step2, route_id)
        params = sanitize_params_draft(
            question=question,
            step1=step1,
            step2=step2,
            params=params,
            entry=entry,
            probe_snapshot=probe_snapshot,
            route_lock=route_lock,
            slot_source=slot_source,
        )
        best = probe_snapshot.get("best") if isinstance(probe_snapshot.get("best"), dict) else None
        if best and not route_lock:
            bid = str(best.get("id") or "").strip()
            if bid:
                params["focus_id"] = bid
            bname = str(best.get("name") or "").strip()
            type_val = str(best.get("type_value") or "").strip()
            scenario = _resolve_equipment_scenario(
                route_lock=route_lock,
                entry=entry,
                out=params,
                question=question,
            )
            if type_val or bname:
                params["focus"] = bname or params.get("focus") or type_val
                if str(best.get("node_type") or "") == "EquipmentType":
                    kw = _equipment_type_slot_value(
                        scenario=scenario,
                        type_val=type_val,
                        name_val=bname,
                        focus=str(params.get("focus") or kw),
                    ) or kw
                else:
                    kw = bname or kw
        elif best and route_lock:
            nt = str(best.get("node_type") or "").strip()
            allowed = set(route_lock.probe_labels or [])
            if nt in allowed:
                bid = str(best.get("id") or "").strip()
                if bid:
                    params["focus_id"] = bid
                bname = str(best.get("name") or "").strip()
                if bname:
                    params["focus"] = bname
                    kw = bname or kw
        elif route_lock and route_lock.focus:
            params["focus"] = route_lock.focus
            kw = route_lock.focus
    else:
        conf = "high"

    route_lock = resolve_route_lock(question)
    if route_lock:
        route_id = route_lock.route_id
        purpose = route_lock.purpose
        if locked_slot_values(route_lock):
            params = apply_route_lock_params(params, route_lock)
        entry = get_route(route_id) or entry
        kw = str(params.get("focus") or route_lock.focus or kw).strip()
        conf = "high"
        probe_snapshot = _filter_probe_for_lock(probe_snapshot, route_lock) or probe_snapshot

    strategy = entry.answer_strategy if entry else "db_synthesize"
    if purpose == PURPOSE_CHAT:
        strategy = "direct_llm"

    from .entity_disambiguation import disambiguate_probe_snapshot
    from .hierarchy_focus import parse_equipment_type_hierarchy

    hf = parse_equipment_type_hierarchy(question)
    parent_hint = str(params.get("equipment_type_parent") or "").strip() or (hf.parent_type if hf else "")
    child_kw = params.get("equipment_type_keywords")
    child_hint = ""
    if isinstance(child_kw, list) and child_kw:
        child_hint = str(child_kw[0]).strip()
    elif hf and hf.child_name:
        child_hint = hf.child_name
    if isinstance(probe_snapshot, dict) and probe_snapshot.get("hits"):
        probe_snapshot = disambiguate_probe_snapshot(
            probe_snapshot,
            question=question,
            parent_hint=parent_hint,
            child_hint=child_hint,
        )
        best_dis = probe_snapshot.get("best") if isinstance(probe_snapshot.get("best"), dict) else None
        if best_dis and str(best_dis.get("node_type") or "") == "EquipmentType":
            type_val = str(best_dis.get("type_value") or "").strip()
            name_val = str(best_dis.get("name") or "").strip()
            if name_val:
                params["equipment_type_keywords"] = [name_val]
                params["focus"] = name_val
            if type_val:
                params["equipment_type_parent"] = type_val
            bid = str(best_dis.get("id") or "").strip()
            if bid:
                params["focus_id"] = bid

    scenario = str(params.get("scenario") or "").strip()
    if not scenario and entry:
        scenario = _resolve_equipment_scenario(
            route_lock=route_lock,
            entry=entry,
            out=params,
            question=question,
        )
    params = _apply_broad_under_type_if_needed(
        params,
        probe_snapshot=probe_snapshot,
        scenario=scenario,
        question=question,
    )

    v2: Dict[str, Any] = {
        "step": 3,
        "route_id": route_id,
        "purpose": purpose,
        "keyword": kw,
        "keyword_nodes": list(entry.keyword_nodes) if entry else [],
        "params_draft": params,
        "probe_snapshot": probe_snapshot,
        "probe_source": probe_source,
        "match_confidence": conf,
        "answer_strategy": strategy,
        "retrieval_question": question,
        "use_history": bool(step1.get("use_history")),
    }
    if route_lock and route_lock.extra.get("scenario"):
        v2["scenario"] = str(route_lock.extra["scenario"])
        if isinstance(params, dict):
            params["scenario"] = v2["scenario"]
            v2["params_draft"] = params
    if history_snapshot:
        v2["history_snapshot"] = history_snapshot
    return v2


def assemble_legacy_plan(
    *,
    question: str,
    step1: Dict[str, Any],
    step2: Dict[str, Any],
    probe_snapshot: Dict[str, Any],
    probe_source: str = "auto",
    driver: Any = None,
    history_snapshot: Optional[Dict[str, Any]] = None,
    slot_source: str = "",
) -> Dict[str, Any]:
    v2 = assemble_route_plan_v2(
        question=question,
        step1=step1,
        step2=step2,
        probe_snapshot=probe_snapshot,
        probe_source=probe_source,
        driver=driver,
        history_snapshot=history_snapshot,
        slot_source=slot_source,
    )
    return normalize_to_legacy_plan(v2, question=question, retrieval_question=question)
