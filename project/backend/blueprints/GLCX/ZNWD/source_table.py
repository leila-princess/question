"""
智能问答「来源」抽屉表格：仅用与 SSYQ /search 相同的 _search_keyword(keyword) 展开 tableRows。
keyword 由 V4 检索规划（LLM + 规则）产出，经 SSE search_keywords 下发，不在前端展示。
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _labels_props(hit: Dict[str, Any]):
    labels = hit.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    labels = [str(x) for x in labels]
    props = hit.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    return labels, props


def _node_id(props: Dict[str, Any]) -> str:
    """图业务主键：仅用 Id / id，不用 Name（避免重名）。"""
    return str(props.get("Id") or props.get("id") or "").strip()


def _as_flat_node(obj: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(obj, dict) or obj.get("error"):
        return None
    labels = obj.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    labels = [str(x) for x in labels if x]
    props = obj.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    if not labels:
        return None
    return {"labels": labels, "properties": props}


# 单条命中内解析节点时的类型优先级（Equipment 最先用于定位设备）
_LABEL_RANK_ORDER: Tuple[str, ...] = (
    "Equipment",
    "TestProject",
    "Indicator",
    "TestMethod",
    "Environment",
    "EquipmentType",
    "File",
)


def _label_rank(labels: List[str]) -> int:
    for i, lb in enumerate(_LABEL_RANK_ORDER):
        if lb in labels:
            return i
    return 99


def _ordered_entities_from_neo_hit(hit: Dict[str, Any]) -> List[Tuple[List[str], Dict[str, Any]]]:
    """从一条 Neo 命中（rowKind / n,m,r / 扁平）抽出 (labels, properties)，并按类型优先级排序。"""
    out: List[Tuple[List[str], Dict[str, Any]]] = []
    if not isinstance(hit, dict) or hit.get("error"):
        return out
    rk = hit.get("rowKind")
    if rk == "EquipmentToTestProject":
        for slot in ("equipment", "testProject"):
            o = _as_flat_node(hit.get(slot))
            if o:
                out.append((o["labels"], o["properties"]))
    elif rk == "EquipmentToIndicator":
        for slot in ("equipment", "indicator"):
            o = _as_flat_node(hit.get(slot))
            if o:
                out.append((o["labels"], o["properties"]))
    elif rk == "EquipmentProjectTestMethod":
        for slot in ("equipment", "testProject", "testMethod"):
            o = _as_flat_node(hit.get(slot))
            if o:
                out.append((o["labels"], o["properties"]))
    elif rk == "EnvironmentToTestProject":
        for slot in ("environment", "testProject"):
            o = _as_flat_node(hit.get(slot))
            if o:
                out.append((o["labels"], o["properties"]))
    else:
        for k in ("n", "m", "r"):
            if k in hit:
                o = _as_flat_node(hit[k])
                if o:
                    out.append((o["labels"], o["properties"]))
        if hit.get("labels") is not None or isinstance(hit.get("labels"), str):
            o = _as_flat_node(hit)
            if o:
                out.append((o["labels"], o["properties"]))
    out.sort(key=lambda x: _label_rank(x[0]))
    return out


def build_table_rows_from_neo4j_hits(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from blueprints.GLCX.SSYQ.routes import (
        _get_equipment_data,
        _get_project_data_with_traceback,
        _get_indicator_data_with_traceback,
    )

    table_rows: List[Dict[str, Any]] = []
    seen_eq: set = set()
    seen_proj: set = set()
    seen_ind: set = set()

    for hit in nodes or []:
        if not isinstance(hit, dict) or hit.get("error"):
            continue
        labels, props = _labels_props(hit)
        nid = _node_id(props)
        if not nid:
            continue

        if "Equipment" in labels:
            if nid in seen_eq:
                continue
            row_data = _get_equipment_data(nid)
            if row_data:
                seen_eq.add(nid)
                eq = {"id": nid, "name": str(props.get("Name") or props.get("name") or nid)}
                table_rows.append({
                    "type": "equipment",
                    "equipmentId": nid,
                    "equipmentName": eq.get("name", ""),
                    "projectsByEnv": row_data["projectsByEnv"],
                    "indicators": row_data["indicators"],
                    "equipments": [eq],
                    "envId": "",
                    "envName": "",
                })

        if "TestProject" in labels:
            if nid in seen_proj:
                continue
            row_data = _get_project_data_with_traceback(nid)
            if row_data and row_data.get("envName"):
                seen_proj.add(nid)
                proj = {"id": nid, "name": str(props.get("Name") or props.get("name") or nid)}
                table_rows.append({
                    "type": "project",
                    "projectId": nid,
                    "projectName": proj["name"],
                    "envName": row_data["envName"],
                    "envId": row_data.get("envId", "") or row_data["project"].get("environmentid", ""),
                    "indicators": row_data["indicators"],
                    "equipments": row_data["equipments"],
                    "projectsByEnv": {row_data["envName"]: [row_data["project"]]},
                })

        if "Indicator" in labels:
            if nid in seen_ind:
                continue
            row_data = _get_indicator_data_with_traceback(nid)
            if row_data:
                seen_ind.add(nid)
                ind = {"id": nid, "name": str(props.get("Name") or props.get("name") or nid)}
                table_rows.append({
                    "type": "indicator",
                    "indicatorId": nid,
                    "indicatorName": ind["name"],
                    "projectsByEnv": row_data.get("projectsByEnv", {}),
                    "equipments": row_data.get("equipments", []),
                    "indicators": [ind],
                    "envId": "",
                    "envName": "",
                })

    return table_rows


def _assemble_table_rows_from_search_results(search_results: Dict[str, Any]) -> List[Dict[str, Any]]:
    from blueprints.GLCX.SSYQ.routes import (
        _get_equipment_data,
        _get_project_data_with_traceback,
        _get_indicator_data_with_traceback,
    )

    table_rows: List[Dict[str, Any]] = []

    for eq in search_results.get("equipments") or []:
        eq_id = eq.get("id", "")
        if not eq_id:
            continue
        row_data = _get_equipment_data(eq_id)
        if row_data:
            table_rows.append({
                "type": "equipment",
                "equipmentId": eq_id,
                "equipmentName": eq.get("name", ""),
                "projectsByEnv": row_data["projectsByEnv"],
                "indicators": row_data["indicators"],
                "equipments": [eq],
                "envId": "",
                "envName": "",
            })

    for proj in search_results.get("projects") or []:
        proj_id = proj.get("id", "")
        if not proj_id:
            continue
        row_data = _get_project_data_with_traceback(proj_id)
        if row_data and row_data.get("envName"):
            env_id = row_data.get("envId", "") or row_data["project"].get("environmentid", "")
            table_rows.append({
                "type": "project",
                "projectId": proj_id,
                "projectName": proj.get("name", ""),
                "envName": row_data["envName"],
                "envId": env_id,
                "indicators": row_data["indicators"],
                "equipments": row_data["equipments"],
                "projectsByEnv": {row_data["envName"]: [row_data["project"]]},
            })

    for ind in search_results.get("indicators") or []:
        ind_id = ind.get("id", "")
        if not ind_id:
            continue
        row_data = _get_indicator_data_with_traceback(ind_id)
        if row_data:
            table_rows.append({
                "type": "indicator",
                "indicatorId": ind_id,
                "indicatorName": ind.get("name", ""),
                "projectsByEnv": row_data.get("projectsByEnv", {}),
                "equipments": row_data.get("equipments", []),
                "indicators": [ind],
                "envId": "",
                "envName": "",
            })

    return table_rows


def _znwd_split_table_keywords(keyword: str) -> List[str]:
    return [p.strip() for p in re.split(r"[、,，\s]+", (keyword or "").strip()) if p.strip()][:5]


def normalize_znwd_table_keyword(keyword: str) -> str:
    """
    将整句问法或过长关键词规范为 SSYQ 可用的短实体词（与 V4/V5 规划器一致）。
    支持「坦克、命中率」等多段，顿号分隔保留。
    """
    from .services_v4 import _extract_ssyq_keyword_from_question

    kw = (keyword or "").strip()
    if not kw:
        return ""
    if "、" in kw or "," in kw or "，" in kw:
        parts = re.split(r"[、,，]+", kw)
        normed: List[str] = []
        seen: set = set()
        for p in parts:
            short = (_extract_ssyq_keyword_from_question(p) or p.strip())[:24]
            if len(short) >= 2 and short not in seen:
                seen.add(short)
                normed.append(short)
        return "、".join(normed)[:80] if normed else ""
    short = (_extract_ssyq_keyword_from_question(kw) or "").strip()
    return (short[:60] if short else kw[:60]).strip()


def _equipment_names_from_neo_hits(hits: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen: set = set()
    for node in flatten_neo4j_hits_to_nodes(hits or []):
        labels, props = _labels_props(node)
        if "Equipment" not in labels:
            continue
        nm = str(props.get("Name") or props.get("name") or "").strip()
        if nm and nm not in seen:
            seen.add(nm)
            names.append(nm)
    return names


def build_znwd_table_keyword(
    question: str,
    neo_hits: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """V6 等未走 V5 规划器时：从问句与 Neo 命中推断表格检索词。"""
    from .services_v4 import _extract_ssyq_keyword_from_question

    q = (question or "").strip()
    base = _extract_ssyq_keyword_from_question(q)
    eq_names = _equipment_names_from_neo_hits(neo_hits or [])
    if base and eq_names:
        matched = [n for n in eq_names if base in n]
        if matched:
            return normalize_znwd_table_keyword(matched[0])
    if base:
        return normalize_znwd_table_keyword(base)
    if len(eq_names) == 1:
        return normalize_znwd_table_keyword(eq_names[0])
    return normalize_znwd_table_keyword(q)


def _filter_table_rows_by_keyword_anchor(
    rows: List[Dict[str, Any]],
    norm_kw: str,
) -> List[Dict[str, Any]]:
    """Neo 命中关联到多台设备时，用问句锚定词收窄到主设备行（如仅「无人机」）。"""
    if not rows or not norm_kw:
        return rows
    anchor = norm_kw.split("、")[0].strip()
    if len(anchor) < 2:
        return rows
    eq_like = [r for r in rows if str(r.get("equipmentTypeId") or r.get("id") or "").strip()]
    if len(eq_like) <= 1:
        return rows
    by_name = [
        r
        for r in rows
        if anchor in str(r.get("equipmentTypeName") or r.get("name") or "")
    ]
    if by_name:
        return by_name
    try:
        from blueprints.GLCX.SSYQ.routes import _search_keyword

        sr = _search_keyword(anchor)
        anchor_ids = {
            str(e.get("id") or "").strip()
            for e in (sr.get("equipments") or [])
            if e.get("id")
        }
        if anchor_ids:
            filtered = [
                r
                for r in rows
                if str(r.get("equipmentTypeId") or r.get("id") or "").strip() in anchor_ids
            ]
            if filtered:
                return filtered
    except Exception as e:
        logger.warning("ZNWD filter rows by anchor %r: %s", anchor, e)
    return rows


def _znwd_table_keyword_search(keyword: str) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """
    多词关键词（如「坦克、命中率」）：首段锚定设备 SSYQ 命中，后续段在关联到这些设备的
    项目/指标上再匹配，并返回用于表格弱高亮的 id 列表。
    """
    from blueprints.GLCX.SSYQ.routes import (
        _get_indicator_data_with_traceback,
        _get_project_data_with_traceback,
        _search_keyword,
    )

    parts = _znwd_split_table_keywords(keyword)
    if len(parts) <= 1:
        return _search_keyword(parts[0] if parts else ""), {}
    anchor = _search_keyword(parts[0])
    anchor_eq_ids = {str(e.get("id") or "").strip() for e in (anchor.get("equipments") or []) if e.get("id")}
    if not anchor_eq_ids:
        flat = re.sub(r"[、,，]+", " ", (keyword or "").strip())
        return _search_keyword(flat), {}

    merged_eq = {str(e.get("id") or ""): e for e in (anchor.get("equipments") or []) if e.get("id")}
    merged_p: Dict[str, Any] = {}
    merged_i: Dict[str, Any] = {}
    for p in anchor.get("projects") or []:
        pid = str(p.get("id") or "").strip()
        if pid:
            merged_p[pid] = p
    for i in anchor.get("indicators") or []:
        iid = str(i.get("id") or "").strip()
        if iid:
            merged_i[iid] = i
    hi_proj: List[str] = []
    hi_ind: List[str] = []

    def _eq_ids_on_project(pid: str) -> set:
        row = _get_project_data_with_traceback(pid)
        if not row:
            return set()
        return {str(x.get("id") or "").strip() for x in (row.get("equipments") or []) if isinstance(x, dict)}

    def _eq_ids_on_indicator(iid: str) -> set:
        row = _get_indicator_data_with_traceback(iid)
        if not row:
            return set()
        return {str(x.get("id") or "").strip() for x in (row.get("equipments") or []) if isinstance(x, dict)}

    for extra in parts[1:]:
        sr = _search_keyword(extra)
        for ind in sr.get("indicators") or []:
            iid = str(ind.get("id") or "").strip()
            if not iid or iid in merged_i:
                continue
            if _eq_ids_on_indicator(iid) & anchor_eq_ids:
                merged_i[iid] = ind
                hi_ind.append(iid)
        for proj in sr.get("projects") or []:
            pid = str(proj.get("id") or "").strip()
            if not pid or pid in merged_p:
                continue
            if _eq_ids_on_project(pid) & anchor_eq_ids:
                merged_p[pid] = proj
                hi_proj.append(pid)

    out = {
        "equipments": list(merged_eq.values()),
        "projects": list(merged_p.values()),
        "indicators": list(merged_i.values()),
    }
    hints = {
        "equipmentIds": sorted(anchor_eq_ids),
        "projectIds": list(dict.fromkeys(hi_proj)),
        "indicatorIds": list(dict.fromkeys(hi_ind)),
    }
    return out, hints


def flatten_neo4j_hits_to_nodes(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将 V4 rowKind 行、V5 的 n/m 行、扁平节点统一为 {labels, properties} 列表。"""
    out: List[Dict[str, Any]] = []
    seen: set = set()

    def push(n: Optional[Dict[str, Any]]) -> None:
        if not n:
            return
        labels, props = _labels_props(n)
        nid = _node_id(props)
        if not nid:
            return
        key = ("|".join(sorted(labels)), nid)
        if key in seen:
            return
        seen.add(key)
        out.append({"labels": labels, "properties": props})

    rk_slots: Dict[str, tuple] = {
        "EquipmentToTestProject": ("equipment", "testProject"),
        "EquipmentToIndicator": ("equipment", "indicator"),
        "EquipmentProjectTestMethod": ("equipment", "testProject", "testMethod"),
        "EnvironmentToTestProject": ("environment", "testProject"),
    }

    for hit in hits or []:
        if not isinstance(hit, dict) or hit.get("error"):
            continue
        rk = hit.get("rowKind")
        if rk and rk in rk_slots:
            for slot in rk_slots[rk]:
                push(_as_flat_node(hit.get(slot)))
            continue
        for k in ("n", "m", "r"):
            if k in hit:
                push(_as_flat_node(hit[k]))
        if hit.get("labels") is not None or isinstance(hit.get("labels"), str):
            push(_as_flat_node(hit))
    return out


