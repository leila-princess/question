"""
项目无关联试验方法时：反查装备/环境、向量检索后 LLM 辅助生成三段式方法正文。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from .business_catalog import BIZ_METHOD_CONTENT, BIZ_PROJECT_TO_METHOD
from .business_expand import _expand_rel_one_hop_steps, expand_testproject_method_keywords
from .executor import execute_meta_steps, run_chroma_vector_search
from .formatters import method_nodes_have_content
from .user_biz_catalog import UBIZ_TEST_METHOD

logger = logging.getLogger(__name__)

_PROJECT_METHOD_LIST_ROUTE_IDS = frozenset(
    {
        "R-TEST_METHOD-by_project",
        "R-TEST_METHOD-by_indicator",
        "R-TEST_METHOD-by_equipment_via_project",
        "R-TEST_METHOD-by_equipment_via_indicator",
        "R-C-PROJECT_THEN_METHOD-default",
        "R-C-INDICATOR_THEN_METHOD-default",
    }
)

_UBIZ_TEST_METHOD_SCENARIOS = frozenset(
    {
        "by_project",
        "by_indicator",
        "by_equipment_via_project",
        "by_equipment_via_indicator",
        "default",
        "",
    }
)

_PROJECT_FROM_QUESTION_RE = re.compile(
    r"([^\s，,。；;？?！!「」\"'【】\[\]（）()]{2,40}?)(?:的)?(?:试验|测试)方法",
    re.I,
)


def _node_labels(row: Dict[str, Any], side: str) -> List[str]:
    node = row.get(side) if isinstance(row, dict) else None
    if not isinstance(node, dict):
        return []
    labs = node.get("labels")
    return list(labs) if isinstance(labs, list) else []


def _node_name(row: Dict[str, Any], side: str, label: str = "") -> str:
    node = row.get(side) if isinstance(row, dict) else None
    if not isinstance(node, dict):
        return ""
    props = node.get("properties") if isinstance(node.get("properties"), dict) else node
    if not isinstance(props, dict):
        return ""
    return str(props.get("Name") or props.get("name") or "").strip()


def project_method_rows_empty(neo_rows: List[Dict[str, Any]]) -> bool:
    """图库检索结果中是否无任何 TestMethod 节点。"""
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        for side in ("m", "n"):
            if "TestMethod" in _node_labels(row, side):
                nm = _node_name(row, side)
                if nm:
                    return False
    return True


def _collect_names(neo_rows: List[Dict[str, Any]], label: str) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        for side in ("m", "n"):
            if label in _node_labels(row, side):
                nm = _node_name(row, side)
                if nm and nm not in seen:
                    seen.add(nm)
                    out.append(nm)
    return out


def enrich_project_context_for_method_generation(
    driver: Any,
    chroma_collection: Any,
    project_names: List[str],
    *,
    question: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按项目反查装备与环境，并做向量检索补充。"""
    neo_extra: List[Dict[str, Any]] = []
    chroma_extra: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    for pname in project_names:
        pname = str(pname or "").strip()
        if not pname:
            continue
        or_kws, _ = expand_testproject_method_keywords(pname)
        vl = or_kws if or_kws else [pname]
        rel_specs = (
            {
                "from_type": "Equipment",
                "to_type": "TestProject",
                "rel_type": "CON",
                "where_side": "to",
                "match_mode": "fuzzy_any",
                "value_list": vl,
            },
            {
                "from_type": "Environment",
                "to_type": "TestProject",
                "rel_type": "CON",
                "where_side": "to",
                "match_mode": "fuzzy_any",
                "value_list": vl,
            },
        )
        for spec in rel_specs:
            steps.extend(_expand_rel_one_hop_steps(spec))
        if steps:
            rows, _, _ = execute_meta_steps(driver, None, steps)
            neo_extra.extend(rows or [])
            steps = []

        equip_names = _collect_names(neo_extra, "Equipment")
        chroma_q = " ".join(
            x
            for x in (
                pname,
                " ".join(equip_names[:6]),
                "试验方法",
                "试验条件",
                "试验过程",
                "结果评定",
            )
            if x
        ).strip()
        if chroma_collection and chroma_q:
            try:
                hits = run_chroma_vector_search(chroma_collection, chroma_q[:500], top_k=8)
                chroma_extra.extend(hits or [])
            except Exception as e:
                logger.warning("[ZNWD4] project method fallback chroma failed: %s", e)
    return neo_extra, chroma_extra


