"""ZNWD6 引导式精准执行：跳过 τ 探测与 LLM 路由，直接按 template_id + bindings 检索。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Generator, List, Optional, Tuple

from blueprints.GLCX.ZNWD4.znwd4_thinking_emit import emit_milestone

from .ontology_loader import get_template, list_template_ids, slot_key_for_node_type
from .plan_builder import build_route_plan
from .plan_verify import verify_plan

logger = logging.getLogger(__name__)

# 引导执行时仍需要 LLM 合成答案的模板（关系/枚举类保持 templates.yaml 的 db_direct）
_GUIDED_LLM_ANSWER_TEMPLATES = frozenset(
    {
        "environment_properties",
        "composite_env_settings",
        "method_by_project_or_indicator",
    }
)


def normalize_guided_bindings(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for b in raw or []:
        if not isinstance(b, dict):
            continue
        name = str(b.get("name") or "").strip()
        if not name:
            continue
        node_type = str(b.get("node_type") or b.get("nodeType") or "").strip()
        slot_key = str(b.get("slot_key") or b.get("slotKey") or slot_key_for_node_type(node_type)).strip()
        entity_id = str(b.get("entity_id") or b.get("entityId") or b.get("id") or "").strip()
        row: Dict[str, Any] = {
            "name": name,
            "node_type": node_type,
            "slot_key": slot_key,
        }
        role = str(b.get("role") or "").strip().lower()
        if role in ("source", "target", "anchor", "goal"):
            row["role"] = role
        if entity_id:
            row["entity_id"] = entity_id
        out.append(row)
    return out


def _build_tau_from_bindings(bindings: List[Dict[str, Any]]) -> Dict[str, Any]:
    flat: List[Dict[str, Any]] = []
    spans: List[Dict[str, Any]] = []
    for b in bindings:
        role = str(b.get("role") or "source").lower()
        if role in ("target", "goal"):
            continue
        nt = str(b.get("node_type") or "")
        name = str(b.get("name") or "")
        eid = str(b.get("entity_id") or name)
        if not name:
            continue
        cand = {
            "entity_id": eid,
            "node_type": nt,
            "name": name,
            "id": eid,
        }
        flat.append(cand)
        spans.append({"span": name, "candidates": [cand]})
    return {
        "spans": spans,
        "flat_candidates": flat,
        "zvec_used": False,
        "guided": True,
    }


def _maybe_force_llm_synthesis(plan: Dict[str, Any], template_id: str) -> Dict[str, Any]:
    if template_id not in _GUIDED_LLM_ANSWER_TEMPLATES:
        return plan
    tpl = get_template(template_id)
    if str(tpl.get("answer_strategy") or "") == "db_synthesize":
        return plan
    out = dict(plan)
    out["answer_strategy"] = "db_synthesize"
    out["answer_mode"] = "synthesize"
    return out


def build_guided_plan(
    question: str,
    *,
    template_id: str,
    bindings: List[Dict[str, Any]],
    driver: Any = None,
    goal_type: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    tid = (template_id or "").strip()
    if not tid or tid not in list_template_ids():
        return {}, [f"invalid template_id: {tid!r}"]

    norm = normalize_guided_bindings(bindings)
    binding: Dict[str, Any] = {
        "bindings": norm,
        "template_id": tid,
        "intent": f"guided:{tid}",
        "doc_hints": [],
        "guided": True,
    }
    gt = (goal_type or "").strip()
    if not gt:
        for b in norm:
            if str(b.get("role") or "").lower() in ("target", "goal") and b.get("node_type"):
                gt = str(b["node_type"])
                break
    if gt:
        binding["goal_type"] = gt
    for b in norm:
        sk = str(b.get("slot_key") or "")
        if sk in ("standard_keywords",) and b.get("name"):
            binding["doc_hints"].append(str(b["name"]))

    tau = _build_tau_from_bindings(norm)
    plan = build_route_plan(question, tau, binding, template_id=tid, driver=driver)
    plan = _maybe_force_llm_synthesis(plan, tid)
    plan["guided_execute"] = True
    plan["skip_method_draft_fallback"] = True
    if gt:
        plan["goal_node_type"] = gt
    verr, plan = verify_plan(plan, tau)
    return plan, verr


def stream_znwd6_guided_execute(
    client: Any,
    model: str,
    question: str,
    *,
    template_id: str,
    bindings: List[Dict[str, Any]],
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    goal_type: Optional[str] = None,
    intent_id: Optional[str] = None,
    neo4j_driver: Any = None,
    chroma_collection: Any = None,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    q = (question or "").strip()
    tid = (template_id or "").strip()
    if not q:
        yield ("error", {"message": "问题不能为空"})
        return
    if not tid:
        yield ("error", {"message": "templateId 不能为空"})
        return

    yield (
        "step",
        {
            "type": "routing",
            "status": "loading",
            "message": "正在按引导选择精准检索…",
            "phase": "znwd6_guided",
        },
    )
    yield emit_milestone(f"引导检索：{tid}", kind="route")

    plan, verr = build_guided_plan(
        q,
        template_id=tid,
        bindings=bindings,
        driver=neo4j_driver,
        goal_type=goal_type,
    )
    if not plan:
        yield ("error", {"message": verr[0] if verr else "无法构建检索计划"})
        return
    if verr:
        logger.warning("[ZNWD6][guided] plan verify: %s", verr)

    norm = normalize_guided_bindings(bindings)
    yield emit_milestone(f"已选定检索模板：{tid}", kind="route")
    yield (
        "step",
        {
            "type": "routing",
            "status": "done",
            "message": f"引导路由完成（{tid}）",
            "phase": "znwd6_guided_plan",
            "templateId": tid,
            "candidateCount": len(norm),
            "bindingCount": len(norm),
            "zvecUsed": False,
            "guided": True,
        },
    )

    from blueprints.GLCX.ZNWD4.pipeline import stream_znwd4_chat

    yield from stream_znwd4_chat(
        client,
        model,
        q,
        user_id=user_id,
        session_id=session_id,
        neo4j_driver=neo4j_driver,
        chroma_collection=chroma_collection,
        llm_limits=llm_limits,
        skip_instant_thinking=True,
        initial_plan=plan,
    )
