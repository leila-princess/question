"""
语义搜索(SSYQ) API

数据模型:
  EquipmentCategory → HAS → EquipmentType → HAS → Equipment
  Equipment → CON → TestProject
  Equipment → CON / CON / BELONGS_TO → Indicator
  Environment → CON → TestProject
  TestProject → CON → TestMethod
  Indicator → CON → TestMethod
  TestMethod → CON → File
"""
from flask import Blueprint, request, jsonify, current_app
import json
import os
import logging
from utils.neo4j_flgl_status import flgl_status_cypher_param
from ..equipment_query_shared import (
    build_indicator_nodes_for_equipment,
    build_project_nodes_for_equipment,
    build_project_nodes_for_indicator,
    filter_displayable_nodes,
    group_project_nodes_by_env,
    is_displayable_hierarchy_type,
    resolve_project_env_info,
)
from ..indicator_hierarchy import apply_indicator_forest_to_graph

logger = logging.getLogger(__name__)

ssyq_bp = Blueprint('ssyq', __name__, url_prefix='/api/GLCX/SSYQ')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
STANDARD_MAPPING_PATH = os.path.join(PROJECT_ROOT, 'standard', 'standard_mapping.json')


def _load_standard_mapping():
    try:
        with open(STANDARD_MAPPING_PATH, 'r', encoding='utf-8') as f:
            return json.load(f).get('mappings', {})
    except Exception:
        return {}


def _get_source_name(source_id):
    mapping = _load_standard_mapping()
    if source_id in mapping:
        return mapping[source_id].get('source_name', source_id)
    return source_id


def _run_cypher(cypher, params=None):
    driver = current_app.config.get('NEO4J_DRIVER')
    if not driver:
        raise RuntimeError("Neo4j driver not available")
    with driver.session() as session:
        result = session.run(cypher, params or {})
        records = []
        for record in result:
            row = {}
            for key in record.keys():
                value = record[key]
                if hasattr(value, 'items'):
                    row[key] = dict(value.items())
                    row[key]['_labels'] = list(value.labels)
                elif hasattr(value, '__iter__') and not isinstance(value, str):
                    row[key] = list(value)
                else:
                    row[key] = value
            records.append(row)
        return records


def _serialize_value(v):
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    return v


def _get_node_props(node_data):
    if not node_data:
        return {}
    result = {}
    for k, v in node_data.items():
        if k.startswith('_'):
            continue
        result[k.lower()] = _serialize_value(v)
    return result


def _search_keyword(keyword):
    kw_lower = keyword.lower()

    stp = flgl_status_cypher_param()
    eq_records = _run_cypher(
        "MATCH (e:Equipment) WHERE toLower(e.Name) CONTAINS $kw AND (e).Status IN $flgl_enabled_status "
        "RETURN e ORDER BY e.Name LIMIT 50",
        {**stp, 'kw': kw_lower}
    )
    equipments = [_get_node_props(r.get('e', {})) for r in eq_records]

    proj_records = _run_cypher(
        "MATCH (p:TestProject) WHERE toLower(p.Name) CONTAINS $kw AND (p).Status IN $flgl_enabled_status "
        "RETURN p ORDER BY p.Name LIMIT 50",
        {**stp, 'kw': kw_lower}
    )
    projects = filter_displayable_nodes([_get_node_props(r.get('p', {})) for r in proj_records])

    ind_records = _run_cypher(
        "MATCH (i:Indicator) WHERE toLower(i.Name) CONTAINS $kw AND (i).Status IN $flgl_enabled_status "
        "RETURN i ORDER BY i.Name LIMIT 50",
        {**stp, 'kw': kw_lower}
    )
    indicators = filter_displayable_nodes([_get_node_props(r.get('i', {})) for r in ind_records])

    return {
        'equipments': equipments,
        'projects': projects,
        'indicators': indicators,
    }