def merge_retrieval_for_project_method_fallback(
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    neo_extra: List[Dict[str, Any]],
    chroma_extra: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    merged_neo = list(neo_rows or []) + [r for r in (neo_extra or []) if isinstance(r, dict)]
    seen_chroma: set = set()
    merged_chroma: List[Dict[str, Any]] = []
    for row in list(chroma_rows or []) + list(chroma_extra or []):
        if not isinstance(row, dict) or row.get("error"):
            continue
        key = str(row.get("doc_id") or row.get("id") or row.get("title") or id(row))
        if key in seen_chroma:
            continue
        seen_chroma.add(key)
        merged_chroma.append(row)
    return merged_neo, merged_chroma


def project_names_from_params(params: Optional[Dict[str, Any]]) -> List[str]:
    p = params or {}
    kws = p.get("project_keywords") if isinstance(p.get("project_keywords"), list) else []
    kws = [str(x).strip() for x in kws if str(x).strip()]
    if kws:
        return kws
    name = str(p.get("project_name") or p.get("focus") or "").strip()
    return [name] if name else []


def _extract_project_names_from_question(question: str) -> List[str]:
    q = (question or "").strip()
    if not q:
        return []
    m = _PROJECT_FROM_QUESTION_RE.search(q)
    if not m:
        return []
    name = re.sub(r"\s+", "", (m.group(1) or "").strip())
    name = re.sub(r"^(请问|查询|查找|告诉我)", "", name)
    name = re.sub(r"(有哪些|是什么|都有什么|包括哪些)$", "", name)
    return [name] if len(name) >= 2 else []


def resolve_project_names(
    params: Optional[Dict[str, Any]],
    *,
    question: str = "",
    keyword: str = "",
) -> List[str]:
    """从 reconcile params、keyword 或问句中解析试验项目名称。"""
    names = project_names_from_params(params)
    if names:
        return names
    kw = str(keyword or "").strip()
    if kw:
        kw = re.sub(r"(的)?(?:试验|测试)方法$", "", kw).strip()
        if len(kw) >= 2:
            return [kw]
    return _extract_project_names_from_question(question)


def indicator_names_from_params(params: Optional[Dict[str, Any]]) -> List[str]:
    p = params or {}
    kws = p.get("indicator_keywords") if isinstance(p.get("indicator_keywords"), list) else []
    kws = [str(x).strip() for x in kws if str(x).strip()]
    if kws:
        return kws
    name = str(p.get("indicator_name") or p.get("focus") or "").strip()
    return [name] if name else []


def resolve_method_draft_focus_names(
    params: Optional[Dict[str, Any]],
    *,
    scenario: str = "",
    question: str = "",
    keyword: str = "",
) -> List[str]:
    """按场景解析「新增为试验方法」草稿所聚焦的项目或指标名称。"""
    sc = (scenario or "").strip() or "default"
    if sc in ("by_indicator", "by_equipment_via_indicator"):
        names = indicator_names_from_params(params)
        if names:
            return names
        kw = re.sub(r"(的)?(?:试验|测试)方法$", "", str(keyword or "").strip()).strip()
        return [kw] if len(kw) >= 2 else []
    return resolve_project_names(params, question=question, keyword=keyword)


def is_project_method_list_query(
    *,
    business_id: str = "",
    user_biz: str = "",
    route_id: str = "",
    scenario: str = "",
) -> bool:
    bid = (business_id or "").strip()
    if bid == BIZ_PROJECT_TO_METHOD:
        return True
    ub = (user_biz or "").strip()
    sc = (scenario or "").strip() or "default"
    if ub == UBIZ_TEST_METHOD and sc in _UBIZ_TEST_METHOD_SCENARIOS:
        return True
    rid = (route_id or "").strip()
    if rid in _PROJECT_METHOD_LIST_ROUTE_IDS:
        return True
    return False


def is_method_content_create_query(
    business_id: str,
    neo_rows: List[Dict[str, Any]],
) -> bool:
    """图库方法节点无正文时，允许据 LLM 答复新增试验方法。"""
    return (business_id or "").strip() == BIZ_METHOD_CONTENT and not method_nodes_have_content(neo_rows)


def should_offer_method_draft(
    *,
    business_id: str,
    user_biz: str,
    route_id: str,
    scenario: str,
    neo_rows: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]],
    question: str = "",
    keyword: str = "",
) -> Tuple[bool, List[str]]:
    """
    图库无可用试验方法正文/清单时，是否进入「生成草稿 + 新增为试验方法」流程。
  返回 (activate, project_or_method_focus_names)。
    """
    if is_method_content_create_query(business_id, neo_rows):
        p = params or {}
        method_nm = str(p.get("method_name") or "").strip()
        if method_nm:
            focus = method_nm
        else:
            focus = str(
                p.get("project_name")
                or p.get("focus")
                or keyword
                or ""
            ).strip()
            focus = re.sub(r"(的)?(?:试验|测试)方法$", "", focus).strip()
            if not focus:
                focus = (_extract_project_names_from_question(question) or [""])[0]
        return (bool(focus), [focus] if focus else [])

    if not is_project_method_list_query(
        business_id=business_id,
        user_biz=user_biz,
        route_id=route_id,
        scenario=scenario,
    ):
        return False, []
    if not project_method_rows_empty(neo_rows):
        return False, []
    names = resolve_method_draft_focus_names(
        params,
        scenario=scenario,
        question=question,
        keyword=keyword,
    )
    return (bool(names), names)


