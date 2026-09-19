"""Tests for ZNWD6 guided execute (direct template + bindings)."""

from blueprints.GLCX.ZNWD6.guided_execute import (
    build_guided_plan,
    normalize_guided_bindings,
    stream_znwd6_guided_execute,
)


def test_normalize_guided_bindings():
    raw = [
        {"slot_key": "environment_keywords", "node_type": "Environment", "name": "test", "entity_id": "e1"},
    ]
    norm = normalize_guided_bindings(raw)
    assert len(norm) == 1
    assert norm[0]["name"] == "test"
    assert norm[0]["slot_key"] == "environment_keywords"


def test_build_guided_plan_environment_properties():
    plan, verr = build_guided_plan(
        "test的分类和属性有哪些",
        template_id="environment_properties",
        bindings=[
            {
                "slot_key": "environment_keywords",
                "node_type": "Environment",
                "name": "test",
                "entity_id": "env-test",
            }
        ],
    )
    assert plan.get("template_id") == "environment_properties"
    assert plan.get("route_id") == "R-ENVIRONMENT-properties"
    params = plan.get("params_draft") or {}
    assert "test" in (params.get("environment_keywords") or [])
    assert plan.get("answer_strategy") == "db_synthesize"


def test_build_guided_plan_enum_no_bindings():
    plan, _verr = build_guided_plan(
        "有哪些装备类型",
        template_id="enum_equipment_type",
        bindings=[],
    )
    assert plan.get("template_id") == "enum_equipment_type"
    assert plan.get("route_id") == "R-EQUIPMENT_TYPE-level1_list"
    assert plan.get("answer_strategy") == "db_direct"


def test_build_guided_plan_equip_to_projects():
    plan, _verr = build_guided_plan(
        "无人机有哪些试验项目",
        template_id="equip_to_projects",
        bindings=[
            {
                "slot_key": "equipment_keywords",
                "node_type": "Equipment",
                "name": "无人机",
            }
        ],
    )
    assert plan.get("template_id") == "equip_to_projects"
    params = plan.get("params_draft") or {}
    assert "无人机" in (params.get("equipment_keywords") or [])


def test_build_guided_plan_project_to_methods_db_direct():
    plan, _verr = build_guided_plan(
        "低温工作测试的试验方法是什么",
        template_id="project_to_methods",
        bindings=[
            {
                "slot_key": "project_keywords",
                "node_type": "TestProject",
                "name": "低温工作测试",
                "entity_id": "XM_0047",
                "role": "source",
            },
            {
                "slot_key": "goal_node_type",
                "node_type": "TestMethod",
                "name": "TestMethod",
                "role": "target",
            },
        ],
        goal_type="TestMethod",
    )
    assert plan.get("answer_strategy") == "db_direct"
    assert plan.get("guided_execute") is True
    assert plan.get("goal_node_type") == "TestMethod"
    params = plan.get("params_draft") or {}
    assert params.get("project_id") == "XM_0047"
    assert "低温工作测试" in (params.get("project_keywords") or [])
    assert params.get("goal_node_type") == "TestMethod"


def test_stream_guided_execute_yields_routing_step():
    events: list = []
    for typ, data in stream_znwd6_guided_execute(
        None,
        "",
        "有哪些装备类型",
        template_id="enum_equipment_type",
        bindings=[],
        neo4j_driver=None,
    ):
        events.append((typ, data))
        if typ == "step" and isinstance(data, dict) and data.get("status") == "done":
            break
    types = [e[0] for e in events]
    assert "step" in types
    done_steps = [d for t, d in events if t == "step" and d.get("status") == "done"]
    assert done_steps[0].get("templateId") == "enum_equipment_type"
    assert done_steps[0].get("guided") is True
