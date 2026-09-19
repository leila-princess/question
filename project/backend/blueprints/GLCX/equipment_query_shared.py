"""JQCX / SSYQ 共用的设备-项目-指标查询与层级过滤。"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from utils.neo4j_flgl_status import flgl_status_cypher_param

from .indicator_hierarchy import build_indicator_tree

HIERARCHY_TYPES = frozenset({"", "category", "test", "父级", "子级"})
EXCLUDED_TYPES = frozenset({"ff"})


def is_displayable_hierarchy_type(type_val) -> bool:
    """展示：空、category/test、父级/子级；排除 Type=FF（方法提取）。"""
    raw = str(type_val or "").strip()
    if not raw:
        return True
    t = raw.lower()
    if t in EXCLUDED_TYPES:
        return False
    return t in {x.lower() for x in HIERARCHY_TYPES if x}


def filter_displayable_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [n for n in (nodes or []) if is_displayable_hierarchy_type(n.get("type"))]


def lookup_env_names_by_label(label: str, *, run_cypher: Callable[..., List[Dict[str, Any]]]) -> List[str]:
    """TestProject.Label 与 Environment.Name 对齐（无 CON 边时的兜底）。"""
    lbl = (label or "").strip()
    if not lbl or lbl in ("其他环境", "未分组", "试验环境"):
        return []
    st = flgl_status_cypher_param()
    rows = run_cypher(
        """
        MATCH (env:Environment)
        WHERE env.Status IN $flgl_enabled_status
          AND (env.Name = $lbl OR env.Label = $lbl
               OR env.Name CONTAINS $lbl OR $lbl CONTAINS env.Name)
        RETURN DISTINCT env.Name AS n
        ORDER BY size(env.Name) ASC
        LIMIT 5
        """,
        {**st, "lbl": lbl},
    )
    out: List[str] = []
    for r in rows:
        n = str(r.get("n") or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def collect_env_names_for_project(
    env_names,
    project_data,
    parent_data,
    *,
    run_cypher: Callable[..., List[Dict[str, Any]]],
) -> List[str]:
    """CON 关联的 Environment.Name 优先；否则用项目/父级 Label 匹配 Environment。"""
    names = [str(e).strip() for e in (env_names or []) if e and str(e).strip()]
    if names:
        return names
    proj = project_data or {}
    parent = parent_data or {}
    proj_type = str(proj.get("type", "")).lower()
    labels: List[str] = []
    pl = str(proj.get("label") or proj.get("Label") or "").strip()
    if pl:
        labels.append(pl)
    ppl = str(parent.get("label") or parent.get("Label") or "").strip()
    if ppl:
        labels.append(ppl)
    for lbl in labels:
        found = lookup_env_names_by_label(lbl, run_cypher=run_cypher)
        if found:
            return found
        if proj_type == "category":
            return [lbl]
    return []


_PROJECTS_CYPHER = """
    MATCH (eq:Equipment)-[:CON]->(seed:TestProject)
    WHERE eq.Id IN $eqIds
      AND eq.Status IN $flgl_enabled_status
      AND seed.Status IN $flgl_enabled_status
      AND coalesce(toLower(seed.Type), '') <> 'ff'
      AND (
        coalesce(seed.Type, '') = ''
        OR seed.Type IN ['test', 'category', '父级', '子级']
      )

    OPTIONAL MATCH (seed)-[:HAS]->(seed_child:TestProject)
    WHERE seed_child.Status IN $flgl_enabled_status
      AND coalesce(toLower(seed_child.Type), '') <> 'ff'
      AND (
        coalesce(seed_child.Type, '') = ''
        OR seed_child.Type IN ['test', 'category', '父级', '子级']
      )

    OPTIONAL MATCH (seed_parent:TestProject)-[:HAS]->(seed)
    WHERE seed_parent.Type = 'category'
      AND seed_parent.Status IN $flgl_enabled_status
      AND coalesce(toLower(seed_parent.Type), '') <> 'ff'

    WITH DISTINCT seed,
         collect(DISTINCT seed_child) AS seed_children,
         collect(DISTINCT seed_parent) AS seed_parents

    UNWIND ([seed] + seed_children + seed_parents) AS proj
    WITH DISTINCT proj
    WHERE proj IS NOT NULL
      AND coalesce(toLower(proj.Type), '') <> 'ff'
      AND (
        coalesce(proj.Type, '') = ''
        OR proj.Type IN ['test', 'category', '父级', '子级']
      )

    OPTIONAL MATCH (parent:TestProject)-[:HAS]->(proj)
    WHERE parent.Type = 'category'
      AND parent.Status IN $flgl_enabled_status
      AND coalesce(toLower(parent.Type), '') <> 'ff'

    OPTIONAL MATCH (env:Environment)-[:CON]->(proj)
    WHERE env.Status IN $flgl_enabled_status

    RETURN DISTINCT proj, parent, collect(DISTINCT env.Name) AS env_names
    ORDER BY proj.Name