def _get_equipment_data(eq_id):
    stp = flgl_status_cypher_param()
    eq_records = _run_cypher(
        "MATCH (e:Equipment {Id: $eqId}) WHERE (e).Status IN $flgl_enabled_status RETURN e",
        {**stp, 'eqId': eq_id}
    )
    if not eq_records:
        return None
    equipment = _get_node_props(eq_records[0].get('e', {}))

    project_nodes = build_project_nodes_for_equipment(
        [eq_id], run_cypher=_run_cypher, get_node_props=_get_node_props
    )
    projects_by_env = group_project_nodes_by_env(project_nodes)
    indicators = build_indicator_nodes_for_equipment(
        [eq_id], run_cypher=_run_cypher, get_node_props=_get_node_props
    )

    return {
        'equipment': equipment,
        'projects': project_nodes,
        'projectsByEnv': projects_by_env,
        'indicators': indicators,
    }


def _get_project_data_with_traceback(proj_id):
    stp = flgl_status_cypher_param()
    proj_records = _run_cypher(
        "MATCH (p:TestProject {Id: $projId}) WHERE (p).Status IN $flgl_enabled_status RETURN p",
        {**stp, 'projId': proj_id}
    )
    if not proj_records:
        return None
    project = _get_node_props(proj_records[0].get('p', {}))
    if not is_displayable_hierarchy_type(project.get('type')):
        return None

    env_id, env_name, _env_names = resolve_project_env_info(
        project, run_cypher=_run_cypher, get_node_props=_get_node_props, proj_id=proj_id
    )

    ind_records = _run_cypher(
        """
        MATCH (p:TestProject {Id: $projId})-[:CON]->(i:Indicator)
        WHERE (p).Status IN $flgl_enabled_status AND (i).Status IN $flgl_enabled_status
        RETURN DISTINCT i ORDER BY i.Name
        """,
        {**stp, 'projId': proj_id}
    )
    indicators = filter_displayable_nodes([_get_node_props(r.get('i', {})) for r in ind_records])

    eq_records = _run_cypher(
        """
        MATCH (eq:Equipment)-[:CON]->(p:TestProject {Id: $projId})
        WHERE (eq).Status IN $flgl_enabled_status AND (p).Status IN $flgl_enabled_status
        RETURN DISTINCT eq ORDER BY eq.Name
        """,
        {**stp, 'projId': proj_id}
    )
    equipments = [_get_node_props(r.get('eq', {})) for r in eq_records]
    if not equipments:
        return None

    eq_ids = [eq.get('id') for eq in equipments if eq.get('id')]
    project_nodes = build_project_nodes_for_equipment(
        eq_ids, run_cypher=_run_cypher, get_node_props=_get_node_props
    )
    projects_by_env = group_project_nodes_by_env(project_nodes)

    return {
        'project': project,
        'envName': env_name,
        'envId': env_id,
        'indicators': indicators,
        'equipments': equipments,
        'projectsByEnv': projects_by_env,
    }


