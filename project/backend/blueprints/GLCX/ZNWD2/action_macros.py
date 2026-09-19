"""动作层宏定义：高频业务模式直连 Cypher。"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.neo4j_flgl_status import flgl_neighbor_filter_params, flgl_status_cypher_param

from .config import default_query_limit

MacroFn = Callable[..., List[Dict[str, Any]]]

MACRO_REGISTRY: Dict[str, str] = {
    "列举": "list_nodes",
    "查关联_类型下设备": "find_equipment_under_type",
    "查指标": "find_indicators_for_equipment",
    "查项目": "find_projects_for_equipment",
    "查项目_有环境": "find_projects_for_equipment_and_environment",
    "查方法_经项目": "find_methods_via_project",
    "查方法_经项目_有环境": "find_methods_via_project_with_environment",
    "查方法_经指标": "find_methods_via_indicator",
    "查文件": "find_files_for_method",
}


def _status_clause(alias: str = "n") -> str:
    return (
        f"(NOT {alias}:EquipmentType AND NOT {alias}:Equipment AND NOT {alias}:Environment "
        f"AND NOT {alias}:TestProject AND NOT {alias}:Indicator AND NOT {alias}:TestMethod "
        f"AND NOT {alias}:File OR {alias}.Status IN $flgl_enabled_status OR {alias}.Status IS NULL)"
    )


def _build_fuzzy_where(alias: str, field: str, value: str, *, param_key: str = "kw") -> Tuple[str, Dict[str, Any]]:
    safe_field = field if re.match(r"^[A-Za-z][A-Za-z0-9_]*$", field) else "Name"
    clause = f"toLower(coalesce({alias}.{safe_field},'')) CONTAINS toLower(${param_key})"
    return clause, {param_key: value}


def _run_query(driver: Any, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    params = dict(params or {})
    params.update(flgl_status_cypher_param())
    params.update(flgl_neighbor_filter_params())
    rows: List[Dict[str, Any]] = []
    with driver.session() as session:
        result = session.run(cypher, **params)
        for record in result:
            rows.append(dict(record))
    return rows


def find_indicators_for_equipment(
    driver: Any,
    *,
    equipment_filter: Optional[Dict[str, str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Equipment → Indicator（一步直连；对齐 ZNWD1 neo_equipment_indicators）。

    指标只依赖设备，与环境、项目无关。
    """
    equipment_filter = equipment_filter or {}
    limit = limit or default_query_limit()
    params: Dict[str, Any] = {"limit": limit, "kw": "", "focus_id": ""}

    eq_match = f"({_status_clause('e')})"
    if equipment_filter:
        field, value = next(iter(equipment_filter.items()))
        if field == "Id":
            params["focus_id"] = value
        else:
            params["kw"] = value

    cypher = """
        MATCH (e:Equipment)
        WHERE (e.Status IS NULL OR e.Status IN $flgl_enabled_status)
          AND (
            ($focus_id <> '' AND toString(coalesce(e.Id,'')) = $focus_id)
            OR (
              $focus_id = '' AND toLower(coalesce(e.Name,'')) CONTAINS toLower($kw)
            )
          )
        WITH e
        OPTIONAL MATCH (e)-[:CON]->(i1:Indicator)
        WHERE i1 IS NULL OR i1.Status IS NULL OR i1.Status IN $flgl_enabled_status
        OPTIONAL MATCH (i2:Indicator)
        WHERE (i2.Status IS NULL OR i2.Status IN $flgl_enabled_status)
          AND toLower(coalesce(i2.Equipment_name,'')) CONTAINS toLower(coalesce(e.Name,''))
        WITH collect(DISTINCT i1) + collect(DISTINCT i2) AS inds
        UNWIND inds AS ind
        WITH ind
        WHERE ind IS NOT NULL
        RETURN DISTINCT ind AS node, labels(ind)[0] AS label
        LIMIT $limit
    """
    return _normalize_rows(_run_query(driver, cypher, params), "Indicator")


