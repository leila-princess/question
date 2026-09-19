"""
ZNWD5 三层路由白名单：Tier0 无槽快路径 / Tier1 UBIZ FAQ 程序锁 / Tier2 Action Planner。
"""

from __future__ import annotations

import re
from typing import Literal, Optional, Set

from blueprints.GLCX.ZNWD4.question_route_lock import RouteLock, lock_is_slotless
from blueprints.GLCX.ZNWD4.routing_catalog import (
    PURPOSE_CHAT,
    PURPOSE_META,
    _route_id_playbook,
)
from blueprints.GLCX.ZNWD4.user_biz_catalog import SCENARIO_REGISTRY

RoutingTier = Literal["tier0", "faq", "complex"]

# 8 类 UBIZ SCENARIO_REGISTRY → route_id 白名单（不含 COMP_*）
FAQ_ROUTE_IDS: Set[str] = set()
for _ub, _sc in SCENARIO_REGISTRY:
    if not str(_ub).startswith("UBIZ_"):
        continue
    FAQ_ROUTE_IDS.add(_route_id_playbook(_ub, _sc))

# 文档章节锁（registry 有 scenario，resolve 用独立 reason）
FAQ_ROUTE_IDS.add("R-STANDARD-chapter_content")

FAQ_REASONS: Set[str] = {
    "equipment_test_projects",
    "environment_test_projects",
    "equip_and_env_test_projects",
    "projects_by_method",
    "enum_test_project",
    "equipment_indicators",
    "enum_indicator",
    "project_methods",
    "indicator_methods",
    "method_content",
    "enum_equipment",
    "equipment_under_type",
    "equipment_by_project",
    "equipment_by_indicator",
    "enum_equipment_type",
    "level2_equipment_types",
    "equipment_belongs_to_type",
    "enum_environment",
    "environment_properties",
    "project_environment",
    "project_standards",
    "standard_metadata",
    "standard_condition_param",
    "document_keyword",
    "document_chapter",
}

COMPOSITE_REASONS: Set[str] = {
    "composite_project_env_method",
    "composite_project_method",
    "composite_indicator_method",
    "composite_env_settings",
}

COMPOSITE_ROUTE_PREFIX = "R-C-"


def znwd5_action_planner_enabled() -> bool:
    import os

    return (os.environ.get("ZNWD5_ROUTING_MODE") or "legacy").strip().lower() == "action_planner"


def is_composite_lock(lock: Optional[RouteLock]) -> bool:
    if not lock:
        return False
    rid = str(lock.route_id or "")
    if rid.startswith(COMPOSITE_ROUTE_PREFIX):
        return True
    return str(lock.reason or "") in COMPOSITE_REASONS


def is_tier0_lock(lock: Optional[RouteLock]) -> bool:
    """chat / meta / 无槽 enum：0-LLM 快路径。"""
    if not lock:
        return False
    if lock.purpose in (PURPOSE_CHAT, PURPOSE_META):
        return True
    rid = str(lock.route_id or "")
    if rid in ("R-META-stats", "R-CHAT-direct"):
        return True
    return lock_is_slotless(lock)


def _looks_like_project_method_combo(question: str) -> bool:
    q = (question or "").strip()
    return bool(
        re.search(r"(试验|测试)项目", q)
        and re.search(r"(试验|测试)方法", q)
    )


def is_faq_whitelist_lock(lock: Optional[RouteLock], question: str = "") -> bool:
    """8 类 UBIZ 单场景 FAQ 程序锁（含槽位，需 Zvec + 槽位 LLM 校验）。"""
    if not lock or is_tier0_lock(lock) or is_composite_lock(lock):
        return False
    if lock.reason == "method_content" and _looks_like_project_method_combo(question):
        return False
    rid = str(lock.route_id or "")
    reason = str(lock.reason or "")
    return rid in FAQ_ROUTE_IDS or reason in FAQ_REASONS


def lock_tier(lock: Optional[RouteLock]) -> RoutingTier:
    if is_tier0_lock(lock):
        return "tier0"
    if is_faq_whitelist_lock(lock):
        return "faq"
    return "complex"


def filter_lock_for_planner_mode(lock: Optional[RouteLock]) -> Optional[RouteLock]:
    """action_planner 模式下丢弃 composite 锁，交给 Action Planner。"""
    if not lock:
        return None
    if znwd5_action_planner_enabled() and is_composite_lock(lock):
        return None
    return lock


__all__ = [
    "FAQ_ROUTE_IDS",
    "FAQ_REASONS",
    "COMPOSITE_REASONS",
    "RoutingTier",
    "filter_lock_for_planner_mode",
    "is_composite_lock",
    "is_faq_whitelist_lock",
    "is_tier0_lock",
    "lock_tier",
    "znwd5_action_planner_enabled",
]