def _get_indicator_data_with_traceback(ind_id):
    stp = flgl_status_cypher_param()
    ind_records = _run_cypher(
        "MATCH (i:Indicator {Id: $indId}) WHERE (i).Status IN $flgl_enabled_status RETURN i",
        {**stp, 'indId': ind_id}
    )
    if not ind_records:
        return None
    indicator = _get_node_props(ind_records[0].get('i', {}))
    if not is_displayable_hierarchy_type(indicator.get('type')):
        return None

    project_nodes = build_project_nodes_for_indicator(
        ind_id, run_cypher=_run_cypher, get_node_props=_get_node_props
    )
    projects_by_env = group_project_nodes_by_env(project_nodes)

    eq_records = _run_cypher(
        """
        MATCH (eq:Equipment)-[:CON]->(p:TestProject)-[:CON]->(i:Indicator {Id: $indId})
        WHERE (eq).Status IN $flgl_enabled_status
          AND (p).Status IN $flgl_enabled_status
          AND (i).Status IN $flgl_enabled_status
        RETURN DISTINCT eq ORDER BY eq.Name
        """,
        {**stp, 'indId': ind_id}
    )
    if not eq_records:
        for rel_type in ['CON', 'BELONGS_TO']:
            eq_records = _run_cypher(
                f"""MATCH (eq:Equipment)-[:{rel_type}]->(i:Indicator {{Id: $indId}})
                WHERE (eq).Status IN $flgl_enabled_status AND (i).Status IN $flgl_enabled_status
                RETURN DISTINCT eq ORDER BY eq.Name""",
                {**stp, 'indId': ind_id}
            )
            if eq_records:
                break
    if not eq_records:
        eq_records = _run_cypher(
            """
            MATCH (eq:Equipment)-[:CON]->(p:TestProject)-[:CON]->(i:Indicator {Id: $indId})
            WHERE (eq).Status IN $flgl_enabled_status
              AND (p).Status IN $flgl_enabled_status
              AND (i).Status IN $flgl_enabled_status
            RETURN DISTINCT eq ORDER BY eq.Name
            """,
            {**stp, 'indId': ind_id}
        )
    equipments = [_get_node_props(r.get('eq', {})) for r in eq_records]

    return {
        'indicator': indicator,
        'projects': project_nodes,
        'projectsByEnv': projects_by_env,
        'equipments': equipments,
    }


