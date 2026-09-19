"""
UserBiz + scenario → business_id/params 或直连 meta_ops 步骤。
高置信 Playbook 由 plan_reconcile 优先采用。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .business_catalog import (
    BIZ_DOCUMENT,
    BIZ_ENUM_NODES,
    BIZ_METHOD_CONTENT,
    BIZ_NODE_PROP,
    BIZ_NODE_PROP_REV,
    BIZ_PROJECT_TO_METHOD,
    BIZ_REL_ONE_HOP,
    BIZ_REL_REVERSE_ONE_HOP,
)
from .business_expand import expand_business
from .user_biz_catalog import (
    COMPOSITE_REGISTRY,
    USER_BIZ_TITLES,
    get_scenario_spec,
)

logger = logging.getLogger(__name__)

StepBuildResult = Union[Tuple[str, Dict[str, Any]], List[Dict[str, Any]]]


def normalize_slots(params_draft: Dict[str, Any], *, keyword: str = "") -> Dict[str, Any]:
    """将路由 params_draft 槽位归一为 playbook 可用形态。"""
    draft = dict(params_draft or {})

    def _as_list(key: str) -> List[str]:
        v = draft.get(key)
        if v is None:
            return []
        if isinstance(v, str):
            s = v.strip()
            return [s] if s else []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    slots: Dict[str, Any] = {
        "environment_focus_id": str(draft.get("environment_focus_id") or draft.get("environment_id") or "").strip(),
        "equipment_keywords": _as_list("equipment_keywords"),
        "equipment_type_keywords": _as_list("equipment_type_keywords"),
        "environment_keywords": _as_list("environment_keywords"),
        "project_keywords": _as_list("project_keywords"),
        "indicator_keywords": _as_list("indicator_keywords"),
        "standard_keywords": _as_list("standard_keywords"),
        "focus": str(draft.get("focus") or keyword or "").strip(),
    }
    if not slots["focus"]:
        for k in (
            "equipment_keywords",
            "environment_keywords",
            "project_keywords",
            "indicator_keywords",
            "standard_keywords",
            "equipment_type_keywords",
        ):
            if slots[k]:
                slots["focus"] = slots[k][0]
                break
    # 多关键词 → value_list（OR 模糊）
    for src, dst in (
        ("equipment_keywords", "equipment_value_list"),
        ("environment_keywords", "environment_value_list"),
        ("project_keywords", "project_value_list"),
        ("indicator_keywords", "indicator_value_list"),
        ("standard_keywords", "standard_value_list"),
        ("equipment_type_keywords", "equipment_type_value_list"),
    ):
        if slots[src]:
            slots[dst] = list(slots[src])
    return slots


def _focus_or_first(slots: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        lst = slots.get(k)
        if isinstance(lst, list) and lst:
            return str(lst[0]).strip()
    return str(slots.get("focus") or "").strip()


def _rel_fuzzy(
    *,
    from_type: str,
    to_type: str,
    rel_type: str = "CON",
    where_side: str = "from",
    focus: str = "",
    value_list: Optional[List[str]] = None,
) -> Dict[str, Any]:
    p: Dict[str, Any] = {
        "from_type": from_type,
        "to_type": to_type,
        "rel_type": rel_type,
        "where_side": where_side,
        "match_mode": "fuzzy",
        "from_field": "Name",
        "focus": focus,
    }
    if value_list:
        p["match_mode"] = "fuzzy_any"
        p["value_list"] = value_list[:8]
        if not focus:
            p["focus"] = value_list[0]
    return p


# --- param builders: (slots, probe) -> (business_id, params) | meta_ops[] ---

def resolve_environment_focus_id(
    slots: Dict[str, Any],
    plan: Dict[str, Any],
    *,
    driver: Any = None,
) -> str:
    """从槽位、RoutePlan.probe_snapshot 或图谱探测解析环境节点 Id。"""
    eid = str(slots.get("environment_focus_id") or "").strip()
    if eid:
        return eid
    draft = plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {}
    eid = str(draft.get("environment_focus_id") or draft.get("environment_id") or "").strip()
    if eid:
        return eid
    snap = plan.get("probe_snapshot") if isinstance(plan.get("probe_snapshot"), dict) else {}
    for h in snap.get("hits") or []:
        if not isinstance(h, dict):
            continue
        if str(h.get("node_type") or "").strip() == "Environment":
            hid = str(h.get("id") or "").strip()
            if hid:
                return hid
    best = snap.get("best") if isinstance(snap.get("best"), dict) else {}
    if str(best.get("node_type") or "").strip() == "Environment":
        return str(best.get("id") or "").strip()
    env_kws = slots.get("environment_keywords") if isinstance(slots.get("environment_keywords"), list) else []
    if driver and env_kws:
        from .keyword_probe import probe_keyword

        pr = probe_keyword(driver, str(env_kws[0]), label_hints=["Environment"])
        b = pr.get("best") if isinstance(pr.get("best"), dict) else None
        if b:
            return str(b.get("id") or "").strip()
    return ""


def _env_filter_step(env_id: str, env_name: str = "") -> Dict[str, Any]:
    label = env_name or env_id
    return {
        "step_id": "s2",
        "op_code": "neo_property_filter",
        "params": {
            "target": "m",
            "field": "EnvironmentId",
            "value": env_id,
            "match_mode": "exact",
        },
        "purpose": f"按试验环境 Id「{label}」过滤试验项目（EnvironmentId）",
    }


def _expand_eq_tp_with_env_filter(
    slots: Dict[str, Any],
    probe: Optional[Dict[str, Any]],
    *,
    plan: Optional[Dict[str, Any]] = None,
    driver: Any = None,
    retrieval_question: str = "",
) -> List[Dict[str, Any]]:
    """装备→项目，再按 EnvironmentId 过滤（双约束）。"""
    built = _pb_rel_eq_tp_fwd(slots, probe)
    if not isinstance(built, tuple):
        return []
    bid, params = built
    steps = expand_business(bid, params, retrieval_question=retrieval_question)
    if not steps:
        return []
    for i, st in enumerate(steps):
        s = dict(st)
        s["step_id"] = f"s{i + 1}"
        steps[i] = s

    env_id = resolve_environment_focus_id(slots, plan or {}, driver=driver)
    if env_id:
        steps.append(_env_filter_step(env_id, _focus_or_first(slots, "environment_keywords")))
    else:
        logger.warning(
            "[ZNWD1][playbook] by_equip_and_env 未解析到环境 Id，仅按装备查项目；environment_keywords=%s",
            slots.get("environment_keywords"),
        )
    return steps


def _pb_rel_eq_tp_with_env_filter(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    """由 expand_playbook 带 plan/driver 展开；此处仅占位。"""
    return _pb_rel_eq_tp_fwd(slots, probe)


def _pb_rel_eq_tp_fwd(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    eq_list = slots.get("equipment_value_list") if isinstance(slots.get("equipment_value_list"), list) else []
    focus = _focus_or_first(slots, "equipment_keywords") or str((probe or {}).get("name") or "")
    params = _rel_fuzzy(
        from_type="Equipment",
        to_type="TestProject",
        focus=focus if len(eq_list) <= 1 else "",
        value_list=eq_list if eq_list else None,
    )
    if eq_list:
        params["value_list"] = eq_list
        params["match_mode"] = "fuzzy_any"
        if len(eq_list) > 1:
            params.pop("focus", None)
    return BIZ_REL_ONE_HOP, params


def _pb_rel_env_tp_fwd(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "environment_keywords") or str((probe or {}).get("name") or "")
    return BIZ_REL_ONE_HOP, _rel_fuzzy(
        from_type="Environment",
        to_type="TestProject",
        where_side="from",
        focus=focus,
        value_list=slots.get("environment_value_list"),
    )


def _pb_rel_eq_ind_fwd(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "equipment_keywords") or str((probe or {}).get("name") or "")
    return BIZ_REL_ONE_HOP, _rel_fuzzy(
        from_type="Equipment",
        to_type="Indicator",
        focus=focus,
        value_list=slots.get("equipment_value_list"),
    )


def _pb_rel_ind_tm_fwd(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "indicator_keywords") or str((probe or {}).get("name") or "")
    return BIZ_REL_ONE_HOP, _rel_fuzzy(
        from_type="Indicator",
        to_type="TestMethod",
        focus=focus,
        value_list=slots.get("indicator_value_list"),
    )


def _pb_rel_et_eq_has_fwd(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "equipment_type_keywords") or str((probe or {}).get("name") or "")
    return BIZ_REL_ONE_HOP, {
        "from_type": "EquipmentType",
        "to_type": "Equipment",
        "rel_type": "HAS",
        "where_side": "from",
        "match_mode": "fuzzy",
        "focus": focus,
        "value_list": slots.get("equipment_type_value_list"),
    }


def _pb_rel_et_eq_has_rev(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "equipment_keywords") or str((probe or {}).get("name") or "")
    out: Dict[str, Any] = {
        "from_type": "EquipmentType",
        "to_type": "Equipment",
        "rel_type": "HAS",
        "where_side": "to",
        "match_mode": "fuzzy",
        "focus": focus,
        "result_type": "EquipmentType",
        "anchor_type": "Equipment",
    }
    if probe and str(probe.get("node_type") or "") == "Equipment":
        pid = str(probe.get("id") or probe.get("Id") or "").strip()
        if pid:
            out["focus_id"] = pid
    return BIZ_REL_ONE_HOP, out


def _pb_rel_eq_tp_rev(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "project_keywords") or str((probe or {}).get("name") or "")
    return BIZ_REL_REVERSE_ONE_HOP, {
        "from_type": "Equipment",
        "to_type": "TestProject",
        "rel_type": "CON",
        "where_side": "to",
        "match_mode": "fuzzy",
        "focus": focus,
        "result_type": "Equipment",
        "anchor_type": "TestProject",
    }


def _pb_rel_eq_ind_rev(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "indicator_keywords") or str((probe or {}).get("name") or "")
    return BIZ_REL_REVERSE_ONE_HOP, {
        "from_type": "Equipment",
        "to_type": "Indicator",
        "rel_type": "CON",
        "where_side": "to",
        "match_mode": "fuzzy",
        "focus": focus,
        "result_type": "Equipment",
        "anchor_type": "Indicator",
    }


def _pb_rel_env_tp_rev(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "project_keywords") or str((probe or {}).get("name") or "")
    return BIZ_REL_ONE_HOP, {
        "from_type": "Environment",
        "to_type": "TestProject",
        "rel_type": "CON",
        "where_side": "to",
        "match_mode": "fuzzy",
        "focus": focus,
    }


def _pb_rel_tm_file_rev(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "standard_keywords") or str((probe or {}).get("name") or "")
    return BIZ_REL_ONE_HOP, {
        "from_type": "TestMethod",
        "to_type": "File",
        "rel_type": "BELONGS_TO",
        "where_side": "to",
        "match_mode": "fuzzy",
        "focus": focus,
        "value_list": slots.get("standard_value_list"),
    }


def _pb_project_to_method(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    name = _focus_or_first(slots, "project_keywords") or str((probe or {}).get("name") or "")
    return BIZ_PROJECT_TO_METHOD, {"project_name": name, "focus": name}


def _pb_method_content(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    return BIZ_METHOD_CONTENT, {
        "method_name": _focus_or_first(slots, "project_keywords", "indicator_keywords") or str((probe or {}).get("name") or ""),
        "project_name": _focus_or_first(slots, "project_keywords"),
        "focus": slots.get("focus") or "",
    }


def _pb_enum(slots: Dict[str, Any], node_type: str) -> StepBuildResult:
    return BIZ_ENUM_NODES, {"node_type": node_type}


def _pb_enum_testproject(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    return _pb_enum(slots, "TestProject")


def _pb_enum_indicator(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    return _pb_enum(slots, "Indicator")


def _pb_enum_environment(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    return _pb_enum(slots, "Environment")


def _pb_enum_equipment_type(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    return _pb_enum(slots, "EquipmentType")


def _pb_et_type_children(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    val = _focus_or_first(slots, "equipment_type_keywords")
    return BIZ_NODE_PROP_REV, {
        "node_type": "EquipmentType",
        "field": "Type",
        "value": val,
    }


def _pb_env_properties(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "environment_keywords") or str((probe or {}).get("name") or "")
    return BIZ_NODE_PROP, {
        "node_type": "Environment",
        "field": "Name",
        "value": focus,
        "property_fields": ["Type", "Level", "Combination", "Requirement", "Constraint", "Name"],
    }


def _pb_document_query(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    q = _focus_or_first(slots, "standard_keywords") or slots.get("focus") or ""
    return BIZ_DOCUMENT, {"query": q}


def _pb_rel_eq_tm_via_tp(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    """装备 → 项目 → 方法（两跳 meta_ops）。"""
    focus = _focus_or_first(slots, "equipment_keywords") or str((probe or {}).get("name") or "")
    p1 = _rel_fuzzy(from_type="Equipment", to_type="TestProject", focus=focus, value_list=slots.get("equipment_value_list"))
    p2 = {
        "from_type": "TestProject",
        "to_type": "TestMethod",
        "rel_type": "CON",
        "where_side": "from",
        "match_mode": "in",
        "from_field": "Id",
        "from_values_ref": {"step_id": "s1", "target": "n", "field": "Id"},
        "limit": 200,
    }
    return [
        {"step_id": "s1", "op_code": "neo_relation_query", "params": p1, "purpose": f"装备「{focus}」关联试验项目"},
        {"step_id": "s2", "op_code": "neo_relation_query", "params": p2, "purpose": "项目关联试验方法"},
    ]


def _pb_rel_eq_tm_via_ind(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "equipment_keywords") or str((probe or {}).get("name") or "")
    p1 = _rel_fuzzy(from_type="Equipment", to_type="Indicator", focus=focus, value_list=slots.get("equipment_value_list"))
    p2 = {
        "from_type": "Indicator",
        "to_type": "TestMethod",
        "rel_type": "CON",
        "where_side": "from",
        "match_mode": "in",
        "from_field": "Id",
        "from_values_ref": {"step_id": "s1", "target": "n", "field": "Id"},
        "limit": 200,
    }
    return [
        {"step_id": "s1", "op_code": "neo_relation_query", "params": p1, "purpose": f"装备「{focus}」关联考核指标"},
        {"step_id": "s2", "op_code": "neo_relation_query", "params": p2, "purpose": "指标关联试验方法"},
    ]


def _pb_rel_et_eq_ind_hop2(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "equipment_type_keywords") or str((probe or {}).get("name") or "")
    p1 = {
        "from_type": "EquipmentType",
        "to_type": "Equipment",
        "rel_type": "HAS",
        "where_side": "from",
        "match_mode": "fuzzy",
        "focus": focus,
        "value_list": slots.get("equipment_type_value_list"),
    }
    p2 = {
        "from_type": "Equipment",
        "to_type": "Indicator",
        "rel_type": "CON",
        "where_side": "from",
        "match_mode": "in",
        "from_field": "Id",
        "from_values_ref": {"step_id": "s1", "target": "n", "field": "Id"},
        "limit": 200,
    }
    return [
        {"step_id": "s1", "op_code": "neo_relation_query", "params": p1, "purpose": f"装备类型「{focus}」下装备"},
        {"step_id": "s2", "op_code": "neo_relation_query", "params": p2, "purpose": "装备关联考核指标"},
    ]


def _pb_rel_tp_tm_file_chain(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    focus = _focus_or_first(slots, "project_keywords") or str((probe or {}).get("name") or "")
    p1 = _rel_fuzzy(from_type="TestProject", to_type="TestMethod", where_side="from", focus=focus, value_list=slots.get("project_value_list"))
    p2 = {
        "from_type": "TestMethod",
        "to_type": "File",
        "rel_type": "BELONGS_TO",
        "where_side": "from",
        "match_mode": "in",
        "from_field": "Id",
        "from_values_ref": {"step_id": "s1", "target": "m", "field": "Id"},
        "limit": 200,
    }
    return [
        {"step_id": "s1", "op_code": "neo_relation_query", "params": p1, "purpose": f"项目「{focus}」关联试验方法"},
        {"step_id": "s2", "op_code": "neo_relation_query", "params": p2, "purpose": "方法关联标准文件"},
    ]


def _pb_rel_ind_tm_from_s1(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    return [
        {
            "step_id": "s2",
            "op_code": "neo_relation_query",
            "params": {
                "from_type": "Indicator",
                "to_type": "TestMethod",
                "rel_type": "CON",
                "where_side": "from",
                "match_mode": "in",
                "from_field": "Id",
                "from_values_ref": {"step_id": "s1", "target": "n", "field": "Id"},
                "limit": 200,
            },
            "purpose": "指标关联试验方法",
        }
    ]


def _pb_rel_tp_env_from_s1(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    """项目 → 环境（反向查 Environment-[:CON]->TestProject）。"""
    return [
        {
            "step_id": "s2",
            "op_code": "neo_relation_query",
            "params": {
                "from_type": "Environment",
                "to_type": "TestProject",
                "rel_type": "CON",
                "where_side": "to",
                "match_mode": "in",
                "from_field": "Id",
                "from_values_ref": {"step_id": "s1", "target": "m", "field": "Id"},
                "limit": 200,
            },
            "purpose": "试验项目关联的试验环境",
        }
    ]


def _pb_env_prop_from_s2(slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    """读取 s2 返回的环境节点属性（Type/Class/Combination/Limit/Request）。

    由于 s2 已返回 Environment 节点及其全部属性，本步不执行新查询，
    仅标记意图供合成 LLM 提取属性字段。
    """
    return [
        {
            "step_id": "s3",
            "op_code": "neo_node_query",
            "params": {
                "node_type": "Environment",
                "field": "Id",
                "value": "__FROM_S2__",
                "limit": 50,
                "property_fields": ["Type", "Class", "Combination", "Limit", "Request", "Name", "Description"],
            },
            "purpose": "读取环境节点属性（分类/分级/组合/约束/要求）",
            "skip_db": True,
        }
    ]


_PARAM_BUILDERS: Dict[str, Callable[..., StepBuildResult]] = {
    "rel_eq_tp_fwd": _pb_rel_eq_tp_fwd,
    "rel_eq_tp_with_env_filter": _pb_rel_eq_tp_with_env_filter,
    "rel_env_tp_fwd": _pb_rel_env_tp_fwd,
    "rel_eq_ind_fwd": _pb_rel_eq_ind_fwd,
    "rel_ind_tm_fwd": _pb_rel_ind_tm_fwd,
    "rel_et_eq_has_fwd": _pb_rel_et_eq_has_fwd,
    "rel_et_eq_has_rev": _pb_rel_et_eq_has_rev,
    "rel_eq_tp_rev": _pb_rel_eq_tp_rev,
    "rel_eq_ind_rev": _pb_rel_eq_ind_rev,
    "rel_env_tp_rev": _pb_rel_env_tp_rev,
    "rel_tm_file_rev": _pb_rel_tm_file_rev,
    "project_to_method": _pb_project_to_method,
    "method_content": _pb_method_content,
    "enum_testproject": _pb_enum_testproject,
    "enum_indicator": _pb_enum_indicator,
    "enum_environment": _pb_enum_environment,
    "enum_equipment_type": _pb_enum_equipment_type,
    "et_type_children": _pb_et_type_children,
    "env_properties": _pb_env_properties,
    "document_query": _pb_document_query,
    "rel_eq_tm_via_tp": _pb_rel_eq_tm_via_tp,
    "rel_eq_tm_via_ind": _pb_rel_eq_tm_via_ind,
    "rel_et_eq_ind_hop2": _pb_rel_et_eq_ind_hop2,
    "rel_tp_tm_file_chain": _pb_rel_tp_tm_file_chain,
    "rel_tp_env_from_s1": _pb_rel_tp_env_from_s1,
    "env_prop_from_s2": _pb_env_prop_from_s2,
}


def _build_step(spec_builder: str, slots: Dict[str, Any], probe: Optional[Dict[str, Any]]) -> StepBuildResult:
    fn = _PARAM_BUILDERS.get(spec_builder)
    if not fn:
        logger.warning("[ZNWD1][playbook] unknown builder %s", spec_builder)
        return "", {}
    return fn(slots, probe)


def _step2_tp_tm_ref() -> Dict[str, Any]:
    return {
        "step_id": "s2",
        "op_code": "neo_relation_query",
        "params": {
            "from_type": "TestProject",
            "to_type": "TestMethod",
            "rel_type": "CON",
            "where_side": "from",
            "match_mode": "in",
            "from_field": "Id",
            "from_values_ref": {"step_id": "s1", "target": "n", "field": "Id"},
            "limit": 200,
        },
        "purpose": "项目关联试验方法",
    }


def _expand_composite_playbook(
    composite_id: str,
    slots: Dict[str, Any],
    probe: Optional[Dict[str, Any]],
    *,
    question: str = "",
    plan: Optional[Dict[str, Any]] = None,
    driver: Any = None,
) -> List[List[Dict[str, Any]]]:
    from .user_biz_catalog import COMP_EQUIP_PROJECT_ENV_SETTINGS, COMP_INDICATOR_THEN_METHOD, COMP_PROJECT_ENV_METHOD, COMP_PROJECT_THEN_METHOD

    groups: List[List[Dict[str, Any]]] = []
    if composite_id == COMP_PROJECT_THEN_METHOD:
        b1 = _pb_rel_eq_tp_fwd(slots, probe)
        if isinstance(b1, tuple):
            bid, p = b1
            g1 = expand_business(bid, p, retrieval_question=question)
            if g1:
                groups.append(g1)
        groups.append([_step2_tp_tm_ref()])
    elif composite_id == COMP_PROJECT_ENV_METHOD:
        g1 = _expand_eq_tp_with_env_filter(
            slots, probe, plan=plan or {}, driver=driver, retrieval_question=question
        )
        if g1:
            groups.append(g1)
        groups.append([_step2_tp_tm_ref()])
    elif composite_id == COMP_INDICATOR_THEN_METHOD:
        b1 = _pb_rel_eq_ind_fwd(slots, probe)
        if isinstance(b1, tuple):
            bid, p = b1
            g1 = expand_business(bid, p, retrieval_question=question)
            if g1:
                groups.append(g1)
        b2 = _pb_rel_ind_tm_from_s1(slots, probe)
        if isinstance(b2, list):
            groups.append(b2)
    elif composite_id == COMP_EQUIP_PROJECT_ENV_SETTINGS:
        # 装备→项目→环境→环境属性
        b1 = _pb_rel_eq_tp_fwd(slots, probe)
        if isinstance(b1, tuple):
            bid, p = b1
            g1 = expand_business(bid, p, retrieval_question=question)
            if g1:
                groups.append(g1)
        # 若用户指定了项目名，在 s1 结果后按项目名称过滤
        proj_kw = _focus_or_first(slots, "project_keywords")
        if proj_kw:
            groups.append([
                {
                    "step_id": "s1b",
                    "op_code": "neo_property_filter",
                    "params": {
                        "target": "m",
                        "field": "Name",
                        "value": proj_kw,
                        "match_mode": "contains",
                    },
                    "purpose": f"按项目名称「{proj_kw}」过滤试验项目",
                }
            ])
        b2 = _pb_rel_tp_env_from_s1(slots, probe)
        if isinstance(b2, list):
            # 若有 s1b 过滤步，from_values_ref 需指向 s1b
            for step in b2:
                ref = step.get("params", {}).get("from_values_ref")
                if isinstance(ref, dict) and ref.get("step_id") == "s1":
                    # 检查是否有 s1b 过滤步
                    if any(s.get("step_id") == "s1b" for g in groups for s in g):
                        ref["step_id"] = "s1b"
            groups.append(b2)
        b3 = _pb_env_prop_from_s2(slots, probe)
        if isinstance(b3, list):
            groups.append(b3)
    return groups


def expand_playbook(
    plan: Dict[str, Any],
    *,
    question: str = "",
    probe_best: Optional[Dict[str, Any]] = None,
    driver: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    若 plan 含高置信 user_biz，展开为 reconcile 可消费的 rec 补丁。
    返回 None 表示不走 Playbook。
    """
    user_biz = str(plan.get("user_biz") or "").strip()
    scenario = str(plan.get("scenario") or "default").strip() or "default"
    conf = str(plan.get("match_confidence") or "").strip().lower()
    if not user_biz:
        return None
    if conf not in ("high", "medium"):
        return None

    spec = get_scenario_spec(user_biz, scenario)
    if not spec:
        logger.info("[ZNWD1][playbook] no spec for %s/%s", user_biz, scenario)
        return None

    draft = plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {}
    kw = str(plan.get("keyword") or "").strip()
    slots = normalize_slots(draft, keyword=kw)
    probe = probe_best if isinstance(probe_best, dict) else None

    business_steps: List[Tuple[str, Dict[str, Any]]] = []
    meta_groups: List[List[Dict[str, Any]]] = []

    if user_biz in COMPOSITE_REGISTRY:
        meta_groups.extend(
            _expand_composite_playbook(
                user_biz, slots, probe, question=question, plan=plan, driver=driver
            )
        )
    else:
        for step_spec in spec.steps:
            if step_spec.param_builder == "rel_eq_tp_with_env_filter":
                g = _expand_eq_tp_with_env_filter(
                    slots, probe, plan=plan, driver=driver, retrieval_question=question
                )
                if g:
                    meta_groups.append(g)
                continue
            built = _build_step(step_spec.param_builder, slots, probe)
            if isinstance(built, list):
                meta_groups.append(built)
                continue
            bid, params = built
            if bid:
                business_steps.append((bid, params))

    strategy = spec.answer_strategy
    title = USER_BIZ_TITLES.get(user_biz, user_biz)
    primary_bid = business_steps[0][0] if business_steps else ""
    params_patch: Dict[str, Any] = {}
    if not primary_bid and meta_groups:
        primary_bid = BIZ_REL_ONE_HOP
        built_fwd = _pb_rel_eq_tp_fwd(slots, probe)
        if isinstance(built_fwd, tuple):
            _, params_patch = built_fwd

    return {
        "playbook": True,
        "user_biz": user_biz,
        "scenario": scenario,
        "user_biz_title": title,
        "business_steps": business_steps,
        "meta_step_groups": meta_groups,
        "answer_strategy": strategy,
        "business_id": primary_bid or (user_biz if meta_groups else ""),
        "params_patch": params_patch,
        "multi_step": len(business_steps) + len(meta_groups) > 1 or user_biz in COMPOSITE_REGISTRY,
    }