"""


def _assemble_project_category_nodes(
    records: List[Dict[str, Any]],
    *,
    get_node_props: Callable[[Any], Dict[str, Any]],
    run_cypher: Callable[..., List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    categories: Dict[str, Dict[str, Any]] = {}
    for record in records:
        project_data = get_node_props(record.get("proj") or {})
        parent_data = get_node_props(record.get("parent") or {})
        if not is_displayable_hierarchy_type(project_data.get("type")):
            continue
        if parent_data and not is_displayable_hierarchy_type(parent_data.get("type")):
            parent_data = {}

        env_names = collect_env_names_for_project(
            record.get("env_names"), project_data, parent_data, run_cypher=run_cypher
        )

        if not project_data.get("id") and not project_data.get("name"):
            continue

        project_type = str(project_data.get("type", "")).lower()
        project_id = project_data.get("id") or project_data.get("name")

        if project_type in ("category", "父级"):
            bucket_id = project_id
            if bucket_id not in categories:
                categories[bucket_id] = {
                    "category": project_data,
                    "children": [],
                    "child_ids": set(),
                    "env_names": set(),
                }
            categories[bucket_id]["env_names"].update(env_names)
            continue

        if parent_data and parent_data.get("id"):
            bucket_id = parent_data.get("id")
            if bucket_id not in categories:
                categories[bucket_id] = {
                    "category": parent_data,
                    "children": [],
                    "child_ids": set(),
                    "env_names": set(),
                }
            child_data = dict(project_data)
            child_data["_env"] = "、".join(sorted(set(env_names))) if env_names else ""
            child_data["type"] = "test"
            if project_id not in categories[bucket_id]["child_ids"]:
                categories[bucket_id]["children"].append(child_data)
                categories[bucket_id]["child_ids"].add(project_id)
            categories[bucket_id]["env_names"].update(env_names)
            continue

        bucket_id = f"__single__{project_id}"
        if bucket_id not in categories:
            categories[bucket_id] = {
                "category": {
                    "id": project_id,
                    "name": project_data.get("name", project_id or "未命名项目"),
                    "type": "category",
                },
                "children": [],
                "child_ids": set(),
                "env_names": set(),
            }
        child_data = dict(project_data)
        child_data["_env"] = "、".join(sorted(set(env_names))) if env_names else ""
        child_data["type"] = "test"
        if project_id not in categories[bucket_id]["child_ids"]:
            categories[bucket_id]["children"].append(child_data)
            categories[bucket_id]["child_ids"].add(project_id)
        categories[bucket_id]["env_names"].update(env_names)

    nodes: List[Dict[str, Any]] = []
    for _, cat_info in categories.items():
        cat_node = dict(cat_info["category"])
        cat_node["children"] = cat_info["children"]
        cat_node["type"] = "category"
        if cat_info["env_names"]:
            cat_node["_env"] = "、".join(sorted(cat_info["env_names"]))
        nodes.append(cat_node)

    nodes.sort(key=lambda x: (str(x.get("name") or ""), str(x.get("id") or "")))
    return nodes


def build_project_nodes_for_equipment(
    eq_ids: List[str],
    *,
    run_cypher: Callable[..., List[Dict[str, Any]]],
    get_node_props: Callable[[Any], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    eq_id_list = [e.strip() for e in (eq_ids or []) if e and str(e).strip()]
    if not eq_id_list:
        return []
    records = run_cypher(_PROJECTS_CYPHER, {**flgl_status_cypher_param(), "eqIds": eq_id_list})
    return _assemble_project_category_nodes(
        records, get_node_props=get_node_props, run_cypher=run_cypher
    )


def build_project_nodes_for_indicator(
    ind_id: str,
    *,
    run_cypher: Callable[..., List[Dict[str, Any]]],
    get_node_props: Callable[[Any], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not ind_id:
        return []
    cypher = """
        MATCH (proj:TestProject)-[:CON]->(i:Indicator {Id: $indId})
        WHERE (proj).Status IN $flgl_enabled_status
          AND (i).Status IN $flgl_enabled_status
          AND coalesce(toLower(proj.Type), '') <> 'ff'
          AND (
            coalesce(proj.Type, '') = ''
            OR proj.Type IN ['test', 'category', '父级', '子级']
          )

        OPTIONAL MATCH (parent:TestProject)-[:HAS]->(proj)
        WHERE parent.Type = 'category'
          AND parent.Status IN $flgl_enabled_status
          AND coalesce(toLower(parent.Type), '') <> 'ff'

        OPTIONAL MATCH (env:Environment)-[:CON]->(proj)
        WHERE env.Status IN $flgl_enabled_status

        RETURN DISTINCT proj, parent, collect(DISTINCT env.Name) AS env_names
        ORDER BY proj.Name
    """
    records = run_cypher(cypher, {**flgl_status_cypher_param(), "indId": ind_id})
    return _assemble_project_category_nodes(
        records, get_node_props=get_node_props, run_cypher=run_cypher
    )


def group_project_nodes_by_env(project_nodes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """将 category+children 结构按环境列分组（与 JQCX 前端 page.tsx 一致）。"""
    projects_by_env: Dict[str, List[Dict[str, Any]]] = {}
    categories = [p for p in (project_nodes or []) if str(p.get("type", "")).lower() in ("category", "父级", "")]

    for cat in categories:
        child_envs: set = set()
        children = cat.get("children") or []

        if children:
            for child in children:
                raw_env = str(child.get("_env") or "").strip()
                env_key = raw_env or "其他环境"
                for part in env_key.split("、"):
                    part = part.strip() or "其他环境"
                    child_envs.add(part)
                    if part not in projects_by_env:
                        projects_by_env[part] = []
                    if not any(p.get("id") == child.get("id") for p in projects_by_env[part]):
                        projects_by_env[part].append(dict(child))

        parent_env = str(cat.get("_env") or "").strip()
        if parent_env:
            for part in parent_env.split("、"):
                part = part.strip()
                if part:
                    child_envs.add(part)

        if not child_envs:
            child_envs.add("其他环境")

        for env_name in child_envs:
            if env_name not in projects_by_env:
                projects_by_env[env_name] = []
            env_children = []
            for child in children:
                raw_env = str(child.get("_env") or "").strip()
                if not raw_env or env_name in raw_env.split("、") or env_name == "其他环境":
                    if not any(c.get("id") == child.get("id") for c in env_children):
                        env_children.append(child)
            if not any(p.get("id") == cat.get("id") for p in projects_by_env[env_name]):
                cat_copy = dict(cat)
                cat_copy["children"] = env_children
                projects_by_env[env_name].insert(0, cat_copy)

    return projects_by_env


def build_indicator_nodes_for_equipment(
    eq_ids: List[str],
    *,
    run_cypher: Callable[..., List[Dict[str, Any]]],
    get_node_props: Callable[[Any], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    eq_id_list = [e.strip() for e in (eq_ids or []) if e and str(e).strip()]
    if not eq_id_list:
        return []

    all_indicators: List[Dict[str, Any]] = []
    indicator_ids: set = set()

    for eq_id in eq_id_list:
        found_for_eq = False
        for rel_type in ["CON", "BELONGS_TO"]:
            cypher = f"""
            MATCH (eq:Equipment {{Id: $eqId}})-[:{rel_type}]->(i:Indicator)
            WHERE (eq).Status IN $flgl_enabled_status AND (i).Status IN $flgl_enabled_status
            RETURN DISTINCT i ORDER BY i.Name
            """
            records = run_cypher(cypher, {**flgl_status_cypher_param(), "eqId": eq_id})
            for r in records:
                ind = get_node_props(r.get("i", {}))
                if not is_displayable_hierarchy_type(ind.get("type")):
                    continue
                ind_id = ind.get("id", "")
                if ind_id and ind_id not in indicator_ids:
                    indicator_ids.add(ind_id)
                    all_indicators.append(ind)
                    found_for_eq = True
            if found_for_eq:
                break

        if not found_for_eq:
            cypher = """
            MATCH (eq:Equipment {Id: $eqId})-[:CON]->(p:TestProject)-[:CON]->(i:Indicator)
            WHERE (eq).Status IN $flgl_enabled_status
              AND (p).Status IN $flgl_enabled_status
              AND (i).Status IN $flgl_enabled_status
            RETURN DISTINCT i ORDER BY i.Name
            """
            records = run_cypher(cypher, {**flgl_status_cypher_param(), "eqId": eq_id})
            for r in records:
                ind = get_node_props(r.get("i", {}))
                if not is_displayable_hierarchy_type(ind.get("type")):
                    continue
                ind_id = ind.get("id", "")
                if ind_id and ind_id not in indicator_ids:
                    indicator_ids.add(ind_id)
                    all_indicators.append(ind)

    if not indicator_ids:
        return []

    parent_rows = run_cypher(
        """
        MATCH (p:Indicator)-[:HAS]->(c:Indicator)
        WHERE p.Id IN $ids OR c.Id IN $ids
        RETURN p.Id AS parent_id, p.Name AS parent_name, c.Id AS child_id
        """,
        {"ids": list(indicator_ids)},
    )
    parent_map: Dict[str, Dict[str, str]] = {}
    for r in parent_rows:
        cid = str(r.get("child_id") or "").strip()
        pid = str(r.get("parent_id") or "").strip()
        if cid and pid:
            parent_map[cid] = {
                "parent_id": pid,
                "parent_name": str(r.get("parent_name") or "").strip(),
            }

    for ind in all_indicators:
        iid = str(ind.get("id") or "").strip()
        p = parent_map.get(iid) or {}
        ind["parent_id"] = p.get("parent_id", "")
        ind["parent_name"] = p.get("parent_name", "")
        t = str(ind.get("type") or "").strip()
        ind["type"] = t if t in ("category", "test") else ("test" if ind["parent_id"] else "category")

    return build_indicator_tree(all_indicators)


def serialize_neo4j_value(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def get_node_props(node_data: Any) -> Dict[str, Any]:
    """Neo4j 节点属性 → 小写键 dict（与 SSYQ/JQCX 一致）。"""
    if not node_data:
        return {}
    if hasattr(node_data, "items"):
        items = node_data.items()
    elif isinstance(node_data, dict):
        items = node_data.items()
    else:
        return {}
    result: Dict[str, Any] = {}
    for k, v in items:
        if str(k).startswith("_"):
            continue
        result[str(k).lower()] = serialize_neo4j_value(v)
    return result


def flatten_projects_for_picker(project_nodes: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """将 category+children 项目树打平为 graph-picker 可选列表（含无父级项目）。"""
    out: List[Dict[str, str]] = []
    seen: set = set()
    for cat in project_nodes or []:
        cat_id = str(cat.get("id") or "")
        cat_name = str(cat.get("name") or "").strip()
        is_single = cat_id.startswith("__single__")
        for ch in cat.get("children") or []:
            pid = str(ch.get("id") or "").strip()
            if not pid or pid in seen:
                continue
            if not is_displayable_hierarchy_type(ch.get("type")):
                continue
            seen.add(pid)
            pname = str(ch.get("name") or pid)
            display = f"{cat_name} / {pname}" if cat_name and not is_single else pname
            out.append({
                "id": pid,
                "name": display,
                "label": str(ch.get("label") or ""),
                "type": str(ch.get("type") or "test"),
            })
    out.sort(key=lambda x: (x.get("name") or "", x.get("id") or ""))
    return out


def flatten_indicators_for_picker(indicator_nodes: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """将指标层级打平为 graph-picker 可选列表（保留 HAS 父子显示名，含 category 孤节点）。"""
    out: List[Dict[str, str]] = []
    seen: set = set()

    def _walk(nodes: List[Dict[str, Any]], parent_name: str = "") -> None:
        for ind in nodes or []:
            iid = str(ind.get("id") or "").strip()
            if not iid or iid in seen:
                continue
            if not is_displayable_hierarchy_type(ind.get("type")):
                continue
            seen.add(iid)
            name = str(ind.get("name") or iid)
            display = f"{parent_name} / {name}" if parent_name else name
            out.append({
                "id": iid,
                "name": display,
                "label": str(ind.get("label") or ""),
                "type": str(ind.get("type") or ""),
            })
            child_prefix = name if str(ind.get("type") or "").lower() == "category" else parent_name
            _walk(ind.get("children") or [], child_prefix or name)

    _walk(indicator_nodes or [])
    out.sort(key=lambda x: (x.get("name") or "", x.get("id") or ""))
    return out


def resolve_project_env_info(
    project: Dict[str, Any],
    *,
    run_cypher: Callable[..., List[Dict[str, Any]]],
    get_node_props: Callable[[Any], Dict[str, Any]],
    proj_id: Optional[str] = None,
) -> tuple:
    """返回 (env_id, env_name, env_names_list)。"""
    pid = proj_id or project.get("id") or ""
    stp = flgl_status_cypher_param()
    env_link = run_cypher(
        """
        MATCH (env:Environment)-[:CON]->(p:TestProject {Id: $projId})
        WHERE (env).Status IN $flgl_enabled_status AND (p).Status IN $flgl_enabled_status
        RETURN env.Id AS env_id, env.Name AS env_name
        LIMIT 1
        """,
        {**stp, "projId": pid},
    )
    env_id = ""
    env_names: List[str] = []
    if env_link:
        env_id = str(env_link[0].get("env_id") or "")
        env_name = str(env_link[0].get("env_name") or "").strip()
        if env_name:
            env_names = [env_name]

    parent_data: Dict[str, Any] = {}
    parent_rows = run_cypher(
        """
        MATCH (parent:TestProject)-[:HAS]->(p:TestProject {Id: $projId})
        WHERE parent.Type = 'category' AND parent.Status IN $flgl_enabled_status
        RETURN parent LIMIT 1
        """,
        {**stp, "projId": pid},
    )
    if parent_rows:
        parent_data = get_node_props(parent_rows[0].get("parent", {}))

    if not env_names:
        env_names = collect_env_names_for_project(
            [], project, parent_data, run_cypher=run_cypher
        )

    env_name = env_names[0] if env_names else "其他环境"
    return env_id, env_name, env_names