def _build_full_graph(search_results, table_rows):
    nodes = []
    edges = []
    seen_nodes = {}
    seen_edges = set()
    node_props_cache = {}

    def add_node(node_id, label, group, props=None):
        if node_id and node_id not in seen_nodes:
            seen_nodes[node_id] = True
            node_props_cache[node_id] = props or {}
            nodes.append({
                'id': node_id,
                'label': label or '',
                'group': group,
                'properties': props or {},
            })

    def add_edge(from_id, to_id, rel_type):
        edge_id = f"{from_id}__{to_id}__{rel_type}"
        if not from_id or not to_id or edge_id in seen_edges:
            return
        seen_edges.add(edge_id)
        edges.append({
            'id': edge_id,
            'from': from_id,
            'to': to_id,
            'label': rel_type,
        })

    matched_ids = set()
    for eq in search_results.get('equipments', []):
        matched_ids.add(eq.get('id', ''))
    for proj in search_results.get('projects', []):
        matched_ids.add(proj.get('id', ''))
    for ind in search_results.get('indicators', []):
        matched_ids.add(ind.get('id', ''))
    matched_ids.discard('')

    env_cache = {}

    def get_env_props(env_id, env_name):
        if env_id in env_cache:
            return env_cache[env_id]
        try:
            stp2 = flgl_status_cypher_param()
            rec = _run_cypher(
                "MATCH (env:Environment {Id: $envId}) WHERE (env).Status IN $flgl_enabled_status RETURN env",
                {**stp2, 'envId': env_id}
            )
            if rec:
                props = _get_node_props(rec[0].get('env', {}))
            else:
                props = {'id': env_id, 'name': env_name}
        except Exception:
            props = {'id': env_id, 'name': env_name}
        env_cache[env_id] = props
        return props

    for row in table_rows:
        row_type = row.get('type', '')

        if row_type == 'equipment':
            eq_id = row.get('equipmentId', '')
            eq_name = row.get('equipmentName', '')
            if not eq_id:
                continue

            eq_data = row.get('equipments', [{}])[0] if row.get('equipments') else {}
            add_node(eq_id, eq_name, 'Equipment', eq_data)

            projects_by_env = row.get('projectsByEnv', {})
            all_projects = []
            for env_projs in projects_by_env.values():
                all_projects.extend(env_projs)

            for proj in all_projects:
                proj_id = proj.get('id', '')
                proj_name = proj.get('name', '')
                add_node(proj_id, proj_name, 'TestProject', proj)
                add_edge(eq_id, proj_id, 'CON')

                children = proj.get('children', [])
                for child in children:
                    child_id = child.get('id', '')
                    child_name = child.get('name', '')
                    add_node(child_id, child_name, 'TestProject', child)
                    add_edge(proj_id, child_id, 'HAS')

                env_id = proj.get('environmentid', '')
                env_name = proj.get('_env', '')
                if env_id and env_name:
                    env_props = get_env_props(env_id, env_name)
                    add_node(env_id, env_name, 'Environment', env_props)
                    add_edge(env_id, proj_id, 'CON')

            apply_indicator_forest_to_graph(
                eq_id,
                row.get('indicators', []),
                add_node=add_node,
                add_edge=add_edge,
            )

        elif row_type == 'project':
            proj_id = row.get('projectId', '')
            proj_name = row.get('projectName', '')
            if not proj_id:
                continue

            proj_data = row.get('projectsByEnv', {}).get(row.get('envName', ''), [{}])[0] if row.get('projectsByEnv') else {}
            add_node(proj_id, proj_name, 'TestProject', proj_data)

            eqs = row.get('equipments', [])
            for eq in eqs:
                eq_id = eq.get('id', '')
                eq_name = eq.get('name', '')
                add_node(eq_id, eq_name, 'Equipment', eq)
                add_edge(eq_id, proj_id, 'CON')

            env_id = row.get('envId', '')
            env_name = row.get('envName', '')
            if env_id:
                env_props = get_env_props(env_id, env_name)
                add_node(env_id, env_name, 'Environment', env_props)
                add_edge(env_id, proj_id, 'CON')

            apply_indicator_forest_to_graph(
                proj_id,
                row.get('indicators', []),
                add_node=add_node,
                add_edge=add_edge,
            )

        elif row_type == 'indicator':
            ind_id = row.get('indicatorId', '')
            if not ind_id:
                continue

            eqs = row.get('equipments', [])
            for eq in eqs:
                eq_id = eq.get('id', '')
                eq_name = eq.get('name', '')
                add_node(eq_id, eq_name, 'Equipment', eq)
                apply_indicator_forest_to_graph(
                    eq_id,
                    row.get('indicators', []),
                    add_node=add_node,
                    add_edge=add_edge,
                )

            projects_by_env = row.get('projectsByEnv', {})
            for env_projs in projects_by_env.values():
                for proj in env_projs:
                    proj_id = proj.get('id', '')
                    proj_name = proj.get('name', '')
                    add_node(proj_id, proj_name, 'TestProject', proj)
                    add_edge(proj_id, ind_id, 'CON')

                    children = proj.get('children', [])
                    for child in children:
                        child_id = child.get('id', '')
                        child_name = child.get('name', '')
                        add_node(child_id, child_name, 'TestProject', child)
                        add_edge(proj_id, child_id, 'HAS')

                    env_id = proj.get('environmentid', '')
                    env_name = proj.get('_env', '')
                    if env_id and env_name:
                        env_props = get_env_props(env_id, env_name)
                        add_node(env_id, env_name, 'Environment', env_props)
                        add_edge(env_id, proj_id, 'CON')

                    for eq in eqs:
                        add_edge(eq.get('id', ''), proj_id, 'CON')

    if not nodes:
        return {'nodes': [], 'edges': []}

    all_proj_ids = [n['id'] for n in nodes if n['group'] == 'TestProject']
    all_ind_ids = [n['id'] for n in nodes if n['group'] == 'Indicator']

    if all_ind_ids:
        stp_ind = flgl_status_cypher_param()
        has_records = _run_cypher(
            """
            MATCH (p:Indicator)-[:HAS]->(c:Indicator)
            WHERE (p).Status IN $flgl_enabled_status
              AND (c).Status IN $flgl_enabled_status
              AND (p.Id IN $ids OR c.Id IN $ids)
            RETURN p, c
            """,
            {**stp_ind, 'ids': all_ind_ids[:200]},
        )
        for r in has_records:
            p_data = _get_node_props(r.get('p', {}))
            c_data = _get_node_props(r.get('c', {}))
            pid = p_data.get('id', '')
            cid = c_data.get('id', '')
            if pid and cid:
                add_node(pid, p_data.get('name', ''), 'Indicator', p_data)
                add_node(cid, c_data.get('name', ''), 'Indicator', c_data)
                add_edge(pid, cid, 'HAS')

    method_nodes = set()
    actual_method_edges = []

    stp3 = flgl_status_cypher_param()
    if all_proj_ids or all_ind_ids:
        if all_proj_ids and all_ind_ids:
            cypher = """
            MATCH (p:TestProject)-[:CON]->(m:TestMethod)
            WHERE p.Id IN $projIds
              AND (p).Status IN $flgl_enabled_status
              AND (m).Status IN $flgl_enabled_status
            WITH p, m
            OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
            WHERE (f) IS NULL OR (f).Status IN $flgl_enabled_status
            RETURN p.Id AS source_id, m, collect(DISTINCT f) AS files, 'project' AS source_type
            UNION
            MATCH (i:Indicator)-[:CON]->(m:TestMethod)
            WHERE i.Id IN $indIds
              AND (i).Status IN $flgl_enabled_status
              AND (m).Status IN $flgl_enabled_status
            WITH i, m
            OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
            WHERE (f) IS NULL OR (f).Status IN $flgl_enabled_status
            RETURN i.Id AS source_id, m, collect(DISTINCT f) AS files, 'indicator' AS source_type
            """
            records = _run_cypher(cypher, {
                'projIds': all_proj_ids[:100],
                'indIds': all_ind_ids[:100],
                **stp3,
            })
        elif all_proj_ids:
            cypher = """
            MATCH (p:TestProject)-[:CON]->(m:TestMethod)
            WHERE p.Id IN $projIds
              AND (p).Status IN $flgl_enabled_status
              AND (m).Status IN $flgl_enabled_status
            OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
            WHERE (f) IS NULL OR (f).Status IN $flgl_enabled_status
            RETURN p.Id AS source_id, m, collect(DISTINCT f) AS files, 'project' AS source_type
            ORDER BY m.Name
            """
            records = _run_cypher(cypher, {**stp3, 'projIds': all_proj_ids[:100]})
        else:
            cypher = """
            MATCH (i:Indicator)-[:CON]->(m:TestMethod)
            WHERE i.Id IN $indIds
              AND (i).Status IN $flgl_enabled_status
              AND (m).Status IN $flgl_enabled_status
            OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
            WHERE (f) IS NULL OR (f).Status IN $flgl_enabled_status
            RETURN i.Id AS source_id, m, collect(DISTINCT f) AS files, 'indicator' AS source_type
            ORDER BY m.Name
            """
            records = _run_cypher(cypher, {**stp3, 'indIds': all_ind_ids[:100]})

        method_file_cache = {}
        for r in records:
            m_data = _get_node_props(r.get('m', {}))
            m_id = m_data.get('id', '')
            m_name = m_data.get('name', '')
            source_id = r.get('source_id', '')

            if m_id and m_id not in seen_nodes:
                seen_nodes[m_id] = True
                if m_data.get('source_id'):
                    m_data['source_name'] = _get_source_name(m_data['source_id'])
                node_props_cache[m_id] = m_data
                nodes.append({
                    'id': m_id,
                    'label': m_name,
                    'group': 'TestMethod',
                    'properties': m_data,
                })
                method_file_cache[m_id] = [_get_node_props(f) for f in r.get('files', []) if f]
            elif m_id and m_id in method_file_cache:
                existing_files = method_file_cache[m_id]
                new_files = [_get_node_props(f) for f in r.get('files', []) if f]
                existing_ids = {f.get('id') for f in existing_files}
                for nf in new_files:
                    if nf.get('id') not in existing_ids:
                        existing_files.append(nf)
                        existing_ids.add(nf.get('id'))

            if source_id:
                actual_method_edges.append((source_id, m_id))
            method_nodes.add(m_id)

        for m_id in method_nodes:
            files = method_file_cache.get(m_id, [])
            for f_data in files:
                f_id = f_data.get('id', '')
                f_name = f_data.get('name', '')
                if f_id and f_id not in seen_nodes:
                    seen_nodes[f_id] = True
                    node_props_cache[f_id] = f_data
                    nodes.append({
                        'id': f_id,
                        'label': f_name or f_id,
                        'group': 'File',
                        'properties': f_data,
                    })
                add_edge(m_id, f_id, 'CON')

        for source_id, m_id in actual_method_edges:
            add_edge(source_id, m_id, 'CON')

    for nid in nodes:
        nid['_matched'] = nid['id'] in matched_ids

    return {'nodes': nodes, 'edges': edges}


