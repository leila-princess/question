"""调用 LLM 生成因果骨架 JSON。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from blueprints.FZSJ.JYLLM.multi_agent_engine import call_llm, extract_json_from_text

from .prompts import SKELETON_SYSTEM, build_skeleton_user_prompt

logger = logging.getLogger(__name__)


def generate_causal_skeleton(
    user_description: str,
    chroma_context: str,
    action_candidates: List[Dict[str, Any]],
    *,
    llm_model_key: str = "",
    temperature: float = 0.35,
) -> Dict[str, Any]:
    user_prompt = build_skeleton_user_prompt(user_description, chroma_context, action_candidates)
    raw = call_llm(
        [
            {"role": "system", "content": SKELETON_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=4096,
        llm_model_key=llm_model_key,
    )
    parsed = extract_json_from_text(raw or "")
    if parsed and parsed.get("nodes"):
        return parsed

    logger.warning("LLM skeleton parse failed, using rule-based fallback")
    return _fallback_skeleton(user_description, action_candidates)


def _fallback_skeleton(description: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    nodes = []
    edges = []
    prev = None
    for i, c in enumerate(candidates[:6]):
        nid = f"n{i+1}"
        nodes.append(
            {
                "id": nid,
                "name": c.get("name"),
                "method_id": c.get("id"),
                "phase": ["预处理", "环境", "实施", "恢复", "检测"][min(i, 4)],
            }
        )
        if prev:
            edges.append({"from": prev, "to": nid})
        prev = nid
    if not nodes:
        nodes = [
            {"id": "n1", "name": "试验前检查", "method_id": None, "phase": "预处理"},
            {"id": "n2", "name": "环境条件建立", "method_id": None, "phase": "环境"},
            {"id": "n3", "name": "试验实施", "method_id": None, "phase": "实施"},
            {"id": "n4", "name": "恢复与试验后检测", "method_id": None, "phase": "检测"},
        ]
        edges = [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}, {"from": "n3", "to": "n4"}]
    return {
        "task_summary": description[:120],
        "equipment": "",
        "environment": "",
        "standards": [],
        "nodes": nodes,
        "edges": edges,
    }
