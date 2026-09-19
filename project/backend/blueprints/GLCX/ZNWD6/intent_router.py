"""基于 Ontology 的意图路由（规则优先，不依赖具体实体名）。"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from .composite_router import detect_composite_template
from .ontology_loader import get_template, load_ontology


def _norm(q: str) -> str:
    return re.sub(r"\s+", "", (q or "").strip())


def _is_global_enum_projects(q: str) -> bool:
    n = _norm(q)
    return bool(
        re.fullmatch(
            r"(有哪些|都有什么|列举|包括哪些|清单|有什么|有啥|有有哪些|有包括哪些|"
            r"试验项目清单|测试项目清单|实验项目清单).*",
            n,
        )
        or re.fullmatch(r"(有哪些|都有什么|列举).*(试验|测试|实验)项目.*", n)
        and not re.search(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}.+(试验|测试|实验)项目", n)
    )


def _has_explicit_equipment_subject(q: str, tau: Dict[str, Any]) -> bool:
    """问句是否指向具体装备（非全局列举）。"""
    n = _norm(q)
    if not n:
        return False
    if re.search(r"^.{2,}的(考核|性能)?指标", n):
        return True
    if re.search(r"^.{2,}有哪些(考核|性能)?指标", n):
        return True
    if re.search(r"^.{2,}有什么(考核|性能)?指标", n):
        return True
    if re.search(r"^.{2,}(的)?(考核|性能)?指标有哪些", n):
        return True
    if re.search(r"^.{2,}的(试验|测试|实验)项目", n):
        return True
    if re.search(r"^.{2,}有哪些(试验|测试|实验)项目", n):
        return True
    if re.search(r"^.{2,}有什么(试验|测试|实验)项目", n):
        return True
    if re.search(r"^.{2,}的(试验|测试)?方法", n):
        return True
    if re.search(r"^.{2,}有哪些(试验|测试)?方法", n):
        return True
    if re.search(r"^.{2,}有哪些指标", n) and not re.search(r"试验项目|测试项目", n):
        return True
    for c in tau.get("flat_candidates") or []:
        if str(c.get("node_type") or "") != "Equipment":
            continue
        name = str(c.get("name") or "").strip()
        if len(name) >= 2 and _norm(name) in n:
            return True
    return False


def _is_global_enum_indicators(q: str) -> bool:
    n = _norm(q)
    if not re.search(r"考核指标|性能指标|指标清单|全部指标|^有哪些指标|^都有什么指标", n):
        return False
    return not _has_explicit_equipment_subject(q, {})


def _is_global_enum_methods(q: str) -> bool:
    n = _norm(q)
    if not re.search(r"试验方法|测试方法|方法清单", n):
        return False
    return not _has_explicit_equipment_subject(q, {})


def _resolve_effective_template_hint(
    question: str,
    tau: Dict[str, Any],
    template_hint: Optional[str] = None,
) -> Optional[str]:
    """问句推断优先于过期的 session template_hint（如上一轮 project、本轮 indicator）。"""
    hint = (template_hint or "").strip()
    inferred = infer_template_hint_from_question(question, tau)
    if not inferred:
        return hint or None
    if not hint:
        return inferred
    if inferred == hint:
        return hint
    # 全局枚举 vs 装备主体
    if hint.startswith("enum_") and not inferred.startswith("enum_"):
        return inferred
    if not hint.startswith("enum_") and inferred.startswith("enum_"):
        if _is_global_enum_indicators(question) or _is_global_enum_projects(question) or _is_global_enum_methods(question):
            return inferred
        return inferred if _has_explicit_equipment_subject(question, tau) else inferred
    # 同族 equip_* 以问句为准（project vs indicator）
    if hint.startswith("equip_") and inferred.startswith("equip_"):
        return inferred
    if hint.startswith("enum_") and inferred.startswith("enum_"):
        return inferred
    return inferred or hint


def infer_template_hint_from_question(question: str, tau: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """从消解后问句推断模板 hint（供 session_context / pipeline 复用）。"""
    q = (question or "").strip()
    if not q:
        return None
    tau = tau or {}
    asks_indicator = bool(
        re.search(r"考核指标|性能指标", q)
        or (re.search(r"指标", q) and not re.search(r"试验项目|测试项目|试验方法|测试方法", q))
    )
    if asks_indicator:
        if re.search(r"装备类型|类别", q):
            return "equip_type_to_indicators"
        if _has_explicit_equipment_subject(q, tau):
            return "equip_to_indicators"
        if _is_global_enum_indicators(q):
            return "enum_indicator"
    if re.search(r"试验方法|测试方法", q) and _has_explicit_equipment_subject(q, tau):
        return "project_to_methods"
    if _is_specific_equip_project(q, tau):
        return "equip_to_projects"
    if _is_global_enum_projects(q):
        return "enum_by_focus_type"
    if _is_global_enum_methods(q):
        return "enum_environment" if re.search(r"环境", q) else "enum_by_focus_type"
    return None


def should_preserve_suggested_template(
    suggested_tid: str,
    reason: str,
    *,
    template_hint: Optional[str] = None,
    question: str = "",
    tau: Optional[Dict[str, Any]] = None,
) -> bool:
    """带装备主体或 session hint 时不应被 LLM 降为 enum_*。"""
    if (template_hint or "").strip():
        return True
    if reason == "session_context_hint":
        return True
    if reason in (
        "equip_indicator",
        "specific_equip_project",
        "equip_env_project",
        "equip_type_indicator",
        "keyword_indicator",
        "type_equipment",
        "enum_override_equip_indicator",
        "enum_override_equip",
    ):
        return True
    if suggested_tid and not suggested_tid.startswith("enum_"):
        return True
    if _has_explicit_equipment_subject(question, tau or {}):
        return True
    return False


def enforce_binding_template(
    binding: Dict[str, Any],
    *,
    suggested_tid: str,
    suggest_reason: str,
    template_hint: Optional[str],
    question: str,
    tau: Dict[str, Any],
) -> Dict[str, Any]:
    """防止 LLM binding 将 equip_* 误改为 enum_*。"""
    out = dict(binding or {})
    cur = str(out.get("template_id") or "").strip()
    locked = _resolve_effective_template_hint(question, tau, template_hint) or ""
    if not locked and should_preserve_suggested_template(
        suggested_tid,
        suggest_reason,
        template_hint=template_hint,
        question=question,
        tau=tau,
    ):
        locked = suggested_tid
    if locked and cur.startswith("enum_") and not locked.startswith("enum_"):
        out["template_id"] = locked
    elif locked and not cur:
        out["template_id"] = locked
    elif cur.startswith("enum_") and not (locked or "").startswith("enum_"):
        inferred = infer_template_hint_from_question(question, tau)
        if inferred and not inferred.startswith("enum_"):
            out["template_id"] = inferred
    bound_types = {str(b.get("node_type") or "") for b in (out.get("bindings") or [])}
    if str(out.get("template_id") or "").startswith("enum_") and "Equipment" in bound_types:
        inferred = infer_template_hint_from_question(question, tau)
        if inferred and not inferred.startswith("enum_"):
            out["template_id"] = inferred
    return out


def _is_specific_equip_project(q: str, tau: Dict[str, Any]) -> bool:
    if not re.search(r"试验项目|测试项目|实验项目", q):
        return False
    if _is_global_enum_projects(q):
        return False
    n = _norm(q)
    if re.search(r".{2,}的(试验|测试|实验)项目", n):
        return True
    if re.search(r".{2,}(有哪些|有什么|包括哪些)(试验|测试|实验)项目", n):
        return True
    if re.search(r"(查询|告诉我|分别|直接说).*(试验|测试|实验)项目", n):
        return True
    has_eq = any(str(c.get("node_type") or "") == "Equipment" for c in (tau.get("flat_candidates") or []))
    return has_eq and re.search(r"试验项目|测试项目", q) and not _is_global_enum_projects(q)


def _is_equip_env_project(q: str) -> bool:
    n = _norm(q)
    return bool(
        re.search(r"环境", n)
        and re.search(r"试验项目|测试项目", n)
        and not re.search(r"环境分类|环境分级|环境属性|环境约束|推荐的环境设置", n)
    )


def _is_doc_overview(q: str) -> bool:
    return bool(re.search(r"主要内容|概述|讲什么|文档概述|标准内容", q or ""))


def _is_doc_section(q: str) -> bool:
    return bool(re.search(r"第.{0,6}[章节]|章节|条文", q or ""))


def _match_intent(question: str) -> str:
    q = (question or "").strip()
    for item in load_ontology().get("intents") or []:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("id") or "")
        for pat in item.get("patterns") or []:
            if pat and re.search(pat, q):
                return iid
    return "graph_default"


def _enum_template_for_question(q: str) -> str:
    if re.search(r"试验环境|环境类型|环境类别", q):
        return "enum_environment"
    if re.search(r"考核指标|指标清单|全部指标", q):
        return "enum_indicator"
    if re.search(r"装备类型|装备类别|二级装备", q):
        return "enum_equipment_type"
    if re.search(r"有哪些装备|装备清单|登记了哪些装备|系统里.*装备", q):
        return "enum_equipment"
    if re.search(r"试验项目|测试项目|实验项目", q):
        return "enum_by_focus_type"
    return "enum_by_focus_type"


def suggest_template(
    question: str,
    tau: Dict[str, Any],
    template_hint: Optional[str] = None,
) -> Tuple[str, str]:
    """返回 (template_id, reason)。"""
    q = (question or "").strip()
    hint = _resolve_effective_template_hint(q, tau, template_hint)
    if hint and get_template(hint):
        return hint, "session_context_hint"

    comp = detect_composite_template(q)
    if comp:
        return comp, "composite"

    if _is_equip_env_project(q):
        return "equip_and_env_to_projects", "equip_env_project"

    if _is_specific_equip_project(q, tau):
        return "equip_to_projects", "specific_equip_project"

    if _is_doc_overview(q):
        return "document_lookup", "doc_overview"
    if _is_doc_section(q):
        return "standard_chapter", "doc_section"
    if re.search(r"试验方案", q):
        return "document_lookup", "doc_plan"

    if re.search(r"考核指标|性能指标", q) and not re.search(r"试验项目|测试项目", q):
        if re.search(r"装备类型|类别", q):
            return "equip_type_to_indicators", "equip_type_indicator"
        if _has_explicit_equipment_subject(q, tau):
            return "equip_to_indicators", "equip_indicator"
        if _is_global_enum_indicators(q):
            return "enum_indicator", "global_enum_indicator"
        return "enum_indicator", "global_enum_indicator"

    if (
        re.search(r"指标", q)
        and not re.search(r"试验项目|测试项目|试验方法|测试方法", q)
        and _has_explicit_equipment_subject(q, tau)
    ):
        return "equip_to_indicators", "equip_indicator_short"

    intent_id = _match_intent(q)
    for item in load_ontology().get("intents") or []:
        if isinstance(item, dict) and item.get("id") == intent_id:
            tid = str(item.get("default_template") or "equip_to_projects")
            if intent_id == "enum_list" and not _is_global_enum_projects(q):
                if _has_explicit_equipment_subject(q, tau):
                    if re.search(r"考核指标|性能指标", q):
                        return "equip_to_indicators", "enum_override_equip_indicator"
                    if _is_specific_equip_project(q, tau):
                        return "equip_to_projects", "enum_override_equip"
            if intent_id == "enum_list":
                tid = _enum_template_for_question(q)
            if intent_id == "enum_indicator" and _has_explicit_equipment_subject(q, tau):
                return "equip_to_indicators", "enum_override_equip_indicator"
            return tid, f"intent={intent_id}"

    types = {str(c.get("node_type") or "") for c in (tau.get("flat_candidates") or [])}
    if "File" in types or re.search(r"标准|GBT|GJB|章节", q):
        if re.search(r"第.{0,6}章|章节|条文", q):
            return "standard_chapter", "type_or_keyword_chapter"
        return "document_lookup", "type_or_keyword_file"
    if "TestMethod" in types and re.search(r"方法|SYNR|试验过程", q):
        return "method_by_project_or_indicator", "method_doc"
    if "Environment" in types:
        if re.search(r"分类|分级|属性|约束", q):
            return "environment_properties", "env_properties"
        return "env_to_projects", "type_environment"
    if "TestProject" in types:
        if re.search(r"标准|文件", q):
            return "standard_for_projects", "project_standards"
        return "project_to_methods", "type_project"
    if "Indicator" in types:
        return "indicator_to_methods", "type_indicator"
    if "EquipmentType" in types:
        if re.search(r"指标|考核", q):
            return "equip_type_to_indicators", "type_equip_type_indicator"
        if re.search(r"有哪些|清单", q):
            return "equipment_under_type", "equip_under_type"
    if "Equipment" in types:
        if re.search(r"指标|考核", q):
            return "equip_to_indicators", "keyword_indicator"
        if re.search(r"环境", q):
            return "equip_and_env_to_projects", "equip_env"
        return "equip_to_projects", "type_equipment"
    if re.search(r"你好|谢谢|你是谁|你能做什么", q):
        return "chitchat", "chitchat"
    return "equip_to_projects", "fallback"


def refine_template_by_binding(
    template_id: str,
    binding: Dict[str, Any],
    template_hint: Optional[str] = None,
    question: str = "",
    tau: Optional[Dict[str, Any]] = None,
) -> str:
    tau = tau or {}
    hint = _resolve_effective_template_hint(question, tau, template_hint)
    if hint and get_template(hint):
        return hint
    tpl = get_template(template_id)
    focus_types = set(tpl.get("focus_types") or [])
    bound_types = {str(b.get("node_type") or "") for b in (binding.get("bindings") or [])}
    if template_id.startswith("composite_"):
        return template_id
    # enum_* 模板 focus_types 为空，不能因 early return 保留枚举
    if template_id.startswith("enum_") and _has_explicit_equipment_subject(question, tau):
        if re.search(r"考核指标|性能指标|指标", question) and not re.search(
            r"试验项目|测试项目", question
        ):
            return "equip_to_indicators"
        if re.search(r"试验项目|测试项目", question):
            return "equip_to_projects"
    if not bound_types:
        return template_id
    if not focus_types:
        if "Equipment" in bound_types:
            if re.search(r"指标|考核", question):
                return "equip_to_indicators"
            return "equip_to_projects"
        return template_id
    if bound_types & focus_types:
        return template_id
    if "Equipment" in bound_types:
        if re.search(r"指标|考核", str(binding.get("intent") or "")):
            return "equip_to_indicators"
        return "equip_to_projects"
    if "Environment" in bound_types:
        return "env_to_projects"
    if "TestProject" in bound_types:
        return "project_to_methods"
    if "Indicator" in bound_types:
        return "indicator_to_methods"
    if "EquipmentType" in bound_types:
        return "equip_type_to_indicators"
    return template_id
