"""
多轮追问：问句消解 + 结构化会话状态（Redis session_context）。

供 ZNWD6 主路径与 v6 Preflight 共用。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

QUERY_TARGET_PROJECT = "project"
QUERY_TARGET_INDICATOR = "indicator"
QUERY_TARGET_METHOD = "method"
QUERY_TARGET_ENVIRONMENT = "environment"

_TEMPLATE_BY_TARGET: Dict[str, str] = {
    QUERY_TARGET_PROJECT: "equip_to_projects",
    QUERY_TARGET_INDICATOR: "equip_to_indicators",
    QUERY_TARGET_METHOD: "project_to_methods",
    QUERY_TARGET_ENVIRONMENT: "env_to_projects",
}

_TARGET_PHRASE: Dict[str, str] = {
    QUERY_TARGET_PROJECT: "试验项目",
    QUERY_TARGET_INDICATOR: "考核指标",
    QUERY_TARGET_METHOD: "试验方法",
    QUERY_TARGET_ENVIRONMENT: "试验环境",
}

# 仅换查询目标的短追问：「考核指标呢」「试验项目呢」
_TARGET_ONLY_PATTERNS: List[Tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^(?:考核|性能|试验)?指标呢[？?！!。.\s]*$", re.I), QUERY_TARGET_INDICATOR, "有哪些考核指标"),
    (re.compile(r"^(?:试验|测试|实验)?项目呢[？?！!。.\s]*$", re.I), QUERY_TARGET_PROJECT, "有哪些试验项目"),
    (re.compile(r"^(?:试验|测试)?方法呢[？?！!。.\s]*$", re.I), QUERY_TARGET_METHOD, "有哪些试验方法"),
    (re.compile(r"^(?:试验|测试)?环境呢[？?！!。.\s]*$", re.I), QUERY_TARGET_ENVIRONMENT, "有哪些试验环境"),
]


@dataclass
class ResolvedQuestion:
    raw_question: str
    resolved_question: str
    is_follow_up: bool = False
    focus_entity: Optional[Dict[str, Any]] = None
    slots: Dict[str, List[str]] = field(default_factory=dict)
    template_hint: Optional[str] = None
    carry_bindings: List[Dict[str, Any]] = field(default_factory=list)
    debug: Dict[str, Any] = field(default_factory=dict)


def context_enrich_enabled() -> bool:
    return os.environ.get("ZNWD6_CONTEXT_ENRICH", "1").strip().lower() not in ("0", "false", "no")


def _get_memory_store():
    """ZNWD6/ZNWD4 与 stream_v4 可能初始化不同 memory_store，优先 ZNWD4。"""
    try:
        from blueprints.GLCX.ZNWD4.memory_store import get_memory_store as get_znwd4_store

        store = get_znwd4_store()
        if store:
            return store
    except Exception:
        pass
    from .memory_store import get_memory_store

    return get_memory_store()


def _empty_slots() -> Dict[str, List[str]]:
    return {
        "equipment": [],
        "environment": [],
        "project": [],
        "indicator": [],
        "standard": [],
        "equipment_type": [],
    }


def _template_to_query_target(template_id: str) -> str:
    tid = (template_id or "").strip()
    if "indicator" in tid:
        return QUERY_TARGET_INDICATOR
    if "method" in tid:
        return QUERY_TARGET_METHOD
    if "env" in tid:
        return QUERY_TARGET_ENVIRONMENT
    if "project" in tid:
        return QUERY_TARGET_PROJECT
    return ""


def _detect_target_only_followup(question: str) -> Optional[Tuple[str, str]]:
    q = (question or "").strip()
    for pat, target, list_phrase in _TARGET_ONLY_PATTERNS:
        if pat.match(q):
            return target, list_phrase
    return None


def _equipment_from_session_ctx(session_ctx: Dict[str, Any]) -> str:
    focus = session_ctx.get("focus_entity") if isinstance(session_ctx.get("focus_entity"), dict) else {}
    name = str(focus.get("name") or "").strip()
    if name:
        return name
    slots = session_ctx.get("slots") if isinstance(session_ctx.get("slots"), dict) else {}
    eq = slots.get("equipment") or []
    if isinstance(eq, list) and eq:
        return str(eq[0] or "").strip()
    last_rq = str(session_ctx.get("last_resolved_question") or session_ctx.get("last_user_question") or "")
    if last_rq:
        from blueprints.GLCX.ZNWD4.znwd4_history_context import _detect_slots

        slots_detected = _detect_slots(last_rq)
        eqs = slots_detected.get("equipment") or []
        if eqs:
            return str(eqs[0] or "").strip()
    return ""


def _binding_from_focus(focus: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not focus:
        return []
    eid = str(focus.get("id") or focus.get("entity_id") or "").strip()
    name = str(focus.get("name") or "").strip()
    ntype = str(focus.get("type") or focus.get("node_type") or "Equipment").strip()
    if not name and not eid:
        return []
    from blueprints.GLCX.ZNWD6.ontology_loader import slot_key_for_node_type

    return [
        {
            "span": name,
            "entity_id": eid,
            "node_type": ntype,
            "name": name,
            "slot_key": slot_key_for_node_type(ntype),
            "role": "source",
        }
    ]


def _compose_equip_target_question(equip: str, list_phrase: str) -> str:
    eq = (equip or "").strip()
    phrase = (list_phrase or "有哪些").strip()
    if not eq:
        return phrase
    if phrase.startswith("有哪些"):
        tail = phrase[3:]
        return f"{eq}有哪些{tail}"
    return f"{eq}的{phrase}"


def resolve_question_context(
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    session_ctx: Optional[Dict[str, Any]] = None,
    client: Any = None,
    model: Optional[str] = None,
) -> ResolvedQuestion:
    """将省略式追问消解为可路由的完整问句。"""
    raw = (question or "").strip()
    if not raw or not context_enrich_enabled():
        return ResolvedQuestion(raw_question=raw, resolved_question=raw)

    ctx = dict(session_ctx or {})
    if not ctx and user_id and session_id:
        store = _get_memory_store()
        if store:
            ctx = store.get_session_context(user_id, session_id)

    # L1：仅换查询目标（考核指标呢）
    target_hit = _detect_target_only_followup(raw)
    if target_hit:
        target, list_phrase = target_hit
        equip = _equipment_from_session_ctx(ctx)
        if equip:
            resolved = _compose_equip_target_question(equip, list_phrase)
            focus = ctx.get("focus_entity") if isinstance(ctx.get("focus_entity"), dict) else None
            slots = dict(ctx.get("slots") or _empty_slots())
            slots.setdefault("equipment", [])
            if equip not in slots["equipment"]:
                slots["equipment"] = [equip] + [x for x in slots["equipment"] if x != equip]
            slots.setdefault("indicator" if target == QUERY_TARGET_INDICATOR else target, [])
            return ResolvedQuestion(
                raw_question=raw,
                resolved_question=resolved,
                is_follow_up=True,
                focus_entity=focus,
                slots=slots,
                template_hint=_TEMPLATE_BY_TARGET.get(target),
                carry_bindings=_binding_from_focus(focus or {"name": equip, "type": "Equipment"}),
                debug={"layer": "target_only", "query_target": target, "equipment": equip},
            )

    # L2：ZNWD4 规则槽位合并
    if user_id and session_id:
        from blueprints.GLCX.ZNWD4.znwd4_history_context import _enrich_short_follow_up_rules

        enriched, snap = _enrich_short_follow_up_rules(raw, user_id=user_id, session_id=session_id)
        if enriched and enriched.strip() != raw:
            enriched_q = enriched.strip()
            try:
                from blueprints.GLCX.ZNWD6.intent_router import infer_template_hint_from_question

                inferred_hint = infer_template_hint_from_question(enriched_q)
            except Exception:
                inferred_hint = None
            return ResolvedQuestion(
                raw_question=raw,
                resolved_question=enriched_q,
                is_follow_up=True,
                focus_entity=ctx.get("focus_entity") if isinstance(ctx.get("focus_entity"), dict) else None,
                slots=dict(ctx.get("slots") or _empty_slots()),
                template_hint=inferred_hint,
                carry_bindings=_binding_from_focus(ctx.get("focus_entity") or {}),
                debug={"layer": "znwd4_rules", "history_rounds": len((snap or {}).get("rounds") or [])},
            )

    # L3：v6 承前主体（messages 可用时）
    if messages:
        from .services_v5 import _build_v5_retrieval_question

        rq = _build_v5_retrieval_question(messages, raw)
        m = re.search(r"【承前主体】\s*([^。；;\n]+)", rq or "")
        if m and m.group(1).strip() not in raw:
            focus_name = m.group(1).strip().split("，")[0].split(",")[0].strip()
            body = re.sub(r"^【承前主体】[^。；;\n]+[。；;\n]?", "", rq).strip()
            resolved = body or rq
            try:
                from blueprints.GLCX.ZNWD6.intent_router import infer_template_hint_from_question

                inferred_hint = infer_template_hint_from_question(resolved)
            except Exception:
                inferred_hint = None
            return ResolvedQuestion(
                raw_question=raw,
                resolved_question=resolved,
                is_follow_up=True,
                focus_entity={"name": focus_name, "type": "Equipment"},
                slots=dict(ctx.get("slots") or _empty_slots()),
                template_hint=inferred_hint,
                carry_bindings=_binding_from_focus(ctx.get("focus_entity") or {"name": focus_name, "type": "Equipment"}),
                debug={"layer": "v5_retrieval", "focus": focus_name},
            )

    # L4：可选 LLM
    if (
        os.environ.get("ZNWD6_CONTEXT_LLM", "0").strip().lower() in ("1", "true", "yes")
        and client
        and model
        and user_id
        and session_id
    ):
        from blueprints.GLCX.ZNWD4.znwd4_llm_context import enrich_question_with_history

        enriched, meta = enrich_question_with_history(
            raw,
            user_id=user_id,
            session_id=session_id,
            client=client,
            model=model,
        )
        if enriched and enriched.strip() != raw:
            enriched_q = enriched.strip()
            try:
                from blueprints.GLCX.ZNWD6.intent_router import infer_template_hint_from_question

                inferred_hint = infer_template_hint_from_question(enriched_q)
            except Exception:
                inferred_hint = None
            return ResolvedQuestion(
                raw_question=raw,
                resolved_question=enriched_q,
                is_follow_up=True,
                focus_entity=ctx.get("focus_entity") if isinstance(ctx.get("focus_entity"), dict) else None,
                slots=dict(ctx.get("slots") or _empty_slots()),
                template_hint=inferred_hint,
                carry_bindings=_binding_from_focus(ctx.get("focus_entity") or {}),
                debug={"layer": "llm", **(meta if isinstance(meta, dict) else {})},
            )

    inferred_hint = None
    try:
        from blueprints.GLCX.ZNWD6.intent_router import infer_template_hint_from_question

        inferred_hint = infer_template_hint_from_question(raw)
    except Exception:
        pass

    return ResolvedQuestion(
        raw_question=raw,
        resolved_question=raw,
        focus_entity=ctx.get("focus_entity") if isinstance(ctx.get("focus_entity"), dict) else None,
        slots=dict(ctx.get("slots") or _empty_slots()),
        template_hint=inferred_hint,
        carry_bindings=_binding_from_focus(ctx.get("focus_entity") or {}) if inferred_hint else [],
        debug={"layer": "none"},
    )


def build_retrieval_question_v6(
    messages: List[Dict[str, Any]],
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Preflight 用：与 resolve_question_context 对齐的检索问句。"""
    resolved = resolve_question_context(
        question,
        user_id=user_id,
        session_id=session_id,
        messages=messages,
    )
    rq = resolved.resolved_question
    focus = (resolved.focus_entity or {}).get("name")
    if focus and "【承前主体】" not in rq and focus not in rq:
        rq = f"【承前主体】{focus}。{rq}"
    from .memory_store import get_memory_store

    store = _get_memory_store() or get_memory_store()
    if store and user_id and session_id:
        sm = store.get_summary(user_id, session_id)
        if sm:
            rq = f"【历史摘要】{sm[:1500]}\n【当前问题】{rq}"
    return rq