def list_nodes(
    driver: Any,
    *,
    node_type: str = "EquipmentType",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """列举某类全部节点（对齐 ZNWD1 neo_list_nodes）。"""
    allowed = (
        "EquipmentType",
        "Equipment",
        "Environment",
        "TestProject",
        "Indicator",
        "TestMethod",
        "File",
    )
    if node_type not in allowed:
        node_type = "EquipmentType"
    limit = limit or default_query_limit()
    params: Dict[str, Any] = {"limit": limit}
    cypher = (
        f"MATCH (n:{node_type}) "
        "WHERE n.Status IS NULL OR n.Status IN $flgl_enabled_status "
        "RETURN n AS node, labels(n)[0] AS label "
        "ORDER BY coalesce(toString(n.Name), toString(n.Label), toString(n.Id), '') "
        "LIMIT $limit"
    )
    return _normalize_rows(_run_query(driver, cypher, params), node_type)


def find_equipment_under_type(
    driver: Any,
    *,
    equipment_type_filter: Optional[Dict[str, str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """EquipmentType → Equipment（对齐 ZNWD1 neo_equipment_under_type）。"""
    equipment_type_filter = equipment_type_filter or {}
    limit = limit or default_query_limit()
    params: Dict[str, Any] = {"limit": limit, "kw": "", "focus_id": ""}
    if equipment_type_filter:
        field, value = next(iter(equipment_type_filter.items()))
        if field == "Id":
            params["focus_id"] = value
        else:
            params["kw"] = value

    cypher = """
        MATCH (et:EquipmentType)
        WHERE (et.Status IS NULL OR et.Status IN $flgl_enabled_status)
          AND (
            ($focus_id <> '' AND toString(coalesce(et.Id,'')) = $focus_id)
            OR (
              $focus_id = '' AND (
                toLower(coalesce(et.Name,'')) CONTAINS toLower($kw)
                OR toLower(coalesce(et.Type,'')) CONTAINS toLower($kw)
              )
            )
          )
        WITH collect(DISTINCT et) AS seeds
        UNWIND seeds AS s
        WITH seeds, collect(DISTINCT coalesce(s.Type,'')) AS type_vals
        MATCH (et:EquipmentType)
        WHERE (et.Status IS NULL OR et.Status IN $flgl_enabled_status)
          AND (
            et IN seeds
            OR ANY(tv IN type_vals WHERE tv <> '' AND coalesce(et.Type,'') = tv)
          )
        WITH collect(DISTINCT et) AS all_et
        UNWIND all_et AS et
        MATCH (e:Equipment)
        WHERE (e.Status IS NULL OR e.Status IN $flgl_enabled_status)
          AND (
            (et)-[:HAS]->(e)
            OR toString(coalesce(e.EquipmentTypeId,'')) = toString(coalesce(et.Id,''))
          )
        RETURN DISTINCT e AS node, labels(e)[0] AS label, et.Name AS equipment_type_name
        LIMIT $limit
    """
    return _normalize_rows(_run_query(driver, cypher, params), "Equipment")


def find_projects_for_equipment(
    driver: Any,
    *,
    equipment_filter: Optional[Dict[str, str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Equipment → TestProject（仅设备约束）。"""
    equipment_filter = equipment_filter or {}
    limit = limit or default_query_limit()
    where_parts: List[str] = [_status_clause("eq"), _status_clause("tp")]
    params: Dict[str, Any] = {"limit": limit}

    if equipment_filter:
        field, value = next(iter(equipment_filter.items()))
        w, p = _build_fuzzy_where("eq", field, value)
        where_parts.append(w)
        params.update(p)
    else:
        where_parts.append("true")

    cypher = (
        "MATCH (eq:Equipment)-[:CON]->(tp:TestProject) "
        f"WHERE {' AND '.join(where_parts)} "
        "OPTIONAL MATCH (parent:TestProject)-[:HAS]->(tp) "
        "RETURN DISTINCT tp AS node, labels(tp)[0] AS label, parent.Name AS parent_name "
        "LIMIT $limit"
    )
    return _normalize_rows(_run_query(driver, cypher, params), "TestProject")


def find_projects_for_equipment_and_environment(
    driver: Any,
    *,
    equipment_filter: Optional[Dict[str, str]] = None,
    environment_filter: Optional[Dict[str, str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Equipment + Environment 共同约束 TestProject（项目依赖设备与环境）。

    双锚点：eq-CON->tp<-CON-env
    """
    equipment_filter = equipment_filter or {}
    environment_filter = environment_filter or {}
    limit = limit or default_query_limit()
    params: Dict[str, Any] = {"limit": limit}
    eq_where = [_status_clause("eq")]
    env_where = [_status_clause("env")]
    tp_where = [_status_clause("tp")]

    if equipment_filter:
        field, value = next(iter(equipment_filter.items()))
        w, p = _build_fuzzy_where("eq", field, value, param_key="eq_kw")
        eq_where.append(w)
        params.update(p)
    else:
        eq_where.append("true")

    if environment_filter:
        field, value = next(iter(environment_filter.items()))
        w, p = _build_fuzzy_where("env", field, value, param_key="env_kw")
        env_where.append(w)
        params.update(p)
    else:
        env_where.append("true")

    cypher = (
        "MATCH (eq:Equipment) "
        f"WHERE {' AND '.join(eq_where)} "
        "MATCH (env:Environment) "
        f"WHERE {' AND '.join(env_where)} "
        "MATCH (eq)-[:CON]->(tp:TestProject)<-[:CON]-(env) "
        f"WHERE {' AND '.join(tp_where)} "
        "OPTIONAL MATCH (parent:TestProject)-[:HAS]->(tp) "
        "RETURN DISTINCT tp AS node, labels(tp)[0] AS label, parent.Name AS parent_name, env.Name AS environment_name "
        "LIMIT $limit"
    )
    return _normalize_rows(_run_query(driver, cypher, params), "TestProject")


def find_methods_via_project(
    driver: Any,
    *,
    equipment_filter: Optional[Dict[str, str]] = None,
    project_filter: Optional[Dict[str, str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Equipment → TestProject → TestMethod。"""
    equipment_filter = equipment_filter or {}
    project_filter = project_filter or {}
    limit = limit or default_query_limit()
    params: Dict[str, Any] = {"limit": limit}
    where_parts: List[str] = [_status_clause("eq"), _status_clause("tp"), _status_clause("tm")]

    if equipment_filter:
        field, value = next(iter(equipment_filter.items()))
        w, p = _build_fuzzy_where("eq", field, value)
        where_parts.append(w)
        params.update(p)
    if project_filter:
        field, value = next(iter(project_filter.items()))
        w, p = _build_fuzzy_where("tp", field, value, param_key="pj_kw")
        where_parts.append(w)
        params.update(p)

    cypher = (
        "MATCH (eq:Equipment)-[:CON]->(tp:TestProject)-[:CON]->(tm:TestMethod) "
        f"WHERE {' AND '.join(where_parts)} "
        "RETURN DISTINCT tm AS node, labels(tm)[0] AS label, tp.Name AS project_name "
        "LIMIT $limit"
    )
    return _normalize_rows(_run_query(driver, cypher, params), "TestMethod")


def find_methods_via_project_with_environment(
    driver: Any,
    *,
    equipment_filter: Optional[Dict[str, str]] = None,
    environment_filter: Optional[Dict[str, str]] = None,
    project_filter: Optional[Dict[str, str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Equipment + Environment 约束 TestProject，再查 TestMethod。"""
    equipment_filter = equipment_filter or {}
    environment_filter = environment_filter or {}
    project_filter = project_filter or {}
    limit = limit or default_query_limit()
    params: Dict[str, Any] = {"limit": limit}
    eq_where = [_status_clause("eq")]
    env_where = [_status_clause("env")]
    tail_where = [_status_clause("tp"), _status_clause("tm")]

    if equipment_filter:
        field, value = next(iter(equipment_filter.items()))
        w, p = _build_fuzzy_where("eq", field, value, param_key="eq_kw")
        eq_where.append(w)
        params.update(p)
    else:
        eq_where.append("true")

    if environment_filter:
        field, value = next(iter(environment_filter.items()))
        w, p = _build_fuzzy_where("env", field, value, param_key="env_kw")
        env_where.append(w)
        params.update(p)
    else:
        env_where.append("true")

    if project_filter:
        field, value = next(iter(project_filter.items()))
        w, p = _build_fuzzy_where("tp", field, value, param_key="pj_kw")
        tail_where.append(w)
        params.update({k: v for k, v in p.items() if k != "pj_kw"})

    cypher = (
        "MATCH (eq:Equipment) "
        f"WHERE {' AND '.join(eq_where)} "
        "MATCH (env:Environment) "
        f"WHERE {' AND '.join(env_where)} "
        "MATCH (eq)-[:CON]->(tp:TestProject)<-[:CON]-(env) "
        f"WHERE {' AND '.join(tail_where)} "
        "MATCH (tp)-[:CON]->(tm:TestMethod) "
        "RETURN DISTINCT tm AS node, labels(tm)[0] AS label, tp.Name AS project_name, env.Name AS environment_name "
        "LIMIT $limit"
    )
    return _normalize_rows(_run_query(driver, cypher, params), "TestMethod")


def find_methods_via_indicator(
    driver: Any,
    *,
    equipment_filter: Optional[Dict[str, str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Equipment → Indicator → TestMethod（问法含指标/考核时使用）。"""
    equipment_filter = equipment_filter or {}
    limit = limit or default_query_limit()
    where_parts: List[str] = [_status_clause("eq"), _status_clause("ind"), _status_clause("tm")]
    params: Dict[str, Any] = {"limit": limit}

    if equipment_filter:
        field, value = next(iter(equipment_filter.items()))
        w, p = _build_fuzzy_where("eq", field, value)
        where_parts.append(w)
        params.update(p)
    else:
        where_parts.append("true")

    cypher = (
        "MATCH (eq:Equipment)-[:CON]->(ind:Indicator)-[:CON]->(tm:TestMethod) "
        f"WHERE {' AND '.join(where_parts)} "
        "RETURN DISTINCT tm AS node, labels(tm)[0] AS label, ind.Name AS indicator_name "
        "LIMIT $limit"
    )
    return _normalize_rows(_run_query(driver, cypher, params), "TestMethod")


def find_files_for_method(
    driver: Any,
    *,
    method_filter: Optional[Dict[str, str]] = None,
    equipment_filter: Optional[Dict[str, str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """TestMethod → File。"""
    method_filter = method_filter or {}
    equipment_filter = equipment_filter or {}
    limit = limit or default_query_limit()
    params: Dict[str, Any] = {"limit": limit}

    if equipment_filter and not method_filter:
        eq_field, eq_value = next(iter(equipment_filter.items()))
        eq_w, eq_p = _build_fuzzy_where("eq", eq_field, eq_value)
        params.update(eq_p)
        cypher = (
            "MATCH (eq:Equipment)-[:CON]->(tp:TestProject)-[:CON]->(tm:TestMethod)-[:BELONGS_TO]->(f:File) "
            f"WHERE {eq_w} AND {_status_clause('tm')} AND {_status_clause('f')} "
            "RETURN DISTINCT f AS node, labels(f)[0] AS label, tm.Name AS method_name "
            "LIMIT $limit"
        )
        return _normalize_rows(_run_query(driver, cypher, params), "File")

    where_parts = [_status_clause("tm"), _status_clause("f")]
    if method_filter:
        field, value = next(iter(method_filter.items()))
        w, p = _build_fuzzy_where("tm", field, value)
        where_parts.append(w)
        params.update(p)

    cypher = (
        "MATCH (tm:TestMethod)-[:BELONGS_TO]->(f:File) "
        f"WHERE {' AND '.join(where_parts)} "
        "RETURN DISTINCT f AS node, labels(f)[0] AS label, tm.Name AS method_name "
        "LIMIT $limit"
    )
    return _normalize_rows(_run_query(driver, cypher, params), "File")


def _normalize_rows(rows: List[Dict[str, Any]], label: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        node = row.get("node")
        props: Dict[str, Any] = {}
        if isinstance(node, dict):
            props = dict(node.get("properties") or node)
        elif hasattr(node, "_properties"):
            props = dict(node._properties)
        elif hasattr(node, "items"):
            props = dict(node)
        entry = {"label": row.get("label") or label, "properties": props}
        for k, v in row.items():
            if k not in ("node", "label"):
                entry[k] = v
        out.append(entry)
    return out


def get_macro_function(name: str) -> Optional[MacroFn]:
    """按宏 key 返回可调用函数。"""
    fn_name = MACRO_REGISTRY.get(name)
    if not fn_name:
        return None
    return globals().get(fn_name)
