"""
business_expand：business_id + params → meta_ops steps[]（确定性，无 LLM）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .business_catalog import (
    BIZ_DOCUMENT,
    BIZ_ENUM_NODES,
    BIZ_METHOD_CONTENT,
    BIZ_NODE_KW,
    BIZ_NODE_PROP,
    BIZ_NODE_PROP_REV,
    BIZ_PROJECT_TO_METHOD,
    BIZ_REL_ONE_HOP,
    BIZ_REL_REVERSE_ONE_HOP,
    BIZ_EQUIP_PROJECT_BY_METHOD,
    BIZ_REL_TWO_HOP,
    BIZ_STANDARD_CONDITION,
)
from .enum_inference import node_type_zh
from .meta_ops import NODE_LABELS, RELATION_SCHEMA

_ENV_SUBJECT_KEYWORDS = (
    "高温",
    "低温",
    "湿热",
    "盐雾",
    "淋雨",
    "振动",
    "沙尘",
    "老化",
)


def expand_testproject_method_keywords(kw: str) -> Tuple[List[str], List[str]]:
    k = re.sub(r"\s+", "", (kw or "").strip())
    or_kws: List[str] = []
    must_all: List[str] = []
    if not k:
        return or_kws, must_all
    or_kws.append(k)
    if "测试" in k and k.replace("测试", "试验") not in or_kws:
        or_kws.append(k.replace("测试", "试验"))
    elif "试验" in k and k.replace("试验", "测试") not in or_kws:
        or_kws.append(k.replace("试验", "测试"))
    core = k
    if core.endswith("测试"):
        core = core[:-2]
    elif core.endswith("试验"):
        core = core[:-2]
    for env in _ENV_SUBJECT_KEYWORDS:
        if env in core and len(core) > len(env) + 1:
            prefix = core.split(env, 1)[0]
            if len(prefix) >= 2:
                must_all.extend([prefix, env])
            else:
                must_all.append(env)
            break
    return or_kws, must_all


def _relation_allowed(fr: str, rel: str, to: str) -> bool:
    return (fr, to) in (RELATION_SCHEMA.get(rel) or [])


def _expand_rel_one_hop_steps(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    fr = str(p.get("from_type") or "Equipment").strip()
    to = str(p.get("to_type") or "TestProject").strip()
    rel = str(p.get("rel_type") or "CON").strip()
    focus = str(p.get("focus") or "").strip()
    focus_id = str(p.get("focus_id") or "").strip()
    where_side = str(p.get("where_side") or "from").lower()
    if where_side not in ("from", "to"):
        where_side = "from"
    draft_vl_early = p.get("value_list") if isinstance(p.get("value_list"), list) else []
    draft_vl_early = [str(x).strip() for x in draft_vl_early if str(x).strip()]
    if not _relation_allowed(fr, rel, to) or (not focus and not focus_id and not draft_vl_early):
        return []
    if fr == "EquipmentType" and to == "Equipment" and rel == "HAS" and where_side == "from":
        parent_type = str(p.get("parent_type") or p.get("equipment_type_parent") or "").strip()
        purpose_label = focus or focus_id
        if parent_type and focus:
            purpose_label = f"{parent_type} · {focus}"
        step_params: Dict[str, Any] = {
            "keyword": focus,
            "focus_id": focus_id,
            "limit": 500,
        }
        if p.get("match_all_equipment_types"):
            step_params["match_all_equipment_types"] = True
            step_params.pop("focus_id", None)
        if parent_type:
            step_params["parent_type"] = parent_type
        return [
            {
                "step_id": "s1",
                "op_code": "neo_equipment_under_type",
                "params": step_params,
                "purpose": f"设备类型「{purpose_label}」下全部具体设备",
            }
        ]
    if fr == "Equipment" and to == "Indicator" and rel == "CON" and where_side == "from":
        return [
            {
                "step_id": "s1",
                "op_code": "neo_equipment_indicators",
                "params": {
                    "keyword": focus,
                    "focus_id": focus_id,
                    "limit": 200,
                },
                "purpose": f"设备「{focus or focus_id}」关联考核指标（CON + Equipment_name 兜底）",
            }
        ]
    step_params: Dict[str, Any] = {
        "from_type": fr,
        "rel_type": rel,
        "to_type": to,
        "limit": 200,
        "where_side": where_side,
    }
    anchor_field = "Name"
    draft_vl = p.get("value_list") if isinstance(p.get("value_list"), list) else []
    draft_vl = [str(x).strip() for x in draft_vl if str(x).strip()]
    if focus_id:
        step_params["match_mode"] = "in"
        step_params["from_field"] = "Id"
        step_params["value_list"] = [focus_id]
    elif len(draft_vl) > 1 or str(p.get("match_mode") or "").lower() == "fuzzy_any":
        step_params["match_mode"] = "fuzzy_any"
        step_params["from_field"] = anchor_field
        step_params["value_list"] = draft_vl if draft_vl else ([focus] if focus else [])
        if not step_params["value_list"]:
            return []
    else:
        step_params["match_mode"] = "fuzzy"
        step_params["from_field"] = anchor_field
        step_params["value"] = focus
    if where_side == "to":
        purpose = f"按{to}「{focus or focus_id}」反查{fr}（where_side=to）"
    else:
        purpose = f"{fr}→{to} 关系查询（{focus or focus_id}）"
    return [
        {
            "step_id": "s1",
            "op_code": "neo_relation_query",
            "params": step_params,
            "purpose": purpose,
        }
    ]


def _step_project_to_method(project_name: str) -> List[Dict[str, Any]]:
    or_kws, _ = expand_testproject_method_keywords(project_name)
    params: Dict[str, Any] = {
        "from_type": "TestProject",
        "rel_type": "CON",
        "to_type": "TestMethod",
        "from_field": "Name",
        "match_mode": "fuzzy_any",
        "where_side": "from",
        "limit": 200,
        "value_list": or_kws if or_kws else [project_name],
    }
    return [
        {
            "step_id": "s1",
            "op_code": "neo_relation_query",
            "params": params,
            "purpose": f"按测试项目「{project_name}」查关联测试方法",
        }
    ]


def _extract_section_prefix(question: str) -> str:
    m = re.search(r"(第\s*\d+(?:\.\d+)*\s*节|第\s*\d+(?:\.\d+)*\s*章|\d+(?:\.\d+)+)", question or "")
    return (m.group(1) or "").strip() if m else ""


def _step_method_by_name(method_name: str) -> List[Dict[str, Any]]:
    from .method_inference import expand_method_standard_keywords

    kws = expand_method_standard_keywords(method_name) or [method_name]
    if len(kws) > 1 or re.search(r"G\s*B\s*/?\s*T", method_name, re.I):
        return [
            {
                "step_id": "s1",
                "op_code": "neo_method_keyword_lookup",
                "params": {"keyword_list": kws, "limit": 20},
                "purpose": f"按标准/方法关键词「{method_name}」查 TestMethod 节点（含 SYNR）",
            }
        ]
    return [
        {
            "step_id": "s1",
            "op_code": "neo_node_query",
            "params": {"node_type": "TestMethod", "field": "Name", "value": method_name, "limit": 20},
            "purpose": f"按方法名「{method_name}」查 TestMethod 节点（含 SYNR）",
        }
    ]


def _step_method_by_id(method_id: str) -> List[Dict[str, Any]]:
    return [
        {
            "step_id": "s1",
            "op_code": "neo_node_query",
            "params": {"node_type": "TestMethod", "field": "Id", "value": method_id, "limit": 5},
            "purpose": f"按 Id「{method_id}」查 TestMethod 节点",
        }
    ]


def expand_method_content(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    p = dict(params or {})
    method_id = str(p.get("method_id") or "").strip()
    method_name = str(p.get("method_name") or "").strip()
    project_name = str(p.get("project_name") or "").strip()
    if method_id:
        return _step_method_by_id(method_id)
    if method_name:
        return _step_method_by_name(method_name)
    if project_name:
        return _step_project_to_method(project_name)
    return []


def expand_method_content_chroma_fallback(query: str) -> List[Dict[str, Any]]:
    q = (query or "").strip()[:500]
    if not q:
        return []
    return [
        {
            "step_id": "s_doc1",
            "op_code": "chroma_vector_search",
            "params": {"query": q, "top_k": 12},
            "purpose": "图库方法字段为空，向量检索标准文档",
        },
        {
            "step_id": "s_doc2",
            "op_code": "chroma_rerank",
            "params": {"query": q, "top_k": 8},
            "purpose": "文档重排序",
        },
    ]


def _expand_equip_project_by_method_steps(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """两步反查：TestProject→TestMethod（锚定方法）→ Equipment→TestProject（引用上步项目 Id）。"""
    from .method_inference import expand_method_standard_keywords

    method_kw = str(p.get("method_keyword") or p.get("keyword") or "").strip()
    method_id = str(p.get("method_id") or "").strip()
    kw_list = p.get("keyword_list") if isinstance(p.get("keyword_list"), list) else []
    if not kw_list and method_kw:
        kw_list = expand_method_standard_keywords(method_kw)
    if not method_kw and not method_id and not kw_list:
        return []

    if not method_id:
        return [
            {
                "step_id": "s1",
                "op_code": "neo_equip_project_by_method",
                "params": {
                    "method_keyword": method_kw,
                    "method_id": "",
                    "keyword_list": kw_list,
                    "limit": 200,
                },
                "purpose": f"按试验方法「{method_kw}」反查设备→项目→方法",
            }
        ]

    step1_params: Dict[str, Any] = {
        "from_type": "TestProject",
        "to_type": "TestMethod",
        "rel_type": "CON",
        "where_side": "to",
        "match_mode": "in",
        "from_field": "Id",
        "value_list": [method_id],
        "limit": 200,
    }
    step2_params: Dict[str, Any] = {
        "from_type": "Equipment",
        "to_type": "TestProject",
        "rel_type": "CON",
        "where_side": "to",
        "match_mode": "in",
        "from_field": "Id",
        "from_values_ref": {"step_id": "s1", "target": "n", "field": "Id"},
        "limit": 200,
    }
    label = method_kw or method_id
    return [
        {
            "step_id": "s1",
            "op_code": "neo_relation_query",
            "params": step1_params,
            "purpose": f"第1跳：测试项目→试验方法（方法 Id={method_id}）",
        },
        {
            "step_id": "s2",
            "op_code": "neo_relation_query",
            "params": step2_params,
            "purpose": f"第2跳：设备→测试项目（引用第1跳项目，方法「{label}」）",
        },
    ]


def expand_business(
    business_id: str,
    params: Dict[str, Any],
    *,
    retrieval_question: str = "",
) -> List[Dict[str, Any]]:
    bid = (business_id or "").strip()
    p = dict(params or {})

    if bid == BIZ_PROJECT_TO_METHOD:
        name = str(p.get("project_name") or p.get("focus") or "").strip()
        if not name:
            return []
        return _step_project_to_method(name)

    if bid == BIZ_METHOD_CONTENT:
        return expand_method_content(p)

    if bid == BIZ_DOCUMENT:
        query = str(p.get("query") or retrieval_question or "").strip()
        sec = str(p.get("section_prefix") or "").strip() or _extract_section_prefix(query)
        std_code = str(p.get("std_code") or "").strip()
        if not std_code:
            try:
                from blueprints.RelFact.znwd_ops import extract_std_code_from_query
                std_code = extract_std_code_from_query(query)
            except Exception:
                std_code = ""
        steps: List[Dict[str, Any]] = []
        if std_code:
            steps.append({
                "step_id": "s0",
                "op_code": "neo_file_by_std",
                "params": {"std_code": std_code, "limit": 5},
                "purpose": f"定位标准文件 {std_code}",
            })
        chroma_params: Dict[str, Any] = {"query": query[:500], "top_k": 12}
        if std_code:
            chroma_params["std_code"] = std_code
        steps.extend([
            {
                "step_id": "s1",
                "op_code": "chroma_vector_search",
                "params": dict(chroma_params),
                "purpose": "文档向量召回",
            },
            {
                "step_id": "s2",
                "op_code": "chroma_rerank",
                "params": {"query": query[:500], "top_k": 8},
                "purpose": "文档重排序",
            },
        ])
        if sec:
            steps.append(
                {
                    "step_id": "s3",
                    "op_code": "chroma_section_expand",
                    "params": {"section_prefix": sec, "max_chunks": 400},
                    "purpose": f"章节扩展 {sec}",
                }
            )
        return steps

    if bid == BIZ_STANDARD_CONDITION:
        query = str(p.get("query") or retrieval_question or "").strip()
        std_code = str(p.get("std_code") or "").strip()
        if not std_code:
            try:
                from blueprints.RelFact.znwd_ops import extract_std_code_from_query
                std_code = extract_std_code_from_query(query)
            except Exception:
                std_code = ""
        steps = [
            {
                "step_id": "s1",
                "op_code": "rel_fact_param_match",
                "params": {"query": query[:500], "top_k": 8, "std_code": std_code},
                "purpose": "标准 fact_index 参数匹配",
            },
        ]
        if std_code:
            steps.insert(0, {
                "step_id": "s0",
                "op_code": "neo_file_by_std",
                "params": {"std_code": std_code, "limit": 3},
                "purpose": f"定位标准 {std_code}",
            })
        return steps

    if bid == BIZ_ENUM_NODES:
        label = str(p.get("node_type") or "EquipmentType").strip()
        if label not in NODE_LABELS:
            label = "EquipmentType"
        op_code = "neo_testproject_list_hierarchical" if label == "TestProject" else "neo_list_nodes"
        return [
            {
                "step_id": "s1",
                "op_code": op_code,
                "params": {"node_type": label, "limit": 300, "status_filter": 1},
                "purpose": f"列举{node_type_zh(label)}",
            }
        ]

    if bid == BIZ_REL_ONE_HOP:
        return _expand_rel_one_hop_steps(p)

    if bid == BIZ_REL_REVERSE_ONE_HOP:
        pr = dict(p)
        pr.setdefault("where_side", "to")
        pr.setdefault("result_type", pr.get("from_type") or "Equipment")
        pr.setdefault("anchor_type", pr.get("to_type") or "TestProject")
        return _expand_rel_one_hop_steps(pr)

    if bid in (BIZ_EQUIP_PROJECT_BY_METHOD, BIZ_REL_TWO_HOP):
        return _expand_equip_project_by_method_steps(p)

    if bid == BIZ_NODE_KW:
        label = str(p.get("node_type") or "Equipment").strip()
        if label not in NODE_LABELS:
            return []
        field = str(p.get("field") or "Name")
        val = str(p.get("value") or p.get("focus") or "").strip()
        fid = str(p.get("focus_id") or "").strip()
        if fid:
            return [
                {
                    "step_id": "s1",
                    "op_code": "neo_node_query",
                    "params": {"node_type": label, "field": "Id", "value": fid, "limit": 20},
                    "purpose": f"按 Id 定位{node_type_zh(label)}",
                }
            ]
        if not val:
            return []
        return [
            {
                "step_id": "s1",
                "op_code": "neo_node_query",
                "params": {"node_type": label, "field": field, "value": val, "limit": 50},
                "purpose": f"按{field}关键词「{val}」查{node_type_zh(label)}",
            }
        ]

    if bid == BIZ_NODE_PROP_REV:
        label = str(p.get("node_type") or "").strip()
        field = str(p.get("field") or "Type")
        val = str(p.get("value") or "").strip()
        if label not in NODE_LABELS or not val:
            return []
        return [
            {
                "step_id": "s1",
                "op_code": "neo_node_query",
                "params": {"node_type": label, "field": field, "value": val, "limit": 200},
                "purpose": f"按 {field}={val} 筛选{node_type_zh(label)}",
            }
        ]

    if bid == BIZ_NODE_PROP:
        label = str(p.get("node_type") or "").strip()
        if label not in NODE_LABELS:
            return []
        field = str(p.get("field") or "Name")
        val = str(p.get("value") or p.get("focus") or "").strip()
        fid = str(p.get("focus_id") or "").strip()
        step_p: Dict[str, Any] = {"node_type": label, "limit": 20}
        if fid:
            step_p.update({"field": "Id", "value": fid})
        elif val:
            step_p.update({"field": field, "value": val})
            if label == "Environment":
                step_p["match_mode"] = "exact"
        else:
            return []
        return [
            {
                "step_id": "s1",
                "op_code": "neo_node_query",
                "params": step_p,
                "purpose": f"读取{node_type_zh(label)}节点属性",
            }
        ]

    return []