def expand_playbook_meta_steps(
    rec: Dict[str, Any],
    *,
    retrieval_question: str = "",
) -> List[Dict[str, Any]]:
    """将 playbook reconcile 结果展开为可 execute_meta_steps 的列表。"""
    all_steps: List[Dict[str, Any]] = []
    sid = 0

    for bid, params in rec.get("business_steps") or []:
        steps = expand_business(bid, params, retrieval_question=retrieval_question)
        for st in steps:
            sid += 1
            s = dict(st)
            s["step_id"] = f"s{sid}"
            all_steps.append(s)

    for group in rec.get("meta_step_groups") or []:
        for st in group:
            if not isinstance(st, dict):
                continue
            sid += 1
            s = dict(st)
            ref = s.get("params", {}).get("from_values_ref") if isinstance(s.get("params"), dict) else None
            if isinstance(ref, dict) and ref.get("step_id") == "s1" and all_steps:
                ref = dict(ref)
                ref["step_id"] = all_steps[0].get("step_id", "s1")
                s["params"] = {**s["params"], "from_values_ref": ref}
            s["step_id"] = f"s{sid}"
            all_steps.append(s)

    return all_steps


def playbook_guardrail_question(user_biz: str, scenario: str, question: str) -> bool:
    """低置信或场景与问句明显冲突时返回 False。"""
    q = (question or "").strip()
    if not q:
        return False
    from .user_biz_catalog import UBIZ_STANDARD

    if user_biz == UBIZ_STANDARD and scenario == "for_projects":
        if re.search(r"试验方法|测试方法", q) and not re.search(r"标准|GB|条文|章节", q):
            return False
    return True
