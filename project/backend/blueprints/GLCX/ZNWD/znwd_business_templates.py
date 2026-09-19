"""
ZNWD 业务模板：固定少量 template_id + 槽位，由 RELATION_SCHEMA 约束生成快速检索 program。

不逐条枚举「装备→项目」「环境→项目」等业务；组合通过 slots（from_type / to_type / rel_type / focus）表达。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Tuple

from .intent_classifier import Intent, IntentResult, plain_intent_slice, relation_target_kind
from .retrieval_meta_ops import RELATION_SCHEMA
from .services_v4 import (
    _extract_ssyq_keyword_from_question,
    _infer_focus_entity_from_messages,
)
from .znwd_business_types import BusinessTemplate
from .znwd_keyword_resolver import resolve_best_focus, resolve_keyword_in_graph

logger = logging.getLogger(__name__)

# 与 services_v5 对齐的环境主体词（用于 from_type=Environment）
_ENV_SUBJECT_KEYWORDS: Tuple[str, ...] = (
    "高温",
    "低温",
    "湿热",
    "盐雾",
    "淋雨",
    "振动",
    "沙尘",
    "砂尘",
    "老化",
    "霉菌",
    "高海拔",
    "复杂气象",
    "太阳辐射",
)

_PROJECT_OR_ENV_IN_EQUIP_VALUE = re.compile(
    r"(测试|试验|鉴定|环境|高温|低温|湿热|盐雾|淋雨|振动|沙尘|老化|可靠性|力学|emc|海拔|霉菌)",
    re.I,
)

_NEO_LABEL_ZH = {
    "Equipment": "设备",
    "EquipmentType": "设备类型",
    "Environment": "试验环境",
    "TestProject": "测试项目",
    "Indicator": "指标",
    "TestMethod": "测试方法",
    "File": "文件",
}


@dataclass
class BusinessSlots:
    """模板槽位（序列化进 program['_business_slots']）。"""

    node_type: str = ""
    from_type: str = ""
    to_type: str = ""
    rel_type: str = "CON"
    focus: str = ""
    project_filter: str = ""
    section_prefix: str = ""
    path: List[Tuple[str, str, str]] = field(default_factory=list)


def _norm(q: str) -> str:
    return (plain_intent_slice(q) or q or "").strip()


def _anchor_from_retrieval_question(rq: str) -> str:
    m = re.search(r"【承前主体】\s*([^。；;\n]+)", rq or "")
    return (m.group(1) or "").strip() if m else ""


def _looks_like_node_type_enumeration_question(text: str) -> bool:
    t = _norm(text)
    if not t:
        return False
    if re.search(
        r"(?:查询|请问|想了解一下|想了解)?\s*[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*的\s*(?:测试|试验)?\s*"
        r"(?:项目|指标|方法|测试方法|试验方法)\s*(?:有哪些|都有什么|包括哪些|是什么)",
        t,
    ):
        return False
    if re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*(?:有什么|有哪些|包括哪些)\s*(?:测试项目|试验项目|指标|方法|测试方法|试验方法)",
        t,
    ) and not re.search(r"(全部|所有|整个库|整个系统|一共)", t):
        return False
    if re.search(r"(有哪些|都有什么|列出|列举|显示|查看|全部|所有)", t) and re.search(
        r"(设备类型|装备类型|设备类别|环境类型|试验项目|测试项目|指标|测试方法|试验方法|文件)",
        t,
    ):
        return True
    if re.search(r"(设备类型|装备类型|环境类型)\s*(有哪些|是什么|都有什么|包括哪些)", t):
        return True
    return False


def _default_label_for_enumeration_question(text: str) -> str:
    t = (text or "").strip()
    if "设备类型" in t or "装备类型" in t or "设备类别" in t:
        return "EquipmentType"
    if ("设备" in t or "装备" in t) and "类型" not in t and re.search(r"(有哪些|都有什么|列出|列举)", t):
        if "项目" not in t and "指标" not in t and "方法" not in t:
            return "Equipment"
    if "环境" in t:
        return "Environment"
    if "测试项目" in t or "试验项目" in t or ("项目" in t and ("测试" in t or "试验" in t)):
        return "TestProject"
    if "指标" in t:
        return "Indicator"
    if "方法" in t or "测试方法" in t or "试验方法" in t:
        return "TestMethod"
    if "文件" in t:
        return "File"
    return "EquipmentType"


def _looks_like_standard_process_question(text: str) -> bool:
    t = text or ""
    return bool(re.search(r"(标准|GB/T|GJB).*(试验过程|试验步骤|章节|第\d+(?:\.\d+)*)", t))


def _extract_section_prefix(text: str) -> str:
    m = re.search(r"\b(\d+(?:\.\d+){0,6})\b", text or "")
    return m.group(1) if m else ""


def _looks_like_project_or_env_in_equipment_value(val: str) -> bool:
    return bool(_PROJECT_OR_ENV_IN_EQUIP_VALUE.search((val or "").strip()))


def _extract_project_keyword_for_equipment_discovery(question: str, retrieval_question: str) -> str:
    for src in (question or "", _norm(retrieval_question) or retrieval_question or ""):
        s = (src or "").strip()
        if not s:
            continue
        for kw in _ENV_SUBJECT_KEYWORDS:
            if kw in s:
                return kw
        m = re.search(r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,14})(?:测试)?项目", s)
        if m:
            core = (m.group(1) or "").strip()
            core = re.sub(r"(的|与|和|及)$", "", core).strip()
            if len(core) >= 2 and core not in ("测试", "试验", "设备", "装备", "哪些", "什么"):
                return core[:14]
    return ""


def _extract_test_project_keyword_for_method_question(question: str, retrieval_question: str) -> str:
    """从「XX测试/试验…的方法」类问句提取测试项目名称（如 电池高温测试、高温工作性能测试）。"""
    best = ""
    for src in (question or "", retrieval_question or ""):
        s = (src or "").strip()
        if not s:
            continue
        # 「电池高温测试有哪些方法 / 有什么方法 / 有什么试验方法」
        for pat in (
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:有什么|有哪些|包括哪些|都有哪些).{0,10}(?:试验|测试)?\s*方法",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:有什么|有哪些|包括哪些|都有哪些)\s*方法",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,36}测试)\s*(?:有什么|有哪些|包括哪些|的)?\s*(?:试验)?\s*方法",
        ):
            m = re.search(pat, s)
            if m:
                cand = re.sub(r"\s+", "", (m.group(1) or "").strip())
                if len(cand) > len(best):
                    best = cand
        if best:
            return best
        m = re.search(
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:有什么|有哪些|包括哪些).{0,12}(?:试验|测试)\s*方法",
            s,
        )
        if m:
            return re.sub(r"\s+", "", (m.group(1) or "").strip())
        m = re.search(r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*测试项目", s)
        if m:
            return (m.group(1) or "").strip()
        m = re.search(r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*试验项目", s)
        if m:
            return (m.group(1) or "").strip()
        # 「高温工作性能 测试的方法」→ 高温工作性能测试
        m = re.search(
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,50}测试)\s*(?:的)?\s*(?:方法|步骤|流程|内容)",
            s,
        )
        if m:
            core = re.sub(r"\s+", "", (m.group(1) or "").strip())
            if len(core) >= 2:
                return core
        m = re.search(
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,50}?)\s*试验\s*(?:的)?\s*(?:方法|步骤|流程|内容)",
            s,
        )
        if m:
            core = re.sub(r"\s+", "", (m.group(1) or "").strip())
            if len(core) >= 2:
                return core if core.endswith(("测试", "试验")) else f"{core}试验"
    return ""


def expand_testproject_method_keywords(kw: str) -> Tuple[List[str], List[str]]:
    """
    项目→方法检索用词：OR 子串列表 + 可选 AND 分词（如 电池+高温，避免只搜「高温」误命中环境）。
    """
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
    return list(dict.fromkeys(or_kws))[:8], list(dict.fromkeys(must_all))[:6]


def _looks_like_test_project_name(text: str) -> bool:
    t = re.sub(r"\s+", "", (text or "").strip())
    if len(t) < 2:
        return False
    return bool(
        re.search(r"(测试|试验|性能|鉴定|防护|可靠性|环境|工作)", t)
        and not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", t)
    )


def _fast_path_focus_entity(
    messages: List[Dict[str, Any]],
    question: str,
    retrieval_question: str,
    driver: Any = None,
) -> str:
    if driver:
        best = resolve_best_focus(driver, question, retrieval_question)
        mf = str(best.get("matched_name") or best.get("focus") or "").strip()
        if mf:
            return mf
    fe = _infer_focus_entity_from_messages(messages, question).strip()
    if fe:
        return fe
    anchor = _anchor_from_retrieval_question(retrieval_question)
    if anchor:
        return anchor
    return _extract_ssyq_keyword_from_question(retrieval_question).strip()


def _relation_allowed(from_type: str, rel_type: str, to_type: str) -> bool:
    pairs = RELATION_SCHEMA.get(rel_type) or []
    return (from_type, to_type) in pairs


def _target_to_node_type(target_kind: str) -> str:
    return {
        "project": "TestProject",
        "indicator": "Indicator",
        "method": "TestMethod",
        "equipment": "Equipment",
    }.get(target_kind, "TestProject")


def _infer_from_type_for_relation(question: str, focus: str, target_kind: str) -> str:
    t = _norm(question)
    if target_kind == "equipment" and re.search(r"(类型|类别)", t):
        return "EquipmentType"
    if re.search(r"(测试项目|试验项目)", t) and target_kind == "method":
        return "TestProject"
    if re.search(r"(测试项目|试验项目)", t) and target_kind in ("method", "project"):
        if target_kind == "method":
            return "TestProject"
    if "指标" in t and target_kind == "method" and "项目" not in t:
        return "Indicator"
    focus_compact = re.sub(r"\s+", "", focus or "")
    # 名称像测试项目时，勿因含「高温」等子串判为环境
    if _looks_like_test_project_name(focus_compact) or re.search(
        r"(测试|试验).{0,6}(方法|步骤|流程|内容)", t
    ):
        if target_kind == "method":
            return "TestProject"
        if target_kind in ("project", "unknown"):
            return "TestProject"
    env_hint = (
        bool(re.search(r"(环境|条件下|试验环境)", t))
        and not _looks_like_test_project_name(focus_compact)
    )
    if target_kind == "method" and re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,36}(?:测试|试验)", t
    ):
        return "TestProject"
    if not env_hint and focus_compact:
        # 仅当 focus 本身是短环境词（如「高温」）且不像项目名
        if any(focus_compact == k or focus_compact.startswith(k) for k in _ENV_SUBJECT_KEYWORDS):
            if len(focus_compact) <= 8 and not _looks_like_test_project_name(focus_compact):
                env_hint = True
    if env_hint and target_kind in ("project", "method", "unknown"):
        return "Environment"
    if re.search(r"(装备类型|设备类型).{0,12}(下|包含|对应).{0,8}(装备|设备)", t):
        return "EquipmentType"
    return "Equipment"


def _preflight_focus_node_type(driver, focus: str) -> Optional[str]:
    """关键词在图谱中的最佳节点类型（优先更长、更准的名称匹配）。"""
    if not driver or not focus or len(focus) < 2:
        return None
    res = resolve_keyword_in_graph(driver, focus)
    nt = str(res.get("node_type") or "").strip()
    if nt:
        logger.info(
            "[ZNWD][PreflightFocus] 关键词「%s」→ %s matched_name=%r score=%s",
            focus,
            nt,
            res.get("matched_name"),
            res.get("score"),
        )
        return nt
    logger.info("[ZNWD][PreflightFocus] 关键词「%s」未匹配到节点", focus)
    return None


def _two_hop_path(from_type: str, target_kind: str) -> List[Tuple[str, str, str]]:
    to_mid = "TestProject"
    if target_kind == "method":
        return [
            (from_type, "CON", to_mid),
            ("TestProject", "CON", "TestMethod"),
        ]
    return [(from_type, "CON", to_mid)]


def _slots_to_dict(slots: BusinessSlots) -> Dict[str, Any]:
    return {
        "node_type": slots.node_type,
        "from_type": slots.from_type,
        "to_type": slots.to_type,
        "rel_type": slots.rel_type,
        "focus": slots.focus,
        "project_filter": slots.project_filter,
        "section_prefix": slots.section_prefix,
        "path": [{"from": a, "rel": b, "to": c} for a, b, c in slots.path],
    }


def _wrap_program(
    *,
    template: BusinessTemplate,
    intent: Intent,
    intent_summary: str,
    slots: BusinessSlots,
    answer_mode: str,
    steps: List[Dict[str, Any]],
    focus_entity: str = "",
) -> Dict[str, Any]:
    prog = {
        "_v5_fast_route": True,
        "_business_template": template.value,
        "_business_slots": _slots_to_dict(slots),
        "_v5_intent": intent.value,
        "intent_summary": intent_summary,
        "focus_entity": focus_entity or slots.focus,
        "answer_mode": answer_mode,
        "fallback_v4": False,
        "missing_capability": "",
        "steps": steps,
    }
    from .znwd_thinking_narrative import enrich_program_display

    return enrich_program_display(prog)


def _step_relation_one_hop(
    step_id: str,
    from_type: str,
    to_type: str,
    rel_type: str,
    *,
    value: str = "",
    match_mode: str = "fuzzy",
    where_side: str = "from",
    from_field: str = "Name",
    limit: int = 120,
    from_values_ref: Optional[Dict[str, Any]] = None,
    purpose: str = "",
    slots: Optional[BusinessSlots] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "from_type": from_type,
        "rel_type": rel_type,
        "to_type": to_type,
        "from_field": from_field,
        "match_mode": match_mode,
        "limit": limit,
    }
    if from_values_ref:
        params["from_values_ref"] = from_values_ref
        params["match_mode"] = "in"
    else:
        params["where_side"] = where_side
        params["value"] = value
        if match_mode == "fuzzy_any":
            or_kws, _must = expand_testproject_method_keywords(value)
            params["value_list"] = or_kws if or_kws else [value]
            params["match_mode"] = "fuzzy_any"

    return {
        "step_id": step_id,
        "op_code": "neo_relation_query",
        "params": params,
        "purpose": purpose,
        "_tech_purpose": purpose,
    }


def _build_enum_program(question: str) -> Optional[Dict[str, Any]]:
    if not _looks_like_node_type_enumeration_question(question):
        return None
    label = _default_label_for_enumeration_question(question)
    slots = BusinessSlots(node_type=label)
    zh = _NEO_LABEL_ZH.get(label, label)
    logger.info("[ZNWD][Template] %s node_type=%s", BusinessTemplate.ENUM.value, label)
    return _wrap_program(
        template=BusinessTemplate.ENUM,
        intent=Intent.ENUMERATION,
        intent_summary=f"列举「{zh}」类节点",
        slots=slots,
        answer_mode="database_direct",
        steps=[
            {
                "step_id": "s1",
                "op_code": "neo_list_nodes",
                "params": {"node_type": label, "limit": 300, "status_filter": 1},
                "purpose": f"模板 {BusinessTemplate.ENUM.value}：列举 {label}",
            }
        ],
    )


def _build_document_program(question: str, retrieval_question: str) -> Optional[Dict[str, Any]]:
    if not _looks_like_standard_process_question(question):
        return None
    sec = _extract_section_prefix(question)
    slots = BusinessSlots(section_prefix=sec)
    steps: List[Dict[str, Any]] = [
        {
            "step_id": "s1",
            "op_code": "chroma_vector_search",
            "params": {"query": retrieval_question[:500], "top_k": 12},
            "purpose": f"模板 {BusinessTemplate.DOCUMENT.value}：向量召回",
        },
        {
            "step_id": "s2",
            "op_code": "chroma_rerank",
            "params": {"query": retrieval_question[:500], "top_k": 12},
            "purpose": f"模板 {BusinessTemplate.DOCUMENT.value}：重排序",
        },
    ]
    if sec:
        steps.append(
            {
                "step_id": "s3",
                "op_code": "chroma_section_expand",
                "params": {"section_prefix": sec, "max_chunks": 400},
                "purpose": f"模板 {BusinessTemplate.DOCUMENT.value}：章节扩展 {sec}",
            }
        )
    logger.info("[ZNWD][Template] %s section_prefix=%r", BusinessTemplate.DOCUMENT.value, sec)
    return _wrap_program(
        template=BusinessTemplate.DOCUMENT,
        intent=Intent.STANDARD_CHAPTER,
        intent_summary="标准/章节文档检索",
        slots=slots,
        answer_mode="database_direct",
        steps=steps,
    )


def _build_project_to_method_program(
    question: str, retrieval_question: str, proj_kw: str
) -> Dict[str, Any]:
    slots = BusinessSlots(
        from_type="TestProject",
        to_type="TestMethod",
        rel_type="CON",
        focus=proj_kw,
    )
    # 单步：按 TestProject.Name 模糊匹配后查 CON→TestMethod（与 JQCX methods 接口同路径，避免 s1 无 Id 导致 s2 为空）
    return _wrap_program(
        template=BusinessTemplate.PROJECT_TO_METHOD,
        intent=Intent.RELATION_QUERY,
        intent_summary=f"按测试项目名称「{proj_kw}」查询关联测试方法",
        slots=slots,
        answer_mode="llm_synthesize",
        focus_entity=proj_kw,
        steps=[
            _step_relation_one_hop(
                "s1",
                "TestProject",
                "TestMethod",
                "CON",
                value=proj_kw,
                match_mode="fuzzy_any",
                where_side="from",
                limit=200,
                purpose=f"在清单库中查找测试项目「{proj_kw}」及其关联的测试方法",
                slots=slots,
            ),
        ],
    )


def _build_equip_discovery_program(project_filter: str) -> Dict[str, Any]:
    slots = BusinessSlots(
        from_type="Equipment",
        to_type="TestProject",
        rel_type="CON",
        project_filter=project_filter,
        focus=project_filter,
    )
    return _wrap_program(
        template=BusinessTemplate.EQUIP_DISCOVERY,
        intent=Intent.RELATION_QUERY,
        intent_summary=f"列出关联「{project_filter}」相关测试项目的设备",
        slots=slots,
        answer_mode="database_direct",
        focus_entity=project_filter,
        steps=[
            _step_relation_one_hop(
                "s1",
                "Equipment",
                "TestProject",
                "CON",
                value=project_filter,
                where_side="to",
                purpose=f"模板 {BusinessTemplate.EQUIP_DISCOVERY.value}：按项目筛设备",
                slots=slots,
            ),
        ],
    )


def _build_rel_one_hop_program(
    from_type: str,
    to_type: str,
    rel_type: str,
    focus: str,
    *,
    match_mode: str = "fuzzy",
    where_side: str = "from",
) -> Optional[Dict[str, Any]]:
    if not _relation_allowed(from_type, rel_type, to_type):
        logger.warning(
            "[ZNWD][Template] 非法关系 %s-[%s]->%s，放弃 one_hop",
            from_type,
            rel_type,
            to_type,
        )
        return None
    slots = BusinessSlots(
        from_type=from_type,
        to_type=to_type,
        rel_type=rel_type,
        focus=focus,
        path=[(from_type, rel_type, to_type)],
    )
    from_zh = _NEO_LABEL_ZH.get(from_type, from_type)
    to_zh = _NEO_LABEL_ZH.get(to_type, to_type)
    return _wrap_program(
        template=BusinessTemplate.REL_ONE_HOP,
        intent=Intent.RELATION_QUERY,
        intent_summary=f"查询{from_zh}「{focus}」关联的{to_zh}",
        slots=slots,
        answer_mode="database_direct",
        focus_entity=focus,
        steps=[
            _step_relation_one_hop(
                "s1",
                from_type,
                to_type,
                rel_type,
                value=focus,
                match_mode=match_mode,
                where_side=where_side,
                purpose=f"模板 {BusinessTemplate.REL_ONE_HOP.value}：{from_type}→{to_type}",
                slots=slots,
            ),
        ],
    )


def _build_rel_two_hop_program(
    from_type: str,
    focus: str,
    path: List[Tuple[str, str, str]],
) -> Optional[Dict[str, Any]]:
    if len(path) < 2:
        return None
    for ft, rt, tt in path:
        if not _relation_allowed(ft, rt, tt):
            logger.warning("[ZNWD][Template] 非法路径 %s-[%s]->%s", ft, rt, tt)
            return None
    slots = BusinessSlots(from_type=from_type, focus=focus, path=path)
    steps: List[Dict[str, Any]] = []
    ft0, rt0, tt0 = path[0]
    steps.append(
        _step_relation_one_hop(
            "s1",
            ft0,
            tt0,
            rt0,
            value=focus,
            purpose=f"模板 {BusinessTemplate.REL_TWO_HOP.value}：第1跳 {ft0}→{tt0}",
            slots=slots,
        )
    )
    ft1, rt1, tt1 = path[1]
    ref_target = "m" if ft0 == "Equipment" and tt0 == "TestProject" else "n"
    steps.append(
        _step_relation_one_hop(
            "s2",
            ft1,
            tt1,
            rt1,
            from_field="Id",
            from_values_ref={"step_id": "s1", "target": ref_target, "field": "Id"},
            limit=200,
            purpose=f"模板 {BusinessTemplate.REL_TWO_HOP.value}：第2跳 {ft1}→{tt1}",
            slots=slots,
        )
    )
    to_zh = _NEO_LABEL_ZH.get(path[-1][2], path[-1][2])
    from_zh = _NEO_LABEL_ZH.get(from_type, from_type)
    return _wrap_program(
        template=BusinessTemplate.REL_TWO_HOP,
        intent=Intent.RELATION_QUERY,
        intent_summary=f"查询{from_zh}「{focus}」关联的{to_zh}（两步）",
        slots=slots,
        answer_mode="database_direct",
        focus_entity=focus,
        steps=steps,
    )


def _build_from_explicit_template(
    template_id: str,
    routing: Any,
    intent_res: IntentResult,
    question: str,
    retrieval_question: str,
    messages: List[Dict[str, Any]],
    driver: Any,
) -> Optional[Dict[str, Any]]:
    """LLM/规则显式指定 business_template 时组装 program。"""
    _ = intent_res, messages, driver
    focus = str(getattr(routing, "focus", "") or "").strip()
    from_type = str(getattr(routing, "from_type", "") or "").strip()
    to_type = str(getattr(routing, "to_type", "") or "").strip()
    target_kind = str(getattr(routing, "target_kind", "") or "unknown").strip()
    if not to_type:
        to_type = _target_to_node_type(target_kind)

    tid = (template_id or "").upper()
    if tid == BusinessTemplate.ENUM.value:
        return _build_enum_program(_norm(question) or question)

    if tid == BusinessTemplate.DOCUMENT.value:
        return _build_document_program(question, retrieval_question)

    if tid == BusinessTemplate.PROJECT_TO_METHOD.value and focus:
        return _build_project_to_method_program(question, retrieval_question, focus)

    if tid == BusinessTemplate.EQUIP_DISCOVERY.value and focus:
        return _build_equip_discovery_program(focus)

    if tid == BusinessTemplate.REL_ONE_HOP.value and focus and from_type and to_type:
        return _build_rel_one_hop_program(from_type, to_type, "CON", focus)

    if tid == BusinessTemplate.REL_TWO_HOP.value and focus and from_type:
        path = _two_hop_path(
            from_type, "method" if to_type == "TestMethod" else target_kind
        )
        return _build_rel_two_hop_program(from_type, focus, path)

    return None


def build_fast_program_from_template(
    intent_res: IntentResult,
    question: str,
    retrieval_question: str,
    messages: List[Dict[str, Any]],
    driver=None,
    routing: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    按意图解析业务模板并生成快速 program；无法匹配模板时返回 None。

    routing: RoutingDecision（LLM/规则路由），可提供 focus、from_type、business_template。
    """
    intent = intent_res.intent
    q_plain = _norm(question) or _norm(retrieval_question)

    if routing is not None:
        skip = getattr(routing, "skip_retrieval", False)
        tpl = str(getattr(routing, "business_template", "") or "").strip()
        if skip or tpl == "BIZ_DIRECT_ANSWER":
            return None
        if tpl == "BIZ_AGENT":
            return None
        prog = _build_from_explicit_template(
            tpl,
            routing,
            intent_res,
            question,
            retrieval_question,
            messages,
            driver,
        )
        if prog:
            if getattr(routing, "plan_user_nl", ""):
                prog["plan_inferred_question"] = routing.plan_user_nl
                prog["intent_summary_user"] = routing.plan_user_nl
            return prog

    if intent == Intent.ENUMERATION:
        return _build_enum_program(q_plain or question)

    if intent == Intent.STANDARD_CHAPTER:
        return _build_document_program(question, retrieval_question)

    if intent != Intent.RELATION_QUERY:
        return None

    tgt0 = relation_target_kind(q_plain or question)
    proj_kw = _extract_test_project_keyword_for_method_question(question, retrieval_question)
    if tgt0 == "method" and len(proj_kw) >= 2:
        logger.info(
            "[ZNWD][Template] %s project_keyword=%r",
            BusinessTemplate.PROJECT_TO_METHOD.value,
            proj_kw[:60],
        )
        return _build_project_to_method_program(question, retrieval_question, proj_kw)

    focus_early = _fast_path_focus_entity(messages, question, retrieval_question, driver)
    if tgt0 == "method" and len(focus_early) >= 2:
        if _looks_like_test_project_name(focus_early) or driver:
            resolved = _preflight_focus_node_type(driver, focus_early) if driver else None
            if resolved == "TestProject" or (
                not resolved and _looks_like_test_project_name(focus_early)
            ):
                logger.info(
                    "[ZNWD][Template] %s focus_as_project=%r resolved=%s",
                    BusinessTemplate.PROJECT_TO_METHOD.value,
                    focus_early[:60],
                    resolved,
                )
                return _build_project_to_method_program(
                    question, retrieval_question, focus_early
                )

    if tgt0 == "equipment":
        pk = _extract_project_keyword_for_equipment_discovery(question, retrieval_question)
        if len(pk) >= 1:
            logger.info(
                "[ZNWD][Template] %s project_filter=%r",
                BusinessTemplate.EQUIP_DISCOVERY.value,
                pk[:40],
            )
            return _build_equip_discovery_program(pk)

    focus = _fast_path_focus_entity(messages, question, retrieval_question, driver)
    if routing is not None and getattr(routing, "focus", ""):
        focus = str(routing.focus).strip() or focus
    if not focus or len(focus) < 2:
        logger.info("[ZNWD][Template] REL 缺少焦点实体，放弃快速路径")
        return None

    tgt = relation_target_kind(q_plain or question)
    if routing is not None and getattr(routing, "target_kind", ""):
        tk = str(routing.target_kind).strip()
        if tk and tk != "unknown":
            tgt = tk

    preflight_node_type = (
        str(getattr(routing, "from_type", "") or "").strip()
        if routing is not None and getattr(routing, "from_type", "")
        else None
    ) or _preflight_focus_node_type(driver, focus)
    if preflight_node_type:
        # 如果预检索找到了明确的节点类型，直接使用
        from_type = preflight_node_type
        logger.info(
            "[ZNWD][Template] 使用预检索结果: from_type=%s (关键词「%s」实际是 %s)",
            from_type, focus, preflight_node_type,
        )
    else:
        # 否则使用原有的启发式推断
        from_type = _infer_from_type_for_relation(q_plain or question, focus, tgt)
        logger.info(
            "[ZNWD][Template] 使用启发式推断: from_type=%s",
            from_type,
        )
    
    to_type = _target_to_node_type(tgt)

    # 设备类型 → 设备（HAS）
    if from_type == "EquipmentType" and to_type == "Equipment":
        if not _relation_allowed("EquipmentType", "HAS", "Equipment"):
            return None
        logger.info(
            "[ZNWD][Template] %s HAS focus=%r",
            BusinessTemplate.REL_ONE_HOP.value,
            focus[:60],
        )
        return _build_rel_one_hop_program(
            "EquipmentType", "Equipment", "HAS", focus, match_mode="fuzzy", where_side="from"
        )

    # 预检索已判定为测试项目 → 直接「项目→方法」（先查节点再查关系）
    if tgt == "method" and preflight_node_type == "TestProject":
        logger.info(
            "[ZNWD][Template] %s preflight=TestProject focus=%r",
            BusinessTemplate.PROJECT_TO_METHOD.value,
            focus[:60],
        )
        return _build_project_to_method_program(question, retrieval_question, focus)

    # 问句主体为「XX测试/试验」+ 方法：禁止误走环境两步（如 电池高温测试）
    proj_for_method = _extract_test_project_keyword_for_method_question(
        question, retrieval_question
    )
    if tgt == "method" and len(proj_for_method) >= 2:
        logger.info(
            "[ZNWD][Template] %s project_keyword=%r (override env/equip 2-hop)",
            BusinessTemplate.PROJECT_TO_METHOD.value,
            proj_for_method[:60],
        )
        return _build_project_to_method_program(
            question, retrieval_question, proj_for_method
        )

    # 环境/设备 + 方法 → 两步（仅当起点确为环境或设备）
    if tgt == "method" and from_type in ("Equipment", "Environment"):
        path = _two_hop_path(from_type, "method")
        logger.info(
            "[ZNWD][Template] %s from=%s focus=%r hops=2",
            BusinessTemplate.REL_TWO_HOP.value,
            from_type,
            focus[:60],
        )
        return _build_rel_two_hop_program(from_type, focus, path)

    # 指标：单边；若误把环境词当装备名则放弃（后处理会纠正 Environment，此处不强行 Equipment）
    if tgt == "indicator":
        if from_type == "Equipment" and _looks_like_project_or_env_in_equipment_value(focus):
            logger.info("[ZNWD][Template] 焦点疑似环境/项目词，放弃 Equipment→Indicator focus=%r", focus[:40])
            return None
        logger.info(
            "[ZNWD][Template] %s %s→Indicator focus=%r",
            BusinessTemplate.REL_ONE_HOP.value,
            from_type,
            focus[:60],
        )
        if from_type == "Environment":
            return None
        return _build_rel_one_hop_program("Equipment", "Indicator", "CON", focus)

    # 项目：Environment 或 Equipment 单边
    if tgt in ("project", "unknown"):
        if from_type == "Environment":
            logger.info(
                "[ZNWD][Template] %s Environment→TestProject focus=%r",
                BusinessTemplate.REL_ONE_HOP.value,
                focus[:60],
            )
            return _build_rel_one_hop_program("Environment", "TestProject", "CON", focus)
        if from_type == "Equipment" and _looks_like_project_or_env_in_equipment_value(focus):
            logger.info("[ZNWD][Template] 焦点疑似项目/环境词，放弃 Equipment→TestProject")
            return None
        logger.info(
            "[ZNWD][Template] %s Equipment→TestProject focus=%r",
            BusinessTemplate.REL_ONE_HOP.value,
            focus[:60],
        )
        return _build_rel_one_hop_program("Equipment", "TestProject", "CON", focus)

    # TestProject / Indicator 起点查方法（名称在 focus）
    if tgt == "method" and from_type in ("TestProject", "Indicator"):
        if not _relation_allowed(from_type, "CON", "TestMethod"):
            return None
        return _build_rel_one_hop_program(from_type, "TestMethod", "CON", focus)

    return None


def template_display_name(program: Dict[str, Any]) -> str:
    tid = str(program.get("_business_template") or "").strip()
    if tid:
        return tid
    return str(program.get("_v5_intent") or "")


__all__ = (
    "BusinessTemplate",
    "BusinessSlots",
    "build_fast_program_from_template",
    "template_display_name",
)
