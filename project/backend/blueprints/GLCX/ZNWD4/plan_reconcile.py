"""
plan_reconcile：RoutePlan + probe 实测 → business_id / params。

策略：**LLM RoutePlan 优先**；规则仅做冲突纠正（guardrail）与 LLM 未给出时的兜底。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

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
    get_business,
    list_business_ids,
)
from .business_catalog_v2 import resolve_biz_key, resolve_business_from_plan
from .enum_inference import looks_like_enumeration, resolve_enum_node_type
from .keyword_probe import probe_keyword
from .meta_ops import NODE_LABELS, RELATION_SCHEMA
from .method_inference import (
    looks_like_equip_project_by_method,
    looks_like_method_content_question,
    resolve_equip_project_by_method_params,
    resolve_method_content_params,
)
from .rel_inference import (
    looks_like_equipment_belongs_to_type,
    looks_like_object_indicator_relation,
    looks_like_object_project_relation,
    looks_like_reverse_relation,
    looks_like_type_equipment_relation,
    relation_answer_entity_type,
    resolve_equipment_belongs_to_type_params,
    resolve_rel_one_hop_params,
    resolve_reverse_rel_params,
    resolve_type_equipment_rel_params,
)
from .playbook_params import merge_playbook_answer_params
from .playbook_resolver import expand_playbook, playbook_guardrail_question, resolve_environment_focus_id
from .question_route_lock import RouteLock, apply_route_lock_to_plan, resolve_route_lock
from .routing_catalog import PURPOSE_META, resolve_route
from .user_biz_catalog import UBIZ_EQUIPMENT, UBIZ_EQUIPMENT_TYPE, UBIZ_TEST_PROJECT
from .route_plan_schema import plan_keyword, plan_retrieval_question
from .znwd4_domain_guard import infer_indicator_fallback, must_use_graph_database

logger = logging.getLogger(__name__)

_VALID_BIZ = frozenset(list_business_ids())

_GREETING = re.compile(
    r"^(你好|您好|嗨|hello|hi|在吗|谢谢|感谢|再见|拜拜)\s*[!！。.?？]*$",
    re.I,
)

_METHOD_GOAL = re.compile(r"(方法|试验方法|测试方法|步骤|怎么做)", re.I)
_PROJECT_GOAL = re.compile(r"(试验项目|测试项目)", re.I)
_INDICATOR_GOAL = re.compile(r"(考核指标|性能指标|试验指标|测试指标|指标)", re.I)
_STANDARD_GOAL = re.compile(r"(GB/?T|标准|章节|条文|规范)", re.I)


def _draft_type_to_equipment(draft: Dict[str, Any]) -> bool:
    fr = str(draft.get("from_type") or "").strip()
    to = str(draft.get("to_type") or "").strip()
    rel = str(draft.get("rel_type") or draft.get("relation_type") or "").strip()
    if str(draft.get("where_side") or "").lower() == "to":
        return False
    return fr == "EquipmentType" and to == "Equipment" and (not rel or rel == "HAS")


def _relation_allowed(fr: str, rel: str, to: str) -> bool:
    return (fr, to) in (RELATION_SCHEMA.get(rel) or [])


def _probe_label_hints(plan: Dict[str, Any], draft: Dict[str, Any], question: str) -> list:
    hints: list = []
    for src in (
        plan.get("guessed_node_types") if isinstance(plan.get("guessed_node_types"), list) else [],
        [draft.get("from_type"), draft.get("node_type"), draft.get("to_type")],
    ):
        for x in src:
            s = str(x or "").strip()
            if s in NODE_LABELS and s not in hints:
                hints.append(s)
    if not hints and looks_like_type_equipment_relation(question):
        hints.append("EquipmentType")
    from .method_inference import looks_like_equip_project_by_method

    if looks_like_equip_project_by_method(question):
        if "TestMethod" not in hints:
            hints.append("TestMethod")
    return hints


def _probe_best(
    plan: Dict[str, Any],
    driver: Any,
    keyword: str,
    *,
    question: str = "",
    draft: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    draft = draft or {}
    snap = plan.get("probe_snapshot") if isinstance(plan.get("probe_snapshot"), dict) else {}
    best = snap.get("best") if isinstance(snap.get("best"), dict) else None
    if best and best.get("node_type"):
        return snap
    hints = _probe_label_hints(plan, draft, question)
    if driver and keyword:
        full = probe_keyword(driver, keyword, label_hints=hints or None)
        return {"best": full.get("best"), "hits": full.get("hits", [])[:5], "keyword": keyword}
    return snap or {}


def _guardrail_override(
    intended: str,
    draft: Dict[str, Any],
    *,
    q_plain: str,
    rq: str,
) -> Tuple[str, str]:
    """
    仅在 LLM 选择与问法/probe/params_draft 明显冲突时纠正。
    返回 (business_id, reason)；空 business_id 表示不纠正。
    """
    text = f"{q_plain}\n{rq}"

    if intended == BIZ_ENUM_NODES:
        if _draft_type_to_equipment(draft) or looks_like_type_equipment_relation(text):
            return BIZ_REL_ONE_HOP, "enum_vs_type_equipment"
        nt = str(draft.get("node_type") or "").strip()
        if nt == "TestProject" and ("设备类型" in text or "装备类型" in text):
            return BIZ_ENUM_NODES, "enum_node_type_fix"  # 仍 ENUM，params 层修正 node_type

    if intended == BIZ_DOCUMENT:
        if looks_like_method_content_question(text) and not _STANDARD_GOAL.search(text):
            return BIZ_METHOD_CONTENT, "document_vs_method_content"
        if looks_like_equip_project_by_method(text):
            return BIZ_EQUIP_PROJECT_BY_METHOD, "document_vs_equip_project_method"

    if intended == BIZ_REL_TWO_HOP and looks_like_equip_project_by_method(text):
        return BIZ_EQUIP_PROJECT_BY_METHOD, "two_hop_equip_project_method"

    if intended == BIZ_PROJECT_TO_METHOD and looks_like_method_content_question(text):
        return BIZ_METHOD_CONTENT, "method_list_vs_content"

    if intended == BIZ_PROJECT_TO_METHOD:
        ans = relation_answer_entity_type(text)
        if looks_like_equip_project_by_method(text):
            return BIZ_EQUIP_PROJECT_BY_METHOD, "equip_project_method_vs_project_to_method"
        if ans in ("equipment", "environment", "indicator", "project") and looks_like_reverse_relation(text):
            return BIZ_REL_REVERSE_ONE_HOP, "reverse_vs_project_to_method"
        if ans == "equipment" and re.search(r"(哪些|哪一些|什么|都有哪些|有哪些).{0,20}(设备|装备)", text):
            return BIZ_REL_REVERSE_ONE_HOP, "equip_discovery_vs_method"

    if intended == BIZ_REL_ONE_HOP:
        fr = str(draft.get("from_type") or "").strip()
        to = str(draft.get("to_type") or "").strip()
        rel = str(draft.get("rel_type") or draft.get("relation_type") or "CON").strip()
        if fr == "Equipment" and to == "TestProject" and rel == "CON":
            if _INDICATOR_GOAL.search(text) or looks_like_object_indicator_relation(text):
                return BIZ_REL_ONE_HOP, "equipment_indicator_vs_project"
        if fr and to and rel and not _relation_allowed(fr, rel, to):
            if fr == "Equipment" and to == "TestMethod":
                return BIZ_REL_ONE_HOP, "invalid_rel_keep_llm"  # params 层会改 to_type
            logger.warning("[ZNWD4][reconcile] LLM rel not in schema: %s-%s->%s", fr, rel, to)

    return "", ""


def _fallback_business_id(
    *,
    q_plain: str,
    rq: str,
    node_type: str,
    user_goal: str,
    kw: str,
) -> str:
    """LLM 未给出有效 intended_business_id 时的启发式兜底。"""
    text = f"{q_plain}\n{rq}"
    if looks_like_method_content_question(text):
        return BIZ_METHOD_CONTENT
    if looks_like_equip_project_by_method(text):
        return BIZ_EQUIP_PROJECT_BY_METHOD
    try:
        from .question_route_lock import _looks_like_equipment_indicators
    except ImportError:
        _looks_like_equipment_indicators = lambda _q: False  # type: ignore
    if _INDICATOR_GOAL.search(text) and _looks_like_equipment_indicators(q_plain):
        return BIZ_REL_ONE_HOP
    if _INDICATOR_GOAL.search(text) and len((q_plain or "").strip()) <= 20:
        return BIZ_ENUM_NODES
    if _STANDARD_GOAL.search(text):
        return BIZ_DOCUMENT
    if looks_like_equipment_belongs_to_type(text):
        return BIZ_REL_ONE_HOP
    if looks_like_type_equipment_relation(text):
        return BIZ_REL_ONE_HOP
    if looks_like_reverse_relation(text):
        return BIZ_REL_REVERSE_ONE_HOP
    if looks_like_enumeration(text):
        return BIZ_ENUM_NODES
    if node_type == "TestProject" and (_METHOD_GOAL.search(rq) or user_goal in ("method", "methods")):
        if relation_answer_entity_type(text) == "method" and not looks_like_reverse_relation(text):
            return BIZ_PROJECT_TO_METHOD
    if node_type == "TestProject" and looks_like_reverse_relation(text):
        return BIZ_REL_REVERSE_ONE_HOP
    if node_type == "Equipment" and (
        looks_like_object_indicator_relation(text)
        or (_INDICATOR_GOAL.search(text) and not _PROJECT_GOAL.search(text))
    ):
        return BIZ_REL_ONE_HOP
    if node_type == "Equipment" and (
        looks_like_object_project_relation(text)
        or (_PROJECT_GOAL.search(q_plain) and not _METHOD_GOAL.search(q_plain))
    ):
        return BIZ_REL_ONE_HOP
    if _METHOD_GOAL.search(rq) and kw:
        return BIZ_PROJECT_TO_METHOD
    if node_type:
        return BIZ_REL_ONE_HOP
    return ""


def _resolve_strategy_mode(
    plan: Dict[str, Any],
    business_id: str,
    *,
    guard_reason: str,
) -> Tuple[str, str]:
    cfg = get_business(business_id) or {}
    strategy = str(plan.get("answer_strategy") or "").strip().lower()
    answer_mode = str(plan.get("answer_mode") or "").strip()

    if business_id in (
        BIZ_ENUM_NODES,
        BIZ_REL_ONE_HOP,
        BIZ_REL_REVERSE_ONE_HOP,
        BIZ_EQUIP_PROJECT_BY_METHOD,
        BIZ_REL_TWO_HOP,
        BIZ_METHOD_CONTENT,
        BIZ_NODE_KW,
        BIZ_NODE_PROP_REV,
    ):
        if strategy not in ("db_direct", "db_synthesize"):
            strategy = "db_direct"
        if not answer_mode:
            answer_mode = str(cfg.get("answer_mode_default") or "database_direct")
        if business_id in (BIZ_EQUIP_PROJECT_BY_METHOD, BIZ_REL_TWO_HOP):
            strategy = "db_direct"
            answer_mode = "database_direct"
    elif business_id in (BIZ_PROJECT_TO_METHOD, BIZ_DOCUMENT, BIZ_NODE_PROP):
        if strategy == "db_direct_doc":
            strategy = "db_direct_doc"
            if not answer_mode:
                answer_mode = "database_direct"
        elif strategy not in ("db_direct", "db_synthesize", "db_direct_doc"):
            strategy = "db_synthesize"
        if not answer_mode:
            answer_mode = str(cfg.get("answer_mode_default") or "llm_synthesize")
        if strategy == "db_direct_doc":
            answer_mode = "database_direct"

    if guard_reason.startswith("enum_vs") or guard_reason.startswith("document_vs"):
        strategy = "db_direct"
        answer_mode = "database_direct"

    if strategy not in ("db_direct", "db_synthesize", "direct_llm", "db_direct_doc"):
        strategy = "db_synthesize" if business_id else "direct_llm"
    return strategy, answer_mode


def _rel_params_from_draft(
    draft: Dict[str, Any],
    *,
    node_type: str,
    node_name: str,
    node_id: str,
    q_plain: str,
    rq: str,
) -> Dict[str, Any]:
    """优先采用 LLM params_draft 中的关系三元组与 focus。"""
    fr = str(draft.get("from_type") or "").strip()
    to = str(draft.get("to_type") or "").strip()
    rel = str(draft.get("rel_type") or draft.get("relation_type") or "").strip()
    focus = str(draft.get("focus") or draft.get("project_name") or draft.get("keyword") or "").strip()

    ws = str(draft.get("where_side") or "").lower()
    if fr == "EquipmentType" and to == "Equipment" and (not rel or rel == "HAS"):
        if ws == "to" or looks_like_equipment_belongs_to_type(f"{q_plain}\n{rq}"):
            return resolve_equipment_belongs_to_type_params(
                q_plain, rq, node_type=node_type, node_name=node_name, node_id=node_id, params_draft=draft
            )
        if _draft_type_to_equipment(draft):
            return resolve_type_equipment_rel_params(
                q_plain, rq, node_type=node_type, node_name=node_name, node_id=node_id, params_draft=draft
            )
    elif _draft_type_to_equipment(draft):
        return resolve_type_equipment_rel_params(
            q_plain, rq, node_type=node_type, node_name=node_name, node_id=node_id, params_draft=draft
        )

    if fr and to and rel and _relation_allowed(fr, rel, to) and (focus or node_id):
        out: Dict[str, Any] = {"from_type": fr, "to_type": to, "rel_type": rel, "focus": focus or node_name}
        text = f"{q_plain}\n{rq}"
        if (
            fr == "Equipment"
            and to == "TestProject"
            and rel == "CON"
            and (_INDICATOR_GOAL.search(text) or looks_like_object_indicator_relation(text))
        ):
            out["to_type"] = "Indicator"
        if node_id and node_type == fr:
            out["focus_id"] = node_id
        elif str(draft.get("focus_id") or "").strip():
            out["focus_id"] = str(draft.get("focus_id")).strip()
        return out

    # draft 不完整时回退规则推断
    if looks_like_equipment_belongs_to_type(f"{q_plain}\n{rq}"):
        return resolve_equipment_belongs_to_type_params(
            q_plain, rq, node_type=node_type, node_name=node_name, node_id=node_id, params_draft=draft
        )
    if looks_like_type_equipment_relation(f"{q_plain}\n{rq}"):
        return resolve_type_equipment_rel_params(
            q_plain, rq, node_type=node_type, node_name=node_name, node_id=node_id, params_draft=draft
        )
    return resolve_rel_one_hop_params(
        q_plain, rq, node_type=node_type, node_name=node_name, node_id=node_id, params_draft=draft
    )


def _build_params(
    business_id: str,
    draft: Dict[str, Any],
    *,
    q_plain: str,
    rq: str,
    node_type: str,
    node_name: str,
    node_id: str,
    kw: str,
) -> Dict[str, Any]:
    if business_id == BIZ_PROJECT_TO_METHOD:
        proj_kws = draft.get("project_keywords") if isinstance(draft.get("project_keywords"), list) else []
        proj_kws = [str(x).strip() for x in proj_kws if str(x).strip()]
        out: Dict[str, Any] = {
            "project_name": str(
                draft.get("project_name") or draft.get("focus") or (proj_kws[0] if proj_kws else "") or node_name or kw
            ).strip()
        }
        if proj_kws:
            out["project_keywords"] = proj_kws
        if node_id and node_type == "TestProject":
            out["project_id"] = node_id
        return out

    if business_id == BIZ_METHOD_CONTENT:
        return resolve_method_content_params(
            q_plain, rq, node_type=node_type, node_name=node_name, node_id=node_id, params_draft=draft
        )

    if business_id == BIZ_DOCUMENT:
        return {
            "query": str(draft.get("query") or rq)[:500],
            "section_prefix": str(draft.get("section_prefix") or "").strip(),
            "std_code": str(draft.get("std_code") or "").strip(),
        }

    if business_id == BIZ_STANDARD_CONDITION:
        return {
            "query": str(draft.get("query") or rq)[:500],
            "std_code": str(draft.get("std_code") or "").strip(),
        }

    if business_id == BIZ_ENUM_NODES:
        nt = str(draft.get("node_type") or "").strip()
        lock_nt = resolve_route_lock(q_plain)
        if lock_nt and lock_nt.route_id == "R-EQUIPMENT-list":
            nt = "Equipment"
        elif lock_nt and lock_nt.route_id == "R-EQUIPMENT_TYPE-level1_list":
            nt = "EquipmentType"
        if nt not in NODE_LABELS:
            nt = resolve_enum_node_type(q_plain, rq, draft)
        return {"node_type": nt, "route_id": lock_nt.route_id if lock_nt else ""}

    if business_id == BIZ_REL_ONE_HOP:
        return _rel_params_from_draft(
            draft,
            node_type=node_type,
            node_name=node_name,
            node_id=node_id,
            q_plain=q_plain,
            rq=rq,
        )

    if business_id == BIZ_REL_REVERSE_ONE_HOP:
        return resolve_reverse_rel_params(
            q_plain,
            rq,
            node_type=node_type,
            node_name=node_name,
            node_id=node_id,
            params_draft=draft,
        )

    if business_id == BIZ_EQUIP_PROJECT_BY_METHOD:
        return resolve_equip_project_by_method_params(
            q_plain,
            rq,
            node_type=node_type,
            node_name=node_name,
            node_id=node_id,
            plan_keyword=kw,
            params_draft=draft,
        )

    if business_id == BIZ_REL_TWO_HOP:
        return resolve_equip_project_by_method_params(
            q_plain,
            rq,
            node_type=node_type,
            node_name=node_name,
            node_id=node_id,
            plan_keyword=kw,
            params_draft=draft,
        )

    if business_id == BIZ_NODE_KW:
        out = {
            "node_type": str(draft.get("node_type") or node_type or "Equipment"),
            "field": str(draft.get("field") or "Name"),
            "value": str(draft.get("value") or draft.get("focus") or node_name or kw).strip(),
        }
        if node_id and (node_type or out["node_type"]) == out["node_type"]:
            out["focus_id"] = node_id
        elif draft.get("focus_id"):
            out["focus_id"] = str(draft.get("focus_id"))
        return out

    if business_id == BIZ_NODE_PROP_REV:
        return {
            "node_type": str(draft.get("node_type") or "EquipmentType"),
            "field": str(draft.get("field") or "Type"),
            "value": str(draft.get("value") or draft.get("focus") or "").strip(),
        }

    if business_id == BIZ_NODE_PROP:
        return {
            "node_type": str(draft.get("node_type") or node_type or "Environment"),
            "field": str(draft.get("field") or "Name"),
            "value": str(draft.get("value") or draft.get("focus") or node_name or kw).strip(),
            "focus_id": str(draft.get("focus_id") or node_id or "").strip(),
            "property_fields": draft.get("property_fields") if isinstance(draft.get("property_fields"), list) else [],
        }

    return {}


def _znwd6_plan_blocks_enum_downgrade(plan: Dict[str, Any], lock: RouteLock) -> bool:
    """ZNWD6 已选定 equip_* 模板时，禁止 reconcile 被 route_lock 降为全库枚举。"""
    if str(plan.get("source") or "") != "znwd6":
        return False
    tid = str(plan.get("template_id") or "")
    if tid.startswith("equip_") and lock.route_id.endswith(("-list_all", "-enum")):
        return True
    if tid == "equip_to_indicators" and lock.route_id == "R-INDICATOR-list_all":
        return True
    if tid == "equip_to_projects" and lock.route_id == "R-TEST_PROJECT-enum":
        return True
    ub = str(plan.get("user_biz") or "")
    sc = str(plan.get("scenario") or "")
    if ub == "UBIZ_INDICATOR" and sc == "by_equipment" and lock.route_id == "R-INDICATOR-list_all":
        return True
    if ub == "UBIZ_TEST_PROJECT" and sc == "by_equipment" and lock.route_id == "R-TEST_PROJECT-enum":
        return True
    return False


def reconcile_route_plan(
    plan: Dict[str, Any],
    *,
    question: str,
    driver: Any = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    输出：business_id, answer_mode, params, keyword, retrieval_question, answer_strategy, probe_best
    """
    rq = plan_retrieval_question(plan, question)
    kw = plan_keyword(plan, question)
    strategy = str(plan.get("answer_strategy") or "").strip().lower()
    intended = str(plan.get("intended_business_id") or "").strip()
    user_goal = str(plan.get("user_goal") or "").strip().lower()
    q_plain = (question or "").strip()
    draft = plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {}

    if plan.get("intent_plan_expanded") and plan.get("meta_step_groups"):
        from .user_biz_catalog import COMP_INTENT_PLAN

        user_biz = str(plan.get("user_biz") or COMP_INTENT_PLAN)
        strategy_pb = str(plan.get("answer_strategy") or "db_direct").strip().lower()
        params_pb = dict(plan.get("params_draft") or {})
        if plan.get("intent_plan"):
            params_pb["intent_plan"] = plan.get("intent_plan")
        snap = _probe_best(plan, driver, kw, question=q_plain, draft=draft)
        return {
            "business_id": user_biz,
            "answer_mode": "database_direct",
            "answer_strategy": strategy_pb,
            "params": params_pb,
            "keyword": kw,
            "retrieval_question": rq,
            "probe_best": snap.get("best") if isinstance(snap, dict) else None,
            "playbook": True,
            "user_biz": user_biz,
            "scenario": str(plan.get("scenario") or "default"),
            "user_biz_title": str(plan.get("user_biz_title") or "多意图检索"),
            "meta_step_groups": plan.get("meta_step_groups") or [],
            "multi_step": True,
            "intent_plan": plan.get("intent_plan"),
        }

    if _GREETING.match(q_plain):
        return {
            "business_id": "",
            "answer_mode": "direct_llm",
            "answer_strategy": "direct_llm",
            "params": {},
            "keyword": kw,
            "retrieval_question": rq,
            "probe_best": None,
        }

    from .meta_info_handler import looks_like_meta_info_question, try_answer_meta_info

    if looks_like_meta_info_question(q_plain):
        ok, meta_answer = try_answer_meta_info(q_plain, driver=driver)
        if ok:
            return {
                "business_id": "",
                "answer_mode": "meta_direct",
                "answer_strategy": "db_direct",
                "meta_answer": meta_answer,
                "params": {"route_id": "R-META-stats"},
                "keyword": kw,
                "retrieval_question": rq,
                "probe_best": None,
                "route_id": "R-META-stats",
                "user_biz_title": "图库/向量库统计",
            }

    route_id = str(plan.get("route_id") or "").strip()
    match_conf_v2 = str(plan.get("match_confidence") or "").strip().lower()
    v2_routed = bool(route_id) and match_conf_v2 in ("high", "medium")

    if route_id == "R-CHAT-direct" or (
        v2_routed and str(plan.get("answer_strategy") or strategy).strip().lower() == "direct_llm"
    ):
        return {
            "business_id": "",
            "answer_mode": "direct_llm",
            "answer_strategy": "direct_llm",
            "params": {},
            "keyword": kw,
            "retrieval_question": rq,
            "probe_best": None,
            "route_id": route_id or "R-CHAT-direct",
        }

    if route_id == "R-META-stats" or str(plan.get("purpose") or "") == PURPOSE_META:
        from .meta_info_handler import try_answer_meta_info

        ok, meta_answer = try_answer_meta_info(q_plain, driver=driver)
        if ok:
            return {
                "business_id": "",
                "answer_mode": "meta_direct",
                "answer_strategy": "db_direct",
                "meta_answer": meta_answer,
                "params": {},
                "keyword": kw,
                "retrieval_question": rq,
                "probe_best": None,
                "route_id": "R-META-stats",
                "user_biz_title": "图库/向量库统计",
            }

    # 图谱领域词：禁止无库直答（清单库优先）
    if strategy == "direct_llm" and must_use_graph_database(q_plain):
        strategy = ""
        intended = ""
        plan = {**plan, "answer_strategy": ""}

    ind_fb_bid = ""
    if not v2_routed and str(plan.get("source") or "") != "znwd6":
        ind_fb = infer_indicator_fallback(
            q_plain, plan, driver=driver, user_id=user_id, session_id=session_id
        )
        if ind_fb:
            ind_fb_bid, fb_params = ind_fb
            draft = {**fb_params, **draft}
            if not str(plan.get("purpose_node") or draft.get("purpose_node") or "").strip():
                plan["purpose_node"] = "Indicator"
                plan["biz_type"] = "enum" if ind_fb_bid == BIZ_ENUM_NODES else "hop_n"
                if ind_fb_bid == BIZ_REL_ONE_HOP:
                    plan["known_type"] = "Equipment"
                    plan["hop_direction"] = "forward"

    snap = _probe_best(plan, driver, kw, question=q_plain, draft=draft)
    best = snap.get("best") if isinstance(snap.get("best"), dict) else None
    node_type = str((best or {}).get("node_type") or "").strip()
    node_id = str((best or {}).get("id") or "").strip()
    node_name = str((best or {}).get("name") or kw).strip()

    route_lock = resolve_route_lock(q_plain)
    if route_lock and _znwd6_plan_blocks_enum_downgrade(plan, route_lock):
        logger.info(
            "[ZNWD4][reconcile] skip route lock downgrade znwd6 template=%s lock=%s",
            plan.get("template_id"),
            route_lock.route_id,
        )
        route_lock = None
    if route_lock:
        expected = resolve_route(route_lock.route_id) or {}
        if (
            str(plan.get("route_id") or "") != route_lock.route_id
            or str(plan.get("user_biz") or "") != str(expected.get("user_biz") or "")
            or str(plan.get("scenario") or "") != str(expected.get("scenario") or "")
        ):
            logger.info(
                "[ZNWD4][reconcile] route lock rewrite %s -> %s",
                plan.get("route_id"),
                route_lock.route_id,
            )
            plan = apply_route_lock_to_plan(plan, route_lock)
            draft = plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else draft
            kw = str(plan.get("keyword") or kw).strip()
            user_biz = str(plan.get("user_biz") or "").strip()
            scenario = str(plan.get("scenario") or "default").strip() or "default"

    # Playbook 优先（高/中置信 user_biz）
    user_biz = str(plan.get("user_biz") or "").strip()
    scenario = str(plan.get("scenario") or "default").strip() or "default"
    match_conf = str(plan.get("match_confidence") or "").strip().lower()
    if user_biz and match_conf in ("high", "medium") and playbook_guardrail_question(user_biz, scenario, q_plain):
        if user_biz == UBIZ_TEST_PROJECT and scenario == "by_equip_and_env":
            from .playbook_resolver import normalize_slots

            _slots = normalize_slots(draft, keyword=kw)
            env_id = resolve_environment_focus_id(_slots, plan, driver=driver)
            if env_id:
                draft = {**draft, "environment_focus_id": env_id}
                plan = {**plan, "params_draft": draft}
        pb = expand_playbook(plan, question=q_plain, probe_best=best, driver=driver)
        if pb and (pb.get("business_steps") or pb.get("meta_step_groups")):
            strategy_pb = str(pb.get("answer_strategy") or "db_synthesize").strip().lower()
            if strategy_pb not in ("db_direct", "db_synthesize", "db_direct_doc"):
                strategy_pb = "db_synthesize"
            primary_bid = str(pb.get("business_id") or "").strip()
            patch = pb.get("params_patch") if isinstance(pb.get("params_patch"), dict) else {}
            merged = merge_playbook_answer_params(
                patch, draft, user_biz=user_biz, scenario=scenario
            )
            if pb.get("business_steps"):
                _, step0_params = pb["business_steps"][0]
                if isinstance(step0_params, dict):
                    merged = {**merged, **step0_params}
            if primary_bid and primary_bid.startswith("BIZ_"):
                params_pb = _build_params(
                    primary_bid,
                    merged,
                    q_plain=q_plain,
                    rq=rq,
                    node_type=node_type,
                    node_name=node_name,
                    node_id=node_id,
                    kw=kw,
                )
                params_pb = merge_playbook_answer_params(
                    params_pb, merged, user_biz=user_biz, scenario=scenario
                )
            else:
                params_pb = merged
            logger.info(
                "[ZNWD4][reconcile] source=playbook user_biz=%s scenario=%s steps=%s",
                user_biz,
                scenario,
                len(pb.get("business_steps") or []) + len(pb.get("meta_step_groups") or []),
            )
            return {
                "business_id": primary_bid or user_biz,
                "answer_mode": "database_direct"
                if strategy_pb in ("db_direct", "db_direct_doc")
                else "database_synthesize",
                "answer_strategy": strategy_pb,
                "params": params_pb,
                "keyword": kw,
                "retrieval_question": rq,
                "probe_best": best,
                "playbook": True,
                "user_biz": user_biz,
                "scenario": scenario,
                "user_biz_title": pb.get("user_biz_title") or user_biz,
                "business_steps": pb.get("business_steps") or [],
                "meta_step_groups": pb.get("meta_step_groups") or [],
                "multi_step": bool(pb.get("multi_step")),
            }

    purpose_node = str(plan.get("purpose_node") or draft.get("purpose_node") or "").strip()
    biz_type = str(plan.get("biz_type") or draft.get("biz_type") or "").strip().lower()
    v2_patch: Dict[str, Any] = {}
    if purpose_node and biz_type:
        plan["biz_key"] = resolve_biz_key(
            purpose_node=purpose_node,
            biz_type=biz_type,
            known_type=str(plan.get("known_type") or draft.get("known_type") or ""),
            hop_direction=str(plan.get("hop_direction") or draft.get("hop_direction") or "forward"),
            biz_key=str(plan.get("biz_key") or ""),
            question=q_plain,
        )
        v2_bid, v2_patch = resolve_business_from_plan(plan, question=q_plain)
    else:
        v2_bid = ""

    # 1) 采纳 LLM 的 intended（若合法）
    business_id = intended if intended in _VALID_BIZ else ""
    source = "llm" if business_id else "fallback"

    if not business_id and v2_bid:
        business_id = v2_bid
        source = "resolver_v2"
    if v2_patch:
        merged = dict(v2_patch)
        merged.update({k: v for k, v in draft.items() if v not in (None, "", [], {})})
        draft = merged

    # 2)  guardrail：仅纠正明显冲突（v2 高置信 route 跳过）
    guard_bid, guard_reason = ("", "")
    if not (v2_routed and match_conf_v2 == "high"):
        guard_bid, guard_reason = _guardrail_override(intended, draft, q_plain=q_plain, rq=rq) if intended else ("", "")
    if guard_bid:
        business_id = guard_bid
        source = f"guardrail:{guard_reason}"

    # 3) LLM 未给出时兜底
    if not business_id:
        business_id = _fallback_business_id(
            q_plain=q_plain, rq=rq, node_type=node_type, user_goal=user_goal, kw=kw
        )
        source = "fallback"

    if not business_id and ind_fb_bid:
        business_id = ind_fb_bid
        source = "indicator_fallback"

    answer_mode = str(plan.get("answer_mode") or "").strip()
    strategy, answer_mode = _resolve_strategy_mode(plan, business_id, guard_reason=guard_reason)
    params = _build_params(
        business_id,
        draft,
        q_plain=q_plain,
        rq=rq,
        node_type=node_type,
        node_name=node_name,
        node_id=node_id,
        kw=kw,
    )
    if business_id == BIZ_NODE_PROP:
        from .env_property_utils import resolve_environment_property_fields, sanitize_environment_keywords

        prop_fields = params.get("property_fields") if isinstance(params.get("property_fields"), list) else []
        if not prop_fields:
            params["property_fields"] = resolve_environment_property_fields(q_plain)
        env_kws = draft.get("environment_keywords") if isinstance(draft.get("environment_keywords"), list) else []
        cleaned = sanitize_environment_keywords([str(x) for x in env_kws])
        if cleaned:
            params["environment_keywords"] = cleaned
        if best and str(best.get("node_type") or "") == "Environment":
            if not str(params.get("focus_id") or "").strip():
                pid = str(best.get("id") or best.get("node_id") or "").strip()
                if pid:
                    params["focus_id"] = pid
            if not str(params.get("value") or "").strip():
                pname = str(best.get("name") or "").strip()
                if pname:
                    params["value"] = pname

    logger.info(
        "[ZNWD4][reconcile] source=%s business=%s intended=%s mode=%s node_type=%s id=%s kw=%r draft_keys=%s",
        source,
        business_id,
        intended or "-",
        answer_mode,
        node_type,
        node_id,
        kw[:40],
        list(draft.keys())[:8],
    )
    route_id_out = str(plan.get("route_id") or "").strip()
    if route_id_out and isinstance(params, dict):
        params.setdefault("route_id", route_id_out)
    out: Dict[str, Any] = {
        "business_id": business_id,
        "answer_mode": answer_mode,
        "answer_strategy": strategy,
        "params": params,
        "keyword": kw,
        "retrieval_question": rq,
        "probe_best": best,
        "route_id": route_id_out,
    }
    for _k in (
        "routing_tier",
        "source",
        "faq_slot_verified",
        "zvec_used",
        "llm_routing_calls",
        "routing_event_id",
    ):
        if plan.get(_k) not in (None, ""):
            out[_k] = plan[_k]
    if out.get("source"):
        out["routing_path_source"] = out["source"]
    return out