def collect_neo_hit_selection_ids(hits: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """从原始 neo4jHits 提取高亮/选中用 Id；Equipment 优先；TestProject/Indicator 必须有 Id。"""
    equip: List[str] = []
    proj: List[str] = []
    ind: List[str] = []
    meth: List[str] = []
    seen_e: set = set()
    seen_p: set = set()
    seen_i: set = set()
    seen_m: set = set()

    for hit in hits or []:
        for labels, props in _ordered_entities_from_neo_hit(hit):
            nid = _node_id(props)
            if not nid:
                continue
            if "Equipment" in labels and nid not in seen_e:
                seen_e.add(nid)
                equip.append(nid)
            elif "TestProject" in labels and nid not in seen_p:
                seen_p.add(nid)
                proj.append(nid)
            elif "Indicator" in labels and nid not in seen_i:
                seen_i.add(nid)
                ind.append(nid)
            elif "TestMethod" in labels and nid not in seen_m:
                seen_m.add(nid)
                meth.append(nid)
    return {
        "equipmentIds": equip,
        "projectIds": proj,
        "indicatorIds": ind,
        "methodIds": meth,
    }


def _projects_indicators_for_method_ids(method_ids: List[str]) -> Tuple[List[str], List[str]]:
    from blueprints.GLCX.SSYQ.routes import _run_cypher
    from utils.neo4j_flgl_status import flgl_status_cypher_param

    mids = [str(x).strip() for x in (method_ids or []) if str(x).strip()][:80]
    if not mids:
        return [], []
    stp = flgl_status_cypher_param()
    pids: List[str] = []
    iids: List[str] = []
    try:
        recs = _run_cypher(
            """
            UNWIND $mids AS mid
            MATCH (p:TestProject)-[:CON]->(m:TestMethod)
            WHERE m.Id = mid AND (p).Status IN $flgl_enabled_status AND (m).Status IN $flgl_enabled_status
            RETURN DISTINCT p.Id AS pid
            """,
            {**stp, "mids": mids},
        )
        pids = [str(r.get("pid") or "").strip() for r in recs if r.get("pid")]
    except Exception as e:
        logger.warning("ZNWD source/table method->project: %s", e)
    try:
        recs2 = _run_cypher(
            """
            UNWIND $mids AS mid
            MATCH (i:Indicator)-[:CON]->(m:TestMethod)
            WHERE m.Id = mid AND (i).Status IN $flgl_enabled_status AND (m).Status IN $flgl_enabled_status
            RETURN DISTINCT i.Id AS iid
            """,
            {**stp, "mids": mids},
        )
        iids = [str(r.get("iid") or "").strip() for r in recs2 if r.get("iid")]
    except Exception as e:
        logger.warning("ZNWD source/table method->indicator: %s", e)
    return pids, iids


def expand_equipment_ids_from_neo_hits(neo4j_hits: List[Dict[str, Any]]) -> List[str]:
    """优先从命中中的 Equipment 与 TestProject/Indicator 反查设备，合并后去重。"""
    from blueprints.GLCX.SSYQ.routes import (
        _get_project_data_with_traceback,
        _get_indicator_data_with_traceback,
    )

    eq_set: set = set(_collect_equipment_ids_from_neo_hits(neo4j_hits))
    hints = collect_neo_hit_selection_ids(neo4j_hits)
    mp, mi = _projects_indicators_for_method_ids(hints.get("methodIds") or [])
    proj_ids = list(dict.fromkeys((hints.get("projectIds") or []) + mp))
    ind_ids = list(dict.fromkeys((hints.get("indicatorIds") or []) + mi))

    for pid in proj_ids:
        row = _get_project_data_with_traceback(pid)
        if not row:
            continue
        for eq in row.get("equipments") or []:
            eid = str(eq.get("id") or "").strip()
            if eid:
                eq_set.add(eid)

    for iid in ind_ids:
        row = _get_indicator_data_with_traceback(iid)
        if not row:
            continue
        for eq in row.get("equipments") or []:
            eid = str(eq.get("id") or "").strip()
            if eid:
                eq_set.add(eid)

    return sorted(eq_set)


def build_rows_and_selection_from_neo4j_hits(
    neo4j_hits: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    以 neo4j 命中为主：展开为设备整表行，并给出抽屉高亮 / 表格默认选中用的 id 列表。
    """
    hints = collect_neo_hit_selection_ids(neo4j_hits)
    mp, mi = _projects_indicators_for_method_ids(hints.get("methodIds") or [])
    select_projects = list(dict.fromkeys((hints.get("projectIds") or []) + mp))
    select_indicators = list(dict.fromkeys((hints.get("indicatorIds") or []) + mi))

    eq_ids = expand_equipment_ids_from_neo_hits(neo4j_hits)
    rows: List[Dict[str, Any]] = []
    if eq_ids:
        rows = _table_rows_from_equipment_ids(eq_ids)
    if not rows:
        flat = flatten_neo4j_hits_to_nodes(neo4j_hits)
        rows = build_table_rows_from_neo4j_hits(flat)

    selection = {
        "highlightEquipmentIds": hints.get("equipmentIds") or [],
        "highlightProjectIds": hints.get("projectIds") or [],
        "highlightIndicatorIds": hints.get("indicatorIds") or [],
        "highlightMethodIds": hints.get("methodIds") or [],
        "selectProjectIds": select_projects,
        "selectIndicatorIds": select_indicators,
        "selectMethodIds": hints.get("methodIds") or [],
    }
    return rows, selection


def _project_search_blob(node: Dict[str, Any]) -> str:
    parts = [
        str(node.get("name") or ""),
        str(node.get("Name") or ""),
        str(node.get("_env") or ""),
        str(node.get("title") or ""),
    ]
    return " ".join(p for p in parts if p).lower()


def _collect_table_keyword_tokens(keyword: str) -> List[str]:
    kw = (keyword or "").strip()
    if not kw:
        return []
    kw = re.sub(
        r"(的|之|与|和|及|请问|查询|告诉|帮我|一下|哪些|什么|吗|呢|\?|？|有什么|有哪些|测定|验证|检测|测试项目|试验项目|项目)",
        " ",
        kw,
        flags=re.I,
    )
    tokens: List[str] = []
    seen: set = set()
    for part in re.split(r"[、,，\s]+", kw):
        p = part.strip().lower()
        if len(p) < 2:
            continue
        if p in ("方法", "步骤", "操作", "如何", "怎样", "怎么"):
            continue
        if p not in seen:
            seen.add(p)
            tokens.append(p)
    return tokens


def _project_ids_matching_tokens_from_table(table_rows: List[Dict[str, Any]], tokens: List[str]) -> List[str]:
    picked: List[str] = []
    seen: set = set()
    for row in table_rows:
        if row.get("type") != "equipment":
            continue
        pbe = row.get("projectsByEnv") or {}
        if not isinstance(pbe, dict):
            continue
        for _, plist in pbe.items():
            if not isinstance(plist, list):
                continue
            for node in plist:
                if not isinstance(node, dict):
                    continue
                for cand in (node, *list(node.get("children") or [])):
                    if not isinstance(cand, dict):
                        continue
                    cid = str(cand.get("id") or "").strip()
                    if not cid or cid in seen:
                        continue
                    blob = _project_search_blob(cand)
                    if any(t in blob for t in tokens):
                        seen.add(cid)
                        picked.append(cid)
    return picked


def _apply_keyword_project_narrowing(keyword: str, table_rows: List[Dict[str, Any]], selection: Dict[str, Any]) -> None:
    """当 Neo 未给出项目选中或命中过多时，用语义表格关键词收窄 selectProjectIds。"""
    tokens = _collect_table_keyword_tokens(keyword)
    if not tokens or not table_rows:
        return
    matched = _project_ids_matching_tokens_from_table(table_rows, tokens)
    if not matched:
        return
    current = [str(x).strip() for x in (selection.get("selectProjectIds") or []) if str(x).strip()]
    if not current:
        selection["selectProjectIds"] = matched
        hp = [str(x) for x in (selection.get("highlightProjectIds") or []) if x]
        for pid in matched:
            if pid not in hp:
                hp.append(pid)
        selection["highlightProjectIds"] = hp
        return
    if len(current) >= 10:
        cur_set = set(current)
        inter = [p for p in matched if p in cur_set]
        if inter:
            selection["selectProjectIds"] = inter


def _collect_equipment_ids_from_neo_hits(hits: List[Dict[str, Any]]) -> List[str]:
    """从原始命中（含 n/m/r 或 rowKind）提取设备 Id；仅用 Id，不用 Name。"""
    out: List[str] = []
    seen: set = set()
    for hit in hits or []:
        if not isinstance(hit, dict) or hit.get("error"):
            continue
        for labels, props in _ordered_entities_from_neo_hit(hit):
            if "Equipment" not in labels:
                continue
            nid = _node_id(props)
            if nid and nid not in seen:
                seen.add(nid)
                out.append(nid)
    return out


def _table_rows_from_equipment_ids(eq_ids: List[str]) -> List[Dict[str, Any]]:
    from blueprints.GLCX.SSYQ.routes import _get_equipment_data

    ids = [str(x).strip() for x in (eq_ids or []) if str(x).strip()]
    if not ids:
        return []

    def _one_row(eid: str) -> Optional[Dict[str, Any]]:
        row_data = _get_equipment_data(eid)
        if not row_data:
            return None
        eq = row_data.get("equipment") or {}
        eid2 = str(eq.get("id") or eid).strip()
        ename = str(eq.get("name") or eid2).strip()
        return {
            "type": "equipment",
            "equipmentId": eid2,
            "equipmentName": ename,
            "projectsByEnv": row_data["projectsByEnv"],
            "indicators": row_data["indicators"],
            "equipments": [{"id": eid2, "name": ename}],
            "envId": "",
            "envName": "",
        }

    if len(ids) == 1:
        row = _one_row(ids[0])
        return [row] if row else []

    rows: List[Dict[str, Any]] = []
    workers = min(8, len(ids))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut_map = {ex.submit(_one_row, eid): eid for eid in ids}
        for fut in as_completed(fut_map):
            try:
                row = fut.result()
                if row:
                    rows.append(row)
            except Exception as e:
                logger.warning("ZNWD batch equipment row failed id=%r: %s", fut_map[fut], e)
    rows.sort(key=lambda r: str(r.get("equipmentId") or ""))
    return rows


ENVIRONMENT_SOURCE_FIELD_MAP: Tuple[Tuple[str, str], ...] = (
    ("Name", "名称"),
    ("Label", "标签"),
    ("Description", "描述"),
    ("Type", "分类"),
    ("Class", "分级"),
    ("Combination", "典型组合"),
    ("Limit", "约束"),
    ("Request", "要求"),
)


def _environment_prop_get(props: Dict[str, Any], field: str) -> str:
    if not isinstance(props, dict):
        return ""
    for k in (field, field.lower(), field.upper()):
        if k in props and props[k] is not None:
            v = props[k]
            if isinstance(v, (list, dict)):
                return json.dumps(v, ensure_ascii=False)
            return str(v).strip()
    return ""


def _environment_record_for_ui(props: Dict[str, Any]) -> Dict[str, Any]:
    """来源抽屉环境视图：仅中文列名，不暴露 Type/Class 等英文字段名。"""
    rec: Dict[str, Any] = {"id": _node_id(props)}
    for en_key, zh_label in ENVIRONMENT_SOURCE_FIELD_MAP:
        rec[zh_label] = _environment_prop_get(props, en_key) or "—"
    return rec


def _environment_nodes_from_neo_hits(neo_hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for hit in neo_hits or []:
        if not isinstance(hit, dict) or hit.get("error"):
            continue
        rk = hit.get("rowKind")
        if rk == "EnvironmentToTestProject":
            env = hit.get("environment")
            if isinstance(env, dict):
                props = (env.get("properties") if isinstance(env.get("properties"), dict) else env) or {}
                nid = _node_id(props)
                if nid and nid not in seen:
                    seen.add(nid)
                    out.append(props)
            continue
        for node in flatten_neo4j_hits_to_nodes([hit]):
            labels, props = _labels_props(node)
            if "Environment" not in labels:
                continue
            nid = _node_id(props)
            if not nid or nid in seen:
                continue
            seen.add(nid)
            out.append(props)
    return out


def _query_environment_nodes_cypher(
    name_keyword: str = "",
    *,
    list_all: bool = False,
    limit: int = 80,
) -> List[Dict[str, Any]]:
    from blueprints.GLCX.SSYQ.routes import _get_node_props, _run_cypher
    from utils.neo4j_flgl_status import flgl_status_cypher_param

    stp = flgl_status_cypher_param()
    lim = max(1, min(int(limit), 200))
    if list_all or not (name_keyword or "").strip():
        cypher = (
            "MATCH (e:Environment) WHERE e.Status IN $flgl_enabled_status "
            "RETURN e ORDER BY coalesce(e.Name, e.Label, e.Id) LIMIT $limit"
        )
        params = {**stp, "limit": lim}
    else:
        cypher = (
            "MATCH (e:Environment) WHERE e.Status IN $flgl_enabled_status "
            "AND toLower(coalesce(e.Name, '')) CONTAINS $kw "
            "RETURN e ORDER BY coalesce(e.Name, e.Label, e.Id) LIMIT $limit"
        )
        params = {**stp, "kw": (name_keyword or "").strip().lower(), "limit": lim}
    try:
        recs = _run_cypher(cypher, params)
    except Exception as e:
        logger.warning("ZNWD environment source cypher failed: %s", e)
        return []
    out: List[Dict[str, Any]] = []
    for r in recs or []:
        props = _get_node_props(r.get("e", {}))
        if props and _node_id(props):
            out.append(props)
    return out


def znwd_should_list_all_environments(question: str) -> bool:
    text = (question or "").strip()
    if not text:
        return False
    return bool(
        re.search(
            r"(环境|试验环境).*(如何分类|怎样分类|有哪些类型|有哪些环境|分哪几类|分类体系|环境类型|环境体系)",
            text,
        )
        or re.search(r"^(有哪些|列出|枚举).*(环境|试验环境)", text)
    )


def znwd_is_environment_source_view(
    question: str,
    keyword: str,
    neo_hits: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """来源抽屉：环境类问题展示 Environment 节点属性（中文列），非设备×项目表格。"""
    text = f"{question or ''} {keyword or ''}".strip()
    if not text:
        return False
    if re.search(
        r"(环境|试验环境).*(分类|分级|类型|组合|约束|要求|体系)",
        text,
    ) or re.search(
        r"(分类|分级|典型组合|约束|要求).{0,16}(环境|试验环境)",
        text,
    ):
        if not re.search(r"(设备|装备).*(试验项目|测试项目|考核指标)", text):
            return True
    asks_env_attrs = bool(re.search(r"分类|分级|组合|约束|要求|类型|体系", text))
    asks_equipment_table = bool(
        re.search(
            r"(设备|装备).{0,8}(试验项目|测试项目|考核指标|有什么试验|有什么项目|有哪些试验|有哪些项目)",
            text,
        )
        or re.search(
            r"(试验项目|测试项目|考核指标).{0,8}(设备|装备)",
            text,
        )
        or re.search(r"有什么试验项目和考核指标", text)
    )
    has_env_ctx = bool(
        re.search(
            r"环境|试验环境|高温|低温|湿热|淋雨|盐雾|沙尘|振动|高海拔|复杂气象",
            text,
        )
    )
    if has_env_ctx and asks_env_attrs and not asks_equipment_table:
        return True
    env_from_neo = _environment_nodes_from_neo_hits(neo_hits or [])
    if env_from_neo and asks_env_attrs and not asks_equipment_table:
        return True
    return False


def _build_environment_sources(
    question: str,
    keyword: str,
    neo_hits: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    kw = normalize_znwd_table_keyword(keyword) or (keyword or "").strip()
    anchor = kw.split("、")[0].strip() if kw else ""
    props_list = _environment_nodes_from_neo_hits(neo_hits)
    if znwd_should_list_all_environments(question) or (not anchor and not props_list):
        props_list = _query_environment_nodes_cypher(list_all=True)
    elif anchor:
        if not props_list:
            props_list = _query_environment_nodes_cypher(anchor, list_all=False)
        else:
            matched = [
                p
                for p in props_list
                if anchor in _environment_prop_get(p, "Name")
                or anchor in _environment_prop_get(p, "Label")
            ]
            if matched:
                props_list = matched
            else:
                props_list = _query_environment_nodes_cypher(anchor, list_all=False) or props_list
    seen: set = set()
    records: List[Dict[str, Any]] = []
    for props in props_list:
        nid = _node_id(props)
        if not nid or nid in seen:
            continue
        seen.add(nid)
        records.append(_environment_record_for_ui(props))
    return records


def _znwd_asks_equipment_table(question: str, keyword: str) -> bool:
    text = f"{question or ''} {keyword or ''}".strip()
    if not text:
        return False
    return bool(
        re.search(
            r"(设备|装备).{0,8}(试验项目|测试项目|考核指标|有什么试验|有什么项目|有哪些试验|有哪些项目)",
            text,
        )
        or re.search(
            r"(试验项目|测试项目|考核指标).{0,8}(设备|装备)",
            text,
        )
        or re.search(r"有什么试验项目和考核指标", text)
    )


def znwd_is_equipment_hierarchy_view(
    question: str,
    keyword: str,
    neo_hits: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """来源抽屉：装备类型/具体装备问题展示三级列表，非项目×指标表格。"""
    if znwd_is_environment_source_view(question, keyword, neo_hits):
        return False
    if _znwd_asks_equipment_table(question, keyword):
        return False
    text = f"{question or ''} {keyword or ''}".strip()
    if not text:
        return False
    hierarchy_q = bool(
        re.search(
            r"(装备类型|设备类型|一级.*装备|二级.*类型|具体装备|装备类别|装备分类)",
            text,
        )
        or re.search(
            r"(哪些|有哪些|列出|枚举).{0,12}(装备类型|设备类型|二级类型|具体装备|装备)",
            text,
        )
        or re.search(
            r"(地面装备|空中装备|海上装备|水下装备|装甲|坦克类型|飞机类型|舰船类型).{0,12}(有哪些|二级|类型|装备)",
            text,
        )
        or re.search(
            r"(Type|type).{0,8}(下|有哪些|二级|装备)",
            text,
        )
    )
    if hierarchy_q:
        return True
    flat = flatten_neo4j_hits_to_nodes(neo_hits or [])
    has_et = False
    has_eq = False
    has_proj_ind = False
    for node in flat:
        labels, _ = _labels_props(node)
        if "EquipmentType" in labels:
            has_et = True
        if "Equipment" in labels:
            has_eq = True
        if "TestProject" in labels or "Indicator" in labels or "TestMethod" in labels:
            has_proj_ind = True
    if has_et and not has_proj_ind:
        return True
    if has_eq and not has_proj_ind and re.search(r"装备|设备|类型|有哪些|清单", text):
        return True
    return False


def _hierarchy_row(
    level1: str,
    level2_id: str,
    level2_name: str,
    eq_id: str = "",
    eq_name: str = "",
) -> Dict[str, str]:
    return {
        "level1Type": (level1 or "").strip() or "—",
        "level2TypeId": (level2_id or "").strip(),
        "level2TypeName": (level2_name or "").strip() or "—",
        "equipmentId": (eq_id or "").strip(),
        "equipmentName": (eq_name or "").strip(),
    }


def _collect_equipment_type_ids_from_neo_hits(hits: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for node in flatten_neo4j_hits_to_nodes(hits or []):
        labels, props = _labels_props(node)
        if "EquipmentType" not in labels:
            continue
        nid = _node_id(props)
        if nid and nid not in seen:
            seen.add(nid)
            out.append(nid)
    return out


def _et_props_to_row_parts(et_props: Dict[str, Any]) -> Tuple[str, str, str]:
    level1 = str(et_props.get("Type") or et_props.get("type") or "").strip() or "未分类"
    level2_id = _node_id(et_props)
    level2_name = str(et_props.get("Name") or et_props.get("name") or level2_id).strip()
    return level1, level2_id, level2_name


def _resolve_equipment_hierarchy_cypher(
    eq_ids: List[str],
    et_ids: List[str],
    *,
    keyword: str = "",
) -> List[Dict[str, str]]:
    from blueprints.GLCX.SSYQ.routes import _get_node_props, _run_cypher
    from utils.neo4j_flgl_status import flgl_status_cypher_param

    stp = flgl_status_cypher_param()
    rows: List[Dict[str, str]] = []
    seen: set = set()

    def push_row(
        level1: str,
        level2_id: str,
        level2_name: str,
        eq_id: str = "",
        eq_name: str = "",
    ) -> None:
        key = (level2_id, eq_id)
        if key in seen:
            return
        seen.add(key)
        rows.append(_hierarchy_row(level1, level2_id, level2_name, eq_id, eq_name))

    eq_ids = [str(x).strip() for x in (eq_ids or []) if str(x).strip()][:120]
    et_ids = [str(x).strip() for x in (et_ids or []) if str(x).strip()][:120]

    if eq_ids:
        try:
            recs = _run_cypher(
                """
                UNWIND $eqIds AS eid
                MATCH (e:Equipment)
                WHERE e.Id = eid AND e.Status IN $flgl_enabled_status
                OPTIONAL MATCH (e)-[:HAS|CON]->(et:EquipmentType)
                WHERE et.Status IN $flgl_enabled_status
                OPTIONAL MATCH (et_in:EquipmentType)-[:HAS|CON]->(e)
                WHERE et_in.Status IN $flgl_enabled_status
                WITH e, coalesce(et, et_in) AS et
                RETURN e, et
                ORDER BY coalesce(et.Type, ''), coalesce(et.Name, ''), coalesce(e.Name, e.Id)
                """,
                {**stp, "eqIds": eq_ids},
            )
            for r in recs or []:
                e_props = _get_node_props(r.get("e", {}))
                et_props = _get_node_props(r.get("et", {}))
                if not e_props:
                    continue
                eq_id = _node_id(e_props)
                eq_name = str(e_props.get("Name") or e_props.get("name") or eq_id).strip()
                if et_props and _node_id(et_props):
                    l1, l2_id, l2_name = _et_props_to_row_parts(et_props)
                    push_row(l1, l2_id, l2_name, eq_id, eq_name)
                else:
                    push_row("未分类", "", "—", eq_id, eq_name)
        except Exception as e:
            logger.warning("ZNWD hierarchy equipment cypher failed: %s", e)

    if et_ids:
        try:
            recs = _run_cypher(
                """
                UNWIND $etIds AS etid
                MATCH (et:EquipmentType)
                WHERE et.Id = etid AND et.Status IN $flgl_enabled_status
                OPTIONAL MATCH (et)-[:HAS|CON]->(e:Equipment)
                WHERE e.Status IN $flgl_enabled_status
                WITH et, collect(DISTINCT e) AS equipments
                RETURN et, equipments
                ORDER BY coalesce(et.Type, ''), coalesce(et.Name, ''), et.Id
                """,
                {**stp, "etIds": et_ids},
            )
            for r in recs or []:
                et_props = _get_node_props(r.get("et", {}))
                if not et_props:
                    continue
                l1, l2_id, l2_name = _et_props_to_row_parts(et_props)
                eq_list = r.get("equipments") or []
                if eq_list:
                    for eq in eq_list:
                        eq_props = _get_node_props(eq)
                        if not eq_props:
                            continue
                        eq_id = _node_id(eq_props)
                        eq_name = str(eq_props.get("Name") or eq_props.get("name") or eq_id).strip()
                        push_row(l1, l2_id, l2_name, eq_id, eq_name)
                else:
                    push_row(l1, l2_id, l2_name)
        except Exception as e:
            logger.warning("ZNWD hierarchy equipmentType cypher failed: %s", e)

    anchor = (keyword or "").strip()
    if not rows and anchor and len(anchor) >= 2:
        try:
            recs = _run_cypher(
                """
                MATCH (et:EquipmentType)
                WHERE et.Status IN $flgl_enabled_status
                  AND (
                    toLower(coalesce(et.Name, '')) CONTAINS $kw
                    OR toLower(coalesce(et.Type, '')) CONTAINS $kw
                    OR toLower(coalesce(et.Label, '')) CONTAINS $kw
                  )
                OPTIONAL MATCH (et)-[:HAS|CON]->(e:Equipment)
                WHERE e.Status IN $flgl_enabled_status
                WITH et, collect(DISTINCT e) AS equipments
                RETURN et, equipments
                ORDER BY coalesce(et.Type, ''), coalesce(et.Name, ''), et.Id
                LIMIT 80
                """,
                {**stp, "kw": anchor.lower()},
            )
            for r in recs or []:
                et_props = _get_node_props(r.get("et", {}))
                if not et_props:
                    continue
                l1, l2_id, l2_name = _et_props_to_row_parts(et_props)
                eq_list = r.get("equipments") or []
                if eq_list:
                    for eq in eq_list:
                        eq_props = _get_node_props(eq)
                        if not eq_props:
                            continue
                        eq_id = _node_id(eq_props)
                        eq_name = str(eq_props.get("Name") or eq_props.get("name") or eq_id).strip()
                        push_row(l1, l2_id, l2_name, eq_id, eq_name)
                else:
                    push_row(l1, l2_id, l2_name)
        except Exception as e:
            logger.warning("ZNWD hierarchy keyword cypher failed kw=%r: %s", anchor, e)

    return rows


def _search_equipment_hierarchy_by_keyword(keyword: str) -> List[Dict[str, str]]:
    """关键词-only：轻量 Cypher 展开装备层级，不走 SSYQ traceback。"""
    from blueprints.GLCX.SSYQ.routes import _search_keyword

    kw = normalize_znwd_table_keyword(keyword) or (keyword or "").strip()
    anchor = kw.split("、")[0].strip() if kw else ""
    if not anchor:
        return []
    eq_ids: List[str] = []
    try:
        sr = _search_keyword(anchor)
        for eq in sr.get("equipments") or []:
            eid = str(eq.get("id") or "").strip()
            if eid:
                eq_ids.append(eid)
    except Exception as e:
        logger.warning("ZNWD hierarchy keyword search failed kw=%r: %s", anchor, e)
    return _resolve_equipment_hierarchy_cypher(eq_ids, [], keyword=anchor)


def _build_equipment_hierarchy_rows(
    question: str,
    keyword: str,
    neo_hits: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    eq_ids = _collect_equipment_ids_from_neo_hits(neo_hits)
    et_ids = _collect_equipment_type_ids_from_neo_hits(neo_hits)
    rows = _resolve_equipment_hierarchy_cypher(eq_ids, et_ids)
    if not rows:
        kw = normalize_znwd_table_keyword(keyword) or (keyword or "").strip()
        if not kw and question:
            kw = build_znwd_table_keyword(question, neo_hits)
        if kw:
            rows = _search_equipment_hierarchy_by_keyword(kw)
    return rows


def _collect_hierarchy_selection_hints(
    neo_hits: List[Dict[str, Any]],
    hierarchy_rows: List[Dict[str, str]],
) -> Dict[str, List[str]]:
    hints = collect_neo_hit_selection_ids(neo_hits)
    level2_ids: List[str] = []
    seen_l2: set = set()
    for node in flatten_neo4j_hits_to_nodes(neo_hits or []):
        labels, props = _labels_props(node)
        if "EquipmentType" not in labels:
            continue
        nid = _node_id(props)
        if nid and nid not in seen_l2:
            seen_l2.add(nid)
            level2_ids.append(nid)
    eq_ids = list(hints.get("equipmentIds") or [])
    if not eq_ids:
        for row in hierarchy_rows:
            eid = str(row.get("equipmentId") or "").strip()
            if eid and eid not in eq_ids:
                eq_ids.append(eid)
    return {
        "highlightEquipmentIds": eq_ids,
        "highlightLevel2TypeIds": level2_ids,
        "highlightProjectIds": [],
        "highlightIndicatorIds": [],
        "highlightMethodIds": [],
        "selectProjectIds": [],
        "selectIndicatorIds": [],
        "selectMethodIds": [],
    }


def hydrate_source_table_payload(
    keyword: str,
    neo4j_hits: List[Dict[str, Any]],
    user_question: str = "",
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    raw_kw = (keyword or "").strip()
    kw = normalize_znwd_table_keyword(raw_kw) or raw_kw
    neo4j_hits = neo4j_hits or []
    question = (user_question or "").strip()

    if znwd_is_environment_source_view(question, kw, neo4j_hits):
        env_records = _build_environment_sources(question, kw, neo4j_hits)
        hydrate_ms = round((time.perf_counter() - t0) * 1000)
        return {
            "sourceView": "environment",
            "environmentSources": env_records,
            "equipmentHierarchyRows": [],
            "tableRows": [],
            "keywordUsed": kw,
            "keywordNormalizedFrom": raw_kw if raw_kw and raw_kw != kw else "",
            "tableFrom": "environment_nodes" if env_records else "empty",
            "hydrateMs": hydrate_ms,
            "selectionHints": {
                "highlightEquipmentIds": [],
                "highlightLevel2TypeIds": [],
                "highlightProjectIds": [],
                "highlightIndicatorIds": [],
                "highlightMethodIds": [],
                "selectProjectIds": [],
                "selectIndicatorIds": [],
                "selectMethodIds": [],
            },
        }

    if znwd_is_equipment_hierarchy_view(question, kw, neo4j_hits):
        hierarchy_rows = _build_equipment_hierarchy_rows(question, kw, neo4j_hits)
        selection = _collect_hierarchy_selection_hints(neo4j_hits, hierarchy_rows)
        hydrate_ms = round((time.perf_counter() - t0) * 1000)
        return {
            "sourceView": "equipment_hierarchy",
            "environmentSources": [],
            "equipmentHierarchyRows": hierarchy_rows,
            "tableRows": [],
            "keywordUsed": kw,
            "keywordNormalizedFrom": raw_kw if raw_kw and raw_kw != kw else "",
            "tableFrom": "equipment_hierarchy" if hierarchy_rows else "empty",
            "hydrateMs": hydrate_ms,
            "selectionHints": selection,
        }
    rows: List[Dict[str, Any]] = []
    selection: Dict[str, Any] = {
        "highlightEquipmentIds": [],
        "highlightProjectIds": [],
        "highlightIndicatorIds": [],
        "highlightMethodIds": [],
        "selectProjectIds": [],
        "selectIndicatorIds": [],
        "selectMethodIds": [],
    }
    table_from = "empty"

    if neo4j_hits:
        rows, selection = build_rows_and_selection_from_neo4j_hits(neo4j_hits)
        if rows:
            table_from = "neo4j_hits"
            rows = _filter_table_rows_by_keyword_anchor(rows, kw)

    if not rows and kw:
        try:
            sr, kw_hints = _znwd_table_keyword_search(kw)
            rows = _assemble_table_rows_from_search_results(sr)
            if rows:
                table_from = "keyword"
                if kw_hints.get("equipmentIds"):
                    selection["highlightEquipmentIds"] = kw_hints["equipmentIds"]
                if kw_hints.get("projectIds"):
                    selection["highlightProjectIds"] = kw_hints["projectIds"]
                if kw_hints.get("indicatorIds"):
                    selection["highlightIndicatorIds"] = kw_hints["indicatorIds"]
        except Exception as e:
            logger.warning("ZNWD source/table _search_keyword failed kw=%r: %s", kw, e)
            rows = []

    if not rows and neo4j_hits:
        rows = build_table_rows_from_neo4j_hits(flatten_neo4j_hits_to_nodes(neo4j_hits))
        if rows:
            table_from = "neo4j_hits_fallback"

    if not rows and neo4j_hits:
        eq_ids = _collect_equipment_ids_from_neo_hits(neo4j_hits)
        rows = _table_rows_from_equipment_ids(eq_ids)
        if rows:
            table_from = "neo4j_equipment_ids_fallback"

    _apply_keyword_project_narrowing(kw, rows, selection)

    hydrate_ms = round((time.perf_counter() - t0) * 1000)
    return {
        "sourceView": "equipment_table",
        "environmentSources": [],
        "equipmentHierarchyRows": [],
        "tableRows": rows,
        "keywordUsed": kw,
        "keywordNormalizedFrom": raw_kw if raw_kw and raw_kw != kw else "",
        "tableFrom": table_from,
        "hydrateMs": hydrate_ms,
        "selectionHints": selection,
    }