def update_session_context_from_plan(
    user_id: Optional[str],
    session_id: Optional[str],
    question: str,
    plan: Dict[str, Any],
) -> None:
    """成功回答后写入会话状态，供下一轮追问继承。"""
    if not user_id or not session_id or not isinstance(plan, dict):
        return
    from .memory_store import get_memory_store

    store = _get_memory_store() or get_memory_store()
    if not store:
        return

    template_id = str(plan.get("template_id") or "").strip()
    binding = plan.get("znwd6_binding")
    if not isinstance(binding, dict):
        binding = (plan.get("probe_snapshot") or {}).get("binding") if isinstance(plan.get("probe_snapshot"), dict) else {}
    if not isinstance(binding, dict):
        binding = {}

    focus_entity: Optional[Dict[str, Any]] = None
    slots = _empty_slots()
    for b in binding.get("bindings") or []:
        if not isinstance(b, dict):
            continue
        ntype = str(b.get("node_type") or "").strip()
        name = str(b.get("name") or "").strip()
        eid = str(b.get("entity_id") or "").strip()
        sk = str(b.get("slot_key") or "").strip()
        if sk and name:
            slots.setdefault(sk, [])
            if name not in slots[sk]:
                slots[sk].append(name)
        if ntype == "Equipment" and name and not focus_entity:
            focus_entity = {"type": "Equipment", "id": eid, "name": name}

    focus_name = str(plan.get("focus") or "").strip()
    if not focus_entity and focus_name:
        focus_entity = {"type": "Equipment", "id": "", "name": focus_name}
        slots.setdefault("equipment", [])
        if focus_name not in slots["equipment"]:
            slots["equipment"].insert(0, focus_name)

    params = plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {}
    for kw in params.get("equipment_keywords") or []:
        k = str(kw).strip()
        if k:
            slots.setdefault("equipment", [])
            if k not in slots["equipment"]:
                slots["equipment"].append(k)
            if not focus_entity:
                focus_entity = {"type": "Equipment", "id": str(params.get("equipment_id") or ""), "name": k}

    ctx = {
        "focus_entity": focus_entity,
        "last_template_id": template_id,
        "last_query_target": _template_to_query_target(template_id),
        "slots": slots,
        "last_user_question": (question or "").strip(),
        "last_resolved_question": str(plan.get("retrieval_question") or question or "").strip(),
    }
    try:
        store.set_session_context(user_id, session_id, ctx)
        logger.info(
            "[session_context] updated session=%s focus=%s template=%s",
            (session_id or "")[:12],
            (focus_entity or {}).get("name"),
            template_id,
        )
    except Exception as exc:
        logger.warning("[session_context] update failed: %s", exc)
