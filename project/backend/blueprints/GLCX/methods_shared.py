"""
GLCX 共用：按选中项目 / 指标查询关联试验方法（与 JQCX POST /methods 逻辑一致）。

SSYQ、JQCX 等页面复用，避免 Cypher 与排序规则分叉。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List

from blueprints.GLCX.indicator_hierarchy import expand_indicator_ids_to_leaf_ids
from utils.neo4j_flgl_status import flgl_status_cypher_param

# Neo4j 5+：静态写 r.Rate / r.Score 等若目录中无该键会报 01N52；用 keys(r) 只读实际存在的属性。
_CYPHER_CON_REL_RATE = """toFloat(CASE
  WHEN 'rate' IN keys(r) THEN r.rate
  WHEN 'Rate' IN keys(r) THEN r.Rate
  WHEN 'score' IN keys(r) THEN r.score
  WHEN 'Score' IN keys(r) THEN r.Score
  ELSE null
END)"""


def _normalize_graph_rate(raw: Any) -> float:
    """将 Neo4j / HTTP 返回的相关度标量规范为有限 float，避免 JSON null 或 NaN 导致前端无关联度。"""
    if raw is None:
        return 0.0
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return 0.0
        try:
            v = float(s)
        except ValueError:
            return 0.0
    else:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return 0.0
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return v


def _method_linked_files_cypher(
    *,
    context_vars: str,
    include_source_id_files: bool,
) -> str:
    """方法关联文件：默认 BELONGS_TO + 方法 Source_id；JYGZ 等场景可仅保留 BELONGS_TO。"""
    head = f"""
            OPTIONAL MATCH (f1:File)-[:BELONGS_TO]->(m)
            WHERE f1 IS NULL OR (f1).Status IN $flgl_enabled_status
            WITH {context_vars}, collect(DISTINCT f1) AS belongs_files"""
    if include_source_id_files:
        return head + f"""
            OPTIONAL MATCH (f2:File)
            WHERE m.Source_id IS NOT NULL AND f2.Id = m.Source_id
              AND (f2).Status IN $flgl_enabled_status
            WITH {context_vars}, belongs_files, collect(DISTINCT f2) AS source_files
            """
    return head + "\n            "


def _method_files_return_expr(*, include_source_id_files: bool) -> str:
    if include_source_id_files:
        return "[x IN belongs_files + source_files WHERE x IS NOT NULL] AS files"
    return "[x IN belongs_files WHERE x IS NOT NULL] AS files"


def build_methods_by_project_and_indicators(
    project_ids: List[Any],
    indicator_ids: List[Any],
    *,
    run_cypher: Callable[..., List[Dict[str, Any]]],
    get_node_props: Callable[[Any], Dict[str, Any]],
    get_source_name: Callable[[str], str],
    files_include_source_id: bool = True,
    populate_source_name: bool = True,
) -> List[Dict[str, Any]]:
    """
    根据 projectIds、indicatorIds 查询 TestMethod，合并项目/指标两条路径，
    附带 BELONGS_TO（及可选 Source_id）文件、related_projects / related_indicators、max_rate，按相关度排序。
    """
    project_ids = [x for x in (project_ids or []) if x]
    indicator_ids = [x for x in (indicator_ids or []) if x]
    indicator_ids = expand_indicator_ids_to_leaf_ids(indicator_ids, run_cypher=run_cypher)

    if not project_ids and not indicator_ids:
        return []

    stp = flgl_status_cypher_param()
    method_data: Dict[str, Dict[str, Any]] = {}

    files_return = _method_files_return_expr(include_source_id_files=files_include_source_id)

    if project_ids:
        # 无向 CON：兼容 (项目)-[:CON]->(方法) 与历史/异常数据下 (方法)-[:CON]->(项目)。
        # CON 边相关度：兼容 rate/Rate/score/Score，且避免引用目录中不存在的键（见 _CYPHER_CON_REL_RATE）。
        files_block = _method_linked_files_cypher(
            context_vars="m, p, r",
            include_source_id_files=files_include_source_id,
        )
        cypher = f"""
            MATCH (p:TestProject)-[r:CON]-(m:TestMethod)
            WHERE p.Id IN $projectIds
              AND (p).Status IN $flgl_enabled_status
              AND (m).Status IN $flgl_enabled_status
            {files_block}
            RETURN m,
                   p.Id AS project_id,
                   p.Name AS project_name,
                   {_CYPHER_CON_REL_RATE} AS project_rate,
                   {files_return}
            """
        records = run_cypher(cypher, {**stp, 'projectIds': project_ids})

        for r in records:
            method = get_node_props(r.get('m', {}))
            method_id = method.get('id', '')

            if method_id not in method_data:
                method_data[method_id] = {
                    'method': method,
                    'project_relations': [],
                    'indicator_relations': [],
                    'files': [],
                }

            project_id = r.get('project_id')
            project_name = r.get('project_name')
            project_rate = _normalize_graph_rate(r.get('project_rate'))

            if project_id:
                method_data[method_id]['project_relations'].append({
                    'id': str(project_id),
                    'name': str(project_name) if project_name else str(project_id),
                    'rate': project_rate,
                })

            for f in r.get('files', []):
                if not f:
                    continue
                file_info = get_node_props(f)
                fid = file_info.get('id', '')
                if fid and not any(
                    x.get('id') == fid for x in method_data[method_id]['files']
                ):
                    method_data[method_id]['files'].append(file_info)

    if indicator_ids:
        files_block = _method_linked_files_cypher(
            context_vars="m, i, r",
            include_source_id_files=files_include_source_id,
        )
        cypher = f"""
            MATCH (i:Indicator)-[r:CON]-(m:TestMethod)
            WHERE i.Id IN $indicatorIds
              AND (i).Status IN $flgl_enabled_status
              AND (m).Status IN $flgl_enabled_status
            {files_block}
            RETURN m,
                   i.Id AS indicator_id,
                   i.Name AS indicator_name,
                   {_CYPHER_CON_REL_RATE} AS indicator_rate,
                   {files_return}
            """
        records = run_cypher(cypher, {**stp, 'indicatorIds': indicator_ids})

        for r in records:
            method = get_node_props(r.get('m', {}))
            method_id = method.get('id', '')

            if method_id not in method_data:
                method_data[method_id] = {
                    'method': method,
                    'project_relations': [],
                    'indicator_relations': [],
                    'files': [],
                }

            indicator_id = r.get('indicator_id')
            indicator_name = r.get('indicator_name')
            indicator_rate = _normalize_graph_rate(r.get('indicator_rate'))

            if indicator_id:
                method_data[method_id]['indicator_relations'].append({
                    'id': str(indicator_id),
                    'name': str(indicator_name) if indicator_name else str(indicator_id),
                    'rate': indicator_rate,
                })

            for f in r.get('files', []):
                if not f:
                    continue
                file_info = get_node_props(f)
                fid = file_info.get('id', '')
                if fid and not any(
                    x.get('id') == fid for x in method_data[method_id]['files']
                ):
                    method_data[method_id]['files'].append(file_info)

    nodes: List[Dict[str, Any]] = []
    for _method_id, data_item in method_data.items():
        method = data_item['method']

        if populate_source_name and method.get('source_id'):
            method['source_name'] = get_source_name(method['source_id'])

        if data_item['files']:
            unique_files: Dict[str, Dict[str, Any]] = {}
            for f in data_item['files']:
                file_id = f.get('id', '')
                if file_id and file_id not in unique_files:
                    unique_files[file_id] = f
            method['files'] = list(unique_files.values())

        if data_item['project_relations']:
            unique_projects: Dict[str, Dict[str, Any]] = {}
            for proj in data_item['project_relations']:
                proj_id = proj.get('id', '')
                if not proj_id:
                    continue
                try:
                    new_r = float(proj.get('rate', 0) or 0)
                except (TypeError, ValueError):
                    new_r = 0.0
                old = unique_projects.get(proj_id)
                if old is None:
                    unique_projects[proj_id] = proj
                else:
                    try:
                        old_r = float(old.get('rate', 0) or 0)
                    except (TypeError, ValueError):
                        old_r = 0.0
                    if new_r > old_r:
                        unique_projects[proj_id] = proj
            method['related_projects'] = sorted(
                list(unique_projects.values()),
                key=lambda x: float(x.get('rate', 0)),
                reverse=True,
            )
        else:
            method['related_projects'] = []

        if data_item['indicator_relations']:
            unique_indicators: Dict[str, Dict[str, Any]] = {}
            for ind in data_item['indicator_relations']:
                ind_id = ind.get('id', '')
                if not ind_id:
                    continue
                try:
                    new_r = float(ind.get('rate', 0) or 0)
                except (TypeError, ValueError):
                    new_r = 0.0
                old = unique_indicators.get(ind_id)
                if old is None:
                    unique_indicators[ind_id] = ind
                else:
                    try:
                        old_r = float(old.get('rate', 0) or 0)
                    except (TypeError, ValueError):
                        old_r = 0.0
                    if new_r > old_r:
                        unique_indicators[ind_id] = ind
            method['related_indicators'] = sorted(
                list(unique_indicators.values()),
                key=lambda x: float(x.get('rate', 0)),
                reverse=True,
            )
        else:
            method['related_indicators'] = []

        max_rate = 0.0
        for proj in method.get('related_projects', []):
            rate = proj.get('rate', 0)
            try:
                rate = float(rate) if rate is not None else 0.0
            except (ValueError, TypeError):
                rate = 0.0
            if rate > max_rate:
                max_rate = rate
        for ind in method.get('related_indicators', []):
            rate = ind.get('rate', 0)
            try:
                rate = float(rate) if rate is not None else 0.0
            except (ValueError, TypeError):
                rate = 0.0
            if rate > max_rate:
                max_rate = rate

        method['max_rate'] = max_rate

        has_project = len(method['related_projects']) > 0
        has_indicator = len(method['related_indicators']) > 0
        if has_project and has_indicator:
            method['_relationType'] = 'both'
        elif has_project:
            method['_relationType'] = 'project-only'
        else:
            method['_relationType'] = 'indicator-only'

        nodes.append(method)

    nodes.sort(key=lambda x: (-float(x.get('max_rate', 0)), x.get('name', '')))
    return nodes
