"""
领域词守卫：清单库（Neo4j/Chroma）优先，禁止把图谱业务问句误路由为 direct_llm。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from .business_catalog import BIZ_ENUM_NODES, BIZ_REL_ONE_HOP
from .enum_inference import looks_like_enumeration
from .keyword_probe import probe_keyword

# 命中任一词则必须先尝试图库/文档库（不可无库直答）
_GRAPH_DOMAIN_RE = re.compile(
    r"(考核指标|性能指标|试验指标|测试指标|指标|设备类型|装备类型|设备|装备|"
    r"试验环境|测试环境|环境|试验项目|测试项目|项目|试验方法|测试方法|方法|"
    r"标准|规范|GJB|GB/?T|synr|清单库|图库|neo4j)",
    re.I,
)

_VAGUE_INDICATOR_ONLY = re.compile(
    r"^(考核指标|性能指标|试验指标|测试指标|指标)(呢|吗|呀|啊|哈)?\s*[？?]?$",
    re.I,
)

_SHORT_INDICATOR = re.compile(r"(考核指标|性能指标|试验指标|指标)", re.I)


def must_use_graph_database(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _VAGUE_INDICATOR_ONLY.match(t):
        return True
    return bool(_GRAPH_DOMAIN_RE.search(t))


def looks_like_vague_indicator_only(text: str) -> bool:
    return bool(_VAGUE_INDICATOR_ONLY.match((text or "").strip()))


def _last_user_from_plan_or_store(
    plan: Dict[str, Any],
    *,
    user_id: Optional[str],
    session_id: Optional[str],
) -> str:
    snap = plan.get("history_snapshot") if isinstance(plan.get("history_snapshot"), dict) else {}
    rounds = snap.get("rounds") if isinstance(snap.get("rounds"), list) else []
    if not rounds and user_id and session_id:
        from .znwd1_history_context import load_history_from_store

        snap = load_history_from_store(user_id, session_id, n=3)
        rounds = snap.get("rounds") or []
    for r in reversed(rounds):
        u = str((r or {}).get("user") or "").strip()
        if u:
            return u
    return ""


def infer_indicator_fallback(
    question: str,
    plan: Dict[str, Any],
    *,
    driver: Any = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    模糊指标问句（如「考核指标呢」）→ 优先列举 Indicator；
    若上文/历史涉及某设备，则查 Equipment→Indicator。
    """
    q = (question or "").strip()
    if not (_VAGUE_INDICATOR_ONLY.match(q) or (_SHORT_INDICATOR.search(q) and len(q) <= 16)):
        return None

    prev_u = _last_user_from_plan_or_store(plan, user_id=user_id, session_id=session_id)
    if prev_u and driver and ("设备" in prev_u or "装备" in prev_u or "坦克" in prev_u or "汽车" in prev_u):
        probe = probe_keyword(driver, prev_u[:80], label_hints=["Equipment"])
        best = (probe or {}).get("best") if isinstance(probe, dict) else None
        if isinstance(best, dict) and best.get("node_type") == "Equipment":
            focus = str(best.get("name") or "").strip()
            params: Dict[str, Any] = {
                "from_type": "Equipment",
                "to_type": "Indicator",
                "rel_type": "CON",
                "where_side": "from",
                "focus": focus,
            }
            fid = str(best.get("id") or "").strip()
            if fid:
                params["focus_id"] = fid
            if focus:
                return BIZ_REL_ONE_HOP, params

    if looks_like_enumeration(q) or _VAGUE_INDICATOR_ONLY.match(q) or _SHORT_INDICATOR.search(q):
        return BIZ_ENUM_NODES, {"node_type": "Indicator"}

    return BIZ_ENUM_NODES, {"node_type": "Indicator"}
