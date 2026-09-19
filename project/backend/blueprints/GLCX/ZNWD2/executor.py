"""查询执行引擎：动作原语 + 宏调度。"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from utils.neo4j_flgl_status import flgl_neighbor_filter_params, flgl_status_cypher_param

from . import action_macros
from .config import default_query_limit
from .schema import DEFAULT_RETURN_ATTRIBUTES
from .types import ActionStep, ExecutionPlan, ExecutionResult, NodeSet

logger = logging.getLogger(__name__)


class QueryExecutor:
    """Neo4j 查询执行器。"""

    def __init__(self, driver: Any, *, status_filter: int = 1, limit: Optional[int] = None) -> None:
        self.driver = driver
        self.status_filter = status_filter
        self.limit = limit or default_query_limit()

    def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        """执行宏或动作序列。"""
        if plan.get("mode") == "macro":
            return self._execute_macro(plan)
        return self._execute_actions(plan)

    def _execute_macro(self, plan: ExecutionPlan) -> ExecutionResult:
        macro_name = str(plan.get("macro") or "")
        params = dict(plan.get("params") or {})
        fn = action_macros.get_macro_function(macro_name)
        if not fn:
            return ExecutionResult(
                rows=[],
                target_label="",
                plan_mode="macro",
                macro_name=macro_name,
                error=f"未知宏: {macro_name}",
            )
        try:
            rows = fn(self.driver, limit=self.limit, **params)
            label = rows[0].get("label", "") if rows else _macro_target_label(macro_name)
            return ExecutionResult(
                rows=rows,
                target_label=str(label),
                plan_mode="macro",
                macro_name=macro_name,
            )
        except Exception as e:
            logger.exception("[ZNWD2] macro %s failed", macro_name)
            return ExecutionResult(
                rows=[],
                target_label="",
                plan_mode="macro",
                macro_name=macro_name,
                error=str(e),
            )

    def _execute_actions(self, plan: ExecutionPlan) -> ExecutionResult:
        steps = list(plan.get("steps") or [])
        node_sets: Dict[str, NodeSet] = {}
        final_rows: List[Dict[str, Any]] = []
        target_label = "Unknown"
        idx = 0

        while idx < len(steps):
            step = steps[idx]
            name = str(step.get("action_name") or "")

            if (
                name == "LocateNode"
                and idx + 1 < len(steps)
                and steps[idx + 1].get("action_name") == "Expand"
            ):
                merged = _try_merge_locate_expand([steps[idx], steps[idx + 1]])
                if merged:
                    cypher, params, out_key, label = merged
                    rows, ids = self._run_node_query(cypher, params, label)
                    node_sets[out_key] = NodeSet(label=label, ids=ids, rows=rows)
                    idx += 2
                    continue

            if name == "LocateNode":
                ns = self._locate_node(step)
                node_sets[str(step.get("node_set") or "s0")] = ns
            elif name == "Expand":
                out_key = str(step.get("node_set_out") or "s1")
                base_key = str(step.get("node_set") or "s0")
                base = node_sets.get(base_key)
                if base:
                    node_sets[out_key] = self._expand(base, step)
            elif name == "Intersect":
                a = node_sets.get(str(step.get("node_set_a") or ""))
                b = node_sets.get(str(step.get("node_set_b") or ""))
                if a and b:
                    node_sets[str(step.get("node_set") or "si")] = _intersect(a, b)
            elif name == "Union":
                a = node_sets.get(str(step.get("node_set_a") or ""))
                b = node_sets.get(str(step.get("node_set_b") or ""))
                if a and b:
                    node_sets[str(step.get("node_set") or "su")] = _union(a, b)
            elif name == "FilterProperty":
                base = node_sets.get(str(step.get("node_set") or ""))
                if base:
                    node_sets[str(step.get("node_set") or "sf")] = self._filter_property(base, step)
            elif name == "ReturnAttributes":
                base = node_sets.get(str(step.get("node_set") or ""))
                target_label = str(step.get("node_type") or (base.label if base else "Unknown"))
                attrs = step.get("attributes") or DEFAULT_RETURN_ATTRIBUTES.get(target_label, ["Id", "Name"])
                if base:
                    final_rows = _project_attributes(base, attrs)
                else:
                    ns = self._locate_node(step)
                    final_rows = _project_attributes(ns, attrs)
            idx += 1

        if not final_rows and node_sets:
            last = list(node_sets.values())[-1]
            final_rows = _project_attributes(last, DEFAULT_RETURN_ATTRIBUTES.get(last.label, ["Id", "Name"]))
            target_label = last.label

        return ExecutionResult(rows=final_rows, target_label=target_label, plan_mode="actions")

    def _base_params(self) -> Dict[str, Any]:
        p: Dict[str, Any] = {"limit": self.limit}
        p.update(flgl_status_cypher_param())
        p.update(flgl_neighbor_filter_params())
        return p

    def _run_node_query(self, cypher: str, params: Dict[str, Any], label: str) -> Tuple[List[Dict[str, Any]], List[int]]:
        rows: List[Dict[str, Any]] = []
        ids: List[int] = []
        with self.driver.session() as session:
            result = session.run(cypher, **params)
            for record in result:
                node = record.get("node")
                nid = record.get("nid")
                props = _node_props(node)
                if nid is not None:
                    ids.append(int(nid))
                rows.append({"label": label, "properties": props, "id": nid})
        return rows, ids

    def _locate_node(self, step: ActionStep) -> NodeSet:
        label = str(step.get("node_type") or "Equipment")
        filters = dict(step.get("filters") or {})
        where, params = _filters_to_where("n", filters)
        params.update(self._base_params())
        cypher = (
            f"MATCH (n:{label}) WHERE {where} "
            "RETURN n AS node, id(n) AS nid LIMIT $limit"
        )
        rows, ids = self._run_node_query(cypher, params, label)
        return NodeSet(label=label, ids=ids, rows=rows)

    def _expand(self, base: NodeSet, step: ActionStep) -> NodeSet:
        rel = str(step.get("relation") or "CON")
        direction = str(step.get("direction") or "out").lower()
        target_label = str(step.get("node_type") or "")
        params = self._base_params()
        params["ids"] = base.ids or [r.get("id") for r in base.rows if r.get("id") is not None]

        if direction == "in":
            pattern = f"(m)-[:{rel}]->(n)"
            alias = "m"
        else:
            pattern = f"(n)-[:{rel}]->(m)"
            alias = "m"

        label_clause = f":{target_label}" if target_label else ""
        cypher = (
            "MATCH (n) WHERE id(n) IN $ids "
            f"MATCH {pattern.replace('(m)', f'(m{label_clause})')} "
            f"WHERE (_status_ok(n)) AND (_status_ok(m)) "
            "RETURN m AS node, id(m) AS nid LIMIT $limit"
        )
        cypher = cypher.replace("_status_ok(x)", _status_placeholder("x"))
        rows, ids = self._run_node_query(cypher, params, target_label or "Unknown")
        out_label = target_label or (rows[0].get("label") if rows else "Unknown")
        return NodeSet(label=out_label, ids=ids, rows=rows)

    def _filter_property(self, base: NodeSet, step: ActionStep) -> NodeSet:
        cond = dict(step.get("condition") or {})
        filtered = []
        ids = []
        for row in base.rows:
            props = row.get("properties") or {}
            if _match_condition(props, cond):
                filtered.append(row)
                if row.get("id") is not None:
                    ids.append(int(row["id"]))
        return NodeSet(label=base.label, ids=ids, rows=filtered)


def _macro_target_label(macro_name: str) -> str:
    mapping = {
        "列举": "EquipmentType",
        "查关联_类型下设备": "Equipment",
        "查指标": "Indicator",
        "查项目": "TestProject",
        "查项目_有环境": "TestProject",
        "查方法_经项目": "TestMethod",
        "查方法_经项目_有环境": "TestMethod",
        "查方法_经指标": "TestMethod",
        "查文件": "File",
    }
    return mapping.get(macro_name, "Unknown")


def _node_props(node: Any) -> Dict[str, Any]:
    if node is None:
        return {}
    if isinstance(node, dict):
        return dict(node.get("properties") or node)
    if hasattr(node, "_properties"):
        return dict(node._properties)
    if hasattr(node, "items"):
        return dict(node)
    return {}


def _filters_to_where(alias: str, filters: Dict[str, str]) -> Tuple[str, Dict[str, Any]]:
    parts = [_status_placeholder(alias)]
    params: Dict[str, Any] = {}
    for i, (field, value) in enumerate(filters.items()):
        safe = field if re.match(r"^[A-Za-z][A-Za-z0-9_]*$", field) else "Name"
        key = f"f{i}"
        parts.append(f"toLower(coalesce({alias}.{safe},'')) CONTAINS toLower(${key})")
        params[key] = value
    if len(parts) == 1:
        parts.append("true")
    return " AND ".join(parts), params


def _status_placeholder(alias: str) -> str:
    return (
        f"(NOT {alias}:EquipmentType AND NOT {alias}:Equipment AND NOT {alias}:Environment "
        f"AND NOT {alias}:TestProject AND NOT {alias}:Indicator AND NOT {alias}:TestMethod "
        f"AND NOT {alias}:File OR {alias}.Status IN $flgl_enabled_status OR {alias}.Status IS NULL)"
    )


def _match_condition(props: Dict[str, Any], cond: Dict[str, str]) -> bool:
    for field, value in cond.items():
        pv = str(props.get(field) or "")
        if value.lower() not in pv.lower():
            return False
    return True


def _intersect(a: NodeSet, b: NodeSet) -> NodeSet:
    id_set = set(a.ids) & set(b.ids)
    rows = [r for r in a.rows if r.get("id") in id_set]
    return NodeSet(label=a.label or b.label, ids=list(id_set), rows=rows)


def _union(a: NodeSet, b: NodeSet) -> NodeSet:
    seen = set()
    rows = []
    ids = []
    for r in a.rows + b.rows:
        rid = r.get("id")
        if rid in seen:
            continue
        seen.add(rid)
        rows.append(r)
        if rid is not None:
            ids.append(int(rid))
    return NodeSet(label=a.label or b.label, ids=ids, rows=rows)


def _project_attributes(ns: NodeSet, attributes: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in ns.rows:
        props = row.get("properties") or {}
        projected = {k: props.get(k) for k in attributes if k in props or k == "Id"}
        if "Id" in attributes and "Id" not in projected:
            projected["Id"] = props.get("Id") or row.get("id")
        out.append({"label": ns.label, "properties": projected, **{k: v for k, v in row.items() if k not in ("properties",)}})
    return out


def _try_merge_locate_expand(steps: List[ActionStep]) -> Optional[Tuple[str, Dict[str, Any], str, str]]:
    """合并 LocateNode + Expand 为单条 Cypher。"""
    if len(steps) < 2:
        return None
    if steps[0].get("action_name") != "LocateNode" or steps[1].get("action_name") != "Expand":
        return None
    from_label = str(steps[0].get("node_type") or "Equipment")
    to_label = str(steps[1].get("node_type") or "")
    rel = str(steps[1].get("relation") or "CON")
    filters = dict(steps[0].get("filters") or {})
    where, params = _filters_to_where("n", filters)
    params["limit"] = default_query_limit()
    params.update(flgl_status_cypher_param())
    params.update(flgl_neighbor_filter_params())
    out_key = str(steps[1].get("node_set_out") or "s1")
    to_clause = f":{to_label}" if to_label else ""
    cypher = (
        f"MATCH (n:{from_label}) WHERE {where} "
        f"MATCH (n)-[:{rel}]->(m{to_clause}) WHERE {_status_placeholder('m')} "
        "RETURN m AS node, id(m) AS nid LIMIT $limit"
    )
    return cypher, params, out_key, to_label or "Unknown"


def format_result_preview(rows: List[Dict[str, Any]], *, max_lines: int = 12) -> List[str]:
    """供思考区展示的检索摘要行。"""
    lines: List[str] = []
    for i, row in enumerate(rows):
        if i >= max_lines:
            lines.append(f"…共 {len(rows)} 条")
            break
        props = row.get("properties") or {}
        name = str(props.get("Name") or props.get("Label") or props.get("Id") or "?")[:60]
        label = str(row.get("label") or "?")
        lines.append(f"- {label}: {name}")
    return lines