def enrich_and_merge_for_method_draft(
    driver: Any,
    chroma_collection: Any,
    *,
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    focus_names: List[str],
    question: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    neo_extra, chroma_extra = enrich_project_context_for_method_generation(
        driver,
        chroma_collection,
        focus_names,
        question=question,
    )
    return merge_retrieval_for_project_method_fallback(
        neo_rows, chroma_rows, neo_extra, chroma_extra
    )


def compress_project_method_generate_context(
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    *,
    project_names: List[str],
    question: str = "",
    limit: int = 8000,
) -> str:
    parts: List[str] = [
        "[任务] 图库中未找到该项目关联的试验方法，请据下列装备、环境与检索资料撰写试验方法正文。",
        f"[用户问题] {question}",
        f"[试验项目] {', '.join(project_names) or '未指定'}",
        "",
    ]
    equip = _collect_names(neo_rows, "Equipment")
    envs = _collect_names(neo_rows, "Environment")
    if equip:
        parts.append("[关联装备]\n" + "\n".join(f"- {n}" for n in equip[:40]))
    if envs:
        parts.append("\n[关联试验环境]\n" + "\n".join(f"- {n}" for n in envs[:20]))
    for row in chroma_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        title = row.get("title") or row.get("doc_id") or ""
        prev = str(row.get("content") or "")[:600]
        if prev.strip():
            parts.append(f"\n[文档片段] {title}\n{prev}")
    blob = "\n".join(parts)
    return blob[:limit] if len(blob) > limit else blob


def _node_id(row: Dict[str, Any], side: str) -> str:
    node = row.get(side) if isinstance(row, dict) else None
    if not isinstance(node, dict):
        return ""
    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    if isinstance(props, dict):
        nid = str(props.get("Id") or props.get("id") or "").strip()
        if nid:
            return nid
    return str(node.get("__elementId") or "").strip()


def _collect_entities(neo_rows: List[Dict[str, Any]], label: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen: set = set()
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        for side in ("m", "n"):
            if label not in _node_labels(row, side):
                continue
            nid = _node_id(row, side)
            nm = _node_name(row, side)
            key = nid or nm
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({"id": nid or nm, "name": nm or nid})
    return out


def _name_matches_keyword(name: str, keyword: str) -> bool:
    nm = re.sub(r"\s+", "", (name or "")).lower()
    kw = re.sub(r"\s+", "", (keyword or "")).lower()
    if not nm or not kw:
        return False
    cores = {kw}
    if kw.endswith("测试项目"):
        cores.add(kw[:-4])
    elif kw.endswith("试验项目"):
        cores.add(kw[:-4])
    elif kw.endswith("测试"):
        cores.add(kw[:-2])
    elif kw.endswith("试验"):
        cores.add(kw[:-2])
    for core in cores:
        if core and (core in nm or nm in core):
            return True
    return False


def _match_entities_from_keywords(
    keywords: List[str],
    neo_entities: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    matched: List[Dict[str, str]] = []
    seen: set = set()
    for kw in keywords:
        kw = str(kw or "").strip()
        if not kw:
            continue
        hit = False
        for ent in neo_entities:
            if _name_matches_keyword(ent.get("name") or "", kw):
                key = ent.get("id") or ent.get("name") or ""
                if key and key not in seen:
                    seen.add(key)
                    matched.append(ent)
                    hit = True
        if not hit:
            matched.append({"id": "", "name": kw})
    return matched


def default_method_label(project_name: str) -> str:
    """方法标识默认：项目核心名去空格与常见标点。"""
    s = re.sub(r"\s+", "", (project_name or "").strip())
    s = re.sub(r"[\\/:*?\"<>|，,。；;（）()【】\[\]]+", "", s)
    s = re.sub(r"(试验|测试)项目$", "", s)
    return s[:64] if s else "方法"


def extract_graph_entities_from_neo_rows(
    neo_rows: List[Dict[str, Any]],
    *,
    project_keywords: Optional[List[str]] = None,
    indicator_keywords: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    proj_kws = [str(x).strip() for x in (project_keywords or []) if str(x).strip()]
    ind_kws = [str(x).strip() for x in (indicator_keywords or []) if str(x).strip()]
    neo_projects = _collect_entities(neo_rows, "TestProject")
    neo_indicators = _collect_entities(neo_rows, "Indicator")
    neo_equipments = _collect_entities(neo_rows, "Equipment")
    projects = _match_entities_from_keywords(proj_kws, neo_projects) if proj_kws else neo_projects
    indicators = _match_entities_from_keywords(ind_kws, neo_indicators) if ind_kws else []
    return {
        "equipments": neo_equipments,
        "projects": projects,
        "indicators": indicators,
    }


def attach_method_create_action(
    done_payload: Dict[str, Any],
    *,
    question: str,
    plan: Optional[Dict[str, Any]],
    rec: Optional[Dict[str, Any]],
    neo_rows: List[Dict[str, Any]],
    full_answer: str,
    project_method_generate_mode: bool,
) -> Dict[str, Any]:
    """在 done 事件中附带「新增为试验方法」草稿（若已生成正文）。"""
    out = dict(done_payload)
    if not project_method_generate_mode or not str(full_answer or "").strip():
        return out
    action = build_method_create_action(
        question=question,
        plan=plan,
        rec=rec,
        neo_rows=neo_rows,
        full_answer=full_answer,
    )
    if action:
        out["methodCreateAction"] = action
    return out


def build_method_create_action(
    *,
    question: str,
    plan: Optional[Dict[str, Any]],
    rec: Optional[Dict[str, Any]],
    neo_rows: List[Dict[str, Any]],
    full_answer: str,
) -> Optional[Dict[str, Any]]:
    """图库无方法且 LLM 已生成正文时，构造分类管理新增方法草稿。"""
    answer = str(full_answer or "").strip()
    if not answer:
        return None
    p = rec.get("params") if isinstance(rec, dict) and isinstance(rec.get("params"), dict) else {}
    route_id = str(
        (rec or {}).get("route_id")
        or (plan or {}).get("route_id")
        or "R-TEST_METHOD-by_project"
    ).strip()
    scenario = str((rec or {}).get("scenario") or "").strip()
    keyword = str((rec or {}).get("keyword") or (plan or {}).get("keyword") or "")
    proj_names = resolve_method_draft_focus_names(
        p,
        scenario=scenario,
        question=question,
        keyword=keyword,
    )
    if not proj_names:
        method_nm = str(p.get("method_name") or p.get("focus") or "").strip()
        if method_nm:
            proj_names = [re.sub(r"(的)?(?:试验|测试)方法$", "", method_nm).strip() or method_nm]
    if not proj_names:
        return None
    ind_kws = p.get("indicator_keywords") if isinstance(p.get("indicator_keywords"), list) else []
    relations = extract_graph_entities_from_neo_rows(
        neo_rows,
        project_keywords=proj_names,
        indicator_keywords=ind_kws if route_id == "R-TEST_METHOD-by_indicator" else [],
    )
    primary = proj_names[0]
    method_name = f"{primary}试验方法"
    plan_source = str((plan or {}).get("source") or "").strip().lower()
    draft_source = "znwd6" if plan_source == "znwd6" else "znwd4"
    return {
        "enabled": True,
        "label": "新增为试验方法",
        "href": "/FLGL?tab=method&autoOpen=1",
        "draft": {
            "source": draft_source,
            "question": question,
            "routeId": route_id,
            "method": {
                "name": method_name,
                "label": default_method_label(primary),
                "description": "",
                "synrMarkdown": answer,
            },
            "relations": relations,
        },
    }