@ssyq_bp.route('/search', methods=['POST'])
def search():
    try:
        data = request.json or {}
        keyword = data.get('keyword', '').strip()
        if not keyword:
            return jsonify({"success": False, "error": "请输入搜索关键词"}), 400

        search_results = _search_keyword(keyword)

        table_rows = []

        for eq in search_results['equipments']:
            eq_id = eq.get('id', '')
            row_data = _get_equipment_data(eq_id)
            if row_data:
                table_rows.append({
                    'type': 'equipment',
                    'equipmentId': eq_id,
                    'equipmentName': eq.get('name', ''),
                    'projectsByEnv': row_data['projectsByEnv'],
                    'indicators': row_data['indicators'],
                    'equipments': [eq],
                    'envId': '',
                    'envName': '',
                })

        for proj in search_results['projects']:
            proj_id = proj.get('id', '')
            row_data = _get_project_data_with_traceback(proj_id)
            if row_data and row_data.get('envName'):
                env_id = row_data.get('envId', '') or row_data['project'].get('environmentid', '')
                table_rows.append({
                    'type': 'project',
                    'projectId': proj_id,
                    'projectName': proj.get('name', ''),
                    'envName': row_data['envName'],
                    'envId': env_id,
                    'indicators': row_data['indicators'],
                    'equipments': row_data['equipments'],
                    'projectsByEnv': row_data.get('projectsByEnv') or {row_data['envName']: [row_data['project']]},
                })

        for ind in search_results['indicators']:
            ind_id = ind.get('id', '')
            row_data = _get_indicator_data_with_traceback(ind_id)
            if row_data:
                table_rows.append({
                    'type': 'indicator',
                    'indicatorId': ind_id,
                    'indicatorName': ind.get('name', ''),
                    'projectsByEnv': row_data.get('projectsByEnv', {}),
                    'equipments': row_data.get('equipments', []),
                    'indicators': [ind],
                    'envId': '',
                    'envName': '',
                })

        graph_data = _build_full_graph(search_results, table_rows)

        return jsonify({
            "success": True,
            "data": {
                "searchResults": search_results,
                "tableRows": table_rows,
                "graphData": graph_data,
            }
        })
    except Exception as e:
        logger.error(f"搜索出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@ssyq_bp.route('/methods', methods=['POST'])
def get_methods():
    """与 JQCX POST /methods 一致：项目/指标 → TestMethod，含文件与相关度排序。"""
    try:
        from ..methods_shared import build_methods_by_project_and_indicators

        data = request.json or {}
        project_ids = data.get('projectIds', [])
        indicator_ids = data.get('indicatorIds', [])
        nodes = build_methods_by_project_and_indicators(
            project_ids,
            indicator_ids,
            run_cypher=_run_cypher,
            get_node_props=_get_node_props,
            get_source_name=_get_source_name,
        )
        return jsonify({"success": True, "data": nodes, "total": len(nodes)})
    except Exception as e:
        logger.error(f"方法查询出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
