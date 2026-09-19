"""路径规划器：宏优先 + 带代价 Dijkstra 线性路径。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .ontology import find_best_linear_path, get_schema_graph
from .schema import DEFAULT_RETURN_ATTRIBUTES, DEFAULT_START_BY_INTENT, DEFAULT_TARGETS_BY_INTENT
from .types import ActionStep, ExecutionPlan, IntentJson, MacroPlan


def _has_environment(intent_json: IntentJson) -> bool:
    env = intent_json.get("environment_constraint")
    return bool(env and isinstance(env, dict) and any(str(v).strip() for v in env.values()))


def _method_route(intent_json: IntentJson) -> str:
    additional = intent_json.get("additional_filters") or {}
    route = str(additional.get("route") or "via_project").strip().lower()
    if route in ("via_indicator", "indicator"):
        return "via_indicator"
    return "via_project"


def _match_macro(intent_json: IntentJson) -> Optional[MacroPlan]:
    intent_type = str(intent_json.get("intent_type") or "")
    start_filters = intent_json.get("start_filters") or {}
    env = intent_json.get("environment_constraint")
    additional = intent_json.get("additional_filters")
    has_env = _has_environment(intent_json)

    if intent_type == "列举":
        node_type = (intent_json.get("target_entities") or ["EquipmentType"])[0]
        return {
            "mode": "macro",
            "macro": "列举",
            "params": {"node_type": node_type},
        }

    if intent_type == "查关联":
        starts = intent_json.get("start_entities") or []
        targets = intent_json.get("target_entities") or []
        if starts == ["EquipmentType"] and targets == ["Equipment"]:
            return {
                "mode": "macro",
                "macro": "查关联_类型下设备",
                "params": {"equipment_type_filter": start_filters or None},
            }

    if intent_type == "查指标":
        # 指标只依赖设备；环境槽位已在 validate_intent 中清除
        return {
            "mode": "macro",
            "macro": "查指标",
            "params": {"equipment_filter": start_filters},
        }

    if intent_type == "查项目":
        if has_env:
            return {
                "mode": "macro",
                "macro": "查项目_有环境",
                "params": {
                    "equipment_filter": start_filters,
                    "environment_filter": env,
                },
            }
        return {
            "mode": "macro",
            "macro": "查项目",
            "params": {"equipment_filter": start_filters},
        }

    if intent_type == "查方法":
        route = _method_route(intent_json)
        if route == "via_indicator":
            return {
                "mode": "macro",
                "macro": "查方法_经指标",
                "params": {"equipment_filter": start_filters},
            }
        params: Dict[str, Any] = {"equipment_filter": start_filters}
        if additional and additional.get("Name"):
            params["project_filter"] = {"Name": str(additional.get("Name"))}
        elif isinstance(additional, dict):
            pf = {k: v for k, v in additional.items() if k != "route" and str(v).strip()}
            if pf:
                params["project_filter"] = pf
        if has_env:
            params["environment_filter"] = env
            return {"mode": "macro", "macro": "查方法_经项目_有环境", "params": params}
        return {"mode": "macro", "macro": "查方法_经项目", "params": params}

    if intent_type == "查文件":
        params: Dict[str, Any] = {}
        if start_filters:
            params["method_filter"] = start_filters
        if additional:
            params["equipment_filter"] = additional
        return {"mode": "macro", "macro": "查文件", "params": params}

    return None


def _pick_best_path(intent_json: IntentJson) -> Optional[Tuple[List[str], float]]:
    starts = intent_json.get("start_entities") or DEFAULT_START_BY_INTENT.get(
        str(intent_json.get("intent_type") or ""), ["Equipment"]
    )
    targets = intent_json.get("target_entities") or DEFAULT_TARGETS_BY_INTENT.get(
        str(intent_json.get("intent_type") or ""), ["Indicator"]
    )
    best: Optional[Tuple[List[str], float]] = None
    for start in starts:
        for target in targets:
            found = find_best_linear_path(intent_json, start, target)
            if found and (best is None or found[1] < best[1]):
                best = found
    return best


def _path_to_actions(path: List[str], intent_json: IntentJson) -> List[ActionStep]:
    """将节点序列转为动作列表。"""
    if len(path) < 1:
        return []

    g = get_schema_graph()
    steps: List[ActionStep] = []
    start_label = path[0]
    start_filters = dict(intent_json.get("start_filters") or {})
    steps.append(
        {
            "action_name": "LocateNode",
            "node_type": start_label,
            "filters": start_filters,
            "node_set": "s0",
        }
    )

    current_set = "s0"
    for i in range(len(path) - 1):
        src, dst = path[i], path[i + 1]
        rel = g[src][dst].get("rel", "CON")
        next_set = f"s{i + 1}"
        steps.append(
            {
                "action_name": "Expand",
                "node_set": current_set,
                "relation": str(rel),
                "direction": "out",
                "node_type": dst,
                "node_set_out": next_set,
            }
        )
        current_set = next_set

    target = path[-1]
    attrs = DEFAULT_RETURN_ATTRIBUTES.get(target, ["Id", "Name", "Label"])
    steps.append(
        {
            "action_name": "ReturnAttributes",
            "node_set": current_set,
            "attributes": attrs,
            "node_type": target,
        }
    )
    return steps


def plan(intent_json: IntentJson) -> ExecutionPlan:
    """
    根据意图 JSON 生成执行计划。

    优先匹配动作宏；否则用 Dijkstra 求线性路径并转为动作序列。
    """
    macro = _match_macro(intent_json)
    if macro:
        return macro

    best = _pick_best_path(intent_json)
    if not best:
        return {
            "mode": "actions",
            "steps": [
                {
                    "action_name": "LocateNode",
                    "node_type": "Equipment",
                    "filters": intent_json.get("start_filters") or {},
                    "node_set": "s0",
                },
                {
                    "action_name": "ReturnAttributes",
                    "node_set": "s0",
                    "attributes": ["Id", "Name"],
                    "node_type": "Equipment",
                },
            ],
        }

    path, _ = best
    return {"mode": "actions", "steps": _path_to_actions(path, intent_json)}
