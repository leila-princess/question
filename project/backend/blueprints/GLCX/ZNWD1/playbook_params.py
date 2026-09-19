"""Playbook 答复参数整理（多装备/环境槽位）。"""

from __future__ import annotations

from typing import Any, Dict, List

from .user_biz_catalog import UBIZ_TEST_PROJECT


def _as_str_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def merge_playbook_answer_params(
    patch: Dict[str, Any],
    draft: Dict[str, Any],
    *,
    user_biz: str = "",
    scenario: str = "",
) -> Dict[str, Any]:
    """合并 patch 与 draft，并修正多装备+环境场景下的 focus/检索参数。"""
    out: Dict[str, Any] = {**patch, **draft}
    eq_kws = _as_str_list(out.get("equipment_keywords"))
    env_kws = _as_str_list(out.get("environment_keywords"))

    if user_biz == UBIZ_TEST_PROJECT and scenario == "by_equip_and_env":
        out["from_type"] = "Equipment"
        out["to_type"] = "TestProject"
        out["rel_type"] = "CON"
        out["where_side"] = "from"
        if env_kws:
            out["environment_label"] = env_kws[0]
        if len(eq_kws) > 1:
            out["equipment_keywords"] = eq_kws
            out["value_list"] = eq_kws
            out["match_mode"] = "fuzzy_any"
            out["focus"] = ""
        elif len(eq_kws) == 1:
            out["focus"] = eq_kws[0]
            out["value_list"] = eq_kws
        # 避免 focus 被环境名覆盖（LLM 偶发填错）
        if out.get("focus") in env_kws:
            out["focus"] = eq_kws[0] if len(eq_kws) == 1 else ""

    return out
