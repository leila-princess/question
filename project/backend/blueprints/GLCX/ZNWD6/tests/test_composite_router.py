from blueprints.GLCX.ZNWD6.composite_router import detect_composite_template
from blueprints.GLCX.ZNWD6.intent_router import suggest_template
from blueprints.GLCX.ZNWD6.ontology_loader import load_ontology
from blueprints.GLCX.ZNWD6.plan_builder import build_route_plan


def test_composite_project_and_indicator():
    tid = detect_composite_template("通信设备A有哪些试验项目和考核指标？")
    assert tid == "composite_project_and_indicator"


def test_composite_project_then_method():
    tid = detect_composite_template("请先告诉我通信设备A有哪些试验项目，再告诉我这些项目对应的试验方法")
    assert tid == "composite_project_then_method"


def test_specific_equip_not_enum():
    load_ontology.cache_clear()
    tid, reason = suggest_template(
        "通信设备A有哪些试验项目",
        {"flat_candidates": [{"node_type": "Equipment", "name": "通信设备A", "entity_id": "1"}]},
    )
    assert tid == "equip_to_projects"
    assert reason in ("specific_equip_project", "composite", "type_equipment", "enum_override_equip")


def test_global_enum_still_works():
    load_ontology.cache_clear()
    tid, _ = suggest_template("有哪些试验项目", {"flat_candidates": []})
    assert tid == "enum_by_focus_type"


def test_composite_plan_route_id():
    load_ontology.cache_clear()
    plan = build_route_plan(
        "通信设备A有哪些试验项目和方法",
        {"flat_candidates": [], "spans": []},
        {
            "bindings": [{"name": "通信设备A", "node_type": "Equipment", "slot_key": "equipment_keywords"}],
            "template_id": "composite_project_then_method",
        },
        template_id="composite_project_then_method",
    )
    assert plan["route_id"] == "R-C-PROJECT_THEN_METHOD-default"
    assert plan["user_biz"] == "COMP_PROJECT_THEN_METHOD"
