"""ZNWD6 注入计划不应在 reconcile 阶段被降为全库枚举。"""

from blueprints.GLCX.ZNWD4.plan_reconcile import reconcile_route_plan
from blueprints.GLCX.ZNWD4.question_route_lock import resolve_route_lock
from blueprints.GLCX.ZNWD6.plan_builder import build_route_plan


def _build_equip_indicator_plan(question: str) -> dict:
    binding = {
        "bindings": [
            {
                "node_type": "Equipment",
                "name": "无人机",
                "entity_id": "ZB_1",
                "slot_key": "equipment",
            }
        ],
        "template_id": "equip_to_indicators",
    }
    tau = {"flat_candidates": [{"node_type": "Equipment", "name": "无人机", "entity_id": "ZB_1"}]}
    return build_route_plan(question, tau, binding, template_id="equip_to_indicators")


def test_route_lock_recognizes_de_indicator_phrase():
    lock = resolve_route_lock("无人机的考核指标有哪些")
    assert lock is not None
    assert lock.route_id == "R-INDICATOR-by_equipment"


def test_reconcile_keeps_by_equipment_for_de_phrase():
    q = "无人机的考核指标有哪些"
    plan = _build_equip_indicator_plan(q)
    rec = reconcile_route_plan(plan, question=q)
    assert rec.get("scenario") == "by_equipment"
    assert rec.get("business_id") == "BIZ_REL_ONE_HOP"
    assert rec.get("playbook") is True


def test_reconcile_keeps_by_equipment_for_standard_phrase():
    q = "无人机有哪些考核指标"
    plan = _build_equip_indicator_plan(q)
    rec = reconcile_route_plan(plan, question=q)
    assert rec.get("scenario") == "by_equipment"
    assert rec.get("business_id") == "BIZ_REL_ONE_HOP"
